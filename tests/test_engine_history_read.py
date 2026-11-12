"""A store read of the engine's history fails closed (backlog 155; found by the duckdb 2.0 test of Dependabot #312,
where every `Store.read` with a ts filter raised a BinderException).

Design rule (engine/history.py): a history read that raises, or one that returns fewer 1m bars than the warm-start
minimum, refuses every entry with a journalled reason (`history_read:<tf>` / `history_short:<tf>`, and RiskGate's
`data_quality_error` for every other entry path) and fails the engine health check; exits and position management
never look at it. A later re-read that succeeds lifts it; a normal read changes nothing."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig, history, key_zones
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import health
from goldbot.specialists import SPECIALISTS
from tests.test_health import make_ctx

TICKS = synthetic_ticks("2025-03-03", "2025-03-15", ticks_per_minute=1, seed=8)
LIVE_FROM = pd.Timestamp("2025-03-13", tz="UTC")


def _engine(tmp_path: Path, threaded: bool = False, **cfg: Any) -> Engine:
    """threaded=False runs the background store reads (`history.StoreRead`) on the calling thread, so a re-read is
    merged at the close that starts it; threaded=True keeps the production thread."""
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                              data_root=str(tmp_path / "data"), **cfg),
                 PaperBroker(equity=10_000), [spec], {"session_open": ConstantModel(p=0.65)})
    if not threaded:
        eng.history.spawn = lambda fn: fn()
    return eng


def _seed(tmp_path: Path, start: pd.Timestamp | None = None, end: pd.Timestamp = LIVE_FROM) -> None:
    t = TICKS[(TICKS["ts_utc"] < end) & ((TICKS["ts_utc"] >= start) if start is not None else True)]
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(t), source="dukascopy")


def _feed(eng: Engine, lo: pd.Timestamp, hi: pd.Timestamp) -> list[dict]:
    t = TICKS[(TICKS["ts_utc"] >= lo) & (TICKS["ts_utc"] < hi)]
    out: list[dict] = []
    for ts, bid, ask in zip(t["ts_utc"], t["bid"].to_numpy(float), t["ask"].to_numpy(float)):
        out += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    return out


class Broken:
    """Store.read raising on every ts-filtered read while `on` (duckdb 2.0: TIMESTAMPTZ_NS vs TIMESTAMP WITH TIME
    ZONE), counting the reads it refused."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.on, self.refused, real = True, 0, Store.read

        def read(store: Store, table: str, **kw: Any) -> pd.DataFrame:
            if self.on and (kw.get("start") is not None or kw.get("end") is not None):
                self.refused += 1
                raise RuntimeError("Binder Error: Cannot compare TIMESTAMP_NS WITH TIME ZONE and TIMESTAMP")
            return real(store, table, **kw)
        monkeypatch.setattr(Store, "read", read)


class TickHistory:
    """A history client whose terminal holds `ticks` (copy_ticks_from semantics)."""

    def __init__(self, ticks: pd.DataFrame):
        self.ticks = ticks.reset_index(drop=True)

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        h = self.ticks
        return h[h["ts_utc"] >= since_utc.floor("s")].head(n).reset_index(drop=True)


def _settle(eng: Engine) -> None:
    f = eng.history.fill
    assert f is not None and f.done.wait(60)
    history.absorb(eng)


def _state(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "engine_icm-demo.json").read_text())


# ------------------------------------------------------------------------------------------- a read that raises
def test_a_warm_start_whose_store_read_raises_starts_the_engine_and_blocks_entries(tmp_path, monkeypatch):
    _seed(tmp_path)
    broken = Broken(monkeypatch)
    eng = _engine(tmp_path)
    assert eng.warm_start(LIVE_FROM) == 0                      # no crash: the engine starts, so its exits run
    assert broken.refused >= 1
    assert "history_read" in eng._dq_blocking()               # RiskGate refuses every entry (data_quality_error)
    assert eng.history.backfill["read_error"] == "RuntimeError"


def test_entries_are_refused_history_read_while_the_store_cannot_be_read(tmp_path, monkeypatch):
    _seed(tmp_path)
    Broken(monkeypatch)
    eng = _engine(tmp_path, backfill_page_ticks=2000)
    eng.warm_start(LIVE_FROM, TickHistory(TICKS[TICKS["ts_utc"] < LIVE_FROM]))   # the broker refills ten days
    _settle(eng)
    assert len(eng.bars_1m) > 10_000
    decisions = _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=12))
    assert decisions and {d["action"] for d in decisions} == {"history_read:15m"}
    assert eng.state.dq_error and not eng.open
    st = _state(tmp_path)
    assert st["history_backfill"]["read_error"] == "RuntimeError" and "history_read" in st["dq_checks"]


def test_a_failed_history_read_never_stops_an_open_trade_s_time_exit(tmp_path, monkeypatch):
    _seed(tmp_path)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    opened = LIVE_FROM + pd.Timedelta(hours=33)                # the 03-14 08:30 session_open entry
    _feed(eng, LIVE_FROM, opened)
    assert eng.open, "the healthy engine opened a trade"
    (pid, tr), = list(eng.open.items())[:1]
    Broken(monkeypatch)
    history.read_failed(eng, opened, RuntimeError("store broken"))
    tr.bars_held = tr.max_bars - 1                             # its time barrier is the next close
    decisions = _feed(eng, opened, opened + pd.Timedelta(hours=1))
    assert pid not in eng.open and pid not in {p.position_id for p in eng.broker.positions()}
    assert not any(d["action"].startswith("executed") for d in decisions)
    assert eng._dq_blocking() == {"history_read"}


def test_a_re_read_that_succeeds_restores_the_history_and_lifts_the_refusal(tmp_path, monkeypatch):
    _seed(tmp_path)
    broken = Broken(monkeypatch)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    broken.on = False                                          # the store reads again (a fix, a restored file)
    decisions = _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=36))
    assert eng.history.backfill["read_error"] is None and not eng._dq_blocking()
    assert len(eng.bars_1m) > 10_000                           # the release minutes are back in the window
    assert decisions and any(not d["action"].startswith("history") for d in decisions)


def test_a_store_read_that_keeps_failing_is_retried_and_stays_refused(tmp_path, monkeypatch):
    _seed(tmp_path)
    broken = Broken(monkeypatch)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    first = broken.refused
    _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=2))
    assert broken.refused > first                              # re-read every REFILL_EVERY of engine clock
    assert "history_read" in eng._dq_blocking()


def test_a_failed_release_reload_blocks_entries_like_a_failed_warm_start(tmp_path, monkeypatch):
    _seed(tmp_path)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    assert not eng._dq_blocking()
    hole = pd.Timestamp("2025-03-11 13:00", tz="UTC")          # two in-session hours the release should heal
    b = eng.bars_1m
    eng.bars_1m = b[(b["ts_utc"] < hole) | (b["ts_utc"] >= hole + pd.Timedelta(hours=2))].reset_index(drop=True)
    eng.history.cutover = LIVE_FROM
    eng.history.gaps = {"15m": {"from": hole.isoformat()}}
    broken = Broken(monkeypatch)
    history.after_close(eng, LIVE_FROM + pd.Timedelta(hours=1))
    assert broken.refused >= 1 and "history_read" in eng._dq_blocking()


def test_the_engine_health_check_fails_and_names_the_failed_history_read(tmp_path, monkeypatch):
    _seed(tmp_path)
    Broken(monkeypatch)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(minutes=20))
    c = health.check_engine(make_ctx(tmp_path), "icm-demo")
    assert c.status == "fail" and "history_read" in c.reason and "store read of the engine's history failed" in c.reason


# ------------------------------------------------------------------------------------------- a short read
def test_a_short_store_read_refuses_entries_history_short_until_the_window_holds_the_minimum(tmp_path):
    _seed(tmp_path, start=LIVE_FROM - pd.Timedelta(days=2))   # ~2 800 minutes stored
    eng = _engine(tmp_path, history_min_bars=4000)
    eng.warm_start(LIVE_FROM)
    assert "history_short" in eng._dq_blocking()
    short = _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=12))
    assert short and {d["action"] for d in short} == {"history_short:15m"}
    shown = _state(tmp_path)["history_backfill"]["read_short"]
    assert shown["min"] == 4000 and 2800 < shown["bars"] < 4000      # the stored minutes and the live ones since
    later = _feed(eng, LIVE_FROM + pd.Timedelta(hours=12), LIVE_FROM + pd.Timedelta(hours=40))
    assert not eng._dq_blocking() and _state(tmp_path)["history_backfill"]["read_short"] is None
    assert any(not d["action"].startswith("history") for d in later)       # the live minutes reached the minimum


def test_a_short_read_on_a_failed_store_is_a_read_error_not_a_short_read(tmp_path, monkeypatch):
    _seed(tmp_path)
    Broken(monkeypatch)
    eng = _engine(tmp_path, history_min_bars=4000)
    eng.warm_start(LIVE_FROM)
    assert eng._dq_blocking() == {"history_read"}


# ------------------------------------------------------------------------------------------- a normal read
def test_a_normal_store_read_with_the_minimum_set_changes_nothing(tmp_path):
    _seed(tmp_path)
    eng = _engine(tmp_path, history_min_bars=4000)
    assert eng.warm_start(LIVE_FROM) > 10_000
    assert not eng._dq_blocking()
    decisions = _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=36))
    assert decisions and not any(d["action"].startswith("history") for d in decisions)
    hb = _state(tmp_path)["history_backfill"]
    assert hb["read_error"] is None and hb["read_short"] is None


def test_the_other_books_never_carry_the_gold_warm_start_minimum():
    from goldbot.engine.books import BookSpec, book_cfg
    cfg = EngineConfig(account_id="icm-probe", broker_name="icm", history_min_bars=28_800)
    spec = BookSpec(key="EURUSD", symbol="EURUSD", store_symbol="EURUSD", magic_block=1, digits=5, point=0.00001,
                    contract_size=100_000.0)
    assert book_cfg(cfg, spec).history_min_bars == 0          # their warm-up is book_backfill's (books.warming)


def test_the_service_passes_the_warm_start_minimum_to_weekday_gold_only():
    from goldbot.config import load_settings
    from goldbot.ops.services.engine import warm_start_min_bars
    s = load_settings()
    assert s.engine.warm_start_min_bars == 120 * 240           # 120 bars of 4h, the longest decision timeframe
    weekday = type("A", (), {"weekend_only": False, "sessions": "broker"})()
    weekend = type("A", (), {"weekend_only": True, "sessions": "always_open"})()
    assert warm_start_min_bars(s, weekday) == 28_800 and warm_start_min_bars(s, weekend) == 0


# ------------------------------------------------------------------------------------------- key zones (display)
def test_an_unreadable_key_zone_cache_is_read_again_at_the_next_build(tmp_path, monkeypatch):
    """Display only (engine/key_zones.py): a failed cache read never blocks entries, but it is no longer final: the
    next build reads the cache again, so one bad read does not leave the chart on the engine's short history until a
    restart (the duckdb 2.0 test saw history_days 52.4 instead of > 700)."""
    broken = Broken(monkeypatch)
    w = key_zones.KeyZoneWorker("icm-demo", "XAUUSD", Store(tmp_path / "data"))
    w.refresh(LIVE_FROM)
    assert broken.refused == 1 and not w.loaded
    broken.on = False
    w.refresh(LIVE_FROM)
    assert w.loaded


# ------------------------------------------------------------------------------------------- review round 1 (R123)
def test_a_read_that_recovers_still_refuses_entries_when_the_window_is_short(tmp_path, monkeypatch):
    """HIGH (fails open): the warm start skipped the short judgement because its read raised; the re-read that
    succeeds must judge it, or entries open on ~2 800 minutes with history_min_bars 4 000."""
    _seed(tmp_path, start=LIVE_FROM - pd.Timedelta(days=2))
    broken = Broken(monkeypatch)
    eng = _engine(tmp_path, history_min_bars=4000)
    eng.warm_start(LIVE_FROM)
    broken.on = False
    _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=1))
    assert eng.history.read_error is None and len(eng.bars_1m) < 4000
    assert eng._dq_blocking() == {"history_short"}


def test_a_re_read_whose_merge_raises_keeps_entries_refused(tmp_path, monkeypatch):
    _seed(tmp_path)
    broken = Broken(monkeypatch)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    broken.on = False

    def boom(*a: Any, **k: Any) -> bool:
        raise MemoryError("merge failed")
    monkeypatch.setattr(history, "_merge_read", boom, raising=False)
    assert history.reread(eng, LIVE_FROM + pd.Timedelta(hours=1)) is False
    assert eng.history.read_error == "MemoryError" and "history_read" in eng._dq_blocking()


def test_the_warm_start_minimum_can_never_exceed_the_bars_the_engine_keeps():
    from pydantic import ValidationError

    from goldbot.config import EngineSettings
    keep = EngineConfig(account_id="icm-demo", broker_name="icm").max_bars_in_memory
    assert EngineSettings(warm_start_min_bars=keep).warm_start_min_bars == keep
    with pytest.raises(ValidationError):
        EngineSettings(warm_start_min_bars=keep + 1)            # entries refused for ever


def test_a_time_exit_fires_on_a_window_below_the_bar_close_floor(tmp_path):
    """Safety item 7: below the 400-bar floor after a failed history read (no broker fill) an open trade's time exit
    still runs at its close, not ~400 minutes later."""
    eng, pid, opened = _below_the_floor(tmp_path)
    history.read_failed(eng, opened, RuntimeError("store broken"))
    _feed(eng, opened, opened + pd.Timedelta(minutes=20))
    assert pid not in eng.open and pid not in {p.position_id for p in eng.broker.positions()}


def test_a_healthy_window_below_the_bar_close_floor_also_runs_the_time_exit(tmp_path):
    """Backlog 160 (superseding the R123 carve-out): with no failed or short read the floor no longer pauses the
    count either; the time exit fires at the same close as after a failed read."""
    eng, pid, opened = _below_the_floor(tmp_path)
    assert history.hold(eng) is None
    _feed(eng, opened, opened + pd.Timedelta(minutes=20))
    assert pid not in eng.open and pid not in {p.position_id for p in eng.broker.positions()}


def _below_the_floor(tmp_path: Path) -> tuple[Engine, int, pd.Timestamp]:
    """A healthy engine with an open trade one bar from its time barrier, its window cut below the 400-bar floor."""
    _seed(tmp_path)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    opened = LIVE_FROM + pd.Timedelta(hours=33)
    _feed(eng, LIVE_FROM, opened)
    (pid, tr), = list(eng.open.items())[:1]
    eng.bars_1m = eng.bars_1m.tail(100).reset_index(drop=True)  # what the engine holds after a failed read
    eng._resamplers, eng._ctx_cache, eng._bars_clean = {}, {}, None
    tr.bars_held = tr.max_bars - 1
    return eng, pid, opened


def test_a_slow_re_read_is_logged_with_its_duration(tmp_path, monkeypatch, caplog):
    _seed(tmp_path)
    broken = Broken(monkeypatch)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    broken.on = False
    monkeypatch.setattr(history, "REREAD_WARN_S", 0.0, raising=False)
    with caplog.at_level("WARNING", logger="goldbot.engine.history"):
        history.reread(eng, LIVE_FROM + pd.Timedelta(hours=1))
    assert any("re-read took" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------------- backlog 157: the re-read off the tick thread
HOLE = pd.Timestamp("2025-03-11 13:00", tz="UTC")


class Hung:
    """Store.read blocking every ts-filtered read until `release` is set (a hung disk or a locked store), recording
    the thread each read ran on; `entered` is set once a read is blocked. fail=True: the released read raises."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, fail: bool = False) -> None:
        self.release, self.entered = threading.Event(), threading.Event()
        self.threads: list[str] = []
        real = Store.read

        def read(store: Store, table: str, **kw: Any) -> pd.DataFrame:
            if kw.get("start") is not None or kw.get("end") is not None:
                self.threads.append(threading.current_thread().name)
                self.entered.set()
                assert self.release.wait(60), "the test never released the hung read"
                if fail:
                    raise RuntimeError("store broken")
            return real(store, table, **kw)
        monkeypatch.setattr(Store, "read", read)

    def reads(self) -> int:
        return len(self.threads)


def _failed_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **cfg: Any) -> tuple[Engine, Broken]:
    """A threaded engine whose warm-start read raised (history_read holds)."""
    _seed(tmp_path)
    broken = Broken(monkeypatch)
    eng = _engine(tmp_path, threaded=True, **cfg)
    eng.warm_start(LIVE_FROM)
    assert eng._dq_blocking() == {"history_read"}
    return eng, broken


def _holed(tmp_path: Path) -> Engine:
    """A threaded engine whose window lacks two release hours the store holds (reload_release heals them)."""
    _seed(tmp_path)
    eng = _engine(tmp_path, threaded=True)
    eng.warm_start(LIVE_FROM)
    b = eng.bars_1m
    eng.bars_1m = b[(b["ts_utc"] < HOLE) | (b["ts_utc"] >= HOLE + pd.Timedelta(hours=2))].reset_index(drop=True)
    eng.history.cutover = LIVE_FROM
    eng.history.gaps = {"15m": {"from": HOLE.isoformat()}}
    return eng


def test_a_hung_store_re_read_never_stalls_the_ticks_and_keeps_entries_refused(tmp_path, monkeypatch):
    eng, _ = _failed_start(tmp_path, monkeypatch)
    hung = Hung(monkeypatch)
    try:
        t0 = time.monotonic()
        _feed(eng, LIVE_FROM, LIVE_FROM + pd.Timedelta(hours=3))   # six REFILL_EVERY; inline, each would hang 60 s
        assert time.monotonic() - t0 < 30
        assert hung.entered.is_set() and hung.threads[0] != threading.current_thread().name
        assert hung.reads() == 1                       # one read in flight at a time: no second read is started
        assert history.hold(eng) == "history_read" and eng.history.backfill["read_error"] == "RuntimeError"
    finally:
        hung.release.set()


def test_a_hung_re_read_is_logged_once_and_entries_stay_refused(tmp_path, monkeypatch, caplog):
    eng, _ = _failed_start(tmp_path, monkeypatch)
    hung = Hung(monkeypatch)
    monkeypatch.setattr(history, "READ_HUNG_S", 0.0, raising=False)
    try:
        with caplog.at_level("ERROR", logger="goldbot.engine.history"):
            history.reread(eng, LIVE_FROM + pd.Timedelta(hours=1))
            assert hung.entered.wait(30)
            for k in range(2, 6):
                history.reread(eng, LIVE_FROM + pd.Timedelta(hours=k))
        hung_logs = [r for r in caplog.records if "has not returned" in r.getMessage()]
        assert len(hung_logs) == 1 and "entries stay refused" in hung_logs[0].getMessage()
        assert hung.reads() == 1 and eng._dq_blocking() == {"history_read"}
    finally:
        hung.release.set()


def test_a_finished_re_read_lifts_the_refusal_only_when_the_tick_thread_merges_it(tmp_path, monkeypatch):
    eng, broken = _failed_start(tmp_path, monkeypatch)
    broken.on = False
    merged_on: list[str] = []
    real = history._merge_read

    def spy(e: Engine, rel: pd.DataFrame, own: pd.DataFrame) -> bool:
        merged_on.append(threading.current_thread().name)
        return real(e, rel, own)
    monkeypatch.setattr(history, "_merge_read", spy)
    hung = Hung(monkeypatch)                           # held until released, then the real (now working) read
    t = LIVE_FROM + pd.Timedelta(hours=1)
    history.reread(eng, t)                             # starts the read
    _finish(hung, eng, "reread")                       # finished on its own thread ...
    assert not merged_on and eng.bars_1m.empty and eng._dq_blocking() == {"history_read"}   # ... nothing merged yet
    assert history.reread(eng, t + pd.Timedelta(minutes=15)) is True
    assert merged_on == [threading.current_thread().name]
    assert len(eng.bars_1m) > 10_000 and not eng._dq_blocking()


def test_a_threaded_re_read_that_recovers_re_judges_a_short_window(tmp_path, monkeypatch):
    _seed(tmp_path, start=LIVE_FROM - pd.Timedelta(days=2))
    broken = Broken(monkeypatch)
    eng = _engine(tmp_path, threaded=True, history_min_bars=4000)
    eng.warm_start(LIVE_FROM)
    broken.on = False
    hung = Hung(monkeypatch)
    t = LIVE_FROM + pd.Timedelta(hours=1)
    history.reread(eng, t)
    _finish(hung, eng, "reread")
    assert eng._dq_blocking() == {"history_read"}
    history.reread(eng, t + pd.Timedelta(minutes=15))
    assert eng.history.read_error is None and eng._dq_blocking() == {"history_short"}


def test_a_threaded_re_read_that_raises_keeps_the_refusal_and_is_tried_again(tmp_path, monkeypatch):
    eng, _ = _failed_start(tmp_path, monkeypatch)
    hung = Hung(monkeypatch, fail=True)
    t = LIVE_FROM + pd.Timedelta(hours=1)
    history.reread(eng, t)
    _finish(hung, eng, "reread")
    history.reread(eng, t + pd.Timedelta(minutes=15))  # collected: still failing
    assert eng._dq_blocking() == {"history_read"} and "reread" not in eng.history.reads
    history.reread(eng, t + pd.Timedelta(minutes=30))  # within REFILL_EVERY of the failure: not read
    history.reread(eng, t + pd.Timedelta(minutes=45))  # REFILL_EVERY after it: read again
    deadline = time.monotonic() + 30
    while hung.reads() < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert hung.reads() == 2


def _finish(hung: Hung, eng: Engine, kind: str) -> None:
    """Let the read `hung` holds return and wait until its thread is done (not yet merged)."""
    assert hung.entered.wait(30)
    hung.release.set()
    assert eng.history.reads[kind].done.wait(30)


def test_an_open_trade_s_time_exit_fires_while_the_re_read_hangs(tmp_path, monkeypatch):
    _seed(tmp_path)
    eng = _engine(tmp_path, threaded=True)
    eng.warm_start(LIVE_FROM)
    opened = LIVE_FROM + pd.Timedelta(hours=33)
    _feed(eng, LIVE_FROM, opened)
    (pid, tr), = list(eng.open.items())[:1]
    hung = Hung(monkeypatch)
    history.read_failed(eng, opened - history.REFILL_EVERY, RuntimeError("store broken"))   # a re-read is due
    tr.bars_held = tr.max_bars - 1
    try:
        t0 = time.monotonic()
        _feed(eng, opened, opened + pd.Timedelta(hours=1))
        assert time.monotonic() - t0 < 30               # inline, the hung read would hold the ticks 60 s
        assert hung.entered.is_set() and hung.threads[0] != threading.current_thread().name
        assert pid not in eng.open and pid not in {p.position_id for p in eng.broker.positions()}
    finally:
        hung.release.set()


def test_a_hung_release_reload_never_stalls_the_close_and_one_runs_at_a_time(tmp_path, monkeypatch):
    eng = _holed(tmp_path)
    hung = Hung(monkeypatch)
    try:
        for k in range(1, 4):                          # REFILL_EVERY apart: a reload is due at every one
            history.after_close(eng, LIVE_FROM + k * history.REFILL_EVERY)
        assert hung.entered.wait(30) and hung.reads() == 1
        assert hung.threads[0] != threading.current_thread().name
        assert not eng._dq_blocking()                  # a hole is history_gap's to refuse, not a failed read
    finally:
        hung.release.set()
    assert eng.history.reads["release"].done.wait(30)
    n = len(eng.bars_1m)
    history.after_close(eng, LIVE_FROM + 4 * history.REFILL_EVERY)    # merged on the tick thread at the next close
    assert len(eng.bars_1m) == n + 120


def test_a_release_read_that_fails_on_its_thread_refuses_entries_at_the_next_close(tmp_path, monkeypatch):
    eng = _holed(tmp_path)
    hung = Hung(monkeypatch, fail=True)
    history.after_close(eng, LIVE_FROM + pd.Timedelta(hours=1))
    _finish(hung, eng, "release")
    assert not eng._dq_blocking()                      # judged only when the tick thread collects it
    history.after_close(eng, LIVE_FROM + pd.Timedelta(hours=1, minutes=15))
    assert "history_read" in eng._dq_blocking()


def test_release_minutes_are_never_merged_past_a_cut_over_moved_back_while_they_were_read(tmp_path, monkeypatch):
    """A rebase can move the cut-over back while a release read runs: the merge keeps only the release minutes
    before the cut-over it finds at the close (one feed per span, one cut-over)."""
    eng = _holed(tmp_path)
    hung = Hung(monkeypatch)
    history.after_close(eng, LIVE_FROM + pd.Timedelta(hours=1))
    _finish(hung, eng, "release")
    eng.history.cutover = HOLE + pd.Timedelta(minutes=30)
    n = len(eng.bars_1m)
    history.after_close(eng, LIVE_FROM + pd.Timedelta(hours=1, minutes=15))
    assert len(eng.bars_1m) == n + 30


# ----------------------------------------------------------------------------------- review of R127 (LOW findings)
def test_a_re_read_started_before_a_later_release_failure_never_lifts_that_failure(tmp_path, monkeypatch):
    """Code review LOW 1: re-read starts at T, the release reload fails at T+15m, the re-read returns fine at T+30m:
    history_read was set after the re-read began, so the re-read cannot vouch for it."""
    eng, broken = _failed_start(tmp_path, monkeypatch)
    broken.on = False
    hung = Hung(monkeypatch)
    t = LIVE_FROM + pd.Timedelta(hours=1)
    history.reread(eng, t)
    assert hung.entered.wait(30)
    history.read_failed(eng, t + pd.Timedelta(minutes=15), RuntimeError("release reload failed"))
    hung.release.set()
    assert eng.history.reads["reread"].done.wait(30)
    history.reread(eng, t + pd.Timedelta(minutes=30))
    assert len(eng.bars_1m) > 10_000                     # its minutes are merged ...
    assert eng._dq_blocking() == {"history_read"}        # ... but the later failure stands


class _Stop(BaseException):
    """A non-Exception ending a read's thread (no result, no error)."""


def _stopping(monkeypatch: pytest.MonkeyPatch, eng: Engine) -> None:
    def read(store: Store, table: str, **kw: Any) -> pd.DataFrame:
        raise _Stop()
    monkeypatch.setattr(Store, "read", read)

    def spawn(fn: Any) -> None:                          # the thread dies on _Stop; `done` is still set
        try:
            fn()
        except _Stop:
            pass
    eng.history.spawn = spawn


def test_a_release_read_whose_thread_died_without_a_result_fails_closed(tmp_path, monkeypatch):
    """Trading-safety LOW 2: a read thread ended by a BaseException leaves no result and no error: fail closed."""
    eng = _holed(tmp_path)
    _stopping(monkeypatch, eng)
    started: list[Any] = []
    run = eng.history.spawn
    eng.history.spawn = started.append                   # held: collected at a later close
    history.after_close(eng, LIVE_FROM + pd.Timedelta(hours=1))
    run(started[0])                                      # the thread dies; `done` is set, no result, no error
    eng.history.gaps = {}                                # the later close collects it on the no-reload path
    history.after_close(eng, LIVE_FROM + pd.Timedelta(hours=1, minutes=15))
    assert "history_read" in eng._dq_blocking() and "release" not in eng.history.reads


def test_a_re_read_whose_thread_died_without_a_result_keeps_entries_refused(tmp_path, monkeypatch):
    eng, _ = _failed_start(tmp_path, monkeypatch)
    _stopping(monkeypatch, eng)
    history.reread(eng, LIVE_FROM + pd.Timedelta(hours=1))
    assert eng._dq_blocking() == {"history_read"} and "reread" not in eng.history.reads
