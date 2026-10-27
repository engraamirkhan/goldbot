"""BACKLOG 135: the MT5 bridge's terminal queue (closes ahead of reads and orders, abandoned reads dropped).

The MetaTrader5 package is not thread-safe, so a terminal connection serves one call at a time. Up to protocol v6 the
bridge also accepted one HTTP request at a time (HTTPServer): while the terminal was sending an entry (Vantage: up to
31 s) every close queued behind it, and behind every read the brain had already given up on, so the worst engine-side
exit delay was about 45-55 s at a 35 s order timeout (trading-safety review of 5d661e94, ADR 0052).

Design rules (ADR 0056):
- "Closes ahead of reads and orders": requests are accepted concurrently and run by ONE worker per terminal connection
  from a priority queue: `close` and `modify` first, then /health, then reads, then `place_order`; first come, first
  served within a class. Still one terminal call at a time per connection; a call already running is never
  interrupted (an order the terminal is sending is never cut).
- "Abandoned reads are dropped": a queued request whose client has closed its socket is never run (`client_gone`),
  whatever its method; a read still queued past the timeout its client sent (`timeout_s`, protocol v7; READ_TTL_S
  without one) is dropped; an order older than ORDER_MAX_AGE_S when it reaches the front is refused stale, as at the
  door. The bridge never re-sends anything.
- At most MAX_QUEUED requests wait; one more is refused (503, nothing run), so a flood cannot exhaust the box.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import select
import socket
import threading
from typing import Any, Callable

log = logging.getLogger("goldbot.bridge")

# priority classes: lower runs first
CLOSE, HEALTH, READ, ORDER = 0, 1, 2, 3
EXIT_METHODS = frozenset({"close", "modify"})      # risk-reducing or stop-moving: ahead of everything
MAX_QUEUED = 64                 # requests waiting per connection; the brain has about six clients per account
READ_TTL_S = 30.0               # a read from a client that sent no timeout_s (a v6 brain) is dropped after this
POLL_S = 0.05                   # how often a waiting handler looks at its client's socket


def priority_of(method: str) -> int:
    if method in EXIT_METHODS:
        return CLOSE
    if method == "place_order":
        return ORDER
    return READ


def client_gone(sock: Any) -> bool:
    """True when the client closed its end (a read with no data pending returns EOF) or the socket is broken. A
    client that sent more bytes (a pipelined request) is still there. Never blocks."""
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return False
        return sock.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


class Job:
    """One request for the terminal. `fn` runs on the queue's worker; `refusal()` (also on the worker, just before
    `fn`) says why it must not run any more (None: run it). `status`: queued -> running -> done, or skipped (with
    `skipped` saying why) or cancelled (its client went while it waited)."""

    def __init__(self, fn: Callable[[], Any], *, priority: int, label: str,
                 refusal: Callable[[], str | None] | None = None) -> None:
        self.fn, self.priority, self.label = fn, priority, label
        self.refusal = refusal
        self.status = "queued"
        self.skipped: str | None = None
        self.result: Any = None
        self.error: BaseException | None = None
        self.done = threading.Event()


class TerminalQueue:
    """One worker thread serving one terminal connection from a priority queue (see the module docstring)."""

    def __init__(self, name: str = "terminal", max_queued: int = MAX_QUEUED) -> None:
        self.name, self.max_queued = name, max_queued
        self._heap: list[tuple[int, int, Job]] = []
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self.running: Job | None = None
        self.counts = {"run": 0, "skipped": 0, "cancelled": 0, "refused_full": 0}

    def submit(self, job: Job) -> bool:
        """Queue `job`; False (nothing queued) when MAX_QUEUED requests already wait."""
        with self._cv:
            if len(self._heap) >= self.max_queued:
                self.counts["refused_full"] += 1
                return False
            heapq.heappush(self._heap, (job.priority, next(self._seq), job))
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name=f"bridge-{self.name}", daemon=True)
                self._thread.start()
            self._cv.notify()
        return True

    def cancel(self, job: Job) -> bool:
        """Its client went: a job still queued is never run (True); one already running finishes (False)."""
        with self._cv:
            if job.status != "queued":
                return False
            job.status = "cancelled"
            self.counts["cancelled"] += 1
        job.done.set()
        return True

    def depth(self) -> int:
        with self._cv:
            return sum(1 for _, _, j in self._heap if j.status == "queued")

    def _next(self) -> Job:
        with self._cv:
            while True:
                while not self._heap:
                    self._cv.wait()
                _, _, job = heapq.heappop(self._heap)
                if job.status == "queued":            # a cancelled job is simply forgotten
                    job.status = "running"
                    self.running = job
                    return job

    def _loop(self) -> None:
        while True:
            job = self._next()
            try:
                why = job.refusal() if job.refusal is not None else None
                if why is not None:
                    job.status, job.skipped = "skipped", why
                    self.counts["skipped"] += 1
                    log.info("bridge %s: %s not run (%s)", self.name, job.label, why)
                    continue
                try:
                    job.result = job.fn()
                except BaseException as exc:         # noqa: BLE001 - handed to the request's own thread
                    job.error = exc
                self.counts["run"] += 1
                if job.status == "running":
                    job.status = "done"
            except Exception:                         # noqa: BLE001 - the worker never dies with a request
                log.exception("bridge %s: queue worker error on %s", self.name, job.label)
                job.status = "done"
            finally:
                self.running = None
                job.done.set()

    def wait(self, job: Job, sock: Any, poll_s: float = POLL_S) -> bool:
        """Block until `job` finished; True, or False when its client went while it was still queued (it is cancelled
        and never run). A job already running is waited for, whatever the client does."""
        while not job.done.wait(poll_s):
            if job.status == "queued" and sock is not None and client_gone(sock) and self.cancel(job):
                return False
        return job.status not in ("cancelled",)


class LaneLost(ConnectionError):
    """The order lane's process died or stopped answering with an order in it: its outcome is unknown. The bridge
    answers nothing (the brain sees no reply: an ambiguous order, settled by the BACKLOG 72 look-up) and never sends
    the order again."""


class LaneError(RuntimeError):
    """The order lane answered with an error (the terminal's or GuardedBroker's), as the inline path's HTTP 500."""
