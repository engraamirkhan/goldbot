"""Engine loop liveness: a loop heartbeat for health and the systemd watchdog for recovery.

A crashed engine is restarted by systemd in 10 s, but a hung one (alive, its loop stuck in a deadlock, an endless
computation or a call without a timeout) used to show only as a stale engine_<account>.json after ENGINE_FAIL_S
(45 min) with the market open: that file is written on ticks and bar closes, so it is silent whenever the feed is.

`LoopWatch` proves the loop itself turns, whatever the feed does:
- `tick()` is called once per pass of the engine loop (`goldbot.ops.run.run_engine`), ticks or no ticks. It rewrites
  state/heartbeat_engine-<account>.json at most every `beat_every_s` (not `engine_*`: the supervisor and the
  dashboards glob those) and sends WATCHDOG=1 to systemd at most every `notify_every_s`. Health FAILs on a loop
  heartbeat older than LOOP_FAIL_S (`goldbot.ops.health.check_engine_loop`).
- `starting()` covers the start (broker connect, session read, bridge check, warm start, model load): a daemon thread
  writes phase "starting" beats and keeps the watchdog fed for at most `startup_max_s`, so a slow but legitimate start
  never trips it; a start hung longer stops being fed and is ended by systemd (TimeoutStartSec) and FAILs in health.
- `ready()` stops that thread and sends READY=1 just before the loop starts (Type=notify): from then on only the loop
  thread feeds the watchdog, so a stuck loop stops the pings and systemd restarts the engine after WatchdogSec. The
  restart is the ordinary one (restart reconciliation, row X7): stops rest with the broker meanwhile, and the loop that
  runs exits is the one being restarted, so the watchdog never blocks an exit.

Outside systemd (tests, the Windows VPS under NSSM, a shell) NOTIFY_SOCKET is unset and `sd_notify` does nothing.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from goldbot.base import write_atomic

log = logging.getLogger(__name__)

LOOP_BEAT_EVERY_S = 15.0       # loop heartbeat file rewritten at most this often (health FAILs after LOOP_FAIL_S)
NOTIFY_EVERY_S = 5.0           # WATCHDOG=1 at most this often (the unit's WatchdogSec is 300 s)
STARTUP_MAX_S = 15 * 60.0      # the start is fed this long at most; the unit's TimeoutStartSec matches


def loop_heartbeat_path(state_dir: str | Path, account_id: str) -> Path:
    """state/heartbeat_engine-<account>.json: the `heartbeat_` prefix keeps it out of every `engine_*.json` glob."""
    return Path(state_dir) / f"heartbeat_engine-{account_id}.json"


def sd_notify(message: str, env: Mapping[str, str] | None = None) -> bool:
    """Send one sd_notify datagram (READY=1, WATCHDOG=1, STATUS=...) to $NOTIFY_SOCKET. True when sent; False when
    not under systemd (no socket), on a platform without AF_UNIX datagrams, or on any socket error (logged, never
    raised: the trading loop must not die because the notify socket did)."""
    addr = (os.environ if env is None else env).get("NOTIFY_SOCKET", "")
    if not addr or addr[0] not in "/@":            # unset, or a vsock address (not used on these hosts)
        return False
    family = getattr(socket, "AF_UNIX", None)
    if family is None:                             # Windows Python (the Wine bridge, the VPS): nothing to notify
        return False
    target = "\0" + addr[1:] if addr[0] == "@" else addr      # "@name": the abstract namespace
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect(target)
            s.sendall(message.encode())
        return True
    except OSError:
        log.warning("sd_notify %s failed", message.split("=", 1)[0], exc_info=True)
        return False


class LoopWatch:
    def __init__(self, state_dir: str | Path, account_id: str, *, beat_every_s: float = LOOP_BEAT_EVERY_S,
                 notify_every_s: float = NOTIFY_EVERY_S, startup_max_s: float = STARTUP_MAX_S,
                 clock: Callable[[], float] = time.time, notify: Callable[[str], bool] = sd_notify) -> None:
        self.path = loop_heartbeat_path(state_dir, account_id).absolute()   # the cwd may change later
        self.account_id = account_id
        self.beat_every_s, self.notify_every_s, self.startup_max_s = beat_every_s, notify_every_s, startup_max_s
        self.clock, self.notify = clock, notify
        self.started = clock()
        self.passes = 0
        self._last_beat: float | None = None
        self._last_notify: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()                # the startup thread and the caller never write at once

    def _write(self, phase: str, now: float) -> bool:
        doc = {"service": f"engine-{self.account_id}", "phase": phase, "ts": now, "started": self.started,
               "passes": self.passes, "pid": os.getpid()}
        try:
            with self._lock:
                write_atomic(self.path, json.dumps(doc), durable=False)   # rewritten constantly: no fsync
        except OSError:                              # a full disk must not stop the loop; health sees the stale beat
            log.exception("loop heartbeat %s", self.account_id)
            return False
        self._last_beat = now
        return True

    def starting(self) -> None:
        """Beat phase "starting" now and from a daemon thread until `ready()` or `startup_max_s`, feeding the
        watchdog meanwhile (whether systemd arms it before READY=1 or not, a long start never trips it)."""
        self._write("starting", self.clock())
        self.notify("STATUS=starting")

        def feed() -> None:
            deadline = time.monotonic() + self.startup_max_s
            while not self._stop.wait(min(self.notify_every_s, self.beat_every_s)):
                if time.monotonic() >= deadline:
                    log.error("engine %s start exceeded %.0f s: no longer feeding the watchdog", self.account_id,
                              self.startup_max_s)
                    return
                now = self.clock()
                self.notify("WATCHDOG=1")
                if self._last_beat is None or now - self._last_beat >= self.beat_every_s:
                    self._write("starting", now)
        self._thread = threading.Thread(target=feed, name=f"loopwatch-start-{self.account_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the startup feeder (idempotent): `ready()` and a start that ends in an exception call it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def ready(self) -> None:
        """The loop starts now: stop the startup feeder, beat phase "loop", READY=1 (Type=notify)."""
        self.stop()
        now = self.clock()
        self._write("loop", now)
        self.notify("READY=1\nSTATUS=engine loop running")
        self.notify("WATCHDOG=1")
        self._last_notify = now

    def tick(self) -> None:
        """Once per pass of the engine loop, from the loop thread only (a thread feeding the watchdog would hide a
        stuck loop). Never raises."""
        self.passes += 1
        now = self.clock()
        if self._last_beat is None or now - self._last_beat >= self.beat_every_s:
            self._write("loop", now)
        if self._last_notify is None or now - self._last_notify >= self.notify_every_s:
            self.notify("WATCHDOG=1")
            self._last_notify = now
