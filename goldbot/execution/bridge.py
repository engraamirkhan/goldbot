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

No credentials pass through the bridge: the terminal keeps its own saved login, and the URL and token live in the
keyring of each machine (`mt5-bridge-url-<account>`, `mt5-bridge-token-<account>`), never in the repository.
"""
from __future__ import annotations

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, AsyncIterator, Callable

import pandas as pd
import requests
from pydantic import BaseModel

from goldbot.execution.broker import AccountInfo, OrderIntent, OrderResult, Position, SymbolInfo, Tick
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
    "deals_since": ("frame", True),
    "broker_terms": ("BrokerTerms", True),
    "place_order": ("OrderResult", False),
    "modify": ("OrderResult", False),
    "close": ("OrderResult", False),
}
_RECORDS: dict[str, type[BaseModel]] = {"SymbolInfo": SymbolInfo, "AccountInfo": AccountInfo, "Tick": Tick, "OrderResult": OrderResult,
            "Position": Position, "BrokerTerms": BrokerTerms, "OrderIntent": OrderIntent}
MAX_BODY = 1_000_000


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
    if hasattr(v, "model_dump"):                    # python-mode dump: Timestamps stay Timestamps and go as __ts__
        return {"__record__": type(v).__name__, "data": _encode(v.model_dump())}
    if isinstance(v, dict):
        return {k: _encode(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_encode(x) for x in v]
    return v


def _decode(v: Any) -> Any:
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
        return cls.model_validate(_decode(v["data"]))
    if isinstance(v, dict):
        return {k: _decode(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_decode(x) for x in v]
    return v


# ---------------------------------------------------------------------------------------------- server
def make_handler(broker: Any, token: str) -> type[BaseHTTPRequestHandler]:
    if len(token) < 32:
        raise ValueError("bridge token must be at least 32 characters")

    class Handler(BaseHTTPRequestHandler):
        server_version = "goldbot-bridge"

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
            self._reply(200, {"ok": True}) if self.path == "/health" else self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
                self._reply(401, {"error": "unauthorised"})
                return
            if self.path != "/rpc":
                self._reply(404, {"error": "not found"})
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                self._reply(400, {"error": "bad body"})
                return
            try:
                req = json.loads(self.rfile.read(n))
                method = req["method"]
                if method not in METHODS:
                    self._reply(403, {"error": f"method {method!r} not allowed"})
                    return
                args = [_decode(a) for a in req.get("args", [])]
                result = getattr(broker, method)(*args)
            except Exception as exc:                                  # the terminal's error goes back to the caller
                log.exception("bridge call failed")
                self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            self._reply(200, {"result": _encode(result)})

    return Handler


def serve(broker: Any, host: str, port: int, token: str) -> None:  # pragma: no cover - blocking loop
    """One request at a time (HTTPServer, not ThreadingHTTPServer): the MetaTrader5 package is not thread-safe."""
    httpd = HTTPServer((host, port), make_handler(broker, token))
    log.info("bridge serving %s on %s:%d", type(broker).__name__, host, port)
    httpd.serve_forever()


# ---------------------------------------------------------------------------------------------- client
class RemoteBroker:
    """The `Broker` protocol over the bridge. Read-only calls retry once; order calls never retry."""

    def __init__(self, url: str, token: str, *, name: str = "mt5-remote", timeout_s: float = 10.0,
                 order_timeout_s: float = 20.0, post: Callable[..., Any] | None = None) -> None:
        self.url, self.name = url.rstrip("/"), name
        self._token, self.timeout_s, self.order_timeout_s = token, timeout_s, order_timeout_s
        self._session = requests.Session()
        self._post = post or self._session.post

    def _call(self, method: str, *args: Any) -> Any:
        _, retry = METHODS[method]
        attempts = 2 if retry else 1
        body = json.dumps({"method": method, "args": [_encode(a) for a in args]})
        timeout = self.timeout_s if retry else self.order_timeout_s
        for i in range(attempts):
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
            return _decode(r.json()["result"])
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
            if last is None or (t.ts_utc, t.bid, t.ask) != last:
                last = (t.ts_utc, t.bid, t.ask)
                yield t
            await asyncio.sleep(0.25)

    def place_order(self, intent: OrderIntent) -> OrderResult:
        return self._call("place_order", intent)

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        return self._call("modify", position_id, sl, tp)

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        return self._call("close", position_id, lots)

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        return self._call("positions", magic_prefix)

    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
        return self._call("deals_since", since_utc)

    def broker_terms(self, account_id: str, since_utc: pd.Timestamp, now: pd.Timestamp) -> BrokerTerms:
        return self._call("broker_terms", account_id, since_utc, now)
