"""BACKLOG 135: the order lane, entry orders on a terminal connection of their own (ADR 0056).

One terminal connection serves one call at a time, so however the bridge orders its queue (goldbot/execution/
bridge_queue.py) a close cannot reach the terminal while that connection is sending an entry, and an engine-side stop
cannot even be seen (no tick can be read). The MetaTrader5 package keeps one connection per process, so the bridge
starts a child process (`python -m goldbot.ops.run bridge-order-lane <account>`) that attaches to the same running
terminal and serves `place_order` only. While the terminal sends an entry there, the bridge's own connection keeps
serving ticks, closes, stop changes and the kill switch.

Design rules:
- Opt-in per account (`order_lane: true` in config/accounts.yaml) until the drill on that box has shown a close
  answered within 5 s while an entry is in flight (RUNBOOK "Order lane"). Off, unstarted or down: `place_order` runs
  on the bridge's own connection, at the back of its queue (protocol v7 rules), exactly as before the lane.
- The child is the same GuardedBroker as the bridge: magic range, 3 lots, the registered account read on every order,
  AutoTrading, the notional ceiling, and the order's age checked again just before the send (`sent_at` crosses the
  pipe). It attaches only: it never logs in (the bridge's self-heal does), and a child whose initialize launched a
  terminal of its own exits TERMINAL_SPAWNED_EXIT; the bridge then exits with it so systemd stops that terminal.
- One order at a time. An order whose reply does not come (the child died, or no reply within LANE_ORDER_MAX_S) raises
  `LaneLost`: the bridge answers nothing, the brain books an ambiguous order and its look-up settles it (BACKLOG 72);
  the lane is down for the rest of this bridge process and orders go back to the shared connection. Never re-sent.
- Wire: one line per message on the child's stdin/stdout, `GBLANE1 <json>`, records in the bridge's wire format
  (`_encode`/`_decode`). Anything else the child prints on stdout (the MetaTrader5 package's own output) is ignored.
"""
from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import threading
import time
from typing import IO, Any, Callable

from goldbot.execution.bridge_queue import LaneError, LaneLost
from goldbot.execution.broker import OrderIntent, OrderResult

log = logging.getLogger("goldbot.bridge")

PREFIX = b"GBLANE1 "
READY_TIMEOUT_S = 90.0          # the child's attach: initialize(path=..., timeout 60 s) plus the import
LANE_ORDER_MAX_S = 90.0         # an order with no reply after this is lost (above every account's order_timeout_s, 60 s)
LANE_STATES = ("off", "starting", "separate", "down")    # /health `order_lane` (fixed tokens)


def _line(msg: dict[str, Any]) -> bytes:
    return PREFIX + json.dumps(msg).encode() + b"\n"


class ProcessLane:
    """The bridge's side of the order lane (see the module docstring). `state` is one of LANE_STATES. `on_spawned`
    is called when the child exits TERMINAL_SPAWNED_EXIT (it found a terminal launched by its own initialize)."""

    def __init__(self, argv: list[str], *, spawned_exit: int, on_spawned: Callable[[], None] | None = None,
                 ready_timeout_s: float = READY_TIMEOUT_S, order_max_s: float = LANE_ORDER_MAX_S,
                 popen: Callable[..., Any] = subprocess.Popen) -> None:
        self.argv, self.spawned_exit, self.on_spawned = argv, spawned_exit, on_spawned
        self.ready_timeout_s, self.order_max_s, self._popen = ready_timeout_s, order_max_s, popen
        self.state = "off"
        self._proc: Any = None
        self._replies: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._ids = 0
        self._lock = threading.Lock()                 # one order at a time (the lane's queue also serialises)

    def ready(self) -> bool:
        return self.state == "separate"

    def start(self) -> bool:
        """Start the child and wait for its ready line; True when the lane is up. Never raises."""
        self.state = "starting"
        try:
            self._proc = self._popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        except Exception as exc:                      # noqa: BLE001 - no lane: orders stay on the shared connection
            log.error("order lane not started (%s): orders stay on the bridge's connection", type(exc).__name__)
            self.state = "down"
            return False
        threading.Thread(target=self._read, name="bridge-order-lane-reader", daemon=True).start()
        try:
            first = self._replies.get(timeout=self.ready_timeout_s)
        except queue.Empty:
            first = None
        if first is None or first.get("ready") is not True:
            self._down("no ready line from the order lane")
            return False
        self.state = "separate"
        log.info("order lane up: entries go out on a terminal connection of their own")
        return True

    def _read(self) -> None:
        proc = self._proc
        out = proc.stdout
        try:
            for raw in iter(out.readline, b""):
                if not raw.startswith(PREFIX):
                    log.debug("order lane printed a line that is not the lane's (ignored)")
                    continue
                try:
                    msg = json.loads(raw[len(PREFIX):])
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    self._replies.put(msg)
        except (OSError, ValueError):
            pass
        self._replies.put(None)                       # EOF: the child is gone
        if self.state == "separate":                  # idle: the next order goes inline instead of into a dead pipe
            log.error("order lane exited: orders go back to the bridge's connection")
            self.state = "down"
        code = proc.wait()
        if code == self.spawned_exit and self.on_spawned is not None:
            log.error("the order lane's initialize launched an MT5 terminal of its own: the bridge exits")
            self.on_spawned()

    def _down(self, why: str) -> None:
        if self.state != "down":
            log.error("order lane down (%s): orders go back to the bridge's connection", why)
        self.state = "down"
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except Exception:                          # noqa: BLE001 - already gone
                pass

    def stop(self) -> None:
        self._down("bridge stopping")

    def place_order(self, intent: OrderIntent, sent_at: float | None) -> OrderResult:
        """The child's result; `LaneError` when it answered with an error; `LaneLost` when no answer came."""
        from goldbot.execution.bridge import _decode, _encode
        with self._lock:
            proc = self._proc
            if self.state != "separate" or proc is None:
                raise LaneLost("order lane not up")
            self._ids += 1
            rid = self._ids
            try:
                proc.stdin.write(_line({"id": rid, "intent": _encode(intent), "sent_at": sent_at}))
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self._down(f"write failed ({type(exc).__name__})")
                raise LaneLost("order lane write failed") from exc
            deadline = time.monotonic() + self.order_max_s
            while True:
                try:
                    msg = self._replies.get(timeout=max(deadline - time.monotonic(), 0.0))
                except queue.Empty:
                    self._down(f"no reply within {self.order_max_s:g} s")
                    raise LaneLost("order lane did not answer") from None
                if msg is None:
                    self._down("the child exited")
                    raise LaneLost("order lane exited")
                if msg.get("id") != rid:
                    continue                          # a stale reply of an order already given up on
                if "error" in msg:
                    raise LaneError(str(msg["error"]))
                res = _decode(msg.get("result"), response=False)
                if not isinstance(res, OrderResult):
                    self._down("a reply that is not an OrderResult")
                    raise LaneLost("order lane answered junk")
                return res


def serve_lane(broker: Any, inp: IO[bytes], out: IO[bytes]) -> None:
    """The child's loop: one order per line from `inp`, its result (or the error) as one line on `out`. `broker` is
    the GuardedBroker on this process's own terminal connection. Returns at EOF (the bridge went)."""
    from goldbot.execution.bridge import GuardedBroker, _decode, _encode
    out.write(_line({"ready": True}))
    out.flush()
    for raw in iter(inp.readline, b""):
        if not raw.startswith(PREFIX):
            continue
        rid: Any = None
        try:
            msg = json.loads(raw[len(PREFIX):])
            rid = msg["id"]
            intent = _decode(msg["intent"], response=False)
            if not isinstance(intent, OrderIntent):
                raise TypeError("not an OrderIntent")
            sent_at = msg.get("sent_at")
            if isinstance(broker, GuardedBroker):
                broker.order_sent_at = float(sent_at) if isinstance(sent_at, (int, float)) else None
            reply: dict[str, Any] = {"id": rid, "result": _encode(broker.place_order(intent))}
        except Exception as exc:                      # noqa: BLE001 - the error goes back, as the bridge's HTTP 500
            log.exception("order lane call failed")
            reply = {"id": rid, "error": f"{type(exc).__name__}: {exc}"}
        out.write(_line(reply))
        out.flush()


def lane_argv(account_id: str) -> list[str]:
    """The child's command line: this interpreter (the Wine Python on the MT5 box, by absolute path)."""
    return [sys.executable, "-m", "goldbot.ops.run", "bridge-order-lane", account_id]
