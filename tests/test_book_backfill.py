"""Live demo issue 2026-11-02: the non-gold books on icm-probe (19) and vantage-demo (7) never traded after an engine
start, because a bar close decides nothing below 400 1m bars and only gold was back-filled from the broker's history.
Rules held here (engine/book_backfill.py): each enabled non-gold book warms up from the broker's 1m history, one book
per pass, paced, only the gap to what it has stored, on its own sessions and point in time, stored under (account,
store_symbol); until then it decides nothing and its exits are untouched; a failed page is retried later and never
raises into the pass; gold is never pulled."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pandas as pd

from goldbot.data.store import Store
from goldbot.engine import Engine, EngineConfig, book_backfill, books, exploration
from goldbot.engine.books import BookSpec, using
from goldbot.engine.history import LIVE_TABLE
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker

NOW = pd.Timestamp("2026-10-28 15:07:30", tz="UTC")      # a Wednesday afternoon: the market is open
SYMS = ("SYM01", "SYM02")


class Rates:
    """The bridge's get_bars_range: every minute in [lo, hi] (MT5's range is inclusive), or a failure while `fail`."""

    def __init__(self, fail: int = 0, clock: Callable[[], pd.Timestamp] | None = None, page_s: float = 0.0):
        self.calls: list[tuple[str, str, pd.Timestamp, pd.Timestamp]] = []
        self.fail, self.clock, self.page_s = fail, clock, page_s

    def get_bars_range(self, symbol: str, tf: str, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
        self.calls.append((symbol, tf, lo, hi))
        if self.page_s and self.clock is not None:
            self.clock.advance(self.page_s)              # type: ignore[attr-defined]
        if self.fail:
            self.fail -= 1
            raise TimeoutError("bridge read timed out")
        ts = pd.date_range(lo, hi, freq="min")
        return pd.DataFrame({"ts_utc": ts, "open": 30.0, "high": 30.05, "low": 29.95, "close": 30.0,
                             "tick_count": 10, "spread": 2})


class Clock:
    def __init__(self, t: pd.Timestamp = NOW) -> None:
        self.t = t

    def __call__(self) -> pd.Timestamp:
        return self.t

    def advance(self, s: float) -> None:
        self.t += pd.Timedelta(seconds=s)


def _engine(tmp_path: Path, n: int = 1, blocked: str | None = None) -> Engine:
    specs = tuple(BookSpec(key=s, symbol=s, store_symbol=f"{s}.store", magic_block=k + 1, digits=2, point=0.01,
                           contract_size=100.0, blocked=blocked) for k, s in enumerate(SYMS[:n]))
    pb = PaperBroker(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", magic_base=260500,
                              state_dir=str(tmp_path / "state"), data_root=str(tmp_path / "data"),
                              instruments=specs), pb, [], {})
    for b in eng.books.values():
        pb.set_quote(b.__dict__["cfg"].symbol, Tick(ts_utc=NOW, bid=30.0, ask=30.02))
    return eng


def _attach(eng: Engine, rates: Rates, clock: Clock | None = None, spawned: list | None = None,
            sleeps: list | None = None) -> book_backfill.Backfill:
    return book_backfill.attach(
        eng, rates, clock=clock or Clock(), sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
        spawn=(spawned.append if spawned is not None else (lambda fn: fn())))


def _book(eng: Engine, sym: str = "SYM01") -> books.SymbolBook:
    return eng.books[sym]


def _warming(eng: Engine, sym: str = "SYM01") -> bool:
    with using(eng, _book(eng, sym)):
        return books.warming(eng)


def _decided(eng: Engine, monkeypatch: Any, sym: str = "SYM01") -> list[str]:
    """The timeframes the book's next bar close reached the decision step on."""
    seen: list[str] = []

    def decide(self: Engine, tf: str, *a: Any, **k: Any) -> list[dict]:
        seen.append(tf)
        return []
    monkeypatch.setattr(Engine, "_decide", decide)
    close = NOW.floor("15min")
    with using(eng, _book(eng, sym)):
        eng.on_bar_close(close, Tick(ts_utc=close, bid=30.0, ask=30.02))
    return seen


def _bars(lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    from goldbot.engine.history import rates_to_bars
    return rates_to_bars(Rates().get_bars_range("x", "1m", lo, hi - pd.Timedelta(minutes=1)), 0.01)


# ------------------------------------------------------------------------------------------- warm-up and decisions
def test_a_book_with_no_stored_bars_decides_on_its_first_bar_close_after_the_backfill(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    _attach(eng, Rates())
    assert _warming(eng)
    book_backfill.step(eng)                     # starts the turn (inline here)
    book_backfill.step(eng)                     # absorbs it
    assert not _warming(eng)
    with using(eng, _book(eng)):
        assert len(eng.bars_1m) >= book_backfill.need(eng) >= book_backfill.MIN_BARS
    assert _decided(eng, monkeypatch) == ["15m"]


def test_a_cold_book_decides_nothing_at_its_bar_close_even_with_enough_bars(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    _attach(eng, Rates())
    with using(eng, _book(eng)):
        eng.bars_1m = _bars(NOW.floor("15min") - pd.Timedelta(days=5), NOW.floor("15min"))
    assert _warming(eng)
    assert _decided(eng, monkeypatch) == []


def test_without_a_backfill_client_a_non_gold_book_is_warm_as_before(tmp_path):
    eng = _engine(tmp_path)
    assert not _warming(eng)


def test_the_need_is_at_least_400_bars_and_a_full_decision_frame(tmp_path):
    eng = _engine(tmp_path)
    with using(eng, _book(eng)):
        assert book_backfill.need(eng) == max(400, 15 * book_backfill.FRAME_BARS)


def test_a_cold_books_exploration_candidates_are_blocked_book_warming_and_never_entered(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    _attach(eng, Rates())
    blocked: list[str] = []
    entered: list[int] = []
    for name in ("refresh_retired", "refresh_cost_reads", "refresh_dormant", "refresh_owner_experiments", "_minutes",
                 "_pyramid"):
        monkeypatch.setattr(exploration, name, lambda *a, **k: None)
    monkeypatch.setattr("goldbot.engine.exploration_learning.refresh", lambda *a, **k: None)
    def block(e: Engine, ex: Any, sigs: list, now: pd.Timestamp, why: str) -> None:
        blocked.append(why)
        ex.queue.clear()
    monkeypatch.setattr(exploration, "_block", block)
    monkeypatch.setattr(exploration, "_enter", lambda *a, **k: entered.append(1))
    with using(eng, _book(eng)):
        eng.exploration = SimpleNamespace(queue=["signal"], retired_ok=True, arms={},  # type: ignore[assignment]
                                          last_close=None)
        eng._closed_through = None
        exploration.on_pass(eng, Tick(ts_utc=NOW, bid=30.0, ask=30.02))
    assert blocked == ["book_warming"] and not entered


# ------------------------------------------------------------------------------------------- pacing
def test_the_backfill_runs_one_book_per_pass_and_never_two_at_once(tmp_path):
    eng = _engine(tmp_path, n=2)
    spawned: list[Callable[[], None]] = []
    rates = Rates()
    _attach(eng, rates, spawned=spawned)
    book_backfill.step(eng)
    book_backfill.step(eng)                     # the first book's turn is still running: nothing else starts
    assert len(spawned) == 1
    spawned[0]()
    first = {c[0] for c in rates.calls}
    assert len(first) == 1
    book_backfill.step(eng)                     # absorbs the first book; the second waits for the next pass
    assert len(spawned) == 1
    book_backfill.step(eng)
    assert len(spawned) == 2
    spawned[1]()
    book_backfill.step(eng)
    assert {c[0] for c in rates.calls} == set(SYMS)
    assert not _warming(eng, "SYM01") and not _warming(eng, "SYM02")


def test_pages_are_bounded_one_minute_bars_with_a_one_second_pause_between_them(tmp_path):
    eng = _engine(tmp_path)
    sleeps: list[float] = []
    rates = Rates()
    _attach(eng, rates, sleeps=sleeps)
    book_backfill.step(eng)
    assert len(rates.calls) >= 2
    assert all(tf == "1m" and hi - lo <= book_backfill.PAGE for _, tf, lo, hi in rates.calls)
    assert sleeps == [book_backfill.PAGE_PAUSE_S] * (len(rates.calls) - 1)


def test_a_turn_past_its_time_budget_keeps_its_pages_and_continues_on_the_books_next_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(book_backfill, "PAGE", pd.Timedelta(hours=6))   # the 1 800-bar need takes over 3 pages
    eng = _engine(tmp_path)
    clock = Clock()
    rates = Rates(clock=clock, page_s=25.0)        # three pages spend the 60 s budget, none over the page limit
    bf = _attach(eng, rates, clock=clock)
    book_backfill.step(eng)
    book_backfill.step(eng)
    assert bf.status["SYM01"]["error"] == "budget" and _warming(eng)
    with using(eng, _book(eng)):
        kept = len(eng.bars_1m)
    assert kept > 0
    for _ in range(10):
        book_backfill.step(eng)
    assert not _warming(eng)


# ------------------------------------------------------------------------------------------- failures
def test_a_failed_page_never_raises_into_the_pass_and_is_retried_later(tmp_path):
    eng = _engine(tmp_path)
    clock = Clock()
    rates = Rates(fail=1)
    bf = _attach(eng, rates, clock=clock)
    book_backfill.step(eng)
    book_backfill.step(eng)
    assert _warming(eng) and bf.status["SYM01"]["error"] == "TimeoutError"
    n = len(rates.calls)
    book_backfill.step(eng)                     # before the retry time: no new pull
    assert len(rates.calls) == n and bf.turn is None
    clock.advance(book_backfill.RETRY_S + 1)
    book_backfill.step(eng)
    book_backfill.step(eng)
    assert not _warming(eng)


def test_a_step_that_fails_inside_logs_and_never_raises(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    _attach(eng, Rates())
    monkeypatch.setattr(book_backfill, "_plan", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    book_backfill.step(eng)
    assert _warming(eng)


# ------------------------------------------------------------------------------------------- only the gap, kept apart
def test_a_book_with_stored_bars_pulls_only_the_tail_since_its_last_minute(tmp_path):
    eng = _engine(tmp_path)
    end = NOW.floor("min") - pd.Timedelta(minutes=30)
    stored = _bars(end - pd.Timedelta(days=3), end)
    rates = Rates()
    _attach(eng, rates)
    with using(eng, _book(eng)):
        eng.bars_1m = stored
    book_backfill.step(eng)
    book_backfill.step(eng)
    [(_, _, lo, hi)] = rates.calls
    assert lo == pd.Timestamp(stored["ts_utc"].iloc[-1]) + pd.Timedelta(minutes=1) and hi == NOW.floor("min")
    assert not _warming(eng)


def test_backfilled_minutes_are_point_in_time_and_stored_under_the_account_and_store_symbol(tmp_path):
    eng = _engine(tmp_path)
    _attach(eng, Rates())
    book_backfill.step(eng)
    book_backfill.step(eng)
    with using(eng, _book(eng)):
        bars = eng.bars_1m
        writer = eng.history.writer
        assert writer is not None and writer.join(30)
    assert (pd.to_datetime(bars["visible_at"], utc=True) <= NOW).all()
    data = Store(tmp_path / "data")
    kept = data.read(LIVE_TABLE, source="icm-probe", symbol="SYM01.store")
    assert len(kept) == len(bars)
    assert data.read(LIVE_TABLE, source="icm", symbol="XAUUSD").empty


def test_backfilled_minutes_keep_the_books_own_sessions(tmp_path):
    eng = _engine(tmp_path)
    _attach(eng, Rates())
    book_backfill.step(eng)
    book_backfill.step(eng)
    with using(eng, _book(eng)):
        ts = pd.DatetimeIndex(pd.to_datetime(eng.bars_1m["ts_utc"], utc=True))
        assert bool(pd.Series(eng.sessions.is_open(ts)).all())


def test_gold_and_blocked_books_are_never_pulled(tmp_path):
    eng = _engine(tmp_path, n=2, blocked=books.UNVERIFIED)
    rates = Rates()
    _attach(eng, rates)
    for _ in range(4):
        book_backfill.step(eng)
    assert rates.calls == []
    assert not books.warming(eng)               # gold, the active book
    assert not _warming(eng, "SYM01")


def test_live_minutes_win_and_nothing_at_or_after_the_first_unbuilt_tick_is_inserted(tmp_path):
    eng = _engine(tmp_path)
    _attach(eng, Rates())
    first_tick = NOW.floor("min") - pd.Timedelta(minutes=5)
    with using(eng, _book(eng)):
        eng.ticks = [Tick(ts_utc=first_tick + pd.Timedelta(seconds=3), bid=31.0, ask=31.02)]
    book_backfill.step(eng)
    book_backfill.step(eng)
    with using(eng, _book(eng)):
        assert pd.Timestamp(eng.bars_1m["ts_utc"].max()) < first_tick


# ------------------------------------------------------------------------------------------- follow-ups (#322)
def test_the_backfill_shares_the_key_zones_box_lock_and_waits_while_a_levels_pull_holds_it(tmp_path):
    from goldbot.engine.key_zones import box_lock
    eng = _engine(tmp_path)
    rates = Rates()
    locks = tmp_path / "locks"
    bf = book_backfill.attach(eng, rates, box="box1", lock_dir=locks, clock=Clock(), sleep=lambda s: None,
                              spawn=lambda fn: fn())
    with box_lock(locks, "box1") as mine:            # a key zones long pull on the same box
        assert mine
        book_backfill.step(eng)
        book_backfill.step(eng)
    assert rates.calls == [] and bf.status["SYM01"]["error"] == "busy" and _warming(eng)
    assert [p.name for p in locks.iterdir()] == ["levels-box1.lock"]


def _pyramid_case(monkeypatch: Any) -> tuple[Any, list[str]]:
    from goldbot.engine import exploration_pyramid as ep
    healed: list[str] = []
    unit = SimpleNamespace(add_no=0)
    grp = SimpleNamespace(side=1, entry=30.0, risk=0.1, end_utc=NOW + pd.Timedelta(days=1), group_id="g1")
    monkeypatch.setattr(ep, "open_units", lambda eng, arm, gid=None: {"g1": [(unit, None)]})
    monkeypatch.setattr(ep, "group", lambda *a: grp)
    monkeypatch.setattr(ep, "heal", lambda eng, ex, arm, g, now, force=False: healed.append(g.group_id))
    monkeypatch.setattr(ep, "ladder", lambda *a: 0)
    arm = SimpleNamespace(arm_id="a1", adds=2, tf="15m")
    ex = SimpleNamespace(arms={"a1": arm}, retired=frozenset(), queue=[], groups={}, ladder={})
    return ex, healed


def test_a_warming_book_queues_no_pyramid_add_but_still_holds_its_groups_stops(tmp_path, monkeypatch):
    from goldbot.engine.exploration_pyramid import pyramid
    eng = _engine(tmp_path)
    _attach(eng, Rates())
    ex, healed = _pyramid_case(monkeypatch)
    with using(eng, _book(eng)):
        pyramid(eng, ex, NOW, Tick(ts_utc=NOW, bid=30.3, ask=30.32))
    assert healed == ["g1"] and ex.queue == []


def test_a_warm_book_queues_its_pyramid_add_once_the_price_reaches_the_level(tmp_path, monkeypatch):
    from goldbot.engine.exploration_pyramid import pyramid
    eng = _engine(tmp_path)
    ex, healed = _pyramid_case(monkeypatch)
    with using(eng, _book(eng)):
        pyramid(eng, ex, NOW, Tick(ts_utc=NOW, bid=30.3, ask=30.32))
    assert healed == ["g1"] and [p.add_no for p in ex.queue] == [1]
