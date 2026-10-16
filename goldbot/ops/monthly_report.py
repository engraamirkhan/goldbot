"""Monthly north-star report (BACKLOG 26, backend; ADR 0004 section 6), from the closed-trade record
(state/closed_trades_*.jsonl) and the engines' live state for the milestone ladder.

    python -m goldbot.ops.run monthly-report [--state DIR] [--month YYYY-MM] [--json]

Day convention (report_days.py, shared with perf_report.py): a day is the opportunity day, D-1 22:00 to D 22:00 UTC,
so the Sunday reopen counts on Monday; a trade belongs to the day of its exit, and the month is that day's month (a
close at 23:30 UTC on the month's last day is the next month's). The month is its calendar days as opportunity days.
This is not the risk day on which the loss caps reset (00:00 UTC, timeutil.risk_day), nor the digest's "yesterday"
(00:00-24:00 UTC): a close between 22:00 and 24:00 UTC is on the next day here. The report states its boundary
(`MonthlyReport.day_boundary`).

P&L basis (finance audit GAP-05, as perf_report.py): every money, R, return, drawdown and day figure uses net rows
only, a row with `pnl_basis` "net" or a legacy row (no `pnl_basis`) that records both commission and swap
(gates_phase.pnl_basis). Every other row is counted in the trades and shown apart as "N trades gross only (costs not
recorded)" with its gross money and R, left out of every net figure: nothing gross is labelled net.

Per account, for one calendar month of opportunity days (default the one holding now):
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
* net P&L and the average per trading day (Monday-Friday opportunity days of the month elapsed so far, today's
  included, traded or not; trades closed after the report's day are left out, so the digest's "so far", built at
  `now - 1 day`, runs to yesterday's 22:00 UTC roll);
* the three best days by net P&L (with their R), and the number of days at £50 or more on a GBP account;
* the milestone ladder at the engine's live equity (goldbot/ops/account_view.py);
* median MAE and MFE in R (BACKLOG 23: how far trades went against and for the position before the exit, in R of
  the initial risk from price) over the exact records; records partly filled from stored bars (a restart, a close
  seen late: `excursion_approx`) get their own median and count and never enter the exact one. A scaled position's
  figures cover the whole position until its final exit.

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
from goldbot.ops.gates_phase import ClosedTrade, load_closed_trades, pnl_basis
from goldbot.ops.report_days import DAY_BOUNDARY, day_buckets, month_days, report_day, weekdays

NORTH_STAR_DAY_GBP = 50.0         # ADR 0004 section 6: days at £50+ are counted, never aimed at
BEST_DAYS = 3
R_NOTE = "R understated on non-USD accounts until BACKLOG 28"
SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


class DayPnl(FrozenRecord):
    day: str                       # YYYY-MM-DD (opportunity day of the exit, report_days.report_day)
    pnl: float
    r: float | None = None         # sum of the day's recorded R
    trades: int


class AccountMonth(FrozenRecord):
    account_id: str
    currency: str | None = None    # from the engine's state; None when no engine reports it
    trades: int                    # every closed trade, whatever its P&L basis
    net_trades: int = 0            # net rows (gates_phase.pnl_basis): the only rows in the money, R, return and days
    twr_pct: float
    pnl: float
    net_r: float | None = None
    r_per_trade: float | None = None
    r_trades: int = 0              # trades with a recorded R
    r_ci90: tuple[float, float] | None = None
    max_drawdown_pct: float
    trading_days: int
    avg_per_day: float | None = None    # None when no trading day has elapsed (shown "n/a", never £0)
    best_days: list[DayPnl]
    days_50_plus: int | None = None    # GBP accounts only
    equity_now: float | None = None
    milestones: Milestones | None = None
    r_note: str | None = None          # why the R figures are not to be trusted yet (non-USD account, BACKLOG 28)
    r_flagged: int = 0                 # trades whose R is understated (recorded before the currency fix)
    overlapped: bool = False           # positions overlapped: the chained return is approximate
    # rows whose P&L is in another currency than the account's (the unconverted USD fallback, BACKLOG 28): counted as
    # trades and in R, never in the money, the return or the days; the note says how many ("1 in USD not summed")
    pnl_excluded: int = 0
    pnl_note: str | None = None
    mae_r_median: float | None = None  # median MAE in R (<= 0) over the exact records (BACKLOG 23)
    mfe_r_median: float | None = None
    excursion_trades: int = 0          # exact records with an MAE / MFE in R
    approx_mae_r_median: float | None = None   # the same over the approximate records only (excursion_approx)
    approx_mfe_r_median: float | None = None
    excursion_approx: int = 0          # approximate records with an MAE / MFE in R
    # rows that are not net (pnl_basis "gross", or a legacy row without both commission and swap): counted in the
    # trades, shown apart with their gross money (account-currency rows only) and R, never in a net figure
    gross_only: int = 0
    gross_pnl: float | None = None
    gross_r: float | None = None
    gross_note: str | None = None      # "N trades gross only (costs not recorded)"


class MonthlyReport(FrozenRecord):
    month: str                     # YYYY-MM
    as_of_utc: str
    day_boundary: str = DAY_BOUNDARY   # what a "day" is in this report (module docstring: day convention)
    accounts: list[AccountMonth]
    errors: list[str] = []


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


def account_month(account: str, trades: list[ClosedTrade], first: str, last: str,
                  engine: dict[str, Any]) -> AccountMonth:
    """One account's month: `trades` already selected by their exit's opportunity day, `first`..`last` (YYYY-MM-DD,
    inclusive) the opportunity days elapsed, for the trading-day count."""
    trades = sorted(trades, key=lambda t: t.exit_utc)
    ccy = engine.get("currency") if isinstance(engine.get("currency"), str) else None
    # money in the account's currency only (BACKLOG 28, as the digest): a row whose pnl_currency differs (the
    # unconverted USD fallback) stays out of the P&L, the chained return, the days and the drawdown; R is currency-free
    acct = (ccy or "").upper()

    def same(t: ClosedTrade) -> bool:
        return t.pnl_currency is None or t.pnl_currency.upper() == acct

    net = [t for t in trades if pnl_basis(t) == "net"]
    gross = [t for t in trades if pnl_basis(t) != "net"]
    money_rows = [t for t in net if same(t)]
    left: dict[str, int] = {}
    for t in net:
        if not same(t):
            left[str(t.pnl_currency).upper()] = left.get(str(t.pnl_currency).upper(), 0) + 1
    pnl_note = ", ".join(f"{n} in {c} not summed" for c, n in sorted(left.items())) or None
    idx, peak, dd = 1.0, 1.0, 0.0
    for t in money_rows:
        idx *= 1 + t.pnl / t.equity_before
        peak = max(peak, idx)
        dd = max(dd, 1 - idx / peak)
    rs = [t.r for t in net if t.r is not None]
    days = day_buckets(money_rows, lambda t: t.exit_utc)
    per_day = [DayPnl(day=d, pnl=sum(t.pnl for t in ts), trades=len(ts),
                      r=sum(t.r for t in ts if t.r is not None) if any(t.r is not None for t in ts) else None)
               for d, ts in days.items()]
    best = sorted(per_day, key=lambda d: d.pnl, reverse=True)[:BEST_DAYS]
    n_days = weekdays(first, last)
    pnl = sum(t.pnl for t in money_rows)
    eq = engine.get("equity")
    flagged = sum(r_understated(t, ccy) for t in net)
    gross_money = [t.pnl for t in gross if same(t)]
    gross_rs = [t.r for t in gross if t.r is not None]
    overlapped = any(b.entry_utc is not None and b.entry_utc < a.exit_utc for a, b in zip(trades, trades[1:]))
    exc = [t for t in trades if t.mae_r is not None and t.mfe_r is not None]
    exact = [t for t in exc if not t.excursion_approx]
    approx_exc = [t for t in exc if t.excursion_approx]

    def med(rows: list[ClosedTrade], field: str) -> float | None:
        return float(np.median([getattr(t, field) for t in rows])) if rows else None
    return AccountMonth(
        account_id=account, currency=ccy, trades=len(trades), net_trades=len(net), twr_pct=100 * (idx - 1), pnl=pnl,
        net_r=sum(rs) if rs else None, r_per_trade=float(np.mean(rs)) if rs else None, r_trades=len(rs),
        r_ci90=_ci90(rs), max_drawdown_pct=100 * dd, trading_days=n_days, avg_per_day=pnl / n_days if n_days else None,
        best_days=best,
        days_50_plus=sum(d.pnl >= NORTH_STAR_DAY_GBP for d in per_day) if ccy == "GBP" else None,
        equity_now=float(eq) if isinstance(eq, (int, float)) else None, milestones=milestones(engine),
        r_note=(R_NOTE if flagged == len(rs) else f"{R_NOTE} ({flagged} of {len(rs)} trades)") if flagged else None,
        r_flagged=flagged, overlapped=overlapped, pnl_excluded=sum(left.values()), pnl_note=pnl_note,
        mae_r_median=med(exact, "mae_r"), mfe_r_median=med(exact, "mfe_r"), excursion_trades=len(exact),
        approx_mae_r_median=med(approx_exc, "mae_r"), approx_mfe_r_median=med(approx_exc, "mfe_r"),
        excursion_approx=len(approx_exc), gross_only=len(gross),
        gross_pnl=sum(gross_money) if gross_money else None, gross_r=sum(gross_rs) if gross_rs else None,
        gross_note=f"{_trades(len(gross))} gross only (costs not recorded)" if gross else None)


def _trades(n: int, kind: str = "") -> str:
    return f"{n} {kind}trade{'s' if n != 1 else ''}"


def build_monthly_report(state_dir: str | Path, *, month: str | None = None,
                         now: pd.Timestamp | None = None) -> MonthlyReport:
    sd = Path(state_dir)
    now = pd.Timestamp.now(tz="UTC") if now is None else now.tz_convert("UTC")
    today = report_day(now)
    month = month or today[:7]
    first, end = month_days(month)
    last = min(end, today)                                    # opportunity days elapsed, today included
    trades, err = load_closed_trades(sd)
    by_acc: dict[str, list[ClosedTrade]] = {}
    for t in trades:
        if first <= report_day(t.exit_utc) <= last:
            by_acc.setdefault(t.account_id, []).append(t)
    rows = [account_month(a, ts, first, last, _engine(sd, a)) for a, ts in sorted(by_acc.items())]
    return MonthlyReport(month=month, as_of_utc=now.isoformat(), accounts=rows, errors=[err] if err else [])


# ---------------------------------------------------------------------------------------------- text
def money(x: float, ccy: str | None, *, sign: bool = True) -> str:
    s = "+" if x >= 0 else "-"
    sym = SYMBOLS.get(ccy or "")
    body = f"{sym}{abs(x):,.2f}" if sym else f"{abs(x):,.2f}" + (f" {ccy}" if ccy else "")
    return (s if sign else ("" if x >= 0 else "-")) + body


def _avg(x: float | None, ccy: str | None) -> str:
    """The average per trading day, "n/a" before the month's first weekday (a £0 would read as a flat day)."""
    return "n/a" if x is None else money(x, ccy)


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
    lines = [f"goldbot monthly report · {name} · as of {rep.as_of_utc[:16].replace('T', ' ')} UTC",
             f"Net of commission and swap; days run {rep.day_boundary}"]
    if not rep.accounts:
        lines.append("No closed trades this month.")
    for a in rep.accounts:
        c = a.currency
        lines.append("")
        lines.append(f"{a.account_id} ({c or 'currency not reported'})")
        approx = " · approximate: positions overlapped" if a.overlapped else ""
        lines.append(f"  Return {a.twr_pct:+.2f}% (chained closed-trade return, time-weighted; deposits excluded"
                     f"{approx}) · net {money(a.pnl, c)}{f' ({a.pnl_note})' if a.pnl_note else ''} over "
                     f"{_trades(a.net_trades, 'net ')} of {a.trades}")
        if a.net_r is not None:
            ci = f", 90% t-interval (approx.) {a.r_ci90[0]:+.2f} to {a.r_ci90[1]:+.2f}" if a.r_ci90 else ""
            note = f" ({a.r_note})" if a.r_note else ""
            lines.append(f"  R {a.net_r:+.2f} net · {a.r_per_trade:+.2f} R/trade over {a.r_trades} with R{ci}{note}")
        else:
            lines.append("  R: not recorded")
        parts = []
        if a.mae_r_median is not None and a.mfe_r_median is not None:
            parts.append(f"median MAE {a.mae_r_median:+.2f}R · median MFE {a.mfe_r_median:+.2f}R over "
                         f"{a.excursion_trades} trade{'s' if a.excursion_trades != 1 else ''}")
        if a.approx_mae_r_median is not None and a.approx_mfe_r_median is not None:
            parts.append(f"approximate (restart): median MAE {a.approx_mae_r_median:+.2f}R · median MFE "
                         f"{a.approx_mfe_r_median:+.2f}R over {a.excursion_approx}")
        if parts:
            lines.append("  Excursion: " + "; ".join(parts) + " (whole position until the final exit)")
        lines.append(f"  Max drawdown on closed trades this month {a.max_drawdown_pct:.2f}% · average "
                     f"{_avg(a.avg_per_day, c)} per trading day over {a.trading_days} weekdays")
        best = " · ".join(f"{pd.Timestamp(d.day):%a %d %b} {money(d.pnl, c)}"
                          + (f" ({d.r:+.1f}R)" if d.r is not None else "") for d in a.best_days)
        lines.append(f"  Best days: {best}")
        if a.days_50_plus is not None:
            lines.append(f"  Days at £50 or more: {a.days_50_plus}")
        if a.gross_note:
            figs = ([money(a.gross_pnl, c)] if a.gross_pnl is not None else []) + \
                ([f"{a.gross_r:+.2f}R"] if a.gross_r is not None else [])
            lines.append(f"  {a.gross_note}: {' · '.join(figs) + ' gross, ' if figs else ''}"
                         "left out of every figure above")
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
        n = _trades(a.net_trades, "net ") if a.gross_only else _trades(a.trades)
        parts.append(f"{who}{a.twr_pct:+.2f}% time-weighted · {n}{r} · "
                     f"avg {_avg(a.avg_per_day, a.currency)}/trading day"
                     + (f" ({a.pnl_note})" if a.pnl_note else "")
                     + (f" ({a.gross_note}, not summed)" if a.gross_note else ""))
    return [f"• {name} so far: " + " | ".join(parts)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goldbot.ops.run monthly-report", description=__doc__.splitlines()[0])
    ap.add_argument("--state", default="state", help="state directory (closed_trades_*.jsonl, engine_*.json)")
    ap.add_argument("--month", help="YYYY-MM (its opportunity days); default the month of today's opportunity day")
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
