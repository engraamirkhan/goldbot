"""Live issue 2026-10-27 (vantage-demo): Vantage fills take 2.6-5.3 s at p95 and up to 31 s, so `bridge place_order`
timed out at the engine's 15 s read timeout while the broker still filled. The fill was adopted, but the timeout raised
as a tick error and blocked every entry until the next clean decision bar (15 min).

The rules now (goldbot/engine/order_send.py):
- an order call that does not answer (a read timeout, a lost connection) is an AMBIGUOUS order, never a tick error: its
  row is looked up by comment at the broker (BACKLOG 72), adopted or marked not placed, and never re-sent;
- through the bridge, the order is sent on a worker thread with the account's own order timeout (vantage-demo 35 s);
  the tick thread waits at most `wait_s`, then carries on: stop checks, closes and the kill switch keep their per-tick
  cadence while the order is in flight;
- one order at a time per account: a second send while one is in flight is refused before its row is written;
- a tick read that fails behind our own order in flight is not a tick error (entries are refused
  `unconfirmed_order_pending` meanwhile anyway)."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import order_send, unconfirmed
from goldbot.engine.order_send import OrderInFlight, Sender
from goldbot.execution.bridge import BridgeError, BridgeUnavailable, RemoteBroker
from goldbot.execution.broker import OrderIntent, OrderResult, Tick
from goldbot.execution.not_confirmed import AMBIGUOUS_SETTLE_S
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts
from goldbot.ops.run import engine_pass
from tests.test_engine_safety import T0, _prop, _tick
from tests.test_order_not_confirmed import AGENT, PID, SL, TP, _engine

HANG_S = 30.0


class _Hanging(PaperBroker):
    """`place_order` for PID hangs until `release` is set (at most HANG_S), then fills (`fill`) or not, and answers
    (`answer`) or raises the bridge's read timeout. Every other call answers at once."""

    def __init__(self, *, fill: bool, answer: bool = False, **kw: Any) -> None:
        super().__init__(equity=10_000, **kw)
        self.release = threading.Event()
        self.fill, self.answer, self.sends = fill, answer, 0

    def place_order(self, intent: OrderIntent) -> OrderResult:
        if intent.client_order_id != PID:
            return super().place_order(intent)
        self.sends += 1
        self.release.wait(HANG_S)
        res = super().place_order(intent) if self.fill else None
        if self.answer and res is not None:
            return res
        raise BridgeUnavailable("bridge place_order: ReadTimeout")


class _TimesOut(PaperBroker):
    """No worker thread (a direct terminal): the order reaches the broker (`fill`) and then the call raises `exc`."""

    def __init__(self, *, fill: bool, exc: BaseException | None = None, **kw: Any) -> None:
        super().__init__(equity=10_000, **kw)
        self.fill, self.sends = fill, 0
        self.exc = exc or BridgeUnavailable("bridge place_order: ReadTimeout")

    def place_order(self, intent: OrderIntent) -> OrderResult:
        self.sends += 1
        if self.fill:
            super().place_order(intent)
        raise self.exc


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[pd.Timestamp]:
    now = [pd.Timestamp.now("UTC")]
    monkeypatch.setattr(unconfirmed, "_now", lambda: now[0])
    return now


def _at(clock: list[pd.Timestamp], eng: Any, seconds: float) -> None:
    clock[0] = pd.Timestamp(eng._orders[PID].ts_utc) + pd.Timedelta(seconds=seconds)


def _started(pb: PaperBroker) -> PaperBroker:
    pb.on_tick(_tick(T0))
    return pb


def _hanging_engine(tmp_path: Path, pb: _Hanging) -> Any:
    eng, _ = _engine(tmp_path, _started(pb))
    eng.order_sender = Sender(pb, wait_s=0.05)
    return eng


def _send(eng: Any) -> OrderResult | None:
    return eng._execute(_prop(PID), AGENT, 0.1, SL, TP, requested=2400.2)


def _finish(eng: Any, pb: _Hanging) -> None:
    pb.release.set()
    flight = eng.order_sender.flight
    assert flight is not None and flight.done.wait(5.0)


def _blocks(eng: Any) -> list[str]:
    return eng.gate.account_reasons(eng.state)


# ---------------------------------------------------------------------------------------------- 1. not a tick error
def test_an_order_call_that_times_out_is_an_ambiguous_order_and_never_a_tick_error(tmp_path):
    pb = _TimesOut(fill=False)
    eng, _ = _engine(tmp_path, _started(pb))
    res = _send(eng)
    assert res is not None and not res.ok and res.message.startswith(order_send.UNANSWERED)
    assert res.message == f"{order_send.UNANSWERED}: BridgeUnavailable"   # the type name only: a message can name hosts
    assert eng._orders[PID].status == unconfirmed.AMBIGUOUS
    assert not eng.state.tick_error and eng.tick_errors_total == 0
    assert "tick_error" not in _blocks(eng)
    assert "unconfirmed_order_pending" in _blocks(eng)          # the account waits only until it is settled
    assert _send(eng) is None and pb.sends == 1                 # never re-sent


def test_a_timed_out_order_that_filled_is_adopted_and_entries_reopen(tmp_path, clock):
    pb = _TimesOut(fill=True)
    eng, _ = _engine(tmp_path, _started(pb))
    _send(eng)
    [pos] = pb.positions()
    eng._reconcile(None)                                         # the look-up by comment
    rec = eng._orders[PID]
    assert rec.status == "filled" and rec.position_id == pos.position_id and pos.position_id in eng.open
    assert eng.open[pos.position_id].sl == SL
    assert "unconfirmed_order_pending" not in _blocks(eng) and "tick_error" not in _blocks(eng)


@pytest.mark.parametrize("exc", [TimeoutError("timed out"), ConnectionError("reset")])
def test_any_no_answer_error_is_ambiguous(tmp_path, exc):
    pb = _TimesOut(fill=False, exc=exc)
    eng, _ = _engine(tmp_path, _started(pb))
    _send(eng)
    assert eng._orders[PID].status == unconfirmed.AMBIGUOUS and not eng.state.tick_error


def test_an_error_the_bridge_answered_still_raises_and_leaves_the_row_sending(tmp_path):
    """Only a call with no answer is ambiguous; a terminal or bridge error keeps the reviewed path (LOW-C)."""
    pb = _TimesOut(fill=False, exc=BridgeError("bridge place_order: HTTP 500 boom"))
    eng, _ = _engine(tmp_path, _started(pb))
    with pytest.raises(BridgeError):
        _send(eng)
    assert eng._orders[PID].status == "sending"


def test_an_unanswered_auto_entry_pauses_this_close_without_reading_the_busy_bridge(tmp_path):
    from goldbot.engine import auto_entry
    from goldbot.risk.gate import GateDecision
    pb = _TimesOut(fill=False)
    eng, _ = _engine(tmp_path, _started(pb))
    res = _send(eng)
    assert res is not None
    reads = []
    pb.last_tick = lambda symbol: reads.append(symbol)            # type: ignore[method-assign,assignment,return-value]
    auto_entry.after_order(eng, res, GateDecision(allowed=True, lots=0.1), _prop(PID), T0)
    assert eng._auto_paused == (T0, "unconfirmed_order") and reads == []


# ---------------------------------------------------------------------------------------------- 2. off the tick thread
def test_exits_and_stop_checks_run_every_tick_while_an_order_hangs_for_30_s(tmp_path):
    pb = _Hanging(fill=False)
    eng = _hanging_engine(tmp_path, pb)
    # an open trade whose engine stop (2398) is tighter than the broker's (2390)
    pos = pb.place_order(OrderIntent(client_order_id="held", symbol="XAUUSD", side=1, lots=0.1, sl=2390.0, tp=2500.0,
                                     magic=eng.cfg.magic_base, comment="held")).position_id
    assert pos is not None
    eng._reconcile(None)
    eng.open[pos].sl = 2398.0
    calls = {"stop": 0, "closes": 0}
    stop_check, retry = eng._stop_check, eng._retry_closes

    def counted_stop(t: Tick) -> None:
        calls["stop"] += 1
        stop_check(t)

    def counted_retry(at: pd.Timestamp) -> None:
        calls["closes"] += 1
        retry(at)

    eng._stop_check, eng._retry_closes = counted_stop, counted_retry
    t0 = time.monotonic()
    _send(eng)
    assert time.monotonic() - t0 < 2.0                       # the tick thread waited wait_s, not the order's 30 s
    assert eng.order_sender.busy() and eng._orders[PID].status == unconfirmed.AMBIGUOUS
    for i in range(1, 6):
        bid = 2400.0 if i < 4 else 2397.5                        # the 4th tick goes through the engine stop
        pb.on_tick(_tick(T0 + pd.Timedelta(seconds=i), bid=bid))
        started = time.monotonic()
        eng.on_tick(pb.last_tick("XAUUSD"))
        assert time.monotonic() - started < 1.0
    assert eng.order_sender.busy()                                # still in flight the whole time
    assert calls == {"stop": 5, "closes": 5}
    assert pos not in {p.position_id for p in pb.positions()}     # closed at market while the order hung
    assert not eng.state.tick_error
    _finish(eng, pb)


def test_a_late_fill_is_journalled_and_adopted_on_the_next_pass(tmp_path, clock):
    pb = _Hanging(fill=True, answer=True)
    eng = _hanging_engine(tmp_path, pb)
    _send(eng)
    assert eng._orders[PID].status == unconfirmed.AMBIGUOUS
    _finish(eng, pb)
    eng._last_reconcile = pd.Timestamp(T0)                        # the periodic look-up is not due yet
    pb.on_tick(_tick(T0 + pd.Timedelta(seconds=2)))
    eng.on_tick(pb.last_tick("XAUUSD"))
    [late] = [d for d in eng.decisions if d.get("action") == "order_late"]
    assert late["proposal"] == PID and late["ok"] and late["position_id"] is not None
    rec = eng._orders[PID]
    assert rec.status == "filled" and rec.position_id == late["position_id"] and rec.position_id in eng.open
    assert not eng.order_sender.busy() and eng.order_sender.flight is None
    assert "unconfirmed_order_pending" not in _blocks(eng) and not eng.state.tick_error


def test_a_hung_order_that_filled_with_no_reply_is_resolved_by_adoption(tmp_path, clock):
    pb = _Hanging(fill=True)
    eng = _hanging_engine(tmp_path, pb)
    _send(eng)
    _finish(eng, pb)
    eng._reconcile(None)
    [pos] = pb.positions()
    rec = eng._orders[PID]
    assert rec.status == "filled" and rec.position_id == pos.position_id and pos.position_id in eng.open
    assert pb.sends == 1 and not eng.state.tick_error


def test_a_hung_order_never_placed_is_marked_not_placed_and_its_slot_freed(tmp_path, clock):
    pb = _Hanging(fill=False)
    eng = _hanging_engine(tmp_path, pb)
    _send(eng)
    _finish(eng, pb)
    order_send.poll(eng)
    assert eng.order_sender.flight is None
    _at(clock, eng, 5)
    eng._reconcile(None)
    _at(clock, eng, 5 + AMBIGUOUS_SETTLE_S)
    eng._reconcile(None)
    assert eng._orders[PID].status == "unfilled" and eng._orders[PID].notified == "no_trade"
    assert not eng.state.unconfirmed_order and not eng._busy(AGENT.agent_id)
    assert "unconfirmed_order_pending" not in _blocks(eng) and "tick_error" not in _blocks(eng)
    order_send.ready(eng)                                          # the account can send again
    assert pb.sends == 1 and not pb.positions()


def test_a_second_send_while_one_is_in_flight_is_refused_before_its_row_is_written(tmp_path):
    pb = _Hanging(fill=False)
    eng = _hanging_engine(tmp_path, pb)
    _send(eng)
    other = "icm-demo-72-b"
    with pytest.raises(OrderInFlight):
        eng._execute(_prop(other), AGENT, 0.1, SL, TP, requested=2400.2)
    assert other not in eng._orders and other not in eng.sent_ids
    _finish(eng, pb)


def test_an_order_answered_within_the_wait_is_handled_as_before(tmp_path):
    pb = _Hanging(fill=True, answer=True)
    pb.release.set()
    eng, _ = _engine(tmp_path, _started(pb))
    eng.order_sender = Sender(pb, wait_s=5.0)
    res = _send(eng)
    assert res is not None and res.ok and eng._orders[PID].status == "filled"
    assert eng.order_sender.flight is None
    assert not [d for d in eng.decisions if d.get("action") == "order_late"]


# ---------------------------------------------------------------------------------------------- 3. tick reads behind it
def test_a_tick_read_that_times_out_behind_our_order_in_flight_is_not_a_tick_error(tmp_path):
    pb = _Hanging(fill=False)
    eng = _hanging_engine(tmp_path, pb)
    _send(eng)

    def busy(symbol: str) -> Tick:
        raise BridgeUnavailable("bridge last_tick: ReadTimeout")
    reader = PaperBroker(equity=10_000)
    reader.last_tick = busy                                          # type: ignore[method-assign]
    assert not engine_pass(eng, reader, "XAUUSD")
    assert not eng.state.tick_error and eng.tick_errors_total == 0
    _finish(eng, pb)
    order_send.poll(eng)
    eng.order_sender.excuse_until = 0.0                              # the grace after the late reply is over
    assert not engine_pass(eng, reader, "XAUUSD")
    assert eng.state.tick_error                                      # a dead feed is a tick error again


# ---------------------------------------------------------------------------------------------- 4. config and wiring
def test_order_timeouts_are_per_account_and_below_the_look_ups_settle_time():
    configured = accounts.load_accounts(logins=False)
    assert configured["vantage-demo"].order_timeout_s == 35.0
    assert configured["icm-demo"].order_timeout_s == 15.0
    bound = accounts.Account.model_fields["order_timeout_s"].metadata
    assert any(getattr(m, "le", None) is not None and m.le < AMBIGUOUS_SETTLE_S for m in bound)


def test_the_bridge_sender_has_its_own_client_with_the_accounts_order_timeout():
    sender = order_send.bridge_sender("http://127.0.0.1:8767", "t" * 40, name="mt5-remote-vantage-demo-orders",
                                      timeout_s=35.0)
    assert isinstance(sender.client, RemoteBroker)
    assert sender.client.order_timeout_s == 35.0 and sender.wait_s == order_send.WAIT_S
    assert order_send.WAIT_S < 15.0                                  # never longer than the old blocking send
