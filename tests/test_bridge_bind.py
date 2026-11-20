"""The bridge's listen bind waits for its port (audit 2026-11-20, debt 1: the 11-04 icm-demo outage mode). A restarted
bridge whose old process (or Wine's server holding its socket) has not let go of the port must neither crash at once
nor wait forever: it retries the bind with a bounded backoff for at most BIND_WAIT_S, then exits with BIND_FAILED_EXIT
and an ERROR line naming the port (journald; the brain's `bridge:<account>` check FAILs and alerts within its 5 min).
These tests hold a real port with a real listening socket."""
from __future__ import annotations

import errno
import logging
import socket

import pytest

from goldbot.execution import bridge

TOKEN = "t" * 40


class _Clock:
    """A fake monotonic clock: sleeping only advances it."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def _held() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s


def test_a_held_port_is_retried_with_bounded_backoff_then_exits_loudly(caplog):
    holder = _held()
    port = holder.getsockname()[1]
    clock = _Clock()
    try:
        with caplog.at_level(logging.INFO, logger="goldbot.bridge"), pytest.raises(SystemExit) as exc:
            bridge.bind_server(lambda: bridge.make_server(object(), "127.0.0.1", port, TOKEN), port,
                               sleep=clock.sleep, clock=clock.now)
    finally:
        holder.close()
    assert exc.value.code == bridge.BIND_FAILED_EXIT
    assert clock.sleeps, "it must retry before giving up"
    assert max(clock.sleeps) <= bridge.BIND_BACKOFF_MAX_S                       # bounded backoff
    assert clock.sleeps == sorted(clock.sleeps)                                 # growing, never shrinking
    assert clock.t - 1000.0 <= bridge.BIND_WAIT_S + bridge.BIND_BACKOFF_MAX_S   # capped wait
    assert sum(clock.sleeps) >= bridge.BIND_WAIT_S - bridge.BIND_BACKOFF_MAX_S  # and it did wait the cap out
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors and f"port {port}" in errors[-1].getMessage()


def test_the_bind_succeeds_once_the_old_process_lets_go():
    holder = _held()
    port = holder.getsockname()[1]
    clock = _Clock()
    calls = {"n": 0}

    def make():
        calls["n"] += 1
        if calls["n"] == 3:                    # the old bridge exits between the 2nd and 3rd try
            holder.close()
        return bridge.make_server(object(), "127.0.0.1", port, TOKEN)

    httpd = bridge.bind_server(make, port, sleep=clock.sleep, clock=clock.now)
    try:
        assert httpd.server_address[1] == port and calls["n"] == 3 and len(clock.sleeps) == 2
    finally:
        httpd.server_close()


def test_any_other_bind_error_is_not_retried():
    clock = _Clock()

    def make():
        raise OSError(errno.EACCES, "permission denied")

    with pytest.raises(OSError) as exc:
        bridge.bind_server(make, 80, sleep=clock.sleep, clock=clock.now)
    assert exc.value.errno == errno.EACCES and clock.sleeps == []


def test_windows_address_in_use_counts_as_in_use():
    """Under Wine the Windows Python reports WSAEADDRINUSE (10048), not errno 98."""
    clock = _Clock()
    n = {"i": 0}

    def make():
        n["i"] += 1
        raise OSError(10048, "Only one usage of each socket address is normally permitted")

    with pytest.raises(SystemExit):
        bridge.bind_server(make, 8765, sleep=clock.sleep, clock=clock.now)
    assert n["i"] > 1


def test_the_bridge_exit_codes_are_distinct():
    from goldbot.ops.services.bridge import TERMINAL_SPAWNED_EXIT
    codes = {bridge.TERMINAL_LOST_EXIT, TERMINAL_SPAWNED_EXIT, bridge.BIND_FAILED_EXIT}
    assert len(codes) == 3
