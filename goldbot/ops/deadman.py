"""Outside dead man's switch (ADR 0014): the brain pings a free check service, which alerts the owner when pings stop.

Every alert used to come from the brain's own Telegram service, so a dead brain (host down, OOM, a crashed Telegram
service, a revoked bot token) was silent. Now the Telegram service's health pass, which runs every minute, also pings
an outside URL. The owner creates the check on Healthchecks.io (free tier) and points it at their email or Telegram.
That service notices a missing ping without any help from this machine.

* Healthy (the report has no FAIL and the alert was delivered): a GET of the URL, at most every
  `telegram.deadman_every_min` minutes.
* Unhealthy (a FAIL, or the Telegram alert could not be sent): a POST to `<url>/fail` (the Healthchecks.io
  convention; Better Stack heartbeats use it too), with only the names of the failing checks as the body. The
  ok ping is not sent while unhealthy, so a service that does not understand `/fail` still sees missing pings
  and alerts after its grace time.
* A change of state (ok -> fail, fail -> ok) pings at once. The cadence is held in memory: a restart pings at once.
* Nothing runs the health pass (the Telegram service or the host is dead, or the checks raise): no ping, and the
  outside service alerts.

The URL is a secret: anyone holding it can mark the check up. It lives in the keyring only
(`python -m goldbot.ops.accounts set deadman-url`), never in config, the repo or a log. Errors are logged by
exception type or HTTP status only. This uses the stdlib `urllib`, which logs nothing; urllib3, under requests, logs
the request path at debug level, and the path holds the check's secret id. Unset: a no-op, and health WARNs
`deadman` with the command to set it.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Literal
from urllib.parse import urlsplit

from goldbot.ops.health import HealthReport

log = logging.getLogger("goldbot.deadman")

DEADMAN_KEY = "deadman-url"
DEADMAN_EVERY_MIN = 2            # with a 2 min period + 3 min grace the owner hears of a dead brain within ~6 min (S5)
PING_TIMEOUT_S = 5.0             # per socket operation (connect, each read)
PING_DEADLINE_S = 8.0            # the whole attempt, DNS included; it runs off the Telegram pump either way
_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_.:-]")
MAX_BODY = 1000                  # Healthchecks.io stores up to 100 kB per ping; names only
SET_HINT = "python -m goldbot.ops.accounts set deadman-url"

Kind = Literal["ok", "fail"]
Ping = Callable[[str, bytes | None], None]       # (url, body or None for a GET); raises on failure


def url_problem(url: str) -> str | None:
    """Why `url` cannot be used as a ping URL, or None. Never echoes the URL (it is a secret)."""
    if any(c.isspace() for c in url):
        return "deadman-url contains whitespace"
    parts = urlsplit(url)
    if parts.scheme != "https":
        return "deadman-url is not an https:// URL (the ping would travel in clear text)"
    if not parts.hostname:
        return "deadman-url has no host"
    if parts.username or parts.password:
        return "deadman-url carries a user name or password"
    return None


def fail_url(url: str) -> str:
    """`<url>/fail` (Healthchecks.io and Better Stack); a query string is kept after the path."""
    parts = urlsplit(url)
    return parts._replace(path=parts.path.rstrip("/") + "/fail").geturl()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is never followed (security review L1): the 3xx surfaces as an HTTPError, i.e. a failed ping."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _request(url: str, body: bytes | None) -> None:
    req = urllib.request.Request(url, data=body, method="GET" if body is None else "POST",
                                 headers={"User-Agent": "goldbot-deadman"})
    with _OPENER.open(req, timeout=PING_TIMEOUT_S) as resp:     # https only (url_problem)
        if not 200 <= resp.status < 300:                        # a 3xx is a failure too, never followed
            raise urllib.error.HTTPError("", resp.status, "not 2xx", resp.headers, None)


def http_ping(url: str, body: bytes | None) -> None:
    """GET (body None) or POST; only a 2xx is success, redirects are refused. Raises urllib's errors (their text has
    no URL), or TimeoutError when the attempt passes PING_DEADLINE_S: socket timeouts do not bound DNS, so the request
    runs in a daemon thread that is abandoned at the deadline."""
    out: list[BaseException] = []

    def run() -> None:
        try:
            _request(url, body)
        except BaseException as exc:             # noqa: BLE001 - re-raised in the caller
            out.append(exc)
    t = threading.Thread(target=run, name="deadman-ping", daemon=True)
    t.start()
    t.join(PING_DEADLINE_S)
    if t.is_alive():
        raise TimeoutError("dead man's switch ping passed its deadline")
    if out:
        raise out[0]


def _describe(exc: BaseException) -> str:
    """Exception type and HTTP status only: messages can carry a host or path."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return type(exc).__name__


class DeadMan:
    """Called by the Telegram service after each health pass (`after_health`). Never raises."""

    def __init__(self, get_secret: Callable[[str], str | None], every_min: float = DEADMAN_EVERY_MIN,
                 ping: Ping = http_ping, clock: Callable[[], float] = time.monotonic):
        self.get_secret, self.every_s, self.ping, self.clock = get_secret, every_min * 60.0, ping, clock
        self._last_kind: Kind | None = None       # kind of the last attempt, accepted or not
        self._last_attempt: float | None = None   # a failed attempt counts too: a dead endpoint is tried once a period
        self._busy = threading.Lock()             # one ping at a time; a pass finding one in flight skips
        self._warned: str | None = None           # the last config problem logged (logged once, not every minute)

    def _url(self) -> str | None:
        try:
            url = (self.get_secret(DEADMAN_KEY) or "").strip()
        except Exception as exc:                  # noqa: BLE001 - a locked keyring must not stop the health pass
            self._warn_once(f"deadman-url unreadable ({type(exc).__name__})")
            return None
        if not url:
            self._warn_once(f"dead man's switch not set ({SET_HINT}); no outside alert if this server dies")
            return None
        problem = url_problem(url)
        if problem:
            self._warn_once(problem)
            return None
        self._warned = None
        return url

    def _warn_once(self, msg: str) -> None:
        if msg != self._warned:
            log.warning(msg)
            self._warned = msg

    def due(self, kind: Kind) -> bool:
        if self._last_attempt is None or kind != self._last_kind:
            return True
        return self.clock() - self._last_attempt >= self.every_s

    def after_health(self, report: HealthReport, *, delivered: bool = True) -> Kind | None:
        """Ping for this report when due; returns the kind the service accepted, None when nothing was accepted (not
        due, not set, a ping already in flight, or the attempt failed). Runs in a worker thread (bot.py)."""
        if not self._busy.acquire(blocking=False):
            return None
        try:
            return self._after_health(report, delivered)
        finally:
            self._busy.release()

    def _after_health(self, report: HealthReport, delivered: bool) -> Kind | None:
        try:
            url = self._url()
            if url is None:
                return None
            failing = sorted(report.failing())
            kind: Kind = "fail" if failing or not delivered else "ok"
            if not self.due(kind):
                return None
            self._last_kind, self._last_attempt = kind, self.clock()
            if kind == "ok":
                self.ping(url, None)
            else:
                names = [_NAME_UNSAFE.sub("_", n) for n in failing] + ([] if delivered else ["telegram_alert_undelivered"])
                self.ping(fail_url(url), ("failing: " + ", ".join(names))[:MAX_BODY].encode("utf-8"))
        except Exception as exc:                  # noqa: BLE001 - the next pass retries; the outside service alerts
            log.warning("dead man's switch ping failed: %s", _describe(exc))
            return None
        return kind
