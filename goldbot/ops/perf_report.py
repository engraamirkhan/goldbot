"""Performance report for the performance-analyst and finance-auditor agents (north-star: ADR 0004 section 6;
evaluation: docs/research/opportunity-days.md section 3.4), from the local state, read only.

    python -m goldbot.ops.run perf-report [--state DIR] [--data-root DIR] [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                                          [--account ID] [--out DIR] [--json]
    python scripts/performance_report.py ...          (the same, from a state folder copied to the Mac)

Writes `perf_<from>_<to>.md` and `perf_<from>_<to>.json` to `--out` (default `reports`, which may not be inside the
state folder) and prints the markdown (`--json`: the JSON). Per account, for the opportunity days `--from`..`--to`
(day D runs D-1 22:00 to D 22:00 UTC, features/day_type.py, so the Sunday reopen belongs to Monday; default the month
so far):

* net and gross R and money per trade and per day (per weekday in the range, traded or not), overall and by family,
  agent, timeframe, session (`DEFAULT_SESSIONS.session_label` at the entry: asia, the Tokyo session; london; newyork),
  side and day type. Gross is before commission and swap (spread and slippage are inside the fill prices); gross R is
  gross over the initial risk, the risk being net P&L / net R;
* owner decisions: proposals approved, rejected (by reason) and expired, and what the rejected and expired signals (and
  the approved ones, for comparison) did in the shadow book: the shadow trade of the same agent, side and signal bar
  (the proposal id carries the signal bar's close; with several model versions, the one whose p is nearest the card's),
  in R of its own initial stop, gross, with the shadow book's optimistic gap fills (engine/shadow.py), and in money as
  R x the card's stop risk;
* realised against modelled costs where both exist: commission against the cost table's (state/costs_<account>.json)
  per-lot commission, only when the account's currency is USD (no conversion is recorded, so none is guessed); with
  `--data-root`, the fills' slippage by session against the table's cell or its prior;
* north-star capture (ADR 0004 section 6 KPI): with `--data-root` bars, an opportunity day is a stub-free weekday whose
  range is at least 1.5 x ATR(1d) of the 20 stub-free days before it (day_type.daily_inputs); captured = the trades
  entered on it made at least 1 R; and the days at £50 or more on them (GBP accounts);
* drawdown against the limits: closed-trade drawdown against the 8% / 12% stages, the worst day against the daily cap
  and the absolute limit, the worst week against the weekly cap, and the engine's live drawdown from its high-water mark;
* with `--data-root`, the journal: proposals, gate refusals by reason, orders and failed orders.

Design rule (the brief to the performance-analyst): "sample sizes everywhere, with no conclusion drawn from fewer than
~30 trades". Every figure carries its count; an interval and a reading ("mean R above zero") are given only from
MIN_TRADES trades, and below that the row says "insufficient sample" instead.

Missing data is never guessed: a field a record does not carry is counted under "not recorded". Fields a newer record
may add (RECORD_GAP_FIELDS: family, timeframe, session, day type) are used when present; otherwise the family comes
from the agent id (`AgentIdentity.agent_id` is `<family>-g<generation>-<hash>`), the timeframe from the population
(state/population.json) or a family with one timeframe, the session from the entry time, the day type from the bars.

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
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, load_record, load_record_json
from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.engine.shadow import ShadowTrade, VersionBook
from goldbot.execution.costs import SESSIONS, CostTable
from goldbot.ops.gates_phase import ClosedTrade, load_closed_trade_rows
from goldbot.telegram.approvals import Outcome, Proposal

MIN_TRADES = 30                  # below this no interval and no reading, only the counts (design rule above)
NORTH_STAR_GBP = 50.0            # ADR 0004 section 6: days at £50+ are counted, never aimed at
OPPORTUNITY_RANGE_ATR = 1.5      # ADR 0004 section 6 KPI: daily range >= 1.5 x ATR(1d)
CAPTURE_R = 1.0                  # ... on which the system made >= 1 R
NOT_RECORDED = "not recorded"
DIMENSIONS = ("family", "agent", "timeframe", "session", "side", "day_type")
# fields a newer closed-trade record may carry (the record-gap lane): used as written, never re-derived
RECORD_GAP_FIELDS = {"family": "family", "timeframe": "timeframe", "session": "session", "day_type": "day_type"}
SESSION_NAMES = {"asia": "asia (Tokyo)", "london": "london (London)", "newyork": "newyork (New York)"}
AGENT_ID = re.compile(r"^([a-z0-9_]+)-g\d+-[0-9a-f]{10}$")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


# ---------------------------------------------------------------------------------------------- records
class GroupStats(FrozenRecord):
    key: str
    trades: int
    net_pnl: float | None = None           # account currency, rows in it only (money_trades)
    money_trades: int = 0
    gross_pnl: float | None = None         # before commission and swap, over the trades that record both
    gross_trades: int = 0
    net_r: float | None = None
    r_trades: int = 0
    gross_r: float | None = None
    gross_r_trades: int = 0
    mean_r: float | None = None
    mean_pnl: float | None = None
    pnl_per_day: float | None = None       # per weekday in the range, traded or not
    r_per_day: float | None = None
    win_rate: float | None = None          # share of trades with R > 0 (R recorded)
    r_ci90: tuple[float, float] | None = None   # Student-t, only from MIN_TRADES trades with R
    sufficient: bool
    verdict: str


class DayStats(FrozenRecord):
    weekdays: int
    traded_days: int
    mean_per_weekday: float | None = None
    median_traded_day: float | None = None
    best_day: str | None = None
    best_day_pnl: float | None = None
    worst_day: str | None = None
    worst_day_pnl: float | None = None
    days_50_plus: int | None = None        # GBP accounts only


class NorthStar(FrozenRecord):
    source: str | None = None              # "bars" when opportunity days were classified
    classified_days: int = 0               # weekdays in the range with a range and a prior ATR(1d)
    opportunity_days: int | None = None
    traded_opportunity_days: int | None = None
    captured_1r: int | None = None
    days_50_plus_on_opportunity: int | None = None    # GBP accounts only
    pnl_on_opportunity_days: float | None = None
    pnl_on_other_days: float | None = None
    r_on_opportunity_days: float | None = None
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
    cost_table_built_utc: str | None = None
    slippage: list[SlippageRow] = []


class DrawdownReport(FrozenRecord):
    max_drawdown_pct: float | None = None      # closed-trade return index over the range
    dd_stage1_pct: float
    dd_stage2_pct: float
    worst_day: str | None = None
    worst_day_loss: float | None = None        # the largest net loss (> 0), account currency; None: no losing day
    worst_day_pct: float | None = None         # of the equity before the day's first trade
    daily_cap_pct: float
    daily_loss_limit_abs: float | None = None
    days_at_daily_limit: int = 0
    worst_week: str | None = None
    worst_week_loss: float | None = None       # None: no losing week (ISO weeks of the exit day)
    worst_week_pct: float | None = None
    weekly_cap_pct: float
    engine_drawdown_pct: float | None = None   # live equity against the closed-balance high-water mark
    engine_stage: str | None = None
    limits_source: str


class ShadowOutcome(FrozenRecord):
    outcome: str                               # approved | rejected | expired
    proposals: int
    matched: int = 0                           # closed shadow trade found
    still_open: int = 0
    unmatched: int = 0
    net_r: float | None = None
    mean_r: float | None = None
    wins: int = 0
    money: float | None = None                 # sum of R x the card's stop risk
    money_trades: int = 0
    currency: str | None = None
    sufficient: bool
    verdict: str


class ApprovalReport(FrozenRecord):
    proposals: int = 0
    approved: int = 0
    rejected: int = 0
    expired: int = 0
    open: int = 0                              # not finished yet (pending)
    gate_refused_after_approval: int = 0
    reasons: dict[str, int] = {}
    counterfactual: list[ShadowOutcome] = []


class JournalReport(FrozenRecord):
    decisions: int
    proposed: int
    auto_entries: int
    gate_refusals: dict[str, int]
    orders: int
    orders_failed: int


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
    money_note: str | None = None


class PerformanceReport(FrozenRecord):
    start: str
    end: str
    as_of_utc: str
    weekdays: int
    min_trades: int = MIN_TRADES
    accounts: list[AccountReport]
    errors: list[str] = []


class _Row(FrozenRecord):
    """One closed trade with what the report derives from it."""
    trade: ClosedTrade
    exit_day: str
    entry_day: str | None
    money: bool                                # pnl in the account's currency
    gross: float | None
    risk: float | None                         # initial risk in account currency (pnl / r)
    gross_r: float | None
    dims: dict[str, str]


# ---------------------------------------------------------------------------------------------- helpers
def opp_day(ts: pd.Timestamp) -> str:
    """The opportunity day (features/day_type.py: [D-1 22:00, D 22:00) UTC is D) of one instant, as YYYY-MM-DD."""
    from goldbot.features.day_type import opportunity_day
    return f"{opportunity_day(pd.DatetimeIndex([ts]))[0]:%Y-%m-%d}"


def _weekdays(start: str, end: str) -> int:
    return int(np.busday_count(date.fromisoformat(start), date.fromisoformat(end) + timedelta(days=1)))


def _ci90(xs: list[float]) -> tuple[float, float]:
    from scipy import stats
    m, sd = float(np.mean(xs)), float(np.std(xs, ddof=1))
    half = float(stats.t.ppf(0.95, len(xs) - 1)) * sd / math.sqrt(len(xs))
    return (m - half, m + half)


def _reading(n: int, ci: tuple[float, float] | None) -> str:
    if n < MIN_TRADES or ci is None:
        return f"insufficient sample ({n} < {MIN_TRADES} trades): no conclusion"
    if ci[0] > 0:
        return "mean R above zero (90% interval)"
    if ci[1] < 0:
        return "mean R below zero (90% interval)"
    return "mean R not distinguishable from zero (90% interval)"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _group(key: str, rows: list[_Row], weekdays: int) -> GroupStats:
    money = [r for r in rows if r.money]
    rs = [r.trade.r for r in rows if r.trade.r is not None]
    gross = [r.gross for r in money if r.gross is not None]
    grs = [r.gross_r for r in rows if r.gross_r is not None]
    net = sum(r.trade.pnl for r in money) if money else None
    net_r = sum(rs) if rs else None
    ci = _ci90(rs) if len(rs) >= MIN_TRADES else None
    return GroupStats(
        key=key, trades=len(rows), net_pnl=net, money_trades=len(money), gross_pnl=sum(gross) if gross else None,
        gross_trades=len(gross), net_r=net_r, r_trades=len(rs), gross_r=sum(grs) if grs else None,
        gross_r_trades=len(grs), mean_r=float(np.mean(rs)) if rs else None,
        mean_pnl=net / len(money) if net is not None else None,
        pnl_per_day=net / weekdays if net is not None and weekdays else None,
        r_per_day=net_r / weekdays if net_r is not None and weekdays else None,
        win_rate=sum(x > 0 for x in rs) / len(rs) if rs else None, r_ci90=ci,
        sufficient=len(rows) >= MIN_TRADES and ci is not None, verdict=_reading(len(rs), ci))


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


def _gap(raw: dict[str, Any], dim: str) -> str | None:
    v = raw.get(RECORD_GAP_FIELDS[dim])
    return v if isinstance(v, str) and v else None


def _dims(t: ClosedTrade, raw: dict[str, Any], population: dict[str, dict[str, Any]],
          day_types: dict[str, str] | None, entry_day: str | None) -> dict[str, str]:
    m = AGENT_ID.match(t.agent_id or "")
    family = _gap(raw, "family") or (m.group(1) if m else None)
    tf = _gap(raw, "timeframe") or _timeframe(t.agent_id, family, population)
    session = _gap(raw, "session")
    if session is None and t.entry_utc is not None:
        session = str(DEFAULT_SESSIONS.session_label(pd.DatetimeIndex([t.entry_utc]))[0])
    day_type = _gap(raw, "day_type")
    if day_type is None and day_types is not None and entry_day is not None:
        day_type = day_types.get(entry_day)
    side = {1: "long", -1: "short"}.get(t.side or 0)
    return {"family": family or NOT_RECORDED, "agent": t.agent_id or NOT_RECORDED, "timeframe": tf or NOT_RECORDED,
            "session": session or NOT_RECORDED, "side": side or NOT_RECORDED, "day_type": day_type or NOT_RECORDED}


def _row(t: ClosedTrade, raw: dict[str, Any], ccy: str | None, population: dict[str, dict[str, Any]],
         day_types: dict[str, str] | None) -> _Row:
    money = t.pnl_currency is None or ccy is None or t.pnl_currency.upper() == ccy.upper()
    gross = t.pnl + t.commission - t.swap if t.commission is not None and t.swap is not None else None
    risk = t.pnl / t.r if t.r not in (None, 0) and t.pnl != 0 and money else None
    gross_r = gross / risk if gross is not None and risk is not None and risk > 0 else None
    entry_day = opp_day(t.entry_utc) if t.entry_utc is not None else None
    return _Row(trade=t, exit_day=opp_day(t.exit_utc), entry_day=entry_day, money=money, gross=gross,
                risk=risk if risk is not None and risk > 0 else None, gross_r=gross_r,
                dims=_dims(t, raw, population, day_types, entry_day))


def _proposals(state: Path) -> tuple[list[Proposal], list[Proposal]]:
    """Finished proposals (approvals/done) and those still pending, read without creating a folder."""
    out: list[Proposal] = []
    root = state / "approvals"
    for f in sorted((root / "done").glob("*.json")) if (root / "done").is_dir() else []:
        try:
            out.append(load_record_json(Proposal, f.read_text(encoding="utf-8"), source="approvals/done"))
        except (OSError, ValueError):
            continue
    pending: list[Proposal] = []
    for f in sorted((root / "pending").glob("*.json")) if (root / "pending").is_dir() else []:
        try:
            pending.append(load_record_json(Proposal, f.read_text(encoding="utf-8"), source="approvals/pending"))
        except (OSError, ValueError):
            continue
    return out, pending


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
        for k, v in raw.items():
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


def _match(p: Proposal, close: pd.Timestamp | None, shadow: list[ShadowTrade]) -> ShadowTrade | None:
    if close is None:
        return None
    cands = [t for t in shadow if t.agent_id == p.agent_id and t.side == p.side and t.taken
             and t.entry_ts + pd.Timedelta(seconds=tf_seconds(t.timeframe)) == close]
    return min(cands, key=lambda t: abs(t.p - p.p)) if cands else None


# ---------------------------------------------------------------------------------------------- data root readers
def _store(data_root: Path | None) -> Any:
    if data_root is None or not Path(data_root).is_dir():
        return None
    from goldbot.data.store import Store
    return Store(data_root)            # the root exists, so its mkdir writes nothing


def _day_types(store: Any, start: str, end: str) -> tuple[dict[str, str] | None, int, set[str]]:
    """Opportunity day -> "opportunity" | "ordinary" for the stub-free days with a prior ATR(1d) (ADR 0004 section 6:
    range >= 1.5 x ATR(1d), the ATR of the ATR_DAYS stub-free days before the day), from the stored 1m bars (mid).
    Returns (types, classified weekdays in the range, opportunity days in the range)."""
    from goldbot.features.day_type import ATR_DAYS, daily_bars, daily_inputs
    if store is None:
        return None, 0, set()
    lo = pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=3 * ATR_DAYS)
    bars = store.read("bars_1m", start=lo, end=pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1))
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
    prior_atr = inp["atr"].shift(1)
    out: dict[str, str] = {}
    for day, rng, atr in zip(inp["day"], inp["range"], prior_atr):
        if pd.notna(atr) and atr > 0 and pd.notna(rng):
            out[f"{pd.Timestamp(day):%Y-%m-%d}"] = "opportunity" if rng >= OPPORTUNITY_RANGE_ATR * atr else "ordinary"
    days = {d for d in out if start <= d <= end and date.fromisoformat(d).weekday() < 5}
    return out, len(days), {d for d in days if out[d] == "opportunity"}


def _slippage(store: Any, account: str, start: str, end: str, table: CostTable | None) -> list[SlippageRow]:
    if store is None:
        return []
    f = store.read("fills", source=account, start=pd.Timestamp(start, tz="UTC") - pd.Timedelta(hours=2),
                   end=pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=22))
    if f.empty or not {"requested", "filled", "side"} <= set(f.columns):
        return []
    f = f.dropna(subset=["requested", "filled", "side"])
    f = f[[start <= opp_day(t) <= end for t in f["ts_utc"]]]
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
    d = d[[start <= opp_day(t) <= end for t in d["ts_utc"]]]
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
    per: dict[str, float] = {}
    for r in rows:
        if r.money:
            per[r.exit_day] = per.get(r.exit_day, 0.0) + r.trade.pnl
    if not per:
        return DayStats(weekdays=weekdays, traded_days=0)
    best, worst = max(per, key=lambda d: per[d]), min(per, key=lambda d: per[d])
    return DayStats(weekdays=weekdays, traded_days=len(per),
                    mean_per_weekday=sum(per.values()) / weekdays if weekdays else None,
                    median_traded_day=float(np.median(list(per.values()))), best_day=best, best_day_pnl=per[best],
                    worst_day=worst, worst_day_pnl=per[worst],
                    days_50_plus=sum(v >= NORTH_STAR_GBP for v in per.values()) if ccy == "GBP" else None)


def _north_star(rows: list[_Row], ccy: str | None, classified: int, opp: set[str], have_bars: bool) -> NorthStar:
    if not have_bars:
        return NorthStar(note=f"opportunity days {NOT_RECORDED}: they need the stored bars (--data-root)")
    per_pnl: dict[str, float] = {}
    per_r: dict[str, float] = {}
    for r in rows:
        if r.entry_day is None:
            continue
        if r.money:
            per_pnl[r.entry_day] = per_pnl.get(r.entry_day, 0.0) + r.trade.pnl
        if r.trade.r is not None:
            per_r[r.entry_day] = per_r.get(r.entry_day, 0.0) + r.trade.r
    traded = {d for d in per_pnl.keys() | per_r.keys()}
    on = [r for r in rows if r.entry_day in opp]
    off = [r for r in rows if r.entry_day is not None and r.entry_day not in opp]
    note = "days by the entry's opportunity day; trades without an entry time are left out"
    if ccy != "GBP":
        note += f"; the £50 north star is in GBP and this account reports {ccy or 'no currency'}"
    return NorthStar(
        source="bars", classified_days=classified, opportunity_days=len(opp), traded_opportunity_days=len(traded & opp),
        captured_1r=sum(per_r.get(d, 0.0) >= CAPTURE_R for d in opp),
        days_50_plus_on_opportunity=sum(per_pnl.get(d, 0.0) >= NORTH_STAR_GBP for d in opp) if ccy == "GBP" else None,
        pnl_on_opportunity_days=sum(r.trade.pnl for r in on if r.money) if on else None,
        pnl_on_other_days=sum(r.trade.pnl for r in off if r.money) if off else None,
        r_on_opportunity_days=sum(r.trade.r for r in on if r.trade.r is not None) if on else None, note=note)


def _costs(rows: list[_Row], ccy: str | None, table: CostTable | None, slippage: list[SlippageRow]) -> CostReport:
    com = [r for r in rows if r.trade.commission is not None and r.money]
    swp = [r for r in rows if r.trade.swap is not None and r.money]
    cost_r = [(r.trade.commission - r.trade.swap) / r.risk for r in rows
              if r.trade.commission is not None and r.trade.swap is not None and r.risk is not None]
    modelled = vs = None
    note = None
    if table is not None:
        lots = [r.trade.lots for r in com if r.trade.lots is not None]
        if com and len(lots) == len(com):
            modelled = sum(2 * table.commission_per_lot_side_usd * lot for lot in lots)
            if (ccy or "").upper() == "USD" and modelled > 0:
                vs = sum(r.trade.commission for r in com) / modelled  # type: ignore[misc]
            else:
                note = (f"not comparable: realised in {ccy or 'an unreported currency'}, modelled in USD "
                        "(no conversion is recorded)")
        elif com:
            note = f"lots {NOT_RECORDED} on some trades: the modelled commission is not computed"
    else:
        note = f"cost table {NOT_RECORDED} (state/costs_<account>.json)"
    return CostReport(
        commission_trades=len(com), commission_total=sum(r.trade.commission for r in com) if com else None,  # type: ignore[misc]
        swap_trades=len(swp), swap_total=sum(r.trade.swap for r in swp) if swp else None,  # type: ignore[misc]
        cost_r_mean=float(np.mean(cost_r)) if cost_r else None, cost_r_trades=len(cost_r),
        modelled_commission_total=modelled, commission_vs_model=vs, commission_note=note,
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
    daily, weekly, s1, s2, abs_lim, source = _limits(engine)
    money = sorted((r for r in rows if r.money), key=lambda r: r.trade.exit_utc)
    idx, peak, dd = 1.0, 1.0, 0.0
    days: dict[str, list[_Row]] = {}
    weeks: dict[str, list[_Row]] = {}
    for r in money:
        idx *= 1 + r.trade.pnl / r.trade.equity_before
        peak = max(peak, idx)
        dd = max(dd, 1 - idx / peak)
        days.setdefault(r.exit_day, []).append(r)
        y, w, _ = date.fromisoformat(r.exit_day).isocalendar()
        weeks.setdefault(f"{y}-W{w:02d}", []).append(r)

    def worst(groups: dict[str, list[_Row]]) -> tuple[str | None, float | None, float | None]:
        """The period with the largest net loss; none when no period lost money (only losses meet a limit)."""
        loss = {k: -sum(r.trade.pnl for r in v) for k, v in groups.items()}
        if not loss or max(loss.values()) <= 0:
            return None, None, None
        k = max(loss, key=lambda x: loss[x])
        return k, loss[k], 100 * loss[k] / groups[k][0].trade.equity_before

    wd, wdl, wdp = worst(days)
    ww, wwl, wwp = worst(weeks)
    at_limit = 0
    for v in days.values():
        start_eq = v[0].trade.equity_before
        limit = daily * start_eq if abs_lim is None else min(daily * start_eq, abs_lim)
        at_limit += -sum(r.trade.pnl for r in v) >= limit
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


def _approvals(props: list[Proposal], pending: int, shadow: list[ShadowTrade]) -> ApprovalReport:
    by: dict[str, list[Proposal]] = {"approved": [], "rejected": [], "expired": []}
    names = {Outcome.APPROVED: "approved", Outcome.REJECTED: "rejected", Outcome.EXPIRED: "expired"}
    for p in props:
        if p.outcome is not None:
            by[names[p.outcome]].append(p)
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
        still = unmatched = 0
        ccys = set()
        for p in ps:
            t = _match(p, _signal_close(p), shadow)
            if t is None:
                unmatched += 1
                continue
            r = _shadow_r(t)
            if r is None:
                still += 1
                continue
            rs.append(r)
            if p.risk_amount is not None:
                money.append(r * p.risk_amount)
                ccys.add(p.currency)
        cf.append(ShadowOutcome(
            outcome=name, proposals=len(ps), matched=len(rs), still_open=still, unmatched=unmatched,
            net_r=sum(rs) if rs else None, mean_r=float(np.mean(rs)) if rs else None, wins=sum(r > 0 for r in rs),
            money=sum(money) if money and len(ccys) == 1 else None, money_trades=len(money) if len(ccys) == 1 else 0,
            currency=next(iter(ccys)) if len(ccys) == 1 else None, sufficient=len(rs) >= MIN_TRADES,
            verdict=_reading(len(rs), _ci90(rs) if len(rs) >= MIN_TRADES else None)))
    return ApprovalReport(proposals=len(props), approved=len(by["approved"]), rejected=len(by["rejected"]),
                          expired=len(by["expired"]), open=pending,
                          gate_refused_after_approval=sum(p.gate_refusal is not None for p in by["approved"]),
                          reasons=dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))), counterfactual=cf)


def _proposal_day(p: Proposal) -> str:
    close = _signal_close(p)
    return opp_day(close if close is not None else pd.Timestamp(p.created, unit="s", tz="UTC"))


# ---------------------------------------------------------------------------------------------- build
def build_performance_report(state_dir: str | Path, *, start: str, end: str, data_root: str | Path | None = None,
                             account: str | None = None, now: pd.Timestamp | None = None) -> PerformanceReport:
    """The report for opportunity days `start`..`end` (YYYY-MM-DD, inclusive). Reads only."""
    state = Path(state_dir)
    now = pd.Timestamp.now(tz="UTC") if now is None else now.tz_convert("UTC")
    weekdays = _weekdays(start, end)
    errors: list[str] = []
    rows_raw, err = load_closed_trade_rows(state) if state.is_dir() else ([], None)
    if err:
        errors.append(err)
    store = _store(Path(data_root) if data_root is not None else None)
    day_types, classified, opp = _day_types(store, start, end)
    population = _population(state)
    shadow, serr = _shadow_trades(state)
    if serr:
        errors.append(serr)
    done, pending_props = _proposals(state)
    by_acc: dict[str, list[tuple[ClosedTrade, dict[str, Any]]]] = {}
    for t, raw in rows_raw:
        if start <= opp_day(t.exit_utc) <= end:
            by_acc.setdefault(t.account_id, []).append((t, raw))
    props_by: dict[str, list[Proposal]] = {}
    for p in done:
        if start <= _proposal_day(p) <= end:
            props_by.setdefault(p.account_id, []).append(p)
    pending_by: dict[str, int] = {}
    for p in pending_props:
        if start <= _proposal_day(p) <= end:
            pending_by[p.account_id] = pending_by.get(p.account_id, 0) + 1
    accounts = sorted(set(by_acc) | set(props_by) | set(pending_by))
    if account is not None:
        accounts = [a for a in accounts if a == account]
    out = []
    for acc in accounts:
        engine = _read_json(state / f"engine_{acc}.json")
        engine = engine if isinstance(engine, dict) else {}
        trades = by_acc.get(acc, [])
        ccy = engine.get("currency") if isinstance(engine.get("currency"), str) else None
        if ccy is None:
            seen = {t.pnl_currency for t, _ in trades if t.pnl_currency}
            ccy = next(iter(seen)) if len(seen) == 1 else None
        rows = [_row(t, raw, ccy, population, day_types) for t, raw in trades]
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
        not_rec.update({dim: sum(r.dims[dim] == NOT_RECORDED for r in rows) for dim in DIMENSIONS})
        excluded = sum(not r.money for r in rows)
        modes = {r.trade.mode for r in rows}
        out.append(AccountReport(
            account_id=acc, mode=next(iter(modes)) if len(modes) == 1 else ("mixed" if modes else None), currency=ccy,
            overall=_group("all", rows, weekdays), by=by, days=_days(rows, weekdays, ccy),
            north_star=_north_star(rows, ccy, classified, opp, day_types is not None),
            costs=_costs(rows, ccy, table, _slippage(store, acc, start, end, table)),
            drawdown=_drawdown(rows, engine),
            approvals=_approvals(props_by.get(acc, []), pending_by.get(acc, 0), shadow),
            journal=_journal(store, acc, start, end), not_recorded={k: v for k, v in not_rec.items() if v},
            money_note=f"{excluded} trade(s) in another currency counted in trades and R, not in money"
            if excluded else None))
    return PerformanceReport(start=start, end=end, as_of_utc=now.isoformat(), weekdays=weekdays, accounts=out,
                             errors=errors)


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


def _table(dim: str, groups: list[GroupStats], ccy: str | None) -> list[str]:
    lines = ["| " + dim.replace("_", " ") + " | trades | net | gross | net R | gross R | mean R | per trade | per day "
             "| R per day | win | 90% interval | reading |", "|" + " --- |" * 13]
    for g in groups:
        gross = _money(g.gross_pnl, ccy) + (f" (gross on {g.gross_trades} of {g.trades})"
                                             if g.gross_trades != g.trades else "")
        gr = _r(g.gross_r) + (f" ({g.gross_r_trades} of {g.trades})" if g.gross_r_trades != g.trades else "")
        nr = _r(g.net_r) + (f" ({g.r_trades} of {g.trades})" if g.r_trades != g.trades else "")
        ci = f"{g.r_ci90[0]:+.2f} to {g.r_ci90[1]:+.2f}" if g.r_ci90 else "-"
        win = f"{100 * g.win_rate:.0f}%" if g.win_rate is not None else NOT_RECORDED
        lines.append(f"| {_label(dim, g.key)} | {g.trades} | {_money(g.net_pnl, ccy)} | {gross} | {nr} | {gr} | "
                     f"{_r(g.mean_r)} | {_money(g.mean_pnl, ccy)} | {_money(g.pnl_per_day, ccy)} | {_r(g.r_per_day)} | "
                     f"{win} | {ci} | {g.verdict} |")
    return lines


def format_markdown(rep: PerformanceReport) -> str:
    L = [f"# goldbot performance report: {rep.start} to {rep.end}", "",
         f"As of {rep.as_of_utc[:16].replace('T', ' ')} UTC. Opportunity days (D runs D-1 22:00 to D 22:00 UTC), "
         f"{rep.weekdays} weekdays. No interval or reading from fewer than {rep.min_trades} trades: such rows say "
         "\"insufficient sample\". Gross is before commission and swap. Reporting only.", ""]
    if not rep.accounts:
        L.append("No closed trades or proposals in this range.")
    for a in rep.accounts:
        c = a.currency
        o = a.overall
        L += [f"## {a.account_id} ({a.mode or 'mode ' + NOT_RECORDED}, {c or 'currency ' + NOT_RECORDED})", ""]
        if a.money_note:
            L += [f"Note: {a.money_note}.", ""]
        L += ["### Overall", ""] + _table("all", [o], c) + [""]
        for dim in DIMENSIONS:
            L += [f"### By {dim.replace('_', ' ')}", ""] + _table(dim, a.by[dim], c) + [""]
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
            L += [f"- opportunity days (range >= {OPPORTUNITY_RANGE_ATR} x ATR(1d)): {n.opportunity_days} of "
                  f"{n.classified_days} classified weekdays; traded on {n.traded_opportunity_days}",
                  f"- captured (>= {CAPTURE_R:g} R made): {n.captured_1r} of {n.opportunity_days}; at £50 or more: "
                  f"{n.days_50_plus_on_opportunity if n.days_50_plus_on_opportunity is not None else '-'}",
                  f"- on opportunity days {_money(n.pnl_on_opportunity_days, c)} ({_r(n.r_on_opportunity_days)}); "
                  f"on other days {_money(n.pnl_on_other_days, c)}",
                  f"- {n.note}", ""]
        ap = a.approvals
        reasons = ", ".join(f"{k} {v}" for k, v in ap.reasons.items()) or "-"
        L += ["### Owner decisions", "",
              f"- proposals {ap.proposals}: approved {ap.approved} (refused by the RiskGate at approval: "
              f"{ap.gate_refused_after_approval}), rejected {ap.rejected} ({reasons}), expired {ap.expired}; "
              f"still open {ap.open}", ""]
        if ap.counterfactual:
            L += ["What the signals would have done in the shadow book (gross, gaps filled at the stop: optimistic):", "",
                  "| outcome | proposals | matched | still open | no shadow trade | net R | mean R | wins | money "
                  "(R x card risk) | reading |", "|" + " --- |" * 10]
            for s in ap.counterfactual:
                L.append(f"| {s.outcome} | {s.proposals} | {s.matched} | {s.still_open} | {s.unmatched} | "
                         f"{_r(s.net_r)} | {_r(s.mean_r)} | {s.wins} | {_money(s.money, s.currency)} | {s.verdict} |")
            L.append("")
        k = a.costs
        L += ["### Costs (realised against modelled)", "",
              f"- commission {_money(k.commission_total, c, sign=False)} on {k.commission_trades} of {o.trades} trades; swap "
              f"{_money(k.swap_total, c)} on {k.swap_trades}; mean cost {_r(k.cost_r_mean)} per trade over "
              f"{k.cost_r_trades}",
              f"- modelled commission {_money(k.modelled_commission_total, 'USD', sign=False)}; realised / modelled "
              + (f"{k.commission_vs_model:.2f}" if k.commission_vs_model is not None else (k.commission_note or "-"))]
        if k.slippage:
            L += ["", "| session | fills | realised slippage $/oz | modelled $/oz | model from | sample |",
                  "|" + " --- |" * 6]
            for sl in k.slippage:
                mod = f"{sl.modelled_usd:.3f}" if sl.modelled_usd is not None else NOT_RECORDED
                real = f"{sl.realised_mean_usd:+.3f}" if sl.realised_mean_usd is not None else NOT_RECORDED
                L.append(f"| {_label('session', sl.session)} | {sl.fills} | {real} | {mod} | "
                         f"{sl.modelled_from or '-'} | {'ok' if sl.sufficient else f'insufficient (< {MIN_TRADES})'} |")
        else:
            L.append(f"- slippage {NOT_RECORDED} (needs fills under --data-root)")
        L.append("")
        dd = a.drawdown
        abs_txt = _money(dd.daily_loss_limit_abs, c, sign=False) if dd.daily_loss_limit_abs is not None else "off"
        day_txt = (f"{dd.worst_day}: {_money(-dd.worst_day_loss, c)} ({_pct(dd.worst_day_pct)})"
                   if dd.worst_day is not None and dd.worst_day_loss is not None else "none: no losing day")
        week_txt = (f"{dd.worst_week}: {_money(-dd.worst_week_loss, c)} ({_pct(dd.worst_week_pct)})"
                    if dd.worst_week is not None and dd.worst_week_loss is not None else "none: no losing week")
        L += ["### Drawdown against the limits", "",
              f"- closed-trade drawdown {_pct(dd.max_drawdown_pct)} (size-down {dd.dd_stage1_pct:.0f}%, halt "
              f"{dd.dd_stage2_pct:.0f}%)",
              f"- worst day {day_txt}; daily cap {dd.daily_cap_pct:.1f}%, absolute {abs_txt}; days at the daily "
              f"limit: {dd.days_at_daily_limit}",
              f"- worst week {week_txt}; weekly cap {dd.weekly_cap_pct:.1f}%",
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goldbot.ops.run perf-report", description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--state", default="state", help="state folder (read only; a copy works)")
    ap.add_argument("--data-root", help="Parquet store for bars, fills and the journal (read only; optional)")
    ap.add_argument("--from", dest="start", help="first opportunity day YYYY-MM-DD (default: the month's first)")
    ap.add_argument("--to", dest="end", help="last opportunity day YYYY-MM-DD (default: today)")
    ap.add_argument("--account", help="one account only")
    ap.add_argument("--out", default="reports", help="folder for the markdown and JSON (not inside the state folder)")
    ap.add_argument("--json", action="store_true", help="print the JSON instead of the markdown")
    a = ap.parse_args(argv)
    now = pd.Timestamp.now(tz="UTC")
    start, end = a.start or f"{now:%Y-%m-01}", a.end or opp_day(now)
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
    rep = build_performance_report(state, start=start, end=end, data_root=a.data_root, account=a.account, now=now)
    md, js = format_markdown(rep), rep.model_dump_json(indent=2)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"perf_{start}_{end}.md").write_text(md, encoding="utf-8")
    (out / f"perf_{start}_{end}.json").write_text(js + "\n", encoding="utf-8")
    print(js if a.json else md)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
