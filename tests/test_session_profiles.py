"""Multi-instrument demo plan (2026-11-02, lane L2): "Sessions per asset class checked vs broker trading_sessions;
Friday cut per symbol" and early closes. Every profile is a static server-time table (goldbot/data/calendar.py
SESSION_PROFILES); a non-gold profile is intersected with the broker's own session list, so each can only narrow the
other. The metals profile IS today's XAUUSD table: gold's sessions, cut and holidays are unchanged."""
from __future__ import annotations

from datetime import date, time
from typing import get_args

import pandas as pd
import pytest

from goldbot.data.calendar import (
    DEFAULT_SESSIONS,
    SESSION_PROFILES,
    SessionTable,
    TradingSession,
    profile_disagreement,
    resolve_server_sessions,
    session_profile,
    session_table_cache,
    session_table_from_cache,
)
from goldbot.instruments import SessionProfile, load_instruments
from goldbot.risk.windows import weekend_cut


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _open(table: SessionTable, s: str) -> bool:
    return bool(table.is_open(pd.DatetimeIndex([_utc(s)]))[0])


def test_every_registry_session_profile_has_a_table():
    assert set(SESSION_PROFILES) == set(get_args(SessionProfile))
    for spec in load_instruments().instruments.values():
        assert session_profile(spec.session_profile).profile == spec.session_profile


def test_the_metals_profile_is_todays_xauusd_table():
    assert session_profile("metals") == DEFAULT_SESSIONS == SessionTable()
    assert session_profile("metals", server_tz="Europe/London") == SessionTable(server_tz="Europe/London")


def test_metals_friday_cut_equals_the_engines_weekend_cut():
    from goldbot.engine.runner import EngineConfig
    assert session_profile("metals").friday_cut.strftime("%H:%M") == EngineConfig.model_fields["weekend_cut"].default


@pytest.mark.parametrize("profile, stamp, is_open", [
    ("index_eu", "2026-11-03 23:30", False),       # Wed 01:30 server: Eurex not open yet
    ("metals", "2026-11-03 23:30", True),          # gold is (after its 01:02 reopen)
    ("index_eu", "2026-11-04 10:00", True),
    ("index_uk", "2026-11-04 00:30", False),       # 02:30 server, ICE FTSE opens 03:00
    ("index_jp", "2026-11-04 07:00", False),       # 09:00 server: the Osaka midday break
    ("index_jp", "2026-11-04 12:00", True),
    ("softs", "2026-11-04 08:00", False),          # 10:00 server: ICE US softs not open
    ("softs", "2026-11-04 12:00", True),
    ("ags", "2026-11-04 13:00", False),            # 15:00 server: CBOT grains' morning pause
    ("ags", "2026-11-04 15:00", True),
    ("rates_eu", "2026-11-04 05:00", False),       # 07:00 server: before the liquid Bund hours
    ("fx", "2026-11-04 03:00", True),
    ("energy", "2026-11-04 00:00", False),         # 02:00 server: Brent not open
    ("index_us", "2026-11-04 22:30", False),       # 00:30 server: the CME daily halt
    ("index_us", "2026-11-04 15:00", True),
    ("rates_us", "2026-11-04 22:30", False),
])
def test_session_profile_hours_per_asset_class(profile, stamp, is_open):
    assert _open(session_profile(profile), stamp) is is_open


def test_every_profile_closes_for_the_weekend():
    for name in SESSION_PROFILES:
        assert not _open(session_profile(name), "2026-11-07 12:00"), name    # Saturday


@pytest.mark.parametrize("profile, stamp, cut", [
    ("metals", "2026-11-06 19:45", True),          # Friday 21:45 server, past 21:30
    ("metals", "2026-11-06 15:45", False),
    ("softs", "2026-11-06 15:45", True),           # 17:45 server, past the softs' 17:30
    ("ags", "2026-11-06 15:45", False),
    ("ags", "2026-11-06 17:15", True),             # 19:15 server, past 19:00
    ("index_eu", "2026-11-05 20:00", False),       # Thursday: never
    ("fx", "2026-11-07 10:00", True),              # Saturday: the weekend
])
def test_friday_cut_per_symbol(profile, stamp, cut):
    assert weekend_cut(session_profile(profile), _utc(stamp)) is cut


@pytest.mark.parametrize("profile, stamp, is_open", [
    ("index_us", "2026-11-26 19:00", False),       # Thanksgiving, 13:00 CT: CME halted at 12:00 CT
    ("index_us", "2026-11-26 16:00", True),        # 10:00 CT, before the halt
    ("metals", "2026-11-26 19:00", True),          # gold's is_open is unchanged (the gap guard reads the holidays)
    ("fx", "2026-11-26 19:00", True),
    ("ags", "2027-01-18 15:00", False),            # MLK day: CBOT grains shut the whole day
    ("ags", "2027-01-19 15:00", True),             # and trade the next day
    ("index_eu", "2027-03-29 10:00", False),       # Easter Monday: Eurex shut
    ("index_eu", "2027-03-30 10:00", True),
    ("index_uk", "2026-12-25 10:00", False),
])
def test_early_closes_and_holidays_per_asset_class(profile, stamp, is_open):
    assert _open(session_profile(profile), stamp) is is_open


class _Broker:
    def __init__(self, sessions: list[TradingSession]):
        self.sessions = sessions

    def trading_sessions(self, symbol: str) -> list[TradingSession]:
        return self.sessions


def _gold_hours(first: date) -> list[TradingSession]:
    return resolve_server_sessions(DEFAULT_SESSIONS.server_sessions(), "Europe/Athens", first)


def test_a_non_gold_profile_is_intersected_with_the_broker_sessions():
    now = _utc("2026-11-03 12:00")
    broker = _Broker(_gold_hours(date(2026, 11, 2)))        # the MT5 adapter's documented (gold) hours
    eu = SessionTable.from_broker(broker, "DE40", now, session_profile("index_eu"))
    assert eu.source == "broker" and eu.profile == "index_eu"
    assert not _open(eu, "2026-11-03 23:30")                  # the broker says open, the profile says closed
    assert _open(eu, "2026-11-04 10:00")
    # the broker can narrow the profile too: a day it does not list is closed
    short = _Broker([s for s in _gold_hours(date(2026, 11, 2)) if s.server_date != date(2026, 11, 4)])
    assert not _open(SessionTable.from_broker(short, "DE40", now, session_profile("index_eu")), "2026-11-04 10:00")
    # gold keeps the broker's list alone (D13), as before
    gold = SessionTable.from_broker(broker, "XAUUSD", now, session_profile("metals"))
    assert _open(gold, "2026-11-03 23:30")


def test_profile_disagreement_reports_where_the_broker_and_the_profile_differ():
    hours = _gold_hours(date(2026, 11, 2))
    assert profile_disagreement(session_profile("metals"), hours) == []
    out = profile_disagreement(session_profile("index_eu"), hours)
    assert out and out[0].startswith("broker open") and "index_eu" in out[0]
    assert profile_disagreement(session_profile("index_eu"), []) == ["the broker reported no sessions"]


def test_a_profile_survives_the_engine_state_cache():
    for name in SESSION_PROFILES:
        t = session_profile(name)
        back = session_table_from_cache(session_table_cache(t))
        assert back is not None and back.profile == name and back == t
    assert "profile" not in session_table_cache(DEFAULT_SESSIONS)            # gold's state file is unchanged


def test_profile_pauses_are_server_time_windows():
    jp = session_profile("index_jp")
    assert jp.daily_pauses and all(isinstance(a, time) and a < b for a, b in jp.daily_pauses)
    assert DEFAULT_SESSIONS.daily_pauses == () and DEFAULT_SESSIONS.holidays == "none"
