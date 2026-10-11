"""Outside dead man's switch (goldbot/ops/deadman.py, ADR 0014): cadence, failure ping, missing URL, and the URL (a
secret) never reaching a log or a health reason."""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.ops import deadman, health
from goldbot.ops.deadman import DeadMan, fail_url, url_problem
from goldbot.ops.health import Check, HealthContext, HealthReport

URL = "https://hc-ping.com/0b5c2f4e-secret-uuid-7d1a"
NOW = pd.Timestamp("2026-10-07 14:00", tz="UTC")


def report(*fails: str) -> HealthReport:
    checks = [Check(name="disk", status="ok", reason="fine")] + [Check(name=n, status="fail", reason="x") for n in fails]
    return HealthReport(ts=NOW, status="fail" if fails else "ok", checks=checks)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(url: str | None = URL, every_min: float = 2, ping: Any = None) -> tuple[DeadMan, list[tuple[str, bytes | None]], Clock]:
    sent: list[tuple[str, bytes | None]] = []
    clock = Clock()
    dm = DeadMan(lambda k: url if k == "deadman-url" else None, every_min,
                 ping=ping or (lambda u, b: sent.append((u, b))), clock=clock)
    return dm, sent, clock


def test_healthy_pings_at_most_every_period():
    dm, sent, clock = make(every_min=2)
    assert dm.after_health(report()) == "ok"           # first pass after a start: at once
    for _ in range(3):                                  # the health pass runs every minute
        clock.t += 60
        dm.after_health(report())
    assert sent == [(URL, None), (URL, None)]           # t=0 and t=120 s; not t=60, t=180
    clock.t += 60
    assert dm.after_health(report()) == "ok" and len(sent) == 3


def test_a_fail_pings_the_fail_url_at_once_with_check_names_only():
    dm, sent, clock = make()
    dm.after_health(report())
    clock.t += 10
    assert dm.after_health(report("engine:icm-demo", "heartbeat:telegram")) == "fail"
    assert sent[-1] == ("https://hc-ping.com/0b5c2f4e-secret-uuid-7d1a/fail",
                        b"failing: engine:icm-demo, heartbeat:telegram")
    clock.t += 60
    assert dm.after_health(report("engine:icm-demo")) is None         # still failing: repeated only per period
    clock.t += 60
    assert dm.after_health(report("engine:icm-demo")) == "fail"
    clock.t += 5
    assert dm.after_health(report()) == "ok"
    last: tuple[str, bytes | None] = sent[-1]
    assert last == (URL, None)                          # recovery: at once; no ok ping while failing
    assert [u for u, _ in sent].count(URL) == 2


def test_an_undelivered_telegram_alert_is_a_fail_ping():
    dm, sent, _ = make()
    assert dm.after_health(report(), delivered=False) == "fail"
    assert sent == [(URL + "/fail", b"failing: telegram_alert_undelivered")]


def test_fail_body_is_capped():
    dm, sent, _ = make()
    dm.after_health(report(*[f"check_{i:04d}" for i in range(500)]))
    assert len(sent[0][1] or b"") == deadman.MAX_BODY


@pytest.mark.parametrize("url", [None, "", "   "])
def test_missing_url_is_a_no_op_with_one_warning(url, caplog):
    dm, sent, clock = make(url=url)
    with caplog.at_level(logging.WARNING, logger="goldbot.deadman"):
        assert dm.after_health(report()) is None
        clock.t += 600
        assert dm.after_health(report("engine:x")) is None
    assert sent == []
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 1 and "accounts set deadman-url" in msgs[0]


@pytest.mark.parametrize("bad", ["http://hc-ping.com/abc", "hc-ping.com/abc", "https:///abc",
                                 "https://user:pw@hc-ping.com/abc", "https://hc-ping.com/a b"])
def test_unusable_urls_are_refused_without_echoing_them(bad, caplog):
    dm, sent, _ = make(url=bad)
    with caplog.at_level(logging.WARNING, logger="goldbot.deadman"):
        assert dm.after_health(report()) is None
    assert sent == [] and url_problem(bad) is not None
    assert "hc-ping" not in caplog.text and "abc" not in caplog.text


def test_fail_url_keeps_a_query_and_a_trailing_slash_does_not_double():
    assert fail_url("https://hc-ping.com/u/") == "https://hc-ping.com/u/fail"
    assert fail_url("https://x.example/ping/key/slug?create=1") == "https://x.example/ping/key/slug/fail?create=1"


@pytest.mark.parametrize("exc", [
    urllib.error.HTTPError(URL, 404, f"not found {URL}", None, None),   # type: ignore[arg-type]
    urllib.error.URLError(f"cannot reach {URL}"),
    TimeoutError(f"timed out {URL}"),
    RuntimeError(URL),
])
def test_a_failed_ping_never_raises_never_logs_the_url_and_backs_off(exc, caplog):
    """Security review M1: a failed attempt counts for the cadence, so a dead endpoint is tried once per period, not
    on every one-minute pass. A change of state is still tried at once."""
    calls: list[str] = []

    def boom(u: str, b: bytes | None) -> None:
        calls.append(u)
        raise exc
    dm, _, clock = make(ping=boom, every_min=2)
    with caplog.at_level(logging.DEBUG):
        assert dm.after_health(report()) is None
        clock.t += 60
        assert dm.after_health(report()) is None              # backing off: not attempted
        assert len(calls) == 1
        clock.t += 60
        assert dm.after_health(report()) is None              # one period after the failed attempt: tried again
        assert len(calls) == 2
        clock.t += 1
        assert dm.after_health(report("engine:x")) is None     # a state change is tried at once
        assert calls[-1].endswith("/fail") and len(calls) == 3
    assert "secret-uuid" not in caplog.text and "hc-ping" not in caplog.text
    assert "dead man's switch ping failed" in caplog.text


def test_a_ping_in_flight_skips_new_ones():
    """Security review M1: while one ping hangs, later passes do not stack more threads behind it."""
    release, started = threading.Event(), threading.Event()
    calls: list[str] = []

    def slow(u: str, b: bytes | None) -> None:
        calls.append(u)
        started.set()
        release.wait(5)
    dm, _, clock = make(ping=slow)
    t = threading.Thread(target=dm.after_health, args=(report(),))
    t.start()
    assert started.wait(5)
    clock.t += 600
    t0 = time.monotonic()
    assert dm.after_health(report("engine:x")) is None          # skipped at once, not queued
    assert time.monotonic() - t0 < 0.5 and len(calls) == 1
    release.set()
    t.join(5)
    clock.t += 1
    assert dm.after_health(report("engine:x")) == "fail" and len(calls) == 2   # free again after it finished


def test_fail_body_names_are_sanitised():
    dm, sent, _ = make()
    dm.after_health(report("engine:icm-demo", "bad name\n<script>", "ok_name.v2@x"))
    assert sent[0][1] == b"failing: bad_name__script_, engine:icm-demo, ok_name.v2_x"


# ------------------------------------------------------------------------------------------------ http_ping, local server
class _Handler(BaseHTTPRequestHandler):
    hits: list[tuple[str, str, bytes]] = []
    mode = "ok"

    def _serve(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        type(self).hits.append((self.command, self.path, self.rfile.read(n) if n else b""))
        if self.path.startswith("/target"):
            self.send_response(200)
        elif type(self).mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/target")
        elif type(self).mode == "slow":
            time.sleep(2)
            self.send_response(200)
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = _serve

    def log_message(self, *a: object) -> None:
        return None


@pytest.fixture
def server():
    _Handler.hits, _Handler.mode = [], "ok"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_http_ping_uses_get_for_ok_and_post_for_fail(server):
    deadman.http_ping(server + "/u", None)
    deadman.http_ping(server + "/u/fail", b"failing: x")
    assert _Handler.hits == [("GET", "/u", b""), ("POST", "/u/fail", b"failing: x")]


@pytest.mark.parametrize("body", [None, b"failing: x"])
def test_http_ping_refuses_redirects(server, body):
    """Security review L1: a redirect is a failure and is never followed (GET or POST)."""
    _Handler.mode = "redirect"
    with pytest.raises(urllib.error.HTTPError) as e:
        deadman.http_ping(server + "/u", body)
    assert e.value.code == 302
    assert [h[1] for h in _Handler.hits] == ["/u"]               # /target was never requested


def test_http_ping_has_an_overall_deadline(server, monkeypatch):
    """Security review M1: a hung endpoint ends the attempt at the deadline (socket timeouts alone do not bound it)."""
    _Handler.mode = "slow"
    monkeypatch.setattr(deadman, "PING_TIMEOUT_S", 5.0)
    monkeypatch.setattr(deadman, "PING_DEADLINE_S", 0.3)
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        deadman.http_ping(server + "/u", None)
    assert time.monotonic() - t0 < 1.5


def test_ping_timeouts_are_short():
    assert deadman.PING_TIMEOUT_S <= 5.0 and deadman.PING_TIMEOUT_S < deadman.PING_DEADLINE_S <= 10.0


# ------------------------------------------------------------------------------------------------ the Telegram pump
def test_the_health_pass_does_not_wait_for_the_ping(monkeypatch):
    """Security review M1: the pump that sends proposals (the owner's 90 s window) never waits on the ping."""
    from goldbot.telegram import bot as botmod
    release, started = threading.Event(), threading.Event()

    def slow(u: str, b: bytes | None) -> None:
        started.set()
        release.wait(10)
    monkeypatch.setattr(botmod, "run_checks", lambda ctx: report())
    monkeypatch.setattr(botmod.HealthContext, "from_runtime", classmethod(lambda cls, state_dir: None))

    async def go() -> float:
        tasks: list[asyncio.Task[Any]] = []

        class App:
            def create_task(self, coro: Any) -> asyncio.Task[Any]:
                t = asyncio.get_running_loop().create_task(coro)
                tasks.append(t)
                return t

        b = object.__new__(botmod.TelegramBot)
        b.app, b.owner_ids, b.state_dir = App(), set(), "unused"
        b.health = type("W", (), {"alert": lambda self, r: None, "record": lambda self, r: None})()
        b.deadman = make(ping=slow)[0]
        t0 = time.monotonic()
        await asyncio.wait_for(b._health_pass(), 3)
        took = time.monotonic() - t0
        assert await asyncio.to_thread(started.wait, 5)          # the ping did start, off the pump
        release.set()
        await asyncio.gather(*tasks)
        return took
    assert asyncio.run(go()) < 1.0


def test_mt5_bootstrap_checks_the_python_installer_before_running_it():
    """Security review L2: the Windows Python installer is checked against a pinned sha256 before Wine runs it."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "goldbot/ops/linux/mt5_bootstrap.sh").read_text(encoding="utf-8")
    m = re.search(r"^PY_SHA256=([0-9a-f]{64})\b", text, re.M)
    assert m, "PY_SHA256 must be a 64-hex pin"
    check, run = text.index("sha256sum -c"), text.index("wine python.exe")
    assert check < run
    assert "python.exe\" | sha256sum -c" in text or "python.exe' | sha256sum -c" in text or 'python.exe" | sha256sum -c' in text


def test_a_broken_keyring_does_not_stop_the_pass():
    def broken(k: str) -> str | None:
        raise RuntimeError("keyring locked")
    dm = DeadMan(broken, ping=lambda u, b: None)
    assert dm.after_health(report()) is None


# ------------------------------------------------------------------------------------------------ health check
def ctx(tmp_path: Any, url: str | None) -> HealthContext:
    return HealthContext(state_dir=tmp_path, now=NOW, settings=load_settings(),
                         get_secret=lambda k: url if k == "deadman-url" else None)


def test_health_warns_with_the_command_when_unset(tmp_path):
    c = health.check_deadman(ctx(tmp_path, None))
    assert c.status == "warn" and "python -m goldbot.ops.accounts set deadman-url" in c.reason
    assert "first-deploy.md" in c.reason


def test_health_ok_when_set_and_never_shows_the_url(tmp_path):
    c = health.check_deadman(ctx(tmp_path, URL))
    assert c.status == "ok" and "secret-uuid" not in c.reason and "hc-ping" not in c.reason
    bad = health.check_deadman(ctx(tmp_path, "http://hc-ping.com/secret-uuid"))
    assert bad.status == "warn" and "https" in bad.reason and "secret-uuid" not in bad.reason


def test_deadman_is_in_the_full_report_and_not_an_alert(tmp_path):
    r = health.run_checks(ctx(tmp_path, None))
    c = next(c for c in r.checks if c.name == "deadman")
    assert c.status == "warn" and not health._alerting(c.name, c.status)
    assert "deadman" not in [c.name for c in health.run_checks(ctx(tmp_path, None), static_only=True).checks]


def test_settings_default_period():
    assert load_settings().telegram.deadman_every_min == 2
