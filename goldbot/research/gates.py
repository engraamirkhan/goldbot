"""The design's research gates (Modelling: Validation), checked on every walk-forward result.

A specialist configuration passes only when
* it has at least MIN_CANDIDATES labelled candidates overall,
* every walk-forward test fold holds at least MIN_TEST_FOLD candidates,
* its model-filtered (cross-fitted, out-of-fold) trades have positive mean net return in at least MIN_POSITIVE_YEARS
  separate calendar years, one of them in the 2021-2022 chop. A year counts only with MIN_TRADES_PER_YEAR trades, so
  one lucky trade is not a "positive year".

* the model-filtered trades number at least MIN_TRADES_FOR_DSR and their deflated Sharpe (with the registry's trial
  count) is at least DSR_BAR (design: Validation).

The same checks are also reported on the rule alone (every candidate, net of costs, no model filter) as
`rule_only_gates`, labelled RULE_ONLY_LABEL: informational, since promotion still requires the model path.

Only complete walk-forward folds count for the per-fold minimum: a last fold the data does not cover is a stub.
The population's shadow -> live promotion requires a passed research trial for the agent's exact configuration
(TrialRegistry.passed_gates).

The held-out year is judged once per configuration by its own pre-stated rule (holdout_verdict): the model-filtered
trades in the window have positive mean R with a one-sided t-statistic of at least HOLDOUT_MIN_T.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pydantic import ValidationError

from goldbot.base import load_record
from goldbot.research.metrics import MIN_TRADES_FOR_DSR, DaySummedDsr, DsrInputs, expectancy
from goldbot.research.registry import n_trials_effective

MIN_CANDIDATES = 1500
MIN_TEST_FOLD = 60
MIN_POSITIVE_YEARS = 3
MIN_TRADES_PER_YEAR = 10
CHOP_YEARS = (2021, 2022)
DSR_BAR = 0.95                  # design: deflated Sharpe ratio of 0.95 or better
HOLDOUT_MIN_T = 1.65            # one-sided 5% on the held-out year's mean R
RULE_ONLY_LABEL = "rule-only (informational; promotion still requires the model path)"


def research_gates(n_candidates: int, fold_test_sizes: list[int], taken: pd.DataFrame,
                   model_filtered: dict[str, Any] | None = None, subject: str = "model-filtered") -> dict[str, Any]:
    """fold_test_sizes: candidates per complete test fold; taken: the model-filtered out-of-fold trades (ts_utc, ret);
    model_filtered: their summary (n, dsr); subject: what `taken` is, for the details ("rule-only" for every
    candidate). Returns {"passed": bool, "checks": [...]}."""
    checks: list[dict[str, Any]] = [
        {"name": "candidates", "passed": n_candidates >= MIN_CANDIDATES,
         "detail": f"{n_candidates:,} labelled candidates (need {MIN_CANDIDATES:,})"},
    ]
    smallest = min(fold_test_sizes) if fold_test_sizes else 0
    checks.append({"name": "per_fold", "passed": bool(fold_test_sizes) and smallest >= MIN_TEST_FOLD,
                   "detail": f"{len(fold_test_sizes)} test folds, smallest {smallest} candidates (need {MIN_TEST_FOLD} in every fold)"})
    positive: list[int] = []
    if not taken.empty:
        year = pd.DatetimeIndex(pd.to_datetime(taken["ts_utc"], utc=True)).year
        for y, g in taken.groupby(year):
            if len(g) >= MIN_TRADES_PER_YEAR and float(g["ret"].mean()) > 0:
                positive.append(int(str(y)))
    chop = any(y in CHOP_YEARS for y in positive)
    checks.append({"name": "positive_years", "passed": len(positive) >= MIN_POSITIVE_YEARS and chop,
                   "detail": f"positive {subject} years (>= {MIN_TRADES_PER_YEAR} trades each): "
                             f"{positive or 'none'}; need {MIN_POSITIVE_YEARS} incl. one of {list(CHOP_YEARS)}"})
    mf = model_filtered or {}
    n_mf, dsr = int(mf.get("n") or 0), mf.get("dsr")
    checks.append({"name": "dsr", "passed": n_mf >= MIN_TRADES_FOR_DSR and dsr is not None and float(dsr) >= DSR_BAR,
                   "detail": f"deflated Sharpe {'n/a' if dsr is None else f'{float(dsr):.3f}'} on {n_mf} {subject} "
                             f"trades (need {MIN_TRADES_FOR_DSR}+ trades and {DSR_BAR})"})
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def holdout_verdict(ret: np.ndarray, risk: np.ndarray) -> dict[str, Any]:
    """The held-out year's rule: positive mean R of the model-filtered trades with t >= HOLDOUT_MIN_T."""
    s = expectancy(ret, risk)
    n, mean_r, t = int(s.get("n", 0)), float(s.get("mean_r", 0.0)), float(s.get("t_stat", 0.0))
    passed = n >= 2 and mean_r > 0 and t >= HOLDOUT_MIN_T
    return {"passed": passed, "n": n, "mean_r": mean_r, "t_stat": t,
            "rule": f"model-filtered trades in the held-out window: mean R > 0 and t >= {HOLDOUT_MIN_T}"}


def current_dsr(row: dict[str, Any] | None, n_trials: int) -> tuple[float | None, str]:
    """The row's model-filtered deflated Sharpe recomputed on `n_trials` (the registry's `n_trials_effective` today)
    from its stored inputs (`metrics.DsrInputs`, results.model_filtered.dsr_inputs), and the reason when it cannot be.
    Design: "DSR >= 1.0 on the true trial count"; the stored `dsr` used the count of its run, which every later trial
    makes too small (quant review 2026-10-16). A row without stored inputs (written before they were recorded, or below
    metrics.MIN_TRADES_FOR_DSR trades) fails closed: it is no DSR evidence whatever its stored value. The phase gate's
    gate 1 and the survivors' `day_summed_check` both read it."""
    mf = ((row or {}).get("results") or {}).get("model_filtered") or {}
    raw = mf.get("dsr_inputs")
    if raw is None:
        return None, "no stored DSR inputs to recompute it on today's trial count (re-run the trial)"
    try:
        inputs = load_record(DsrInputs, raw, source="trial_registry")
    except ValidationError:
        return None, "unreadable stored DSR inputs (re-run the trial)"
    return inputs.dsr(max(int(n_trials), 1)), ""


def day_summed_check(row: dict[str, Any] | None, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The discovery survivors' DSR reading (pre-registration 2027Q1 O-5/O-6, E-6): "DSR >= 0.95 on >= 200
    model-filtered trades at the real trial count) AND ... the day-summed DSR is also >= 0.95", both read on "the same
    K = `n_trials_effective` as the gate (both discoveries' K_eff included)", i.e. the registry's count today
    (`registry.n_trials_effective(rows)`). The gate's DSR is `current_dsr` (on `ret`, as the phase gate reads it); the
    day-summed DSR is recomputed from the trial's stored `results.model_filtered.day_summed` (metrics.DaySummedDsr),
    whose per-trade side is on R and only shown (R basis, not the gate DSR; quant review 2026-10-17). A row without
    either set of inputs, or below MIN_TRADES_FOR_DSR trading days, fails closed. Returns a research_gates-style
    check with the DSRs and ts."""
    k = n_trials_effective(rows)
    gate, gate_why = current_dsr(row, k)
    mf = ((row or {}).get("results") or {}).get("model_filtered") or {}
    out: dict[str, Any] = {"name": "day_summed_dsr", "passed": False, "n_trials": k, "dsr_gate": gate,
                           "dsr_day_summed": None, "dsr_per_trade_r": None, "t_day_summed": None,
                           "t_per_trade_r": None}
    gate_txt = f"gate DSR {gate:.3f}" if gate is not None else f"gate DSR n/a: {gate_why}"
    raw = mf.get("day_summed")
    if raw is None:
        return {**out, "detail": f"no stored day-summed DSR inputs (fewer than {MIN_TRADES_FOR_DSR} trades or trading "
                                 f"days, or a run before E-6): re-run the trial; {gate_txt}"}
    try:
        res = load_record(DaySummedDsr, raw, source="trial_registry").at(k)
    except ValidationError:
        return {**out, "detail": f"unreadable stored day-summed DSR inputs: re-run the trial; {gate_txt}"}
    fall = 1 - res.t_day_summed / res.t_per_trade_r if res.t_per_trade_r else 0.0
    passed = gate is not None and gate >= DSR_BAR and res.dsr_day_summed >= DSR_BAR
    return {**out, "passed": passed, "dsr_day_summed": res.dsr_day_summed, "dsr_per_trade_r": res.dsr_per_trade_r,
            "t_day_summed": res.t_day_summed, "t_per_trade_r": res.t_per_trade_r,
            "detail": f"day-summed DSR {res.dsr_day_summed:.3f} on {res.n_days} trading days and {gate_txt} on "
                      f"{res.n_trades} trades, K = {k} (need both >= {DSR_BAR}); per trade on R "
                      f"{res.dsr_per_trade_r:.3f} (R basis, not the gate DSR), t {res.t_day_summed:.2f} day-summed vs "
                      f"{res.t_per_trade_r:.2f} per trade on R ({fall:.1%} lower)"}
