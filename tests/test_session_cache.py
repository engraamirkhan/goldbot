"""Incident 2026-11-02 (docs/incidents/2026-11-02-multi-instrument.md): the icm-probe engine read `trading_sessions` for
19 books one after another before its loop started, each a 14-day M1 history read holding the bridge's one terminal
for seconds on a CPU-starved box (34 s of start-up). The rules held here:

* engine start makes no blocking non-gold `trading_sessions` call: a book starts on its static session profile, or on
  its table read in the last 24 h (state/instruments.db, `instrument_sessions`), with no broker call at all;
* after the loop starts the reads are staggered: at most one in flight, the next started on a later pass;
* a table read from the broker is cached for SESSION_CACHE_TTL (24 h) and reused, by a restart too;
* a failed read keeps the static profile and is retried after 30 min, never in a loop;
* the observed-hours read is 8 days of M1 bars, not 14 (every weekday once, from complete days only)."""
from __future__ import annotations

import inspect
import threading
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.calendar import DEFAULT_SESSIONS, TradingSession, resolve_server_sessions, session_profile
from goldbot.engine import Engine, EngineConfig, session_cache
from goldbot.engine.books import BookSpec, using
from goldbot.engine.instruments_db import InstrumentsDb
from goldbot.engine.session_cache import SESSION_CACHE_TTL, CachedSessions, SessionStagger
from goldbot.execution import mt5_adapter
from goldbot.execution.paper import PaperBroker
from goldbot.ops.services import engine as engine_svc

NOW = pd.Timestamp.now("UTC").floor("s")
TZ = DEFAULT_SESSIONS.server_tz


class SessionsBroker:
    """trading_sessions only: the documented hours for the week of `now`; counts calls; `gate` holds a call open."""

    def __init__(self, *, fail: bool = False, gate: threading.Event | None = None) -> None:
        self.calls: list[str] = []
        self.fail, self.gate = fail, gate

    def trading_sessions(self, symbol: str) -> list[TradingSession]:
        self.calls.append(symbol)
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail:
            raise TimeoutError("bridge read timed out")
        return resolve_server_sessions(DEFAULT_SESSIONS.server_sessions(), TZ, NOW.tz_convert(TZ).date())


def _source(tmp_path: Path, broker: Any, symbol: str = "US500", profile: str = "index_us",
            protocol: int | None = 11) -> CachedSessions:
    return CachedSessions(broker, symbol, account_id="icm-probe", db=InstrumentsDb(tmp_path / "state"),
                          fallback=session_profile(profile, TZ), protocol=lambda: protocol)


# ------------------------------------------------------------------------------------------------ the 24 h cache
def test_a_broker_table_is_cached_and_a_restart_reuses_it_without_a_broker_call(tmp_path):
    first = SessionsBroker()
    t = _source(tmp_path, first).table(NOW)
    assert t.source == "broker" and first.calls == ["US500"]
    again = SessionsBroker()                                   # a restarted engine: a new source, the same file
    src = _source(tmp_path, again)
    cached = src.cached(NOW + pd.Timedelta(hours=2))
    assert cached is not None and cached.source == "broker" and cached.broker_windows == t.broker_windows
    assert cached.profile == "index_us" and cached.intersect_broker == t.intersect_broker
    assert not src.due(NOW + pd.Timedelta(hours=2))
    assert src.table(NOW + pd.Timedelta(hours=2)).broker_windows == t.broker_windows and again.calls == []


def test_the_cache_expires_after_24_hours(tmp_path):
    _source(tmp_path, SessionsBroker()).table(NOW)
    later = NOW + SESSION_CACHE_TTL + pd.Timedelta(minutes=1)
    b = SessionsBroker()
    src = _source(tmp_path, b)
    assert SESSION_CACHE_TTL == pd.Timedelta(hours=24)
    assert src.cached(later) is None and src.due(later)
    src.table(later)
    assert b.calls == ["US500"]


def test_a_cache_is_per_account_and_symbol_and_profile(tmp_path):
    _source(tmp_path, SessionsBroker()).table(NOW)
    assert _source(tmp_path, SessionsBroker(), symbol="DE40").cached(NOW) is None
    assert _source(tmp_path, SessionsBroker(), profile="fx").cached(NOW) is None      # a re-profiled instrument
    other = CachedSessions(SessionsBroker(), "US500", account_id="vantage-demo", db=InstrumentsDb(tmp_path / "state"),
                           fallback=session_profile("index_us", TZ), protocol=lambda: 11)
    assert other.cached(NOW) is None


def test_a_failed_read_keeps_the_static_profile_is_not_cached_and_waits_30_min(tmp_path):
    b = SessionsBroker(fail=True)
    src = _source(tmp_path, b)
    t = src.table(NOW)
    assert t.source == "static (fallback)" and t.profile == "index_us"
    assert _source(tmp_path, SessionsBroker()).cached(NOW) is None
    assert not src.due(NOW + pd.Timedelta(minutes=29)) and src.due(NOW + pd.Timedelta(minutes=31))
    src.table(NOW + pd.Timedelta(minutes=5))                  # asked anyway inside the back-off: no second read
    assert b.calls == ["US500"]


def test_an_unreadable_cache_is_no_cache(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "instruments.db").write_bytes(b"not a database" * 100)
    b = SessionsBroker()
    src = _source(tmp_path, b)
    assert src.cached(NOW) is None
    assert src.table(NOW).source == "broker"                 # the read still works; only the save is lost


# ------------------------------------------------------------------------------------------------ the stagger
class _Src:
    def __init__(self, due: bool, gate: threading.Event) -> None:
        self._due, self.gate, self.reads = due, gate, 0

    def due(self, now: pd.Timestamp) -> bool:
        return self._due

    def table(self, now: pd.Timestamp) -> Any:
        self.reads += 1
        self.gate.wait(10)
        self._due = False
        return DEFAULT_SESSIONS


def test_the_stagger_runs_one_read_at_a_time_and_skips_books_that_are_not_due():
    gate = threading.Event()
    srcs = [_Src(True, gate), _Src(False, gate), _Src(True, gate)]
    refs = [(f"b{i}", engine_svc.SessionRefresher(s)) for i, s in enumerate(srcs)]
    st = SessionStagger(refs)
    assert st.step(NOW) == "b0"
    assert st.step(NOW) is None and st.step(NOW) is None           # one in flight: nothing else starts
    gate.set()
    refs[0][1]._thread.join(5)
    assert refs[0][1].collect(NOW, None) is DEFAULT_SESSIONS
    assert st.step(NOW) == "b2"                                     # b1 is not due (cached): skipped
    refs[2][1]._thread.join(5)
    refs[2][1].collect(NOW, None)
    assert st.step(NOW) is None                                     # nothing due
    assert [s.reads for s in srcs] == [1, 0, 1]


# ------------------------------------------------------------------------------------------------ engine start
def _engine(tmp_path: Path, n: int = 4) -> Engine:
    specs = tuple(BookSpec(key=f"SYM{k:02d}", symbol=f"SYM{k:02d}", store_symbol=f"SYM{k:02d}", magic_block=k,
                           digits=2, point=0.01, contract_size=100.0) for k in range(1, n + 1))
    return Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", magic_base=260500,
                               state_dir=str(tmp_path / "state"), instruments=specs), PaperBroker(), [], {})


def test_engine_start_makes_no_blocking_non_gold_sessions_call_and_staggers_them_after(tmp_path):
    eng = _engine(tmp_path)
    gate = threading.Event()
    b = SessionsBroker(gate=gate)                                   # a read that would hang the start
    stagger = engine_svc.book_sessions(eng, b, "icm-probe", TZ, NOW, protocol=lambda: 11)
    assert b.calls == []                                            # the start read nothing from the broker
    for book in list(eng.books.values())[1:]:
        with using(eng, book):
            assert eng.sessions.source != "broker"                  # the static profile until read
    assert stagger.step(NOW) is not None and stagger.step(NOW) is None
    for _ in range(100):
        if b.calls:
            break
        threading.Event().wait(0.01)
    assert b.calls == ["SYM01"]                                     # one read in flight, on its own thread
    gate.set()


def test_engine_start_applies_a_table_cached_in_the_last_24_hours(tmp_path):
    eng = _engine(tmp_path, n=2)
    CachedSessions(SessionsBroker(), "SYM02", account_id="icm-probe", db=InstrumentsDb(tmp_path / "state"),
                   fallback=session_profile("metals", TZ), protocol=lambda: 11).table(NOW - pd.Timedelta(hours=3))
    b = SessionsBroker()
    stagger = engine_svc.book_sessions(eng, b, "icm-probe", TZ, NOW, protocol=lambda: 11)
    books = list(eng.books.values())
    with using(eng, books[2]):
        assert eng.sessions.source == "broker"                      # the cached table, no call
    with using(eng, books[1]):
        assert eng.sessions.source != "broker"
    assert b.calls == []
    assert [x for x, _ in stagger.items] == books[1:]
    assert not stagger.items[1][1].source.due(NOW)                  # SYM02 is not read again until it expires


def test_the_engine_loop_reads_book_sessions_only_through_the_stagger():
    src = inspect.getsource(engine_svc._run_engine)
    assert src.count("refresh_sessions") == 1                       # gold's documented hours only
    assert "resolve(book_sessions)(" in src and "stagger.step(" in src


def test_the_observed_hours_read_is_eight_days():
    assert mt5_adapter.OBSERVE_DAYS == 8
    assert session_cache.SESSION_CACHE_TTL == pd.Timedelta(hours=24)


@pytest.mark.parametrize("hours", [1, 23])
def test_a_cached_table_is_fresh_within_the_ttl(tmp_path, hours):
    _source(tmp_path, SessionsBroker()).table(NOW)
    assert _source(tmp_path, SessionsBroker()).cached(NOW + pd.Timedelta(hours=hours)) is not None


# ------------------------------------------------------------------------------------------------ review of #319
def test_a_table_from_a_bridge_older_than_v11_is_used_but_never_cached(tmp_path):
    """F4: a v10 bridge still builds hours from today's unfinished day (the bug PR #317 fixed on the bridge); caching
    its table would keep that wrong table for 24 h after the bridge is fixed."""
    for proto in (10, None):
        b = SessionsBroker()
        assert _source(tmp_path, b, protocol=proto).table(NOW).source == "broker"
        assert _source(tmp_path, SessionsBroker()).cached(NOW) is None, proto


def test_a_cached_row_without_the_protocol_marker_is_rejected(tmp_path):
    t = _source(tmp_path, SessionsBroker()).table(NOW)
    db = InstrumentsDb(tmp_path / "state")
    loaded = db.load_sessions("icm-probe", "US500")
    assert loaded is not None
    read_at, body = loaded
    assert body[session_cache.PROTOCOL_KEY] >= session_cache.SESSIONS_MIN_PROTOCOL
    db.save_sessions("icm-probe", "US500", read_at, {k: v for k, v in body.items() if k != session_cache.PROTOCOL_KEY})
    assert _source(tmp_path, SessionsBroker()).cached(NOW) is None
    db.save_sessions("icm-probe", "US500", read_at, {**body, session_cache.PROTOCOL_KEY: 10})
    assert _source(tmp_path, SessionsBroker()).cached(NOW) is None
    assert t.source == "broker"


def test_a_read_running_past_twice_its_timeout_no_longer_holds_the_stagger(caplog):
    """F6: one hung read (a bridge call that never returns) must not stop every other book's read for good."""
    gate = threading.Event()
    now = [100.0]
    srcs = [_Src(True, gate), _Src(True, gate)]
    refs = [(f"b{i}", engine_svc.SessionRefresher(s)) for i, s in enumerate(srcs)]
    st = SessionStagger(refs, read_timeout_s=10.0, clock=lambda: now[0])
    caplog.set_level("WARNING", logger="goldbot.engine.sessions")
    assert st.step(NOW) == "b0"
    now[0] += 19.0
    assert st.step(NOW) is None                                     # within 2 x 10 s: still in flight
    now[0] += 2.0
    assert st.step(NOW) == "b1"                                     # b0 hung: skipped, logged
    msgs = [r.getMessage() for r in caplog.records if "session read" in r.getMessage()]
    assert len(msgs) == 1 and "b0" in msgs[0]
    now[0] += 1.0
    st.step(NOW)
    assert len([r for r in caplog.records if "session read" in r.getMessage()]) == 1   # logged once
    gate.set()
