"""The day of the owner's north-star reports (ADR 0004 section 6: "£50 or more on the days the market offers it"), in
one place for the monthly report (monthly_report.py) and the performance report (perf_report.py).

A report day is the opportunity day, D-1 22:00 to D 22:00 UTC (`timeutil.opportunity_day`, the same day the opt-in
`day_type` features and the ex-post opportunity-day classifier use). 22:00 UTC falls inside gold's 17:00-18:00 New York
daily break in both US DST regimes, so a trading session is never split across two days: the Sunday reopen (18:00 New
York, 22:00 or 23:00 UTC) belongs to Monday and the Friday close (17:00 New York, 21:00 or 22:00 UTC) to Friday. A
trade belongs to the day of its exit (perf_report's north-star capture reads its entry instead). Trading days are the
Monday-Friday opportunity days; a close stamped on a weekend day (only at or after a winter Friday's 22:00 UTC close)
still counts in that day's total but not in the number of trading days.

This is not the risk day. The loss caps reset at 00:00 UTC (`timeutil.risk_day`, gate `new_day`), and the reports'
drawdown-against-the-limits figures stay on the risk day. A close between 22:00 and 24:00 UTC is on day D+1 here
and on risk day D. Each report states which day it uses. Reporting only: nothing here changes an order, halt or limit.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timedelta
from typing import TypeVar

import numpy as np
import pandas as pd

from goldbot.data.timeutil import ROLLOVER_UTC_H, opportunity_day

T = TypeVar("T")

# what a "day" is, as the reports print it ("days run ..."): day D is D-1 22:00 to D 22:00 UTC
DAY_BOUNDARY = (f"from {ROLLOVER_UTC_H:02d}:00 UTC the evening before (the opportunity day; the Sunday reopen counts "
                "on Monday)")


def report_day(ts: pd.Timestamp) -> str:
    """The opportunity day ([D-1 22:00, D 22:00) UTC is D) of one tz-aware instant, as YYYY-MM-DD."""
    return f"{opportunity_day(pd.DatetimeIndex([ts]))[0]:%Y-%m-%d}"


def weekdays(first: str, last: str) -> int:
    """Monday-Friday opportunity days from `first` to `last` (YYYY-MM-DD), both included; 0 when `last` is before."""
    a, b = date.fromisoformat(first), date.fromisoformat(last)
    return int(np.busday_count(a, b + timedelta(days=1))) if b >= a else 0


def month_days(month: str) -> tuple[str, str]:
    """The first and last opportunity day of calendar month `month` (YYYY-MM)."""
    start = pd.Timestamp(f"{month}-01")
    return f"{start:%Y-%m-%d}", f"{start + pd.offsets.MonthEnd(0):%Y-%m-%d}"


def day_buckets(items: Iterable[T], at: Callable[[T], pd.Timestamp]) -> dict[str, list[T]]:
    """`items` grouped by the opportunity day of `at(item)`, keeping their order within a day. The caller chooses the
    rows (net only, account currency only: the reports' P&L basis rules) before it buckets them."""
    out: dict[str, list[T]] = {}
    for x in items:
        out.setdefault(report_day(at(x)), []).append(x)
    return out
