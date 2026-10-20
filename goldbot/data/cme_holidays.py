"""CME Globex metals holiday closures: when the gold market is shut although the static session table calls it open.

Incident 2026-10-19: the release feed stops at 18:28 UTC on Labor Day 2026 (CME metals halted early) and resumes at
the 17:00 CT reopen; the static table (goldbot/data/calendar.py) knows only the daily break and the weekend, so the
engine's history guard counted ~150 "open" minutes missing and refused every 1h and 4h entry for weeks. Spot gold
(Dukascopy, the broker CFDs) stops with COMEX: no liquidity provider quotes through a CME holiday.

Dates come from pandas' holiday rules (pandas.tseries.holiday: the US federal rules, Easter and the observance
shifts are maintained there) with CME's own deviations: New Year's Day on a Saturday is not observed (CME traded
2021-12-31), Juneteenth only from 2022 (CME traded 2021-06-18), Columbus and Veterans Day trade normally, the day
after Thanksgiving and Christmas Eve (Mon-Thu) halt early. Hours, on Chicago wall clocks per date (DST-safe):
* full closure (New Year, Good Friday, Christmas; observed dates): the trading date is not held, i.e. closed from the
  previous day's 16:00 CT daily break to 17:00 CT on the date;
* early halt (MLK, Presidents, Memorial, Juneteenth, Independence, Labor Day, Thanksgiving, the day after, 24 Dec):
  closed from 12:00 CT to the 17:00 CT reopen. CME's real halt moved (12:00 CT up to 2021, 12:45 CT on half days,
  13:30 CT on most holidays since 2022); 12:00 CT is the earliest halt the 2016-2026 release shows, so a recent
  holiday's 12:00-13:30 CT counts as closed. Trade-off: a feed hole of up to 90 min in that slice of a holiday
  afternoon goes unseen by the gap guard, against weeks of refused entries when the table is too narrow.
Checked against the release store (2016-2026): every US-holiday hole is one of these closures (tests/test_cme_holidays.py
keeps the hand-reviewed list 2010-2027). An unscheduled closure (a national day of mourning, an outage) is not here:
the guard then refuses until the window passes, the safe direction. Used only to judge missing data (the gap guard),
never to filter bars: `SessionTable.is_open` and every release, resample and research result are unchanged.
"""
from __future__ import annotations

from datetime import date, time, timedelta
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pandas.tseries.holiday import (
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
    sunday_to_monday,
)
from pandas.tseries.offsets import DateOffset, Day

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import epoch_ns

CHICAGO = ZoneInfo("America/Chicago")
BREAK = time(16, 0)          # CME Globex daily break 16:00-17:00 CT
REOPEN = time(17, 0)
EARLY_HALT = time(12, 0)     # earliest holiday halt seen in the 2016-2026 release (see the module docstring)

Kind = Literal["full", "early"]


class Closure(FrozenRecord):
    name: str
    kind: Kind


def _sat_none_sun_mon(d: pd.Timestamp) -> pd.Timestamp | None:
    """New Year's Day: CME does not move a Saturday holiday to the Friday (it traded 2010-12-31 and 2021-12-31)."""
    return None if d.weekday() == 5 else pd.Timestamp(sunday_to_monday(d))


_RULES: tuple[tuple[Holiday, Kind], ...] = (
    (Holiday("New Year's Day", month=1, day=1), "full"),           # observance applied in cme_metals_holidays
    (GoodFriday, "full"),
    (Holiday("Christmas Day", month=12, day=25, observance=nearest_workday), "full"),
    (USMartinLutherKingJr, "early"),
    (USPresidentsDay, "early"),
    (USMemorialDay, "early"),
    (Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday), "early"),
    (Holiday("Independence Day", month=7, day=4, observance=nearest_workday), "early"),
    (USLaborDay, "early"),
    (USThanksgivingDay, "early"),
    (Holiday("Day after Thanksgiving", month=11, day=22,    # 4th Thursday = first on/after 22 Nov
             offset=[DateOffset(weekday=3), Day(1)]), "early"),
    (Holiday("Christmas Eve", month=12, day=24, days_of_week=(0, 1, 2, 3)), "early"),
)


@lru_cache(maxsize=64)
def cme_metals_holidays(year: int) -> dict[date, Closure]:
    """{CME trading date: closure} for one calendar year; a full closure wins over an early halt on the same date."""
    lo, hi = pd.Timestamp(year, 1, 1), pd.Timestamp(year, 12, 31)
    out: dict[date, Closure] = {}
    for rule, kind in _RULES:
        for d in rule.dates(lo, hi):
            when = _sat_none_sun_mon(d) if rule.name == "New Year's Day" else d
            if when is None or when.weekday() >= 5:
                continue
            day = when.date()
            if day not in out or kind == "full":
                out[day] = Closure(name=str(rule.name), kind=kind)
    return dict(sorted(out.items()))


def _chicago_ns(d: date, t: time) -> int:
    return int(pd.Timestamp.combine(d, t).tz_localize(CHICAGO).tz_convert("UTC").value)


@lru_cache(maxsize=64)
def _year_spans(year: int) -> tuple[tuple[int, int], ...]:
    spans = []
    for d, c in cme_metals_holidays(year).items():
        start = _chicago_ns(d - timedelta(days=1), BREAK) if c.kind == "full" else _chicago_ns(d, EARLY_HALT)
        spans.append((start, _chicago_ns(d, REOPEN)))
    return tuple(spans)


def closure_spans(first_year: int, last_year: int) -> list[tuple[int, int]]:
    """[start, end) UTC epoch-ns spans of every closure in the years (inclusive), sorted."""
    return sorted(s for y in range(first_year, last_year + 1) for s in _year_spans(y))


def cme_metals_closed(utc_ts: pd.DatetimeIndex) -> np.ndarray:
    """True for each stamp inside a CME metals holiday closure."""
    ns = epoch_ns(pd.DatetimeIndex(utc_ts))
    out = np.zeros(len(ns), dtype=bool)
    if not len(ns):
        return out
    years = pd.DatetimeIndex(utc_ts).year
    for lo, hi in closure_spans(int(years.min()) - 1, int(years.max()) + 1):    # a span can cross 1 January
        out |= (ns >= lo) & (ns < hi)
    return out
