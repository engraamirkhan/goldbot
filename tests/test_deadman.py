"""Outside dead man's switch (goldbot/ops/deadman.py, ADR 0014): cadence, failure ping, missing URL, and the URL (a
secret) never reaching a log or a health reason."""
from __future__ import annotations

import logging
import urllib.error
import urllib.request
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
def test_a_failed_ping_never_raises_never_logs_the_url_and_is_retried(exc, caplog):
    calls: list[str] = []

    def boom(u: str, b: bytes | None) -> None:
        calls.append(u)
        raise exc
    dm, _, clock = make(ping=boom)
    with caplog.at_level(logging.DEBUG):
        assert dm.after_health(report()) is None
        clock.t += 60
        assert dm.after_health(report()) is None              # not accepted: retried on the next pass, not per period
    assert len(calls) == 2
    assert "secret-uuid" not in caplog.text and "hc-ping" not in caplog.text
    assert "dead man's switch ping failed" in caplog.text


def test_http_ping_uses_get_for_ok_and_post_for_fail(monkeypatch):
    seen: list[tuple[str, str, bytes | None, float]] = []

    class Resp:
        status = 200
        headers: dict[str, str] = {}

        def __enter__(self) -> "Resp":
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> Resp:
        seen.append((req.full_url, req.get_method(), req.data, timeout))   # type: ignore[arg-type]
        return Resp()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    deadman.http_ping(URL, None)
    deadman.http_ping(URL + "/fail", b"failing: x")
    assert seen == [(URL, "GET", None, deadman.PING_TIMEOUT_S), (URL + "/fail", "POST", b"failing: x", deadman.PING_TIMEOUT_S)]
    Resp.status = 302
    with pytest.raises(urllib.error.HTTPError):
        deadman.http_ping(URL, None)


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
