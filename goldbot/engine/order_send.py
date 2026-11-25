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
  so a reply in time is handled exactly as before; after that the order is unanswered and the tick thread carries on
  with its passes. A direct terminal (one MT5 connection per process) and the paper broker send on the tick thread, as
  before.
- Exits while an order is in flight (trading-safety review of 5d661e94, MEDIUM; BACKLOG 135, ADR 0056): a terminal
  connection serves one call at a time (the MetaTrader5 package is not thread-safe). With the account's order lane up
  (bridge protocol v7, `order_lane: true`), the entry goes out on a terminal connection of its own and ticks, closes
  and the kill switch keep their per-tick cadence: `exit_delay_bound_s` is EXIT_BOUND_LANE_S (5 s). Without it, the
  v7 bridge runs a close next after the order the terminal is sending, ahead of every queued read, and drops the reads
  the engine gave up on: the bound is the order's hold (at most the account's order_timeout_s) plus one tick pass.
  A v6 bridge served them in arrival order (the hold plus a whole 2 x 10 s read cycle, about 45-55 s at 35 s); a live
  account refuses entries on it (bridge_compat.CLOSE_PRIORITY_VERSION). Broker-side stops cover every window.
- One order at a time per account: a send while one is in flight is refused (`OrderInFlight`) before its row is written,
  so nothing can have reached the broker (BACKLOG 85 "not sent"); every caller journals it `REFUSED`, never a tick
  error. The gate's `unconfirmed_order_pending` refuses such an entry first; this is the backstop.
- The late reply is collected after the pass's exits (`poll`) and journalled `order_late`; it brings the look-up
  forward to that pass. It never changes the row itself: the look-up at the broker decides (reads only).
- order_timeout_s stays below AMBIGUOUS_SETTLE_S (Account field bound), so no clean look can call an order `unfilled`
  while its call can still answer.
- A tick read that fails while our order is in flight (or within EXCUSE_S of its late reply) is not a tick error
  (`excused`): entries are refused `unconfirmed_order_pending` then anyway, and the no-quote pass still runs. Exits
  never pass through this module.

Exits before entries on a demo account without limits (ADR 0097; trading-safety H1/H2 of 2026-11-25: "no entry send
while any exit or stop-modify is pending; an entry yields at the next safe point", "an entry duty-cycle ceiling", "an
exit-latency SLO with an alert (p99 < 5 s)", "classify 10040/10033 as refusals, never a retry loop"). Once the caps
are lifted, entries could hold the tick thread (it waits for each reply) and the serial MT5 box most of the time, so a
software exit would wait behind entry after entry. On such an account (`RiskGate.unlimited`):
- exit-first (`EXIT_FIRST`): no entry while any book has a close the engine decided and has not got filled (one backing
  off because the broker says the market is closed excepted: it is not contending for the box), a kill-switch flatten
  pending, or a stop modify whose retry is due;
- the duty ceiling (`ENTRY_DUTY`): no entry starts once this pass's entries held the tick thread ENTRY_PASS_S, or while
  entries held it DUTY_MAX of the last DUTY_WINDOW_S, so every pass reaches its exits within about one order's hold;
- broker capacity (`BROKER_CAPACITY`): a 10033 (orders) or 10040 (positions) refusal holds entries while the account
  holds at least as many positions as it did then; the order is a definite reject (nothing re-sent).
`entry_yield` says which, `ready` raises it as `EntryYield` (an `OrderInFlight`: every caller journals it as a refusal,
`refusal_action`, never a tick error), and the exploration send loop checks it before each signal and keeps the rest
queued for the next pass. These decide when an entry goes, never whether a trade is allowed: engineering, not risk.
Every account's loop passes are timed (`pass_started`): the gap between two passes with a position open is the
longest an exit that became due could wait; the p99 of the last EXIT_GAPS is published (`published`) and health
alerts above EXIT_SLO_S (goldbot/ops/checks/risk.py `check_exit_latency`).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
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
REFUSED = "entry_refused:order_in_flight"      # the journal's word for an OrderInFlight refusal
READ_TIMEOUT_S, READ_ATTEMPTS = 10.0, 2       # the engine's bridge client (RemoteBroker timeout_s, read_attempts)
EXIT_BOUND_LANE_S = 5.0     # BACKLOG 135 acceptance: an exit reaches the terminal within this with an order in flight
PASS_S = 1.0                # one engine tick pass on the bridge (a tick read, the stop check, the close)
NO_ANSWER = (ConnectionError, TimeoutError)     # BridgeUnavailable is a ConnectionError
# ADR 0097 (module docstring, "Exits before entries"): engineering bounds, not risk limits
EXIT_FIRST, ENTRY_DUTY, BROKER_CAPACITY = "exit_first", "entry_duty", "broker_capacity"
ENTRY_PASS_S = 1.5          # no entry starts once this pass's entries held the tick thread this long (bridge RTT p95)
DUTY_WINDOW_S, DUTY_MAX = 10.0, 0.5     # ... nor while entries held it half of the last 10 s
EXIT_SLO_S = 5.0            # trading-safety H1: exit wait p99 below this (health alerts above it)
EXIT_GAPS = 600             # the loop-pass gaps the p99 is read over (about 10 min at a pass a second)
CAPACITY_RETCODES = frozenset({10033, 10040})   # TRADE_RETCODE_LIMIT_ORDERS, TRADE_RETCODE_LIMIT_POSITIONS
YIELD_LOG_S = 60.0          # a yield is journalled at most this often per reason (a binding ceiling repeats per pass)
_clock: Callable[[], float] = time.monotonic     # the host clock of the duty and the gaps (a test models it)


class OrderInFlight(RuntimeError):
    """An order was asked for while this account's previous one is still in flight: nothing was sent."""


class EntryYield(OrderInFlight):
    """ADR 0097: nothing was sent because exits, the duty ceiling or the broker's capacity go first (`entry_yield`)."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"entry yields ({reason}): nothing sent")
        self.reason = reason


class Duty:
    """The account's entry and loop-pass timing (one per engine, multi-book included: the MT5 box is the account's).
    `spans` are the entries' (start, end) on the host clock, `gaps` the loop-pass gaps with a position open."""

    def __init__(self) -> None:
        self.spans: deque[tuple[float, float]] = deque()
        self.pass_at: float | None = None       # this loop pass's start
        self.pass_busy = 0.0                    # this pass's entries' time
        self.gaps: deque[float] = deque(maxlen=EXIT_GAPS)
        self.capacity_hold: int | None = None   # open positions when the broker refused for capacity
        self.logged: dict[str, float] = {}

    def busy(self, now: float) -> float:
        """Seconds of the last DUTY_WINDOW_S the entries held the tick thread."""
        start = now - DUTY_WINDOW_S
        while self.spans and self.spans[0][1] <= start:
            self.spans.popleft()
        return sum(min(b, now) - max(a, start) for a, b in self.spans if b > start)

    def note(self, start: float, end: float) -> None:
        self.spans.append((start, end))
        self.pass_busy += max(0.0, end - start)


def duty(eng: Any) -> Duty:
    """The engine's `Duty` (in the engine's own attributes, never a book's)."""
    d = eng.__dict__.get("_entry_duty")
    if d is None:
        d = eng.__dict__["_entry_duty"] = Duty()
    return d


def _unlimited(eng: Any) -> bool:
    try:
        return bool(eng.gate.unlimited())
    except Exception:                     # noqa: BLE001 - no answer: the account's caps stand, nothing changes here
        return False


def _holds(eng: Any) -> bool:
    return any(b.__dict__.get("open") for b in eng.books.values())


def exits_pending(eng: Any) -> bool:
    """Any book's close due (not backing off for a closed market), kill-switch flatten or stop modify due now."""
    t = getattr(eng, "_last_tick", None)
    now = t.ts_utc if t is not None else pd.Timestamp.now("UTC")
    for b in eng.books.values():
        d = b.__dict__
        if d.get("_kill_pending"):
            return True
        backoff = d.get("_close_backoff") or {}
        if any(not (pid in backoff and now < backoff[pid][1]) for pid in d.get("_close_due") or {}):
            return True
        if any(due is not None and now >= due for _, due in (d.get("_modify_backoff") or {}).values()):
            return True
    return False


def entry_yield(eng: Any) -> str | None:
    """Why an entry must not start now on a demo account without limits (module docstring), or None. None on every
    other account: its caps bound the entry rate, and its behaviour is unchanged."""
    if not _unlimited(eng):
        return None
    if exits_pending(eng):
        return EXIT_FIRST
    d = duty(eng)
    if d.capacity_hold is not None:
        if eng.state.open_positions >= d.capacity_hold:
            return BROKER_CAPACITY
        d.capacity_hold = None
    if d.pass_at is not None and d.pass_busy >= ENTRY_PASS_S - 1e-9:
        return ENTRY_DUTY
    if d.busy(_clock()) >= DUTY_MAX * DUTY_WINDOW_S - 1e-9:
        return ENTRY_DUTY
    return None


def note_yield(eng: Any, why: str, waiting: int) -> None:
    """Journal a yield (`entry_yield`), at most once per YIELD_LOG_S per reason."""
    d, now = duty(eng), _clock()
    if now - d.logged.get(why, -YIELD_LOG_S) < YIELD_LOG_S:
        return
    d.logged[why] = now
    eng.decisions.append({"ts": time.time(), "action": "entry_yield", "reason": why, "waiting": waiting})


def pass_started(eng: Any) -> None:
    """At the top of every engine loop pass (ops.services.engine): this pass's entry time starts at 0, and the gap since
    the last pass is an exit wait when a position is open. Never raises."""
    try:
        d, now = duty(eng), _clock()
        if d.pass_at is not None and _holds(eng):
            d.gaps.append(now - d.pass_at)
        d.pass_at, d.pass_busy = now, 0.0
    except Exception:                     # noqa: BLE001 - timing only: the pass and its exits go on
        log.exception("loop pass timing failed")


def published(eng: Any) -> dict[str, Any]:
    """The engine state's exit-latency figures (health `check_exit_latency`): p99 and max of the last EXIT_GAPS loop
    gaps with a position open, their count, the SLO, the entries' share of the last window and the account's flag."""
    d = duty(eng)
    gaps = list(d.gaps)
    return {"exit_wait_p99_s": round(float(np.percentile(gaps, 99)), 3) if gaps else None,
            "exit_wait_max_s": round(max(gaps), 3) if gaps else None, "exit_wait_n": len(gaps),
            "exit_slo_s": EXIT_SLO_S, "entry_duty": round(d.busy(_clock()) / DUTY_WINDOW_S, 3),
            "demo_unlimited": _unlimited(eng)}


def refusal_action(exc: BaseException) -> str:
    """The journal's action for an order refused before it was sent: `entry_refused:<reason>` for a yield."""
    return f"entry_refused:{exc.reason}" if isinstance(exc, EntryYield) else REFUSED


def exit_delay_bound_s(order_timeout_s: float, *, order_lane: bool) -> float:
    """The worst delay of an engine-side exit while an order is in flight on a v7 bridge (module docstring): with the
    order lane, EXIT_BOUND_LANE_S; without it, the order's hold plus one tick pass."""
    return EXIT_BOUND_LANE_S if order_lane else order_timeout_s + PASS_S


def refused(eng: Engine, exc: BaseException, agent_id: str, proposal_id: str) -> bool:
    """An `OrderInFlight` journalled as the refusal it is (`refusal_action`); False for any other error."""
    if not isinstance(exc, OrderInFlight):
        return False
    eng.decisions.append({"ts": time.time(), "agent": agent_id, "action": refusal_action(exc),
                          "proposal": proposal_id})
    return True


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
    """Before an order's row is written: refuse it while this account's previous order is in flight, and, on a demo
    account without limits, while exits, the duty ceiling or the broker's capacity go first (`EntryYield`)."""
    sender = eng.order_sender
    if sender is not None:
        if sender.busy():
            raise OrderInFlight("an order is still in flight on this account: nothing sent")
        poll(eng)                         # a late reply not yet collected is journalled first
    if (why := entry_yield(eng)) is not None:
        raise EntryYield(why)


def send(eng: Engine, intent: OrderIntent) -> OrderResult:
    """The broker's result, or an unanswered result (see the module docstring). Raises what the broker answered with.
    The time the tick thread waited counts to the entry duty (`Duty.note`); a broker capacity refusal (10033/10040) on
    a demo account without limits holds the next entries (`entry_yield`)."""
    start = _clock()
    try:
        res = _send(eng, intent)
    finally:
        duty(eng).note(start, _clock())
    if res.retcode in CAPACITY_RETCODES and _unlimited(eng):
        held = max(1, int(eng.state.open_positions))
        duty(eng).capacity_hold = held
        log.warning("order %s refused for broker capacity (retcode %s) at %d open positions: entries held until "
                    "one closes", intent.client_order_id, res.retcode, held)
        eng.decisions.append({"ts": time.time(), "action": BROKER_CAPACITY, "proposal": intent.client_order_id,
                              "retcode": res.retcode, "open_positions": held})
    return res


def _send(eng: Engine, intent: OrderIntent) -> OrderResult:
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
    from goldbot.engine import books
    eng.store.append("fills", fill, source=eng.cfg.account_id, symbol=books.store_symbol(eng), dedupe=False)
