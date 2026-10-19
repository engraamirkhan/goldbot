"""Engine history continuity (brain 2026-10-19): a restarted engine must never decide on a window with a hole.

warm_start backfills the store -> now gap from the broker's ticks (the live bar builder), completed live 1m bars are
persisted so a restart never reopens the hole, and a decision frame whose window still has a hole longer than the
market's normal closures refuses entries for that timeframe (`history_gap:<tf>`), exits unaffected."""
import json
from pathlib import Path

import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.engine.history import live_source, open_gaps
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.specialists import SPECIALISTS


class TickHistoryBroker(PaperBroker):
    """A paper broker whose terminal holds `history` ticks (copy_ticks_from semantics: first n at or after since)."""

    def __init__(self, history: pd.DataFrame, fail: bool = False):
        super().__init__(equity=10_000)
        self.history, self.fail, self.calls = history.reset_index(drop=True), fail, 0

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        self.calls += 1
        if self.fail:
            raise ConnectionError("bridge down")
        h = self.history
        return h[h["ts_utc"] >= since_utc.floor("s")].head(n).reset_index(drop=True)


def _engine(tmp_path: Path, broker: PaperBroker, **cfg) -> Engine:
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                               data_root=str(tmp_path / "data"), **cfg),
                  broker, [spec], {"session_open": ConstantModel(p=0.65)})


def _feed(eng: Engine, ticks: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        out += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    return out


TICKS = synthetic_ticks("2025-03-03", "2025-03-15", ticks_per_minute=1, seed=8)
STORE_END = pd.Timestamp("2025-03-06", tz="UTC")       # the release bars end here: the hole runs to the restart
LIVE_FROM = pd.Timestamp("2025-03-13", tz="UTC")


def _seed_store(tmp_path: Path) -> None:
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(TICKS[TICKS["ts_utc"] < STORE_END]), source="dukascopy")


def test_the_weekend_and_the_daily_break_are_not_history_gaps():
    bars = ticks_to_1m(TICKS)
    assert open_gaps(pd.DatetimeIndex(bars["ts_utc"]), DEFAULT_SESSIONS, max_open_min=60) == []


def test_two_missing_trading_days_are_a_history_gap_with_their_open_minutes_counted():
    bars = ticks_to_1m(TICKS)
    ts = pd.DatetimeIndex(bars["ts_utc"])
    holed = ts[(ts < STORE_END) | (ts >= STORE_END + pd.Timedelta(days=2))]
    gaps = open_gaps(holed, DEFAULT_SESSIONS, max_open_min=60)
    assert len(gaps) == 1
    g = gaps[0]
    assert g.start <= STORE_END and g.end >= STORE_END + pd.Timedelta(days=2)
    assert 2 * 22 * 60 <= g.open_min <= 2 * 24 * 60


def test_warm_start_backfills_the_gap_from_the_broker_ticks_with_the_live_bar_builder(tmp_path):
    _seed_store(tmp_path)
    broker = TickHistoryBroker(TICKS[TICKS["ts_utc"] < LIVE_FROM])
    eng = _engine(tmp_path, broker, backfill_page_ticks=2000)
    eng.warm_start(LIVE_FROM)
    expect = ticks_to_1m(TICKS[(TICKS["ts_utc"] >= STORE_END) & (TICKS["ts_utc"] < LIVE_FROM)], DEFAULT_SESSIONS)
    got = eng.bars_1m[eng.bars_1m["ts_utc"] >= STORE_END].reset_index(drop=True)
    pd.testing.assert_frame_equal(got[expect.columns].reset_index(drop=True), expect, check_dtype=False)
    assert open_gaps(pd.DatetimeIndex(eng.bars_1m["ts_utc"]), DEFAULT_SESSIONS, max_open_min=60) == []
    assert broker.calls > 1                                        # paged
    # the backfilled bars are stored (a distinct source), so the next restart starts from them
    kept = Store(tmp_path / "data").read("bars_1m", source=live_source("icm"))
    assert len(kept) == len(expect)
    assert eng.history.backfill["bars"] == len(expect) and eng.history.backfill["error"] is None


def test_a_failed_backfill_keeps_the_store_bars_and_refuses_entries_with_history_gap(tmp_path):
    _seed_store(tmp_path)
    eng = _engine(tmp_path, TickHistoryBroker(TICKS, fail=True))
    assert eng.warm_start(LIVE_FROM) > 0                           # fail soft: the store's bars still load
    assert eng.history.backfill["error"] == "ConnectionError"
    decisions = _feed(eng, TICKS[(TICKS["ts_utc"] >= LIVE_FROM) & (TICKS["ts_utc"] < LIVE_FROM + pd.Timedelta(hours=12))])
    assert decisions and {d["action"] for d in decisions} == {"history_gap:15m"}
    state = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    gap = state["history_gap"]["15m"]
    assert pd.Timestamp(gap["from"]) <= STORE_END and pd.Timestamp(gap["to"]) >= LIVE_FROM
    assert gap["open_min"] > 4 * 23 * 60                            # Thu 6th -> Thu 13th: five trading days
    assert state["history_backfill"]["error"] == "ConnectionError"


def test_a_continuous_window_decides_and_publishes_no_history_gap(tmp_path):
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(TICKS[TICKS["ts_utc"] < LIVE_FROM]), source="dukascopy")
    eng = _engine(tmp_path, PaperBroker(equity=10_000))            # no copy_ticks: nothing to backfill, none needed
    eng.warm_start(LIVE_FROM)
    decisions = _feed(eng, TICKS[(TICKS["ts_utc"] >= LIVE_FROM) & (TICKS["ts_utc"] < LIVE_FROM + pd.Timedelta(days=2))])
    assert decisions and not any(d["action"].startswith("history_gap") for d in decisions)
    assert json.loads((tmp_path / "engine_icm-demo.json").read_text())["history_gap"] == {}


def test_completed_live_minutes_are_persisted_so_a_restart_has_no_hole(tmp_path):
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(TICKS[TICKS["ts_utc"] < LIVE_FROM]), source="dukascopy")
    eng = _engine(tmp_path, PaperBroker(equity=10_000))
    eng.warm_start(LIVE_FROM)
    until = LIVE_FROM + pd.Timedelta(hours=6)
    _feed(eng, TICKS[(TICKS["ts_utc"] >= LIVE_FROM) & (TICKS["ts_utc"] < until)])
    kept = Store(tmp_path / "data").read("bars_1m", source=live_source("icm"))
    assert len(kept) and pd.Timestamp(kept["ts_utc"].min()) >= LIVE_FROM
    assert pd.Timestamp(kept["ts_utc"].max()) >= until - pd.Timedelta(minutes=16)   # up to the last decision close
    again = _engine(tmp_path, PaperBroker(equity=10_000))
    again.warm_start(until)
    assert open_gaps(pd.DatetimeIndex(again.bars_1m["ts_utc"]), DEFAULT_SESSIONS, max_open_min=30) == []


def test_warm_start_without_a_store_backfills_nothing(tmp_path):
    broker = TickHistoryBroker(TICKS)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), broker, [], {})
    assert eng.warm_start(LIVE_FROM) == 0 and broker.calls == 0
