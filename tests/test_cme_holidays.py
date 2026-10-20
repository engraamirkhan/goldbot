"""CME metals holiday closures (incident 2026-10-19: the 1h and 4h windows refused every entry with `history_gap`
because the static session table called the Labor Day 2026 afternoon halt open).

The expected dates below are a hand-reviewed list, not computed with the module's rules: each 2016-2026 date was
checked against the holes the Dukascopy release store on the brain shows (census of 2026-10-20: every US-holiday hole
over 60 open minutes in 2016-01..2026-10 is one of these dates, and none of these dates trades normally); 2010-2015 and
2027 follow CME's published holiday rules (New Year on a Saturday is not observed, Christmas on a Saturday closes the
Friday, Juneteenth from 2022)."""
from datetime import date

import pandas as pd
import pytest
import yaml

from goldbot.config import ROOT
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.cme_holidays import cme_metals_closed, cme_metals_holidays

FULL = {   # the CME trading date is closed: from the previous day's 16:00 CT break to 17:00 CT on the date
    2010: ["01-01", "04-02", "12-24"], 2011: ["04-22", "12-26"], 2012: ["01-02", "04-06", "12-25"],
    2013: ["01-01", "03-29", "12-25"], 2014: ["01-01", "04-18", "12-25"], 2015: ["01-01", "04-03", "12-25"],
    2016: ["01-01", "03-25", "12-26"], 2017: ["01-02", "04-14", "12-25"], 2018: ["01-01", "03-30", "12-25"],
    2019: ["01-01", "04-19", "12-25"], 2020: ["01-01", "04-10", "12-25"], 2021: ["01-01", "04-02", "12-24"],
    2022: ["04-15", "12-26"], 2023: ["01-02", "04-07", "12-25"], 2024: ["01-01", "03-29", "12-25"],
    2025: ["01-01", "04-18", "12-25"], 2026: ["01-01", "04-03", "12-25"], 2027: ["01-01", "03-26", "12-24"],
}
EARLY = {  # halted from 12:00 CT (the earliest halt seen) to the 17:00 CT reopen
    2010: ["01-18", "02-15", "05-31", "07-05", "09-06", "11-25", "11-26"],
    2011: ["01-17", "02-21", "05-30", "07-04", "09-05", "11-24", "11-25"],
    2012: ["01-16", "02-20", "05-28", "07-04", "09-03", "11-22", "11-23", "12-24"],
    2013: ["01-21", "02-18", "05-27", "07-04", "09-02", "11-28", "11-29", "12-24"],
    2014: ["01-20", "02-17", "05-26", "07-04", "09-01", "11-27", "11-28", "12-24"],
    2015: ["01-19", "02-16", "05-25", "07-03", "09-07", "11-26", "11-27", "12-24"],
    2016: ["01-18", "02-15", "05-30", "07-04", "09-05", "11-24", "11-25"],
    2017: ["01-16", "02-20", "05-29", "07-04", "09-04", "11-23", "11-24"],
    2018: ["01-15", "02-19", "05-28", "07-04", "09-03", "11-22", "11-23", "12-24"],
    2019: ["01-21", "02-18", "05-27", "07-04", "09-02", "11-28", "11-29", "12-24"],
    2020: ["01-20", "02-17", "05-25", "07-03", "09-07", "11-26", "11-27", "12-24"],
    2021: ["01-18", "02-15", "05-31", "07-05", "09-06", "11-25", "11-26"],
    2022: ["01-17", "02-21", "05-30", "06-20", "07-04", "09-05", "11-24", "11-25"],
    2023: ["01-16", "02-20", "05-29", "06-19", "07-04", "09-04", "11-23", "11-24"],
    2024: ["01-15", "02-19", "05-27", "06-19", "07-04", "09-02", "11-28", "11-29", "12-24"],
    2025: ["01-20", "02-17", "05-26", "06-19", "07-04", "09-01", "11-27", "11-28", "12-24"],
    2026: ["01-19", "02-16", "05-25", "06-19", "07-03", "09-07", "11-26", "11-27", "12-24"],
    2027: ["01-18", "02-15", "05-31", "06-18", "07-05", "09-06", "11-25", "11-26"],
}


@pytest.mark.parametrize("year", range(2010, 2028))
def test_the_closure_dates_match_the_hand_reviewed_list(year):
    got = cme_metals_holidays(year)
    assert sorted(d.isoformat()[5:] for d, k in got.items() if k.kind == "full") == FULL[year]
    assert sorted(d.isoformat()[5:] for d, k in got.items() if k.kind == "early") == EARLY[year]


def test_every_closure_is_on_the_owners_thin_liquidity_list_where_it_overlaps():
    """config/holidays_cme_metals.yaml (RiskGate's holiday window) is a superset: a day the exchange closes or halts
    early is a thin day. A closure missing there means one of the two lists is wrong."""
    doc = yaml.safe_load((ROOT / "config/holidays_cme_metals.yaml").read_text())
    listed = {pd.Timestamp(d).date() for d in doc["days"]}
    for y in (2026, 2027):
        assert set(cme_metals_holidays(y)) <= listed, y


def _utc(s: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(s, tz="UTC")])


def test_labor_day_2026_is_closed_from_the_halt_to_the_evening_reopen_only():
    assert not cme_metals_closed(_utc("2026-09-07 16:59"))[0]       # 11:59 CT: still trading
    assert cme_metals_closed(_utc("2026-09-07 17:00"))[0]           # 12:00 CT halt
    assert cme_metals_closed(_utc("2026-09-07 21:59"))[0]
    assert not cme_metals_closed(_utc("2026-09-07 22:00"))[0]       # 17:00 CT: Tuesday's session opens
    assert not cme_metals_closed(_utc("2026-09-08 17:00"))[0]       # an ordinary day


def test_a_full_closure_runs_from_the_previous_break_to_the_evening_reopen():
    # Good Friday 2026-04-03: Thursday's 16:00 CT break (21:00 UTC, CDT) to Friday 17:00 CT
    assert not cme_metals_closed(_utc("2026-04-02 20:59"))[0]
    assert cme_metals_closed(_utc("2026-04-02 21:00"))[0]
    assert cme_metals_closed(_utc("2026-04-03 14:00"))[0]
    # Christmas 2025 on a Thursday: the 24th halts at 12:00 CT (18:00 UTC, CST), closed until 25th 17:00 CT
    assert not cme_metals_closed(_utc("2025-12-24 17:59"))[0]
    assert cme_metals_closed(_utc("2025-12-25 10:00"))[0]
    assert not cme_metals_closed(_utc("2025-12-25 23:00"))[0]


def test_the_session_table_expects_no_bars_in_an_exchange_closure_but_keeps_is_open_unchanged():
    """is_open filters the release bars, the resampler and research (golden files): it must not move. The gap guard
    asks expected_open, which also knows the exchange closures."""
    ts = pd.date_range("2026-09-07 16:00", "2026-09-07 23:00", freq="min", tz="UTC", inclusive="left")
    halt = (ts >= pd.Timestamp("2026-09-07 17:00", tz="UTC")) & (ts < pd.Timestamp("2026-09-07 22:00", tz="UTC"))
    assert (DEFAULT_SESSIONS.expected_open(ts) == (DEFAULT_SESSIONS.is_open(ts) & ~halt)).all()
    assert DEFAULT_SESSIONS.is_open(ts)[halt].sum() > 0          # the static table alone still calls part of it open


def test_closures_are_resolved_per_date_in_chicago_time_across_dst():
    # MLK 2026-01-19 (CST, UTC-6): halt 18:00 UTC; Labor Day 2026 (CDT, UTC-5): halt 17:00 UTC
    assert cme_metals_closed(_utc("2026-01-19 18:00"))[0] and not cme_metals_closed(_utc("2026-01-19 17:59"))[0]
    assert cme_metals_closed(_utc("2026-09-07 17:00"))[0] and not cme_metals_closed(_utc("2026-09-07 16:59"))[0]


def test_an_empty_index_is_handled():
    assert cme_metals_closed(pd.DatetimeIndex([], tz="UTC")).shape == (0,)


def test_kinds_carry_a_name():
    assert cme_metals_holidays(2026)[date(2026, 9, 7)].name == "Labor Day"
