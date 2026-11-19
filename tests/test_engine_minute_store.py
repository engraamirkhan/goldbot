"""The engine's stored minutes say what each engine held, and since when (engine/history.py "stored minutes").

Brain 2026-11-18 18:00..11-19 02:45: two accounts' engines on one broker feed write gold to one `bars_1m_live` key and
the store keeps the last copy of a minute, so the other engine's plain copy, written seconds after a replacement,
erased the `replaced` flag of 47 of 61 replaced minutes and the nightly arm read voided most decision points as
replaced_minute. A minute a history fill joined (a quote stall, a restart) was stored like a live one, and the fill
also rewrote minutes the engine already held."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig, history
from goldbot.engine.history import FILLED_COL, LIVE_TABLE, REPLACED_COL, BarWriter
from goldbot.execution.paper import PaperBroker
from goldbot.specialists import SPECIALISTS

T = pd.Timestamp("2026-11-18 21:55", tz="UTC")


def _row(ts: pd.Timestamp, close: float = 1.0) -> dict[str, Any]:
    return {"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1), "bid_open": 1.0, "bid_high": max(1.0, close),
            "bid_low": min(1.0, close), "bid_close": close, "ask_open": 1.1, "ask_high": max(1.1, close + 0.1),
            "ask_low": min(1.1, close + 0.1), "ask_close": close + 0.1, "tick_count": 1, "spread_mean": 0.1,
            "spread_max": 0.1}


def _stored(store: Store) -> pd.DataFrame:
    return store.read(LIVE_TABLE, source="icm", symbol="XAUUSD").sort_values("ts_utc").reset_index(drop=True)


def _flag(frame: pd.DataFrame, col: str) -> list[bool]:
    if col not in frame.columns:
        return [False] * len(frame)
    return [bool(x) for x in frame[col].astype("boolean").fillna(False)]


# ------------------------------------------------------------------------------------- A: the replaced flag
def test_a_store_row_with_a_sticky_flag_is_replaced_only_by_a_flagged_copy(tmp_path):
    store = Store(tmp_path / "data")
    store.append(LIVE_TABLE, pd.DataFrame([_row(T, 2.0)]).assign(replaced=True), source="icm", symbol="XAUUSD",
                 sticky=REPLACED_COL)
    store.append(LIVE_TABLE, pd.DataFrame([_row(T, 1.0), _row(T + pd.Timedelta(minutes=1))]), source="icm",
                 symbol="XAUUSD", sticky=REPLACED_COL)
    got = _stored(store)
    assert list(got["bid_close"]) == [2.0, 1.0] and _flag(got, REPLACED_COL) == [True, False]
    store.append(LIVE_TABLE, pd.DataFrame([_row(T, 3.0)]).assign(replaced=True), source="icm", symbol="XAUUSD",
                 sticky=REPLACED_COL)
    assert list(_stored(store)["bid_close"]) == [3.0, 1.0]                 # a newer flagged copy still wins


def test_without_a_sticky_column_the_store_keeps_the_last_copy_as_before(tmp_path):
    store = Store(tmp_path / "data")
    store.append(LIVE_TABLE, pd.DataFrame([_row(T, 2.0)]).assign(replaced=True), source="icm", symbol="XAUUSD")
    store.append(LIVE_TABLE, pd.DataFrame([_row(T, 1.0)]), source="icm", symbol="XAUUSD")
    got = _stored(store)
    assert list(got["bid_close"]) == [1.0] and _flag(got, REPLACED_COL) == [False]


def test_another_engine_on_the_same_feed_writing_a_minute_after_its_replacement_keeps_the_replaced_flag(tmp_path):
    """The brain case: icm-probe replaces 21:55 (its sampled bar was late) and stores the broker's bar flagged;
    icm-demo, which had the broker's bar in time, stores its plain copy a few seconds later on the same key."""
    store = Store(tmp_path / "data")
    probe, demo = BarWriter(store, "icm", "XAUUSD"), BarWriter(store, "icm", "XAUUSD")
    probe.put(pd.DataFrame([_row(T, 2.0)]).assign(**{REPLACED_COL: True}))
    assert probe.join(5.0)
    written = pd.Timestamp(_stored(store)["available_utc"].iloc[0])
    demo.put(pd.DataFrame([_row(T, 2.0)]))
    assert demo.join(5.0)
    got = _stored(store)
    assert _flag(got, REPLACED_COL) == [True]
    assert pd.Timestamp(got["available_utc"].iloc[0]) == written          # the replacement's own write time


# ------------------------------------------------------------------------------------- B: the filled minutes
TICKS = synthetic_ticks("2025-03-03", "2025-03-15", ticks_per_minute=1, seed=8)
STORE_END = pd.Timestamp("2025-03-06", tz="UTC")
LIVE_FROM = pd.Timestamp("2025-03-13", tz="UTC")


class TickHistory:
    def __init__(self, ticks: pd.DataFrame):
        self.ticks = ticks.reset_index(drop=True)

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        h = self.ticks
        return h[h["ts_utc"] >= since_utc.floor("s")].head(n).reset_index(drop=True)


def _engine(tmp_path: Path) -> Engine:
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                               data_root=str(tmp_path / "data"), backfill_page_ticks=2000),
                  PaperBroker(equity=10_000), [spec], {"session_open": ConstantModel(p=0.65)})


def test_minutes_a_history_fill_joins_are_stored_flagged_filled_with_their_write_time(tmp_path):
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(TICKS[TICKS["ts_utc"] < STORE_END]), source="dukascopy")
    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM, TickHistory(TICKS[TICKS["ts_utc"] < LIVE_FROM]))
    f = eng.history.fill
    assert f is not None and f.done.wait(60)
    before = pd.Timestamp.now("UTC")
    history.absorb(eng)
    assert eng.history.writer is not None and eng.history.writer.join(30)
    got = _stored(Store(tmp_path / "data"))
    assert len(got) > 4 * 23 * 60 and all(_flag(got, FILLED_COL))
    assert (pd.to_datetime(got["available_utc"], utc=True) >= before).all()     # known from the absorb, not its close
    assert not any(_flag(got, REPLACED_COL))


def test_live_minutes_are_stored_without_the_filled_flag(tmp_path):
    eng = _engine(tmp_path)
    history.queue_minutes(eng, ticks_to_1m(TICKS[(TICKS["ts_utc"] >= LIVE_FROM)
                                                 & (TICKS["ts_utc"] < LIVE_FROM + pd.Timedelta(minutes=5))]))
    assert eng.history.writer is not None and eng.history.writer.join(30)
    got = _stored(Store(tmp_path / "data"))
    assert len(got) == 5 and not any(_flag(got, FILLED_COL))


def test_a_fill_stores_only_the_minutes_it_joined_never_a_minute_the_engine_already_held(tmp_path):
    """The engine's own copy wins in memory (keep first); the store must keep it too, with its own write time."""
    eng = _engine(tmp_path)
    own = ticks_to_1m(TICKS[(TICKS["ts_utc"] >= LIVE_FROM) & (TICKS["ts_utc"] < LIVE_FROM + pd.Timedelta(minutes=3))])
    eng.bars_1m = own.copy()
    history.queue_minutes(eng, own)
    assert eng.history.writer is not None and eng.history.writer.join(30)
    first = _stored(Store(tmp_path / "data"))
    pulled = ticks_to_1m(TICKS[(TICKS["ts_utc"] >= LIVE_FROM)
                               & (TICKS["ts_utc"] < LIVE_FROM + pd.Timedelta(minutes=6))]).assign(bid_close=9.0)
    done = SimpleNamespace(is_set=lambda: True)
    fill: Any = SimpleNamespace(done=done, bars=[pulled], covered=[], error=None, rebase=None, spans=[])
    eng.history.fill = fill                                                # a finished fill, as HistoryFill leaves it
    assert history.absorb(eng)
    assert eng.history.writer.join(30)
    got = _stored(Store(tmp_path / "data"))
    assert len(got) == 6
    kept = got[got["ts_utc"].isin(own["ts_utc"])].reset_index(drop=True)
    pd.testing.assert_series_equal(kept["bid_close"], first["bid_close"], check_dtype=False)
    pd.testing.assert_series_equal(kept["available_utc"], first["available_utc"])
    assert not any(_flag(kept, FILLED_COL)) and all(_flag(got[~got["ts_utc"].isin(own["ts_utc"])], FILLED_COL))
    assert (got.loc[~got["ts_utc"].isin(own["ts_utc"]), "bid_close"] == 9.0).all()


def test_the_broker_minutes_a_rebase_stores_are_flagged_filled(tmp_path):
    rel_hole, own_from = pd.Timestamp("2025-03-05 13:00", tz="UTC"), pd.Timestamp("2025-03-10", tz="UTC")
    rel = ticks_to_1m(TICKS[TICKS["ts_utc"] < own_from])
    rel = rel[(rel["ts_utc"] < rel_hole) | (rel["ts_utc"] >= rel_hole + pd.Timedelta(hours=2))]
    store = Store(tmp_path / "data")
    store.append("bars_1m", rel, source="dukascopy")
    store.append(LIVE_TABLE, ticks_to_1m(TICKS[(TICKS["ts_utc"] >= own_from) & (TICKS["ts_utc"] < LIVE_FROM)]),
                 source="icm", symbol="XAUUSD")
    point = 0.01
    b = ticks_to_1m(TICKS[TICKS["ts_utc"] < LIVE_FROM])
    rates = pd.DataFrame({"ts_utc": b["ts_utc"], "open": b["bid_open"], "high": b["bid_high"], "low": b["bid_low"],
                          "close": b["bid_close"], "tick_count": b["tick_count"].astype(int),
                          "spread": ((b["ask_close"] - b["bid_close"]) / point).round().astype(int)})

    class Bars(TickHistory):
        def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
            return rates.tail(n).reset_index(drop=True)

        def symbol_info(self, symbol: str) -> Any:
            return SimpleNamespace(point=point)

    eng = _engine(tmp_path)
    eng.warm_start(LIVE_FROM, Bars(TICKS.iloc[:0]))
    f = eng.history.fill
    assert f is not None and f.done.wait(60)
    history.absorb(eng)
    assert eng.history.cutover == rel_hole
    assert eng.history.writer is not None and eng.history.writer.join(30)
    got = _stored(store)
    rebased = got[got["ts_utc"] < own_from]
    assert len(rebased) and all(_flag(rebased, FILLED_COL))
    assert not any(_flag(got[got["ts_utc"] >= own_from], FILLED_COL))      # the engine's stored minutes untouched
