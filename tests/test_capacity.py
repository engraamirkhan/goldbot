"""Multi-instrument plan (lane L3): "test_capacity.py 26-symbol cycle < 20 s". One engine holds gold and 25 other
books, each warm with a week of 1m bars (features, context timeframes, the ladder ATRs), and every book decides one
15m bar close in turn on one thread, as the run loop's serial pass does. The whole cycle must finish within 20 s,
well inside the 15m bar and within the plan's budget for the icm-probe box."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import Engine, EngineConfig, books, magic
from goldbot.engine.books import BookSpec, using
from goldbot.engine.history import RELEASE_SOURCE
from goldbot.execution.broker import Position, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops.services.engine import engine_pass

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


# ---------------------------------------------------------------- trading-safety review of #310, M1 (bridge load)
LATENCY_S = 0.08          # one bridge round trip (the MT5 box over HTTP), every broker call
BRIDGE_CALLS = frozenset({"account", "account_positions", "positions", "last_tick", "ticks_many", "symbol_info",
                          "loss_per_lot", "margin_required", "modify", "close", "place_order", "deals_since"})
T_PASS = pd.Timestamp.now("UTC").floor("s")   # fresh: the batched read (`ticks_many`) serves every book


class SlowBridge(MultiPaper):
    """The paper broker behind a bridge: every call takes LATENCY_S and is logged (name, first argument)."""

    def __init__(self) -> None:
        super().__init__(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
        self.calls: list[tuple[str, Any]] = []
        self.latency_s = LATENCY_S

    def __getattribute__(self, name: str) -> Any:
        attr = object.__getattribute__(self, name)
        if name not in BRIDGE_CALLS:
            return attr
        calls, wait = object.__getattribute__(self, "calls"), object.__getattribute__(self, "latency_s")

        def slow(*a: Any, **k: Any) -> Any:
            calls.append((name, a[0] if a else k.get("symbol")))
            time.sleep(wait)
            return attr(*a, **k)
        return slow


def _bridge_engine(tmp_path: Path, **cfg: Any) -> tuple[Engine, SlowBridge]:
    specs = tuple(BookSpec(key=f"SYM{k:02d}", symbol=f"SYM{k:02d}", store_symbol=f"SYM{k:02d}", magic_block=k,
                           digits=2, point=0.01, contract_size=100.0) for k in range(1, N_BOOKS))
    pb = SlowBridge()
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", magic_base=260500,
                              state_dir=str(tmp_path / "state"), instruments=specs, **cfg), pb, [], {})
    for b in eng.books.values():
        pb.set_quote(b.__dict__["cfg"].symbol, Tick(ts_utc=T_PASS, bid=30.0, ask=30.02))
    pb.calls.clear()
    return eng, pb


def test_a_26_book_pass_reads_the_account_and_its_positions_once_and_stays_under_its_budget(tmp_path):
    """M1: with ~80 ms per bridge call, one pass over 26 books (every book's state refresh due) reads account() and
    account_positions() once for all books, and finishes within the engine's pass budget (plus at most one book's
    step: the deadline is checked before each book starts)."""
    eng, pb = _bridge_engine(tmp_path)
    t0 = time.perf_counter()
    books.account_pass(eng, pb, engine_pass, books.QuoteFeed(pb))
    took = time.perf_counter() - t0
    names = [n for n, _ in pb.calls]
    assert names.count("account") == 1 and names.count("account_positions") == 1, names
    assert took < books.PASS_BUDGET_S + 1.0, f"26-book pass took {took:.1f} s"


def test_every_books_stop_check_runs_before_any_books_refresh_or_decisions(tmp_path):
    """M1: the last book's engine stop (tighter than the broker's) is closed at market before the pass reads the
    account for any book's refresh, so a slow pass never holds an exit behind 25 other books' work."""
    eng, pb = _bridge_engine(tmp_path)
    last = list(eng.books.values())[-1]
    pb._positions[9] = Position(position_id=9, symbol=last.__dict__["cfg"].symbol, side=1, lots=0.1, open_price=31.0,
                                sl=20.0, tp=None, magic=magic.book_base(260500, N_BOOKS - 1) + 95, comment="",
                                open_time_utc=T_PASS, profit=0.0)
    with using(eng, last):
        eng._reconcile(None)
        eng.open[9].sl = 30.5                       # the engine's stop, above the 30.00 bid: through it
    pb.calls.clear()
    books.account_pass(eng, pb, engine_pass, books.QuoteFeed(pb))
    assert ("close", 9) in pb.calls and 9 not in pb._positions
    assert pb.calls.index(("close", 9)) < next(i for i, (n, _) in enumerate(pb.calls) if n == "account")


def test_a_pass_past_its_deadline_defers_the_remaining_books_which_go_first_next_pass(tmp_path):
    """M1: once the pass budget is spent no further book starts its refresh and decisions; the deferred books go
    first on the next pass (after gold), so none starves, while every book's exits ran on every pass."""
    eng, pb = _bridge_engine(tmp_path)
    pb.latency_s = 0.02                             # the same calls, a quicker test
    seen: list[str] = []

    def one(e: Engine, quotes: Any, symbol: str) -> bool:
        seen.append(symbol)
        return engine_pass(e, quotes, symbol)
    feed = books.QuoteFeed(pb)
    books.account_pass(eng, pb, one, feed, budget_s=0.3)
    first = list(seen)
    assert first[0] == "XAUUSD" and len(first) < N_BOOKS
    seen.clear()
    books.account_pass(eng, pb, one, feed, budget_s=0.3)
    assert seen[0] == "XAUUSD" and seen[1] == next(s for s in eng.books if s not in first)
    ran = set(first) | set(seen)
    for _ in range(N_BOOKS):
        if len(ran) == N_BOOKS:
            break
        seen.clear()
        books.account_pass(eng, pb, one, feed, budget_s=0.3)
        ran |= set(seen)
    assert ran == set(eng.books)


def test_an_entry_booked_inside_a_pass_clears_the_passs_account_reads(tmp_path):
    """M1 follow-up: an entry sent on the bridge's order thread goes around the PassBroker; booking it (even with no
    position id in the reply) clears the pass's account() and account_positions() reads, so the next book's refresh
    in the same pass reads the broker again and sees the fill."""
    from goldbot.execution.broker import OrderResult
    from goldbot.risk import Intent
    from goldbot.risk.gate import GateDecision
    eng, pb = _bridge_engine(tmp_path)
    pb.latency_s = 0.0
    seen: list[int] = []

    def one(e: Engine, quotes: Any, symbol: str) -> bool:
        seen.append(len(e.broker.account_positions()))
        if symbol == "XAUUSD":                      # an entry filled on the order thread, reply without a position id
            pb._positions[42] = Position(position_id=42, symbol="SYM01", side=1, lots=0.1, open_price=30.0, sl=29.0,
                                         tp=None, magic=magic.book_base(260500, 1) + 95, comment="",
                                         open_time_utc=T_PASS, profit=0.0)
            books.book(e, Intent(agent_id="a", side=1, p=0.5, target_atr=2.0, stop_atr=1.0, atr_usd=0.5,
                                 cost_atr=0.1, multiplier=1.0, price=2000.0), GateDecision(allowed=True, lots=0.1, risk_amount=1.0),
                       OrderResult(ok=True, retcode=10009, order_id=1, position_id=None, filled_lots=0.1, price=30.0,
                                   message=""))
        return True
    books.account_pass(eng, pb, one, books.QuoteFeed(pb))
    assert seen[0] == 0 and seen[1] == 1
