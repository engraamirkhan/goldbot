"""TradingView's public ideas API behind a read-only, cookie-less GET guard (ADR 0030's read-only rules; owner
directive 2026-10-28: follow the three authors' ideas).

The author-ideas collector (goldbot/data/tv_ideas.py) talks to TradingView only through `TvNetwork`, so the tests
replay a fake API and no test reaches the network. The rules are enforced here rather than trusted to the caller:

* "Strictly read-only": the interface has a GET and nothing else (no POST, PUT, DELETE, no websocket); `ReadOnlyNetwork`
  lets a GET through only to the public ideas endpoints in `READ_URLS` over https. Anything else raises
  `ReadOnlyViolation` before a byte leaves.
* No session: no cookie or account token is ever sent (the owner's TradingView session never touches this path).
* A polite rate: `Pacer` spaces two requests by at least `min_interval_s`; bodies are capped at `MAX_HTTP_BYTES`.

Kept from the retired chart pull (goldbot/data/tv_wire.py and tv_pull.py, removed 2026-11-02 with the whole
TradingView chart path): only what the ideas collector used, narrowed to the ideas endpoints.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.parse import unquote, urlsplit

MAX_HTTP_BYTES = 4 * 2**20           # about three times the largest ideas page seen (100 items)
SITE = "https://www.tradingview.com"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
# (host, path prefix) of every URL a GET may reach: the public idea listings and an idea's detail
READ_URLS: tuple[tuple[str, str], ...] = (("www.tradingview.com", "/api/v1/ideas/"),)


class TvReadError(RuntimeError):
    """A read failed (network, an unexpected answer). The message never carries a header."""


class ReadOnlyViolation(TvReadError):
    """Something tried to send a request outside the read-only allow list. Never retried: it is a code fault."""


class TvNetwork(Protocol):
    """Read-only by construction: a GET, nothing else."""

    def get(self, url: str, headers: Mapping[str, str]) -> str: ...


def _url_allowed(url: str) -> bool:
    """https, an allowed host and path prefix, and nothing that could steer the request elsewhere: no userinfo, no
    explicit port, and no `.`/`..` segment (plain or percent-encoded) that a client or server would resolve out of the
    prefix."""
    u = urlsplit(url)
    try:
        port = u.port
    except ValueError:                       # a malformed port is refused, never guessed
        return False
    if u.scheme != "https" or u.username is not None or u.password is not None or port is not None:
        return False
    if any(unquote(seg) in (".", "..") for seg in u.path.split("/")):
        return False
    return any(u.hostname == host and u.path.startswith(prefix) for host, prefix in READ_URLS)


class ReadOnlyNetwork:
    """Every request the collector makes goes through here: the allow list, the size cap, no cookie."""

    def __init__(self, inner: TvNetwork):
        self._inner = inner

    def get(self, url: str, *, xhr: bool = False) -> str:
        where = f"{urlsplit(url).hostname}{urlsplit(url).path}"
        if not _url_allowed(url):
            raise ReadOnlyViolation(f"refused GET {where}: not a read endpoint")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html", "Origin": SITE,
                   "Referer": SITE + "/"}
        if xhr:
            headers.update({"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
        try:
            body = self._inner.get(url, headers)
        except TvReadError:
            raise
        except Exception as exc:
            raise TvReadError(f"GET {where} failed: {type(exc).__name__}: {exc}"[:300]) from None
        if len(body) > MAX_HTTP_BYTES:
            raise TvReadError(f"GET {where}: a {len(body)}-character body exceeds the {MAX_HTTP_BYTES} cap")
        return body


class Pacer:
    """At most one request every `min_interval_s` (monotonic clock); counts the requests."""

    def __init__(self, min_interval_s: float, monotonic: Callable[[], float], sleep: Callable[[float], None]):
        self.min_interval_s, self._mono, self._sleep = min_interval_s, monotonic, sleep
        self._last: float | None = None
        self.count = 0

    def wait(self) -> None:
        if self._last is not None:
            gap = self.min_interval_s - (self._mono() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._mono()
        self.count += 1


class LiveNetwork:
    """httpx for the GETs. Not exercised by the tests (no live calls in tests); every call is wrapped by
    `ReadOnlyNetwork`. Redirects are not followed."""

    def __init__(self, timeout_s: float):
        self.timeout_s = timeout_s

    def get(self, url: str, headers: Mapping[str, str]) -> str:  # pragma: no cover - network
        import httpx
        with httpx.stream("GET", url, headers=dict(headers), timeout=self.timeout_s, follow_redirects=False) as r:
            r.raise_for_status()
            body = bytearray()
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > MAX_HTTP_BYTES:
                    raise TvReadError(f"a GET body over the {MAX_HTTP_BYTES}-byte cap")
            return body.decode(r.encoding or "utf-8", errors="replace")
