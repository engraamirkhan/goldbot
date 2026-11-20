"""Gold's live key per account, one writer per account, a boot id on every row (ADR 0091,
2026-11-20; the architect plan and the trading-safety conditions S1-S5 of it).

Design rules pinned here:
- `books.live_key` of a gold book is (account, symbol): two gold engines of one broker never write one key. The
  minutes stored under the broker's key before (`books.legacy_live_key`) are read under the account's own (its copy
  wins) and so is the saved cut-over, so a restart holds the same history and cut-over as before (S4); the new
  `writer` column never reaches the engine's bars (S5).
- Each engine start holds the account's exclusive writer lock (a lock file of its own, the repair protocol unchanged),
  taken before any bridge connection (S2). Held by another engine: the start is refused with a journal line and a
  health WARN naming the holder's pid, and an exit the service manager restarts, never through `start_refused`, which
  would write the HOLDER's startup_error and halt real accounts (S1). kill -9 of the holder frees it (S3). No flock:
  the engine starts unproven (S2, quant Q6).
- Every row an engine writes carries its boot id: bars_1m_live (`writer`), its dq events, its exploration candidates;
  each start records `engine_boot` with whether it holds the lock.
Synthetic rows only; every store and state dir is a temp copy."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.quality import DQEvent, events_frame
from goldbot.data.resample import BAR_COLUMNS, ticks_to_1m
from goldbot.data.store import Store
from goldbot.db import Database
from goldbot.engine import books, history
from goldbot.engine.history import FILLED_COL, LIVE_TABLE, REPLACED_COL, WRITER_COL, BarWriter
from goldbot.ops import accounts, engine_lock, health
from goldbot.ops import run as run_mod
from goldbot.ops.services import engine as engine_service
from tests.test_engine_history_read import LIVE_FROM, TICKS, _engine
from tests.test_health import make_ctx
from tests.test_start_refusal import ACC

OWN_FROM = pd.Timestamp("2025-03-10", tz="UTC")
REPO = Path(__file__).resolve().parents[1]


def _legacy_store(tmp_path: Path) -> Store:
    """Release minutes to OWN_FROM, then the engine's minutes under the broker's (legacy) key to LIVE_FROM."""
    store = Store(tmp_path / "data")
    store.append("bars_1m", ticks_to_1m(TICKS[TICKS["ts_utc"] < OWN_FROM]), source="dukascopy")
    live = ticks_to_1m(TICKS[(TICKS["ts_utc"] >= OWN_FROM) & (TICKS["ts_utc"] < LIVE_FROM)])
    store.append(LIVE_TABLE, live.assign(available_utc=pd.to_datetime(live["visible_at"], utc=True)), source="icm",
                 symbol="XAUUSD")
    return store


# ---------------------------------------------------------------- the key
def test_a_gold_book_keys_its_minutes_by_its_account_and_reads_the_brokers_key_only_as_legacy(tmp_path):
    eng = _engine(tmp_path)
    assert books.live_key(eng) == ("icm-demo", "XAUUSD")
    assert books.legacy_live_key(eng) == ("icm", "XAUUSD")


def test_the_engine_writes_its_minutes_under_its_account_with_its_boot_id_and_nothing_under_the_brokers_key(
        tmp_path):
    eng = _engine(tmp_path, boot_id="b" * 32)
    history._write(eng, ticks_to_1m(TICKS[(TICKS["ts_utc"] >= OWN_FROM) & (TICKS["ts_utc"] < OWN_FROM
                                                                             + pd.Timedelta(hours=1))]))
    assert eng.history.writer is not None and eng.history.writer.join(10)
    store = Store(tmp_path / "data")
    got = store.read(LIVE_TABLE, source="icm-demo", symbol="XAUUSD")
    assert len(got) == 60 and set(got[WRITER_COL]) == {"b" * 32}
    assert store.read(LIVE_TABLE, source="icm", symbol="XAUUSD").empty


# ---------------------------------------------------------------- S4 / S5: the restart holds what it held before
def test_a_restart_after_the_key_change_holds_the_same_bars_and_cut_over_byte_for_byte(tmp_path, monkeypatch):
    """S4: the engine before the change read (broker, symbol); the engine after it reads (account, symbol) with the
    legacy key under it. On a store written only before the change both warm starts give identical bars and cut-over,
    the saved cut-over (aux.db history_cutover, keyed by the old live key) included."""
    _legacy_store(tmp_path)
    before = _engine(tmp_path)
    saved = OWN_FROM - pd.Timedelta(days=1)
    with Database.open(str(tmp_path), "aux").write() as conn:        # a rebase saved under the old key
        conn.execute("INSERT INTO history_cutover (account_id, source, symbol, cutover_ns, updated_ns) "
                     "VALUES (?, ?, ?, ?, ?)", ("icm-demo", "icm", "XAUUSD", int(saved.value), 0))
    with monkeypatch.context() as m:                                  # the code before the change
        m.setattr(books, "live_key", lambda eng: (eng.cfg.broker_name, eng.cfg.symbol))
        m.setattr(books, "legacy_live_key", lambda eng: None)
        before.warm_start(LIVE_FROM)
    after = _engine(tmp_path)
    after.warm_start(LIVE_FROM)
    assert before.history.cutover == after.history.cutover == saved
    pd.testing.assert_frame_equal(before.bars_1m, after.bars_1m)
    assert len(after.bars_1m) > 0


def test_a_minute_on_both_keys_is_held_as_the_accounts_own_copy(tmp_path):
    store = _legacy_store(tmp_path)
    own = ticks_to_1m(TICKS[(TICKS["ts_utc"] >= LIVE_FROM - pd.Timedelta(hours=1)) & (TICKS["ts_utc"] < LIVE_FROM)])
    own = own.assign(bid_close=own["bid_close"] + 7.0, writer="b" * 32)
    store.append(LIVE_TABLE, own, source="icm-demo", symbol="XAUUSD")
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    held = eng.bars_1m.set_index("ts_utc")
    assert (held.loc[own["ts_utc"], "bid_close"].to_numpy() == own["bid_close"].to_numpy()).all()
    assert eng.history.cutover == OWN_FROM                           # the legacy minutes still set it


def test_the_writer_column_never_reaches_the_engines_bars(tmp_path):
    """S5: `_clean` keeps BAR_COLUMNS only; the stored writer (and the flags) stay in the store."""
    store = _legacy_store(tmp_path)
    own = ticks_to_1m(TICKS[(TICKS["ts_utc"] >= LIVE_FROM - pd.Timedelta(hours=1)) & (TICKS["ts_utc"] < LIVE_FROM)])
    store.append(LIVE_TABLE, own.assign(writer="b" * 32, filled=True), source="icm-demo", symbol="XAUUSD")
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    assert set(eng.bars_1m.columns) <= set(BAR_COLUMNS) and WRITER_COL not in eng.bars_1m.columns


def test_a_sticky_flagged_copy_keeps_its_own_writer_over_a_later_plain_copy(tmp_path):
    """Quant Q3: `Store.append(sticky=...)` keeps the flagged row whole: its writer goes with its flag and write
    time, never the later plain copy's writer."""
    store = Store(tmp_path / "data")
    row = ticks_to_1m(TICKS[(TICKS["ts_utc"] >= OWN_FROM) & (TICKS["ts_utc"] < OWN_FROM + pd.Timedelta(minutes=1))])
    flags = (REPLACED_COL, FILLED_COL)
    store.append(LIVE_TABLE, row.assign(replaced=True, writer="a" * 32), source="icm-demo", symbol="XAUUSD",
                 sticky=flags)
    store.append(LIVE_TABLE, row.assign(replaced=False, writer="c" * 32), source="icm-demo", symbol="XAUUSD",
                 sticky=flags)
    got = store.read(LIVE_TABLE, source="icm-demo", symbol="XAUUSD")
    assert len(got) == 1 and bool(got["replaced"].iloc[0]) and got[WRITER_COL].iloc[0] == "a" * 32


def test_the_bar_writer_without_a_boot_id_writes_no_writer_column(tmp_path):
    store = Store(tmp_path / "data")
    w = BarWriter(store, "icm-demo", "XAUUSD")
    w.put(ticks_to_1m(TICKS[(TICKS["ts_utc"] >= OWN_FROM) & (TICKS["ts_utc"] < OWN_FROM + pd.Timedelta(minutes=5))]))
    assert w.join(10)
    assert WRITER_COL not in store.read(LIVE_TABLE, source="icm-demo", symbol="XAUUSD").columns


# ---------------------------------------------------------------- the boot record and the dq events' writer
def test_each_engine_start_records_its_boot_id_and_whether_it_holds_the_writer_lock(tmp_path):
    eng = _engine(tmp_path, boot_id="b" * 32)
    at = pd.Timestamp("2025-03-13 06:00", tz="UTC")
    assert history.record_boot(eng, held=True, at=at)
    ev = Store(tmp_path / "data").read("dq_events", source="icm-demo", symbol="XAUUSD")
    assert list(ev["check"]) == [history.BOOT_CHECK] and list(ev["writer"]) == ["b" * 32]
    assert json.loads(ev["detail"].iloc[0]) == {"writer_lock": True} and ev["ts_utc"].iloc[0] == at
    assert not history.record_boot(_engine(tmp_path / "x"), held=True, at=at)       # no boot id: nothing recorded


def test_the_engines_dq_events_carry_its_boot_id_and_other_writers_rows_keep_their_columns():
    ev = [DQEvent(ts_utc=pd.Timestamp("2025-03-13", tz="UTC"), check="broker_bar_late", severity="warning",
                  detail="1 minute(s)")]
    assert list(events_frame(ev, writer="b" * 32)["writer"]) == ["b" * 32]
    assert list(events_frame(ev).columns) == ["ts_utc", "check", "severity", "detail"]


def test_an_exploration_candidate_records_the_deciding_engines_boot_id(tmp_path):
    from goldbot.research.exploration_data import record_candidate
    db = Database.open(str(tmp_path), "aux")
    record_candidate(db, candidate_id="c1", account_id="icm-probe", broker="icm", arm_id="A1", family="f",
                     style="scalp", tf="15m", side=1, decision_utc=pd.Timestamp("2025-03-13 10:00", tz="UTC"),
                     stop_dist=2.0, target_dist=3.0, max_hold_s=3600, taken=False, blocked_reason="x",
                     branch="blocked", mu=0.0, boot_id="b" * 32)
    with db.connection() as conn:
        assert conn.execute("SELECT boot_id FROM exploration_obs").fetchall() == [("b" * 32,)]


# ---------------------------------------------------------------- S1 / S2 / S3: the writer lock
_HOLDER = """
import sys, time
from goldbot.ops import engine_lock
got = engine_lock.hold_writer(sys.argv[1], "icm-demo")
print("held" if got.held else "not", flush=True)
time.sleep(120)
"""


def _holder(state: Path) -> subprocess.Popen[str]:
    p = subprocess.Popen([sys.executable, "-c", _HOLDER, str(state)], cwd=REPO, stdout=subprocess.PIPE, text=True)
    assert p.stdout is not None and p.stdout.readline().strip() == "held"
    return p


@pytest.mark.skipif(sys.platform == "win32", reason="flock: the brain only")
def test_a_second_engine_of_the_account_is_refused_naming_the_holders_pid_and_kill_9_frees_the_lock(tmp_path):
    """S2/S3: the exclusive writer lock names its holder; the kernel releases it when the holder dies, kill -9
    included (no pidfile or takeover rule), and the next start holds it."""
    p = _holder(tmp_path)
    try:
        got = engine_lock.hold_writer(tmp_path, "icm-demo")
        assert not got.held and got.holder_pid == p.pid and str(p.pid) in (got.refused or "")
        assert engine_lock.hold_engine(tmp_path, "icm-demo") is None        # the repair protocol's lock is separate
    finally:
        os.kill(p.pid, signal.SIGKILL)
        p.wait(10)
    got = engine_lock.hold_writer(tmp_path, "icm-demo")
    assert got.held and got.refused is None
    assert engine_lock.holder_pid(engine_lock.writer_lock_path(tmp_path, "icm-demo")) == os.getpid()


def test_without_flock_the_engine_starts_unproven_and_is_never_refused(tmp_path, monkeypatch):
    """S2 / quant Q6 (the Windows VPS has no flock): the lock is not taken, the start goes on, its rows unproven."""
    def no_flock(path: Path, exclusive: bool) -> int:
        raise ImportError("No module named 'fcntl'")
    monkeypatch.setattr(engine_lock, "_flock", no_flock)
    got = engine_lock.hold_writer(tmp_path, "icm-probe")
    assert not got.held and got.refused is None


def test_a_refused_duplicate_start_exits_for_a_restart_before_any_bridge_and_never_writes_the_holders_state(
        tmp_path, monkeypatch):
    """S1: never `start_refused` (it writes startup_error into the running engine's engine_<account>.json and the
    supervisor halts every real account); a journal line and a health WARN naming the holder's pid, an exit the
    service manager restarts (not 78), and no bridge client built (S2: the lock is taken first)."""
    from goldbot.execution import bridge as bridge_mod
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(accounts, "load_accounts", lambda path=None: {"icm-demo": ACC})
    monkeypatch.setattr(accounts, "bridge_endpoint", lambda aid: ("http://127.0.0.1:1", "t"))

    def no_bridge(*a: Any, **k: Any) -> None:
        raise AssertionError("a bridge client was built before the writer lock")
    monkeypatch.setattr(bridge_mod, "RemoteBroker", no_bridge)
    monkeypatch.setattr(engine_lock, "hold_writer", lambda state, aid: engine_lock.WriterHold(
        held=False, holder_pid=4242, refused="another engine of icm-demo holds its writer lock (pid 4242)"))
    state = tmp_path / "state"
    state.mkdir()
    (state / "engine_icm-demo.json").write_text(json.dumps({"account_id": "icm-demo", "equity": 1000.0}))
    with pytest.raises(SystemExit) as exc:
        run_mod.run_engine("icm-demo")
    assert exc.value.code == engine_service.ENGINE_DUPLICATE_EXIT != engine_service.ENGINE_DISABLED_EXIT
    assert json.loads((state / "engine_icm-demo.json").read_text()) == {"account_id": "icm-demo", "equity": 1000.0}
    now = pd.Timestamp.now("UTC")
    c = health.check_duplicate_engine(make_ctx(state, now=now), "icm-demo")
    assert c is not None and c.status == "warn" and "4242" in c.reason
    assert health._alerting(c.name, c.status)                                  # told (NOTIFY_ON_WARN)
    later = health.check_duplicate_engine(make_ctx(state, now=now + pd.Timedelta(hours=1)), "icm-demo")
    assert later is not None and later.status == "ok"
    assert health.check_duplicate_engine(make_ctx(tmp_path / "none", now=now), "icm-demo") is None
