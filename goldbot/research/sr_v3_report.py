"""The sr-v3 trainer's read and run record (split from sr_v3_train so its fit path stays guarded; ADR 0108): the
descriptive read of a fitted unit and its run manifests. Descriptive, never a selector; Stage L gates are the
evaluator's read.

The read (`read_unit`, quant H2/H3 on #488) is refused unless every label load was gate_eligible and costed at the
ub95 cost of record, and for a judged unit unless a booked registry trial id is given. It reports, per learner of
`read_learners()` (L-cw and B8; G-cw, HB and AVG only once the zoo-v2 card is signed): the keep share, kept mean, S
with its split, S at 2x cost, the paired D per OOS session day, B0/B1/B8, the matched random twin (expectation),
buy-and-hold after one round trip, calibration, R²_OOS, within-instrument IC and AUC (diagnostic), score deciles,
the N6 shuffle check per (instrument x tf, session), the oracle canaries, seed stability, the parameter shift, the
learning curve, half-split sign agreement and gold's levels. Every read statistic uses `net_read` (the realised
cost: RT_ub95 + overshoot on stops + nights held x swap; quant M2); keeps use cost(x).

Run record (ADR 0106 decision 7, ADR 0109): `record_unit` writes one research/run_manifest.py manifest per v0
artifact, with `build_artifact` as the builder, so `run_manifest.rebuild` refits any model byte for byte.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.daily import session_date
from goldbot.research import decision_cost as DC
from goldbot.research import diagnostics as G
from goldbot.research import learners as L
from goldbot.research import model_quality as MQ
from goldbot.research import objective as O
from goldbot.research import sr_v3 as V
from goldbot.research import sr_v3_train as T
from goldbot.research.sr_v3_store import StoreData
from goldbot.research.sr_v3_train import (
    BUILT,
    HB_COLUMNS,
    MANIFEST_KEY,
    ROOT,
    SEED_SUBSAMPLE,
    W2_COST_VERSION,
    Collected,
    CostTable,
    FoldModels,
    UnitData,
    UnitPlan,
    UnitResult,
    _hash_u,
    _ns,
    _sample_td,
    _td,
    _with_dense,
    cost_of,
    frames_from,
    hb_from_plans,
    net_read,
    plan_dict,
    plan_of,
    sealed_scores,
    settings,
    train_mask,
    year_start,
)

CANARY_DELTAS: tuple[float, ...] = (0.03, 0.05, 0.10)


# quant H3 on #488: G-cw, HB and AVG belong to zoo-v2, which books its own K; until its card is signed they write
# sealed OOF scores only, never S or D. Set to the signed card's path and sha when it is.
ZOO_V2_CARD: str | None = None


ZOO_V2_LEARNERS: tuple[str, ...] = ("G-cw", "HB", "AVG")


class ReadRefused(PermissionError):
    """A read statistic (S, kept mean, S at 2x cost, D, a net-based verdict) was asked for on a label read that is not
    a gate read (`gate_eligible` not True) or without the ub95 cost of record."""


CURVE_SIZES: tuple[int, ...] = (1_000, 3_000, 10_000, 30_000, 100_000)


def _day_boot(d_by_day: np.ndarray, *, seed: int = V.SEED_BOOT, boot: int = 2_000) -> tuple[float, float, float]:
    """(mean per day, 5 %, 95 %) of a per-day series under the stationary day-block bootstrap (MEAN_BLOCK_DAYS).
    Uses objective's draw generator so every day-block read in the repo draws the same way."""
    n = len(d_by_day)
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    draws = np.concatenate([(W @ d_by_day) / n for W in O._day_weights(n, boot=boot, seed=seed,
                                                                         mean_block=V.MEAN_BLOCK_DAYS)])
    return float(d_by_day.mean()), float(np.quantile(draws, 0.05)), float(np.quantile(draws, 0.95))


def _require_trial(trial_id: int | None, judged: bool, what: str) -> None:
    if judged and trial_id is None:
        raise ReadRefused(f"refused {what}: a judged unit is read only under a booked registry trial id (quant H3)")


def read_learners() -> tuple[str, ...]:
    """The learners a read may report S and D for: L-cw and its B8 baseline; G-cw, HB and AVG only once the zoo-v2
    card is signed (`ZOO_V2_CARD`)."""
    return ("L-cw", "B8", *(ZOO_V2_LEARNERS if ZOO_V2_CARD else ()))


def learner_read(o: pd.DataFrame, name: str, *, trial_id: int | None, judged: bool) -> dict[str, Any]:
    """The descriptive obj-v1 read of one learner on OOF rows (no bootstrap beyond the per-day D). A judged unit is
    read only under a booked registry trial id (quant H3 on #488)."""
    _require_trial(trial_id, judged, f"the read of {name}")
    k = o[f"k_{name}"].to_numpy(bool)
    if not len(o):
        return {"n": 0}
    rd = O.read(o["net_read"].to_numpy(float), o["gross"].to_numpy(float), k, o["day"].to_numpy(),
                cost_x=o["cost"].to_numpy(float), boot=0, stress_keep=k)
    net2 = o["net2"].to_numpy(float)
    kf = np.zeros(len(k)) if rd.abstain else k.astype(float)
    per_day = pd.Series(o["net_read"].to_numpy(float) * kf).groupby(o["day"].to_numpy()).sum()
    return {"n": rd.n, "days": rd.n_days, "kept": rd.n_kept, "kept_days": rd.n_kept_days, "abstain": rd.abstain,
            "keep_share": rd.keep_share, "kept_mean_net": rd.kept_mean, "mean_net": rd.mean_net, "s": rd.s,
            "s_2x_cost": float((net2 * kf).mean() - max(0.0, float(net2.mean()))),
            "split": rd.decomposition.model_dump(), "net_r_per_day": float(per_day.mean()),
            "n_eff": O.kish_n_eff(o["w"].to_numpy(float))}


def paired_d(o: pd.DataFrame, a: str, b: str, *, trial_id: int | None, judged: bool) -> dict[str, float]:
    """SYNTH R3: Δ net R per OOS session day, −(Σ dropped net)/days, as Σ net·(keep_a − keep_b) / days, with a
    day-block 90 % band. Reported, never judged; a judged unit only under a booked trial id (quant H3)."""
    _require_trial(trial_id, judged, f"D {a} vs {b}")
    ok = o[f"v_{a}"].notna() & o[f"v_{b}"].notna()
    x = o[ok]
    if not len(x):
        return {"n": 0}
    d = x["net_read"].to_numpy(float) * (x[f"k_{a}"].to_numpy(float) - x[f"k_{b}"].to_numpy(float))
    by_day = pd.Series(d).groupby(x["day"].to_numpy()).sum().sort_index().to_numpy()
    m, lo, hi = _day_boot(by_day)
    return {"n": int(len(x)), "days": int(len(by_day)), "d_per_day": m, "lo90": lo, "hi90": hi}


def twin_expectation(o: pd.DataFrame, name: str) -> float:
    """B7, the side- and hour-matched random twin at the learner's keep share per (side, hour) cell, in expectation
    (no draw): Σ_cells share · mean net · n / N − max(0, mean net)."""
    k = o[f"k_{name}"].to_numpy(float)
    g = pd.DataFrame({"c": o["side"].astype(str) + "|" + o["hour"].astype(str), "k": k, "net": o["net_read"]})
    agg = g.groupby("c").agg(share=("k", "mean"), mu=("net", "mean"), n=("net", "size"))
    mean = float(o["net_read"].mean())
    return float((agg["share"] * agg["mu"] * agg["n"]).sum() / len(o) - max(0.0, mean))


def buy_and_hold(o: pd.DataFrame) -> dict[str, Any]:
    """ADR 0106 benchmark: buy the instrument at its first OOS decision, hold to the last, pay one round trip. In % of
    price and in R per OOS session day (R = the median T2 base distance)."""
    out = {}
    for inst, g in o.sort_values("decision_utc").groupby("instrument"):
        if len(g) < 2:
            continue
        p0, p1 = float(g["entry"].iloc[0]), float(g["entry"].iloc[-1])
        rt = float(g["cost_rt_bp"].median()) * 1e-4 * p0
        r = float(g["base_dist"].median())
        days = int(g["day"].nunique())
        out[str(inst)] = {"pct": (p1 - p0 - rt) / p0, "r_per_day": (p1 - p0 - rt) / r / days if r > 0 else None,
                          "from": str(g["decision_utc"].iloc[0]), "to": str(g["decision_utc"].iloc[-1])}
    return out


def calibration(o: pd.DataFrame, name: str, kind: str) -> dict[str, Any]:
    v = o[f"v_{name}"].to_numpy(float)
    ok = np.isfinite(v)
    if ok.sum() < 10:
        return {}
    w = o["w"].to_numpy(float)[ok]
    if kind == "gross":
        cal = G.weighted_calibration(v[ok], o["gross"].to_numpy(float)[ok], w)
        return {"slope": cal.slope, "intercept": cal.intercept,
                "r2_oos": G.mse_skill(v[ok], o["gross"].to_numpy(float)[ok], w)}
    y = (o["net_read"].to_numpy(float)[ok] > 0).astype(float)
    cw = O.net_logit_weights(o["net_read"].to_numpy(float)[ok], w)
    cal = G.weighted_calibration(v[ok], y, cw)
    pl = L.fit_platt(v[ok], y, cw)
    return {"slope": cal.slope, "intercept": cal.intercept, "brier_skill_w": G.brier_skill(v[ok], y, cw),
            "platt": pl.model_dump()}


def rank_diagnostics(o: pd.DataFrame, name: str, kind: str) -> dict[str, Any]:
    v = o[f"v_{name}"].to_numpy(float)
    ok = np.isfinite(v)
    if ok.sum() < 50:
        return {}
    s = v[ok] - o["cost"].to_numpy(float)[ok] if kind == "gross" else v[ok]
    net = o["net_read"].to_numpy(float)[ok]
    inst = o["instrument"].to_numpy()[ok]
    ic = {str(i): float(pd.Series(s[inst == i]).corr(pd.Series(net[inst == i]), method="spearman"))
          for i in np.unique(inst) if (inst == i).sum() > 10}
    dec = G.score_deciles(s, net) if len(s) >= 10 else pd.DataFrame()
    return {"ic_within_instrument": ic, "auc_within_instrument": G.within_arm_auc(s, net > 0, inst),
            "deciles": dec.reset_index().to_dict(orient="records")}


def _fit_score(name: str, tr: pd.DataFrame, te: pd.DataFrame, plan: UnitPlan, pri: DC.CostPriors, impute: np.ndarray,
               *, seed: int, extra: Sequence[str] = (), shift: Mapping[str, float] | None = None,
               lam_mult: float = 1.0, lam_a_mult: float = 1.0) -> np.ndarray:
    """Fit one learner on `tr`, return its keep on `te` (the canary, shuffle and shift harness; HB on its own rung)."""
    cost_te = cost_of(te, pri)
    if name in ("L-cw", "HB"):
        cols = (*plan.lcw_columns, *extra) if name == "L-cw" else (*HB_COLUMNS, *extra)
        imp = L.medians(tr[list(cols)].to_numpy(float)) if cols else np.zeros(0)
        td = _sample_td(tr, cols, pri, plan)
        a = L.fit_lcw(td, lam=V.LAMBDA_ROWS * lam_mult, lam_a=plan.lam_a * lam_a_mult, impute=imp)
        v = a.value(te[list(cols)].to_numpy(float) if cols else np.empty((len(te), 0)),
                    te["instrument"].astype(str).to_numpy())
        return L.keep("gross", v, cost_te)
    cols = (*V.P30, L.RANGE_TO_COST, *extra)
    trd = _with_dense(tr, None, cost_of(tr, pri))
    ted = _with_dense(te, None, cost_te)
    g = L.fit_gcw(_td(trd, cols, cost_of(tr, pri), design_effect=plan.design_effect), seed=seed, shift=shift)
    return L.keep("p", g.value(ted[list(cols)].to_numpy(float)), cost_te)


def _s(te: pd.DataFrame, k: np.ndarray, gross: np.ndarray | None = None) -> float:
    return O.s_statistic(net_read(te, gross=gross), k.astype(float))


def block_permutation(f: pd.DataFrame, *, seed: int) -> np.ndarray:
    """N6's shuffle (card 5.1, quant M3 on #488): a seeded permutation of the rows WITHIN each (instrument, session
    day) block, so a shuffled label keeps its instrument and day and only its position inside the day is random."""
    key = f["instrument"].astype(str).to_numpy() + "|" + np.asarray(session_date(f["decision_utc"]).astype(str))
    rand = np.random.default_rng(seed).random(len(f))
    by_key = np.argsort(key, kind="stable")
    by_key_random = np.lexsort((rand, key))
    perm = np.empty(len(f), dtype=np.int64)
    perm[by_key] = by_key_random
    return perm


def n6_cells(f: pd.DataFrame) -> np.ndarray:
    """The N6 check's cells: instrument:tf|session of the decision (card 5.1 "within 5 pp per (cell, session)")."""
    from goldbot.data.calendar import DEFAULT_SESSIONS
    dec = pd.DatetimeIndex(pd.to_datetime(f["decision_utc"], utc=True))
    sess = np.asarray(DEFAULT_SESSIONS.session_label(dec)).astype(str) if len(dec) else np.array([], str)
    return (f["instrument"].astype(str) + ":" + f["tf"].astype(str)).to_numpy() + "|" + sess


def harness(col: Collected, plan: UnitPlan, *, seeds: Sequence[int], learners: Sequence[str] = ("L-cw", "HB", "G-cw"),
            max_rows: int = 200_000) -> dict[str, Any]:
    """On the last OOF fold of the thinned sample (capped at `max_rows`, deterministic): oracle canaries at +0.03 /
    +0.05 / +0.10 R (a planted fair-coin column), the N6 shuffle (gross R permuted within (instrument, session day)
    blocks; the false-keep rule per (instrument x tf, session) cell; OOF AUC within 0.5 ± 4·SE, diagnostic), the parameter shift (each named setting x 0.8 / x 1.25, report only), seed
    stability and the learning curve (L-cw). Descriptive: nothing here selects."""
    Y = plan.oof_years[-1]
    s = col.sample
    if len(s) > max_rows:
        s = s[_hash_u(s, seeds[0]) < max_rows / len(s)].reset_index(drop=True)
    yr = pd.DatetimeIndex(pd.to_datetime(s["decision_utc"], utc=True)).year.to_numpy()
    tr = s[train_mask(s, Y, extra_s=plan.extra_lookback_s)].reset_index(drop=True)
    te = s[(yr == Y) & (s["phase"] == 0)].reset_index(drop=True)
    out: dict[str, Any] = {"fold": Y, "train_rows": len(tr), "test_rows": len(te)}
    if len(tr) < 1_000 or len(te) < 200:
        out["skipped"] = "too few sample rows on the last fold"
        return out
    pri = col.priors[Y].priors(year_start(Y))
    rng = np.random.default_rng(V.SEED_CANARY)
    z_tr, z_te = rng.integers(0, 2, len(tr)).astype(float), rng.integers(0, 2, len(te)).astype(float)
    can: dict[str, list[dict[str, Any]]] = {}
    for name in learners:
        rows = []
        for delta in CANARY_DELTAS:
            g_tr = tr["gross"].to_numpy(float) + delta * (2 * z_tr - 1)
            g_te = te["gross"].to_numpy(float) + delta * (2 * z_te - 1)
            k = _fit_score(name, tr.assign(gross=g_tr, canary=z_tr), te.assign(gross=g_te, canary=z_te), plan, pri,
                           col.impute, seed=seeds[0], extra=("canary",))
            oracle = L.keep("gross", float(np.average(O.clip_r(tr["gross"]), weights=tr["w"])) + delta * (2 * z_te - 1),
                            cost_of(te, pri))
            null = L.keep("gross", np.full(len(te), float(np.average(O.clip_r(g_tr), weights=tr["w"]))),
                          cost_of(te, pri))
            sm, so, sn = (_s(te, kk, g_te) for kk in (k, oracle, null))
            rows.append({"delta": delta, "s_model": sm, "s_oracle": so, "s_null": sn,
                         "recovered": bool(so > sn and (sm - sn) >= 0.5 * (so - sn))})
        can[name] = rows
    out["canaries"] = can
    perm = block_permutation(tr, seed=V.SEED_B2)
    cells = n6_cells(te)
    sh: dict[str, Any] = {}
    for name in learners:
        k = _fit_score(name, tr.assign(gross=tr["gross"].to_numpy(float)[perm]), te, plan, pri, col.impute,
                       seed=seeds[0])
        k0 = L.keep("gross", np.full(len(te), float(np.average(O.clip_r(tr["gross"]), weights=tr["w"]))),
                    cost_of(te, pri))
        y = net_read(te) > 0
        npos, nneg = int(y.sum()), int((~y).sum())
        auc = G.within_arm_auc(k.astype(float), y, te["instrument"].astype(str).to_numpy())
        se = float(np.sqrt((npos + nneg + 1) / (12.0 * max(1, npos) * max(1, nneg))))
        per_cell = {str(c): {"false_keep_share": float((k[m] & ~k0[m]).mean()), "kept": int(k[m].sum()),
                             "rows": int(m.sum())} for c in np.unique(cells) for m in [cells == c]}
        sh[name] = {"false_keep_share": float((k & ~k0).mean()), "auc": auc, "band": [0.5 - 4 * se, 0.5 + 4 * se],
                    "auc_in_band": bool(abs(auc - 0.5) <= 4 * se) if np.isfinite(auc) else True, "cells": per_cell,
                    # diagnostics.shuffled_keep_check's rule (N6), per (instrument x tf, session) cell (quant M3)
                    "passed": all(c["false_keep_share"] <= G.MAX_FALSE_KEEP or c["kept"] < V.MIN_KEPT
                                  for c in per_cell.values())}
    out["shuffle"] = sh

    def net_mean(k: np.ndarray) -> float:
        return float((net_read(te) * k).mean())
    lin = {"lambda": V.LAMBDA_ROWS, "lambda_a": plan.lam_a}

    def ev_lin(name: str) -> Any:
        def ev(pt: dict[str, Any]) -> float:
            return net_mean(_fit_score(name, tr, te, plan, pri, col.impute, seed=seeds[0],
                                       lam_mult=pt["lambda"] / lin["lambda"],
                                       lam_a_mult=pt["lambda_a"] / lin["lambda_a"]))
        return ev
    gb = {"learning_rate": L.GCW_PARAMS["learning_rate"], "num_iterations": L.GCW_PARAMS["num_iterations"],
          "min_child_share": L.GCW_PARAMS["min_child_share"]}

    def ev_g(pt: dict[str, Any]) -> float:
        return net_mean(_fit_score("G-cw", tr, te, plan, pri, col.impute, seed=seeds[0],
                                   shift={k: float(pt[k]) / float(gb[k]) for k in gb}))
    # ADR 0106 "stays profitable when parameters are shifted slightly": model_quality's +-10 % / +-20 % grid, one
    # parameter at a time, net mean after cost on the last fold (HB here is its own rung: λ0 and λ_c play λ, λ_a)
    rk = rows_key(te)
    shifts = {"L-cw": lambda: MQ.param_shift(lin, ev_lin("L-cw"), rows=rk),
              "HB": lambda: MQ.param_shift(lin, ev_lin("HB"), rows=rk), "G-cw": lambda: MQ.param_shift(gb, ev_g, rows=rk)}
    out["parameter_shift"] = {n: shifts[n]().model_dump(mode="json") for n in learners}
    if "G-cw" not in learners:
        return _curve(out, tr, te, plan, pri, col, seeds)
    out["seed_s"] = {"G-cw": [_s(te, _fit_score("G-cw", tr[_hash_u(tr, sd) < SEED_SUBSAMPLE].reset_index(drop=True),
                                                te, plan, pri, col.impute, seed=sd)) for sd in seeds]}
    return _curve(out, tr, te, plan, pri, col, seeds)


def _curve(out: dict[str, Any], tr: pd.DataFrame, te: pd.DataFrame, plan: UnitPlan, pri: DC.CostPriors,
           col: Collected, seeds: Sequence[int]) -> dict[str, Any]:
    """The L-cw learning curve and the half-split sign agreement (the harness's L-cw tail)."""
    curve = []
    order = np.argsort(_ns(tr["decision_utc"]), kind="stable")
    for n in (*[c for c in CURVE_SIZES if c < len(tr)], len(tr)):
        sub = tr.iloc[order[-n:]].reset_index(drop=True)     # the most recent n rows
        curve.append({"n": int(n), "s": _s(te, _fit_score("L-cw", sub, te, plan, pri, col.impute, seed=seeds[0]))})
    out["learning_curve_lcw"] = curve
    if plan.lcw_columns:
        yrs = pd.DatetimeIndex(pd.to_datetime(tr["decision_utc"], utc=True)).year.to_numpy()
        halves = [tr[yrs % 2 == r].reset_index(drop=True) for r in (0, 1)]
        if all(len(h) > 100 for h in halves):
            co = [L.fit_lcw(_sample_td(h, plan.lcw_columns, pri, plan), lam=V.LAMBDA_ROWS, lam_a=plan.lam_a).coef
                  for h in halves]
            out["half_split_sign_agreement"] = float(np.mean(np.sign(co[0]) == np.sign(co[1])))
    return out


def read_refusal(data: UnitData) -> str | None:
    """Why a read statistic may not be computed on `data`, or None. ADR 0085 A4 (quant A4 on #482): "Any gate or
    evaluator read refuses `gate_eligible` False"; quant H1 on #488: a read charges the ub95 cost of record."""
    g = data.gate_status()
    if g.get("gate_eligible") is not True:
        return "the label read is not gate_eligible: " + ("; ".join(g.get("gate_block") or []) or "no gate status")
    if data.cost_version() == W2_COST_VERSION:
        return "rows are costed at the W2 point (w2-point), not the ub95 cost of record per cell"
    return None


def fit_unit(data: UnitData, plan: UnitPlan, *, hb_by_fold: Mapping[int, L.HbArtifact] | None,
             seeds: Sequence[int] = T.SEEDS, col: Collected | None = None, harness_rows: int = 200_000,
             with_harness: bool = True, read: bool = False, trial_id: int | None = None) -> UnitResult:
    """`sr_v3_train.fit_unit`, then (`read=True`) `read_unit`. A judged unit's read is refused before any fit when no
    trial id is given."""
    if read:
        _require_trial(trial_id, bool(plan.stage0.get("judged")), f"the read of unit {plan.unit}")
    res = T.fit_unit(data, plan, hb_by_fold=hb_by_fold, seeds=seeds, col=col)
    if not read:
        return res
    return read_unit(res, data, plan, seeds=seeds, harness_rows=harness_rows, with_harness=with_harness,
                     trial_id=trial_id)


def read_unit(res: UnitResult, data: UnitData, plan: UnitPlan, *, seeds: Sequence[int] = T.SEEDS,
              harness_rows: int = 200_000, with_harness: bool = True, trial_id: int | None = None) -> UnitResult:
    """The descriptive read of a fitted unit (quant H2 on #488): raises `ReadRefused` unless the label read is
    gate_eligible and costed at the ub95 cost of record (`read_refusal`), and for a judged unit unless a booked trial
    id is given (H3). Adds S, kept mean, S at 2x cost, D and the harness for `read_learners()` only."""
    _require_trial(trial_id, bool(plan.stage0.get("judged")), f"the read of unit {plan.unit}")
    if why := read_refusal(data):
        raise ReadRefused(f"refused the read statistics of unit {plan.unit}: {why}. Fit-only (the default) still runs")
    if res.col is None or res.v0 is None:
        raise ValueError("read_unit needs the fit's in-memory result (sr_v3_train.fit_unit)")
    rep = unit_report(res.full, plan, res.col, res.v0, seeds=seeds, harness_rows=harness_rows,
                      with_harness=with_harness, trial_id=trial_id)
    keep = {k: v for k, v in res.report.items() if k not in rep and k != "note"}
    return UnitResult(report={**rep, **keep, "mode": "read"}, artifacts=res.artifacts, oof=res.full, full=res.full,
                      v0=res.v0, col=res.col)



def unit_report(oof: pd.DataFrame, plan: UnitPlan, col: Collected, v0: FoldModels, *, seeds: Sequence[int],
                harness_rows: int, with_harness: bool = True, trial_id: int | None = None) -> dict[str, Any]:
    allowed = read_learners()
    judged = bool(plan.stage0.get("judged"))
    tid: dict[str, Any] = {"trial_id": trial_id, "judged": judged}
    rep: dict[str, Any] = {"trial_id": trial_id, "read_learners": list(allowed), "zoo_v2_card": ZOO_V2_CARD,
                           "withheld": [b for b in BUILT if b not in allowed],
                           "unit": plan.unit, "tfs": list(plan.tfs), "members": list(plan.members),
                           "level": plan.stage0.get("level"), "judged": plan.stage0.get("judged"),
                           "served_lcw": list(plan.lcw_columns), "first_oof": plan.first_oof,
                           "rows_learn": col.n_rows, "tree_sample_pi": col.pi, "learners": {},
                           "note": "descriptive, never a selector; Stage L gates are the evaluator's read"}
    if not len(oof):
        rep["oof"] = "no OOF rows"
        return rep
    gate10 = {int(str(k)): int(v) for k, v in oof.groupby("year").size().items()}
    rep["candidates"] = {"oof_rows": int(len(oof)), "per_year": {int(k): int(v) for k, v in gate10.items()},
                         "min_learn_1500": bool(col.n_rows >= V.MIN_LEARN_CANDIDATES),
                         "min_per_fold_60": bool(min(gate10.values()) >= V.MIN_PER_FOLD)}
    mean_net = float(oof["net_read"].mean())
    rep["baselines"] = {"B0_abstain": -max(0.0, mean_net), "B1_take_all": mean_net - max(0.0, mean_net),
                        "B8_intercept_only": learner_read(oof, "B8", **tid), "buy_and_hold": buy_and_hold(oof)}
    for name in BUILT:
        if f"k_{name}" not in oof or name not in allowed:
            continue
        kind = L.LEARNERS[name].kind or "gross"
        o = oof[oof[f"v_{name}"].notna()]
        entry = {"read": learner_read(o, name, **tid), "twin_s_expectation": twin_expectation(o, name) if len(o) else None,
                 "calibration": calibration(o, name, kind), "rank": rank_diagnostics(o, name, kind)}
        if name != "G-cw" and "G-cw" in allowed:
            entry["d_vs_gcw"] = paired_d(oof, name, "G-cw", **tid)
        rep["learners"][name] = entry
    rd = [b for b in BUILT if b in allowed]
    rep["paired_d"] = {f"{a} vs {b}": paired_d(oof, a, b, **tid) for i, a in enumerate(rd) for b in rd[i + 1:]
                       if f"k_{a}" in oof and f"k_{b}" in oof}
    rep["seed_stability"] = {**{k: dict(SEED_NOT_APPLICABLE) for k in CLOSED_FORM if k in BUILT and k in allowed},
                             **{k: v.model_dump(mode="json") for k, v in seed_verdicts(oof, seeds).items()
                                if k in allowed}}
    gold = {i: {"shrunk": v0.lcw.levels.get(i), "unshrunk": v0.lcw.levels_unshrunk.get(i)} for i in v0.lcw.levels}
    rep["levels"] = gold or "gold body: no instrument level (unit intercept only)"
    yr = pd.DatetimeIndex(pd.to_datetime(oof["decision_utc"], utc=True)).year
    late = oof[np.isin(yr, [2015, 2016, 2017])]
    rep["v0_keep_share_2015_17"] = float(late["k_L-cw"].mean()) if len(late) else None   # H1 breaker reference (OOF)
    hl = tuple(n for n in ("L-cw", "HB", "G-cw") if n in allowed)
    rep["harness"] = (harness(col, plan, seeds=seeds, max_rows=harness_rows, learners=hl) if with_harness
                      else {"skipped": "builder"})
    rep["not_in_this_report"] = ["Stage L gates (evaluator)", "B2-B6 baselines (5.2, evaluator)",
                                 "arm-fire subset S (no arm candidates in dense rows)", "block dS table (no X block "
                                 "built)", "offset bias, staleness (live)", "£/day at 0.01 lot (needs the lot map)"]
    return rep


def write_unit(res: UnitResult, out: Path) -> Path:
    d = out / res.report["unit"]
    d.mkdir(parents=True, exist_ok=True)
    for name, text in res.artifacts.items():
        (d / f"{name}.json").write_text(text)
    (d / "report.json").write_text(json.dumps(res.report, indent=1, sort_keys=True, default=_json))
    (d / "report.md").write_text(report_md(res.report))
    sealed_scores(res.oof).to_parquet(d / "oof_scores.parquet", index=False)
    if res.report.get("mode") == "read" and len(res.oof):
        res.oof.to_parquet(d / "oof.parquet", index=False)
    return d


def _json(x: Any) -> Any:
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (pd.Timestamp,)):
        return x.isoformat()
    return str(x)


def _f(x: Any) -> str:
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else (f"{x:+.4f}" if isinstance(
        x, float) else str(x))


def report_md(r: Mapping[str, Any]) -> str:
    served = ", ".join(r.get("served_lcw") or []) or ("none (intercept-only B8: a per-instrument level minus cost(x), "
                                                      "a cost-and-level filter)")
    lines = [f"# sr-v3 training report: unit {r['unit']}", "",
             f"Level {r.get('level')} (judged: {r.get('judged')}); members {', '.join(r.get('members', []))}; "
             f"first OOF {r.get('first_oof')}; L-cw served columns: {served}.", "",
             f"Mode: {r.get('mode')}; gate_eligible {r.get('gate_eligible')} "
             f"({'; '.join(r.get('gate_block') or []) or 'no block'}); cost version {r.get('cost_version')}; sealed OOF "
             f"scores sha256 {r.get('oof_scores_sha256')}.", ""]
    if r.get("mode") != "read":
        lines += ["Fit-only: artifacts and sealed OOF scores only. No read statistic is computed until the label "
                  "read is gate_eligible and costed at the ub95 cost of record (quant H2 on #488).", "",
                  "| artifact | sha256 |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in (r.get("artifacts") or {}).items()]
        return "\n".join([*lines, ""])
    lines += ["Descriptive, never a selector. Stage L gates are the evaluator's read. G-cw is the gradient-boosted "
             "baseline every learner must beat out of sample after costs (ADR 0106).", "",
             "| learner | rows | keep share | kept mean net | S | S at 2x cost | net R / day | D/day vs G-cw (90 %) |",
             "|---|---|---|---|---|---|---|---|"]
    for name, e in r.get("learners", {}).items():
        rd, d = e.get("read", {}), e.get("d_vs_gcw", {})
        band = f"{_f(d.get('d_per_day'))} [{_f(d.get('lo90'))}, {_f(d.get('hi90'))}]" if d.get("n") else "baseline"
        lines.append(f"| {name} | {rd.get('n')} | {_f(rd.get('keep_share'))} | {_f(rd.get('kept_mean_net'))} | "
                     f"{_f(rd.get('s'))} | {_f(rd.get('s_2x_cost'))} | {_f(rd.get('net_r_per_day'))} | {band} |")
    lines += ["", "## Benchmarks", "", f"- B0 abstain S: {_f(r.get('baselines', {}).get('B0_abstain'))}",
              f"- B1 take-all S: {_f(r.get('baselines', {}).get('B1_take_all'))}"]
    for i, b in (r.get("baselines", {}).get("buy_and_hold") or {}).items():
        lines.append(f"- buy-and-hold {i}: {_f(b.get('pct'))} of price, {_f(b.get('r_per_day'))} R per OOS day")
    lines += ["", "## Paired D per OOS session day (reported, not judged)", ""]
    for k, d in (r.get("paired_d") or {}).items():
        lines.append(f"- {k}: {_f(d.get('d_per_day'))} [{_f(d.get('lo90'))}, {_f(d.get('hi90'))}] over "
                     f"{d.get('days')} days")
    lines += ["", "## Diagnostics", "", "```json",
              json.dumps({k: r.get(k) for k in ("seed_stability", "levels", "v0_keep_share_2015_17", "candidates",
                                                 "harness")}, indent=1, default=_json), "```",
              "", "Not in this report: " + "; ".join(r.get("not_in_this_report", [])) + ".", ""]
    return "\n".join(lines)


def row_edge(oof: pd.DataFrame, kind: str, col: str) -> np.ndarray:
    """Net R after cost per OOF decision row, 0 where the learner does not keep (model_quality's read)."""
    v = oof[col].to_numpy(float)
    k = np.isfinite(v) & L.keep(kind, np.nan_to_num(v, nan=-np.inf), oof["cost"].to_numpy(float))  # type: ignore[arg-type]
    return oof["net_read"].to_numpy(float) * k


def rows_key(f: pd.DataFrame) -> str:
    """model_quality.rows_key of a read's rows: instrument, tf, phase (0 when the frame has none), decision instant
    and side, in row order."""
    phase = f["phase"].astype(str) if "phase" in f else pd.Series("0", index=f.index)
    keys = (f["instrument"].astype(str) + "|" + f["tf"].astype(str) + "|" + phase + "|"
            + pd.Series(_ns(f["decision_utc"]), index=f.index).astype(str) + "|" + f["side"].astype(str))
    return MQ.rows_key(list(keys))


CLOSED_FORM = ("L-cw", "HB", "B8")


SEED_NOT_APPLICABLE = {"passed": None, "detail": "not applicable: a closed form, every seed fits the same bytes (quant "
                                                 "M4 on #488: no seed-stability verdict is recorded for it)"}


def seed_verdicts(oof: pd.DataFrame, seeds: Sequence[int]) -> dict[str, Any]:
    """ADR 0106 "results stable across at least 5 random seeds" (model_quality.seed_stability): per learner with a
    random component (G-cw, AVG over each G-cw seed), the mean net R per OOF row for each seed, each seed its own
    90 % resample. The closed forms (L-cw, HB, B8) get no verdict: a seed cannot move them, so a "pass" would be
    true by construction (the report writes `SEED_NOT_APPLICABLE`)."""
    out: dict[str, Any] = {}
    if not len(oof):
        return out
    for name in BUILT:
        if name in CLOSED_FORM:
            continue
        kind = L.LEARNERS[name].kind or "gross"
        cols = {sd: f"v_{name}@{sd}" for sd in seeds if f"v_{name}@{sd}" in oof}
        if len(cols) != len(seeds):
            continue
        edges = {sd: float(row_edge(oof, kind, c).mean()) for sd, c in cols.items()}
        v = MQ.seed_stability(edges, rows=rows_key(oof))
        out[name] = v
    return out


FAMILY: dict[str, str] = {"L-cw": "F", "B8": "F", "HB": "F", "G-cw": "C", "D-40": "C", "AVG": "G"}


BUILDER = "goldbot.research.sr_v3_report:build_artifact"


def run_settings(plan: UnitPlan, learner: str, source: Mapping[str, Any], hb_plans: Sequence[UnitPlan]) -> dict[str, Any]:
    """Everything `build_artifact` reads besides the data files and seeds (the manifest's settings)."""
    return {"sr_v3": {"learner": learner, "plan": plan_dict(plan), "hb_plans": [plan_dict(p) for p in hb_plans],
                      "source": dict(source)}, "frozen": settings()}


def build_artifact(spec: Any) -> bytes:
    """run_manifest builder (`BuildSpec` -> artifact bytes): refits one learner's v0 exactly as `fit_unit` did.
    Data: `frames.parquet` (in-memory data written by `frames_file`) or the stores named in settings, with the tiers
    file named by `source.tiers_file`. Labels only through load_labels, as in the original run."""
    cfg = spec.settings["sr_v3"]
    if cfg["source"]["kind"] == "frames":
        data: UnitData = frames_from(spec.data_files["frames.parquet"])
    else:
        st = cfg["source"]
        data = StoreData(labels=Path(st["labels"]), features=Path(st["features"]),
                         tiers_path=Path(spec.data_files[st["tiers_file"]]), unit=st.get("unit"),
                         read_key=st.get("read_key"), read=st.get("read_key") is not None,
                         costs=CostTable.from_manifest(st["cost_manifest"]) if st.get("cost_manifest") else None)
    plan = plan_of(cfg["plan"])
    seeds = tuple(int(s) for s in spec.seeds)
    hb_plans = [plan_of(p) for p in cfg["hb_plans"]]
    hb = hb_from_plans(data, hb_plans, seed=seeds[0]) if hb_plans else None
    res = T.fit_unit(data, plan, hb_by_fold=hb, seeds=seeds)
    return res.artifacts[cfg["learner"]].encode()


def record_unit(res: UnitResult, plan: UnitPlan, *, seeds: Sequence[int], source: Mapping[str, Any],
                data_version: str, data_root: Path, data_files: Sequence[str], hb_plans: Sequence[UnitPlan],
                root: Path, repo: Path = ROOT) -> dict[str, Any]:
    """Record every v0 artifact with research/run_manifest.py (ADR 0106 decision 7, ADR 0109): data version and file
    shas, settings, the manifest key and its seeds, code sha, train end and label end, the folds, the builder, results
    and the quality verdicts (seed_stability on the OOF rows, param_shift on the last fold's sample). The v0 artifact
    is seed index 0 (`served="seed0"`). No `beats_baseline` verdict is written here: it needs a registry trial (K +1)
    and is read at promotion, never by the trainer (the trial budget is not the trainer's to spend); the report's
    paired D against G-cw stays diagnostic."""
    from goldbot.research import run_manifest as RM
    read = res.report.get("mode") == "read"
    allowed = set(res.report.get("read_learners") or ()) if read else set()
    verdicts = {k: v for k, v in seed_verdicts(res.oof, seeds).items() if k in allowed} if read else {}   # H2, H3
    shift = ((res.report.get("harness") or {}).get("parameter_shift") or {}) if read else {}
    status = {k: res.report.get(k) for k in ("mode", "gate_eligible", "gate_block", "cost_version",
                                              "oof_scores_sha256")}
    shas = {f: RM.file_sha256(Path(data_root) / f) for f in data_files}
    if not res.report.get("train_end") or not res.report.get("label_end_max"):
        # quant H5 on #488: never record the learn end in place of the real ones (a reloaded Collected lost them)
        raise ValueError(f"unit {plan.unit}: train_end / label_end_max unknown; refusing to record a manifest")
    end = pd.Timestamp(res.report["train_end"])
    label_end = pd.Timestamp(res.report["label_end_max"])
    folds = [f"oof:{y}" for y in plan.oof_years] + [f"v0:{V.LEARN_YEARS[0]}-{V.LEARN_YEARS[-1]}"]
    out: dict[str, Any] = {}
    for name in res.artifacts:
        q = [v for v in (verdicts.get(name),) if v is not None]
        if name in shift:
            q.append(MQ.QualityVerdict.model_validate(shift[name]))
        out[name] = RM.record(model=f"sr-v3:{plan.unit}:{name}", family=FAMILY[name],  # type: ignore[arg-type]
                              artifact=res.artifacts[name].encode(), data_version=data_version,
                              settings=run_settings(plan, name, source, hb_plans), manifest_key=MANIFEST_KEY,
                              served="seed0", seeds=seeds, unit=plan.unit, fold_keys=folds, train_end=end,
                              scaler_fit_end=end, label_end_max=label_end, data_files=shas, builder=BUILDER,
                              results={**((res.report.get("learners", {}).get(name) or {}).get("read", {})
                                          if name in allowed else {}), **status}, quality=q,
                              spec_id=V.SPEC_VERSION, root=root, repo=repo)
    return out


__all__ = ["BUILDER", "ZOO_V2_CARD", "ReadRefused", "build_artifact", "buy_and_hold", "fit_unit", "harness",
           "learner_read", "paired_d", "read_learners", "read_unit", "record_unit", "report_md", "seed_verdicts",
           "twin_expectation", "write_unit"]
