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
registry, AFTER the pre-registered queue's reservation (`reserved_trials`): budget - used - reserved, where reserved =
max(`research.reserved_trials_quarter` - trials run against one of the quarter's `preregistered` registry rows, the
quarter's preregistered rows not yet run). The monthly label grid is held to the same remainder. At most GRID_SHARE of
a family's allowance may be spent by the label grid (none while `research.label_grid_paused`). Trials recorded with
status "holdout" are never read as evidence: the held-out year is scored once per configuration and must not steer
the search.

Flags: `lookahead` (the family's latest trial that ran the lookahead check found columns using future data) blocks the
family: it gets no budget until a clean check is recorded. `few_candidates_per_fold` (best trial below
MIN_CANDIDATES_PER_FOLD, the design's per-fold minimum), `no_trials`, `no_oof_auc` and `few_filtered_trades` are
reported as reasons; they do not change the budget by themselves.

Out-of-sample attribution (state/attribution.json, research/attribution.py) is one more, bounded input. The director
rebuilds each family's cell from the report's per-trade rows (`trades`: taken, champion-path shadow trades), keeping
only trades entered on or after their model version's promotion (`promoted`, from the model registry; a version with
no promotion record is not counted), and for a retired family only trades entered on or after its retirement date.
A cell under the report's `min_trades` is noise; only a report dated at or before the plan and at most
ATTRIBUTION_MAX_AGE_DAYS old is read. The rows are the report's latest `attribution.trade_rows` taken trades, so a
truncated report gives a smaller sample, never a selected one. Per family:

    t_shrunk = net-R t-stat x n / (n + ATTRIBUTION_SHRINK_TRADES)      (shrunk toward 0 by trade count)
    term     = clip(t_shrunk / Z_FULL, -1, 1)

The attribution trades are the same shadow trades shadow_evidence already scores, so the term tilts a family only
when it has no shadow_z (one source of shadow evidence, never two). The term tilts the evidence-based split of the
pool (what is left after the floors), never the floors themselves:
with s_i the evidence-based share and s_bar = sum s_i x term_i, the tilted share is
s_i x (1 + lam x (term_i - s_bar)), lam = ATTRIBUTION_SHIFT_CAP / max(1, max |term_i - s_bar|). The shares still sum to
1 and no share moves by more than ATTRIBUTION_SHIFT_CAP (25%) of its evidence-based value; a family with zero
evidence share stays at zero (attribution cannot create a share, only tilt one). Without a usable report every term
is 0 and the plan is the evidence-only plan, bit for bit.

Retired ideas: the list is governance, so it lives in settings (`research.retired_families`: family, hypothesis id,
retired date, reason, registry trials). docs/research/hypotheses.md section B mirrors it; the plan stores the doc's
sha256 and any disagreement (`retired_drift`), but the doc never changes the allocation by itself. A retired family
keeps an exploration floor of RETIRED_FLOOR trial a quarter (less the trials it already had this quarter) and nothing
from the evidence pool. It is reinstated (back to the normal floor and pool) by one source only: its attribution
trades entered after the retirement date, non-noise, with t_shrunk >= reinstatement_threshold(m), REINSTATE_T's
one-sided tail divided by m = retired families tested x REINSTATE_SOURCES (Bonferroni; 5 families: 2.61). Shadow
books are not a reinstatement source: they carry no per-trade dates, so they cannot be cut at the retirement date.
A lookahead-dirty family gets 0 whatever its evidence.

Allocation (`allocate`) is described in its docstring; every number in a ResearchPlan can be recomputed from the
evidence table stored with it, and `ResearchPlan.moves` records which input (retirement, attribution) moved which
family's budget and share, and by how much, against the evidence-only plan.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.config import RetiredFamily
from goldbot.research.attribution import T_SIGNIFICANT
from goldbot.research.population import DSR_PROMOTE, MIN_RANK_TRADES
from goldbot.research.promotion import MIN_SHADOW_TRADES, PerfStats
from goldbot.research.registry import PREREGISTERED, is_trial, quarter_of, quarter_trials

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
ATTRIBUTION_SHIFT_CAP = 0.25     # attribution may move a family's share of the evidence split by at most +-25%
ATTRIBUTION_SHRINK_TRADES = 100  # t x n / (n + 100): 30 trades keep 23% of the t-stat, 300 keep 75%
ATTRIBUTION_MAX_AGE_DAYS = 14    # an older attribution report (the job runs daily) is not used
RETIRED_FLOOR = 1                # exploration trials a quarter a retired family keeps (never from the evidence pool)
REINSTATE_T = 2.0                # one-sided bar on the shrunk attribution t before the multiple-testing correction
REINSTATE_SOURCES = 1            # reinstatement sources tested: attribution trades after the retirement date, only
GRID_RATIONALE = "monthly bounded label-grid search"   # the rationale prefix jobs.monthly_research records
ATTRIBUTION_FILE = "attribution.json"   # research/attribution.py REPORT_FILE, in the state directory
HYPOTHESES_PATH = Path(__file__).resolve().parents[2] / "docs" / "research" / "hypotheses.md"
RETIRED_SECTION = "## B."
# Section B names ideas, not always family names: the lead words of its "Idea" cell that mean a specialist family.
IDEA_ALIASES = {"range breakout": "breakout", "trend pullback": "trend", "session_open breakout": "session_open"}

ALLOCATION_RULE = (
    "The budget is what is left of the quarter's trial budget after the trials already run and the pre-registered "
    "queue's reservation. Eligible families (not lookahead-blocked, not retired) each get floor = min(floor, budget // "
    "eligible, cap); then each retired family gets its exploration floor (1 a quarter, less its trials this quarter). "
    "The rest is split among eligible families in proportion to evidence (equal split when every eligible family has "
    "zero evidence) by largest remainders (ties: higher evidence, then family name); a family above its cap is cut to "
    "the cap and the excess is split again among the others. Blocked families get 0. The total equals the budget "
    "unless every eligible family is at its cap (then the rest is reported as unallocated). The label grid may use at "
    "most GRID_SHARE of a family's allowance (0 while paused), the rest is for pre-registered trials with a rationale. "
    "Retired families (settings research.retired_families) are reinstated only by attribution trades after the "
    "retirement date with shrunk t >= the Bonferroni-corrected bar. The attribution term (per-trade rows of "
    "state/attribution.json from each version's promotion on, net-R t-stat shrunk by n/(n+100), clipped to +-1 at "
    "t=3) tilts the evidence share of an eligible family without shadow evidence by at most +-25%; the floor is "
    "never tilted.")


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


class AttributionEvidence(FrozenRecord):
    """One family's out-of-sample cell, rebuilt from the attribution report's per-trade rows (state/attribution.json
    `trades`), and the bounded term the director derives from it (0 when the cell is noise or the report unusable)."""
    n: int = 0
    mean_r_net: float | None = None
    t_stat: float | None = None
    verdict: str = "missing"
    used: bool = False                # cell not noise, finite t-stat, report usable
    t_shrunk: float = 0.0             # t x n / (n + ATTRIBUTION_SHRINK_TRADES)
    term: float = 0.0                 # clip(t_shrunk / Z_FULL, -1, 1)
    excluded_pre_promotion: int = 0   # trades entered before their version's promotion (or of an unpromoted version)
    excluded_pre_retirement: int = 0  # retired family: trades entered before its retirement date
    since: str | None = None          # retired family: the retirement date the cell starts from
    tilts: bool = False               # the term is applied (used, no shadow_z: one shadow source, not two)


class Reservation(FrozenRecord):
    """Trials of the quarter held for the pre-registered queue (`reserved_trials`)."""
    setting: int                      # research.reserved_trials_quarter
    run: int                          # trials of the quarter run against one of its preregistered rows
    pending: int                      # preregistered rows of the quarter not yet run
    reserved: int                     # max(setting - run, pending)


class EvidenceMove(FrozenRecord):
    """What one input changed for one family against the plan without it (budget in trials, share of the pool)."""
    family: str
    source: str                       # "retired_families" | "attribution"
    detail: str
    budget_before: int
    budget_after: int
    share_before: float | None = None  # attribution: evidence-based share of the pool, then the tilted share
    share_after: float | None = None
    shift_pct: float | None = None     # (share_after / share_before - 1) x 100, |.| <= 25


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
    retired_id: str | None = None     # research.retired_families entry's hypothesis id (hypotheses.md section B row)
    retired_status: str | None = None  # its reason
    retired_since: str | None = None   # its retirement date
    retired: bool = False             # retired and not reinstated: the exploration floor only
    new_evidence: list[str] = Field(default_factory=list)   # what reinstated it
    attribution: AttributionEvidence | None = None


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
    evidence_budget: dict[str, int] = Field(default_factory=dict)   # registry + shadow evidence only (no inputs below)
    moves: list[EvidenceMove] = Field(default_factory=list)
    attribution_as_of: str | None = None
    attribution_note: str | None = None
    hypotheses_note: str | None = None
    quarter_reserved: int = 0                                       # held for the pre-registered queue
    reservation: Reservation | None = None
    retired_floor: dict[str, int] = Field(default_factory=dict)     # exploration floor per retired family, this quarter
    reinstate_t: float | None = None                                # corrected bar on the shrunk attribution t
    hypotheses_sha256: str | None = None                            # the doc as read at plan time
    hypotheses_drift: list[str] = Field(default_factory=list)       # where it disagrees with research.retired_families

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


def grid_allowance(budget: dict[str, int], trial_budget_per_month: int, paused: bool = False) -> dict[str, int]:
    """The label grid's share of each family's allowance: floor(GRID_SHARE x budget), at most the monthly grid size,
    and 0 while the grid is paused (research.label_grid_paused, or trial_budget_per_month == 0)."""
    per_month = 0 if paused else trial_budget_per_month
    return {f: min(int(b * GRID_SHARE), per_month) for f, b in budget.items()}


def _row_quarter(row: dict[str, Any]) -> str | None:
    try:
        ts = datetime.fromisoformat(str(row.get("ts")))
    except ValueError:
        return None
    return quarter_of(ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc))


def reserved_trials(rows: list[dict[str, Any]], quarter: str, setting: int) -> Reservation:
    """Trials of `quarter` held for the pre-registered queue, counted BEFORE the director's pool and the label grid:
    max(setting - run, pending). `run` counts the quarter's trials that were run against one of its `preregistered`
    rows (linked by `preregistration.trial`, or the same family and config hash with a trial number at or after the
    pre-registration's); `pending` counts the preregistered rows not yet run. So the reservation shrinks only as the
    pre-registered plan is executed, a queued pre-registration is always covered, and a row is never counted twice."""
    prereg = [r for r in rows if r.get("status") == PREREGISTERED and _row_quarter(r) == quarter]
    matched: set[int] = set()
    for t in rows:
        if not is_trial(t) or _row_quarter(t) != quarter:
            continue
        link = (t.get("preregistration") or {}).get("trial") if isinstance(t.get("preregistration"), dict) else None
        n = int(_num(t.get("trial")) or 0)
        for i, p in enumerate(prereg):
            if i in matched:
                continue
            same = p.get("family") == t.get("family") and p.get("config_hash") == t.get("config_hash") \
                and n >= int(_num(p.get("trial")) or 0)
            if (link is not None and p.get("trial") == link) or (link is None and same):
                matched.add(i)
                break
    run, pending = len(matched), len(prereg) - len(matched)
    return Reservation(setting=setting, run=run, pending=pending, reserved=max(setting - run, pending, 0))


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


# ---------------------------------------------------------------------------------------------- attribution, hypotheses
def _ts(v: Any) -> pd.Timestamp | None:
    try:
        t = pd.Timestamp(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(t):
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _verdict(n: int, t: float | None, min_trades: int) -> str:
    if n < min_trades or t is None:
        return "noise"
    if abs(t) >= T_SIGNIFICANT:
        return "positive" if t > 0 else "negative"
    return "indistinguishable from zero"


def attribution_evidence(report: dict[str, Any] | None, families: list[str], now: pd.Timestamp,
                         promoted: Mapping[str, pd.Timestamp | None] | None = None,
                         since: Mapping[str, pd.Timestamp] | None = None,
                         ) -> tuple[dict[str, AttributionEvidence], str | None, str]:
    """Per family the out-of-sample cell and its bounded term; (evidence, report as_of, note). Cells are rebuilt from
    the report's per-trade rows: a trade counts only when its version has a promotion date in `promoted` and it was
    entered on or after it, and, for a family in `since` (retired), entered on or after that date. A cell under the
    report's `min_trades` is noise. Only a report dated at or before `now` (point in time) and at most
    ATTRIBUTION_MAX_AGE_DAYS old is read; anything else gives term 0."""
    empty = {f: AttributionEvidence() for f in families}
    if not isinstance(report, dict):
        return empty, None, "no attribution report: no attribution term"
    as_of = _ts(report.get("as_of")) if isinstance(report.get("as_of"), str) else None
    if as_of is None:
        return empty, None, "attribution report without a readable as_of: not used"
    stamp = f"{as_of:%Y-%m-%dT%H:%M:%SZ}"
    if as_of > now:
        return empty, stamp, "attribution report dated after the plan (not visible at plan time): not used"
    if now - as_of > pd.Timedelta(days=ATTRIBUTION_MAX_AGE_DAYS):
        return empty, stamp, f"attribution report older than {ATTRIBUTION_MAX_AGE_DAYS} days: not used"
    min_trades = int(_num(report.get("min_trades")) or 0)
    promo = {str(k): _ts(v) for k, v in (promoted or {}).items() if v is not None}
    cut = dict(since or {})
    rets: dict[str, list[float]] = {f: [] for f in families}
    pre_promo = {f: 0 for f in families}
    pre_ret = {f: 0 for f in families}
    rows = [r for r in report.get("trades") or [] if isinstance(r, dict)]
    for r in rows:
        fam = str(r.get("family"))
        if fam not in rets or r.get("taken") is False:
            continue
        entry, x = _ts(r.get("entry_utc")), _num(r.get("r_net"))
        if entry is None or x is None or entry > now:
            continue
        p = promo.get(str(r.get("version")))
        if p is None or entry < p:
            pre_promo[fam] += 1
            continue
        if fam in cut and entry < cut[fam]:
            pre_ret[fam] += 1
            continue
        rets[fam].append(x)
    out: dict[str, AttributionEvidence] = {}
    for f in families:
        v = rets[f]
        n = len(v)
        mean = statistics.fmean(v) if v else None
        sd = statistics.stdev(v) if n >= 2 else 0.0
        t = mean / sd * math.sqrt(n) if mean is not None and sd > 0 else None
        verdict = _verdict(n, t, min_trades)
        used = verdict != "noise"
        t_shrunk = (t or 0.0) * n / (n + ATTRIBUTION_SHRINK_TRADES) if used else 0.0
        out[f] = AttributionEvidence(
            n=n, mean_r_net=None if mean is None else round(mean, 9), t_stat=None if t is None else round(t, 9),
            verdict=verdict if n else "missing", used=used, t_shrunk=round(t_shrunk, 9),
            term=round(min(max(t_shrunk / Z_FULL, -1.0), 1.0), 9), excluded_pre_promotion=pre_promo[f],
            excluded_pre_retirement=pre_ret[f], since=f"{cut[f]:%Y-%m-%d}" if f in cut else None)
    used_n = sum(1 for e in out.values() if e.used)
    n_taken = int(_num(report.get("n_taken")) or 0)
    trunc = (f"; the report keeps the latest {len(rows)} of {n_taken} taken trades (attribution.trade_rows)"
             if n_taken > len(rows) else "")
    return out, stamp, (f"attribution report {stamp}: {used_n} non-noise family cells from its per-trade rows (min "
                        f"{min_trades} trades; {sum(pre_promo.values())} trades before their version's promotion and "
                        f"{sum(pre_ret.values())} before a retirement date left out{trunc})")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def hypotheses_retired(text: str, families: list[str]) -> tuple[dict[str, tuple[str, list[int]]], list[str]]:
    """(family -> (row id, registry trials), problems) for docs/research/hypotheses.md section B rows whose status
    starts with "retired". A row names a family when the first word of its Idea cell is the family name, or its lead
    words are an IDEA_ALIASES key. Read-only, and only for the consistency check (`retired_drift`): the allocation
    reads research.retired_families. A missing section or a table without ID / Idea / Status columns is a problem,
    never "nothing retired"."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(RETIRED_SECTION)), None)
    if start is None:
        return {}, [f"hypotheses.md has no '{RETIRED_SECTION}' section"]
    header: list[str] | None = None
    out: dict[str, tuple[str, list[int]]] = {}
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        if not ln.lstrip().startswith("|"):
            continue
        cells = _cells(ln)
        if header is None:
            header = [c.lower() for c in cells]
            if not {"id", "idea", "status"} <= set(header):
                return {}, [f"hypotheses.md section B table header {cells} lacks ID, Idea and Status columns"]
            continue
        if len(cells) != len(header) or set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(header, cells))
        if not row["status"].strip().lower().startswith("retired"):
            continue
        lead = row["idea"].split("(")[0].strip().lower()
        words = lead.split()
        fam = IDEA_ALIASES.get(lead) or (words[0] if words else "")
        col = next((h for h in header if h.startswith("trials")), None)
        trials = [int(x) for x in re.findall(r"#(\d+)", row[col].split("(")[0])] if col else []
        if fam in families and fam not in out:
            out[fam] = (row["id"], trials)
    if header is None:
        return {}, ["hypotheses.md section B has no table"]
    return out, []


def retired_drift(retired: Sequence[RetiredFamily], text: str | None, families: list[str]) -> list[str]:
    """Where research.retired_families (the governance) and hypotheses.md section B (its mirror) disagree: family,
    row id or registry trials. Empty when they agree. Reported in the plan; never changes the allocation."""
    if text is None:
        return ["docs/research/hypotheses.md missing: research.retired_families cannot be checked against it"]
    doc, problems = hypotheses_retired(text, families)
    cfg = {e.family: e for e in retired}
    for fam in sorted(set(doc) | set(cfg)):
        if fam not in families:
            problems.append(f"{fam}: in research.retired_families but not a specialist family")
        elif fam not in cfg:
            problems.append(f"{fam}: retired in hypotheses.md ({doc[fam][0]}) but not in research.retired_families")
        elif fam not in doc:
            problems.append(f"{fam}: in research.retired_families ({cfg[fam].hypothesis_id}) but not retired in "
                            "hypotheses.md section B")
        else:
            if doc[fam][0] != cfg[fam].hypothesis_id:
                problems.append(f"{fam}: hypotheses.md row {doc[fam][0]} vs settings {cfg[fam].hypothesis_id}")
            if sorted(doc[fam][1]) != sorted(cfg[fam].trials):
                problems.append(f"{fam}: hypotheses.md trials {sorted(doc[fam][1])} vs settings {sorted(cfg[fam].trials)}")
    return problems


def reinstatement_threshold(n_tests: int) -> float:
    """The bar a retired family's shrunk attribution t must reach: REINSTATE_T's one-sided tail probability divided by
    the number of tests (retired families x REINSTATE_SOURCES; Bonferroni), back to a z. 1 test: 2.0; 5: 2.61."""
    nd = statistics.NormalDist()
    return nd.inv_cdf(1.0 - (1.0 - nd.cdf(REINSTATE_T)) / max(n_tests, 1))


def load_attribution(state_dir: str | Path) -> dict[str, Any] | None:
    """state/attribution.json, or None when missing or unreadable (then the plan has no attribution term)."""
    f = Path(state_dir) / ATTRIBUTION_FILE
    try:
        data = json.loads(f.read_text()) if f.exists() else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_hypotheses(path: str | Path = HYPOTHESES_PATH) -> str | None:
    p = Path(path)
    try:
        return p.read_text() if p.exists() else None
    except OSError:
        return None


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


def tilted_shares(evidence: dict[str, float], term: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    """(evidence-based shares, attribution-tilted shares) over the families in `evidence`: s_i x (1 + lam x (term_i -
    s_bar)), s_bar the share-weighted mean term, lam = ATTRIBUTION_SHIFT_CAP / max(1, max |term_i - s_bar|). Shares sum
    to 1 before and after; no share moves by more than ATTRIBUTION_SHIFT_CAP of itself."""
    w = evidence if sum(evidence.values()) > 0 else {k: 1.0 for k in evidence}
    total = sum(w.values())
    base = {k: v / total for k, v in w.items()}
    t = {k: min(max(term.get(k, 0.0), -1.0), 1.0) for k in base}
    s_bar = sum(base[k] * t[k] for k in base)
    dev = {k: t[k] - s_bar for k in base}
    lam = ATTRIBUTION_SHIFT_CAP / max(1.0, max((abs(d) for d in dev.values()), default=0.0))
    return base, {k: base[k] * (1.0 + lam * dev[k]) for k in base}


def allocate(scores: list[FamilyScore], monthly_budget: int, floor: int, cap: int | None = None,
             tilt: dict[str, float] | None = None,
             retired_floor: Mapping[str, int] | None = None) -> tuple[dict[str, int], int]:
    """Trial budget per family (monthly_budget: the trials being planned); returns (budget per family, unallocated).

    1. A family with lookahead-dirty features (FamilyScore.blocked) gets 0 until a clean check is recorded.
    2. Every eligible family (not blocked, not .retired) gets the exploration floor f = min(floor, monthly_budget //
       n_eligible, cap), so no family is starved by a lucky streak elsewhere and a family with no evidence yet still
       gets looked at.
    3. Each retired family (research.retired_families, not reinstated, not blocked) then gets its `retired_floor`
       (RETIRED_FLOOR a quarter less its trials this quarter), in family order while trials are left, and nothing
       more: retirement is never permanent, and never more than exploration.
    4. The rest is split among eligible families in proportion to `evidence` (equally when every eligible family has
       zero evidence) by largest remainders, ties to higher evidence, then family name.
    5. With a cap (the number of distinct variants a family's search can run), a family above it is cut to the cap
       and the excess is split again among the uncapped families by step 4; whatever cannot be placed is unallocated.
    6. `tilt` (attribution terms in [-1, 1]) replaces the evidence weights of step 4 by `tilted_shares`: each share of
       the split moves by at most ATTRIBUTION_SHIFT_CAP of itself. All terms 0 (or no tilt) is exactly steps 1-5.

    Sum of the budget == monthly_budget unless every eligible family is at its cap or none is eligible.
    """
    if monthly_budget < 0 or floor < 0 or (cap is not None and cap < 0):
        raise ValueError("budget, floor and cap must be non-negative")
    alloc = {s.family: 0 for s in scores}
    eligible = sorted(s.family for s in scores if not s.blocked and not s.retired)
    retirees = sorted(s.family for s in scores if s.retired and not s.blocked)
    hi = cap if cap is not None else monthly_budget
    left = monthly_budget
    if eligible:
        f = min(floor, monthly_budget // len(eligible), hi)
        for fam in eligible:
            alloc[fam] = f
        left -= f * len(eligible)
    for fam in retirees:
        give = max(min((retired_floor or {}).get(fam, 0), left, hi), 0)
        alloc[fam] = give
        left -= give
    evidence = {s.family: s.evidence for s in scores}
    pool = left
    active = [fam for fam in eligible if alloc[fam] < hi]
    while pool > 0 and active:
        weights = {fam: evidence[fam] for fam in active}
        if tilt and any(tilt.get(fam, 0.0) != 0.0 for fam in active):
            weights = tilted_shares(weights, tilt)[1]
        share = _largest_remainder(pool, weights)
        pool = 0
        for fam in active:
            give = min(share[fam], hi - alloc[fam])
            alloc[fam] += give
            pool += share[fam] - give
        active = [fam for fam in active if alloc[fam] < hi]
    return alloc, pool


# ---------------------------------------------------------------------------------------------- plan
def _reasons(s: FamilyScore, retired_floor: int = 0) -> list[str]:
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
    if s.retired:
        out.append(f"retired since {s.retired_since} ({s.retired_id}: {s.retired_status}); not reinstated by "
                   f"attribution after that date: exploration floor of {retired_floor} trial(s) left this quarter")
    elif s.retired_id is not None:
        out.append(f"retired since {s.retired_since} ({s.retired_id}) but reinstated: {'; '.join(s.new_evidence)}")
    a = s.attribution
    if a is not None and a.used and a.t_stat is not None:
        net = f"net R {a.mean_r_net:+.3f}" if a.mean_r_net is not None else "net R n/a"
        use = "tilts the split" if a.tilts else ("not a tilt: shadow evidence already counts these trades"
                                                 if s.shadow_z is not None else "not a tilt")
        out.append(f"attribution: {net} over {a.n} out-of-sample shadow trades, t {a.t_stat:+.2f} "
                   f"(shrunk {a.t_shrunk:+.2f}, term {a.term:+.2f}; {a.verdict}; {use})")
    elif a is not None and a.n:
        out.append(f"attribution: {a.n} out-of-sample shadow trades, {a.verdict}: not evidence")
    if "few_candidates_per_fold" in s.flags and s.best_candidates_per_fold is not None:
        out.append(f"only {s.best_candidates_per_fold:.0f} candidates per fold (need {MIN_CANDIDATES_PER_FOLD}): "
                   "a label grid cannot fix this; a trigger change needs a written rationale")
    if "no_trials" in s.flags:
        out.append("no trials yet: exploration floor only")
    elif "no_oof_auc" in s.flags:
        out.append("no trial produced an out-of-fold AUC (too few candidates to fit)")
    return out


def _with_inputs(s: FamilyScore, attr: AttributionEvidence, retired: RetiredFamily | None,
                 reinstate_t: float) -> FamilyScore:
    """The score with its attribution cell and retirement. A retired family is reinstated by one source only: its
    attribution trades after the retirement date (already cut in `attr`), non-noise, shrunk t >= reinstate_t. The
    attribution term tilts only a family without shadow_z (the same shadow trades are not counted twice)."""
    reinstated = retired is not None and attr.used and attr.t_shrunk >= reinstate_t
    new = ([f"attribution after {attr.since}: net-R t {attr.t_stat:+.2f}, shrunk {attr.t_shrunk:+.2f} >= "
            f"{reinstate_t:.2f} on {attr.n} trades"] if reinstated and attr.t_stat is not None else [])
    is_retired = retired is not None and not reinstated
    attr = attr.model_copy(update={"tilts": attr.used and attr.term != 0.0 and s.shadow_z is None and not is_retired})
    return s.model_copy(update={
        "attribution": attr, "retired_id": retired.hypothesis_id if retired else None,
        "retired_status": retired.reason if retired else None,
        "retired_since": f"{retired.retired:%Y-%m-%d}" if retired else None, "retired": is_retired,
        "new_evidence": new})


def _pool_shares(scores: list[FamilyScore], tilt: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    eligible = {s.family: s.evidence for s in scores if not s.blocked and not s.retired}
    return tilted_shares(eligible, tilt) if eligible else ({}, {})


def build_plan(now: pd.Timestamp, families: list[str], trials: list[dict[str, Any]], shadow: list[ShadowEvidence],
               agents: list[AgentEvidence], *, quarter_budget: int, monthly_total: int, trial_budget_per_month: int,
               floor: int, cap: int | None = None,
               holdout: tuple[pd.Timestamp, pd.Timestamp] | None = None,
               attribution: dict[str, Any] | None = None, hypotheses: str | None = None,
               retired: Sequence[RetiredFamily] = (), promoted: Mapping[str, pd.Timestamp | None] | None = None,
               reserved_setting: int = 0, grid_paused: bool = False) -> ResearchPlan:
    """Plan for the rest of the quarter: total = min(quarter budget - used - reserved, monthly_total); evidence never
    includes trials recorded with status "holdout". `attribution` is the parsed state/attribution.json (None: no
    term), `promoted` each model version's promotion time (model registry), `retired` research.retired_families,
    `reserved_setting` research.reserved_trials_quarter. `hypotheses` (the text of docs/research/hypotheses.md) is
    only hashed and checked against `retired`: it never moves a trial. A pure function of its inputs."""
    quarter, used, _ = quarter_usage(trials, now, quarter_budget)
    reservation = reserved_trials(trials, quarter, reserved_setting)
    total = min(max(quarter_budget - used - reservation.reserved, 0), monthly_total)
    evidence_rows = [r for r in trials if r.get("status") != HOLDOUT_STATUS]
    fams = sorted(families)
    retired_map = {e.family: e for e in retired if e.family in fams}
    since = {f: pd.Timestamp(e.retired).tz_localize("UTC") for f, e in retired_map.items()}
    attr, attr_as_of, attr_note = attribution_evidence(attribution, fams, now, promoted, since)
    reinstate_t = reinstatement_threshold(len(retired_map) * REINSTATE_SOURCES)
    base_scores = score_families(fams, evidence_rows, shadow, agents)
    scores = [_with_inputs(s, attr[s.family], retired_map.get(s.family), reinstate_t) for s in base_scores]
    q_family = {f: sum(1 for r in trials if is_trial(r) and r.get("family") == f and _row_quarter(r) == quarter)
                for f in retired_map}
    retired_floor = {f: max(RETIRED_FLOOR - q_family[f], 0) for f in retired_map}
    evidence_budget, _ = allocate(base_scores, total, floor, cap)
    untilted, _ = allocate(scores, total, floor, cap, retired_floor=retired_floor)
    tilt = {s.family: s.attribution.term for s in scores if s.attribution is not None and s.attribution.tilts}
    budget, unallocated = allocate(scores, total, floor, cap, tilt=tilt or None, retired_floor=retired_floor)

    moves: list[EvidenceMove] = []
    for s in scores:
        if s.retired or untilted[s.family] != evidence_budget[s.family]:
            detail = (f"retired ({s.retired_id}, {s.retired_since}) and not reinstated: exploration floor only"
                      if s.retired else "budget freed by retired families, split by the allocation rule")
            moves.append(EvidenceMove(family=s.family, source="retired_families", detail=detail,
                                      budget_before=evidence_budget[s.family], budget_after=untilted[s.family]))
    base_share, tilted = _pool_shares(scores, tilt)
    for s in scores:
        a = s.attribution
        if s.family not in base_share or a is None:
            continue
        b, t = base_share[s.family], tilted[s.family]
        if abs(t - b) <= 1e-12 and budget[s.family] == untilted[s.family]:
            continue
        detail = (f"attribution t {a.t_stat:+.2f} on {a.n} trades -> term {a.term:+.3f}"
                  if a.tilts and a.t_stat is not None else "share moved by other families' attribution terms")
        moves.append(EvidenceMove(family=s.family, source="attribution", detail=detail,
                                  budget_before=untilted[s.family], budget_after=budget[s.family],
                                  share_before=round(b, 6), share_after=round(t, 6),
                                  shift_pct=round((t / b - 1.0) * 100.0, 4) if b > 0 else 0.0))

    h0, h1 = holdout or holdout_window(None)
    order = sorted(scores, key=lambda s: (s.blocked or s.retired, -s.evidence, s.family))
    focus = [FocusItem(rank=i + 1, family=s.family, budget=budget[s.family], evidence=round(s.evidence, 4),
                       reasons=_reasons(s, retired_floor.get(s.family, 0))) for i, s in enumerate(order)]
    drift = retired_drift(list(retired), hypotheses, fams)
    hyp_note = (f"research.retired_families: {sorted(retired_map) or 'none'}; docs/research/hypotheses.md section B "
                + ("agrees" if not drift else f"disagrees in {len(drift)} place(s) (hypotheses_drift)")
                + "; the doc never moves a trial")
    return ResearchPlan(created_utc=now, quarter=quarter, quarter_budget=quarter_budget, quarter_used=used,
                        total_budget=total, floor=floor, cap=cap, budget=budget,
                        grid_budget=grid_allowance(budget, trial_budget_per_month, grid_paused),
                        unallocated=unallocated, holdout_from=f"{h0:%Y-%m-%d}", holdout_to=f"{h1:%Y-%m-%d}",
                        holdout_trials_ignored=len(trials) - len(evidence_rows), focus=focus, evidence=scores,
                        evidence_budget=evidence_budget, moves=moves, attribution_as_of=attr_as_of,
                        attribution_note=attr_note, hypotheses_note=hyp_note, quarter_reserved=reservation.reserved,
                        reservation=reservation, retired_floor=retired_floor, reinstate_t=round(reinstate_t, 6),
                        hypotheses_sha256=hashlib.sha256(hypotheses.encode()).hexdigest() if hypotheses else None,
                        hypotheses_drift=drift)
