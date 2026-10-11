"""Monthly north-star report (BACKLOG 26, backend; ADR 0004 section 6), from the closed-trade record
(state/closed_trades_*.jsonl, net of every cost) and the engines' live state for the milestone ladder.

    python -m goldbot.ops.run monthly-report [--state DIR] [--month YYYY-MM] [--json]

Per account, for one calendar month (UTC; default the current one):
* % return as a chained closed-trade return (time-weighted; deposits excluded): the product of
  (1 + pnl / equity before the trade) over the month's trades, minus 1. Each trade's return is measured on the
  balance just before it, so a deposit or withdrawal between trades moves no return (ADR 0004). When positions
  overlapped in time the chain (in exit order) is approximate, and the report says "approximate: positions
  overlapped";
* net R, R per trade with the trade count and a 90% Student-t interval (approximate: R is rarely normal) (trades with a recorded R only). R is as the
  engine recorded it: on a non-USD account it is understated (the risk is in USD, the P&L in the account currency;
  ADR 0004 audit A5) for every trade recorded before BACKLOG 28 (records without `pnl_currency`), and the report says
  so beside R, with the count when only some trades are affected;
* max drawdown % on closed trades this month (on the closed-trade return index: floating losses inside a trade are
  not seen);
* net P&L and the average per trading day (Monday-Friday days of the month elapsed so far, traded or not; trades
  closed after the report's day are left out, so the digest's "so far" is up to yesterday);
* the three best days by net P&L (with their R), and the number of days at £50 or more on a GBP account;
* the milestone ladder at the engine's live equity (goldbot/ops/account_view.py).

The report describes; it never sets a day's amount to reach (ADR 0004: a missed £50 day never prompts more risk), so
the wording has no per-day aim, countdown or shortfall. Reporting only: nothing here changes an order, halt or setting.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.ops.account_view import Milestones, milestones
from goldbot.ops.gates_phase import ClosedTrade, load_closed_trades

NORTH_STAR_DAY_GBP = 50.0         # ADR 0004 section 6: days at £50+ are counted, never aimed at
BEST_DAYS = 3
R_NOTE = "R understated on non-USD accounts until BACKLOG 28"
SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


class DayPnl(FrozenRecord):
    day: str                       # YYYY-MM-DD (UTC exit date)
    pnl: float
    r: float | None = None         # sum of the day's recorded R
    trades: int


class AccountMonth(FrozenRecord):
    account_id: str
    currency: str | None = None    # from the engine's state; None when no engine reports it
    trades: int
    twr_pct: float
    pnl: float
    net_r: float | None = None
    r_per_trade: float | None = None
    r_trades: int = 0              # trades with a recorded R
    r_ci90: tuple[float, float] | None = None
    max_drawdown_pct: float
    trading_days: int
    avg_per_day: float
    best_days: list[DayPnl]
    days_50_plus: int | None = None    # GBP accounts only
    equity_now: float | None = None
    milestones: Milestones | None = None
    r_note: str | None = None          # why the R figures are not to be trusted yet (non-USD account, BACKLOG 28)
    r_flagged: int = 0                 # trades whose R is understated (recorded before the currency fix)
    overlapped: bool = False           # positions overlapped: the chained return is approximate


class MonthlyReport(FrozenRecord):
    month: str                     # YYYY-MM
    as_of_utc: str
    accounts: list[AccountMonth]
    errors: list[str] = []


def _month_bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(f"{month}-01", tz="UTC")
    return start, start + pd.offsets.MonthBegin(1)


def _weekdays(start: pd.Timestamp, end: pd.Timestamp) -> int:
    """Monday-Friday dates in [start, end)."""
    if end <= start:
        return 0
    return int(np.busday_count(start.date(), end.date()))


def _engine(state_dir: Path, account: str) -> dict[str, Any]:
    try:
        e = json.loads((state_dir / f"engine_{account}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return e if isinstance(e, dict) else {}


def _ci90(rs: list[float]) -> tuple[float, float] | None:
    if len(rs) < 2:
        return None
    from scipy import stats
    m, sd = float(np.mean(rs)), float(np.std(rs, ddof=1))
    half = float(stats.t.ppf(0.95, len(rs) - 1)) * sd / math.sqrt(len(rs))
    return (m - half, m + half)


def r_understated(trade: Any, account_ccy: str | None) -> bool:
    """A trade's R is understated when it was recorded on a non-USD (or unknown-currency) account before BACKLOG 28:
    its risk is in USD and its P&L in the account currency (ADR 0004 audit A5). A record made after the fix states its
    P&L currency (`pnl_currency`), so the flag is per trade: trades recorded before the fix keep it even after the
    engine starts publishing `usd_to_account`."""
    if getattr(trade, "r", None) is None or account_ccy == "USD":
        return False
    return getattr(trade, "pnl_currency", None) is None


def account_month(account: str, trades: list[ClosedTrade], start: pd.Timestamp, end: pd.Timestamp,
                  engine: dict[str, Any]) -> AccountMonth:
    trades = sorted(trades, key=lambda t: t.exit_utc)
    idx, peak, dd = 1.0, 1.0, 0.0
    for t in trades:
        idx *= 1 + t.pnl / t.equity_before
        peak = max(peak, idx)
        dd = max(dd, 1 - idx / peak)
    rs = [t.r for t in trades if t.r is not None]
    days: dict[str, list[ClosedTrade]] = {}
    for t in trades:
        days.setdefault(f"{t.exit_utc:%Y-%m-%d}", []).append(t)
    per_day = [DayPnl(day=d, pnl=sum(t.pnl for t in ts), trades=len(ts),
                      r=sum(t.r for t in ts if t.r is not None) if any(t.r is not None for t in ts) else None)
               for d, ts in days.items()]
    best = sorted(per_day, key=lambda d: d.pnl, reverse=True)[:BEST_DAYS]
    ccy = engine.get("currency") if isinstance(engine.get("currency"), str) else None
    n_days = _weekdays(start, end)
    pnl = sum(t.pnl for t in trades)
    eq = engine.get("equity")
    flagged = sum(r_understated(t, ccy) for t in trades)
    overlapped = any(b.entry_utc is not None and b.entry_utc < a.exit_utc for a, b in zip(trades, trades[1:]))
    return AccountMonth(
        account_id=account, currency=ccy, trades=len(trades), twr_pct=100 * (idx - 1), pnl=pnl,
        net_r=sum(rs) if rs else None, r_per_trade=float(np.mean(rs)) if rs else None, r_trades=len(rs),
        r_ci90=_ci90(rs), max_drawdown_pct=100 * dd, trading_days=n_days, avg_per_day=pnl / n_days if n_days else 0.0,
        best_days=best,
        days_50_plus=sum(d.pnl >= NORTH_STAR_DAY_GBP for d in per_day) if ccy == "GBP" else None,
        equity_now=float(eq) if isinstance(eq, (int, float)) else None, milestones=milestones(engine),
        r_note=(R_NOTE if flagged == len(rs) else f"{R_NOTE} ({flagged} of {len(rs)} trades)") if flagged else None,
        r_flagged=flagged, overlapped=overlapped)


def build_monthly_report(state_dir: str | Path, *, month: str | None = None,
                         now: pd.Timestamp | None = None) -> MonthlyReport:
    sd = Path(state_dir)
    now = pd.Timestamp.now(tz="UTC") if now is None else now.tz_convert("UTC")
    month = month or f"{now:%Y-%m}"
    start, end = _month_bounds(month)
    upto = min(end, now.normalize() + pd.Timedelta(days=1))   # weekdays elapsed, today included
    trades, err = load_closed_trades(sd)
    by_acc: dict[str, list[ClosedTrade]] = {}
    for t in trades:
        if start <= t.exit_utc < upto:
            by_acc.setdefault(t.account_id, []).append(t)
    rows = [account_month(a, ts, start, upto, _engine(sd, a)) for a, ts in sorted(by_acc.items())]
    return MonthlyReport(month=month, as_of_utc=now.isoformat(), accounts=rows, errors=[err] if err else [])


# ---------------------------------------------------------------------------------------------- text
def money(x: float, ccy: str | None, *, sign: bool = True) -> str:
    s = "+" if x >= 0 else "-"
    sym = SYMBOLS.get(ccy or "")
    body = f"{sym}{abs(x):,.2f}" if sym else f"{abs(x):,.2f}" + (f" {ccy}" if ccy else "")
    return (s if sign else ("" if x >= 0 else "-")) + body


def _ladder_line(m: Milestones | None, ccy: str | None) -> str:
    if m is None:
        return "Milestones: no engine state yet"
    if m.pending:
        return f"Milestones: {m.pending}"
    now = ", ".join(m.tradeable) if m.tradeable else "none yet"
    if m.next_timeframe is None or m.next_equity is None:
        return f"Milestones: trades {now}; every rung reached"
    return (f"Milestones: trades {now}; next: {m.next_timeframe} at {money(m.next_equity, ccy, sign=False)} "
            f"({100 * (m.progress or 0):.0f}% of the way)")


def format_report(rep: MonthlyReport) -> str:
    name = pd.Timestamp(f"{rep.month}-01").strftime("%B %Y")
    lines = [f"goldbot monthly report · {name} (UTC) · as of {rep.as_of_utc[:16].replace('T', ' ')} UTC"]
    if not rep.accounts:
        lines.append("No closed trades this month.")
    for a in rep.accounts:
        c = a.currency
        lines.append("")
        lines.append(f"{a.account_id} ({c or 'currency not reported'})")
        approx = " · approximate: positions overlapped" if a.overlapped else ""
        lines.append(f"  Return {a.twr_pct:+.2f}% (chained closed-trade return, time-weighted; deposits excluded"
                     f"{approx}) · net {money(a.pnl, c)} over {a.trades} trade{'s' if a.trades != 1 else ''}")
        if a.net_r is not None:
            ci = f", 90% t-interval (approx.) {a.r_ci90[0]:+.2f} to {a.r_ci90[1]:+.2f}" if a.r_ci90 else ""
            note = f" ({a.r_note})" if a.r_note else ""
            lines.append(f"  R {a.net_r:+.2f} net · {a.r_per_trade:+.2f} R/trade over {a.r_trades} with R{ci}{note}")
        else:
            lines.append("  R: not recorded")
        lines.append(f"  Max drawdown on closed trades this month {a.max_drawdown_pct:.2f}% · average {money(a.avg_per_day, c)} "
                     f"per trading day over {a.trading_days} weekdays")
        best = " · ".join(f"{pd.Timestamp(d.day):%a %d %b} {money(d.pnl, c)}"
                          + (f" ({d.r:+.1f}R)" if d.r is not None else "") for d in a.best_days)
        lines.append(f"  Best days: {best}")
        if a.days_50_plus is not None:
            lines.append(f"  Days at £50 or more: {a.days_50_plus}")
        lines.append("  " + _ladder_line(a.milestones, c))
    for e in rep.errors:
        lines.append(f"⚠️ {e}")
    return "\n".join(lines)


def month_to_date_line(state_dir: str | Path, now: pd.Timestamp) -> list[str]:
    """One digest line for the month so far (all accounts), or nothing before the month's first closed trade."""
    rep = build_monthly_report(state_dir, now=now)
    if not rep.accounts:
        return []
    name = pd.Timestamp(f"{rep.month}-01").strftime("%B")
    parts = []
    for a in rep.accounts:
        who = f"{a.account_id} " if len(rep.accounts) > 1 else ""
        r = f" · {a.net_r:+.2f}R" + (f" ({a.r_note})" if a.r_note else "") if a.net_r is not None else ""
        parts.append(f"{who}{a.twr_pct:+.2f}% time-weighted · {a.trades} trade{'s' if a.trades != 1 else ''}{r} · "
                     f"avg {money(a.avg_per_day, a.currency)}/trading day")
    return [f"• {name} so far: " + " | ".join(parts)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goldbot.ops.run monthly-report", description=__doc__.splitlines()[0])
    ap.add_argument("--state", default="state", help="state directory (closed_trades_*.jsonl, engine_*.json)")
    ap.add_argument("--month", help="YYYY-MM (UTC); default the current month")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    if a.month is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", a.month):
        print(f"--month must be YYYY-MM, got {a.month!r}", file=sys.stderr)
        return 2
    rep = build_monthly_report(a.state, month=a.month)
    print(rep.model_dump_json(indent=2) if a.json else format_report(rep))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
