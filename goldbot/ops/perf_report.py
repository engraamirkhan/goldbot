"""Performance report for the performance-analyst and finance-auditor agents (north-star: ADR 0004 section 6;
evaluation: docs/research/opportunity-days.md section 3.4), from the local state, read only.

    python -m goldbot.ops.run perf-report [--state DIR] [--data-root DIR] [--bars-source NAME] [--from YYYY-MM-DD]
                                          [--to YYYY-MM-DD] [--account ID] [--out DIR] [--json]
    python scripts/performance_report.py ...          (the same, from a state folder copied to the Mac)

Writes `perf_<from>_<to>.md` and `perf_<from>_<to>.json` to `--out` (default `reports`, gitignored, never inside the
state folder or the data root) and prints the markdown (`--json`: the JSON). Per account, for the opportunity days
`--from`..`--to` (day D runs D-1 22:00 to D 22:00 UTC, report_days.py, the day the monthly report uses too, so the
Sunday reopen belongs to Monday; default the month so far); trades are in the range by their exit's opportunity day:

* net and gross R and money per trade and per day (per weekday in the range, traded or not), overall and by family,
  agent, timeframe, session (`DEFAULT_SESSIONS.session_label` at the entry: asia, the Tokyo session; london; newyork),
  side and day type (ex post). The P&L basis decides the column (quant review of 207c9ef, item 1):
  - a row with `pnl_basis` "net", or a legacy row (no `pnl_basis`) that records both commission and swap, is net: its
    P&L and R are the net columns, and its gross (P&L + commission - swap) and gross R (gross / initial risk, the risk
    being net P&L / net R) the gross columns;
  - a row with `pnl_basis` "gross" goes to the gross columns only, counted as "net not recorded (gross only, N)";
  - any other row has its basis not recorded: counted in the trades, in no money or R column ("basis not recorded").
  Readings, money per day, the north star and drawdown use net rows only;
* owner decisions: proposals approved, rejected (by reason) and expired, and what each signal did in the shadow book:
  the shadow trade of the same agent, side and signal bar (the proposal id carries the signal bar's close), taken by
  the shadow model or not (`taken` is reported). The model version comes from the record, not the proposal: an approved
  proposal that became a trade takes the closed record's `model_version` (the record's client order id is the
  proposal id: EntryAudit); a rejected or expired proposal, or an approved one without a record or a version, takes the
  version whose p is nearest the card's, labelled "version inferred (nearest p)". R of the shadow trade's own initial stop, GROSS (no commission or
  swap, gaps filled at the stop: engine/shadow.py), summed (`sum_r`) and in money as R x the card's stop risk; every
  unmatched proposal says why. A calibration row sets the approved signals' shadow R against their own live net R (the
  closed trade whose client order id is the proposal id);
* realised against modelled costs where both exist: commission against the cost table's (state/costs_<account>.json)
  per-lot commission, only when the account's currency is USD (no conversion is recorded, so none is guessed); from the
  record, execution slippage side x (fill - requested_price) apart from approval drift side x (requested_price -
  decision_price), the cost of the owner's wait; with `--data-root`, the fills' slippage by session against the table;
* north-star capture (ADR 0004 section 6 KPI), over the trades ENTERED on the range's days (whenever they closed):
  with `--data-root` bars (source `--bars-source`, default dukascopy), an opportunity day is a stub-free weekday whose
  range is at least 1.5 x ATR(1d) of the 20 stub-free days BEFORE it (day_type.daily_inputs; the day's own range is not
  in its ATR). This is ex post: the day's range is known only at its close. Captured = the trades entered on it made at
  least 1 R net; days at £50 or more (GBP accounts); open trades entered on an opportunity day are "pending";
* drawdown against the limits, closed-trade, lower bound: realised P&L in the RiskGate's own windows (the risk day
  from 00:00 UTC, `timeutil.risk_day`; the risk week from Sunday 00:00 UTC, gate `_risk_week`) against the daily cap
  and the absolute limit, the weekly cap and the 8% / 12% stages. Floating losses and open risk are not seen; the exact
  figures need a daily risk summary from the engine, which does not exist yet;
* with `--data-root`, the journal: proposals, gate refusals by reason, orders and failed orders;
* exploration, control and cost-probe closes (ops/learning_ledger.py: the trade ledger over aux.db), in their own
  section labelled by kind and by kind and family, the random controls beside the families they are read against.
  These are NEVER in the closed-trade record above (it feeds the phase gate, the stop rule and the throttle), so the
  sections above are exactly what they were; a trade in both sources (same account and broker position) is counted
  once, in the record. Same honesty rules: net only where commission and swap are both recorded, no interval under
  MIN_TRADES net R, and the groups count in the multiple-groups note.

Design rule (the brief to the performance-analyst): "sample sizes everywhere, with no conclusion drawn from fewer than
~30 trades". Every figure carries its count; an interval and a reading ("mean R above zero") are given only from
MIN_TRADES trades with a net R, and below that the row says "insufficient sample" instead. The header gives the number
of groups: with many groups some 90% intervals exclude zero by chance alone.

Missing data is never guessed: a field a record does not carry is counted under "not recorded". The finance-audit
fields of the record (`family`, `timeframe`, `model_version`, `pnl_basis`, `money_source`, `decision_price`,
`requested_price`; None on records from before them) are used when present; otherwise the family comes from the agent id
(`AgentIdentity.agent_id` is `<family>-g<generation>-<hash>`) and the timeframe from the population
(state/population.json) or a family with one timeframe. The lane's `initial_sl` is not read: R is the engine's.

Read only: nothing under the state folder or the data root is written (a Store is opened only on an existing root).
No identifiers reach the output: no position or order ids, broker or server names, proposal ids or who decided.
Money is in the account's currency; a row in another currency (the unconverted USD fallback, BACKLOG 28) is counted in
trades and R, never in the money. Reporting only: nothing here changes an order, halt or setting.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, load_record, load_record_json
from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.timeutil import floor_tf, risk_day
from goldbot.engine.shadow import ShadowTrade, VersionBook
from goldbot.execution.costs import SESSIONS, CostTable
from goldbot.execution.not_placed import CAUSE_PHRASE, approval_cause
from goldbot.ops.gates_phase import ClosedTrade, load_closed_trades, pnl_basis
from goldbot.ops.learning_ledger import (
    KIND_LABEL,
    KINDS,
    LearningClose,
    closed_learning_trades,
    recorded_keys,
)
from goldbot.ops.report_days import day_buckets, month_days, report_day
from goldbot.ops.report_days import weekdays as weekdays_in
from goldbot.telegram.approvals import Outcome, Proposal
from goldbot.telegram.entry_text import account_name

MIN_TRADES = 30                  # below this no interval and no reading, only the counts (design rule above)
NORTH_STAR_GBP = 50.0            # ADR 0004 section 6: days at £50+ are counted, never aimed at
OPPORTUNITY_RANGE_ATR = 1.5      # ADR 0004 section 6 KPI: daily range >= 1.5 x ATR(1d)
CAPTURE_R = 1.0                  # ... on which the system made >= 1 R
BARS_SOURCE = "dukascopy"        # the bars_1m source the day types are read from (data/release.py load_into_store)
NOT_RECORDED = "not recorded"
OPPORTUNITY, ORDINARY = "opportunity (ex post)", "ordinary (ex post)"
DIMENSIONS = ("family", "agent", "timeframe", "session", "side", "day_type")
SESSION_NAMES = {"asia": "asia (Tokyo)", "london": "london (London)", "newyork": "newyork (New York)"}
AGENT_ID = re.compile(r"^([a-z0-9_]+)-g\d+-[0-9a-f]{10}$")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}
VERSION_INFERRED = "version inferred (nearest p)"
NO_BAR, NO_SHADOW, NO_VERSION = ("no signal bar in the proposal id", "no shadow trade for this agent, side and signal bar",
                                 "model version not in the shadow book")


# ---------------------------------------------------------------------------------------------- records
class GroupStats(FrozenRecord):
    key: str
    trades: int
    net_pnl: float | None = None           # net rows in the account currency (money_trades)
    money_trades: int = 0
    gross_pnl: float | None = None         # net rows that record commission and swap, plus gross rows
    gross_trades: int = 0
    net_r: float | None = None
    r_trades: int = 0                      # net rows with an R: the readings rest on these
    gross_r: float | None = None
    gross_r_trades: int = 0
    gross_only: int = 0                    # pnl_basis "gross": net not recorded
    basis_not_recorded: int = 0            # no pnl_basis and not a legacy net row
    mean_r: float | None = None
    mean_pnl: float | None = None
    pnl_per_day: float | None = None       # per weekday in the range, traded or not
    r_per_day: float | None = None
    win_rate: float | None = None          # share of net R > 0
    r_ci90: tuple[float, float] | None = None   # Student-t, only from MIN_TRADES net R
    sufficient: bool
    verdict: str
    # exploration, control and probe rows only (None / 0 elsewhere): trades closed by anything but the arm's own stop,
    # target or hold, counted apart and never in R or money; a family's mean net R less the random control's, matched
    # by risk day x session x side (the control weighted by the family's cell mix), with a Welch 90% interval only when
    # both sides have MIN_TRADES trades; the family trades whose cell has no control trade are dropped and counted
    censored: int = 0
    excess_r: float | None = None
    excess_ci90: tuple[float, float] | None = None
    excess_cells: int | None = None
    excess_dropped: int = 0


class DayStats(FrozenRecord):
    weekdays: int
    traded_days: int                       # days with a net row (by the exit's opportunity day)
    mean_per_weekday: float | None = None
    median_traded_day: float | None = None
    best_day: str | None = None
    best_day_pnl: float | None = None
    worst_day: str | None = None
    worst_day_pnl: float | None = None
    days_50_plus: int | None = None        # GBP accounts only


class NorthStar(FrozenRecord):
    source: str | None = None              # "bars (<source>), ex post" when opportunity days were classified
    classified_days: int = 0               # weekdays in the range with a range and a prior ATR(1d)
    opportunity_days: int | None = None
    traded_opportunity_days: int | None = None
    captured_1r: int | None = None
    days_50_plus_on_opportunity: int | None = None    # GBP accounts only
    pnl_on_opportunity_days: float | None = None
    pnl_on_other_days: float | None = None
    r_on_opportunity_days: float | None = None
    pending_open: int | None = None        # open trades entered on an opportunity day (state/orders_<account>.json)
    note: str = ""


class SlippageRow(FrozenRecord):
    session: str
    fills: int
    realised_mean_usd: float | None = None     # $/oz, positive = worse than requested
    modelled_usd: float | None = None
    modelled_from: str | None = None           # "measured" (the table's cell) | "prior"
    sufficient: bool


class CostReport(FrozenRecord):
    commission_trades: int = 0
    commission_total: float | None = None      # account currency, positive = paid
    swap_trades: int = 0
    swap_total: float | None = None            # account currency, broker sign (negative = paid)
    cost_r_mean: float | None = None           # (commission - swap) / initial risk, per trade
    cost_r_trades: int = 0
    modelled_commission_total: float | None = None   # USD: the table's per-lot commission x 2 sides x lots
    commission_vs_model: float | None = None   # realised / modelled, same currency only
    commission_note: str | None = None
    execution_slippage_trades: int = 0         # records with a requested_price and a fill
    execution_slippage_mean_usd: float | None = None   # side x (fill - requested_price), $/oz, positive = worse
    approval_drift_trades: int = 0             # records with both a requested_price and a decision_price
    approval_drift_mean_usd: float | None = None   # side x (requested_price - decision_price): the wait's cost
    cost_table_built_utc: str | None = None
    slippage: list[SlippageRow] = []


class DrawdownReport(FrozenRecord):
    """Closed-trade, lower bound: realised P&L only, in the RiskGate's windows."""
    max_drawdown_pct: float | None = None      # closed-trade return index over the range
    dd_stage1_pct: float
    dd_stage2_pct: float
    worst_day: str | None = None               # risk day (UTC date, timeutil.risk_day)
    worst_day_loss: float | None = None        # the largest net loss (> 0), account currency; None: no losing day
    worst_day_pct: float | None = None         # of the equity before the day's first closed trade
    daily_cap_pct: float
    daily_loss_limit_abs: float | None = None
    days_at_daily_limit: int = 0
    worst_week: str | None = None              # risk week, by its Sunday (gate._risk_week)
    worst_week_loss: float | None = None       # None: no losing week
    worst_week_pct: float | None = None
    weekly_cap_pct: float
    engine_drawdown_pct: float | None = None   # live equity against the closed-balance high-water mark
    engine_stage: str | None = None
    limits_source: str


class ShadowOutcome(FrozenRecord):
    """What the proposals of one outcome did in the shadow book: gross R (no commission or swap, optimistic fills)."""
    outcome: str                               # approved | rejected | expired
    proposals: int
    matched: int = 0                           # closed shadow trade found
    taken: int = 0                             # of the matched, the shadow model's p cleared its threshold
    version_from_record: int = 0               # matched on the closed record's model_version
    version_inferred: int = 0                  # matched on the nearest p (VERSION_INFERRED)
    still_open: int = 0
    unmatched: int = 0
    unmatched_reasons: dict[str, int] = {}
    sum_r: float | None = None                 # gross
    mean_r: float | None = None                # gross
    wins: int = 0
    money: float | None = None                 # sum of R x the card's stop risk (gross)
    money_trades: int = 0
    currency: str | None = None
    sufficient: bool
    verdict: str                               # ends "(gross)"


class Calibration(FrozenRecord):
    """Approved signals with both a closed shadow trade and a live net row: live net R against shadow gross R."""
    pairs: int
    shadow_sum_r: float | None = None
    live_sum_r: float | None = None
    mean_gap_r: float | None = None            # mean of (live net R - shadow gross R)
    gap_ci90: tuple[float, float] | None = None
    sufficient: bool
    verdict: str


class ApprovalReport(FrozenRecord):
    proposals: int = 0
    approved: int = 0
    rejected: int = 0
    expired: int = 0
    open: int = 0                              # not finished yet (pending)
    # BACKLOG 83: approved cards by cause (`not_placed.approval_cause`); the RiskGate's own refusals only in the first
    gate_refused_after_approval: int = 0
    not_placed_after_approval: int = 0        # refused by the MT5 box or the broker, or not confirmed then no trade
    not_confirmed_after_approval: int = 0     # not confirmed by the broker, still unresolved
    reasons: dict[str, int] = {}
    counterfactual: list[ShadowOutcome] = []
    calibration: Calibration | None = None     # None without approved proposals


class JournalReport(FrozenRecord):
    decisions: int
    proposed: int
    auto_entries: int
    gate_refusals: dict[str, int]
    orders: int
    orders_failed: int


class LearningReport(FrozenRecord):
    """Exploration, control and cost-probe closes (not in the phase-gate record), by kind and by kind and family."""
    currency: str | None = None            # the learning trades' own (the record sections keep theirs)
    kinds: list[GroupStats]
    families: list[GroupStats]           # "<kind> / <family>": the controls (random_bracket, ...) beside the families


class AccountReport(FrozenRecord):
    account_id: str
    mode: str | None = None
    currency: str | None = None
    overall: GroupStats
    by: dict[str, list[GroupStats]]
    days: DayStats
    north_star: NorthStar
    costs: CostReport
    drawdown: DrawdownReport
    approvals: ApprovalReport
    journal: JournalReport | None = None
    not_recorded: dict[str, int]
    money_sources: dict[str, int] = {}         # the record's money_source (record-gap lane), "not recorded" without
    money_note: str | None = None
    learning: LearningReport | None = None     # None when the account closed no exploration, control or probe trade


class PerformanceReport(FrozenRecord):
    start: str
    end: str
    as_of_utc: str
    weekdays: int
    min_trades: int = MIN_TRADES
    groups: int = 0                            # group rows across every account and dimension
    groups_with_reading: int = 0
    learning_groups: int = 0                   # exploration, control and probe rows (kind rows overlap family rows)
    accounts: list[AccountReport]
    errors: list[str] = []


class _Row(FrozenRecord):
    """One closed trade with what the report derives from it."""
    trade: ClosedTrade
    exit_day: str                              # opportunity day of the exit
    entry_day: str | None
    risk_day: str                              # RiskGate day (UTC date) of the exit
    risk_week: str                             # RiskGate week (its Sunday) of the exit
    basis: str | None                          # "net" | "gross" | None (not recorded)
    net_pnl: float | None                      # net rows in the account currency
    net_r: float | None
    gross_pnl: float | None
    gross_r: float | None
    risk: float | None                         # initial risk in account currency (P&L / R of the same basis)
    dims: dict[str, str]


# ---------------------------------------------------------------------------------------------- helpers
def _ci90(xs: list[float]) -> tuple[float, float]:
    from scipy import stats
    m, sd = float(np.mean(xs)), float(np.std(xs, ddof=1))
    half = float(stats.t.ppf(0.95, len(xs) - 1)) * sd / math.sqrt(len(xs))
    return (m - half, m + half)


def _reading(n: int, ci: tuple[float, float] | None, what: str = "mean R") -> str:
    if n < MIN_TRADES or ci is None:
        return f"insufficient sample ({n} < {MIN_TRADES} trades): no conclusion"
    if ci[0] > 0:
        return f"{what} above zero (90% interval)"
    if ci[1] < 0:
        return f"{what} below zero (90% interval)"
    return f"{what} not distinguishable from zero (90% interval)"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class _Measured(Protocol):
    """What a group sums: a closed trade's net and gross money and R, and the basis they rest on."""
    @property
    def basis(self) -> str | None: ...
    @property
    def net_pnl(self) -> float | None: ...
    @property
    def net_r(self) -> float | None: ...
    @property
    def gross_pnl(self) -> float | None: ...
    @property
    def gross_r(self) -> float | None: ...


class _LRow(FrozenRecord):
    """One closed exploration, control or probe trade with what the report derives from it (the `_Row` fields a
    group reads)."""
    trade: LearningClose
    cell: tuple[str, str, str]                 # risk day x session x side: what a family is matched to the control on
    basis: str | None
    net_pnl: float | None
    net_r: float | None
    gross_pnl: float | None
    gross_r: float | None


def _group(key: str, rows: Sequence[_Measured], weekdays: int, censored: int = 0) -> GroupStats:
    net = [r.net_pnl for r in rows if r.net_pnl is not None]
    rs = [r.net_r for r in rows if r.net_r is not None]
    gross = [r.gross_pnl for r in rows if r.gross_pnl is not None]
    grs = [r.gross_r for r in rows if r.gross_r is not None]
    net_sum = sum(net) if net else None
    net_r = sum(rs) if rs else None
    ci = _ci90(rs) if len(rs) >= MIN_TRADES else None
    return GroupStats(
        key=key, trades=len(rows), net_pnl=net_sum, money_trades=len(net), gross_pnl=sum(gross) if gross else None,
        gross_trades=len(gross), net_r=net_r, r_trades=len(rs), gross_r=sum(grs) if grs else None,
        gross_r_trades=len(grs), gross_only=sum(r.basis == "gross" for r in rows),
        basis_not_recorded=sum(r.basis is None for r in rows), mean_r=float(np.mean(rs)) if rs else None,
        mean_pnl=net_sum / len(net) if net_sum is not None else None,
        pnl_per_day=net_sum / weekdays if net_sum is not None and weekdays else None,
        r_per_day=net_r / weekdays if net_r is not None and weekdays else None,
        win_rate=sum(x > 0 for x in rs) / len(rs) if rs else None, r_ci90=ci,
        sufficient=len(rs) >= MIN_TRADES and ci is not None, verdict=_reading(len(rs), ci), censored=censored)


# ---------------------------------------------------------------------------------------------- state readers
def _population(state: Path) -> dict[str, dict[str, Any]]:
    raw = _read_json(state / "population.json")
    if not isinstance(raw, list):
        return {}
    return {m["agent_id"]: m for m in raw if isinstance(m, dict) and isinstance(m.get("agent_id"), str)}


def _timeframe(agent_id: str | None, family: str | None, population: dict[str, dict[str, Any]]) -> str | None:
    """The agent's decision timeframe: its population config's override, else its family's default; a family that
    can move between timeframes and an agent not in the population give None (not guessed)."""
    from goldbot.specialists import SPECIALISTS
    from goldbot.specialists.base import TIMEFRAME_KEY
    member = population.get(agent_id or "")
    cls = SPECIALISTS.get(family or "")
    if member is not None:
        cfg: dict[str, Any] = member["config"] if isinstance(member.get("config"), dict) else {}
        tf = cfg.get(TIMEFRAME_KEY)
        if isinstance(tf, str):
            return tf
        return cls.timeframe if cls is not None else None
    return cls.timeframe if cls is not None and not cls.timeframes else None


def _dims(t: ClosedTrade, population: dict[str, dict[str, Any]], day_types: dict[str, str] | None,
          entry_day: str | None) -> dict[str, str]:
    m = AGENT_ID.match(t.agent_id or "")
    family = t.family or (m.group(1) if m else None)
    tf = t.timeframe or _timeframe(t.agent_id, family, population)
    session = str(DEFAULT_SESSIONS.session_label(pd.DatetimeIndex([t.entry_utc]))[0]) if t.entry_utc is not None \
        else None
    day_type = day_types.get(entry_day) if day_types is not None and entry_day is not None else None
    side = {1: "long", -1: "short"}.get(t.side or 0)
    return {"family": family or NOT_RECORDED, "agent": t.agent_id or NOT_RECORDED, "timeframe": tf or NOT_RECORDED,
            "session": session or NOT_RECORDED, "side": side or NOT_RECORDED, "day_type": day_type or NOT_RECORDED}


def _row(t: ClosedTrade, ccy: str | None, population: dict[str, dict[str, Any]],
         day_types: dict[str, str] | None) -> _Row:
    same_ccy = t.pnl_currency is None or ccy is None or t.pnl_currency.upper() == ccy.upper()
    basis = pnl_basis(t)
    risk = t.pnl / t.r if basis is not None and same_ccy and t.r not in (None, 0) and t.pnl != 0 else None
    risk = risk if risk is not None and risk > 0 else None
    if basis == "net":
        net_pnl = t.pnl if same_ccy else None
        net_r = t.r
        gross = t.pnl + t.commission - t.swap if t.commission is not None and t.swap is not None and same_ccy else None
        gross_r = gross / risk if gross is not None and risk is not None else None
    elif basis == "gross":
        net_pnl, net_r, gross, gross_r = None, None, t.pnl if same_ccy else None, t.r
    else:
        net_pnl = net_r = gross = gross_r = None
    entry_day = report_day(t.entry_utc) if t.entry_utc is not None else None
    ex = pd.DatetimeIndex([t.exit_utc])
    return _Row(trade=t, exit_day=report_day(t.exit_utc), entry_day=entry_day, risk_day=f"{risk_day(ex)[0]:%Y-%m-%d}",
                risk_week=f"{floor_tf(ex, 7 * 86400)[0]:%Y-%m-%d}", basis=basis, net_pnl=net_pnl, net_r=net_r,
                gross_pnl=gross, gross_r=gross_r, risk=risk, dims=_dims(t, population, day_types, entry_day))


def _proposals(state: Path, folder: str) -> list[Proposal]:
    """Proposals in approvals/<folder>, read without creating a folder."""
    d = state / "approvals" / folder
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(load_record_json(Proposal, f.read_text(encoding="utf-8"), source=f"approvals/{folder}"))
        except (OSError, ValueError):
            continue
    return out


def _signal_close(p: Proposal) -> pd.Timestamp | None:
    """The signal bar's close the engine put in the proposal id (`<account>-<epoch>-<hash>`, runner._proposal_id)."""
    parts = p.proposal_id.rsplit("-", 2)
    if len(parts) == 3 and parts[1].isdigit():
        return pd.Timestamp(int(parts[1]), unit="s", tz="UTC")
    return None


def _shadow_trades(state: Path) -> tuple[list[ShadowTrade], str | None]:
    raw = _read_json(state / "shadow_book.json")
    if raw is None:
        return [], None
    if not isinstance(raw, dict):
        return [], "shadow_book.json unreadable"
    out: list[ShadowTrade] = []
    try:
        for v in raw.values():
            b = load_record(VersionBook, v, source="shadow_book.json")
            out.extend(b.closed + b.open)
    except ValueError:
        return out, "shadow_book.json: a version book is unreadable"
    return out, None


def _shadow_r(t: ShadowTrade) -> float | None:
    risk = abs(t.entry - t.stop)
    if t.exit is None or risk <= 0:
        return None
    return t.side * (t.exit - t.entry) / risk


def match_shadow(p: Proposal, model_version: str | None,
                 shadow: list[ShadowTrade]) -> tuple[ShadowTrade | None, str | None]:
    """(shadow trade, None) or (None, why). Same agent, side and signal bar, whether the shadow model took it or not
    (quant review, item 4); of `model_version` when known (from the trade's closed record, item 5), else the version
    whose p is nearest the card's (VERSION_INFERRED)."""
    close = _signal_close(p)
    if close is None:
        return None, NO_BAR
    cands = [t for t in shadow if t.agent_id == p.agent_id and t.side == p.side
             and t.entry_ts + pd.Timedelta(seconds=tf_seconds(t.timeframe)) == close]
    if not cands:
        return None, NO_SHADOW
    if model_version is not None:
        same = [t for t in cands if t.version == model_version]
        return (same[0], None) if same else (None, NO_VERSION)
    return min(cands, key=lambda t: abs(t.p - p.p)), None


def _open_entries(state: Path, account: str) -> list[pd.Timestamp]:
    """Entry times of the positions still open (or closed but not recorded yet) in state/orders_<account>.json."""
    d = _read_json(state / f"orders_{account}.json")
    if not isinstance(d, dict):
        return []
    trades = list(d["open"].values()) if isinstance(d.get("open"), dict) else []
    trades += [c.get("trade") for c in d.get("closing", []) if isinstance(c, dict)]
    out = []
    for t in trades:
        if isinstance(t, dict) and isinstance(t.get("opened_utc"), str):
            try:
                out.append(pd.Timestamp(t["opened_utc"]).tz_convert("UTC"))
            except (ValueError, TypeError):
                continue
    return out


# ---------------------------------------------------------------------------------------------- data root readers
def _store(data_root: Path | None) -> Any:
    if data_root is None or not Path(data_root).is_dir():
        return None
    from goldbot.data.store import Store
    return Store(data_root)            # the root exists, so its mkdir writes nothing


def _day_types(store: Any, start: str, end: str,
               source: str = BARS_SOURCE) -> tuple[dict[str, str] | None, int, set[str]]:
    """Opportunity day -> OPPORTUNITY | ORDINARY (ex post) for the stub-free days with a prior ATR(1d) (ADR 0004
    section 6: range >= 1.5 x ATR(1d), the ATR of the ATR_DAYS stub-free days before the day, never including it), from
    the stored 1m bars of `source` (mid). Returns (types, classified weekdays in the range, opportunity days in it)."""
    from goldbot.features.day_type import ATR_DAYS, daily_bars, daily_inputs
    if store is None:
        return None, 0, set()
    lo = pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=3 * ATR_DAYS)
    bars = store.read("bars_1m", source=source, start=lo, end=pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1))
    if bars.empty:
        return None, 0, set()
    bars = bars.drop_duplicates("ts_utc", keep="last").sort_values("ts_utc")
    df = pd.DataFrame({"ts_utc": bars["ts_utc"]})
    for c in ("open", "high", "low", "close"):
        if f"bid_{c}" in bars and f"ask_{c}" in bars:
            df[c] = (bars[f"bid_{c}"].astype(float) + bars[f"ask_{c}"].astype(float)) / 2
        elif c in bars:
            df[c] = bars[c].astype(float)
        else:
            return None, 0, set()
    inp = daily_inputs(daily_bars(df.reset_index(drop=True)))
    prior_atr = inp["atr"].shift(1)                       # the ATR known at the day's open: the day itself left out
    out: dict[str, str] = {}
    for day, rng, atr in zip(inp["day"], inp["range"], prior_atr):
        if pd.notna(atr) and atr > 0 and pd.notna(rng):
            out[f"{pd.Timestamp(day):%Y-%m-%d}"] = OPPORTUNITY if rng >= OPPORTUNITY_RANGE_ATR * atr else ORDINARY
    days = {d for d in out if start <= d <= end and date.fromisoformat(d).weekday() < 5}
    return out, len(days), {d for d in days if out[d] == OPPORTUNITY}


def _slippage(store: Any, account: str, start: str, end: str, table: CostTable | None) -> list[SlippageRow]:
    if store is None:
        return []
    f = store.read("fills", source=account, start=pd.Timestamp(start, tz="UTC") - pd.Timedelta(hours=2),
                   end=pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=22))
    if f.empty or not {"requested", "filled", "side"} <= set(f.columns):
        return []
    f = f.dropna(subset=["requested", "filled", "side"])
    f = f[[start <= report_day(t) <= end for t in f["ts_utc"]]]
    if f.empty:
        return []
    ts = pd.DatetimeIndex(pd.to_datetime(f["ts_utc"], utc=True))
    label = DEFAULT_SESSIONS.session_label(ts)
    slip = f["side"].to_numpy(dtype=float) * (f["filled"].to_numpy(dtype=float) - f["requested"].to_numpy(dtype=float))
    out = []
    for s in SESSIONS:
        v = slip[label == s]
        if not len(v):
            continue
        cell = table.slippage.get(f"{s}:market") if table is not None else None
        model = cell.mean if cell is not None else (table.slippage_prior_usd if table is not None else None)
        src = ("prior" if cell.from_prior else "measured") if cell is not None else ("prior" if table else None)
        out.append(SlippageRow(session=s, fills=len(v), realised_mean_usd=float(v.mean()), modelled_usd=model,
                               modelled_from=src, sufficient=len(v) >= MIN_TRADES))
    return out


def _journal(store: Any, account: str, start: str, end: str) -> JournalReport | None:
    if store is None:
        return None
    d = store.read("decisions", source=account, start=pd.Timestamp(start, tz="UTC") - pd.Timedelta(hours=2),
                   end=pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=22))
    if d.empty or "action" not in d:
        return None
    d = d[[start <= report_day(t) <= end for t in d["ts_utc"]]]
    actions = d["action"].astype(str)
    gate: dict[str, int] = {}
    for a in actions[actions.str.startswith("gate:")]:
        for reason in a.split(":", 1)[1].split(","):
            if reason:
                gate[reason] = gate.get(reason, 0) + 1
    orders = d[actions == "order"]
    failed = 0
    for detail in orders.get("detail", pd.Series(dtype=str)):
        try:
            failed += int(json.loads(detail).get("ok") is False)
        except (TypeError, ValueError, AttributeError):
            continue
    return JournalReport(decisions=len(d), proposed=int((actions == "proposed").sum()),
                         auto_entries=int(actions.str.startswith("executed:auto").sum()),
                         gate_refusals=dict(sorted(gate.items(), key=lambda kv: (-kv[1], kv[0]))),
                         orders=len(orders), orders_failed=failed)


# ---------------------------------------------------------------------------------------------- sections
def _days(rows: list[_Row], weekdays: int, ccy: str | None) -> DayStats:
    per = {d: sum(r.net_pnl or 0.0 for r in v)
           for d, v in day_buckets([r for r in rows if r.net_pnl is not None], lambda r: r.trade.exit_utc).items()}
    if not per:
        return DayStats(weekdays=weekdays, traded_days=0)
    best, worst = max(per, key=lambda d: per[d]), min(per, key=lambda d: per[d])
    return DayStats(weekdays=weekdays, traded_days=len(per),
                    mean_per_weekday=sum(per.values()) / weekdays if weekdays else None,
                    median_traded_day=float(np.median(list(per.values()))), best_day=best, best_day_pnl=per[best],
                    worst_day=worst, worst_day_pnl=per[worst],
                    days_50_plus=sum(v >= NORTH_STAR_GBP for v in per.values()) if ccy == "GBP" else None)


def _north_star(rows: list[_Row], ccy: str | None, classified: int, opp: set[str], have_bars: bool,
                open_entries: list[pd.Timestamp], source: str) -> NorthStar:
    """`rows`: the account's trades ENTERED on the range's days (quant review, item 6), whenever they closed."""
    if not have_bars:
        return NorthStar(note=f"opportunity days {NOT_RECORDED}: they need the stored bars (--data-root)")
    entered = [(r, r.trade.entry_utc) for r in rows if r.trade.entry_utc is not None]
    by_entry = {d: [r for r, _ in v] for d, v in day_buckets(entered, lambda x: x[1]).items()}
    per_pnl = {d: sum(r.net_pnl for r in v if r.net_pnl is not None) for d, v in by_entry.items()}
    per_r = {d: sum(r.net_r for r in v if r.net_r is not None) for d, v in by_entry.items()}
    on = [r for r in rows if r.entry_day in opp]
    off = [r for r in rows if r.entry_day not in opp]
    note = ("ex post: a day's range is known only at its close; trades by their entry's opportunity day (net rows), "
            "trades without an entry time left out")
    if ccy != "GBP":
        note += f"; the £50 north star is in GBP and this account reports {ccy or 'no currency'}"

    def total(xs: list[float | None]) -> float | None:
        vals = [x for x in xs if x is not None]
        return sum(vals) if vals else None
    return NorthStar(
        source=f"bars ({source}), ex post", classified_days=classified, opportunity_days=len(opp),
        traded_opportunity_days=len({r.entry_day for r in on}),
        captured_1r=sum(per_r.get(d, 0.0) >= CAPTURE_R for d in opp),
        days_50_plus_on_opportunity=sum(per_pnl.get(d, 0.0) >= NORTH_STAR_GBP for d in opp) if ccy == "GBP" else None,
        pnl_on_opportunity_days=total([r.net_pnl for r in on]), pnl_on_other_days=total([r.net_pnl for r in off]),
        r_on_opportunity_days=total([r.net_r for r in on]),
        pending_open=sum(report_day(t) in opp for t in open_entries), note=note)


def _costs(rows: list[_Row], ccy: str | None, table: CostTable | None, slippage: list[SlippageRow]) -> CostReport:
    com = [r for r in rows if r.trade.commission is not None and r.net_pnl is not None]
    swp = [r for r in rows if r.trade.swap is not None and r.net_pnl is not None]
    cost_r = [(r.trade.commission - r.trade.swap) / r.risk for r in rows
              if r.trade.commission is not None and r.trade.swap is not None and r.risk is not None]
    execution: list[float] = []
    drift: list[float] = []
    for t in (r.trade for r in rows):
        if t.side not in (1, -1) or t.requested_price is None:
            continue
        if t.entry_price is not None:
            execution.append(t.side * (t.entry_price - t.requested_price))
        if t.decision_price is not None:
            drift.append(t.side * (t.requested_price - t.decision_price))
    modelled = vs = None
    note = None
    if table is not None:
        lots = [r.trade.lots for r in com if r.trade.lots is not None]
        if com and len(lots) == len(com):
            modelled = sum(2 * table.commission_per_lot_side_usd * lot for lot in lots)
            if (ccy or "").upper() == "USD" and modelled > 0:
                vs = sum(r.trade.commission for r in com if r.trade.commission is not None) / modelled
            else:
                note = (f"not comparable: realised in {ccy or 'an unreported currency'}, modelled in USD "
                        "(no conversion is recorded)")
        elif com:
            note = f"lots {NOT_RECORDED} on some trades: the modelled commission is not computed"
    else:
        note = f"cost table {NOT_RECORDED} (state/costs_<account>.json)"
    return CostReport(
        commission_trades=len(com),
        commission_total=sum(r.trade.commission for r in com if r.trade.commission is not None) if com else None,
        swap_trades=len(swp), swap_total=sum(r.trade.swap for r in swp if r.trade.swap is not None) if swp else None,
        cost_r_mean=float(np.mean(cost_r)) if cost_r else None, cost_r_trades=len(cost_r),
        modelled_commission_total=modelled, commission_vs_model=vs, commission_note=note,
        execution_slippage_trades=len(execution),
        execution_slippage_mean_usd=float(np.mean(execution)) if execution else None,
        approval_drift_trades=len(drift), approval_drift_mean_usd=float(np.mean(drift)) if drift else None,
        cost_table_built_utc=table.built_utc.isoformat() if table is not None else None, slippage=slippage)


def _limits(engine: dict[str, Any]) -> tuple[float, float, float, float, float | None, str]:
    """(daily cap, weekly cap, stage 1, stage 2, absolute daily limit, source): the engine's published limits first,
    the settings for what it does not publish, the RiskGate defaults when the settings do not load."""
    from goldbot.risk.gate import RiskLimits
    d = RiskLimits()
    daily, weekly, s1, s2, source = d.daily_cap, d.weekly_cap, d.dd_stage1, d.dd_stage2, "RiskGate defaults"
    try:
        from goldbot.config import load_settings
        r = load_settings().risk
        daily, weekly, s1, s2, source = r.daily_cap, r.weekly_cap, r.drawdown_stage1, r.drawdown_stage2, "settings"
    except Exception:          # a copied state folder without a readable config: the design defaults
        pass
    lim: dict[str, Any] = engine["limits"] if isinstance(engine.get("limits"), dict) else {}
    abs_lim = lim.get("daily_loss_limit_abs", engine.get("daily_loss_limit_abs"))
    if isinstance(lim.get("daily_cap"), (int, float)) and isinstance(lim.get("weekly_cap"), (int, float)):
        daily, weekly, source = float(lim["daily_cap"]), float(lim["weekly_cap"]), f"engine state and {source}"
    return daily, weekly, s1, s2, float(abs_lim) if isinstance(abs_lim, (int, float)) else None, source


def _drawdown(rows: list[_Row], engine: dict[str, Any]) -> DrawdownReport:
    """Closed-trade, lower bound (quant review, item 3): net rows grouped by the RiskGate's own windows, the risk day
    (00:00 UTC, timeutil.risk_day) and the risk week (Sunday 00:00 UTC, gate._risk_week)."""
    daily, weekly, s1, s2, abs_lim, source = _limits(engine)
    money = sorted((r for r in rows if r.net_pnl is not None), key=lambda r: r.trade.exit_utc)
    idx, peak, dd = 1.0, 1.0, 0.0
    days: dict[str, list[_Row]] = {}
    weeks: dict[str, list[_Row]] = {}
    for r in money:
        idx *= 1 + (r.net_pnl or 0.0) / r.trade.equity_before
        peak = max(peak, idx)
        dd = max(dd, 1 - idx / peak)
        days.setdefault(r.risk_day, []).append(r)
        weeks.setdefault(r.risk_week, []).append(r)

    def loss(v: list[_Row]) -> float:
        return -sum(r.net_pnl or 0.0 for r in v)

    def worst(groups: dict[str, list[_Row]]) -> tuple[str | None, float | None, float | None]:
        """The period with the largest net loss; none when no period lost money (only losses meet a limit)."""
        losses = {k: loss(v) for k, v in groups.items()}
        if not losses or max(losses.values()) <= 0:
            return None, None, None
        k = max(losses, key=lambda x: losses[x])
        return k, losses[k], 100 * losses[k] / groups[k][0].trade.equity_before

    wd, wdl, wdp = worst(days)
    ww, wwl, wwp = worst(weeks)
    at_limit = 0
    for v in days.values():
        start_eq = v[0].trade.equity_before
        limit = daily * start_eq if abs_lim is None else min(daily * start_eq, abs_lim)
        at_limit += loss(v) >= limit
    eq, hwm = engine.get("equity"), engine.get("balance_closed_hwm")
    eng_dd = 100 * (hwm - eq) / hwm if isinstance(eq, (int, float)) and isinstance(hwm, (int, float)) and hwm > 0 \
        else None
    stage = engine.get("stage")
    return DrawdownReport(
        max_drawdown_pct=100 * dd if money else None, dd_stage1_pct=100 * s1, dd_stage2_pct=100 * s2, worst_day=wd,
        worst_day_loss=wdl, worst_day_pct=wdp, daily_cap_pct=100 * daily, daily_loss_limit_abs=abs_lim,
        days_at_daily_limit=at_limit, worst_week=ww, worst_week_loss=wwl, worst_week_pct=wwp,
        weekly_cap_pct=100 * weekly, engine_drawdown_pct=max(eng_dd, 0.0) if eng_dd is not None else None,
        engine_stage=stage if isinstance(stage, str) else None, limits_source=source)


def _record_version(p: Proposal, live: dict[str, _Row]) -> str | None:
    """The model version an approved proposal traded with: its closed record's (client order id = proposal id,
    EntryAudit). None for rejected and expired proposals (no trade) and records without one: nearest p then."""
    row = live.get(p.proposal_id)
    return row.trade.model_version if row is not None and p.outcome == Outcome.APPROVED else None


def _calibration(approved: list[Proposal], shadow: list[ShadowTrade], live: dict[str, _Row]) -> Calibration:
    shadow_rs, live_rs = [], []
    for p in approved:
        t, _ = match_shadow(p, _record_version(p, live), shadow)
        sr = _shadow_r(t) if t is not None else None
        row = live.get(p.proposal_id)
        if sr is not None and row is not None and row.net_r is not None:
            shadow_rs.append(sr)
            live_rs.append(row.net_r)
    gaps = [lv - sh for lv, sh in zip(live_rs, shadow_rs)]
    ci = _ci90(gaps) if len(gaps) >= MIN_TRADES else None
    return Calibration(pairs=len(gaps), shadow_sum_r=sum(shadow_rs) if gaps else None,
                       live_sum_r=sum(live_rs) if gaps else None, mean_gap_r=float(np.mean(gaps)) if gaps else None,
                       gap_ci90=ci, sufficient=ci is not None,
                       verdict=_reading(len(gaps), ci, what="live net R minus shadow gross R"))


def _approvals(props: list[Proposal], pending: int, shadow: list[ShadowTrade],
               live: dict[str, _Row]) -> ApprovalReport:
    by: dict[str, list[Proposal]] = {"approved": [], "rejected": [], "expired": []}
    names = {Outcome.APPROVED: "approved", Outcome.REJECTED: "rejected", Outcome.EXPIRED: "expired"}
    for p in props:
        if p.outcome is not None:
            by[names[p.outcome]].append(p)
    causes: dict[str | None, int] = {}
    for p in by["approved"]:
        c = approval_cause(p.gate_refusal, p.not_confirmed.outcome if p.not_confirmed is not None else None)
        causes[c] = causes.get(c, 0) + 1
    reasons: dict[str, int] = {}
    for p in by["rejected"]:
        k = p.reason_code or NOT_RECORDED
        reasons[k] = reasons.get(k, 0) + 1
    cf = []
    for name, ps in by.items():
        if not ps:
            continue
        rs: list[float] = []
        money: list[float] = []
        still = taken = from_record = inferred = 0
        why: dict[str, int] = {}
        ccys = set()
        for p in ps:
            version = _record_version(p, live)
            t, reason = match_shadow(p, version, shadow)
            if t is None:
                why[reason or NOT_RECORDED] = why.get(reason or NOT_RECORDED, 0) + 1
                continue
            r = _shadow_r(t)
            if r is None:
                still += 1
                continue
            rs.append(r)
            taken += t.taken
            from_record += version is not None
            inferred += version is None
            if p.risk_amount is not None:
                money.append(r * p.risk_amount)
                ccys.add(p.currency)
        cf.append(ShadowOutcome(
            outcome=name, proposals=len(ps), matched=len(rs), taken=taken, version_from_record=from_record,
            version_inferred=inferred, still_open=still,
            unmatched=sum(why.values()), unmatched_reasons=dict(sorted(why.items())),
            sum_r=sum(rs) if rs else None, mean_r=float(np.mean(rs)) if rs else None, wins=sum(r > 0 for r in rs),
            money=sum(money) if money and len(ccys) == 1 else None, money_trades=len(money) if len(ccys) == 1 else 0,
            currency=next(iter(ccys)) if len(ccys) == 1 else None, sufficient=len(rs) >= MIN_TRADES,
            verdict=_reading(len(rs), _ci90(rs) if len(rs) >= MIN_TRADES else None) + " (gross)"))
    return ApprovalReport(proposals=len(props), approved=len(by["approved"]), rejected=len(by["rejected"]),
                          expired=len(by["expired"]), open=pending,
                          gate_refused_after_approval=causes.get("risk_check", 0),
                          not_placed_after_approval=causes.get("not_placed", 0),
                          not_confirmed_after_approval=causes.get("not_confirmed", 0),
                          reasons=dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))), counterfactual=cf,
                          calibration=_calibration(by["approved"], shadow, live) if by["approved"] else None)


def _proposal_day(p: Proposal) -> str:
    close = _signal_close(p)
    return report_day(close if close is not None else pd.Timestamp(p.created, unit="s", tz="UTC"))


# ---------------------------------------------------------------------------------------------- learning trades
def _lrow(c: LearningClose, ccy: str | None) -> _LRow:
    """Net only when commission and swap are both recorded (`LearningClose.net`); money only in the account currency."""
    same_ccy = c.currency is None or ccy is None or c.currency.upper() == ccy.upper()
    gross_r = c.gross / c.risk if c.net and c.gross is not None and c.risk else None
    session = str(DEFAULT_SESSIONS.session_label(pd.DatetimeIndex([c.opened_utc]))[0])
    day = f"{risk_day(pd.DatetimeIndex([c.exit_utc]))[0]:%Y-%m-%d}"
    return _LRow(trade=c, cell=(day, session, c.side), basis="net" if c.net else None,
                 net_pnl=c.pnl if c.net and same_ccy else None, net_r=c.r if c.net else None,
                 gross_pnl=c.gross if c.net and same_ccy else None, gross_r=gross_r)


def _excess(key: str, fam: list[_LRow], control: list[_LRow], weekdays: int) -> GroupStats:
    """The family's group, read against the random control on the same cells (risk day x session x side).

    excess = the family's mean net R less the control's mean net R in each trade's own cell (the control weighted by the
    family's cell mix); a family trade whose cell has no control trade is dropped and counted. The 90% interval is
    Welch's on the difference and only when both sides have MIN_TRADES trades; the verdict is against the control,
    never against zero (a family riding the day's drift reads "not distinguishable")."""
    from scipy import stats
    g = _group(key, fam, weekdays)
    ctrl: dict[tuple[str, str, str], list[float]] = {}
    for r in control:
        if r.net_r is not None:
            ctrl.setdefault(r.cell, []).append(r.net_r)
    mine = [(r.cell, r.net_r) for r in fam if r.net_r is not None]
    matched = [(c, x) for c, x in mine if c in ctrl]
    dropped = len(mine) - len(matched)
    if not matched:
        return g.model_copy(update={"excess_dropped": dropped, "excess_cells": 0, "r_ci90": None, "sufficient": False,
                                    "verdict": f"no matched control trades ({dropped} unmatched)"})
    cmean = {c: float(np.mean(v)) for c, v in ctrl.items()}
    diffs = [x - cmean[c] for c, x in matched]
    excess = float(np.mean(diffs))
    used = sorted({c for c, _ in matched})
    n_ctrl = sum(len(ctrl[c]) for c in used)
    ci: tuple[float, float] | None = None
    if len(diffs) >= MIN_TRADES and n_ctrl >= MIN_TRADES:
        pooled_var = float(np.var([x for c in used for x in ctrl[c]], ddof=1))
        va = float(np.var(diffs, ddof=1)) / len(diffs)
        vb = sum((sum(c == u for c, _ in matched) / len(matched)) ** 2
                 * (float(np.var(ctrl[u], ddof=1)) if len(ctrl[u]) > 1 else pooled_var) / len(ctrl[u]) for u in used)
        se = math.sqrt(va + vb)
        if se > 0:
            df = (va + vb) ** 2 / ((va ** 2 / (len(diffs) - 1)) + (vb ** 2 / (n_ctrl - 1)))
            half = float(stats.t.ppf(0.95, df)) * se
        else:
            half = 0.0
        ci = (excess - half, excess + half)
    note = f"{_r(excess)} vs control on {len(used)} cells, {dropped} unmatched"
    if ci is None:
        verdict = f"insufficient sample (< {MIN_TRADES} trades on a side): {note}"
    elif ci[0] > 0:
        verdict = f"above the random control (matched 90% interval): {note}"
    elif ci[1] < 0:
        verdict = f"below the random control (matched 90% interval): {note}"
    else:
        verdict = f"not distinguishable from the random control (matched 90% interval): {note}"
    return g.model_copy(update={"excess_r": excess, "excess_ci90": ci, "excess_cells": len(used),
                                "excess_dropped": dropped, "r_ci90": None, "sufficient": ci is not None,
                                "verdict": verdict})


def _learning_group(key: str, kind: str, rows: list[_LRow], censored: int, weekdays: int, control: list[_LRow],
                    family: bool) -> GroupStats:
    """One learning row. Never "mean R above zero": a pooled arm's mean is the market's drift plus selection."""
    if family and kind == "exploration":
        return _excess(key, rows, control, weekdays).model_copy(update={"censored": censored})
    g = _group(key, rows, weekdays, censored)
    if g.r_trades < MIN_TRADES:
        verdict = f"insufficient sample (< {MIN_TRADES} trades with a net R)"
    elif kind == "control":
        verdict = "the random control (the null the families are read against)"
    elif kind == "cost_probe":
        verdict = "cost measurement, not a return reading"
    else:
        verdict = "descriptive only: not tested against the control"
    return g.model_copy(update={"verdict": verdict})


def _learning(rows: list[_LRow], censored: list[_LRow], weekdays: int, ccy: str | None) -> LearningReport | None:
    """`rows` are the countable trades; the `censored` ones are counted beside their row, never in R or money."""
    if not rows and not censored:
        return None
    order = {k: i for i, k in enumerate(KINDS)}
    control = [r for r in rows if r.trade.kind == "control"]
    both = [*rows, *censored]
    kinds = [_learning_group(KIND_LABEL[k], k, [r for r in rows if r.trade.kind == k],
                             sum(r.trade.kind == k for r in censored), weekdays, control, False)
             for k in KINDS if any(r.trade.kind == k for r in both)]
    keys = sorted({(r.trade.kind, r.trade.family) for r in both}, key=lambda kf: (order[kf[0]], kf[1]))
    families = [_learning_group(f"{k} / {f}", k, [r for r in rows if (r.trade.kind, r.trade.family) == (k, f)],
                                sum((r.trade.kind, r.trade.family) == (k, f) for r in censored), weekdays, control,
                                True) for k, f in keys]
    return LearningReport(currency=ccy, kinds=kinds, families=families)


# ---------------------------------------------------------------------------------------------- build
def build_performance_report(state_dir: str | Path, *, start: str, end: str, data_root: str | Path | None = None,
                             account: str | None = None, now: pd.Timestamp | None = None,
                             bars_source: str = BARS_SOURCE) -> PerformanceReport:
    """The report for opportunity days `start`..`end` (YYYY-MM-DD, inclusive). Reads only."""
    state = Path(state_dir)
    now = pd.Timestamp.now(tz="UTC") if now is None else now.tz_convert("UTC")
    weekdays = weekdays_in(start, end)
    errors: list[str] = []
    trades, err = load_closed_trades(state) if state.is_dir() else ([], None)
    if err:
        errors.append(err)
    # exploration, control and probe closes: read from the ledger, never from (or into) the closed-trade record
    learn = None
    if state.is_dir():
        try:
            learn = closed_learning_trades(state, now, recorded_keys(trades))
        except Exception as exc:                   # noqa: BLE001 - reporting only: the closed-trade sections still show
            errors.append(f"trade ledger unreadable ({type(exc).__name__}): exploration, control and probe trades "
                          "missing, not zero")
    if learn is not None and learn.unreadable:
        errors.append("trade ledger source(s) unreadable: " + ", ".join(learn.unreadable)
                      + " (exploration, control or probe trades missing, not zero)")
    # the ledger cuts digit runs out of an account name: map it back through the record's ids, the accounts config and
    # `--account`, so an account with only learning trades keeps its id (and `--account <id>` finds it)
    from goldbot.ops import accounts as accounts_config
    listed = accounts_config.account_ids()
    known = {*(t.account_id for t in trades), *(listed.listed if listed is not None else ()), *([account] if account else ())}
    ids = {account_name(a): a for a in known}
    learn_by: dict[str, list[LearningClose]] = {}
    for c in learn.rows if learn is not None else []:
        if start <= report_day(c.exit_utc) <= end:
            learn_by.setdefault(ids.get(c.account, c.account), []).append(c)
    store = _store(Path(data_root) if data_root is not None else None)
    day_types, classified, opp = _day_types(store, start, end, bars_source)
    population = _population(state)
    shadow, serr = _shadow_trades(state)
    if serr:
        errors.append(serr)
    all_by: dict[str, list[ClosedTrade]] = {}
    for t in trades:
        all_by.setdefault(t.account_id, []).append(t)
    props_by: dict[str, list[Proposal]] = {}
    for p in _proposals(state, "done"):
        if start <= _proposal_day(p) <= end:
            props_by.setdefault(p.account_id, []).append(p)
    pending_by: dict[str, int] = {}
    for p in _proposals(state, "pending"):
        if start <= _proposal_day(p) <= end:
            pending_by[p.account_id] = pending_by.get(p.account_id, 0) + 1
    in_range = {a for a, ts in all_by.items()
                if any(start <= report_day(t.exit_utc) <= end
                       or (t.entry_utc is not None and start <= report_day(t.entry_utc) <= end) for t in ts)}
    accounts = sorted(in_range | set(props_by) | set(pending_by) | set(learn_by))
    if account is not None:
        accounts = [a for a in accounts if a == account]
    out = []
    for acc in accounts:
        engine = _read_json(state / f"engine_{acc}.json")
        engine = engine if isinstance(engine, dict) else {}
        everything = all_by.get(acc, [])
        ccy = engine.get("currency") if isinstance(engine.get("currency"), str) else None
        if ccy is None:
            seen = {t.pnl_currency for t in everything if t.pnl_currency}
            ccy = next(iter(seen)) if len(seen) == 1 else None
        all_rows = [_row(t, ccy, population, day_types) for t in everything]
        rows = [r for r in all_rows if start <= r.exit_day <= end]
        entered = [r for r in all_rows if r.entry_day is not None and start <= r.entry_day <= end]
        live = {r.trade.client_order_id: r for r in all_rows if r.trade.client_order_id}
        try:
            table = CostTable.load(state / f"costs_{acc}.json")
        except ValueError:
            table = None
            errors.append(f"costs_{acc}.json unreadable")
        by = {dim: [_group(k, [r for r in rows if r.dims[dim] == k], weekdays)
                    for k in sorted({r.dims[dim] for r in rows}, key=lambda k: (k == NOT_RECORDED, k))]
              for dim in DIMENSIONS}
        not_rec = {f: sum(getattr(r.trade, f) is None for r in rows)
                   for f in ("agent_id", "side", "entry_utc", "r", "commission", "swap", "lots")}
        not_rec["pnl_basis"] = sum(r.basis is None for r in rows)
        not_rec.update({dim: sum(r.dims[dim] == NOT_RECORDED for r in rows) for dim in DIMENSIONS})
        sources: dict[str, int] = {}
        for r in rows:
            k = r.trade.money_source or NOT_RECORDED
            sources[k] = sources.get(k, 0) + 1
        excluded = sum(not (r.trade.pnl_currency is None or ccy is None or r.trade.pnl_currency.upper() == ccy.upper())
                       for r in rows)
        modes = {r.trade.mode for r in rows}
        lcs = learn_by.get(acc, [])
        seen_l = {c.currency for c in lcs if c.currency}
        lccy = ccy or (next(iter(seen_l)) if len(seen_l) == 1 else None)     # the record sections keep `ccy`
        lrows = [_lrow(c, lccy) for c in lcs]
        learning = _learning([r for r in lrows if not r.trade.censored], [r for r in lrows if r.trade.censored],
                             weekdays, lccy)
        out.append(AccountReport(
            account_id=acc, mode=next(iter(modes)) if len(modes) == 1 else ("mixed" if modes else None), currency=ccy,
            overall=_group("all", rows, weekdays), by=by, days=_days(rows, weekdays, ccy),
            north_star=_north_star(entered, ccy, classified, opp, day_types is not None, _open_entries(state, acc),
                                   bars_source),
            costs=_costs(rows, ccy, table, _slippage(store, acc, start, end, table)),
            drawdown=_drawdown(rows, engine),
            approvals=_approvals(props_by.get(acc, []), pending_by.get(acc, 0), shadow, live),
            journal=_journal(store, acc, start, end), not_recorded={k: v for k, v in not_rec.items() if v},
            money_sources=dict(sorted(sources.items())),
            money_note=f"{excluded} trade(s) in another currency counted in trades and R, not in money"
            if excluded else None, learning=learning))
    groups = [g for a in out for gs in a.by.values() for g in gs]
    learning_groups = sum(len(a.learning.kinds) + len(a.learning.families) for a in out if a.learning is not None)
    return PerformanceReport(start=start, end=end, as_of_utc=now.isoformat(), weekdays=weekdays, accounts=out,
                             groups=len(groups), groups_with_reading=sum(g.sufficient for g in groups),
                             learning_groups=learning_groups, errors=errors)


# ---------------------------------------------------------------------------------------------- markdown
def _money(x: float | None, ccy: str | None, *, sign: bool = True) -> str:
    if x is None:
        return NOT_RECORDED
    sym = SYMBOLS.get((ccy or "").upper())
    s = "-" if x < 0 else "+" if sign else ""
    return f"{s}{sym}{abs(x):,.2f}" if sym else f"{s}{abs(x):,.2f}" + (f" {ccy}" if ccy else "")


def _r(x: float | None) -> str:
    return NOT_RECORDED if x is None else f"{x:+.2f}R"


def _pct(x: float | None) -> str:
    return NOT_RECORDED if x is None else f"{x:.2f}%"


def _label(dim: str, key: str) -> str:
    return SESSION_NAMES.get(key, key) if dim == "session" else key


def _table(dim: str, groups: list[GroupStats], ccy: str | None, learning: bool = False) -> list[str]:
    lines = ["| " + dim.replace("_", " ") + " | trades | net | gross | net R | gross R | mean R | per trade | per day "
             "| R per day | win | 90% interval | reading |", "|" + " --- |" * 13]
    for g in groups:
        net = _money(g.net_pnl, ccy) + "".join(
            [f"; net not recorded (gross only, {g.gross_only})" if g.gross_only else "",
             f"; basis not recorded ({g.basis_not_recorded})" if g.basis_not_recorded else ""])
        gross = _money(g.gross_pnl, ccy) + (f" (gross on {g.gross_trades} of {g.trades})"
                                             if g.gross_trades != g.trades else "")
        gr = _r(g.gross_r) + (f" ({g.gross_r_trades} of {g.trades})" if g.gross_r_trades != g.trades else "")
        nr = _r(g.net_r) + (f" ({g.r_trades} of {g.trades})" if g.r_trades != g.trades else "")
        iv = g.excess_ci90 if learning else g.r_ci90        # a learning row's interval is on its excess over the control
        ci = f"{iv[0]:+.2f} to {iv[1]:+.2f}" if iv else "-"
        win = f"{100 * g.win_rate:.0f}%" if g.win_rate is not None else NOT_RECORDED
        trades = f"{g.trades}" + (f" (+{g.censored} censored)" if g.censored else "")
        lines.append(f"| {_label(dim, g.key)} | {trades} | {net} | {gross} | {nr} | {gr} | "
                     f"{_r(g.mean_r)} | {_money(g.mean_pnl, ccy)} | {_money(g.pnl_per_day, ccy)} | {_r(g.r_per_day)} | "
                     f"{win} | {ci} | {g.verdict} |")
    return lines


def _heading(dim: str) -> str:
    if dim == "day_type":
        return "By day type (ex post: classified from the day's own range, known only at its close)"
    return f"By {dim.replace('_', ' ')}"


def format_markdown(rep: PerformanceReport) -> str:
    L = [f"# goldbot performance report: {rep.start} to {rep.end}", "",
         f"As of {rep.as_of_utc[:16].replace('T', ' ')} UTC. Opportunity days (D runs D-1 22:00 to D 22:00 UTC), "
         f"{rep.weekdays} weekdays. No interval or reading from fewer than {rep.min_trades} trades with a net R: such "
         "rows say \"insufficient sample\". Net columns hold net rows only; gross is before commission and swap. "
         "Reporting only.", "",
         f"{rep.groups} groups across {len(DIMENSIONS)} dimensions, {rep.groups_with_reading} with a reading. With many "
         "groups some 90% intervals exclude zero by chance alone (about 1 group in 10 with no edge at all): read a "
         "single group's interval as a lead to test, not a finding"
         + (f", plus {rep.learning_groups} learning groups (kind rows overlap family rows); about 1 in 10 groups shows "
            "an interval by chance" if rep.learning_groups else "") + ".", ""]
    if not rep.accounts:
        L.append("No closed trades or proposals in this range.")
    for a in rep.accounts:
        c = a.currency
        o = a.overall
        L += [f"## {a.account_id} ({a.mode or 'mode ' + NOT_RECORDED}, {c or 'currency ' + NOT_RECORDED})", ""]
        if a.money_note:
            L += [f"Note: {a.money_note}.", ""]
        if a.money_sources:
            L += ["Money source: " + ", ".join(f"{k} {v}" for k, v in a.money_sources.items()) + ".", ""]
        L += ["### Overall", ""] + _table("all", [o], c) + [""]
        for dim in DIMENSIONS:
            L += [f"### {_heading(dim)}", ""] + _table(dim, a.by[dim], c) + [""]
        if a.learning is not None:
            L += ["### Exploration, control and cost-probe closes (not in the phase-gate record)", "",
                  "These trades are never written to the closed-trade record the phase gate, stop rule and throttle "
                  "read; they come from the trade ledger. Net only where commission and swap are both recorded. "
                  "Control rows (random entries) sit beside the families: read a family's mean R against the control's "
                  "over the same range, not alone.", ""]
            lc = a.learning.currency
            L += _table("kind", a.learning.kinds, lc, True) + [""]
            L += _table("kind / family", a.learning.families, lc, True) + [""]
            L += ["A family's reading is its mean net R less the random control's on the same risk day, session and side "
                  "(the 90% interval is on that difference, only from "
                  f"{rep.min_trades} trades on each side; \"unmatched\" trades had no control trade in their cell). "
                  "Censored trades (closed by anything but the arm's own stop, target or hold) are counted beside the "
                  "row, never in R or money; voided trades are left out. The bandit chooses which arms trade and when, so "
                  "a family's pooled arms carry selection bias; no row here is evidence: claims go through a registry "
                  "trial.", ""]
        d = a.days
        L += ["### Days", "",
              f"- traded {d.traded_days} of {d.weekdays} weekdays; mean {_money(d.mean_per_weekday, c)} per weekday; "
              f"median traded day {_money(d.median_traded_day, c)}",
              f"- best {d.best_day or '-'} {_money(d.best_day_pnl, c)}; worst {d.worst_day or '-'} "
              f"{_money(d.worst_day_pnl, c)}",
              f"- days at £50 or more: {d.days_50_plus if d.days_50_plus is not None else 'not a GBP account'}", ""]
        n = a.north_star
        L += ["### North star (ADR 0004 section 6: £50+ on opportunity days)", ""]
        if n.opportunity_days is None:
            L += [f"- {n.note}", ""]
        else:
            L += [f"- opportunity days (range >= {OPPORTUNITY_RANGE_ATR} x the prior ATR(1d), {n.source}): "
                  f"{n.opportunity_days} of {n.classified_days} classified weekdays; traded on "
                  f"{n.traded_opportunity_days}; pending (open trades entered on one): {n.pending_open}",
                  f"- captured (>= {CAPTURE_R:g} R net made): {n.captured_1r} of {n.opportunity_days}; at £50 or more: "
                  f"{n.days_50_plus_on_opportunity if n.days_50_plus_on_opportunity is not None else '-'}",
                  f"- on opportunity days {_money(n.pnl_on_opportunity_days, c)} ({_r(n.r_on_opportunity_days)}); "
                  f"on other days {_money(n.pnl_on_other_days, c)}",
                  f"- {n.note}", ""]
        ap = a.approvals
        reasons = ", ".join(f"{k} {v}" for k, v in ap.reasons.items()) or "-"
        # BACKLOG 83: the MT5 box's or broker's and the not-confirmed counts follow only when there are any
        apart = "".join(f"; {CAUSE_PHRASE[c]}: {n}" for c, n in (("not_placed", ap.not_placed_after_approval),
                                                                  ("not_confirmed", ap.not_confirmed_after_approval))
                        if n)
        L += ["### Owner decisions", "",
              f"- proposals {ap.proposals}: approved {ap.approved} (refused by the RiskGate at approval: "
              f"{ap.gate_refused_after_approval}{apart}), rejected {ap.rejected} ({reasons}), expired {ap.expired}; "
              f"still open {ap.open}", ""]
        if ap.counterfactual:
            L += ["What the signals would have done in the shadow book (GROSS: no commission or swap, gaps filled at "
                  "the stop, so optimistic; matched whether or not the shadow model took the trade):", "",
                  "| outcome | proposals | matched | model version | taken by the shadow model | still open | "
                  "no shadow trade | sum R (gross) | mean R (gross) | wins | money (R x card risk, gross) | reading |",
                  "|" + " --- |" * 12]
            for s in ap.counterfactual:
                ver = "; ".join(x for x in (f"from the record {s.version_from_record}" if s.version_from_record else "",
                                             f"{VERSION_INFERRED} {s.version_inferred}" if s.version_inferred else "")
                                if x) or "-"
                L.append(f"| {s.outcome} | {s.proposals} | {s.matched} | {ver} | {s.taken} | {s.still_open} | "
                         f"{s.unmatched} | "
                         f"{_r(s.sum_r)} | {_r(s.mean_r)} | {s.wins} | {_money(s.money, s.currency)} | {s.verdict} |")
            for s in ap.counterfactual:
                if s.unmatched_reasons:
                    L.append(f"- {s.outcome}, no shadow trade: "
                             + "; ".join(f"{k} ({v})" for k, v in s.unmatched_reasons.items()))
            L.append("")
        if ap.calibration is not None:
            cb = ap.calibration
            L += ["Calibration (approved signals with a live net row): "
                  f"{cb.pairs} pairs; shadow {_r(cb.shadow_sum_r)} gross, live {_r(cb.live_sum_r)} net; mean gap "
                  f"(live - shadow) {_r(cb.mean_gap_r)}; {cb.verdict}", ""]
        k = a.costs
        execution = (f"{k.execution_slippage_mean_usd:+.3f} $/oz over {k.execution_slippage_trades}"
                     if k.execution_slippage_mean_usd is not None else NOT_RECORDED + " (no requested_price)")
        drift = (f"{k.approval_drift_mean_usd:+.3f} $/oz over {k.approval_drift_trades}"
                 if k.approval_drift_mean_usd is not None else NOT_RECORDED + " (no decision or requested price)")
        L += ["### Costs (realised against modelled)", "",
              f"- commission {_money(k.commission_total, c, sign=False)} on {k.commission_trades} of {o.trades} trades; "
              f"swap {_money(k.swap_total, c)} on {k.swap_trades}; mean cost {_r(k.cost_r_mean)} per trade over "
              f"{k.cost_r_trades}",
              f"- modelled commission {_money(k.modelled_commission_total, 'USD', sign=False)}; realised / modelled "
              + (f"{k.commission_vs_model:.2f}" if k.commission_vs_model is not None else (k.commission_note or "-")),
              f"- execution slippage from the records (fill against the requested price): {execution}",
              f"- approval drift from the records (requested against the decision price, the owner's wait): {drift}"]
        if k.slippage:
            L += ["", "| session | fills | realised slippage $/oz | modelled $/oz | model from | sample |",
                  "|" + " --- |" * 6]
            for sl in k.slippage:
                mod = f"{sl.modelled_usd:.3f}" if sl.modelled_usd is not None else NOT_RECORDED
                real = f"{sl.realised_mean_usd:+.3f}" if sl.realised_mean_usd is not None else NOT_RECORDED
                L.append(f"| {_label('session', sl.session)} | {sl.fills} | {real} | {mod} | "
                         f"{sl.modelled_from or '-'} | {'ok' if sl.sufficient else f'insufficient (< {MIN_TRADES})'} |")
        else:
            L.append(f"- slippage from fills {NOT_RECORDED} (needs fills under --data-root)")
        L.append("")
        dd = a.drawdown
        abs_txt = _money(dd.daily_loss_limit_abs, c, sign=False) if dd.daily_loss_limit_abs is not None else "off"
        day_txt = (f"{dd.worst_day}: {_money(-dd.worst_day_loss, c)} ({_pct(dd.worst_day_pct)})"
                   if dd.worst_day is not None and dd.worst_day_loss is not None else "none: no losing day")
        week_txt = (f"from {dd.worst_week}: {_money(-dd.worst_week_loss, c)} ({_pct(dd.worst_week_pct)})"
                    if dd.worst_week is not None and dd.worst_week_loss is not None else "none: no losing week")
        L += ["### Drawdown against the limits (closed-trade, lower bound)", "",
              "- realised net P&L only, in the RiskGate's windows (risk day from 00:00 UTC, risk week from Sunday "
              "00:00 UTC). A lower bound on what the RiskGate saw: intraday floating losses and its open-risk "
              "projection are not in it; the exact figures need a daily risk summary from the engine (not built yet).",
              f"- closed-trade drawdown {_pct(dd.max_drawdown_pct)} (size-down {dd.dd_stage1_pct:.0f}%, halt "
              f"{dd.dd_stage2_pct:.0f}%)",
              f"- worst risk day {day_txt}; daily cap {dd.daily_cap_pct:.1f}%, absolute {abs_txt}; days at the daily "
              f"limit: {dd.days_at_daily_limit}",
              f"- worst risk week {week_txt}; weekly cap {dd.weekly_cap_pct:.1f}%",
              f"- engine now: drawdown {_pct(dd.engine_drawdown_pct)} from the closed-balance high-water mark, stage "
              f"{dd.engine_stage or NOT_RECORDED}; limits from {dd.limits_source}", ""]
        if a.journal is not None:
            j = a.journal
            gate = ", ".join(f"{k} {v}" for k, v in j.gate_refusals.items()) or "-"
            L += ["### Journal", "", f"- {j.decisions} decisions: proposed {j.proposed}, auto entries {j.auto_entries}, "
                  f"orders {j.orders} ({j.orders_failed} failed); gate refusals: {gate}", ""]
        if a.not_recorded:
            L += ["### Not recorded", "", "- " + ", ".join(f"{k}: {v} of {o.trades}" for k, v in a.not_recorded.items()),
                  ""]
    for e in rep.errors:
        L.append(f"Warning: {e}")
    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------------------------------------- cli
def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def default_range(now: pd.Timestamp, start: str | None = None, end: str | None = None) -> tuple[str, str]:
    """`--from`/`--to` defaults: the first opportunity day of the month holding today's opportunity day, and today's
    (at 31 Oct 23:00 UTC that is 1 Nov..1 Nov, the month the monthly report shows)."""
    today = report_day(now)
    return start or month_days(today[:7])[0], end or today


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goldbot.ops.run perf-report", description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--state", default="state", help="state folder (read only; a copy works)")
    ap.add_argument("--data-root", help="Parquet store for bars, fills and the journal (read only; optional)")
    ap.add_argument("--bars-source", default=BARS_SOURCE, help="bars_1m source for the day types (default dukascopy)")
    ap.add_argument("--from", dest="start", help="first opportunity day YYYY-MM-DD (default: the month's first)")
    ap.add_argument("--to", dest="end", help="last opportunity day YYYY-MM-DD (default: today)")
    ap.add_argument("--account", help="one account only")
    ap.add_argument("--out", default="reports", help="folder for the markdown and JSON (not inside the state folder)")
    ap.add_argument("--json", action="store_true", help="print the JSON instead of the markdown")
    a = ap.parse_args(argv)
    now = pd.Timestamp.now(tz="UTC")
    start, end = default_range(now, a.start, a.end)
    for v in (start, end):
        if not DATE.fullmatch(v):
            print(f"dates must be YYYY-MM-DD, got {v!r}", file=sys.stderr)
            return 2
    if start > end:
        print(f"--from {start} is after --to {end}", file=sys.stderr)
        return 2
    state, out = Path(a.state), Path(a.out)
    if _inside(out, state) or (a.data_root and _inside(out, Path(a.data_root))):
        print("--out must not be inside the state folder or the data root (the report never writes there)",
              file=sys.stderr)
        return 2
    rep = build_performance_report(state, start=start, end=end, data_root=a.data_root, account=a.account, now=now,
                                   bars_source=a.bars_source)
    md, js = format_markdown(rep), rep.model_dump_json(indent=2)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"perf_{start}_{end}.md").write_text(md, encoding="utf-8")
    (out / f"perf_{start}_{end}.json").write_text(js + "\n", encoding="utf-8")
    print(js if a.json else md)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
