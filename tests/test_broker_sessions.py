"""TRACEABILITY D13: session state from the broker's session list, not from silence; the static table is the fallback.

The broker reports its trading sessions per server-calendar day resolved to UTC for that date (DST per date, server
Europe/Athens); `SessionTable.from_broker` turns them into the runtime table, refreshed daily by `BrokerSessions`,
whose `entry_block(now)` is the engine's one-line hook."""
from datetime import date, time
from typing import Any

import pandas as pd
import pytest

from goldbot.data.calendar import (
    DEFAULT_SESSIONS,
    SESSION_DAYS,
    BrokerSessions,
    SessionTable,
    TradingSession,
    resolve_server_sessions,
)
from goldbot.execution import bridge as bridge_mod
from goldbot.execution.bridge import GuardedBroker
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker

TZ = "Europe/Athens"


def _utc(s: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([s], tz="UTC")


class _ListBroker:
    """A broker whose session list the test controls; counts reads."""

    def __init__(self, sessions: list[TradingSession] | Exception) -> None:
        self.sessions, self.reads = sessions, 0

    def trading_sessions(self, symbol: str) -> list[TradingSession]:
        self.reads += 1
        if isinstance(self.sessions, Exception):
            raise self.sessions
        return self.sessions


def test_server_sessions_convert_to_utc_per_date_across_the_dst_change():
    # Athens leaves EET (UTC+2) for EEST (UTC+3) on Sunday 2026-03-29: Thursday/Friday before it and Monday after it
    # resolve the same 01:02 server open to different UTC times
    out = resolve_server_sessions(DEFAULT_SESSIONS.server_sessions(), TZ, date(2026, 3, 26))
    by_day = {s.server_date: s for s in out}
    assert set(by_day) == {date(2026, 3, d) for d in (26, 27, 30, 31)} | {date(2026, 4, 1)}   # weekend closed
    assert (by_day[date(2026, 3, 26)].start_utc, by_day[date(2026, 3, 26)].end_utc) == (time(23, 2), time(21, 59))
    assert by_day[date(2026, 3, 27)].end_utc == time(21, 57)                                   # Friday close 23:57
    assert (by_day[date(2026, 3, 30)].start_utc, by_day[date(2026, 3, 30)].end_utc) == (time(22, 2), time(20, 59))
    assert by_day[date(2026, 3, 30)].weekday == 0
    # the Monday session starts on the Sunday in UTC; resolved as instants it is one unbroken window
    start, end = by_day[date(2026, 3, 30)].interval(TZ)
    assert start == pd.Timestamp("2026-03-29 22:02", tz="UTC") and end == pd.Timestamp("2026-03-30 20:59", tz="UTC")
    # and back in October (EEST -> EET on Sunday 2026-10-25)
    autumn = {s.server_date: s for s in resolve_server_sessions(DEFAULT_SESSIONS.server_sessions(), TZ, date(2026, 10, 23))}
    assert autumn[date(2026, 10, 23)].start_utc == time(22, 2) and autumn[date(2026, 10, 26)].start_utc == time(23, 2)


def test_a_broker_table_matches_the_static_table_when_the_broker_reports_the_documented_hours():
    now = pd.Timestamp("2026-03-26 12:00", tz="UTC")
    pb = PaperBroker()
    pb.on_tick(Tick(ts_utc=now, bid=2400.0, ask=2400.2))
    table = SessionTable.from_broker(pb, "XAUUSD", now)
    assert table.source == "broker"
    minutes = pd.date_range(now.floor("D"), periods=SESSION_DAYS * 1440, freq="1min", tz="UTC")
    assert (table.is_open(minutes) == DEFAULT_SESSIONS.is_open(minutes)).all()


def test_a_shortened_session_blocks_entries_outside_it():
    # Christmas Eve (Thursday 2026-12-24, server) closes early at 18:00 instead of 23:59; Christmas Day (Friday) is
    # not reported at all; the other weekdays keep the documented hours
    normal = [s for s in DEFAULT_SESSIONS.server_sessions() if s[0] in (0, 1, 2)]
    sessions = resolve_server_sessions([*normal, (3, 62 * 60, 18 * 3600)], TZ, date(2026, 12, 23))
    now = pd.Timestamp("2026-12-23 10:00", tz="UTC")
    table = SessionTable.from_broker(_ListBroker(sessions), "XAUUSD", now)
    assert table.is_open(_utc("2026-12-24 15:00"))[0]                  # 17:00 server, inside the short session
    assert not table.is_open(_utc("2026-12-24 17:00"))[0]              # 19:00 server: the static table says open
    assert DEFAULT_SESSIONS.is_open(_utc("2026-12-24 17:00"))[0]
    assert not table.is_open(_utc("2026-12-25 12:00"))[0]              # holiday inside the reported days: closed
    assert table.entry_block(pd.Timestamp("2026-12-24 17:00", tz="UTC")) is not None


@pytest.mark.parametrize("bad", [RuntimeError("terminal not connected"), [], ["not a session"]])
def test_an_unreadable_session_list_falls_back_to_the_static_table_never_always_open(bad: Any, caplog):
    now = pd.Timestamp("2026-03-26 12:00", tz="UTC")
    with caplog.at_level("WARNING", logger="goldbot.calendar"):
        table = SessionTable.from_broker(_ListBroker(bad), "XAUUSD", now)
    assert table.source == "static (fallback)" and table.note
    assert "static table" in caplog.text
    assert not table.is_open(_utc("2026-03-28 12:00"))[0]              # Saturday stays closed
    assert not table.is_open(_utc("2026-03-26 22:30"))[0]              # daily break stays closed
    assert table.is_open(_utc("2026-03-26 12:00"))[0]


def test_a_session_list_with_no_closed_time_is_refused():
    always = [TradingSession(weekday=d.weekday(), start_utc=time(0), end_utc=time(0), server_date=d)
              for d in pd.date_range("2026-03-23", periods=SESSION_DAYS).date]
    table = SessionTable.from_broker(_ListBroker(always), "XAUUSD", pd.Timestamp("2026-03-23 12:00", tz="UTC"))
    assert table.source == "static (fallback)" and "no closed time" in table.note


def test_the_engine_hook_refreshes_once_per_server_day_and_blocks_entries_when_closed():
    sessions = resolve_server_sessions(DEFAULT_SESSIONS.server_sessions(), TZ, date(2026, 3, 23))
    broker = _ListBroker(sessions)
    hook = BrokerSessions(broker, "XAUUSD", fallback=SessionTable(server_tz=TZ))
    assert hook.entry_block(pd.Timestamp("2026-03-23 10:00", tz="UTC")) is None
    assert hook.entry_block(pd.Timestamp("2026-03-23 15:00", tz="UTC")) is None
    assert broker.reads == 1                                            # same server day: no re-read
    why = hook.entry_block(pd.Timestamp("2026-03-23 22:30", tz="UTC"))  # 00:30 server Tuesday: daily break
    assert why is not None and "market closed" in why
    assert broker.reads == 2                                            # new server day: refreshed


def test_a_failed_refresh_is_retried_after_half_an_hour_and_never_opens_the_market():
    broker = _ListBroker(RuntimeError("down"))
    hook = BrokerSessions(broker, "XAUUSD", fallback=SessionTable(server_tz=TZ))
    assert hook.entry_block(pd.Timestamp("2026-03-28 12:00", tz="UTC")) is not None   # Saturday, static: closed
    hook.entry_block(pd.Timestamp("2026-03-28 12:20", tz="UTC"))
    assert broker.reads == 1
    broker.sessions = resolve_server_sessions(DEFAULT_SESSIONS.server_sessions(), TZ, date(2026, 3, 28))
    hook.entry_block(pd.Timestamp("2026-03-28 13:01", tz="UTC"))
    assert broker.reads == 2 and hook.table(pd.Timestamp("2026-03-28 13:01", tz="UTC")).source == "broker"


def test_trading_sessions_is_a_read_only_bridge_method():
    returns, retry_safe = bridge_mod.METHODS["trading_sessions"]
    assert retry_safe and returns == "list[TradingSession]"
    pb = PaperBroker()
    pb.on_tick(Tick(ts_utc=pd.Timestamp("2026-03-26 12:00", tz="UTC"), bid=1.0, ask=1.1))
    assert GuardedBroker(pb, magic_base=260100).trading_sessions("XAUUSD") == pb.trading_sessions("XAUUSD")
    wire = bridge_mod._decode(bridge_mod._encode(pb.trading_sessions("XAUUSD")))
    assert wire == pb.trading_sessions("XAUUSD") and all(isinstance(s, TradingSession) for s in wire)


def test_the_mt5_adapter_reports_the_documented_hours_because_the_python_api_has_no_schedule(caplog):
    from goldbot.execution.mt5_adapter import MT5Broker
    b = object.__new__(MT5Broker)                      # no terminal: the method never calls the MetaTrader5 package
    b.server_tz, b._sessions_noted = TZ, False
    with caplog.at_level("INFO", logger="goldbot.mt5"):
        out = b.trading_sessions("XAUUSD")
    assert "no session schedule" in caplog.text
    today = pd.Timestamp.now(tz="UTC").tz_convert(TZ).date()
    assert out == resolve_server_sessions(DEFAULT_SESSIONS.server_sessions(), TZ, today)
    assert SessionTable.from_broker(b, "XAUUSD", pd.Timestamp.now(tz="UTC")).source == "broker"
