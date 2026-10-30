"""TradingView's chart websocket and page reads, behind an interface (owner decision 2026-10-18, ADR 0030).

The direct pull (goldbot/data/tv_pull.py) talks to TradingView only through `TvNetwork`, so the tests replay recorded
conversations and no test reaches the network. This module holds the three rules that keep the pull read-only and the
session cookie private, enforced here rather than trusted to the caller:

* "Strictly read-only (never create/modify alerts, layouts or anything on the account)". The interface has no POST,
  PUT or DELETE at all; `ReadOnlyNetwork` lets a GET through only to the read endpoints in `READ_URLS`, and a
  websocket frame only when its method is in `READ_METHODS` (sessions, symbol resolution, series, studies: what a
  chart tab sends to draw itself). Anything else raises `ReadOnlyViolation` before a byte leaves.
* "The cookie never reaches chat, logs, the repo, config or exceptions". `redact` replaces every secret the pull holds
  (the cookie, each cookie value, the auth token) in any text that leaves this layer; `ReadOnlyNetwork` re-raises a
  transport error as `TvPullError` with the message redacted and no chained cause.
* The wire format: frames are `~m~<length>~m~<payload>`, several may share one websocket message; a payload `~h~N` is a
  heartbeat the client must echo; anything else is JSON `{"m": method, "p": params}`.

The protocol is TradingView's own and unofficial (no published contract): its frames and endpoints are reproduced
from the community-documented client, not from a TradingView specification, and can change without notice. The
pull fails soft when they do (tv_pull.run_pass): the alerts path is independent of it.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

# `type=chart` is required: without it every create_study is refused ("Study not allowed in this connection").
# A paid plan's history depth is served by `prodata` only (measured 2026-10-18, Premium, 15m: data 5,050 bars,
# prodata 11,080); the free/unauthenticated data host is `data`.
WS_URL = "wss://data.tradingview.com/socket.io/websocket?from=chart%2F&type=chart"
PRO_WS_URL = "wss://prodata.tradingview.com/socket.io/websocket?from=chart%2F&type=chart"
# response caps, about twice the largest seen on a real pass (2026-10-18, Premium, 3 layout studies, 5000-bar pages):
# a websocket message 3.66 MB (a study's history), a GET body 1.31 MB (a chart page)
MAX_WS_BYTES = 8 * 2**20
MAX_HTTP_BYTES = 4 * 2**20
SITE = "https://www.tradingview.com"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"

# (host, path prefix) of every URL the pull may GET: the signed-in home page (auth token), the saved-layout list, a
# layout's chart page (its indicators and their inputs), a layout's read token and saved sources (fallback) and a
# script's compiled source.
READ_URLS: tuple[tuple[str, str], ...] = (
    ("www.tradingview.com", "/"),                                  # exact: the home page only (see _url_allowed)
    ("www.tradingview.com", "/my-charts/"),
    ("www.tradingview.com", "/chart/"),                            # a saved layout's page (initData.content)
    ("www.tradingview.com", "/chart-token/"),
    ("charts-storage.tradingview.com", "/charts-storage/get/layout/"),
    ("pine-facade.tradingview.com", "/pine-facade/translate/"),
    ("www.tradingview.com", "/api/v1/ideas/"),                     # public idea listings (tv_ideas.py, no cookie)
)
# what a chart tab sends to draw itself; nothing that saves, alerts, trades or writes to the account
READ_METHODS = frozenset({
    "set_auth_token", "set_locale", "chart_create_session", "chart_delete_session", "switch_timezone",
    "resolve_symbol", "create_series", "request_more_data", "create_study", "remove_study",
})

_FRAME = re.compile(r"~m~(\d+)~m~")


class TvPullError(RuntimeError):
    """A pull failed (network, protocol, an unexpected answer). The message is already redacted."""


class TvAuthError(TvPullError):
    """TradingView did not accept the session cookie (expired, signed out, or never stored)."""


class ReadOnlyViolation(TvPullError):
    """Something tried to send a request outside the read-only allow lists. Never retried: it is a code fault."""


class TvSocket(Protocol):
    def send(self, text: str) -> None: ...

    def recv(self, timeout: float) -> str:
        """The next websocket message; raises TimeoutError when none arrives within `timeout` seconds."""
        ...

    def close(self) -> None: ...


class TvNetwork(Protocol):
    """Read-only by construction: a GET and a websocket, nothing else."""

    def get(self, url: str, headers: Mapping[str, str]) -> str: ...

    def open_socket(self, url: str, headers: Mapping[str, str]) -> TvSocket: ...


# ------------------------------------------------------------------------------------------------- wire format
def encode(method: str, params: list[Any]) -> str:
    payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    return encode_raw(payload)


def encode_raw(payload: str) -> str:
    return f"~m~{len(payload)}~m~{payload}"


def decode(raw: str) -> list[str]:
    """The payloads of every frame in one websocket message, in order. Text outside a frame is a protocol error."""
    out: list[str] = []
    pos = 0
    while pos < len(raw):
        m = _FRAME.match(raw, pos)
        if m is None:
            raise TvPullError(f"unframed data at offset {pos} of a {len(raw)}-character message")
        start = m.end()
        end = start + int(m.group(1))
        if end > len(raw):
            raise TvPullError(f"truncated frame: {m.group(1)} characters announced, {len(raw) - start} present")
        out.append(raw[start:end])
        pos = end
    return out


def is_heartbeat(payload: str) -> bool:
    return payload.startswith("~h~")


def methods_in(text: str) -> list[str]:
    """The JSON methods of the frames in `text` (heartbeats have none)."""
    found = []
    for p in decode(text):
        if is_heartbeat(p):
            continue
        try:
            msg = json.loads(p)
        except ValueError as exc:
            raise ReadOnlyViolation("an outgoing frame is not JSON") from exc
        found.append(str(msg.get("m")) if isinstance(msg, dict) else "")
    return found


# ------------------------------------------------------------------------------------------------- secrets
def cookie_header(cookie: str) -> str:
    """The Cookie header from what the owner stored: a full `sessionid=...; sessionid_sign=...` or the bare value."""
    c = cookie.strip()
    return c if "=" in c else f"sessionid={c}"


def secret_parts(*secrets: str | None, min_len: int = 8) -> list[str]:
    """Every string worth redacting: each secret whole and each cookie value (at least `min_len` characters)."""
    parts: set[str] = set()
    for s in secrets:
        if not s:
            continue
        parts.add(s.strip())
        for piece in s.split(";"):
            _, _, value = piece.partition("=")
            parts.add(value.strip())
    return sorted((p for p in parts if len(p) >= min_len), key=len, reverse=True)


def redact(text: str, secrets: Iterable[str]) -> str:
    for s in secrets:
        text = text.replace(s, "[redacted]")
    return text


# ------------------------------------------------------------------------------------------------- the guard
def _url_allowed(url: str) -> bool:
    u = urlsplit(url)
    if u.scheme != "https":
        return False
    for host, prefix in READ_URLS:
        if u.hostname != host:
            continue
        if prefix == "/" and u.path in ("", "/") and not u.query:
            return True
        if prefix != "/" and u.path.startswith(prefix):
            return True
    return False


class GuardedSocket:
    """A socket that sends only read-only frames and redacts every error it raises."""

    def __init__(self, inner: TvSocket, secrets: list[str]):
        self._inner, self._secrets = inner, secrets

    def send(self, text: str) -> None:
        bad = [m for m in methods_in(text) if m not in READ_METHODS]
        if bad:
            raise ReadOnlyViolation(f"refused to send {', '.join(bad)}: not a read-only chart method")
        try:
            self._inner.send(text)
        except Exception as exc:
            raise TvPullError(redact(f"websocket send failed: {type(exc).__name__}: {exc}", self._secrets)) from None

    def recv(self, timeout: float) -> str:
        try:
            msg = self._inner.recv(timeout)
        except TimeoutError:
            raise
        except Exception as exc:
            raise TvPullError(redact(f"websocket receive failed: {type(exc).__name__}: {exc}", self._secrets)) from None
        if len(msg) > MAX_WS_BYTES:
            raise TvPullError(f"a websocket message of {len(msg)} characters exceeds the {MAX_WS_BYTES} cap")
        return msg

    def close(self) -> None:
        try:
            self._inner.close()
        except Exception:      # closing a dead socket must never replace the pull's own result
            pass


class ReadOnlyNetwork:
    """Every request the pull makes goes through here: the allow lists, the cookie header and redaction."""

    def __init__(self, inner: TvNetwork, cookie: str, secrets: list[str]):
        self._inner, self._cookie, self.secrets = inner, cookie_header(cookie), secrets

    def add_secret(self, value: str, *, min_len: int = 8) -> None:
        """Redact `value` from now on (in place: the sockets share the list); `min_len` 1 for an account id."""
        self.secrets[:] = sorted(set(self.secrets) | set(secret_parts(value, min_len=min_len)), key=len, reverse=True)

    def get(self, url: str, *, with_cookie: bool = True, xhr: bool = False) -> str:
        if not _url_allowed(url):
            raise ReadOnlyViolation(f"refused GET {urlsplit(url).hostname}{urlsplit(url).path}: not a read endpoint")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html", "Origin": SITE,
                   "Referer": SITE + "/"}
        if xhr:
            headers.update({"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
        if with_cookie:
            headers["Cookie"] = self._cookie
        try:
            body = self._inner.get(url, headers)
        except TvPullError:
            raise
        except Exception as exc:
            msg = f"GET {urlsplit(url).hostname}{urlsplit(url).path} failed: {type(exc).__name__}: {exc}"
            raise TvPullError(redact(msg, self.secrets)[:300]) from None
        if len(body) > MAX_HTTP_BYTES:
            raise TvPullError(redact(f"GET {urlsplit(url).hostname}{urlsplit(url).path}: a {len(body)}-character body "
                                     f"exceeds the {MAX_HTTP_BYTES} cap", self.secrets))
        return body

    def open_socket(self, *, pro: bool = False) -> GuardedSocket:
        try:
            inner = self._inner.open_socket(PRO_WS_URL if pro else WS_URL, {"Origin": SITE, "User-Agent": USER_AGENT})
        except Exception as exc:
            raise TvPullError(redact(f"websocket connect failed: {type(exc).__name__}: {exc}", self.secrets)[:300]) \
                from None
        return GuardedSocket(inner, self.secrets)


# ------------------------------------------------------------------------------------------------- the real network
class LiveNetwork:
    """httpx for the GETs, `websockets` (sync client) for the chart socket. Not exercised by the tests (no live calls
    in tests); every call is wrapped by `ReadOnlyNetwork`. Redirects are not followed: a sign-in redirect means the
    cookie was refused and shows as a page without an auth token."""

    def __init__(self, timeout_s: float):
        self.timeout_s = timeout_s

    def get(self, url: str, headers: Mapping[str, str]) -> str:  # pragma: no cover - network
        import httpx
        with httpx.stream("GET", url, headers=dict(headers), timeout=self.timeout_s, follow_redirects=False) as r:
            if r.status_code in (401, 403):
                raise TvAuthError(f"TradingView answered {r.status_code}: the session cookie was refused")
            r.raise_for_status()
            body = bytearray()
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > MAX_HTTP_BYTES:
                    raise TvPullError(f"a GET body over the {MAX_HTTP_BYTES}-byte cap")
            return body.decode(r.encoding or "utf-8", errors="replace")

    def open_socket(self, url: str, headers: Mapping[str, str]) -> TvSocket:  # pragma: no cover - network
        from websockets.sync.client import connect
        ws = connect(url, additional_headers=dict(headers), open_timeout=self.timeout_s, max_size=MAX_WS_BYTES)
        return _WsSocket(ws)


class _WsSocket:  # pragma: no cover - network
    def __init__(self, ws: Any):
        self._ws = ws

    def send(self, text: str) -> None:
        self._ws.send(text)

    def recv(self, timeout: float) -> str:
        msg = self._ws.recv(timeout=timeout)
        return msg if isinstance(msg, str) else msg.decode("utf-8")

    def close(self) -> None:
        self._ws.close()
