"""Outside dead man's switch (ADR 0014): the brain pings a free check service, which alerts the owner when pings stop.

Every alert used to come from the brain's own Telegram service, so a dead brain (host down, OOM, a crashed Telegram
service, a revoked bot token) was silent. Now the Telegram service pings an outside URL. The owner creates the check on
Healthchecks.io (free tier) and points it at their email or Telegram. That service notices a missing ping without any
help from this machine.

Owner complaint 2026-11-04 (the monitor's emails went "up and down"): the ping used to follow each health pass, so
(a) every transient FAIL pinged /fail (a "down" email, then an "up" one), (b) a health pass slower than the
monitor's period made the pings late, and (c) a deploy's restart left minutes without a ping. Now:

* Liveness has its own cadence: a daemon thread (`start_liveness`) beats every `live_every_s` = min(60 s, half of
  `telegram.deadman_every_min`), never waiting on a health pass, so the monitor's period and grace no longer matter.
  It beats only on a health verdict (SRE review HIGH on b6ca26c2): the first completed pass after a start, or the
  last pass read back from state/health_last.json when fresh (bot.py `_seed_deadman`). Without one it sends nothing,
  so a service that crashes in every pass never looks alive. It keeps beating through a slow shutdown until the
  process exits, so a clean restart's gap is the time the process is really down (under a minute on 2026-11-04).
* The health pass only hands over its verdict (`observe`): the failures the owner was told about
  (goldbot/ops/health.py `HealthWatch.failing`, the same hysteresis as the Telegram alerts: 3 polls and 5 min, or the
  immediate list) and the transient rest.
* Healthy: a GET of the URL; with a transient FAIL a POST to the URL naming it (Healthchecks.io keeps the body in
  the check's log; the check stays up).
* Unhealthy (a confirmed FAIL, a Telegram alert undelivered on two passes in a row, or no health verdict for
  HEALTH_STALE_S, i.e. a frozen pump: a blocked send, a blocked event loop or a dead pump task): a POST to
  `<url>/fail` (the Healthchecks.io convention; Better Stack heartbeats use it too) naming the checks. The ok ping is
  not sent while unhealthy, so a service that does not understand `/fail` still sees missing pings.
* A change of state (ok -> fail, fail -> ok) pings at once. A dead service or host: no ping, the outside service
  alerts. A hung health loop: /fail once its verdict is HEALTH_STALE_S old.

`/start` is not used: on Healthchecks.io it only times a run and turns the check down when no success follows within
the grace, so it could add an email around a deploy, never remove one. Nothing here needs an API key.

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
from typing import Callable, Iterable, Literal
from urllib.parse import urlsplit

from goldbot.ops.health import HealthReport

log = logging.getLogger("goldbot.deadman")

DEADMAN_KEY = "deadman-url"
DEADMAN_EVERY_MIN = 2            # the monitor's period (telegram.deadman_every_min); liveness beats well inside it
LIVE_EVERY_S = 60.0              # the slowest liveness beat, whatever the period (owner 2026-11-04: 60 s if unknown)
# no health verdict this long: the pump is frozen, /fail. 3 health periods (bot.HEALTH_EVERY_S, 60 s) plus the
# slowest pass seen (about 2 min before the 2026-11-04 CPU fix's runs of over 3 min were cut to ~10 s), not the
# alerts check's 15 min (SRE review HIGH on b6ca26c2: a frozen pump looked alive for 15 min)
HEALTH_STALE_S = 300.0
UNDELIVERED_FAIL_PASSES = 2      # a failed Telegram send is retried next pass; two in a row ping /fail (SRE review LOW)
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


def live_every_s(every_min: float) -> float:
    """The liveness beat: half the monitor's period, at most LIVE_EVERY_S, so one late beat never misses a period."""
    return min(LIVE_EVERY_S, every_min * 60.0 / 2)


class DeadMan:
    """Fed by the Telegram service's health pass (`observe`), pinged by its own liveness thread (`beat`). Never
    raises. `on_change(kind, names)` hears each accepted change of state (each one an email from the monitor; the
    first ok after a start is not one) for the alert log."""

    def __init__(self, get_secret: Callable[[str], str | None], every_min: float = DEADMAN_EVERY_MIN,
                 ping: Ping = http_ping, clock: Callable[[], float] = time.monotonic,
                 on_change: Callable[[Kind, tuple[str, ...]], None] | None = None):
        self.get_secret, self.every_s, self.ping, self.clock = get_secret, live_every_s(every_min), ping, clock
        self.on_change = on_change
        self._undelivered = 0                     # consecutive passes whose Telegram alert was not delivered
        self._last_kind: Kind | None = None       # kind of the last attempt, accepted or not
        self._last_attempt: float | None = None   # a failed attempt counts too: a dead endpoint is tried once a beat
        self._accepted: Kind | None = None        # kind of the last ping the service accepted
        self._busy = threading.Lock()             # one ping at a time; a beat finding one in flight skips
        self._warned: str | None = None           # the last config problem logged (logged once, not every beat)
        # the last health verdict: (confirmed failing, transient failing, alert delivered, clock at the pass)
        self._verdict: tuple[tuple[str, ...], tuple[str, ...], bool, float] | None = None

    def _url(self) -> str | None:
        try:
            url = (self.get_secret(DEADMAN_KEY) or "").strip()
        except Exception as exc:                  # noqa: BLE001 - a locked keyring must not stop the beat
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

    def observe(self, failing: Iterable[str], *, transient: Iterable[str] = (), delivered: bool = True,
                age_s: float = 0.0) -> None:
        """The health pass's verdict: `failing` are the confirmed failures (HealthWatch.failing), `transient` the
        FAILs still inside the alert window. `age_s` dates a verdict read back from state/health_last.json at start."""
        self._undelivered = 0 if delivered else self._undelivered + 1
        self._verdict = (tuple(sorted(failing)), tuple(sorted(transient)), delivered, self.clock() - age_s)

    def _state(self) -> tuple[Kind, list[str], list[str]] | None:
        """(kind, failing names, transient names) from the last verdict and its age; None without a verdict (no ping:
        a service that never completes a pass must not look alive)."""
        if self._verdict is None:
            return None
        failing, transient, _, at = self._verdict
        if self.clock() - at > HEALTH_STALE_S:
            return "fail", ["health_checks_not_completing"], []
        names = list(failing) + (["telegram_alert_undelivered"]
                                 if self._undelivered >= UNDELIVERED_FAIL_PASSES else [])
        return ("fail" if names else "ok"), names, list(transient)

    def beat(self) -> Kind | None:
        """Ping when due; returns the kind the service accepted, None when nothing was accepted (not due, not set, a
        ping already in flight, or the attempt failed). Runs in the liveness thread (or a worker thread)."""
        if not self._busy.acquire(blocking=False):
            return None
        try:
            return self._beat()
        finally:
            self._busy.release()

    def _beat(self) -> Kind | None:
        try:
            url = self._url()
            if url is None:
                return None
            state = self._state()
            if state is None:
                return None
            kind, names, transient = state
            if not self.due(kind):
                return None
            self._last_kind, self._last_attempt = kind, self.clock()
            safe = [_NAME_UNSAFE.sub("_", n) for n in names]
            if kind == "fail":
                self.ping(fail_url(url), ("failing: " + ", ".join(safe))[:MAX_BODY].encode("utf-8"))
            elif transient:
                body = "ok; failing, not confirmed yet: " + ", ".join(_NAME_UNSAFE.sub("_", n) for n in transient)
                self.ping(url, body[:MAX_BODY].encode("utf-8"))
            else:
                self.ping(url, None)
        except Exception as exc:                  # noqa: BLE001 - the next beat retries; the outside service alerts
            log.warning("dead man's switch ping failed: %s", _describe(exc))
            return None
        if kind != self._accepted and not (self._accepted is None and kind == "ok") and self.on_change is not None:
            try:
                self.on_change(kind, tuple(safe))
            except Exception as exc:              # noqa: BLE001 - the log never stops the switch
                log.warning("dead man's switch change log failed: %s", type(exc).__name__)
        self._accepted = kind
        return kind

    def after_health(self, report: HealthReport, *, delivered: bool = True,
                     failing: Iterable[str] | None = None) -> Kind | None:
        """`observe` this report (its FAILs are all confirmed unless `failing` names the confirmed ones), then `beat`.
        One call per pass, for callers without a liveness thread."""
        confirmed = set(report.failing() if failing is None else failing)
        self.observe(confirmed, transient=report.failing() - confirmed, delivered=delivered)
        return self.beat()


def start_liveness(dm: DeadMan, *, stop: threading.Event | None = None, tick_s: float = 5.0) -> threading.Thread:
    """The liveness beat on its own daemon thread: `dm.beat()` every `tick_s` (it pings only when due), from start
    until the process exits or `stop` is set. Never waits on a health pass; a beat that raises is logged and the loop
    goes on."""
    stop = stop or threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                dm.beat()
            except Exception:                     # noqa: BLE001 - beat never raises; belt and braces for the thread
                log.exception("dead man's switch beat")
            stop.wait(tick_s)
    t = threading.Thread(target=loop, name="deadman-liveness", daemon=True)
    t.start()
    return t
