"""MT5 broker bridge: the engine on one machine, the MT5 terminal on another.

The MetaTrader5 package only talks to a terminal on the same (Windows or Wine) machine. Hosting both on Oracle Cloud's
free tier puts the terminal on a small x86 VM under Wine and everything else on a larger ARM VM, so the engine reaches
the terminal through this bridge:

* `serve(broker, host, port, token)` runs next to the terminal (`python -m goldbot.ops.run bridge <account>` under the
  Wine Python). It exposes the `Broker` protocol methods in `METHODS` (and nothing else) as JSON over HTTP POST
  /rpc, one request at a time (the MetaTrader5 package is not thread-safe), each authenticated by a bearer token
  compared in constant time. Bind it to the private network address only.
* `RemoteBroker(url, token)` implements the `Broker` protocol on the engine's side.

Order safety: `place_order`, `modify` and `close` are never retried. A timeout after the bridge sent an order raises
`BridgeUnavailable`, exactly like a terminal that died after `order_send`; the engine's restart reconciliation finds
the fill by its client order id in positions and deals (row X7), so an order is never sent twice. Read-only calls
are retried once. While the bridge is unreachable there are no ticks, so the gate's stale-data rule blocks entries;
open positions keep their broker-side stop and target (sent with every order).

Defence in depth on the terminal's side: requests time out after `SOCKET_TIMEOUT_S` (one stalled connection cannot
hold the single-threaded server); an order call older than `ORDER_MAX_AGE_S` when it reaches the terminal is refused
(the engine has already given up on it, and its price is stale); `GuardedBroker` refuses orders outside the account's
magic range or above `MAX_ORDER_LOTS`, and stop/target changes on positions that are not goldbot's (closes stay allowed: the kill switch closes everything). The bridge listens on
127.0.0.1 only and the engine reaches it through an SSH tunnel (goldbot/ops/linux), so the token never crosses the
network in clear text. On start both ends check the terminal is logged in to the registry's account (login, server,
demo vs real): `accounts.verify_terminal_account`.

No credentials pass through the bridge: the terminal keeps its own saved login, and the URL and token live in the
keyring of each machine (`mt5-bridge-url-<account>`, `mt5-bridge-token-<account>`), never in the repository.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import date
from datetime import time as dtime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, AsyncIterator, Callable

import pandas as pd
import requests
from pydantic import BaseModel

from goldbot.base import load_record
from goldbot.data.calendar import TradingSession
from goldbot.execution.broker import (
    AccountInfo,
    OrderIntent,
    OrderResult,
    Position,
    SymbolInfo,
    Tick,
    tick_key,
)
from goldbot.execution.costs import BrokerTerms

log = logging.getLogger("goldbot.bridge")

# method -> (returns, retry-safe). Orders are never retried (see the module docstring).
METHODS: dict[str, tuple[str, bool]] = {
    "symbol_info": ("SymbolInfo", True),
    "account": ("AccountInfo", True),
    "get_bars": ("frame", True),
    "copy_ticks": ("frame", True),
    "last_tick": ("Tick", True),
    "positions": ("list[Position]", True),
    "account_positions": ("list[Position]", True),   # read-only, every symbol: the equity cross-check (ADR 0016)
    "deals_since": ("frame", True),
    "broker_terms": ("BrokerTerms", True),
    "margin_required": ("float", True),      # read-only (order_calc_margin): retry-safe, nothing reaches the market
    "loss_per_lot": ("float", True),         # read-only (order_calc_profit / tick value): the sizing conversion
    "trading_sessions": ("list[TradingSession]", True),   # read-only session hours (D13)
    "place_order": ("OrderResult", False),
    "modify": ("OrderResult", False),
    "close": ("OrderResult", False),
}
# The wire protocol's version, reported on GET /health with the served METHODS (BACKLOG 42, gap G6): bump it whenever a
# method is added, removed or changes its arguments, result or a wire record's fields. tests/test_bridge_compat.py pins
# (API_VERSION, MIN_COMPATIBLE, protocol_digest()) so any such change without a bump fails CI. MIN_COMPATIBLE is the
# oldest peer version this code still interoperates with, in both directions: the brain refuses entries when its
# API_VERSION is below the bridge's MIN_COMPATIBLE, or the bridge's API_VERSION is below its own MIN_COMPATIBLE, or the
# bridge lacks a method it calls (goldbot/ops/bridge_compat.py). Exits are never gated by it.
# v3 (ADR 0010): the brain reads responses tolerantly (unknown fields dropped); a v2 brain read them strictly. Same wire
# bytes, so MIN_COMPATIBLE stays 2; a future bridge that adds a response field sets MIN_COMPATIBLE = 3.
# v4: AccountInfo.margin_mode (hedging | netting | exchange). A v4 brain on a v3 bridge reads None and refuses to start
# (accounts.verify_terminal_account: a hedging account is required), so the MT5 box is deployed first. MIN_COMPATIBLE
# = 4 (review LOW-A): bridge-compat and preflight then name a v3 bridge as the cause instead of a generic refusal.
# v5 (ADR 0016): `account_positions` (every symbol) and Position.swap, read by the equity cross-check only. A v5
# brain calls a method a v4 bridge lacks, so MIN_COMPATIBLE = 5 names the v4 bridge (entries refused
# bridge_incompatible, exits unaffected): deploy the MT5 box first. A v4 brain drops the unknown swap field.
API_VERSION = 5
MIN_COMPATIBLE = 5
_RECORDS: dict[str, type[BaseModel]] = {"SymbolInfo": SymbolInfo, "AccountInfo": AccountInfo, "Tick": Tick, "OrderResult": OrderResult,
            "Position": Position, "BrokerTerms": BrokerTerms, "OrderIntent": OrderIntent, "TradingSession": TradingSession}
# Any change to these records' fields goes to the trading-safety reviewer (AGENT_STANDARDS S12): responses are read
# tolerantly, so a new field must never carry an outcome or a restriction on its own.
# Records the brain sends as arguments: always decoded strictly, even inside a response (ADR 0010, `_decode`).
_REQUEST_RECORDS = frozenset({"OrderIntent"})
MAX_BODY = 1_000_000
SOCKET_TIMEOUT_S = 5            # a connection that sends nothing for this long is dropped
ORDER_MAX_AGE_S = 10.0          # an order call older than this when the terminal gets to it is refused
MAX_ORDER_LOTS = 3.0            # the design's combined exposure cap; no single order may exceed it


class BridgeUnavailable(ConnectionError):
    """The bridge did not answer (network, timeout, terminal down). The call may or may not have reached the broker."""


class BridgeError(RuntimeError):
    """The bridge answered with an error (bad request, refused, or the terminal raised)."""


# ---------------------------------------------------------------------------------------------- wire format
def _encode(v: Any) -> Any:
    if isinstance(v, pd.DataFrame):
        out = v.copy()
        times = [c for c in out.columns if isinstance(out[c].dtype, pd.DatetimeTZDtype)]
        for c in times:
            out[c] = out[c].map(lambda t: None if pd.isna(t) else t.isoformat())
        return {"__frame__": json.loads(out.to_json(orient="split", index=False, date_format="iso")), "times": times}
    if isinstance(v, pd.Timestamp):
        return {"__ts__": v.isoformat()}
    if isinstance(v, (date, dtime)):                # record fields (TradingSession): pydantic parses the ISO text back
        return v.isoformat()
    if hasattr(v, "model_dump"):                    # python-mode dump: Timestamps stay Timestamps and go as __ts__
        return {"__record__": type(v).__name__, "data": _encode(v.model_dump())}
    if isinstance(v, dict):
        return {k: _encode(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_encode(x) for x in v]
    return v


def _decode(v: Any, *, response: bool) -> Any:
    """Wire JSON -> Python. ADR 0010: "read tolerantly, build strictly", but an order request is never guessed.

    `response=True` (the brain reading what the MT5 box sent back): the box is updated by hand and can run a newer
    release, so its records are read with `load_record` (unknown fields dropped and counted in `schema_skew()`; a
    wrong type or a missing field still raises). `response=False` (the bridge server reading the brain's arguments)
    and request records (`_REQUEST_RECORDS`) in either direction stay strict: an unknown field fails the call."""
    if isinstance(v, dict) and "__frame__" in v:
        f = v["__frame__"]
        df = pd.DataFrame(f["data"], columns=f["columns"])
        for c in v.get("times", []):
            df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
        return df
    if isinstance(v, dict) and "__ts__" in v:
        return pd.Timestamp(v["__ts__"])
    if isinstance(v, dict) and "__record__" in v:
        cls = _RECORDS.get(v["__record__"])
        if cls is None:
            raise BridgeError(f"unknown record type {v['__record__']!r}")
        data = _decode(v["data"], response=response)
        if response and v["__record__"] not in _REQUEST_RECORDS:
            return load_record(cls, data, source="bridge-response")
        return cls.model_validate(data)
    if isinstance(v, dict):
        return {k: _decode(x, response=response) for k, x in v.items()}
    if isinstance(v, list):
        return [_decode(x, response=response) for x in v]
    return v


# ---------------------------------------------------------------------------------------------- server
def make_handler(broker: Any, token: str, *, commit: str | None = None) -> type[BaseHTTPRequestHandler]:
    """`commit`: the commit this bridge process runs (read once at start), reported on /health so the brain can see
    when the two boxes run different code. Nothing on /health is a secret: zone, commit (public repo), protocol."""
    if len(token) < 32:
        raise ValueError("bridge token must be at least 32 characters")

    class Handler(BaseHTTPRequestHandler):
        server_version = "goldbot-bridge"
        timeout = SOCKET_TIMEOUT_S

        def log_message(self, fmt: str, *args: Any) -> None:     # requests go to the module log, not stderr
            log.debug("%s " + fmt, self.address_string(), *args)

        def _reply(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path != "/health":
                self._reply(404, {"error": "not found"})
                return
            body: dict[str, Any] = {"ok": True}
            tz = getattr(broker, "server_tz", None)      # the zone this box converts MT5 stamps with (not a secret)
            if isinstance(tz, str):
                body["server_tz"] = tz
            body["api_version"] = API_VERSION
            body["min_compatible"] = MIN_COMPATIBLE
            body["methods"] = sorted(METHODS)                # exactly what do_POST serves
            if commit is not None:
                body["commit"] = commit
            self._reply(200, body)

        def do_POST(self) -> None:
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
                self._reply(401, {"error": "unauthorised"})
                return
            if self.path != "/rpc":
                self._reply(404, {"error": "not found"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0 or n > MAX_BODY:
                self._reply(400, {"error": "bad body"})
                return
            try:
                req = json.loads(self.rfile.read(n))
                method = req["method"]
                if not isinstance(method, str) or method not in METHODS:
                    self._reply(403, {"error": f"method {method!r} not allowed"})
                    return
                age = time.time() - float(req.get("sent_at", 0.0))
                if not METHODS[method][1] and not -ORDER_MAX_AGE_S < age < ORDER_MAX_AGE_S:
                    self._reply(409, {"error": f"{method} is {age:.1f} s old; refused (stale)"})
                    return
                args = [_decode(a, response=False) for a in req.get("args", [])]
                result = getattr(broker, method)(*args)
            except Exception as exc:                                  # the terminal's error goes back to the caller
                log.exception("bridge call failed")
                self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            self._reply(200, {"result": _encode(result)})

    return Handler


def serve(broker: Any, host: str, port: int, token: str,
          commit: str | None = None) -> None:  # pragma: no cover - blocking loop
    """One request at a time (HTTPServer, not ThreadingHTTPServer): the MetaTrader5 package is not thread-safe."""
    httpd = HTTPServer((host, port), make_handler(broker, token, commit=commit))
    log.info("bridge serving %s on %s:%d", type(broker).__name__, host, port)
    httpd.serve_forever()


class GuardedBroker:
    """The terminal-side broker behind the bridge, refusing what goldbot never sends: orders outside this account's
    magic range (magic_base .. magic_base + 99) or above MAX_ORDER_LOTS, and stop/target changes on positions outside
    that range. Closes are allowed for any position: closing only reduces risk, and the 12% kill switch closes
    everything on the account (design: Drawdown kill switch)."""

    def __init__(self, broker: Any, *, magic_base: int, verify: Callable[[Any], str | None] | None = None) -> None:
        self._b, self.magic_base, self._verify = broker, magic_base, verify

    def _ours(self, magic: int) -> bool:
        return self.magic_base <= int(magic) < self.magic_base + 100

    def _own_position(self, position_id: int) -> None:
        if not any(p.position_id == position_id and self._ours(p.magic) for p in self._b.positions()):
            raise PermissionError(f"position {position_id} is not one of this account's goldbot positions")

    def place_order(self, intent: OrderIntent) -> OrderResult:
        if not self._ours(intent.magic):
            raise PermissionError(f"magic {intent.magic} outside {self.magic_base}..{self.magic_base + 99}")
        if not 0 < intent.lots <= MAX_ORDER_LOTS:
            raise PermissionError(f"{intent.lots} lots outside (0, {MAX_ORDER_LOTS}]")
        why = self._verify(self._b.account()) if self._verify is not None else None
        if why:                                        # the terminal was switched to another account after start
            raise PermissionError(f"terminal account changed: {why}")
        return self._b.place_order(intent)

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        self._own_position(position_id)
        return self._b.modify(position_id, sl, tp)

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        return self._b.close(position_id, lots)

    @property
    def server_tz(self) -> str | None:
        """The wrapped terminal's server_tz (reported on /health so the brain can compare it with its own)."""
        tz = getattr(self._b, "server_tz", None)
        return tz if isinstance(tz, str) else None

    def __getattr__(self, name: str) -> Any:          # read-only calls pass through
        if name in METHODS and METHODS[name][1]:
            return getattr(self._b, name)
        raise AttributeError(name)


def protocol_digest() -> str:
    """sha-256 over what makes the wire protocol: METHODS (name, result, retry-safety), the signature of each method on
    the brain's side (RemoteBroker) and on the terminal's side behind the bridge (MT5Broker, the dispatch target; with
    GuardedBroker's own override where it has one), and the field names of every wire record. Pinned with API_VERSION
    by tests/test_bridge_compat.py."""
    import inspect

    from goldbot.execution.mt5_adapter import (
        MT5Broker,  # imports without the MetaTrader5 package (lazy inside)
    )

    def sig(cls: type, name: str) -> str:
        fn = cls.__dict__.get(name) if cls is GuardedBroker else getattr(cls, name, None)
        return str(inspect.signature(fn)) if fn is not None else "-"
    parts = [[m, ret, retry, sig(RemoteBroker, m), sig(MT5Broker, m), sig(GuardedBroker, m)]
             for m, (ret, retry) in sorted(METHODS.items())]
    records = [[n, sorted(cls.model_fields)] for n, cls in sorted(_RECORDS.items())]
    return hashlib.sha256(json.dumps([parts, records]).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- client
def probe_health(url: str, timeout_s: float = 5.0, get: Callable[..., Any] = requests.get) -> str | None:
    """None when the bridge answers GET /health with {"ok": true}; otherwise why not (no URL or token in the text).
    Used by the brain's health checks; no token is needed and nothing reaches the terminal."""
    try:
        r = get(f"{url.rstrip('/')}/health", timeout=timeout_s)
    except requests.RequestException as exc:
        return f"unreachable ({type(exc).__name__})"
    if r.status_code != 200:
        return f"HTTP {r.status_code}"
    try:
        ok = r.json().get("ok")
    except (ValueError, AttributeError):
        return "answered, but not with the bridge's JSON"
    return None if ok is True else "answered without ok"


def bridge_server_tz(url: str, timeout_s: float = 5.0, get: Callable[..., Any] = requests.get) -> str | None:
    """The server_tz the bridge converts MT5 stamps with (GET /health), or None when it does not say (an older bridge,
    unreachable, junk). The brain compares it with its own accounts.yaml in preflight."""
    try:
        r = get(f"{url.rstrip('/')}/health", timeout=timeout_s)
        tz = r.json().get("server_tz") if r.status_code == 200 else None
    except (requests.RequestException, ValueError, AttributeError):
        return None
    return tz if isinstance(tz, str) else None


class RemoteBroker:
    """The `Broker` protocol over the bridge. Read-only calls retry once; order calls never retry."""

    def __init__(self, url: str, token: str, *, name: str = "mt5-remote", timeout_s: float = 10.0,
                 order_timeout_s: float = 15.0, post: Callable[..., Any] | None = None) -> None:
        self.url, self.name = url.rstrip("/"), name
        self._token, self.timeout_s, self.order_timeout_s = token, timeout_s, order_timeout_s
        self._session = requests.Session()
        self._post = post or self._session.post

    def _call(self, method: str, *args: Any) -> Any:
        _, retry = METHODS[method]
        attempts = 2 if retry else 1
        timeout = self.timeout_s if retry else self.order_timeout_s
        for i in range(attempts):
            body = json.dumps({"method": method, "args": [_encode(a) for a in args], "sent_at": time.time()})
            try:
                r = self._post(f"{self.url}/rpc", data=body, timeout=timeout,
                               headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"})
            except requests.RequestException as exc:
                if i + 1 < attempts:
                    continue
                raise BridgeUnavailable(f"bridge {method}: {type(exc).__name__}") from exc
            if r.status_code != 200:
                try:
                    msg = r.json().get("error", "")
                except ValueError:
                    msg = r.text[:200]
                raise BridgeError(f"bridge {method}: HTTP {r.status_code} {msg}")
            return _decode(r.json()["result"], response=True)
        raise AssertionError("unreachable")

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return self._call("symbol_info", symbol)

    def account(self) -> AccountInfo:
        return self._call("account")

    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
        return self._call("get_bars", symbol, tf, n)

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        return self._call("copy_ticks", symbol, since_utc, n)

    def last_tick(self, symbol: str) -> Tick:
        return self._call("last_tick", symbol)

    async def stream_ticks(self, symbol: str) -> AsyncIterator[Tick]:  # pragma: no cover - the engine polls last_tick
        import asyncio
        last = None
        while True:
            t = self.last_tick(symbol)
            if last is None or tick_key(t) != last:
                last = tick_key(t)
                yield t
            await asyncio.sleep(0.25)

    def margin_required(self, symbol: str, side: int, lots: float, price: float) -> float | None:
        return self._call("margin_required", symbol, side, lots, price)

    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float) -> float | None:
        return self._call("loss_per_lot", symbol, side, price, distance)

    def trading_sessions(self, symbol: str) -> list[TradingSession]:
        return self._call("trading_sessions", symbol)

    def place_order(self, intent: OrderIntent) -> OrderResult:
        return self._call("place_order", intent)

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        return self._call("modify", position_id, sl, tp)

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        return self._call("close", position_id, lots)

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        return self._call("positions", magic_prefix)

    def account_positions(self) -> list[Position]:
        return self._call("account_positions")

    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
        return self._call("deals_since", since_utc)

    def broker_terms(self, account_id: str, since_utc: pd.Timestamp, now: pd.Timestamp) -> BrokerTerms:
        return self._call("broker_terms", account_id, since_utc, now)
