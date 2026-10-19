"""The Home trades summary (owner request 2026-10-19: "a summary of all types of trades"): `GET /api/trades/overview`.
Nothing here writes or decides.

It counts the trades the activity feed reads (api/activity.py `read_trades`, one parse for both), per type:
* shadow_taken: shadow trades that cleared their threshold; shadow_all: those plus the counterfactual candidates;
* demo / live: the accounts' positions by mode (an open position whose engine state is missing has no mode and counts
  as demo: live needs the recorded paper->tiny-live gate and the owner's unlock, and its engine writes its state);
* probe: cost-probe orders (demo).

Periods are opportunity days (ops/report_days.py, D-1 22:00 to D 22:00 UTC): today, the last 7 days to today, and all
time. A closed trade belongs to the period of its close; "open" is the trades open now, the same in every period.

Design rules kept: money from net-of-cost rows only and by currency, never summed across currencies ("nothing gross
is labelled net", gates_phase.pnl_basis); R where the record has one (a shadow trade's R is gross of costs, from its
return); no account names at all. A source that cannot be read is named in `unreadable` and its trades are missing,
not zero; the others still count.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, get_args

import pandas as pd

from goldbot.api.activity import Trade, read_trades
from goldbot.api.schema import (
    ActivityEvent,
    ExitCounts,
    TradePeriod,
    TradeRef,
    TradesOverview,
    TradesPeriodView,
    TradeStats,
    TradeType,
    TradeTypeSummary,
)
from goldbot.data.timeutil import ROLLOVER_UTC_H
from goldbot.ops.report_days import report_day
from goldbot.research.attribution import family_of

__all__ = ["OVERVIEW_TTL_S", "PERIOD_DAYS", "trades_overview"]

OVERVIEW_TTL_S = 15.0               # one build serves every poll for 15 s (the Home section polls every 30 s)
PERIOD_DAYS: dict[TradePeriod, int | None] = {"today": 1, "7d": 7, "all": None}   # opportunity days, today included
UNKNOWN = "unknown"                 # the breakdown key of a trade without a timeframe or agent (cost probes: timeframe)


def _type_of(t: Trade) -> list[TradeType]:
    """The summary types a trade counts in (a taken shadow trade is in both shadow types)."""
    if t.source == "shadow":
        return ["shadow_taken", "shadow_all"] if t.taken else ["shadow_all"]
    if t.source == "probes":
        return ["probe"]
    e = t.closed or t.opened
    return ["live"] if e is not None and e.mode == "live" else ["demo"]


def _result(e: ActivityEvent) -> float | None:
    """The sign of a trade: R, else P&L, else return."""
    return e.r if e.r is not None else e.pnl if e.pnl is not None else e.ret


def _exit_counts(closed: Iterable[ActivityEvent]) -> ExitCounts:
    """target | stop | time by the reason's first word (the engine's "time_exit" is time, its "engine_stop_close" and
    the policy's "trail" and "flat" are other)."""
    c = ExitCounts()
    for e in closed:
        reason = (e.reason or "").lower()
        key = next((k for k in ("target", "stop", "time") if reason.startswith(k)), "other")
        setattr(c, key, getattr(c, key) + 1)
    return c


def _ref(e: ActivityEvent) -> TradeRef:
    return TradeRef(ts=e.ts, agent_id=e.agent_id, timeframe=e.timeframe, r=e.r, pnl=e.pnl, currency=e.currency)


def _extremes(closed: list[ActivityEvent]) -> tuple[TradeRef | None, TradeRef | None]:
    """Best and worst by R when any trade has one, else by P&L."""
    ranked = [(e.r, e) for e in closed if e.r is not None] or [(e.pnl, e) for e in closed if e.pnl is not None]
    if not ranked:
        return None, None
    return _ref(max(ranked, key=lambda x: x[0])[1]), _ref(min(ranked, key=lambda x: x[0])[1])


def _stats(open_now: list[Trade], closed: list[Trade]) -> TradeStats:
    ev = [t.closed for t in closed if t.closed is not None]
    results = [_result(e) for e in ev]
    wins = sum(x is not None and x > 0 for x in results)
    rs = [e.r for e in ev if e.r is not None]
    net: dict[str, float] = {}
    for t in closed:
        if t.net and t.closed is not None and t.closed.pnl is not None:
            ccy = t.closed.currency or UNKNOWN
            net[ccy] = net.get(ccy, 0.0) + t.closed.pnl
    best, worst = _extremes(ev)
    return TradeStats(
        open=len(open_now), closed=len(ev), wins=wins, losses=sum(x is not None and x < 0 for x in results),
        win_rate=round(wins / len(ev), 4) if ev else None, n_r=len(rs),
        total_r=round(sum(rs), 3) if rs else None, avg_r=round(sum(rs) / len(rs), 3) if rs else None,
        net_pnl={k: round(v, 2) for k, v in sorted(net.items())}, best=best, worst=worst, exits=_exit_counts(ev))


def _by(key: Callable[[ActivityEvent], str], open_now: list[Trade], closed: list[Trade]) -> dict[str, TradeStats]:
    def k(t: Trade) -> str:
        e = t.closed or t.opened
        return key(e) if e is not None else UNKNOWN
    keys = sorted({k(t) for t in [*open_now, *closed]})
    return {g: _stats([t for t in open_now if k(t) == g], [t for t in closed if k(t) == g]) for g in keys}


def _family(e: ActivityEvent) -> str:
    return family_of(e.agent_id) if e.agent_id else UNKNOWN


def _since(now: pd.Timestamp, days: int | None) -> pd.Timestamp | None:
    """The start of the first opportunity day of the last `days` (today included): day D starts D-1 22:00 UTC."""
    if days is None:
        return None
    first = date.fromisoformat(report_day(now)) - timedelta(days=days - 1)
    return pd.Timestamp(first, tz="UTC") - pd.Timedelta(hours=24 - ROLLOVER_UTC_H)


def trades_overview(state_dir: str | Path, now: pd.Timestamp,
                    engines: list[dict[str, Any]] | None = None) -> TradesOverview:
    """Every trade type for today, 7 days and all time (every type listed, empty ones as zeros)."""
    trades, unreadable = read_trades(state_dir, engines)
    by_type: dict[TradeType, list[Trade]] = {k: [] for k in get_args(TradeType)}
    for t in trades:
        for k in _type_of(t):
            by_type[k].append(t)
    periods = []
    for period, days in PERIOD_DAYS.items():
        since = _since(now, days)
        types = []
        for kind, ts in by_type.items():
            open_now = [t for t in ts if t.closed is None]
            closed = [t for t in ts if t.closed is not None and (since is None or pd.Timestamp(t.closed.ts) >= since)]
            types.append(TradeTypeSummary(type=kind, stats=_stats(open_now, closed),
                                          by_family=_by(_family, open_now, closed),
                                          by_timeframe=_by(lambda e: e.timeframe or UNKNOWN, open_now, closed)))
        periods.append(TradesPeriodView(period=period, since=since, types=types))
    return TradesOverview(as_of=now, periods=periods, unreadable=unreadable)
