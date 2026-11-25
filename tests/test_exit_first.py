"""Exits before entries on a demo account without limits (trading-safety H1/H2 of 2026-11-25; ADR 0097).

Once the slot, global and daily caps are lifted, entries could keep the serial MT5 box (and the engine's tick thread,
which waits for each entry's reply) busy most of the time, so a software exit (time barrier, weekend cut, trail close,
kill-switch flatten, engine stop) would wait behind entry after entry. The broker's own stop still fires, but K3
("exits never gated, never queued behind entries") would be broken in practice. The rules (goldbot/engine/order_send.py):

* exit-first: no entry is sent while any book has a close the engine decided and has not got filled (unless it is in
  its back-off until its next attempt), or a kill-switch flatten pending; a stop-modify retry never holds entries
  (the pass's reconcile sends it first; code review C-M1);
* an entry duty-cycle ceiling: no entry starts once this pass's entries have held the tick thread ENTRY_PASS_S, or
  while entries held it DUTY_MAX of the last DUTY_WINDOW_S;
* the broker's own capacity answers (10033 order limit, 10040 position limit) are refusals that hold entries at least
  CAPACITY_HOLD_S, then until the account holds fewer positions, never a retry loop;
* every loop pass's gap is measured; the p99 of the last 15 min is published with the account's SLO, and health
  alerts above it on an unlimited account (a published baseline elsewhere).

These are engineering rules, not risk limits: they decide when an entry goes, never whether a trade is allowed.
Accounts without `demo_unlimited` keep their behaviour exactly (their caps bound the entry rate already)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.engine import order_send
from goldbot.engine.order_send import EntryYield, OrderInFlight
from goldbot.execution.broker import OrderIntent, OrderResult
from goldbot.execution.paper import PaperBroker
from goldbot.ops.services.engine import engine_pass
from tests.test_engine_safety import T0, _prop, _tick
from tests.test_order_not_confirmed import AGENT, SL, TP, _engine


def _free(eng: Any, on: bool = True) -> Any:
    eng.gate.limits = eng.gate.limits.model_copy(update={"demo_unlimited": on, "probe_only": True})
    return eng


def _paper(tmp_path: Path, pb: PaperBroker | None = None, *, free: bool = True) -> Any:
    pb = pb if pb is not None else PaperBroker(equity=1_000_000)
    pb.on_tick(_tick(T0))
    eng, _ = _engine(tmp_path, pb)
    return _free(eng, free)


def _send(eng: Any, pid: str) -> OrderResult | None:
    return eng._execute(_prop(pid), AGENT, 0.01, SL, TP, requested=2400.2)


def _open(eng: Any, pb: PaperBroker, n: int) -> list[int]:
    for i in range(n):
        side = 1 if i % 2 == 0 else -1
        pb.place_order(OrderIntent(client_order_id=f"held{i}", symbol="XAUUSD", side=side, lots=0.01,
                                   sl=2400.0 - side * 50, tp=2400.0 + side * 50, magic=eng.cfg.magic_base,
                                   comment=f"held{i}"))
    eng._reconcile(None)
    return sorted(eng.open)


# ---------------------------------------------------------------------------------------------- exit-first
def test_a_close_still_due_means_no_entry_is_sent_and_nothing_reaches_the_broker(tmp_path) -> None:
    eng = _paper(tmp_path)
    pid = _open(eng, eng.broker, 1)[0]
    eng._close_due[pid] = ("time_exit", {"symbol": "XAUUSD"}, None)
    before = len(eng.broker.positions())
    with pytest.raises(EntryYield) as exc:
        _send(eng, "e1")
    assert exc.value.reason == order_send.EXIT_FIRST
    assert len(eng.broker.positions()) == before and "e1" not in eng._orders     # refused before its row
    eng._close_due.clear()
    res = _send(eng, "e2")
    assert res is not None and res.ok


def test_a_pending_kill_switch_flatten_holds_every_entry(tmp_path) -> None:
    eng = _paper(tmp_path)
    eng._kill_pending = True
    with pytest.raises(EntryYield):
        _send(eng, "e1")


@pytest.mark.parametrize("due_in_s", [-1.0, 60.0])
def test_a_stop_modify_retry_never_holds_entries_due_or_backing_off(tmp_path, due_in_s) -> None:
    """Code review C-M1: a modify retry is sent by the pass's reconcile, which runs before that pass's entries, so it is
    never behind an entry. Counting it made a stale back-off entry (a reinstate rejected, then the stop tightened by
    another path) hold every entry for good, and a due one stall entries until the next reconcile."""
    eng = _paper(tmp_path)
    pid = _open(eng, eng.broker, 1)[0]
    eng._modify_backoff[pid] = (3, eng._last_tick.ts_utc + pd.Timedelta(seconds=due_in_s))
    assert order_send.entry_yield(eng) is None
    assert _send(eng, "e1") is not None


def test_a_close_in_its_refusal_back_off_does_not_hold_entries_until_its_next_attempt(tmp_path) -> None:
    """Trading-safety L3: any back-off (market closed, or 30 refusals in a row) means the close is not on the box
    until its next attempt; the per-tick stop check and the broker's stop guard it meanwhile."""
    eng = _paper(tmp_path)
    pid = _open(eng, eng.broker, 1)[0]
    now = eng._last_tick.ts_utc
    eng._close_due[pid] = ("time_exit", {"symbol": "XAUUSD"}, now)
    eng._close_backoff[pid] = (31, now + pd.Timedelta(seconds=30))
    assert order_send.entry_yield(eng) is None
    eng._close_backoff[pid] = (31, now - pd.Timedelta(seconds=1))         # its next attempt is due: exits first
    assert order_send.entry_yield(eng) == order_send.EXIT_FIRST


def test_a_close_backing_off_because_the_market_is_closed_does_not_hold_other_entries(tmp_path) -> None:
    eng = _paper(tmp_path)
    pid = _open(eng, eng.broker, 1)[0]
    now = eng._last_tick.ts_utc
    eng._close_due[pid] = ("weekend", {"symbol": "XAUUSD"}, now)
    eng._close_backoff[pid] = (1, now + pd.Timedelta(minutes=5))
    assert order_send.entry_yield(eng) is None


def test_a_close_due_on_another_book_holds_this_books_entries(tmp_path) -> None:
    """The MT5 box is the account's, not the book's: any book's pending exit goes first."""
    eng = _paper(tmp_path)

    class _Book:
        def __init__(self) -> None:
            self.__dict__.update(_close_due={7: ("time_exit", {"symbol": "EURUSD"}, None)}, _close_backoff={},
                                 _modify_backoff={}, _kill_pending=False, open={})

    eng.books["EURUSD"] = _Book()
    assert order_send.entry_yield(eng) == order_send.EXIT_FIRST


def test_an_account_without_the_flag_keeps_sending_as_before(tmp_path) -> None:
    eng = _paper(tmp_path, free=False)
    pid = _open(eng, eng.broker, 1)[0]
    eng._close_due[pid] = ("time_exit", {"symbol": "XAUUSD"}, None)
    assert order_send.entry_yield(eng) is None
    res = _send(eng, "e1")
    assert res is not None and res.ok


def test_a_yield_is_journalled_as_its_own_refusal_never_a_tick_error(tmp_path) -> None:
    eng = _paper(tmp_path)
    eng._kill_pending = True
    exc = EntryYield(order_send.EXIT_FIRST)
    assert isinstance(exc, OrderInFlight)                   # every caller already journals these as refusals
    assert order_send.refusal_action(exc) == "entry_refused:exit_first"
    assert order_send.refusal_action(OrderInFlight("x")) == order_send.REFUSED
    assert order_send.refused(eng, exc, "a", "p")
    assert eng.decisions[-1]["action"] == "entry_refused:exit_first"
    assert not eng.state.tick_error


# ---------------------------------------------------------------------------------------------- the duty cycle
class _Clock:
    """A host clock the order module reads (`order_send._clock`); the test's broker moves it per call."""

    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(order_send, "_clock", c)
    return c


class _Box(PaperBroker):
    """The serial MT5 box on a modelled clock: each call holds it this long (order at the documented bridge RTT
    p95 bound, 1.5 s; closes, modifies and reads at the bridge's slow-call figures)."""

    ORDER_S, CLOSE_S, MODIFY_S, READ_S = 1.5, 0.15, 0.15, 0.05

    def __init__(self, clock: _Clock, **kw: Any) -> None:
        super().__init__(equity=1_000_000, **kw)
        self.clock = clock
        self.closed_at: dict[int, float] = {}
        self.refuse_with: int | None = None

    def place_order(self, intent: OrderIntent) -> OrderResult:
        self.clock.t += self.ORDER_S
        if self.refuse_with is not None:
            return OrderResult(ok=False, retcode=self.refuse_with, order_id=None, position_id=None, filled_lots=0.0,
                               price=None, message="limit")
        return super().place_order(intent)

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        self.clock.t += self.CLOSE_S
        self.closed_at[position_id] = self.clock.t
        return super().close(position_id, lots)

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        self.clock.t += self.MODIFY_S
        return super().modify(position_id, sl, tp)

    def positions(self, magic_prefix: int | None = None, symbol: str | None = None) -> Any:
        self.clock.t += self.READ_S
        return super().positions(magic_prefix, symbol)


def test_no_entry_starts_once_this_pass_entries_held_the_tick_thread_their_budget(tmp_path, clock) -> None:
    eng = _paper(tmp_path, _Box(clock))
    order_send.pass_started(eng)
    assert _send(eng, "e1") is not None                       # 1.5 s: at the pass budget
    assert order_send.entry_yield(eng) == order_send.ENTRY_DUTY
    with pytest.raises(EntryYield):
        _send(eng, "e2")
    clock.t += 1.0
    order_send.pass_started(eng)                              # the next pass runs its exits first, then may enter
    assert order_send.entry_yield(eng) is None


def test_entries_never_hold_the_tick_thread_more_than_the_duty_ceiling_of_a_window(tmp_path, clock) -> None:
    eng = _paper(tmp_path, _Box(clock))
    sent = 0
    for k in range(40):                                        # a pass every 0.1 s, entries wanted on every one
        order_send.pass_started(eng)
        try:
            _send(eng, f"e{k}")
            sent += 1
        except EntryYield:
            pass
        clock.t += 0.1
    window = order_send.DUTY_WINDOW_S
    busy = sent * _Box.ORDER_S
    elapsed = clock.t - 1_000.0
    assert sent >= 2 and busy <= order_send.DUTY_MAX * elapsed + _Box.ORDER_S + 1e-9, (sent, elapsed)
    assert order_send.duty(eng).busy(clock.t) <= order_send.DUTY_MAX * window + _Box.ORDER_S


@pytest.mark.parametrize("retcode", sorted(order_send.CAPACITY_RETCODES))
def test_the_brokers_capacity_refusal_holds_entries_until_a_position_closes_never_a_retry_loop(tmp_path, clock,
                                                                                               retcode) -> None:
    box = _Box(clock)
    eng = _paper(tmp_path, box)
    pids = _open(eng, box, 3)
    eng._refresh_account(eng._last_tick)
    box.refuse_with = retcode
    res = _send(eng, "e1")
    assert res is not None and not res.ok and eng._orders["e1"].status != "ambiguous"   # a definite reject
    assert any(d.get("action") == "broker_capacity" for d in eng.decisions)
    box.refuse_with = None
    clock.t += 30.0
    order_send.pass_started(eng)
    assert order_send.entry_yield(eng) == order_send.BROKER_CAPACITY
    with pytest.raises(EntryYield):
        _send(eng, "e2")
    box.close(pids[0])
    eng.open.pop(pids[0])
    eng._refresh_account(eng._last_tick)
    assert order_send.entry_yield(eng) == order_send.BROKER_CAPACITY   # trading-safety L2: at least CAPACITY_HOLD_S
    clock.t += order_send.CAPACITY_HOLD_S
    assert order_send.entry_yield(eng) is None


def test_a_capacity_refusal_with_nothing_open_holds_for_the_minimum_then_lets_entries_try_again(tmp_path, clock):
    """Trading-safety L2: at 0 open the position rule alone never holds, so the refusal repeated every pass."""
    box = _Box(clock)
    eng = _paper(tmp_path, box)
    box.refuse_with = 10040
    _send(eng, "e1")
    box.refuse_with = None
    clock.t += order_send.CAPACITY_HOLD_S - 1.0
    assert order_send.entry_yield(eng) == order_send.BROKER_CAPACITY
    clock.t += 2.0
    assert order_send.entry_yield(eng) is None


# ---------------------------------------------------------------------------------------------- the exit SLO
def _soak(tmp_path: Path, clock: _Clock, *, n_open: int = 300, passes: int = 120, burst: int = 6) -> list[float]:
    """`n_open` positions on the paper bridge. Every loop pass: the pass's exits, then two positions' engine stops go
    through (their exit is due from that moment), then `burst` entries are wanted. The exit wait of each close is the
    modelled time from its stop going through to its close reaching the box."""
    box = _Box(clock)
    eng = _paper(tmp_path, box)
    pids = _open(eng, box, n_open)
    assert len(eng.open) == n_open
    due: dict[int, float] = {}
    queue = list(pids)
    for k in range(passes):
        order_send.pass_started(eng)
        t = T0 + pd.Timedelta(seconds=k + 1)
        PaperBroker.on_tick(box, _tick(t))
        assert engine_pass(eng, box, "XAUUSD")
        for pid in (queue.pop(), queue.pop()):
            tr = eng.open[pid]
            tr.sl = 2400.0 + tr.side * 5                       # through: bid 2400 for a long, ask 2400.2 for a short
            due[pid] = clock.t
        for j in range(burst):
            try:
                _send(eng, f"e{k}-{j}")
            except OrderInFlight:
                break
    order_send.pass_started(eng)
    PaperBroker.on_tick(box, _tick(T0 + pd.Timedelta(seconds=passes + 1)))
    engine_pass(eng, box, "XAUUSD")
    assert set(due) <= set(box.closed_at), "an armed exit never closed"
    return [box.closed_at[p] - due[p] for p in due]


def test_with_300_open_positions_on_the_paper_bridge_exit_wait_p99_is_under_5_s(tmp_path, clock) -> None:
    waits = _soak(tmp_path, clock)
    assert float(np.percentile(waits, 99)) < order_send.EXIT_SLO_S, sorted(waits)[-5:]
    state = order_send.published(_paper(tmp_path / "x"))
    assert state["exit_slo_s"] == order_send.EXIT_SLO_S


def test_without_exit_first_and_the_duty_ceiling_the_same_load_breaks_the_exit_slo(tmp_path, clock, monkeypatch) -> None:
    """The control: the soak above would pass on its own if entries never contended."""
    monkeypatch.setattr(order_send, "entry_yield", lambda eng: None)
    waits = _soak(tmp_path, clock)
    assert float(np.percentile(waits, 99)) > order_send.EXIT_SLO_S


def test_the_pass_gap_p99_is_published_in_the_engine_state(tmp_path, clock) -> None:
    eng = _paper(tmp_path, _Box(clock))
    _open(eng, eng.broker, 1)
    for k in range(50):
        order_send.pass_started(eng)
        clock.t += 0.5 if k % 10 else 7.0
    pub = order_send.published(eng)
    assert pub["exit_wait_n"] == 49 and pub["exit_wait_max_s"] == pytest.approx(7.0)
    assert pub["exit_wait_p99_s"] > order_send.EXIT_SLO_S and pub["demo_unlimited"] is True
    eng._write_state()
    state = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert state["exit_wait_p99_s"] == pytest.approx(pub["exit_wait_p99_s"])


def test_gaps_older_than_the_window_are_not_published_so_a_flat_account_shows_no_stale_figure(tmp_path, clock):
    """Code review C-M2: a slow pass an hour ago, then flat: nothing old enough is read."""
    eng = _paper(tmp_path, _Box(clock))
    pid = _open(eng, eng.broker, 1)[0]
    for _ in range(3):
        order_send.pass_started(eng)
        clock.t += 9.0
    order_send.pass_started(eng)
    assert order_send.published(eng)["exit_wait_n"] == 3
    eng.open.pop(pid)
    clock.t += order_send.EXIT_WINDOW_S + 1
    order_send.pass_started(eng)
    assert order_send.published(eng)["exit_wait_n"] == 0 and order_send.published(eng)["exit_wait_p99_s"] is None


def test_a_multi_book_account_is_judged_against_its_pass_budget_plus_one_entry_slot(tmp_path) -> None:
    from goldbot.engine.books import PASS_BUDGET_S
    eng = _paper(tmp_path)
    assert order_send.exit_slo_s(eng) == order_send.EXIT_SLO_S
    eng.books["EURUSD"] = object()
    assert order_send.exit_slo_s(eng) == max(order_send.EXIT_SLO_S, PASS_BUDGET_S + order_send.ENTRY_PASS_S)


def test_a_pass_gap_with_nothing_open_is_not_an_exit_wait(tmp_path, clock) -> None:
    eng = _paper(tmp_path, _Box(clock))
    for _ in range(5):
        order_send.pass_started(eng)
        clock.t += 30.0
    assert order_send.published(eng)["exit_wait_n"] == 0


def test_every_order_on_an_unlimited_account_is_journalled_with_its_limits_epoch(tmp_path) -> None:
    free, capped = _paper(tmp_path / "a"), _paper(tmp_path / "b", free=False)
    _send(free, "e1")
    _send(capped, "e1")
    assert [d.get("limits_epoch") for d in free.decisions if d.get("action") == "order"] == ["unlimited"]
    assert [d for d in capped.decisions if d.get("action") == "order" and "limits_epoch" in d] == []


def test_the_engine_loop_times_every_pass_before_its_exits() -> None:
    """The gap meter runs at the top of the service loop, before the pass that runs the exits (one call site)."""
    import inspect

    from goldbot.ops.services import engine as service
    src = inspect.getsource(service._run_engine)
    assert src.count("order_send.pass_started(eng)") == 1
    assert src.index("order_send.pass_started(eng)") < src.index("resolve(engine_pass)(eng, broker, acc.symbol)")


def test_orders_whose_capped_reasons_cannot_be_read_are_counted_apart_for_the_day(tmp_path, monkeypatch) -> None:
    """Quant R2: `capped_reasons == ["unread"]` is counted (`capped_reasons_unread`) beside the day's tagged orders."""
    eng = _paper(tmp_path)
    _send(eng, "e1")
    monkeypatch.setattr(eng.gate, "account_reasons", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    _send(eng, "e2")
    pub = order_send.published(eng)
    assert (pub["capped_reasons_orders"], pub["capped_reasons_unread"]) == (2, 1)
    assert [d.get("capped_reasons") for d in eng.decisions if d.get("action") == "order"] == [[], ["unread"]]
