"""BACKLOG 152: exchange holidays come from a published, versioned calendar (config/research/exchange_holidays.yaml),
each date with the instant it became knowable, never from missing data in the long daily series."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data import exchange_holidays as XH
from goldbot.data.timeutil import epoch_ns
from goldbot.research.catalogue import core as K
from goldbot.research.catalogue import detectors_catalogue as DC
from goldbot.research.catalogue import dispatcher as D
from goldbot.research.catalogue.data import DAY, Bars

ROOT = Path(__file__).resolve().parents[1]


def _day(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").value // DAY)


def _ns(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").value)


def test_calendar_is_versioned_and_covers_the_research_windows() -> None:
    cal = XH.load_calendar()
    assert cal.version.startswith("xcals-") and cal.source.startswith("exchange_calendars")
    assert cal.valid_from <= pd.Timestamp("2003-01-01").date() and cal.valid_through >= pd.Timestamp("2025-12-31").date()
    assert len(XH.calendar_sha256()) == 64
    assert set(cal.exchanges) == {"US", "UK", "DE"}


@pytest.mark.parametrize(("ex", "day", "known"), [
    ("US", "2015-12-25", "2014-12-01"),        # Christmas: regular rule, the prior-year floor
    ("US", "2025-01-09", "2025-01-01"),        # Carter day of mourning: the day after NYSE's notice
    ("US", "2001-09-11", "2001-09-12"),        # unannounced: knowable only after the fact
    ("UK", "2022-09-19", "2022-09-11"),        # Queen's funeral: notice 10 Sept 2022
    ("UK", "2011-04-29", "2010-11-24"),        # royal wedding: notice 23 Nov 2010
    ("DE", "2017-10-31", "2016-12-01"),        # Reformation Day 2017: one-off, proclaimed years ahead
])
def test_published_closures_carry_their_availability(ex: str, day: str, known: str) -> None:
    days, av = XH.exchange_holidays(ex)
    k = np.flatnonzero(days == _day(day))
    assert len(k) == 1, f"{ex} {day} missing"
    assert int(av[k[0]]) == _ns(known)


def test_only_weekdays_sorted_unique_and_regular_dates_known_before_the_day() -> None:
    cal = XH.load_calendar()
    for ex in cal.exchanges:
        days, av = XH.exchange_holidays(ex)
        assert (np.diff(days) > 0).all()
        assert (((days + 3) % 7) < 5).all()                  # day 0 = Thursday 1970-01-01
        reg = np.array([r.kind == "regular" for r in cal.exchanges[ex]])
        assert (av[reg] < days[reg] * DAY).all()


def test_a_data_gap_that_is_not_a_holiday_is_not_a_holiday() -> None:
    """2015-03-10 (a Tuesday, NYSE open): the old inference would have called a missing bar a holiday."""
    days, _ = XH.exchange_holidays("US")
    assert _day("2015-03-10") not in set(days.tolist())
    assert _day("2015-12-25") in set(days.tolist())
    assert not hasattr(D, "HOLIDAY_SOURCE")                 # build_ctx no longer reads the long series for holidays


def test_holidays_known_by_cut_at_availability() -> None:
    """The as-of contract: a closure whose notice comes after the instant is not visible at it."""
    d0, _ = XH.exchange_holidays("US", known_by_ns=_ns("2024-12-30"))
    d1, _ = XH.exchange_holidays("US", known_by_ns=_ns("2025-01-02"))
    assert _day("2025-01-09") not in set(d0.tolist()) and _day("2025-01-09") in set(d1.tolist())


def test_validation_rejects_a_weekend_or_unsorted_or_untimed_row(tmp_path: Path) -> None:
    src = (ROOT / "config" / "research" / "exchange_holidays.yaml").read_text()
    bad = src.replace("date: '2015-12-25'", "date: '2015-12-26'")      # a Saturday
    p = tmp_path / "x.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="weekend"):
        XH.load_calendar(p)
    p.write_text(src.replace("available_utc: '2014-12-01T00:00:00Z', name: \"Christmas", "name: \"Christmas", 1))
    with pytest.raises(ValueError):
        XH.load_calendar(p)


def test_holiday_ctx_carries_availability_for_s6() -> None:
    ctx = K.Ctx(window="explore")
    D.add_holidays(ctx, end_ns=_ns("2018-01-01"))
    for ex in ("US", "UK", "DE"):
        ev, av = ctx.events[f"holiday_{ex}"], ctx.avail[f"holiday_{ex}"]
        assert len(ev) == len(av) > 100 and (av < _ns("2018-01-01")).all()


# ------------------------------------------------------------------ S05 reads holidays point in time (seeded release)
def _daily(start: str, n: int) -> Bars:
    t = pd.bdate_range(start, periods=n, tz="UTC")
    ts = epoch_ns(t)
    c = 100 + np.cumsum(np.random.default_rng(3).normal(0, 1, n))
    o = np.r_[c[0], c[:-1]]
    h, low = np.maximum(o, c) + 0.5, np.minimum(o, c) - 0.5
    z = np.zeros(n)
    return Bars("US500", "1d", ts, ts + DAY, o, h, low, c, z, np.zeros(n, bool), np.zeros(n, np.int64), z)


def test_s05_does_not_trade_a_holiday_published_after_the_decision() -> None:
    """Seed a future release: the same holiday known well ahead fires at T-2; published after T-2 it does not."""
    b = _daily("2016-01-04", 120)
    hol = _day("2016-05-30")                                 # a Monday inside the bars (T-2 = Thu 26 May)
    t2 = int(np.flatnonzero((b.t // DAY) == _day("2016-05-26"))[0])
    early = K.Ctx(window="explore", events={"holiday_US": np.array([hol])},
                  avail={"holiday_US": np.array([_ns("2015-12-01")])})
    late = K.Ctx(window="explore", events={"holiday_US": np.array([hol])},
                 avail={"holiday_US": np.array([_ns("2016-05-28")])})
    assert t2 in set(DC.s05_preholiday(b, {}, early).i.tolist())
    assert t2 not in set(DC.s05_preholiday(b, {}, late).i.tolist())
