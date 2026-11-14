"""Demo entry volume drop 2026-11-09 (SRE diagnosis, cause 5, fixes 3 and 4): pass-budget starvation at bar closes.

(a) `exploration_stale` counts only signals that could still have entered: a stale signal of an arm refused new
    entries for good (retired, or a thin control whose cost cell is complete) is recorded with that refusal instead.
    No order changes: a stale signal is never sent either way.
(b) The pass-budget warning names, for the slowest books, where the pass went: bridge calls, bar close (frames,
    features, decisions), key zones and exploration.
(c) A book whose every 1m/5m arm is refused new entries for good does no minute work for them and is not `due`; its
    exits, reconciliation and position reads still run every pass (it is never made quiet).
(d) Gold's key-zones build starts after the pass's entries were evaluated, never on the bar close itself, and builds
    exactly what it would have built at the close.
"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.data.resample import ticks_to_1m
from goldbot.engine import Engine, EngineConfig, books, exploration, key_zones
from goldbot.engine import exploration_budget as budget
from goldbot.engine.books import BookSpec
from goldbot.engine.exploration import Arm, Pending
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops.accounts import DailyEntries
from goldbot.research import arm_rules
from goldbot.research import exploration_data as ed
from tests.test_exploration_engine import (
    CLOSE,
    TICKS,
    _actions,
    _arm,
    _at,
    _rules,  # noqa: F401 - the always-long rule and the fixed loss (autouse)
    _sent,
)
from tests.test_exploration_engine import _engine as _base_engine

pytestmark = pytest.mark.usefixtures("_rules")
DAILY = DailyEntries(max_per_day=100, thin_control_fills=30)


def _thin(arm_id: str = "S0-US500", tf: str = "1m") -> Arm:
    """An unpaired per-instrument control (`exploration_budget.entry_kind` "thin"): stopped at its cost cell's fills."""
    a = _arm(arm_id, tf=tf, style="scalp", control_share=0.25, max_open=10)
    return a.model_copy(update={"spec": a.spec.model_copy(update={"params": {**a.spec.params,
                                                                             "instrument": "US500"}})})


def _engine(tmp_path: Path, arms: list[Arm], cell: int, monkeypatch: pytest.MonkeyPatch) -> tuple[Engine, Any]:
    eng, pb = _base_engine(tmp_path, arms)
    assert eng.exploration is not None
    eng.exploration.cfg = eng.exploration.cfg.model_copy(update={"daily": DAILY})
    monkeypatch.setattr(budget, "cell_fills", lambda *a, **k: {"US500": cell})
    return eng, pb


def _stale(eng: Engine, pb: Any, arm_id: str) -> pd.Timestamp:
    """One signal of `arm_id` queued at CLOSE and handed to `_enter` a minute later (past every 1m stale limit)."""
    ex = eng.exploration
    assert ex is not None
    tick = _at(eng, pb, CLOSE + pd.Timedelta(minutes=1), lag_s=2.0)
    ex.queue.append(Pending(arm_id=arm_id, side=1, bar_ts=CLOSE, stop_usd=5.0, target_usd=10.0, hold_bars=4))
    exploration._enter(eng, ex, tick.ts_utc, tick)
    return tick.ts_utc


def _blocked(tmp_path: Path) -> list[str]:
    return list(ed.read_obs(exploration.ledger_db(str(tmp_path)))["blocked_reason"])


# ------------------------------------------------------------------------------------------- (a) stale order
def test_a_stale_signal_of_a_thin_control_whose_cost_cell_is_complete_is_recorded_as_that_refusal_not_stale(
        tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_thin()], cell=30, monkeypatch=monkeypatch)
    _stale(eng, pb, "S0-US500")
    assert not _actions(eng, "exploration_stale")
    [row] = _actions(eng, "exploration_refused:")
    assert (row["action"], row["arm"]) == ("exploration_refused:thin_cell_complete", "S0-US500") and row["age_s"] > 10
    assert _blocked(tmp_path) == ["thin_cell_complete"]
    assert not _sent(eng) and not eng.open and eng.exploration is not None and not eng.exploration.queue


def test_a_stale_signal_of_a_retired_arm_is_recorded_arm_retired_not_stale(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_arm("a", tf="1m", style="scalp")], cell=0, monkeypatch=monkeypatch)
    assert eng.exploration is not None
    eng.exploration.retired = frozenset({"a"})
    _stale(eng, pb, "a")
    assert not _actions(eng, "exploration_stale")
    assert [r["action"] for r in _actions(eng, "exploration_refused:")] == ["exploration_refused:arm_retired"]
    assert _blocked(tmp_path) == ["arm_retired"] and not _sent(eng)


def test_a_stale_signal_of_an_arm_that_could_still_have_entered_is_recorded_stale_as_before(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_thin(), _arm("a", tf="1m", style="scalp")], cell=29, monkeypatch=monkeypatch)
    _stale(eng, pb, "S0-US500")
    _stale(eng, pb, "a")
    assert [r["agent"] for r in _actions(eng, "exploration_stale")] == [exploration.PREFIX + "S0-US500",
                                                                         exploration.PREFIX + "a"]
    assert not _actions(eng, "exploration_refused:") and _blocked(tmp_path) == ["stale", "stale"]
    assert not _sent(eng)


def test_a_terminal_check_that_cannot_read_the_ledger_records_the_stale_signal_stale_and_the_pass_goes_on(
        tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_thin()], cell=30, monkeypatch=monkeypatch)

    def boom(*a: Any, **k: Any) -> dict[str, int]:
        raise OSError("ledger locked")
    monkeypatch.setattr(budget, "cell_fills", boom)
    _stale(eng, pb, "S0-US500")
    assert [r["action"] for r in _actions(eng, "exploration_stale")] == ["exploration_stale"]
    assert not _actions(eng, "exploration_error") and not _sent(eng)


def test_a_fresh_signal_of_a_thin_control_whose_cell_is_complete_is_refused_in_send_exactly_as_before(
        tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_thin()], cell=30, monkeypatch=monkeypatch)
    ex = eng.exploration
    assert ex is not None
    tick = _at(eng, pb, CLOSE, lag_s=8.5)                                # past its send delay, inside 10 s
    ex.queue.append(Pending(arm_id="S0-US500", side=1, bar_ts=CLOSE, stop_usd=5.0, target_usd=10.0, hold_bars=4))
    exploration._enter(eng, ex, tick.ts_utc, tick)
    [row] = _actions(eng, "exploration_refused:")
    assert row["action"] == "exploration_refused:thin_cell_complete" and "proposal" in row   # `_send`'s own row
    assert not _sent(eng)


# ------------------------------------------------------------------------------------------- (c) dormant minutes
def _minute_pass(eng: Engine, pb: Any, minute: pd.Timestamp) -> None:
    tick = _at(eng, pb, CLOSE)
    eng.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < minute])
    exploration.on_pass(eng, tick.model_copy(update={"ts_utc": minute + pd.Timedelta(seconds=2)}))


def _count_rule(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real = arm_rules.screened

    def screened(arm: Any, ctx: Any) -> Any:
        seen.append(arm.id)
        return real(arm, ctx)
    monkeypatch.setattr(arm_rules, "screened", screened)
    return seen


def test_a_1m_arm_refused_for_good_reads_no_minute_and_its_book_is_never_due_for_it(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_thin()], cell=30, monkeypatch=monkeypatch)
    seen = _count_rule(monkeypatch)
    for k in (1, 2, 3):
        _minute_pass(eng, pb, CLOSE + pd.Timedelta(minutes=k))
    ex = eng.exploration
    assert ex is not None
    assert seen == [] and not ex.queue and not _actions(eng, "exploration_refused:")
    assert ex.dormant == frozenset({"S0-US500"})
    assert not exploration.due(ex, CLOSE + pd.Timedelta(minutes=4, seconds=3))
    [row] = _actions(eng, "exploration_arm_dormant")                     # journalled once, with its reason
    assert (row["arm"], row["reason"]) == ("S0-US500", "thin_cell_complete")


def test_a_1m_arm_that_can_still_enter_is_decided_every_minute_as_before(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_thin()], cell=29, monkeypatch=monkeypatch)
    seen = _count_rule(monkeypatch)
    _minute_pass(eng, pb, CLOSE + pd.Timedelta(minutes=1))
    ex = eng.exploration
    assert ex is not None and seen == ["S0-US500"] and ex.dormant == frozenset()
    assert exploration.due(ex, CLOSE + pd.Timedelta(minutes=2, seconds=3))


def test_a_dormant_arm_beside_a_live_one_leaves_the_live_ones_minute_work_unchanged(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_thin(), _arm("a", tf="1m", style="scalp", max_open=10)], cell=30,
                      monkeypatch=monkeypatch)
    seen = _count_rule(monkeypatch)
    _minute_pass(eng, pb, CLOSE + pd.Timedelta(minutes=1))
    assert seen == ["a"] and [d["agent"] for d in _sent(eng)] == [exploration.PREFIX + "a"]


def test_an_unreadable_cost_cell_keeps_the_arm_working_until_a_read_says_it_is_complete(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, [_thin()], cell=30, monkeypatch=monkeypatch)

    def boom(*a: Any, **k: Any) -> dict[str, int]:
        raise OSError("ledger locked")
    monkeypatch.setattr(budget, "cell_fills", boom)
    seen = _count_rule(monkeypatch)
    _minute_pass(eng, pb, CLOSE + pd.Timedelta(minutes=1))
    assert seen == ["S0-US500"] and eng.exploration is not None and eng.exploration.dormant == frozenset()


SPECS = tuple(BookSpec(key=f"SYM{k:02d}", symbol=f"SYM{k:02d}", store_symbol=f"SYM{k:02d}", magic_block=k, digits=2,
                       point=0.01, contract_size=100.0) for k in range(1, 4))
NOW = pd.Timestamp("2025-03-05 10:00:03", tz="UTC")


class _Multi(PaperBroker):
    def on_tick(self, tick: Tick) -> None:
        return None


def _multi(tmp_path: Path) -> Engine:
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", magic_base=260500,
                              state_dir=str(tmp_path), instruments=SPECS), _Multi(equity=10_000), [], {})
    eng.clock = lambda: NOW
    return eng


def test_a_book_whose_minute_arms_are_all_dormant_still_runs_its_exits_and_its_pass_every_pass(tmp_path, monkeypatch):
    """Never quiet: its open positions, close retries, reconciliation and position reads run as every book's do."""
    eng = _multi(tmp_path)
    arm = SimpleNamespace(tf="1m", arm_id="S0-SYM02")
    eng.books["SYM02"].__dict__["exploration"] = SimpleNamespace(arms={"S0-SYM02": arm}, last_minute=None,
                                                                 queue=deque(), dormant=frozenset({"S0-SYM02"}),
                                                                 deregistered=frozenset())
    exits: list[str] = []
    seen: list[str] = []

    def exits_first(e: Engine, q: Any, s: str) -> bool:
        exits.append(s)
        return True

    def one(e: Engine, q: Any, s: str) -> bool:
        seen.append(s)
        return True
    monkeypatch.setattr(books, "_exits_first", exits_first)
    books.account_pass(eng, eng.broker, one)
    assert "SYM02" not in books.quiet_books(eng)
    assert sorted(exits) == sorted(seen) == sorted(eng.books)
    assert not exploration.due(eng.books["SYM02"].__dict__["exploration"], NOW)


# ------------------------------------------------------------------------------------------- (b) phase timings
def test_the_pass_budget_warning_names_each_slow_books_bridge_bar_close_zones_and_exploration_time(
        tmp_path, monkeypatch, caplog):
    eng = _multi(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(books, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    real = eng.broker.positions

    def slow_positions() -> Any:
        clock[0] += 0.25
        return real()
    monkeypatch.setattr(eng.broker, "positions", slow_positions)

    def one(e: Engine, q: Any, s: str) -> bool:
        with books.phase(e, "bars"):
            clock[0] += 0.5
            e.broker.positions()                     # a bridge call inside the bar close: counted as bridge only
        books.timed(e, "zones", lambda: clock.__setitem__(0, clock[0] + 0.125))
        books.timed(e, "exploration", lambda: clock.__setitem__(0, clock[0] + 1.0))
        clock[0] += 0.0625
        return True
    with caplog.at_level(logging.WARNING, logger="goldbot.engine"):
        books.account_pass(eng, eng.broker, one, budget_s=2.0)
    [msg] = [r.getMessage() for r in caplog.records if "pass budget" in r.getMessage()]
    assert "XAUUSD 1.94 s (bridge 0.25, bars 0.50, zones 0.12, exploration 1.00)" in msg, msg
    assert "phases: bridge 0.50, bars 1.00, zones 0.25, exploration 2.00" in msg, msg
    assert eng.__dict__.get("_pass_times") is None                     # only while a pass runs


def test_phase_timing_outside_an_account_pass_runs_the_step_and_records_nothing(tmp_path):
    eng = _multi(tmp_path)
    assert books.timed(eng, "exploration", lambda x: x + 1, 41) == 42
    with books.phase(eng, "bars"):
        pass
    assert eng.__dict__.get("_pass_times") is None


def test_a_step_that_raises_inside_a_phase_raises_unchanged(tmp_path):
    eng = _multi(tmp_path)
    eng.__dict__["_pass_times"] = books.PassTimes()

    def bad() -> None:
        raise ValueError("x")
    with pytest.raises(ValueError):
        books.timed(eng, "bars", bad)
    assert eng.__dict__["_pass_times"].by[""]["bars"] >= 0.0


# ------------------------------------------------------------------------------------------- (d) key zones
class _Worker:
    """A worker whose submits are recorded and whose builds never finish (the thread has not run)."""

    def __init__(self) -> None:
        self.submits: list[tuple[pd.Timestamp, float, int]] = []
        self.sessions: Any = None
        self.parked: Any = None

    def collect(self) -> None:
        return None

    def busy(self) -> bool:
        return False

    def submit(self, recent: pd.DataFrame, t: pd.Timestamp, price: float) -> bool:
        self.submits.append((t, price, len(recent)))
        return True


def _zones_engine() -> Any:
    from goldbot.data.resample import resample_bars
    return SimpleNamespace(key_zones=None, key_zones_at=None, key_zone_worker=_Worker(),
                           key_zones_errors=set(), sessions="calendar", _resample=lambda c, tf: resample_bars(c, tf))


def test_the_bar_close_only_parks_the_zones_build_and_launch_starts_it_with_the_closes_own_inputs():
    eng = _zones_engine()
    bars = ticks_to_1m(TICKS)
    t0 = pd.Timestamp("2025-03-04 10:15", tz="UTC")
    key_zones.update(eng, bars[bars["visible_at"] <= t0], t0)
    assert eng.key_zone_worker.submits == [] and eng.key_zone_worker.parked is not None
    key_zones.update(eng, bars[bars["visible_at"] <= t0 + pd.Timedelta(minutes=1)], t0 + pd.Timedelta(minutes=1))
    key_zones.launch(eng)
    [(t, price, n)] = eng.key_zone_worker.submits
    last = bars[bars["visible_at"] <= t0].iloc[-1]
    assert t == t0 and price == pytest.approx((last["bid_close"] + last["ask_close"]) / 2)   # the close's, not later
    assert eng.key_zone_worker.sessions == "calendar" and eng.key_zone_worker.parked is None
    key_zones.launch(eng)
    assert len(eng.key_zone_worker.submits) == 1                         # once


def test_a_single_book_engine_starts_the_zones_build_only_after_its_exploration_pass(tmp_path, monkeypatch):
    from tests.test_engine_key_zones import BARS, _n_at_15m_close
    from tests.test_engine_key_zones import _engine as kz_engine
    eng = kz_engine(tmp_path)
    order: list[str] = []

    def spawn(fn: Any) -> None:
        order.append("build")
        fn()

    def on_pass(e: Engine, t: Tick) -> None:
        order.append("exploration")
    setattr(eng.key_zone_worker, "_spawn", spawn)
    monkeypatch.setattr(exploration, "on_pass", on_pass)
    n = _n_at_15m_close()
    eng.bars_1m = BARS.iloc[:n].reset_index(drop=True)
    close_ts = pd.Timestamp(eng.bars_1m["visible_at"].iloc[-1])
    setattr(eng, "_rebuild_bars", lambda until=None: None)
    eng.last_bar_close = close_ts - pd.Timedelta(minutes=15)
    eng._closed_through = eng.last_bar_close
    last = eng.bars_1m.iloc[-1]
    eng.on_tick(Tick(ts_utc=close_ts + pd.Timedelta(seconds=2), bid=float(last["bid_close"]),
                     ask=float(last["ask_close"])))
    assert order == ["exploration", "build"] and eng.key_zones is not None


def test_a_multi_book_pass_starts_the_zones_build_after_every_books_pass_and_never_inside_one(tmp_path):
    eng = _multi(tmp_path)
    w = _Worker()
    setattr(eng, "key_zone_worker", w)
    w.parked = (pd.DataFrame({"x": [1]}), NOW.floor("15min"), 2000.0, "calendar")
    seen: list[str] = []

    def one(e: Engine, q: Any, s: str) -> bool:
        seen.append(f"{s}:{len(w.submits)}")
        return True
    books.account_pass(eng, eng.broker, one)
    assert all(s.endswith(":0") for s in seen) and len(seen) == 4 and len(w.submits) == 1


def test_a_deferred_pass_holds_the_zones_build_until_a_pass_completes_or_it_waited_its_limit(tmp_path, monkeypatch):
    eng = _multi(tmp_path)
    w = _Worker()
    setattr(eng, "key_zone_worker", w)
    w.parked = (pd.DataFrame({"x": [1]}), NOW.floor("15min"), 2000.0, "calendar")
    clock = [100.0]
    monkeypatch.setattr(books, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def slow(e: Engine, q: Any, s: str) -> bool:
        clock[0] += 1.0
        return True
    books.account_pass(eng, eng.broker, slow, budget_s=1.5)              # deferred: no build
    assert w.submits == []
    clock[0] += books.ZONES_WAIT_S
    books.account_pass(eng, eng.broker, slow, budget_s=1.5)              # still deferred, but waited its limit
    assert len(w.submits) == 1


def _bar_close(eng: Engine, n: int) -> None:
    """The bar close alone (`Engine.on_bar_close` on the first `n` minutes): what parks the build, nothing after."""
    from tests.test_engine_key_zones import BARS
    b = BARS.iloc[:n].reset_index(drop=True)
    eng.bars_1m = b
    close_ts = pd.Timestamp(b["visible_at"].iloc[-1])
    setattr(eng, "_rebuild_bars", lambda until=None: None)
    eng.on_bar_close(close_ts, Tick(ts_utc=close_ts, bid=float(b["bid_close"].iloc[-1]),
                                    ask=float(b["ask_close"].iloc[-1])))


def test_a_zones_build_started_later_publishes_the_same_zones_as_one_started_at_the_close(tmp_path):
    from tests.test_engine_key_zones import _engine as kz_engine
    from tests.test_engine_key_zones import _n_at_15m_close
    n = _n_at_15m_close()
    now_eng = kz_engine(tmp_path / "now")
    _bar_close(now_eng, n)
    key_zones.launch(now_eng)
    later = kz_engine(tmp_path / "later")
    _bar_close(later, n)
    _bar_close(later, n + 3)                  # three more minutes of bars before the build starts (no new 15m bar)
    assert later.key_zones is None
    key_zones.launch(later)
    assert now_eng.key_zones is not None and later.key_zones == now_eng.key_zones


def test_a_bridge_call_from_another_thread_is_not_timed_into_the_pass():
    import threading
    times = books.PassTimes()

    def other() -> None:
        with times.phase("bridge"):
            pass
    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert times.by == {}


def test_a_book_pass_that_raises_leaves_no_later_time_charged_to_that_book(tmp_path, monkeypatch):
    """Code review LOW-2: the timed book is reset even when its pass raises."""
    eng = _multi(tmp_path)
    made: list[books.PassTimes] = []

    class Recording(books.PassTimes):
        def __init__(self) -> None:
            super().__init__()
            made.append(self)
    monkeypatch.setattr(books, "PassTimes", Recording)

    def boom(e: Engine, q: Any, s: str) -> bool:
        raise RuntimeError("book pass failed")
    with pytest.raises(RuntimeError):
        books.account_pass(eng, eng.broker, boom)
    [times] = made
    assert times.book == ""
    with times.phase("bridge"):                       # what runs after it is the pass's own, never XAUUSD's
        pass
    assert "bridge" not in times.by.get("XAUUSD", {}) and "bridge" in times.by[""]


# ------------------------------------------------------------------------------------------- trading-safety round
def test_a_build_whose_thread_cannot_start_leaves_the_worker_free_and_the_next_launch_builds(tmp_path):
    """Trading-safety L1: a `_spawn` that raises never leaves a job that is never done (busy for ever)."""
    from tests.test_engine_key_zones import _engine as kz_engine
    from tests.test_engine_key_zones import _n_at_15m_close
    eng = kz_engine(tmp_path)
    calls: list[int] = []

    def spawn(fn: Any) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("can't start new thread")
        fn()
    setattr(eng.key_zone_worker, "_spawn", spawn)
    _bar_close(eng, _n_at_15m_close())
    key_zones.launch(eng)                                                # the thread did not start
    assert not eng.key_zone_worker.busy() and eng.key_zone_worker.parked is not None and eng.key_zones is None
    key_zones.launch(eng)                                                # the next pass builds it
    assert len(calls) == 2 and eng.key_zones is not None and eng.key_zone_worker.parked is None


class _CountingDb:
    """The ledger db, counting its connections (one per read)."""

    def __init__(self, db: Any) -> None:
        self.db, self.reads = db, 0

    def connection(self) -> Any:
        self.reads += 1
        return self.db.connection()


def test_the_cost_cells_are_read_once_a_minute_per_account_not_once_per_book(tmp_path):
    """Trading-safety L2: every book's dormant check and send read the same account-wide count."""
    from goldbot.engine.exploration import LEDGER
    from tests.test_exploration_daily_budget import _rec
    db = exploration.ledger_db(str(tmp_path))
    with db.write() as conn:
        LEDGER.upsert(conn, _rec(1, "S0-US500", CLOSE, symbol="US500"))
    counting = _CountingDb(db)
    eng: Any = SimpleNamespace(cfg=SimpleNamespace(account_id="icm-probe"))
    books_ex: list[Any] = [SimpleNamespace(db=counting, budget_cache={}) for _ in range(5)]
    for ex in books_ex:
        assert budget.cell_fills(eng, ex, CLOSE) == {"US500": 1}
    assert counting.reads == 1
    budget.cell_counted(eng, books_ex[2], _thin())                               # a fill on one book ...
    assert budget.cell_fills(eng, books_ex[4], CLOSE) == {"US500": 2}             # ... every book sees it
    assert budget.cell_fills(eng, books_ex[0], CLOSE + pd.Timedelta(minutes=1)) == {"US500": 2}   # never falls
    assert counting.reads == 2


class _Bridge:
    """A broker whose figure and order fail with a typed error, counting every call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def account_positions(self) -> list[str]:
        self.calls.append("account_positions")
        return ["p"]

    def account(self) -> str:
        self.calls.append("account")
        return "acc"

    def loss_per_lot(self, *a: Any) -> float:
        self.calls.append("loss_per_lot")
        if a and a[0] == "bad":
            raise KeyError("bad", 7)
        return 1.5

    def place_order(self, *a: Any, **k: Any) -> str:
        self.calls.append("place_order")
        raise TimeoutError("bridge", 15.0)

    def close(self, *a: Any, **k: Any) -> str:
        self.calls.append("close")
        return "closed"

    def modify(self, *a: Any, **k: Any) -> str:
        self.calls.append("modify")
        return "modified"


@pytest.mark.parametrize("write", ["place_order", "close", "modify"])
def test_a_timed_pass_broker_clears_its_reads_on_every_write_and_raises_the_writes_own_error(write):
    """Trading-safety I3: the timing changes nothing the PassBroker serves."""
    inner = _Bridge()
    pb = books.PassBroker(inner, books.PassTimes())
    assert pb.account_positions() == ["p"] and pb.account() == "acc"
    pb.account_positions()
    pb.account()
    assert inner.calls.count("account_positions") == inner.calls.count("account") == 1
    if write == "place_order":
        with pytest.raises(TimeoutError) as err:
            pb.place_order("intent")
        assert err.type is TimeoutError and err.value.args == ("bridge", 15.0)
    else:
        assert getattr(pb, write)("ticket") == {"close": "closed", "modify": "modified"}[write]
    pb.account_positions()
    pb.account()
    assert inner.calls.count("account_positions") == inner.calls.count("account") == 2
    assert set(pb.__dict__["_times"].by[""]) == {"bridge"}


def test_a_timed_pass_broker_asks_each_figure_once_and_raises_its_error_unchanged_every_time():
    inner = _Bridge()
    pb = books.PassBroker(inner, books.PassTimes())
    assert pb.loss_per_lot("ok", 1) == pb.loss_per_lot("ok", 1) == 1.5
    for _ in range(2):
        with pytest.raises(KeyError) as err:
            pb.loss_per_lot("bad")
        assert err.type is KeyError and err.value.args == ("bad", 7)
    assert inner.calls.count("loss_per_lot") == 2                             # once per exact question
    pb.close("ticket")                                                          # a write never clears figures
    pb.loss_per_lot("ok", 1)
    assert inner.calls.count("loss_per_lot") == 2
