"""Multi-instrument plan (lane L3): "test_capacity.py 26-symbol cycle < 20 s". One engine holds gold and 25 other
books, each warm with a week of 1m bars (features, context timeframes, the ladder ATRs), and every book decides one
15m bar close in turn on one thread, as the run loop's serial pass does. The whole cycle must finish within 20 s,
well inside the 15m bar and within the plan's budget for the icm-probe box."""
from __future__ import annotations

import time

import pandas as pd

from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import Engine, EngineConfig
from goldbot.engine.books import BookSpec, using
from goldbot.engine.history import RELEASE_SOURCE
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker

N_BOOKS = 26
CYCLE_BUDGET_S = 20.0


class MultiPaper(PaperBroker):
    def on_tick(self, tick: Tick) -> None:
        return None


def test_26_symbol_cycle_under_20s(tmp_path):
    ticks = synthetic_ticks("2025-03-03", "2025-03-08", ticks_per_minute=1, seed=3)
    bars = ticks_to_1m(ticks)
    close_ts = pd.Timestamp(bars["visible_at"].iloc[-1]).floor("15min")
    store = Store(tmp_path / "data")
    specs = tuple(BookSpec(key=f"SYM{k:02d}", symbol=f"SYM{k:02d}", store_symbol=f"SYM{k:02d}", magic_block=k,
                           digits=2, point=0.01, contract_size=100.0) for k in range(1, N_BOOKS))
    for sym in ["XAUUSD", *(s.store_symbol for s in specs)]:
        store.append("bars_1m", bars, source=RELEASE_SOURCE, symbol=sym)
    pb = MultiPaper(equity=10_000)
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", magic_base=260500,
                              state_dir=str(tmp_path / "state"), data_root=str(tmp_path / "data"), instruments=specs),
                 pb, [], {})
    assert len(eng.books) == N_BOOKS
    last = Tick(ts_utc=close_ts, bid=float(bars["bid_close"].iloc[-1]), ask=float(bars["ask_close"].iloc[-1]))
    for book in eng.books.values():
        with using(eng, book):
            assert eng.warm_start(close_ts) > 4000
            pb.set_quote(eng.cfg.symbol, last)
            eng._last_tick = last
    t0 = time.perf_counter()
    for book in eng.books.values():
        with using(eng, book):
            eng.on_bar_close(close_ts, last)
            assert eng._last_atr is not None          # every book really decided (features built, ATR read)
    took = time.perf_counter() - t0
    assert took < CYCLE_BUDGET_S, f"26-symbol bar-close cycle took {took:.1f} s"
