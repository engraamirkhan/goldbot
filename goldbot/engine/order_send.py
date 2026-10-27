"""Entry orders that do not answer in time (live issue 2026-10-27, vantage-demo: Vantage fills take 2.6-5.3 s at p95
and up to 31 s, so `bridge place_order` timed out at the engine's 15 s read timeout while the broker still filled; the
fill was adopted, but the timeout raised as a tick error and blocked every entry for a whole decision bar). Kept out of
engine/runner.py (ADR 0023): the runner calls in from `__init__` (`order_sender`), `_execute` (`ready`, `send`,
`record_fill`) and `_on_tick` (`poll`); the engine loop from `engine_pass` (`excused`).

Design rules:
- "An order-send timeout is not a tick error": an order call that does not answer (`NO_ANSWER`: the bridge's read
  timeout or lost connection, BridgeUnavailable, is a ConnectionError) returns an unanswered result (retcode -1, message
  `UNANSWERED: <type>`, never the error's text, which can name a host). Its pending_orders row is `ambiguous`
  (BACKLOG 72): looked up by comment at the broker on every pass, adopted when the position is there, `unfilled` after
  two clean looks AMBIGUOUS_SETTLE_S apart, never re-sent. Entries wait (`unconfirmed_order_pending`, condition 1) only
  until that look-up settles it, not for a decision bar. An error the bridge or terminal answered with is raised as
  before (the row stays `sending`: review LOW-C).
- Through the bridge an order is sent on a `Sender` worker thread (the broker_bars `Fetcher` pattern), through a client
  of its own (a requests.Session is never shared across threads) whose order timeout is the account's
  `order_timeout_s` (accounts.yaml; vantage-demo 35 s). The tick thread waits at most `wait_s` (WAIT_S) for the reply,
  so a reply in time is handled exactly as before; after that the order is unanswered and the tick thread carries on:
  stop checks, closes, the Friday cut and the kill switch keep their per-tick cadence while it is in flight. A direct
  terminal (one MT5 connection per process) and the paper broker send on the tick thread, as before.
- One order at a time per account: a send while one is in flight is refused (`OrderInFlight`) before its row is written,
  so nothing can have reached the broker (BACKLOG 85 "not sent"). The gate's `unconfirmed_order_pending` refuses such an
  entry first; this is the backstop.
- The late reply is collected after the pass's exits (`poll`) and journalled `order_late`; it brings the look-up
  forward to that pass. It never changes the row itself: the look-up at the broker decides (reads only).
- order_timeout_s stays below AMBIGUOUS_SETTLE_S (Account field bound), so no clean look can call an order `unfilled`
  while its call can still answer.
- Known limit: the MT5 box serves one call at a time (bridge.serve, the MetaTrader5 package is not thread-safe), so the
  engine's own calls queue behind an order the terminal is still sending; the broker-side stop and target sent with
  every order cover the position meanwhile. A tick read that fails while our order is in flight (or within
  EXCUSE_S of its late reply) is not a tick error (`excused`): entries are refused `unconfirmed_order_pending` then
  anyway, and the no-quote pass still runs. Exits never pass through this module.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
import pandas as pd

from goldbot.execution.broker import OrderIntent, OrderResult

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.specialists.base import Specialist
    from goldbot.telegram.approvals import Proposal

log = logging.getLogger("goldbot.engine")

WAIT_S = 5.0            # the tick thread waits at most this for the reply (Vantage p95 2.6-5.3 s; the old send blocked 15 s)
EXCUSE_S = 5.0          # after a late reply, the engine's own calls still queued behind it drain within this
UNANSWERED = "order_unanswered"
NO_ANSWER = (ConnectionError, TimeoutError)     # BridgeUnavailable is a ConnectionError


class OrderInFlight(RuntimeError):
    """An order was asked for while this account's previous one is still in flight: nothing was sent."""


class Flight:
    """One order call: its intent and its outcome (the result, or the exception). `done` is set when it has finished."""

    def __init__(self, intent: OrderIntent) -> None:
        self.intent = intent
        self.result: OrderResult | None = None
        self.error: BaseException | None = None
        self.started = time.monotonic()
        self.finished: float | None = None
        self.done = threading.Event()

    def run(self, client: Any) -> None:
        try:
            self.result = client.place_order(self.intent)
        except Exception as exc:          # noqa: BLE001 - the outcome is applied on the tick thread
            self.error = exc
        finally:
            self.finished = time.monotonic()
            self.done.set()


class Sender:
    """One order call at a time, each on a daemon thread, through `client` (its own: production gives a RemoteBroker
    with the account's order timeout). `flight` is the call in flight, or the late one not yet collected."""

    def __init__(self, client: Any, wait_s: float = WAIT_S) -> None:
        self.client, self.wait_s = client, wait_s
        self.flight: Flight | None = None
        self.excuse_until = 0.0           # monotonic: a late reply's queued calls drain until then

    def busy(self) -> bool:
        return self.flight is not None and not self.flight.done.is_set()

    def start(self, intent: OrderIntent) -> Flight:
        flight = Flight(intent)
        self.flight = flight
        self._spawn(lambda: flight.run(self.client))
        return flight

    def _spawn(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, name="order-send", daemon=True).start()


def bridge_sender(url: str, token: str, *, name: str, timeout_s: float) -> Sender:
    """The production sender: a bridge client of its own whose order calls wait `timeout_s` (the account's)."""
    from goldbot.execution.bridge import RemoteBroker
    return Sender(RemoteBroker(url, token, name=name, order_timeout_s=timeout_s))


def ready(eng: Engine) -> None:
    """Before an order's row is written: refuse it while this account's previous order is in flight."""
    sender = eng.order_sender
    if sender is None:
        return
    if sender.busy():
        raise OrderInFlight("an order is still in flight on this account: nothing sent")
    poll(eng)                             # a late reply not yet collected is journalled first


def send(eng: Engine, intent: OrderIntent) -> OrderResult:
    """The broker's result, or an unanswered result (see the module docstring). Raises what the broker answered with."""
    sender = eng.order_sender
    if sender is None:
        try:
            return eng.broker.place_order(intent)
        except NO_ANSWER as exc:
            return _unanswered(intent, type(exc).__name__)
    flight = sender.start(intent)
    if not flight.done.wait(sender.wait_s):
        return _unanswered(intent, f"in flight after {sender.wait_s:g} s")
    sender.flight = None                  # answered in time: handled exactly as a call on this thread
    if flight.error is not None:
        if isinstance(flight.error, NO_ANSWER):
            return _unanswered(intent, type(flight.error).__name__)
        raise flight.error
    assert flight.result is not None
    return flight.result


def _unanswered(intent: OrderIntent, why: str) -> OrderResult:
    log.warning("order %s not answered (%s): looked up at the broker by its comment; entries wait until it is "
                "settled, exits carry on", intent.client_order_id, why)
    return OrderResult(ok=False, retcode=-1, order_id=None, position_id=None, filled_lots=0.0, price=None,
                       message=f"{UNANSWERED}: {why}")


def unanswered(res: OrderResult) -> bool:
    return not res.ok and res.message.startswith(UNANSWERED)


def poll(eng: Engine) -> None:
    """After the pass's exits: collect a late reply, journal it (`order_late`) and run the look-up on this pass."""
    sender = eng.order_sender
    flight = sender.flight if sender is not None else None
    if sender is None or flight is None or not flight.done.is_set():
        return
    sender.flight = None
    sender.excuse_until = (flight.finished or time.monotonic()) + EXCUSE_S
    res = flight.result
    eng.decisions.append({"ts": time.time(), "action": "order_late", "proposal": flight.intent.client_order_id,
                          "ok": bool(res and res.ok), "retcode": res.retcode if res else None,
                          "position_id": res.position_id if res else None, "lots": res.filled_lots if res else None,
                          "error": type(flight.error).__name__ if flight.error is not None else None,
                          "seconds": round((flight.finished or flight.started) - flight.started, 1)})
    eng._last_reconcile = None            # the look-up settles the row on this pass, not reconcile_every_s later


def excused(eng: Any, exc: BaseException) -> bool:
    """A failed tick read that is our own order holding the MT5 box (see the module docstring), not a tick error."""
    sender = getattr(eng, "order_sender", None)
    if sender is None or not isinstance(exc, NO_ANSWER):
        return False
    poll(eng)                             # a reply that came in while no tick could be read starts the grace
    return sender.busy() or time.monotonic() < sender.excuse_until


def record_fill(eng: Engine, prop: Proposal, agent: Specialist, res: OrderResult, requested: float) -> None:
    """Requested vs filled feeds the nightly slippage table (moved from `Engine._execute`)."""
    from goldbot.engine import cost_probe
    if eng.store is None or not res.ok or res.price is None or cost_probe.is_learning(agent.agent_id):
        return                            # ADR 0037: a probe's fill feeds its own ledger only
    fill = pd.DataFrame([{"ts_utc": eng.broker.last_tick(eng.cfg.symbol).ts_utc, "client_order_id": prop.proposal_id,
                          "agent_id": agent.agent_id, "side": prop.side, "lots": res.filled_lots, "requested": requested,
                          "filled": res.price, "order_type": "market", "retcode": res.retcode, "commission": np.nan}])
    eng.store.append("fills", fill, source=eng.cfg.account_id, symbol=eng.cfg.symbol, dedupe=False)
