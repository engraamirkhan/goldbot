"""The confirm and holdout stages of autonomous discovery (ADR 0058 phase 3; design sections 3.3 and 3.5).

Design 3.3: "At most 8 specs a month. Each is an ad-hoc `registry.preregister` row (`rule_ref = autodisc:<hash>`) with
the reading rule text generated from a fixed template and committed before the run. Each is one budget trial
(`check_budget_reserved`: they never take reserved slots)." "An evaluator verdict row is recorded before any follow-up.
A failed spec's descendants may be searched again in explore, but the failed hash can never be re-confirmed."
Design 3.5: "Only confirm passers, scored once per configuration through the existing `holdout_scored` /
`gates.holdout_verdict` path. G5 and other `holdout_contaminated` specs skip it."

    preregister_confirm(registry, ledger, spec_hash)          the promise: refused unless the spec is a recorded explore
                                                              survivor that meets the ADR 0058 2026-10-30 rule (DSR >= 0.95
                                                              at the cumulative K_search on 200+ events), the registry's
                                                              discovery K covers the ledger (F3), the month's batch has
                                                              room (8) and is not run, and the hash was never confirm-read
    run_confirm_batch(registry, ledger, batch, read, cap=, reserved=)
                                                              the month's frozen batch read ONCE, under the registry lock
    holdout_read(registry, ledger, spec_hash, read, cap=, reserved=)
                                                              a confirm passer with a "continue" verdict, read once
    stage_backends(settings, stage, timeframe) / read_on(tensor, provider)
                                                              the real reader: 1m bars of the stage window plus a year of
                                                              warm-up, the explore cost model, the explore evaluator

The reading rule (frozen in `reading_rule`; every part is a check in the verdict): pass iff
  (1) DSR >= gates.DSR_BAR (0.95) on >= MIN_TRADES_FOR_DSR (200) events, read at K = the registry's
      `n_trials_discovery` (registry K + every discovery batch's k_contrib) + this batch's trials, on the gates' per-trade
      DSR inputs (`metrics.DsrInputs`);
  (2) net mean R > 0 and the gates' positive years: >= 3 years with >= 10 events and a positive mean, one of them 2021 or
      2022 (`gates.research_gates`' positive_years check, on R);
  (3) the clustered one-sided t (by session day) rejects under Holm at 5% over the month's frozen batch;
  (4) net mean R > 0 at 1.5x cost (the explore stress);
  (5) consistency: the confirm mean R inside the explore window's 90% prediction interval,
      explore mean +- 1.645 x explore se x sqrt(1 + n_explore / n_confirm), the explore numbers frozen in the
      pre-registration;
  (6) the read is net of the declared measured cost table (A4 cost floor; quant review of phase 3, F3).
Advancing to a confirm trial (quant review F1-F3): the survivor's DSR is re-read from its stored inputs at the
registry's `n_trials_discovery` on the day (grammar v1's K 864 included, `k_link.PRIOR_SEARCHES`), its own explore net
mean R is > 0 and it was judged on a measured cost table. The holdout passes on `gates.holdout_verdict` AND the t
clustered by session day >= HOLDOUT_MIN_T (F4).
Deviation from design 3.3, recorded in ADR 0058's phase-3 amendment: the read is rule-only on the outcome tensor (the same
evaluator and costs as explore), not the `SpecSpecialist` -> `research_pass` model path; the gates' model-only checks
(1,500 candidates, per-fold test sizes) do not apply. A pass here is therefore never `passed_gates` (no "evaluated"
row), so promotion and live eligibility are unchanged: a confirm or holdout passer can only become a demo arm
(`ops.autodisc_register`).

Every read is journalled before it looks (`stages.StageJournal`, hash-chained, unique per stage and hash) and recorded as
a registry trial with its verdict row; a read that crashes is closed void, charged and stopped, never repeated."""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as sps

from goldbot.base import FrozenRecord, load_record_json
from goldbot.config import Settings
from goldbot.data.timeutil import epoch_ns
from goldbot.research import gates
from goldbot.research import registry as RG
from goldbot.research.autodisc.contracts import BoolArr, MaskProvider, SideArr
from goldbot.research.autodisc.evaluate import STRESS_MULT, clustered_t
from goldbot.research.autodisc.gen_gp import Resolver
from goldbot.research.autodisc.k_link import sync_discovery_k
from goldbot.research.autodisc.ledger import SearchLedger
from goldbot.research.autodisc.nightly import meets_confirm_bar
from goldbot.research.autodisc.nulls import exit_index
from goldbot.research.autodisc.outcomes import OutcomeTensor
from goldbot.research.autodisc.primitives import PrimitiveProvider
from goldbot.research.autodisc.spec import Predicate, StrategySpec, canonical_json, spec_hash
from goldbot.research.autodisc.stages import Stage, StageJournal, StageRefused
from goldbot.research.autodisc.windows import STAGE_WINDOWS, StageFrame, StageName
from goldbot.research.director import verdict_of
from goldbot.research.metrics import MIN_TRADES_FOR_DSR, DsrInputs
from goldbot.research.registry import PREREGISTERED, TrialRegistry, row_ref

FAMILY = "autodisc"
AGENT_ID = "autodisc"
STATUS_CONFIRM = "autodisc_confirm"     # a trial row (any build counts it: it takes a budget slot, as it should)
FEATURE_VERSION = "autodisc-spec-v1"
MONTHLY_MAX = 8                         # design 3.3: at most 8 specs a month
HOLM_ALPHA = 0.05
PI_Z = 1.645                            # two-sided 90% prediction interval
WARMUP_DAYS = 365                       # indicator warm-up before a stage window (never scored)
RULE = "ADR 0058 design 3.3 (confirm)"
WINDOWS = STAGE_WINDOWS


class StageRead(FrozenRecord):
    """One stage read of one spec: its events (net return and stop risk as fractions of price, entry time) and the
    numbers the rule reads. Built by `read_on` from the outcome tensor (or synthetically in tests)."""
    ret: list[float]
    risk: list[float]
    ts_ns: list[int]
    n_events: int
    mean_r: float | None
    t_stat: float | None                # clustered by session day
    stress_mean_r: float | None         # net mean R at STRESS_MULT x cost
    input_digest: str
    cost_line: str

    @classmethod
    def of(cls, *, ret: Sequence[float] | np.ndarray, risk: Sequence[float] | np.ndarray, ts_utc: pd.DatetimeIndex,
           stress_mean_r: float | None, input_digest: str, cost_line: str) -> StageRead:
        ret_a, risk_a = np.asarray(ret, dtype=float), np.asarray(risk, dtype=float)
        r = ret_a / risk_a
        idx = pd.DatetimeIndex(ts_utc)
        return cls(ret=ret_a.tolist(), risk=risk_a.tolist(), ts_ns=[int(x) for x in epoch_ns(idx)], n_events=len(r),
                   mean_r=float(r.mean()) if len(r) else None,
                   t_stat=clustered_t(r, idx.normalize().to_numpy()) if len(r) else None,
                   stress_mean_r=stress_mean_r, input_digest=input_digest, cost_line=cost_line)

    def r(self) -> np.ndarray:
        return np.asarray(self.ret, dtype=float) / np.asarray(self.risk, dtype=float)

    def ts(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(pd.to_datetime(np.asarray(self.ts_ns, dtype="int64"), utc=True))


class ConfirmVerdict(FrozenRecord):
    spec_hash: str
    passed: bool
    checks: list[dict[str, Any]]
    failed: list[str]
    n_trials: int                       # the K the DSR was read at
    trial: int                          # the registry trial row
    p_value: float


class HoldoutVerdict(FrozenRecord):
    spec_hash: str
    passed: bool
    verdict: dict[str, Any]             # gates.holdout_verdict
    trial: int


Reader = Callable[[StrategySpec], StageRead]


def config_for(spec: StrategySpec) -> dict[str, Any]:
    """The registry config of a discovered spec (its hash is the config hash every stage row carries)."""
    return {"spec": canonical_json(spec), "spec_hash": spec_hash(spec), "timeframe": spec.timeframe}


def _day(ts: pd.Timestamp) -> str:
    return f"{ts:%Y-%m-%d}"


def window_text(stage: Stage) -> list[str]:
    lo, hi = WINDOWS[stage]
    return [_day(lo), _day(hi - pd.Timedelta(days=1))]


def reading_rule(h: str, plan: dict[str, Any]) -> str:
    lo, hi = window_text("confirm")
    m, se, n = plan["explore_mean_r"], plan["explore_se"], plan["explore_n"]
    return (f"{RULE}, frozen before the read: spec {h} is read once on {lo} .. {hi} (rule-only, the explore cost model "
            f"and evaluator), in batch {plan['batch']}. Pass iff (1) DSR >= {gates.DSR_BAR} on >= {MIN_TRADES_FOR_DSR} "
            f"events at K = the registry's n_trials_discovery (registry K + every discovery batch's k_contrib) + this "
            f"batch's trials; (2) net mean R > 0 and >= {gates.MIN_POSITIVE_YEARS} positive years (>= "
            f"{gates.MIN_TRADES_PER_YEAR} events each) including one of {list(gates.CHOP_YEARS)}; (3) the clustered "
            f"one-sided t rejects under Holm at {HOLM_ALPHA:.0%} over the batch; (4) net mean R > 0 at "
            f"{STRESS_MULT:g}x cost; (5) the confirm mean R lies inside the explore 90% prediction interval "
            f"{m:.4f} +- {PI_Z} x {se:.4f} x sqrt(1 + {n} / n_confirm). A pass is followed by one holdout read "
            f"(gates.holdout_verdict: mean R > 0 and t >= {gates.HOLDOUT_MIN_T}, and clustered t >= "
            f"{gates.HOLDOUT_MIN_T} by session day), never for a holdout_contaminated spec. Every read is net of the "
            f"measured cost table ({plan['explore_cost_line']}); a read on other costs fails. A failed hash is never "
            "re-confirmed.")


def is_measured(cost_line: str) -> bool:
    """The A4 cost floor: the cost line names a measured cost table (`execution.costs.research_costs`), not priors."""
    return cost_line.startswith("cost table ")


def _require_measured(cost_line: str, what: str) -> None:
    if not is_measured(cost_line):
        raise StageRefused(f"{what} is not on a measured cost table ({cost_line or 'no cost line'}): the A4 cost floor "
                           "needs one (pass --cost-table / research.autodisc.cost_table)")


def _real(ledger: SearchLedger) -> None:
    if ledger.mode() != "real":
        raise StageRefused(f"{ledger.path} is not a real search ledger: a synthetic search never reaches a stage read")
    if ledger.verify() == 0:
        raise StageRefused(f"no search ledger at {ledger.path} (empty): stage reads run on the host that searched")


def _spec(ledger: SearchLedger, h: str) -> StrategySpec:
    s = ledger.get_spec(h)
    if s is None:
        raise StageRefused(f"spec {h[:12]} is not in the search ledger")
    return s


def _batch_rows(rows: list[dict[str, Any]], status: str, batch: str) -> list[dict[str, Any]]:
    key = "plan" if status == PREREGISTERED else "results"
    return [r for r in rows if r.get("status") == status and r.get("family") == FAMILY
            and (r.get(key) or {}).get("batch") == batch]


# ---------------------------------------------------------------- pre-registration
def preregister_confirm(registry: TrialRegistry, ledger: SearchLedger, h: str) -> dict[str, Any]:
    """Write the spec's confirm pre-registration (module docstring); StageRefused says why not."""
    _real(ledger)
    spec = _spec(ledger, h)
    sv = ledger.survivor_record(h)
    res = ledger.result(h) or {}
    if sv is None:
        raise StageRefused(f"spec {h[:12]} is not a recorded explore survivor: only survivors advance to confirm")
    n = int(res.get("n_events") or 0)
    if n < MIN_TRADES_FOR_DSR:
        raise StageRefused(f"spec {h[:12]}: {n} events; advancing to confirm needs {MIN_TRADES_FOR_DSR}+ events "
                           "(ADR 0058 amendment 2026-10-30): it stays a recorded fact")
    m, t = res.get("mean_r"), res.get("t_stat")
    if m is None or t is None or not t:
        raise StageRefused(f"spec {h[:12]}: no explore mean R / t_stat in the ledger result, so the consistency "
                           "interval cannot be frozen")
    if not float(m) > 0:
        raise StageRefused(f"spec {h[:12]}: explore net mean R {float(m):.4f} <= 0 (its own, before the drift baseline)")
    _require_measured(sv.get("cost_line") or "", f"spec {h[:12]}'s explore result")
    if not sv.get("dsr_inputs"):
        raise StageRefused(f"spec {h[:12]}: its survivor row has no stored DSR inputs, so the DSR cannot be read at "
                           "today's K: re-judge it on a later night")
    inputs = load_record_json(DsrInputs, sv["dsr_inputs"], source="discovery_survivors")
    if StageJournal(ledger.path).state(h).confirm is not None:
        raise StageRefused(f"spec {h[:12]} was read on the confirm window already: a failed hash is never re-confirmed")
    with registry.locked():
        rows = registry._rows()
        ref = f"autodisc:{h}"
        if any(r.get("rule_ref") == ref for r in rows):
            raise StageRefused(f"spec {h[:12]} is pre-registered already ({ref})")
        sync_discovery_k(registry, ledger, generator="confirm precondition", lock=False)      # F3, before any trial
        k = registry.n_trials_discovery
        dsr = inputs.dsr(k)                   # quant F2: re-read at the registry's discovery K of today, not the night's
        if not meets_confirm_bar(dsr, n):
            raise StageRefused(f"spec {h[:12]}: explore DSR {dsr:.3f} at the registry's discovery K {k:,} (judged "
                               f"{_f(sv.get('dsr'))} at K {sv['k_search']:,}) on {n} events; advancing to confirm needs "
                               f"DSR >= {gates.DSR_BAR} (ADR 0058 amendment 2026-10-30): it stays a recorded fact")
        batch = f"{RG._utcnow():%Y-%m}"
        if _batch_rows(rows, STATUS_CONFIRM, batch):
            raise StageRefused(f"the {batch} confirm batch has been read: pre-register into next month's batch")
        if len(_batch_rows(rows, PREREGISTERED, batch)) >= MONTHLY_MAX:
            raise StageRefused(f"{MONTHLY_MAX} confirm pre-registrations in {batch} already (design 3.3)")
        plan = {"spec_hash": h, "spec": canonical_json(spec), "timeframe": spec.timeframe, "batch": batch,
                "explore_mean_r": float(m), "explore_se": abs(float(m) / float(t)), "explore_n": n,
                "survivor_dsr": float(dsr), "survivor_k": k, "survivor_night": sv["night"],
                "survivor_dsr_at_night": sv.get("dsr"), "survivor_k_at_night": int(sv["k_search"]),
                "explore_cost_line": sv.get("cost_line"),
                "k_discovery_at_prereg": registry.n_trials_discovery, "window": window_text("confirm"),
                "flags": ledger.spec_flags(h)}
        return registry.preregister(agent_id=AGENT_ID, family=FAMILY, config=config_for(spec),
                                    feature_version=FEATURE_VERSION,
                                    rationale=f"autonomous discovery survivor {h[:12]} (ADR 0058 phase 3)",
                                    reading_rule=reading_rule(h, plan), plan=plan, rule_ref=ref)


def _f(x: Any) -> str:
    return "n/a" if x is None else f"{float(x):.3f}"


# ---------------------------------------------------------------- the rule
def holm(p: Sequence[float], alpha: float = HOLM_ALPHA) -> list[bool]:
    """Holm's step-down over the batch: the i-th smallest p (0-based) rejects at alpha / (m - i), stopping at the first
    that does not. Returns the rejections in the input order."""
    order = sorted(range(len(p)), key=lambda i: p[i])
    out = [False] * len(p)
    for rank, i in enumerate(order):
        if p[i] > alpha / (len(p) - rank):
            break
        out[i] = True
    return out


def positive_years(r: np.ndarray, ts: pd.DatetimeIndex) -> tuple[bool, str]:
    """The gates' positive-years check on the events' R (`gates.research_gates`, rule-only subject)."""
    taken = pd.DataFrame({"ts_utc": ts, "ret": np.asarray(r, dtype=float)})
    chk = next(c for c in gates.research_gates(0, [], taken, subject="rule-only")["checks"]
               if c["name"] == "positive_years")
    return bool(chk["passed"]), str(chk["detail"])


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def judge(read: StageRead, plan: dict[str, Any], k: int) -> list[dict[str, Any]]:
    """Checks (1), (2), (4), (5) of the reading rule; Holm (3) is added over the batch by `run_confirm_batch`."""
    r = read.r()
    n = read.n_events
    dsr = DsrInputs.of(r).dsr(k) if n >= MIN_TRADES_FOR_DSR else None
    years_ok, years = positive_years(r, read.ts()) if n else (False, "no events")
    m = read.mean_r
    half = PI_Z * plan["explore_se"] * float(np.sqrt(1 + plan["explore_n"] / max(n, 1)))
    lo, hi = plan["explore_mean_r"] - half, plan["explore_mean_r"] + half
    return [
        _check("dsr", dsr is not None and dsr >= gates.DSR_BAR,
               f"DSR {_f(dsr)} at K {k:,} on {n} events (need {MIN_TRADES_FOR_DSR}+ events and {gates.DSR_BAR})"),
        _check("mean_r", m is not None and m > 0, f"net mean R {_f(m)} (need > 0)"),
        _check("positive_years", years_ok, years),
        _check("stress", read.stress_mean_r is not None and read.stress_mean_r > 0,
               f"net mean R at {STRESS_MULT:g}x cost {_f(read.stress_mean_r)} (need > 0)"),
        _check("consistency", m is not None and lo <= m <= hi,
               f"confirm mean R {_f(m)} vs the explore 90% prediction interval [{lo:.3f}, {hi:.3f}]"),
    ]


def _cost_check(read: StageRead, cost_line: str) -> dict[str, Any]:
    return _check("costs", read.cost_line == cost_line and is_measured(read.cost_line),
                  f"read net of {read.cost_line or 'no cost line'} (declared: {cost_line})")


def p_value(t: float | None) -> float:
    """One-sided p of the clustered t (normal reference); no t: 1."""
    return 1.0 if t is None or not np.isfinite(t) else float(sps.norm.sf(t))


# ---------------------------------------------------------------- the confirm read
def run_confirm_batch(registry: TrialRegistry, ledger: SearchLedger, batch: str, read: Reader, *, cap: int,
                      reserved: int, cost_line: str, now: Any = None) -> list[ConfirmVerdict]:
    """Read the month's pre-registered batch once (module docstring). `cap` / `reserved`: the quarter's budget and
    reservation (research.budget_for / reserved_for); the check, the reads and the rows happen under the registry lock.
    `cost_line`: the measured cost table the reads are net of (`stage_costs`), checked before anything opens; a read on
    other costs fails its "costs" check."""
    _real(ledger)
    _require_measured(cost_line, "the confirm read")
    journal = StageJournal(ledger.path)
    with registry.locked():
        rows = registry._rows()
        preregs = _batch_rows(rows, PREREGISTERED, batch)
        if not preregs:
            raise StageRefused(f"no pre-registered confirm trial in batch {batch}: a confirm read needs its "
                               "pre-registration (preregister_confirm) first")
        if _batch_rows(rows, STATUS_CONFIRM, batch):
            raise StageRefused(f"the {batch} confirm batch has been read: it is read once")
        hashes = [str(p["plan"]["spec_hash"]) for p in preregs]
        for h in hashes:
            if journal.state(h).confirm is not None:
                raise StageRefused(f"spec {h[:12]} was read on the confirm window already")
        quarter, _ = registry.check_budget_reserved([(FAMILY, p["config"]) for p in preregs], cap, reserved, now,
                                                    retired=())
        sync_discovery_k(registry, ledger, generator="confirm precondition", lock=False)
        k = registry.n_trials_discovery + len(preregs)
        specs = {h: _spec(ledger, h) for h in hashes}
        for p, h in zip(preregs, hashes):
            journal.open("confirm", h, {"batch": batch, "n_trials": k, "prereg": row_ref(p), "window": window_text("confirm")})
        reads: dict[str, StageRead] = {}
        try:
            for h in hashes:
                reads[h] = read(specs[h])
        except Exception as exc:
            for p, h in zip(preregs, hashes):
                _void(registry, journal, "confirm", h, p["config"], p, quarter, batch, f"{type(exc).__name__}: {exc}")
            raise
        checks = {h: judge(reads[h], p["plan"], k) + [_cost_check(reads[h], cost_line)] for p, h in zip(preregs, hashes)}
        pv = [p_value(reads[h].t_stat) for h in hashes]
        rejects = holm(pv)
        out: list[ConfirmVerdict] = []
        for p, h, pval, rej in zip(preregs, hashes, pv, rejects):
            cs = checks[h] + [_check("holm", rej, f"clustered t {_f(reads[h].t_stat)}, one-sided p {pval:.4f}, Holm at "
                                                  f"{HOLM_ALPHA:.0%} over the batch of {len(hashes)}")]
            passed = all(c["passed"] for c in cs)
            failed = [f"{c['name']}: {c['detail']}" for c in cs if not c["passed"]]
            rd = reads[h]
            results = {"batch": batch, "passed": passed, "checks": cs, "n_trials": k, "n": rd.n_events,
                       "mean_r": rd.mean_r, "t_stat": rd.t_stat, "p_value": pval, "stress_mean_r": rd.stress_mean_r,
                       "input_digest": rd.input_digest, "cost_source": rd.cost_line, "window": window_text("confirm")}
            journal.close("confirm", h, results)
            row = registry.record(agent_id=AGENT_ID, family=FAMILY, config=p["config"], feature_version=FEATURE_VERSION,
                                  rationale=f"confirm read of {h[:12]} (ADR 0058 phase 3)", results=results,
                                  status=STATUS_CONFIRM, budget_quarter=quarter, preregistration=p,
                                  rule_ref=p["rule_ref"])
            registry.record_verdict(row_ref(row), "continue" if passed else "stop",
                                    "every part of the frozen confirm rule passed" if passed else "; ".join(failed),
                                    rule=RULE, agent_id="evaluator")
            out.append(ConfirmVerdict(spec_hash=h, passed=passed, checks=cs, failed=failed, n_trials=k,
                                      trial=int(row["trial"]), p_value=pval))
        return out


def _void(registry: TrialRegistry, journal: StageJournal, stage: Stage, h: str, cfg: dict[str, Any],
          prereg: dict[str, Any] | None, quarter: str, batch: str, why: str) -> None:
    """A read that did not complete: closed void, charged as a trial and stopped (it may have looked)."""
    results = {"batch": batch, "passed": False, "void": why, "window": window_text(stage)}
    journal.close(stage, h, results)
    row = registry.record(agent_id=AGENT_ID, family=FAMILY, config=cfg, feature_version=FEATURE_VERSION,
                          rationale=f"{stage} read of {h[:12]} did not complete", results=results,
                          status=STATUS_CONFIRM if stage == "confirm" else "holdout", budget_quarter=quarter,
                          preregistration=prereg, rule_ref=f"autodisc:{h}")
    registry.record_verdict(row_ref(row), "stop", f"void read ({why}): never read again", rule=RULE, agent_id="evaluator")


# ---------------------------------------------------------------- the holdout read
def holdout_read(registry: TrialRegistry, ledger: SearchLedger, h: str, read: Reader, *, cap: int, reserved: int,
                 cost_line: str, now: Any = None) -> HoldoutVerdict:
    """Read a confirm passer once on the holdout window (module docstring); StageRefused says why not. It passes on
    `gates.holdout_verdict` AND the t clustered by session day >= HOLDOUT_MIN_T (quant F4), net of the declared
    measured `cost_line`."""
    _real(ledger)
    _require_measured(cost_line, "the holdout read")
    spec = _spec(ledger, h)
    if ledger.spec_flags(h).get("holdout_contaminated"):
        raise StageRefused(f"spec {h[:12]} is holdout_contaminated: its holdout read is void, the forward demo read is "
                           "its test (design 3.5)")
    journal = StageJournal(ledger.path)
    st = journal.state(h)
    if st.holdout is not None:
        raise StageRefused(f"spec {h[:12]} was read on the holdout window already: the result is final")
    if st.confirm != "passed":
        raise StageRefused(f"spec {h[:12]} is not a confirm passer (confirm read: {st.confirm or 'none'})")
    cfg = config_for(spec)
    with registry.locked():
        rows = registry._rows()
        trial = next((r for r in rows if r.get("status") == STATUS_CONFIRM and r.get("config_hash") == RG.config_hash(cfg)),
                     None)
        v = verdict_of(rows, trial) if trial is not None else None
        if v != "continue":
            raise StageRefused(f"spec {h[:12]}: no 'continue' verdict on its confirm trial: the verdict row comes before "
                               "any follow-up")
        if registry.holdout_scored(FAMILY, cfg):
            raise StageRefused(f"spec {h[:12]} was scored on the holdout already: that result is final")
        quarter, _ = registry.check_budget_reserved([(FAMILY, cfg)], cap, reserved, now, retired=())
        journal.open("holdout", h, {"confirm_trial": row_ref(trial or {}), "window": window_text("holdout")})
        try:
            rd = read(spec)
        except Exception as exc:
            _void(registry, journal, "holdout", h, cfg, None, quarter, "", f"{type(exc).__name__}: {exc}")
            raise
        hv = gates.holdout_verdict(np.asarray(rd.ret, dtype=float), np.asarray(rd.risk, dtype=float))
        clustered = rd.t_stat is not None and rd.t_stat >= gates.HOLDOUT_MIN_T
        costs = _cost_check(rd, cost_line)
        passed = bool(hv["passed"]) and clustered and costs["passed"]
        results = {"passed": passed, "holdout": hv, "clustered_t": rd.t_stat, "costs": costs, "input_digest": rd.input_digest,
                   "cost_source": rd.cost_line, "window": window_text("holdout")}
        journal.close("holdout", h, results)
        row = registry.record(agent_id=AGENT_ID, family=FAMILY, config=cfg, feature_version=FEATURE_VERSION,
                              rationale=f"holdout read of {h[:12]} (ADR 0058 design 3.5)", results=results,
                              status="holdout", budget_quarter=quarter, rule_ref=f"autodisc:{h}")
        registry.record_verdict(row_ref(row), "continue" if passed else "stop",
                                f"holdout: {json.dumps({k: hv[k] for k in ('n', 'mean_r', 't_stat')})}, clustered t "
                                f"{_f(rd.t_stat)} (need {gates.HOLDOUT_MIN_T}); {hv['rule']}; {costs['detail']}",
                                rule="ADR 0058 design 3.5 (holdout, one-way)", agent_id="evaluator")
        return HoldoutVerdict(spec_hash=h, passed=passed, verdict=hv, trial=int(row["trial"]))


# ---------------------------------------------------------------- the real reader (the owner's Mac)
class StageProvider(PrimitiveProvider):
    """The explore MaskProvider over a stage window's 1m bars (warm-up included): decision bars close before the
    stage's end."""

    _explore_only = False

    def __init__(self, b1: StageFrame) -> None:
        self._end = WINDOWS[b1.stage][1]
        self._setup(b1.frame, None, None)

    def bars(self, timeframe: str) -> pd.DataFrame:
        if timeframe not in self._bars:
            b = super().bars(timeframe)
            self._bars[timeframe] = b[pd.DatetimeIndex(b["visible_at"]) < self._end].reset_index(drop=True)
        return self._bars[timeframe]


class SlicedProvider:
    """A MaskProvider's masks from decision bar `k0` on: the stage window's bars, the warm-up dropped."""

    def __init__(self, provider: MaskProvider, k0: int) -> None:
        self.p, self.k0 = provider, k0

    def family_entries(self, family: str, timeframe: str) -> tuple[BoolArr, SideArr]:
        m, s = self.p.family_entries(family, timeframe)
        return m[self.k0:], s[self.k0:]

    def predicate_mask(self, pred: Predicate, timeframe: str) -> BoolArr:
        return self.p.predicate_mask(pred, timeframe)[self.k0:]


def stage_years(stage: StageName) -> tuple[int, int]:
    """The 1m bar files a stage read opens: the year before its start (warm-up) to the year of its last day."""
    lo, hi = WINDOWS[stage]
    return (lo - pd.Timedelta(days=WARMUP_DAYS)).year, (hi - pd.Timedelta(days=1)).year


def stage_bars(raw: Path, stage: StageName) -> StageFrame:
    """The stage's 1m bars, cut to [start - WARMUP_DAYS, end) as they are loaded (the year file of the window's end
    also holds the next window's first months; they are dropped here, before any reader sees them)."""
    from goldbot.data.release import read_bar_files
    lo, hi = WINDOWS[stage]
    b = read_bar_files(raw, *stage_years(stage))
    ns, vis = (epoch_ns(pd.DatetimeIndex(b[c])) for c in ("ts_utc", "visible_at"))
    lo_ns, hi_ns = (int(epoch_ns(pd.DatetimeIndex([x]))[0]) for x in (lo - pd.Timedelta(days=WARMUP_DAYS), hi))
    return StageFrame(b.loc[(ns >= lo_ns) & (vis < hi_ns)].reset_index(drop=True), stage)   # visible before the end


def stage_costs(settings: Settings, cost_table: str | None = None) -> tuple[float, Any, str]:
    """(extra cost per oz, swap, cost line) of a stage read: the measured cost table only (the A4 cost floor; no
    silent fallback to the priors), at the same conservative value as the explore search (never below the settings
    prior). `cost_table` None: research.autodisc.cost_table."""
    from goldbot.execution.costs import load_research_cost_table, research_costs, settings_extra_cost_usd
    ct = cost_table if cost_table is not None else settings.research.autodisc.cost_table
    table = load_research_cost_table(ct) if ct else None
    if table is None:
        raise StageRefused(f"a stage read needs a measured cost table (A4 cost floor); {ct or 'none'} was given or found")
    extra, swap, source = research_costs(settings, table)
    _require_measured(source, "the stage read")
    return max(extra, settings_extra_cost_usd(settings)), swap, source


def stage_backends(settings: Settings, stage: StageName, timeframe: str, raw: Path | None = None,
                   cost_table: str | None = None) -> tuple[OutcomeTensor, MaskProvider, str]:
    """The stage window's outcome tensor (decision bars inside the window only), its mask provider and the cost line:
    the same conservative cost model as the explore search (`nightly.real_backends`)."""
    from goldbot.features.technical import atr
    from goldbot.research.autodisc.gen_combine import MAX_BARS
    from goldbot.research.autodisc.nightly import raw_dir
    from goldbot.research.autodisc.outcomes import build_tensor, default_menu
    a = settings.research.autodisc
    extra, swap, source = stage_costs(settings, cost_table)          # refused before any bar is read
    prov = StageProvider(stage_bars(raw if raw is not None else raw_dir(a.raw_dir), stage))
    bars = prov.bars(timeframe)
    lo = WINDOWS[stage][0]
    k0 = int(np.searchsorted(epoch_ns(pd.DatetimeIndex(bars["ts_utc"])), int(epoch_ns(pd.DatetimeIndex([lo]))[0])))
    t = build_tensor(StageFrame(bars.iloc[k0:].reset_index(drop=True), stage),
                     atr(prov.mid_bars(timeframe), 14).iloc[k0:].reset_index(drop=True),
                     default_menu(MAX_BARS[timeframe]), extra_cost_usd=extra, swap=swap)
    return t, SlicedProvider(prov, k0), source


def read_on(t: OutcomeTensor, provider: MaskProvider, cost_line: str = "") -> Reader:
    """The stage reader: the spec's entries, side and exit on the tensor, one position at a time, as the explore
    evaluator reads them."""
    res = Resolver(provider)

    def read(spec: StrategySpec) -> StageRead:
        mask, side = res.entries(spec)
        ev = t.select(mask, side, exit_index(t, spec))
        stress = float(ev.r(STRESS_MULT).mean()) if len(ev) else None
        return StageRead.of(ret=ev.ret, risk=ev.risk, ts_utc=ev.ts_utc, stress_mean_r=stress, input_digest=t.digest,
                            cost_line=cost_line)
    return read
