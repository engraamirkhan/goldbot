"""Session windows follow London and New York daylight saving (playbook gap D-1).

Sessions are defined on local exchange clocks and converted to UTC per date: Asia opens 08:00 Asia/Tokyo (no DST:
23:00 UTC all year), London 08:00 Europe/London, New York 08:30 America/New_York until 17:00 America/New_York. Each
session runs until the next one opens. The US and the UK switch on different dates (2026: US 8 Mar and 1 Nov, UK
29 Mar and 25 Oct), so the weeks in between have London on winter time and New York on summer time (March) or the
reverse (late October).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.execution.costs import spread_table
from goldbot.features import FEATURES, build_features
from goldbot.features.session import f_session
from goldbot.features.trader import SESSION_NAMES, session_instances
from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES

# date: (London open, New York open, New York close) in UTC
CASES = {
    "2026-03-06": ("08:00", "13:30", "22:00"),   # both on winter time (Friday before the US switch)
    "2026-03-08": ("08:00", "12:30", "21:00"),   # day of the US switch (Sunday): New York already on EDT at 08:30
    "2026-03-09": ("08:00", "12:30", "21:00"),   # March gap week: London winter, New York summer
    "2026-03-27": ("08:00", "12:30", "21:00"),   # March gap week, last weekday
    "2026-03-29": ("07:00", "12:30", "21:00"),   # day of the UK switch (Sunday): London on BST at 08:00
    "2026-03-30": ("07:00", "12:30", "21:00"),   # both on summer time
    "2026-07-15": ("07:00", "12:30", "21:00"),   # mid summer
    "2026-10-23": ("07:00", "12:30", "21:00"),   # both on summer time (Friday before the UK switch)
    "2026-10-25": ("08:00", "12:30", "21:00"),   # day of the UK switch (Sunday): London back on GMT
    "2026-10-26": ("08:00", "12:30", "21:00"),   # autumn gap week: London winter, New York summer
    "2026-10-30": ("08:00", "12:30", "21:00"),   # autumn gap week, last weekday
    "2026-11-01": ("08:00", "13:30", "22:00"),   # day of the US switch (Sunday): New York back on EST
    "2026-11-02": ("08:00", "13:30", "22:00"),   # both on winter time
    "2026-01-14": ("08:00", "13:30", "22:00"),   # mid winter
}


def _utc(day: str, hhmm: str, minutes: int = 0) -> pd.Timestamp:
    return pd.Timestamp(f"{day} {hhmm}", tz="UTC") + pd.Timedelta(minutes=minutes)


def _label(ts: pd.Timestamp) -> str:
    return str(DEFAULT_SESSIONS.session_label(pd.DatetimeIndex([ts]))[0])


def _code(ts: pd.Timestamp) -> int:
    return int(DEFAULT_SESSIONS.session_windows(pd.DatetimeIndex([ts]))[0][0])


@pytest.mark.parametrize("day", sorted(CASES))
def test_london_and_new_york_open_at_their_local_times_on_every_regime_and_switch_day(day):
    ldn, ny, _ = CASES[day]
    assert _label(_utc(day, ldn, -1)) == "asia" and _label(_utc(day, ldn)) == "london"
    assert _label(_utc(day, ny, -1)) == "london" and _label(_utc(day, ny)) == "newyork"


@pytest.mark.parametrize("day", sorted(CASES))
def test_new_york_closes_at_five_pm_new_york_and_asia_opens_at_eleven_pm_utc_all_year(day):
    _, _, close = CASES[day]
    names = list(DEFAULT_SESSIONS.sessions_local)
    assert _code(_utc(day, close, -1)) == names.index("newyork")
    assert _code(_utc(day, close)) == -1                     # between the New York close and the Asia open
    assert _code(_utc(day, "22:59")) == -1                   # Tokyo has no DST: Asia opens 23:00 UTC on every date
    assert _code(_utc(day, "23:00")) == names.index("asia")


def test_bars_between_the_new_york_close_and_the_asia_open_are_priced_as_asia():
    # 21:00-23:00 UTC in US summer, 22:00-23:00 in US winter: thin hours around the daily break, priced with Asia
    assert _label(_utc("2026-07-15", "21:30")) == "asia"
    assert _label(_utc("2026-01-14", "21:30")) == "newyork" and _label(_utc("2026-01-14", "22:30")) == "asia"


def test_session_instances_start_at_the_local_open_of_their_own_date():
    ts = pd.DatetimeIndex([_utc("2026-03-27", "09:00"), _utc("2026-03-30", "09:00"), _utc("2026-03-27", "03:00"),
                           _utc("2026-10-27", "14:00"), _utc("2026-11-03", "14:00")])
    code, start = session_instances(ts)
    assert [SESSION_NAMES[c] for c in code] == ["london", "london", "asia", "newyork", "newyork"]
    assert list(pd.to_datetime(start, utc=True)) == [_utc("2026-03-27", "08:00"), _utc("2026-03-30", "07:00"),
                                                     _utc("2026-03-26", "23:00"), _utc("2026-10-27", "12:30"),
                                                     _utc("2026-11-03", "13:30")]


def _bars(start: str, end: str) -> pd.DataFrame:
    ts = pd.date_range(start, end, freq="15min", tz="UTC")
    o = 100 + np.cumsum(np.random.default_rng(7).normal(0, 0.2, len(ts)))
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": o + 0.3, "low": o - 0.3, "close": o + 0.05,
                         "spread": 0.1, "tick_count": 10.0})


@pytest.mark.parametrize("day", ["2026-03-06", "2026-03-09", "2026-03-27", "2026-03-30", "2026-10-23", "2026-10-26",
                                 "2026-10-30", "2026-11-02"])
def test_minutes_since_each_open_are_zero_on_the_local_open_bar(day):
    ldn, ny, _ = CASES[day]
    m = _bars(f"{day} 00:00", f"{day} 23:45")
    out = f_session(m, {})
    at = {t: k for k, t in enumerate(pd.DatetimeIndex(m["ts_utc"]))}
    assert out["min_since_london_open"][at[_utc(day, ldn)]] == 0
    assert out["min_since_london_open"][at[_utc(day, ldn, 15)]] == 15
    assert out["min_since_newyork_open"][at[_utc(day, ny)]] == 0
    assert out["min_since_asia_open"][at[_utc(day, "23:00")]] == 0
    assert out["min_since_newyork_open"][at[_utc(day, ny, -15)]] == 1425          # yesterday's open, 23 h 45 m ago
    codes = ["asia", "london", "newyork"]
    assert out["session_id"][at[_utc(day, ldn)]] == codes.index("london")
    assert out["session_id"][at[_utc(day, ldn, -15)]] == codes.index("asia")


def test_minutes_since_an_open_span_the_twenty_five_hour_day_after_a_switch():
    # the calendar has an open every date: Saturday 24 Oct 08:00 BST = 07:00 UTC, Sunday 25 Oct 08:00 GMT = 08:00 UTC
    m = _bars("2026-10-24 06:00", "2026-10-25 09:00")
    out = f_session(m, {})
    at = {t: k for k, t in enumerate(pd.DatetimeIndex(m["ts_utc"]))}
    assert out["min_since_london_open"][at[_utc("2026-10-25", "07:45")]] == pytest.approx(24 * 60 + 45)
    assert out["min_since_london_open"][at[_utc("2026-10-25", "08:00")]] == 0


def test_session_features_on_a_prefix_equal_the_full_history_across_both_switches():
    m = _bars("2026-03-01", "2026-04-05")
    full = build_features(m, ["session", "session_zones", "market_structure"])
    for cut in (_utc("2026-03-08", "13:00"), _utc("2026-03-29", "07:30"), _utc("2026-03-20", "12:45")):
        n = int((pd.DatetimeIndex(m["ts_utc"]) <= cut).sum())
        part = build_features(m.iloc[:n].reset_index(drop=True), ["session", "session_zones", "market_structure"])
        pd.testing.assert_frame_equal(part.reset_index(drop=True), full.iloc[:n].reset_index(drop=True))


def test_cost_table_sessions_follow_daylight_saving():
    # 07:30 UTC is London (08:30 BST) in July but still Asia (07:30 GMT) in January
    ticks = pd.DataFrame({"ts_utc": [_utc("2026-07-15", "07:30"), _utc("2026-01-14", "07:30"),
                                     _utc("2026-01-14", "13:00")],
                          "bid": [2000.0, 2000.0, 2000.0], "ask": [2000.1, 2000.5, 2000.1]})
    t = spread_table(ticks)
    assert t["london"].median == pytest.approx(0.1, abs=1e-6) and t["london"].n == 2       # July 07:30, Jan 13:00
    assert t["asia"].median == pytest.approx(0.5, abs=1e-6) and t["asia"].n == 1


def test_features_whose_values_changed_carry_a_new_version_and_stay_in_the_default_set():
    for name in ("session", "session_zones", "market_structure"):
        assert FEATURES[name].version == "2", name
        assert name in DEFAULT_FEATURE_NAMES
