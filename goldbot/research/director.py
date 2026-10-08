"""Research director: decides what to research next from recorded evidence, never what goes live.

The design's boundary holds: nothing is promoted or traded by opinion. Promotion stays with the existing gates
(deflated Sharpe with the registry's trial count, trade counts, the shadow record, CUSUM). The director only splits
what is left of the quarter's trial budget between specialist families and ranks where the research effort should go, and it
does that with a fixed, deterministic rule over numbers already recorded:

* the trial registry (every walk-forward: `results.oof_auc`, `results.all_candidates.n`, `results.model_filtered`
  n / hit_rate / dsr, `results.n_candidates`, `results.n_folds`, `results.lookahead.lookahead_columns`);
* the shadow book per model version (state/shadow_<version>.json, PerfStats);
* the population's shadow scores per agent (state/population.json: n, per-trade Sharpe, fitness, ECE).

Evidence of signal per family (each component in [0, 1], summed to `evidence` in [0, 3]):

    auc_evidence    = clip(auc_z, 0, Z_FULL) / Z_FULL
                      auc_z = (median OOF AUC - 0.5) / se,  se = 1 / sqrt(3 * n_oof)
                      (the AUC's standard error under no skill with balanced classes, Hanley & McNeil; n_oof is the
                      median out-of-fold candidate count of the same trials). The median over trials, not the best,
                      so one lucky variant out of many does not count as signal.
    dsr_evidence    = clip((best DSR - 0.5) / (DSR_BAR - 0.5), 0, 1), over trials whose model-filtered trade count is
                      at least DSR_MIN_TRADES (a DSR on a handful of trades is noise); DSR_BAR is the promotion bar.
    shadow_evidence = clip(shadow_z, 0, Z_FULL) / Z_FULL, shadow_z = per-trade Sharpe x sqrt(n) (the t-statistic of
                      the mean shadow return), best over the family's model versions with at least MIN_SHADOW_TRADES
                      shadow trades and its population agents with at least MIN_RANK_TRADES.

Statistical budget (docs/proposals/2026-10-design-improvements.md, P1/P2): every trial raises the deflated-Sharpe bar
for any later real discovery, so the director plans only what is left of the quarter's trial budget
(`research.trial_budget_quarter`, DEFAULT_QUARTER_BUDGET when that setting does not exist), counted from the trial
registry. Pre-registered trials with a written rationale come first: at most GRID_SHARE of a family's allowance may be
spent by the monthly label grid (none while `trial_budget_per_month` is 0). Trials recorded with status "holdout"
are never read as evidence: the held-out year is scored once per configuration and must not steer the search.

Flags: `lookahead` (the family's latest trial that ran the lookahead check found columns using future data) blocks the
family: it gets no budget until a clean check is recorded. `few_candidates_per_fold` (best trial below
MIN_CANDIDATES_PER_FOLD, the design's per-fold minimum), `no_trials`, `no_oof_auc` and `few_filtered_trades` are
reported as reasons; they do not change the budget by themselves.

Allocation (`allocate`) is described in its docstring; every number in a ResearchPlan can be recomputed from the
evidence table stored with it.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.research.population import DSR_PROMOTE, MIN_RANK_TRADES
from goldbot.research.promotion import MIN_SHADOW_TRADES, PerfStats
from goldbot.research.registry import quarter_of, quarter_trials

Z_FULL = 3.0                     # three standard errors above chance counts as full evidence
DSR_BAR = DSR_PROMOTE            # 0.95: the deflated-Sharpe bar the gates use
DSR_MIN_TRADES = 200             # model-filtered trades before a trial's DSR counts as evidence (P1: no DSR below 200)
MIN_CANDIDATES_PER_FOLD = 60     # design: at least 60 labelled candidates per fold
PLAN_FILE = "research_plan.json"
DEFAULT_QUARTER_BUDGET = 20      # P2: at most 20 pre-registered trials a quarter across all families
DEFAULT_HOLDOUT_FROM = "2025-10-01"   # P2: the held-out year 2025-10-01 .. 2026-09-30
HOLDOUT_MONTHS = 12
GRID_SHARE = 0.5                 # at most half a family's allowance goes to label-grid variants
HOLDOUT_STATUS = "holdout"

ALLOCATION_RULE = (
    "Eligible families (not lookahead-blocked) each get floor = min(floor, budget // eligible, cap). The rest of the "
    "budget is split in proportion to evidence (equal split when every eligible family has zero evidence) by largest "
    "remainders (ties: higher evidence, then family name); a family above its cap is cut to the cap and the excess is "
    "split again among the others. Blocked families get 0. The total equals the monthly budget unless every eligible "
    "family is at its cap (then the rest is reported as unallocated). The budget is what is left of the quarter's "
    "trial budget; the label grid may use at most GRID_SHARE of a family's allowance, the rest is for pre-registered "
    "trials with a rationale.")


class ShadowEvidence(FrozenRecord):
    """One model version's shadow record (state/shadow_<version>.json) with the family it belongs to."""
    family: str
    version: str
    stats: PerfStats


class AgentEvidence(FrozenRecord):
    """One population member's shadow score (research/population.py Score fields the director reads)."""
    family: str
    agent_id: str
    status: str
    n: int = Field(ge=0)
    sharpe_per_trade: float = 0.0
    fitness: float = 0.0
    ece: float = 1.0


class FamilyScore(FrozenRecord):
    family: str
    trials: int
    trials_with_auc: int
    best_auc: float | None
    median_auc: float | None
    median_n_oof: int
    auc_margin: float | None          # median AUC - 0.5
    auc_se: float | None              # 1 / sqrt(3 * median_n_oof)
    auc_z: float | None
    max_filtered_trades: int
    best_dsr: float | None            # among trials with >= DSR_MIN_TRADES model-filtered trades
    best_dsr_trial: int | None
    best_candidates_per_fold: float | None
    lookahead_trial: int | None       # latest trial that ran the lookahead check
    lookahead_columns: list[str]
    shadow_trades: int                # most shadow trades of any version / agent of the family
    shadow_z: float | None
    shadow_source: str | None         # version or agent id behind shadow_z
    best_fitness: float
    best_fitness_ece: float | None
    auc_evidence: float
    dsr_evidence: float
    shadow_evidence: float
    evidence: float
    blocked: bool
    flags: list[str]


class FocusItem(FrozenRecord):
    rank: int
    family: str
    budget: int
    evidence: float
    reasons: list[str]


class ResearchPlan(FrozenRecord):
    created_utc: UtcTimestamp
    quarter: str                      # e.g. "2026Q4"
    quarter_budget: int               # trials allowed this quarter across all families
    quarter_used: int                 # trials already in the registry this quarter
    total_budget: int                 # planned now: min(remaining quarter budget, the monthly loop's flat total)
    floor: int
    cap: int | None
    budget: dict[str, int]            # trials per family for the rest of the quarter (pre-registered first)
    grid_budget: dict[str, int]       # of which the monthly label grid may use (<= GRID_SHARE, 0 while paused)
    unallocated: int
    holdout_from: str                 # the held-out window the plan never reads (ISO dates, end exclusive)
    holdout_to: str
    holdout_trials_ignored: int
    focus: list[FocusItem]
    evidence: list[FamilyScore]
    rule: str = ALLOCATION_RULE

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=1))
        tmp.replace(p)

    @classmethod
    def load(cls, path: str | Path) -> ResearchPlan | None:
        p = Path(path)
        return cls.model_validate(json.loads(p.read_text())) if p.exists() else None


# ---------------------------------------------------------------------------------------------- budget and holdout
def quarter_budget(research: Any) -> int:
    """research.trial_budget_quarter when the settings have it, else DEFAULT_QUARTER_BUDGET (read defensively: the
    setting arrives with the P2 change)."""
    v = getattr(research, "trial_budget_quarter", None)
    return int(v) if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else DEFAULT_QUARTER_BUDGET


def holdout_window(research: Any) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) of the held-out year: the settings' own window (ResearchSettings.holdout_window, the one the
    pipeline excludes) when there is one, else DEFAULT_HOLDOUT_FROM plus 12 months."""
    own = getattr(research, "holdout_window", None)
    if callable(own) and own() is not None:
        w = own()
        return pd.Timestamp(w[0]), pd.Timestamp(w[1])
    v = getattr(research, "holdout_from", None)
    start = pd.Timestamp(str(v) if v else DEFAULT_HOLDOUT_FROM)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    return start, start + pd.DateOffset(months=HOLDOUT_MONTHS)


def quarter_usage(trials: list[dict[str, Any]], now: pd.Timestamp, budget: int) -> tuple[str, int, int]:
    """(quarter label, trials recorded in `now`'s calendar quarter, remaining budget). Every registry row counts,
    whatever its status: each one was a look at the data. The count is registry.quarter_trials, the same one
    TrialRegistry.check_budget enforces."""
    q = quarter_of(now.tz_convert("UTC").to_pydatetime())
    used = quarter_trials(trials, q)
    return q, used, max(budget - used, 0)


def grid_allowance(budget: dict[str, int], trial_budget_per_month: int) -> dict[str, int]:
    """The label grid's share of each family's allowance: floor(GRID_SHARE x budget), at most the monthly grid size,
    and 0 while the grid is paused (trial_budget_per_month == 0)."""
    return {f: min(int(b * GRID_SHARE), trial_budget_per_month) for f, b in budget.items()}


# ---------------------------------------------------------------------------------------------- evidence
def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _clip01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def _shadow_z(stats: PerfStats) -> float | None:
    if stats.mean_ret is not None and stats.std_ret:
        return stats.mean_ret / stats.std_ret * math.sqrt(stats.n_trades)
    if stats.trades_per_week > 0:       # annualised Sharpe -> per trade
        return stats.sharpe_ann / math.sqrt(stats.trades_per_week * 52.0) * math.sqrt(stats.n_trades)
    return None


def score_family(family: str, trials: list[dict[str, Any]], shadow: list[ShadowEvidence],
                 agents: list[AgentEvidence]) -> FamilyScore:
    rows = sorted((r for r in trials if r.get("family") == family), key=lambda r: int(r.get("trial") or 0))
    aucs: list[float] = []
    n_oofs: list[int] = []
    max_filtered, best_dsr, best_dsr_trial = 0, None, None
    best_cpf: float | None = None
    la_trial: int | None = None
    la_cols: list[str] = []
    for r in rows:
        res = r.get("results") or {}
        auc = _num(res.get("oof_auc"))
        n_oof = int(_num((res.get("all_candidates") or {}).get("n")) or 0)
        if auc is not None and n_oof > 0:
            aucs.append(auc)
            n_oofs.append(n_oof)
        mf = res.get("model_filtered") or {}
        n_mf = int(_num(mf.get("n")) or 0)
        max_filtered = max(max_filtered, n_mf)
        dsr = _num(mf.get("dsr"))
        if dsr is not None and n_mf >= DSR_MIN_TRADES and (best_dsr is None or dsr > best_dsr):
            best_dsr, best_dsr_trial = dsr, int(r.get("trial") or 0)
        n_c, n_f = _num(res.get("n_candidates")), _num(res.get("n_folds"))
        if n_c is not None and n_f:
            cpf = n_c / n_f
            best_cpf = cpf if best_cpf is None else max(best_cpf, cpf)
        la = res.get("lookahead")
        if isinstance(la, dict) and "lookahead_columns" in la:
            la_trial, la_cols = int(r.get("trial") or 0), [str(c) for c in la.get("lookahead_columns") or []]

    best_auc = max(aucs) if aucs else None
    med_auc = float(statistics.median(aucs)) if aucs else None
    med_n = int(statistics.median(n_oofs)) if n_oofs else 0
    se = 1.0 / math.sqrt(3.0 * med_n) if med_n > 0 else None
    margin = med_auc - 0.5 if med_auc is not None else None
    z = margin / se if margin is not None and se else None

    sh_z: float | None = None
    sh_src: str | None = None
    sh_n = 0
    for s in shadow:
        if s.family != family:
            continue
        sh_n = max(sh_n, s.stats.n_trades)
        sz = _shadow_z(s.stats) if s.stats.n_trades >= MIN_SHADOW_TRADES else None
        if sz is not None and (sh_z is None or sz > sh_z):
            sh_z, sh_src = sz, s.version
    best_fit, best_fit_ece = 0.0, None
    for a in agents:
        if a.family != family:
            continue
        sh_n = max(sh_n, a.n)
        if a.n >= MIN_RANK_TRADES:
            az = a.sharpe_per_trade * math.sqrt(a.n)
            if sh_z is None or az > sh_z:
                sh_z, sh_src = az, a.agent_id
            if best_fit_ece is None or a.fitness > best_fit:
                best_fit, best_fit_ece = a.fitness, a.ece

    auc_ev = _clip01((z or 0.0) / Z_FULL)
    dsr_ev = _clip01(((best_dsr if best_dsr is not None else 0.5) - 0.5) / (DSR_BAR - 0.5))
    sh_ev = _clip01((sh_z or 0.0) / Z_FULL)

    flags: list[str] = []
    if la_cols:
        flags.append("lookahead")
    if not rows:
        flags.append("no_trials")
    elif not aucs:
        flags.append("no_oof_auc")
    if best_cpf is not None and best_cpf < MIN_CANDIDATES_PER_FOLD:
        flags.append("few_candidates_per_fold")
    if rows and max_filtered < DSR_MIN_TRADES:
        flags.append("few_filtered_trades")
    return FamilyScore(
        family=family, trials=len(rows), trials_with_auc=len(aucs), best_auc=best_auc, median_auc=med_auc,
        median_n_oof=med_n, auc_margin=margin, auc_se=se, auc_z=z, max_filtered_trades=max_filtered, best_dsr=best_dsr,
        best_dsr_trial=best_dsr_trial, best_candidates_per_fold=best_cpf, lookahead_trial=la_trial,
        lookahead_columns=la_cols, shadow_trades=sh_n, shadow_z=sh_z, shadow_source=sh_src, best_fitness=best_fit,
        best_fitness_ece=best_fit_ece, auc_evidence=auc_ev, dsr_evidence=dsr_ev, shadow_evidence=sh_ev,
        evidence=auc_ev + dsr_ev + sh_ev, blocked=bool(la_cols), flags=flags)


def score_families(families: list[str], trials: list[dict[str, Any]], shadow: list[ShadowEvidence],
                   agents: list[AgentEvidence]) -> list[FamilyScore]:
    return [score_family(f, trials, shadow, agents) for f in sorted(families)]


# ---------------------------------------------------------------------------------------------- allocation
def _largest_remainder(units: int, weights: dict[str, float]) -> dict[str, int]:
    """Split `units` in proportion to weights (all equal when they sum to 0); leftover units go to the largest
    fractional parts, ties to the larger weight, then the family name."""
    w = weights if sum(weights.values()) > 0 else {k: 1.0 for k in weights}
    total = sum(w.values())
    quota = {k: units * v / total for k, v in w.items()}
    out = {k: int(math.floor(q)) for k, q in quota.items()}
    left = units - sum(out.values())
    for k in sorted(w, key=lambda k: (-(quota[k] - out[k]), -w[k], k))[:left]:
        out[k] += 1
    return out


def allocate(scores: list[FamilyScore], monthly_budget: int, floor: int, cap: int | None = None) -> tuple[dict[str, int], int]:
    """Trial budget per family (monthly_budget: the trials being planned); returns (budget per family, unallocated).

    1. A family with lookahead-dirty features (FamilyScore.blocked) gets 0 until a clean check is recorded.
    2. Every other family gets the exploration floor f = min(floor, monthly_budget // n_eligible, cap), so no family
       is starved by a lucky streak elsewhere and a family with no evidence yet still gets looked at.
    3. The remaining R = monthly_budget - f * n_eligible is split in proportion to `evidence` (equally when every
       eligible family has zero evidence) by largest remainders, ties to higher evidence, then family name.
    4. With a cap (the number of distinct variants a family's search can run), a family above it is cut to the cap
       and the excess is split again among the uncapped families by step 3; whatever cannot be placed is unallocated.

    Sum of the budget == monthly_budget unless every eligible family is at its cap or every family is blocked.
    """
    if monthly_budget < 0 or floor < 0 or (cap is not None and cap < 0):
        raise ValueError("budget, floor and cap must be non-negative")
    alloc = {s.family: 0 for s in scores}
    eligible = sorted(s.family for s in scores if not s.blocked)
    if not eligible:
        return alloc, monthly_budget
    evidence = {s.family: s.evidence for s in scores}
    hi = cap if cap is not None else monthly_budget
    f = min(floor, monthly_budget // len(eligible), hi)
    for fam in eligible:
        alloc[fam] = f
    pool = monthly_budget - f * len(eligible)
    active = [fam for fam in eligible if alloc[fam] < hi]
    while pool > 0 and active:
        share = _largest_remainder(pool, {fam: evidence[fam] for fam in active})
        pool = 0
        for fam in active:
            give = min(share[fam], hi - alloc[fam])
            alloc[fam] += give
            pool += share[fam] - give
        active = [fam for fam in active if alloc[fam] < hi]
    return alloc, pool


# ---------------------------------------------------------------------------------------------- plan
def _reasons(s: FamilyScore) -> list[str]:
    out: list[str] = []
    if s.blocked:
        out.append(f"blocked: trial #{s.lookahead_trial}'s lookahead check found {len(s.lookahead_columns)} columns using "
                   f"future data ({', '.join(s.lookahead_columns[:5])}); fix the features and record a clean check")
    if s.median_auc is not None and s.auc_z is not None:
        out.append(f"median OOF AUC {s.median_auc:.3f} over {s.trials_with_auc} trials ({s.auc_margin:+.3f} vs chance, "
                   f"z {s.auc_z:.1f} at ~{s.median_n_oof} OOF candidates; best {s.best_auc:.3f})")
    if s.best_dsr is not None:
        out.append(f"best deflated Sharpe {s.best_dsr:.3f} (trial #{s.best_dsr_trial}; bar {DSR_BAR})")
    elif s.trials:
        out.append(f"no trial with {DSR_MIN_TRADES}+ model-filtered trades (most: {s.max_filtered_trades})")
    if s.shadow_z is not None:
        out.append(f"shadow t-stat {s.shadow_z:.1f} ({s.shadow_source}, {s.shadow_trades} shadow trades)")
    elif s.shadow_trades:
        out.append(f"{s.shadow_trades} shadow trades so far (ranked from {MIN_SHADOW_TRADES}-{MIN_RANK_TRADES})")
    if "few_candidates_per_fold" in s.flags and s.best_candidates_per_fold is not None:
        out.append(f"only {s.best_candidates_per_fold:.0f} candidates per fold (need {MIN_CANDIDATES_PER_FOLD}): "
                   "a label grid cannot fix this; a trigger change needs a written rationale")
    if "no_trials" in s.flags:
        out.append("no trials yet: exploration floor only")
    elif "no_oof_auc" in s.flags:
        out.append("no trial produced an out-of-fold AUC (too few candidates to fit)")
    return out


def build_plan(now: pd.Timestamp, families: list[str], trials: list[dict[str, Any]], shadow: list[ShadowEvidence],
               agents: list[AgentEvidence], *, quarter_budget: int, monthly_total: int, trial_budget_per_month: int,
               floor: int, cap: int | None = None,
               holdout: tuple[pd.Timestamp, pd.Timestamp] | None = None) -> ResearchPlan:
    """Plan for the rest of the quarter: total = min(remaining quarter budget, monthly_total); evidence never includes
    trials recorded with status "holdout"."""
    quarter, used, remaining = quarter_usage(trials, now, quarter_budget)
    total = min(remaining, monthly_total)
    evidence_rows = [r for r in trials if r.get("status") != HOLDOUT_STATUS]
    scores = score_families(families, evidence_rows, shadow, agents)
    budget, unallocated = allocate(scores, total, floor, cap)
    h0, h1 = holdout or holdout_window(None)
    order = sorted(scores, key=lambda s: (s.blocked, -s.evidence, s.family))
    focus = [FocusItem(rank=i + 1, family=s.family, budget=budget[s.family], evidence=round(s.evidence, 4),
                       reasons=_reasons(s)) for i, s in enumerate(order)]
    return ResearchPlan(created_utc=now, quarter=quarter, quarter_budget=quarter_budget, quarter_used=used,
                        total_budget=total, floor=floor, cap=cap, budget=budget,
                        grid_budget=grid_allowance(budget, trial_budget_per_month), unallocated=unallocated,
                        holdout_from=f"{h0:%Y-%m-%d}", holdout_to=f"{h1:%Y-%m-%d}",
                        holdout_trials_ignored=len(trials) - len(evidence_rows), focus=focus, evidence=scores)
