"""1m/5m arms ahead of the 15m+ close work (vantage-demo refusals 2026-11-10, SRE diagnosis items 1-4, fixes 2-4).

On a 5m/15m close every book is due, and inside each book's pass the 15m+ exploration step (2.4-5 s of CPU per book
on the 2-vCPU brain) ran before the book's 1m signal was even queued, behind every earlier book's step: 47-64 % of
the 1m signals were refused `stale` (10 s from the bar close) over 7 days. And a pass starting inside the 1.5 s grace
saw no book due and spent its budget on a non-due book's close work first.

(1) A book's 1m/5m arms are decided and sent before its 15m+ step and the learning read (`exploration.on_pass`).
(2) A multi-book pass runs every book's 1m/5m part before any book's 15m+ part (`books.account_pass`), and starts no
    15m+ work while a 1m/5m decision or send is pending or a minute is inside its grace.
(3) Only the order changes: the refusals, the PER_PASS cap and the journal rows are the same.
(4) The deferral warning names the exploration sub-phases (reads, learning, step, minutes, entry, budget).
"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig, books, exploration, exploration_learning
from goldbot.engine.books import BookSpec
from goldbot.engine.exploration import GRACE_S, Pending, stale_s
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.research.arms import send_delay_s

CLOSE = pd.Timestamp("2025-03-05 07:15:00", tz="UTC")          # a quarter-hour close: every 1m and 15m arm is due
SYMBOLS = [f"SYM{k:02d}" for k in range(1, 12)]                 # 11 books beside gold, as vantage-demo
SPECS = tuple(BookSpec(key=s, symbol=s, store_symbol=s, magic_block=k, digits=2, point=0.01, contract_size=100.0)
              for k, s in enumerate(SYMBOLS, start=1))
STEP_S = 3.0                                                    # one book's 15m+ step on the brain (2.4-5 s)
BOOK_S = 0.05                                                   # the rest of one book's pass


class _Multi(PaperBroker):
    def on_tick(self, tick: Tick) -> None:
        return None


class _World:
    """One fake clock for the pass deadline (books.time.monotonic) and the engine (`Engine.clock`, live clock), and
    what the exploration steps did when."""

    def __init__(self, start_s: float) -> None:
        self.t = start_s
        self.entered: dict[str, float] = {}           # arm -> its signal's age when `_enter` took it (sent or stale)
        self.stale: list[str] = []
        self.steps: list[tuple[str, float]] = []      # (book, clock) of each 15m+ step
        self.decided: dict[str, float] = {}           # book -> when its 1m minute was decided
        self.closes: list[tuple[str, pd.Timestamp]] = []   # (book, close) of each 15m+ step
        self.ticks: list[tuple[str, float]] = []      # (book, bid) of each send's quote

    def now(self) -> pd.Timestamp:
        return CLOSE + pd.Timedelta(seconds=self.t)


def _ex(sym: str) -> Any:
    arm = SimpleNamespace(tf="1m", arm_id=f"S0-{sym}", family="random", spec=SimpleNamespace(role="control"))
    big = SimpleNamespace(tf="15m", arm_id=f"B-{sym}", family="box", spec=SimpleNamespace(role="strategy"))
    return SimpleNamespace(arms={arm.arm_id: arm, big.arm_id: big}, queue=deque(), dormant=frozenset(),
                           last_minute=CLOSE - pd.Timedelta(minutes=1), last_close=CLOSE - pd.Timedelta(minutes=15),
                           retired_ok=True, learning_group=frozenset(), retired=frozenset(), slow_owed=None)


def _world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start_s: float) -> tuple[Engine, _World]:
    w = _World(start_s)
    eng = Engine(EngineConfig(account_id="vantage-demo", broker_name="vantage", mode="demo", magic_base=260500,
                              state_dir=str(tmp_path), instruments=SPECS, live_clock=True), _Multi(equity=10_000),
                 [], {})
    eng.clock = w.now
    monkeypatch.setattr(books, "time", SimpleNamespace(monotonic=lambda: w.t))
    for b in eng.books.values():
        if b.primary:
            continue
        with books.using(eng, b):
            eng.exploration = _ex(b.__dict__["cfg"].symbol)
            eng._closed_through = CLOSE
    for name in ("refresh_retired", "refresh_cost_reads", "refresh_dormant", "refresh_owner_experiments", "_pyramid"):
        monkeypatch.setattr(exploration, name, lambda *a, **k: None)
    monkeypatch.setattr(exploration_learning, "refresh", lambda *a, **k: None)

    def step(e: Engine, close_ts: pd.Timestamp) -> bool:
        w.steps.append((e.cfg.symbol, w.t))
        w.closes.append((e.cfg.symbol, close_ts))
        w.t += STEP_S
        return True

    def minutes(e: Engine, ex: Any, now: pd.Timestamp) -> None:
        through = (now - pd.Timedelta(seconds=GRACE_S)).floor("min")
        if through <= ex.last_minute:
            return
        ex.last_minute = through
        w.decided[e.cfg.symbol] = w.t
        ex.queue.append(Pending(arm_id=f"S0-{e.cfg.symbol}", side=1, bar_ts=through, stop_usd=5.0, target_usd=10.0,
                                hold_bars=4))

    def enter(e: Engine, ex: Any, now: pd.Timestamp, tick: Tick, limit: int = exploration.PER_PASS) -> int:
        """`_enter` reduced to its timing: a stale signal refused, a waiting one kept, at most `limit` sent."""
        took = 0
        for s in list(ex.queue):
            age = (now - s.bar_ts).total_seconds()
            if age > stale_s(ex.arms[s.arm_id].tf):
                w.stale.append(s.arm_id)
            elif exploration._waits(ex, s, now) or took >= limit:
                continue
            else:
                took += 1
                w.ticks.append((e.cfg.symbol, tick.bid))
            w.entered[s.arm_id] = age
            ex.queue.remove(s)
        return took
    monkeypatch.setattr(exploration, "step", step)
    monkeypatch.setattr(exploration, "_minutes", minutes)
    monkeypatch.setattr(exploration, "_enter", enter)
    return eng, w


def _one(w: _World) -> Any:
    """`ops.services.engine.engine_pass` reduced to what orders the work: the book's tick, its pass, its exploration."""
    def one(e: Engine, q: Any, s: str) -> bool:
        w.t += BOOK_S
        books.exploration_pass(e, Tick(ts_utc=w.now(), bid=100.0, ask=100.1))
        return True
    return one


def _run(eng: Engine, w: _World, until_s: float = 60.0) -> None:
    one = _one(w)
    while w.t < until_s:
        books.account_pass(eng, eng.broker, one)
        w.t += 0.25                                                    # the run loop's sleep


@pytest.mark.parametrize("start_s", [1.6, 3.0])
def test_every_books_1m_signal_at_a_quarter_hour_close_is_sent_inside_its_10_s_stale_limit(
        tmp_path, monkeypatch, start_s):
    eng, w = _world(tmp_path, monkeypatch, start_s)
    _run(eng, w)
    assert w.stale == [], {a: round(v, 2) for a, v in w.entered.items()}
    assert sorted(w.entered) == sorted(f"S0-{s}" for s in SYMBOLS)
    assert max(w.entered.values()) < stale_s("1m"), {a: round(v, 2) for a, v in w.entered.items()}
    for a, age in w.entered.items():                                    # never sent before its send delay either
        assert age >= send_delay_s(a, "1m", CLOSE)
    assert sorted(s for s, _ in w.steps) == sorted(SYMBOLS)            # every book's 15m+ step still ran once
    assert max(t for _, t in w.steps) + STEP_S < stale_s("15m")       # inside the 15m+ limit


def test_a_pass_starting_inside_the_grace_runs_no_15m_work_before_every_due_books_minute_is_decided(
        tmp_path, monkeypatch):
    eng, w = _world(tmp_path, monkeypatch, start_s=0.5)
    _run(eng, w)
    assert sorted(w.decided) == sorted(SYMBOLS)
    first_step = min(t for _, t in w.steps)
    assert max(w.decided.values()) <= first_step, (w.decided, w.steps)
    assert max(w.decided.values()) < GRACE_S + 1.0                      # every minute decided right at its grace
    assert w.stale == [] and max(w.entered.values()) < stale_s("1m")


def test_inside_one_book_the_1m_minute_is_decided_and_sent_before_its_15m_step(tmp_path, monkeypatch):
    """A single-book engine (gold alone) runs both parts in one call: the 1m part first."""
    eng, w = _world(tmp_path, monkeypatch, start_s=1.6)
    order: list[str] = []
    real_minutes, real_step = exploration._minutes, exploration.step
    def minutes(*a: Any) -> None:
        order.append("minutes")
        real_minutes(*a)

    def step(*a: Any) -> bool:
        order.append("step")
        return real_step(*a)
    monkeypatch.setattr(exploration, "_minutes", minutes)
    monkeypatch.setattr(exploration, "step", step)
    book = eng.books["SYM01"]
    with books.using(eng, book):
        exploration.on_pass(eng, Tick(ts_utc=w.now(), bid=100.0, ask=100.1))
    assert order == ["minutes", "step"]
    assert eng.books["SYM01"].__dict__["exploration"].slow_owed is None   # nothing left for a later sweep


def test_a_book_whose_1m_part_ran_in_a_pass_owes_its_15m_part_to_that_same_pass(tmp_path, monkeypatch):
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)            # no 1m work pending: the 15m part runs at once
    books.account_pass(eng, eng.broker, _one(w), budget_s=1e9)
    assert sorted(s for s, _ in w.steps) == sorted(SYMBOLS)
    assert all(b.__dict__["exploration"].slow_owed is None for b in eng.books.values() if not b.primary)


def test_15m_work_carried_past_the_budget_goes_first_next_pass_so_no_book_starves(tmp_path, monkeypatch):
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)
    one = _one(w)
    books.account_pass(eng, eng.broker, one)                           # 5 s budget: two 3 s steps
    first = [s for s, _ in w.steps]
    assert len(first) == 2
    books.account_pass(eng, eng.broker, one)
    assert [s for s, _ in w.steps][2:4] == [s for s in SYMBOLS if s not in first][:2]
    for _ in range(10):
        books.account_pass(eng, eng.broker, one)
    assert sorted(s for s, _ in w.steps) == sorted(SYMBOLS)            # each once: the closes were stepped


def test_15m_work_owed_longer_than_its_wait_limit_runs_even_while_1m_work_is_pending(tmp_path, monkeypatch):
    """A 1m signal stuck in a queue (an order in flight) never holds the 15m+ close work back past SLOW_WAIT_S."""
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)
    stuck = Pending(arm_id="S0-SYM05", side=1, bar_ts=CLOSE + pd.Timedelta(minutes=1), stop_usd=5.0,
                    target_usd=10.0, hold_bars=4)
    ex5 = eng.books["SYM05"].__dict__["exploration"]
    monkeypatch.setattr(exploration, "_enter", lambda *a, **k: 0)    # never leaves the queue
    ex5.queue.append(stuck)
    w.t = 61.0 + 1.6                                                    # the 07:16 signal is ready to send
    one = _one(w)
    books.account_pass(eng, eng.broker, one)
    assert w.steps == []                                                # 1m work first
    w.t += books.SLOW_WAIT_S
    books.account_pass(eng, eng.broker, one)
    assert len(w.steps) >= 1


def test_a_book_whose_pass_never_reaches_its_exploration_holds_no_other_books_15m_work(tmp_path, monkeypatch):
    """A book with no quote (a closed market) keeps its minute due for ever: it must not hold the account's 15m+ work
    behind SLOW_WAIT_S on every pass."""
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)
    for b in eng.books.values():
        if not b.primary and b.__dict__["cfg"].symbol != "SYM11":
            b.__dict__["exploration"].last_minute = CLOSE                  # decided: nothing pending
    one = _one(w)

    def no_quote_for_sym11(e: Engine, q: Any, s: str) -> bool:
        return True if s == "SYM11" else bool(one(e, q, s))
    books.account_pass(eng, eng.broker, no_quote_for_sym11)
    assert len(w.steps) == 2 and "SYM11" not in w.decided


def test_the_15m_part_never_sends_more_than_per_pass_with_the_1m_part(tmp_path, monkeypatch):
    """Only the order changes: one book's pass still sends at most PER_PASS orders in all."""
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)
    limits: list[int] = []

    def enter(e: Engine, ex: Any, now: pd.Timestamp, tick: Tick, limit: int = exploration.PER_PASS) -> int:
        limits.append(limit)
        return 3 if len(limits) == 1 else 0
    monkeypatch.setattr(exploration, "_enter", enter)
    ex = eng.books["SYM01"].__dict__["exploration"]
    ex.queue.append(Pending(arm_id="S0-SYM01", side=1, bar_ts=CLOSE, stop_usd=5.0, target_usd=10.0, hold_bars=4))
    with books.using(eng, eng.books["SYM01"]):
        exploration.on_pass(eng, Tick(ts_utc=w.now(), bid=100.0, ask=100.1))
    assert limits == [exploration.PER_PASS, exploration.PER_PASS - 3]


@pytest.mark.parametrize(("tf", "expected"), [("1m", ["learning", "minutes", "step"]),
                                               ("15m", ["minutes", "learning", "step"])])
def test_the_learning_read_comes_before_the_1m_send_when_a_1m_arm_waits_on_it(tmp_path, monkeypatch, tf, expected):
    """A learning arm's entries read its loss and retirement (`exploration_learning.refresh`): a 1m learning arm's
    send never runs on an older read than before; with no 1m/5m learning arm the read moves behind the 1m part."""
    eng, w = _world(tmp_path, monkeypatch, start_s=1.6)
    order: list[str] = []
    ex = eng.books["SYM01"].__dict__["exploration"]
    ex.arms["L-SYM01"] = SimpleNamespace(tf=tf, arm_id="L-SYM01", family="box", spec=SimpleNamespace(role="learning"))
    ex.learning_group = frozenset({"L-SYM01"})
    real_minutes, real_step = exploration._minutes, exploration.step

    def minutes(*a: Any) -> None:
        order.append("minutes")
        real_minutes(*a)

    def step(*a: Any) -> bool:
        order.append("step")
        return real_step(*a)
    monkeypatch.setattr(exploration, "_minutes", minutes)
    monkeypatch.setattr(exploration, "step", step)
    monkeypatch.setattr(exploration_learning, "refresh", lambda *a: order.append("learning"))
    with books.using(eng, eng.books["SYM01"]):
        exploration.on_pass(eng, Tick(ts_utc=w.now(), bid=100.0, ask=100.1))
    assert [o for i, o in enumerate(order) if o not in order[:i]] == expected


def test_an_order_that_raised_in_the_1m_part_holds_the_15m_parts_send_as_in_one_step(tmp_path, monkeypatch):
    """Trading-safety M1 (`_unsettled`): nothing else goes out in the pass an order raised in, on the live clock too."""
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)
    limits: list[int] = []

    def enter(e: Engine, ex: Any, now: pd.Timestamp, tick: Tick, limit: int = exploration.PER_PASS) -> int:
        limits.append(limit)
        if len(limits) == 1:
            e._auto_paused = (now, "order_raised")
        return 0
    monkeypatch.setattr(exploration, "_enter", enter)
    ex = eng.books["SYM01"].__dict__["exploration"]
    ex.queue.append(Pending(arm_id="S0-SYM01", side=1, bar_ts=CLOSE, stop_usd=5.0, target_usd=10.0, hold_bars=4))
    with books.using(eng, eng.books["SYM01"]):
        exploration.on_pass(eng, Tick(ts_utc=w.now(), bid=100.0, ask=100.1))
    assert limits == [exploration.PER_PASS, 0] and w.steps                 # the close step itself still ran


def test_a_1m_part_that_raises_runs_no_15m_part_and_is_journalled_as_before(tmp_path, monkeypatch):
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)

    def boom(*a: Any) -> None:
        raise ValueError("bad minute")
    monkeypatch.setattr(exploration, "_minutes", boom)
    with books.using(eng, eng.books["SYM01"]):
        exploration.on_pass(eng, Tick(ts_utc=w.now(), bid=100.0, ask=100.1))
        assert [d["action"] for d in eng.decisions if d.get("action") == "exploration_error"] == ["exploration_error"]
    assert w.steps == []


def test_the_deferral_warning_names_the_exploration_sub_phases(tmp_path, monkeypatch, caplog):
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)
    with caplog.at_level(logging.DEBUG, logger="goldbot.engine"):  # lane E: 15m+ carried only
        books.account_pass(eng, eng.broker, _one(w))
    [msg] = [r.getMessage() for r in caplog.records if "pass budget" in r.getMessage()]
    assert "exploration 3.00 [step 3.00]" in msg, msg
    assert "15m+ work carried for 9 books" in msg, msg


def test_pass_times_describe_lists_only_the_sub_phases_that_took_time():
    times = books.PassTimes()
    times.by = {"SYM01": {"exploration": 0.5, "exploration:step": 2.0, "exploration:minutes": 0.25,
                          "exploration:budget": 0.0, "bridge": 0.125}}
    assert times.describe("SYM01") == ("bridge 0.12, bars 0.00, zones 0.00, exploration 2.75 [step 2.00, "
                                       "minutes 0.25]")
    assert times.describe("SYM02") == "bridge 0.00, bars 0.00, zones 0.00, exploration 0.00"


# ------------------------------------------------------------------------------------------- review round 1
def _queued_15m(sym: str, arm: str | None = None) -> Pending:
    return Pending(arm_id=arm or f"B-{sym}", side=1, bar_ts=CLOSE, stop_usd=5.0, target_usd=10.0, hold_bars=4)


def _decided(eng: Engine) -> None:
    """Every book's 07:15 minute decided: no 1m/5m work pending."""
    for b in eng.books.values():
        if not b.primary:
            b.__dict__["exploration"].last_minute = CLOSE


def test_a_queued_15m_learning_signal_is_refused_by_a_learning_read_that_retires_its_arm_before_any_send(
        tmp_path, monkeypatch):
    """Quant C1 / safety 4: a learning-group signal already queued is never sent on an older learning read than the
    single step used: the read runs before the 1m/5m part's send whenever such a signal waits."""
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)
    ex = eng.books["SYM01"].__dict__["exploration"]
    ex.last_minute = CLOSE
    ex.arms["L-SYM01"] = SimpleNamespace(tf="15m", arm_id="L-SYM01", family="box",
                                         spec=SimpleNamespace(role="learning"))
    ex.learning_group = frozenset({"L-SYM01"})
    ex.queue.append(_queued_15m("SYM01", "L-SYM01"))
    refused: list[tuple[str, str]] = []

    def learning_read(e: Engine, x: Any, now: pd.Timestamp) -> None:      # the ledger read retires the arm
        x.retired = x.retired | {"L-SYM01"}
        for s in [s for s in x.queue if s.arm_id in x.retired]:
            x.queue.remove(s)
            refused.append((s.arm_id, "arm_retired"))
    monkeypatch.setattr(exploration_learning, "refresh", learning_read)
    with books.using(eng, eng.books["SYM01"]):
        exploration.on_pass(eng, Tick(ts_utc=w.now(), bid=100.0, ask=100.1))
    assert refused == [("L-SYM01", "arm_retired")] and "L-SYM01" not in w.entered


def test_every_books_15m_close_is_stepped_before_the_next_close_under_a_sustained_1m_backlog(tmp_path, monkeypatch):
    """Quant C3: 1m work pending on every book on every pass (signals stuck behind an order in flight) never leaves a
    15m close unevaluated: each carried book's 15m+ part runs (SLOW_WAIT_S) before the next close."""
    eng, w = _world(tmp_path, monkeypatch, start_s=1.6)
    monkeypatch.setattr(exploration, "_enter", lambda *a, **k: 0)        # nothing ever leaves a queue
    one = _one(w)
    nxt = CLOSE + pd.Timedelta(minutes=15)
    while w.t < 1000.0:
        if w.now() >= nxt:
            for b in eng.books.values():
                if not b.primary:
                    with books.using(eng, b):
                        eng._closed_through = nxt
        books.account_pass(eng, eng.broker, one)
        w.t += 1.0
    for sym in SYMBOLS:
        assert [c for s, c in w.closes if s == sym] == [CLOSE, nxt], sym
    assert max(t for (_, t), (_, c) in zip(w.steps, w.closes, strict=True) if c == CLOSE) < 900.0
    assert all(b.__dict__["exploration"].queue for b in eng.books.values() if not b.primary)   # the backlog held


def test_a_carried_book_that_goes_quiet_leaves_the_carry_and_the_deferral_warning_logs_again(
        tmp_path, monkeypatch, caplog):
    """Code review LOW 1: a carry key of a book no longer in the pass never keeps every pass `deferred`."""
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)
    _decided(eng)
    eng.__dict__["_slow_carry"] = {"SYM05": w.t}
    eng.__dict__["_pass_was_deferred"] = True
    monkeypatch.setattr(books, "quiet_books", lambda e: {"SYM05"})
    books.account_pass(eng, eng.broker, _one(w), budget_s=1e9)
    assert "SYM05" not in eng.__dict__["_slow_carry"] and not eng.__dict__["_pass_was_deferred"]
    for b in eng.books.values():                                        # a new close: the budget carries again
        if not b.primary:
            b.__dict__["exploration"].last_close = CLOSE - pd.Timedelta(minutes=15)
    with caplog.at_level(logging.DEBUG, logger="goldbot.engine"):
        books.account_pass(eng, eng.broker, _one(w))
    assert [r for r in caplog.records if "pass budget" in r.getMessage()]


def _step_queues(monkeypatch: pytest.MonkeyPatch, sym: str) -> None:
    """The 15m+ step queues one 15m signal on `sym`: its send is the 15m+ part's own."""
    real = exploration.step

    def step(e: Engine, close_ts: pd.Timestamp) -> bool:
        if e.cfg.symbol == sym and e.exploration is not None:
            e.exploration.queue.append(_queued_15m(sym))
        return bool(real(e, close_ts))
    monkeypatch.setattr(exploration, "step", step)


class _Quotes:
    """The pass's shared quote reader: the bid has moved since the pass started."""

    def __init__(self, w: _World) -> None:
        self.w = w

    def last_tick(self, symbol: str) -> Tick:
        return Tick(ts_utc=self.w.now(), bid=105.0, ask=105.1)


def test_the_15m_send_uses_the_quote_read_when_it_is_sent_not_the_one_read_at_the_pass_start(tmp_path, monkeypatch):
    """Code review LOW 2 / safety 5: the 15m+ part runs seconds after the book's pass; its send prices on a quote read
    then."""
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)
    _decided(eng)
    _step_queues(monkeypatch, "SYM01")
    books.account_pass(eng, _Quotes(w), _one(w), budget_s=1e9)
    assert ("SYM01", 105.0) in w.ticks and ("SYM01", 100.0) not in w.ticks


def test_a_15m_send_waits_for_the_next_pass_when_the_quote_cannot_be_read_again(tmp_path, monkeypatch):
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)
    _decided(eng)
    _step_queues(monkeypatch, "SYM01")

    class Dead:
        def last_tick(self, symbol: str) -> Tick:
            raise TimeoutError("bridge")
    books.account_pass(eng, Dead(), _one(w), budget_s=1e9)
    assert not w.ticks and eng.books["SYM01"].__dict__["exploration"].queue      # kept for the next pass


def test_an_order_that_raised_on_one_book_holds_another_books_15m_send_in_that_pass(tmp_path, monkeypatch):
    """Safety 6: the order-raised hold is the account's for the pass, not the raising book's alone."""
    eng, w = _world(tmp_path, monkeypatch, start_s=8.0)
    _decided(eng)
    for sym in ("SYM01", "SYM02"):
        eng.books[sym].__dict__["exploration"].queue.append(_queued_15m(sym))
    calls: list[tuple[str, int]] = []

    def enter(e: Engine, ex: Any, now: pd.Timestamp, tick: Tick, limit: int = exploration.PER_PASS) -> int:
        calls.append((e.cfg.symbol, limit))
        if e.cfg.symbol == "SYM02" and len(calls) == 2:
            e._auto_paused = (now, "order_raised")                      # SYM02's 1m/5m part: its order raised
        return 0
    monkeypatch.setattr(exploration, "_enter", enter)
    books.account_pass(eng, eng.broker, _one(w), budget_s=1e9)
    assert calls[:2] == [("SYM01", exploration.PER_PASS), ("SYM02", exploration.PER_PASS)]
    assert [lim for s, lim in calls[2:] if s == "SYM01"] == [0]
