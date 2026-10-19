"""Engine history continuity (brain 2026-10-19): a restarted engine must never decide on a window with a hole.

The store's minutes load at once (release before the cut-over, the broker's own minutes after); the tail and the holes
are filled from the broker's ticks on a background thread (the live bar builder), never delaying an exit; completed
minutes are written by a background writer to their own table (`bars_1m_live`); a decision frame whose window misses
more than 60 open minutes that no broker pull explains refuses entries for that timeframe (`history_gap:<tf>`)."""
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig, history
from goldbot.engine.history import LIVE_TABLE, BarWriter, open_gaps
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops.jobs import _bars
from goldbot.specialists import SPECIALISTS


class TickHistory:
    """A history client whose terminal holds `ticks` (copy_ticks_from semantics: the first n at or after since)."""

    def __init__(self, ticks: pd.DataFrame, fail: bool = False, gate: threading.Event | None = None):
        self.ticks, self.fail, self.gate, self.calls = ticks.reset_index(drop=True), fail, gate, 0

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(30)
        if self.fail:
            raise ConnectionError("bridge down")
        h = self.ticks
        return h[h["ts_utc"] >= since_utc.floor("s")].head(n).reset_index(drop=True)


def _engine(tmp_path: Path, broker: PaperBroker | None = None, **cfg: Any) -> Engine:
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                               data_root=str(tmp_path / "data"), **cfg),
                  broker or PaperBroker(equity=10_000), [spec], {"session_open": ConstantModel(p=0.65)})


def _feed(eng: Engine, ticks: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        out += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    return out


def _settle(eng: Engine) -> None:
    """Wait for the background fill and absorb it (the tick thread does this at the next bar close)."""
    f = eng.history.fill
    assert f is not None and f.done.wait(60)
    history.absorb(eng)
    if eng.history.writer is not None:
        assert eng.history.writer.join(30)


TICKS = synthetic_ticks("2025-03-03", "2025-03-15", ticks_per_minute=1, seed=8)
STORE_END = pd.Timestamp("2025-03-06", tz="UTC")       # the release bars end here: the hole runs to the restart
LIVE_FROM = pd.Timestamp("2025-03-13", tz="UTC")
FAST: dict[str, Any] = {"backfill_page_ticks": 2000}


def _seed_store(tmp_path: Path, end: pd.Timestamp = STORE_END) -> None:
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(TICKS[TICKS["ts_utc"] < end]), source="dukascopy")


# ------------------------------------------------------------------------------------------- the gap rule
def test_the_weekend_and_the_daily_break_are_not_history_gaps():
    bars = ticks_to_1m(TICKS)
    assert open_gaps(pd.DatetimeIndex(bars["ts_utc"]), DEFAULT_SESSIONS, max_open_min=60) == []


def test_two_hours_missing_in_session_are_a_history_gap():
    ts = pd.DatetimeIndex(ticks_to_1m(TICKS)["ts_utc"])
    hole = pd.Timestamp("2025-03-05 13:00", tz="UTC")
    holed = ts[(ts < hole) | (ts >= hole + pd.Timedelta(hours=2))]
    [g] = open_gaps(holed, DEFAULT_SESSIONS, max_open_min=60)
    assert g.start == hole and g.open_min == 120


def test_a_span_a_broker_pull_found_without_ticks_is_not_a_history_gap():
    ts = pd.DatetimeIndex(ticks_to_1m(TICKS)["ts_utc"])
    hole = pd.Timestamp("2025-03-05 13:00", tz="UTC")
    holed = ts[(ts < hole) | (ts >= hole + pd.Timedelta(hours=2))]
    assert open_gaps(holed, DEFAULT_SESSIONS, max_open_min=60, covered=[(hole, hole + pd.Timedelta(hours=2))]) == []


# ------------------------------------------------------------------------------------------- the background fill
def test_warm_start_fills_the_gap_from_the_broker_ticks_with_the_live_bar_builder(tmp_path):
    _seed_store(tmp_path)
    broker = TickHistory(TICKS[TICKS["ts_utc"] < LIVE_FROM])
    eng = _engine(tmp_path, **FAST)
    eng.warm_start(LIVE_FROM, broker)
    _settle(eng)
    expect = ticks_to_1m(TICKS[(TICKS["ts_utc"] >= STORE_END) & (TICKS["ts_utc"] < LIVE_FROM)], DEFAULT_SESSIONS)
    got = eng.bars_1m[eng.bars_1m["ts_utc"] >= STORE_END].reset_index(drop=True)
    pd.testing.assert_frame_equal(got[expect.columns], expect, check_dtype=False)
    assert open_gaps(pd.DatetimeIndex(eng.bars_1m["ts_utc"]), eng.sessions, max_open_min=60) == []
    assert broker.calls > 1                                        # paged
    kept = Store(tmp_path / "data").read(LIVE_TABLE, source="icm")
    assert len(kept) == len(expect)
    assert eng.history.backfill["bars"] == len(expect) and eng.history.backfill["error"] is None
    assert pd.Timestamp(eng.history.backfill["cutover"]) == expect["ts_utc"].iloc[0]


def test_an_exit_check_runs_while_the_fill_is_still_pulling(tmp_path, monkeypatch):
    _seed_store(tmp_path)
    gate = threading.Event()
    eng = _engine(tmp_path, **FAST)
    t0 = time.monotonic()
    eng.warm_start(LIVE_FROM, TickHistory(TICKS[TICKS["ts_utc"] < LIVE_FROM], gate=gate))
    assert time.monotonic() - t0 < 5                               # the store read only: the pull waits on its thread
    checks: list[pd.Timestamp] = []
    real = eng._stop_check

    def spy(t: Tick) -> None:
        checks.append(t.ts_utc)
        real(t)

    monkeypatch.setattr(eng, "_stop_check", spy)
    _feed(eng, TICKS[(TICKS["ts_utc"] >= LIVE_FROM) & (TICKS["ts_utc"] < LIVE_FROM + pd.Timedelta(hours=1))])
    assert eng.history.fill is not None and not eng.history.fill.done.is_set()
    assert len(checks) > 30                                        # every tick's exit check ran meanwhile
    gate.set()
    _settle(eng)


def test_live_minutes_before_the_fill_finishes_never_turn_the_hole_into_covered(tmp_path):
    """Re-review HIGH (first deploy, empty live table): live minutes arriving while the tail fill still pulls must not
    move the cut-over past the pulled tail, or the tail would be dropped yet counted covered (entries on the hole)."""
    _seed_store(tmp_path)
    gate = threading.Event()
    eng = _engine(tmp_path, **FAST)
    eng.warm_start(LIVE_FROM, TickHistory(TICKS[TICKS["ts_utc"] < LIVE_FROM], gate=gate))
    _feed(eng, TICKS[(TICKS["ts_utc"] >= LIVE_FROM) & (TICKS["ts_utc"] < LIVE_FROM + pd.Timedelta(minutes=30))])
    assert eng.history.cutover is not None and eng.history.cutover <= STORE_END + pd.Timedelta(minutes=1)
    gate.set()
    _settle(eng)
    filled = eng.bars_1m[(eng.bars_1m["ts_utc"] >= STORE_END) & (eng.bars_1m["ts_utc"] < LIVE_FROM)]
    assert len(filled) > 4 * 23 * 60                                # the hole's minutes were merged, not dropped
    assert open_gaps(pd.DatetimeIndex(eng.bars_1m["ts_utc"]), eng.sessions, max_open_min=60) == []   # no "covered"


def test_a_failed_fill_keeps_the_store_bars_and_refuses_entries_with_history_gap(tmp_path):
    _seed_store(tmp_path)
    eng = _engine(tmp_path, **FAST)
    assert eng.warm_start(LIVE_FROM, TickHistory(TICKS, fail=True)) > 0
    _settle(eng)
    assert eng.history.backfill["error"] == "ConnectionError"
    decisions = _feed(eng, TICKS[(TICKS["ts_utc"] >= LIVE_FROM) & (TICKS["ts_utc"] < LIVE_FROM + pd.Timedelta(hours=12))])
    assert decisions and {d["action"] for d in decisions} == {"history_gap:15m"}
    state = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    gap = state["history_gap"]["15m"]
    assert pd.Timestamp(gap["from"]) <= STORE_END and pd.Timestamp(gap["to"]) >= LIVE_FROM
    assert gap["open_min"] > 4 * 23 * 60                            # Thu 6th -> Thu 13th: five trading days
    assert state["history_backfill"]["error"] == "ConnectionError"


def test_a_continuous_window_decides_and_publishes_no_history_gap(tmp_path):
    _seed_store(tmp_path, LIVE_FROM)
    eng = _engine(tmp_path)                                        # no history client: nothing to fill, none needed
    eng.warm_start(LIVE_FROM)
    decisions = _feed(eng, TICKS[(TICKS["ts_utc"] >= LIVE_FROM) & (TICKS["ts_utc"] < LIVE_FROM + pd.Timedelta(days=2))])
    assert decisions and not any(d["action"].startswith("history_gap") for d in decisions)
    assert json.loads((tmp_path / "engine_icm-demo.json").read_text())["history_gap"] == {}


# ------------------------------------------------------------------------------------------- storing, feeds
def test_completed_live_minutes_are_stored_so_a_restart_has_no_hole(tmp_path):
    _seed_store(tmp_path, LIVE_FROM)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    until = LIVE_FROM + pd.Timedelta(hours=6)
    _feed(eng, TICKS[(TICKS["ts_utc"] >= LIVE_FROM) & (TICKS["ts_utc"] < until)])
    assert eng.history.writer is not None and eng.history.writer.join(30)
    kept = Store(tmp_path / "data").read(LIVE_TABLE, source="icm")
    assert len(kept) and pd.Timestamp(kept["ts_utc"].min()) >= LIVE_FROM
    assert pd.Timestamp(kept["ts_utc"].max()) >= until - pd.Timedelta(minutes=16)
    again = _engine(tmp_path)
    again.warm_start(until)
    assert open_gaps(pd.DatetimeIndex(again.bars_1m["ts_utc"]), again.sessions, max_open_min=30) == []


def test_a_locked_store_never_stalls_the_tick_thread(tmp_path, monkeypatch):
    _seed_store(tmp_path, LIVE_FROM)
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    gate = threading.Event()
    real = Store.append

    def blocked(self, table, df, **kw):
        if table == LIVE_TABLE:
            gate.wait(60)
        return real(self, table, df, **kw)

    monkeypatch.setattr(Store, "append", blocked)
    t0 = time.monotonic()
    _feed(eng, TICKS[(TICKS["ts_utc"] >= LIVE_FROM) & (TICKS["ts_utc"] < LIVE_FROM + pd.Timedelta(hours=1))])
    assert time.monotonic() - t0 < 30                              # the writer waits, the ticks did not
    gate.set()
    assert eng.history.writer is not None and eng.history.writer.join(30)
    assert len(Store(tmp_path / "data").read(LIVE_TABLE, source="icm")) > 0


def test_a_full_writer_queue_drops_its_oldest_batch_with_a_log(tmp_path, monkeypatch, caplog):
    gate = threading.Event()
    store = Store(tmp_path / "data")
    monkeypatch.setattr(store, "append", lambda *a, **k: gate.wait(60))
    w = BarWriter(store, "icm", "XAUUSD", maxsize=2)
    bars = ticks_to_1m(TICKS.head(300))
    with caplog.at_level(logging.WARNING, logger="goldbot.engine.history"):
        for i in range(6):
            w.put(bars.iloc[i:i + 1])
    assert w.dropped >= 3 and w.q.qsize() <= 2 and "oldest batch" in caplog.text
    gate.set()
    assert w.join(30)


def test_research_reads_of_bars_1m_and_warm_start_see_one_feed_per_minute(tmp_path):
    # both feeds stored: bars_1m keeps only the release (jobs._bars reads it unfiltered); warm_start takes the release
    # before the cut-over (the first broker minute) and the traded broker's minutes from it on, never another broker's
    store = Store(tmp_path / "data")
    rel = ticks_to_1m(TICKS[TICKS["ts_utc"] < LIVE_FROM])
    store.append("bars_1m", rel, source="dukascopy")
    cut = pd.Timestamp("2025-03-11", tz="UTC")
    mine = ticks_to_1m(TICKS[(TICKS["ts_utc"] >= cut) & (TICKS["ts_utc"] < LIVE_FROM)].assign(bid=lambda d: d["bid"] + 1, ask=lambda d: d["ask"] + 1))
    store.append(LIVE_TABLE, mine, source="icm")
    other = mine.assign(bid_close=mine["bid_close"] + 50)
    store.append(LIVE_TABLE, other, source="pepperstone")

    class Ctx:
        pass

    ctx = Ctx()
    ctx.store = store  # type: ignore[attr-defined]
    read = _bars(ctx, "1m", rel["ts_utc"].iloc[0], LIVE_FROM)   # type: ignore[arg-type]
    assert len(read) == len(rel) and read["ts_utc"].is_unique
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM)
    b = eng.bars_1m
    assert b["ts_utc"].is_unique
    before, after = b[b["ts_utc"] < cut], b[b["ts_utc"] >= cut]
    assert len(before) == (rel["ts_utc"] < cut).sum()
    pd.testing.assert_series_equal(after["bid_close"].reset_index(drop=True), mine["bid_close"].reset_index(drop=True),
                                   check_names=False)
    assert pd.Timestamp(eng.history.backfill["cutover"]) == mine["ts_utc"].iloc[0]


def test_warm_start_without_a_store_pulls_nothing(tmp_path):
    broker = TickHistory(TICKS)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)),
                 PaperBroker(equity=10_000), [], {})
    assert eng.warm_start(LIVE_FROM, broker) == 0 and broker.calls == 0 and eng.history.fill is None
