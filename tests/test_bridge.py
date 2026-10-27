"""MT5 broker bridge (goldbot/execution/bridge.py): the engine on one machine, the terminal on another (Oracle free
tier: MT5 under Wine). A real HTTP bridge on localhost serves the paper broker; RemoteBroker must behave like the
broker itself, authenticate every call, expose only the Broker methods and never retry an order."""
import json
import socket
import threading
import time
from http.server import HTTPServer
from typing import Any, Iterator

import pandas as pd
import pytest
import requests
from pydantic import ValidationError

from goldbot.base import schema_skew
from goldbot.engine import Engine, EngineConfig
from goldbot.execution import bridge as bridge_mod
from goldbot.execution.bridge import BridgeError, BridgeUnavailable, GuardedBroker, RemoteBroker, make_handler
from goldbot.execution.broker import AccountInfo, OrderIntent, OrderResult, Position, SymbolInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.risk.gate import RiskLimits
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter, Proposal

TOKEN = "t" * 40
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")


@pytest.fixture
def bridge() -> Iterator[tuple[str, PaperBroker]]:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(pb, TOKEN))
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", pb
    httpd.shutdown()
    httpd.server_close()


def _intent(cid: str = "icm-demo-1-a") -> OrderIntent:
    return OrderIntent(client_order_id=cid, symbol="XAUUSD", side=1, lots=0.1, sl=2396.0, tp=2406.0, magic=260101)


def test_remote_calls_return_what_the_broker_returns(bridge):
    url, pb = bridge
    rb = RemoteBroker(url, TOKEN)
    assert rb.last_tick("XAUUSD") == pb.last_tick("XAUUSD")
    assert rb.last_tick("XAUUSD").ts_utc.tzinfo is not None
    assert rb.account() == pb.account() and rb.symbol_info("XAUUSD") == pb.symbol_info("XAUUSD")
    res = rb.place_order(_intent())
    assert res.ok and res.position_id is not None
    [pos] = rb.positions()
    assert pos == pb.positions()[0] and pos.open_time_utc.tzinfo is not None
    assert rb.modify(pos.position_id, 2397.0, None).ok and rb.positions()[0].sl == 2397.0
    assert rb.close(pos.position_id).ok and rb.positions() == []
    deals = rb.deals_since(T0 - pd.Timedelta(days=1))
    local = pb.deals_since(T0 - pd.Timedelta(days=1))
    assert list(deals.columns) == list(local.columns) and len(deals) == len(local)
    if "ts_utc" in local.columns and len(local):
        assert deals["ts_utc"].tolist() == local["ts_utc"].tolist()


def test_every_call_needs_the_token_and_only_broker_methods_are_served(bridge):
    url, pb = bridge
    with pytest.raises(BridgeError, match="401"):
        RemoteBroker(url, "x" * 40).last_tick("XAUUSD")
    r = requests.post(f"{url}/rpc", json={"method": "on_tick", "args": []},
                      headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5)
    assert r.status_code == 403                                  # not in the allow-list, although PaperBroker has it
    r = requests.post(f"{url}/rpc", json={"method": "__init__", "args": []},
                      headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5)
    assert r.status_code == 403
    # no server_tz on the paper broker, no commit given: only the protocol (BACKLOG 42), no account data
    assert requests.get(f"{url}/health", timeout=5).json() == {"ok": True, "api_version": bridge_mod.API_VERSION,
                                                               "min_compatible": bridge_mod.MIN_COMPATIBLE,
                                                               "methods": sorted(bridge_mod.METHODS),
                                                               "order_lane": "off"}
    with pytest.raises(ValueError):
        make_handler(pb, "short")                                # weak tokens refused


def test_a_broker_error_comes_back_as_a_bridge_error(bridge):
    url, _ = bridge
    with pytest.raises(BridgeError, match="500"):
        RemoteBroker(url, TOKEN).broker_terms("icm-demo", T0, T0)     # the paper broker has no broker_terms


class _Flaky:
    """A transport that fails the first `fail` calls, counting attempts per method."""

    def __init__(self, fail: int) -> None:
        self.fail, self.calls = fail, 0

    def __call__(self, url: str, data: str, **kw: Any) -> Any:
        self.calls += 1
        if self.calls <= self.fail:
            raise requests.ConnectionError("reset")
        resp = requests.Response()
        resp.status_code = 200
        resp._content = b'{"result": {"__record__": "Tick", "data": {"ts_utc": {"__ts__": "2025-03-05T10:00:00+00:00"}, "bid": 1, "ask": 2}}}'
        return resp


def test_reads_retry_once_but_orders_are_never_retried():
    read = _Flaky(fail=1)
    assert RemoteBroker("http://bridge", TOKEN, post=read).last_tick("XAUUSD").bid == 1 and read.calls == 2
    order = _Flaky(fail=1)
    with pytest.raises(BridgeUnavailable):
        RemoteBroker("http://bridge", TOKEN, post=order).place_order(_intent())
    assert order.calls == 1                      # a lost reply may mean a sent order: restart reconciliation decides
    for method, args in (("close", (1,)), ("modify", (1, 2390.0, None))):
        once = _Flaky(fail=1)
        with pytest.raises(BridgeUnavailable):
            getattr(RemoteBroker("http://bridge", TOKEN, post=once), method)(*args)
        assert once.calls == 1


def test_the_engine_trades_through_the_bridge_and_survives_a_restart(bridge, tmp_path):
    url, pb = bridge
    rb = RemoteBroker(url, TOKEN)
    cfg: dict[str, Any] = dict(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=1)
    eng = Engine(EngineConfig(**cfg), rb, [], {}, ApprovalCenter({1}))
    agent = SPECIALISTS["session_open"]()
    prop = Proposal(proposal_id="icm-demo-1-a", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2400.2, stop=2396.2, target=2406.2, p=0.62, ev_r=0.2, spread_points=20, top_features=[])
    eng._execute(prop, agent, 0.1, 2396.0, 2406.0, requested=2400.2)
    [pos] = pb.positions()
    again = Engine(EngineConfig(**cfg), RemoteBroker(url, TOKEN), [], {}, ApprovalCenter({1}))
    assert again.open[pos.position_id].agent_id == agent.agent_id      # adopted after the restart, not an orphan
    again._execute(prop, agent, 0.1, 2396.0, 2406.0, requested=2400.2)
    assert len(pb.positions()) == 1                                    # never sent twice


def test_the_bridge_endpoint_comes_from_the_keyring(tmp_path, monkeypatch):
    monkeypatch.setattr(acc_mod, "ROOT", tmp_path)
    monkeypatch.setattr(acc_mod, "keyring", None)
    monkeypatch.setattr(acc_mod, "_PLATFORM", "linux")                # the file stands in only on headless Linux, opted in
    monkeypatch.setenv("GOLDBOT_ALLOW_PLAINTEXT", "1")
    assert acc_mod.bridge_endpoint("icm-demo") is None                # terminal on this machine
    acc_mod.set_secret("mt5-bridge-url-icm-demo", "http://10.0.0.5:8765")
    assert acc_mod.bridge_endpoint("icm-demo") is None                # no token yet: not used
    acc_mod.set_secret(acc_mod.bridge_token_key("icm-demo"), TOKEN)
    assert acc_mod.bridge_endpoint("icm-demo") == ("http://10.0.0.5:8765", TOKEN)


# ---------------------------------------------------------------------------------------------- review fixes
def _acc(**kw: Any) -> acc_mod.Account:
    base: dict[str, Any] = dict(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=1234567,
                                terminal_path="", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)
    base.update(kw)
    return acc_mod.Account(**base)


def _info(**kw: Any) -> AccountInfo:
    base: dict[str, Any] = dict(login=1234567, equity=1e4, balance=1e4, margin=0, margin_free=1e4, leverage=20,
                                currency="USD", server="ICMarketsSC-Demo", trade_mode="demo", margin_mode="hedging")
    base.update(kw)
    return AccountInfo(**base)


def _why(acc: acc_mod.Account, info: AccountInfo) -> str:
    return acc_mod.verify_terminal_account(acc, info) or ""


def test_the_terminal_must_be_logged_in_to_the_registry_account():
    assert acc_mod.verify_terminal_account(_acc(), _info()) is None
    assert "another account" in _why(_acc(), _info(login=7654321))
    assert "server" in _why(_acc(), _info(server="ICMarketsSC-Live"))
    # a live account in the terminal cannot be traded as the demo account (it would skip the phase gate)
    assert "real" in _why(_acc(), _info(trade_mode="real"))
    assert "unknown" in _why(_acc(), _info(trade_mode=None))
    assert "no login" in _why(_acc(login=None), _info())
    assert acc_mod.verify_terminal_account(_acc(mode="live", server="ICMarketsSC-Live"),
                                           _info(server="ICMarketsSC-Live", trade_mode="real")) is None


def test_the_terminal_account_must_be_hedging():
    # review LOW (MEDIUM-1 follow-up): the engine keys one position per order ticket; on a netting or exchange account
    # fills join one position per symbol, so the engine refuses to start (and to send orders) unless it is hedging.
    # Unknown (an older bridge that does not report it) refuses too: update the bridge first
    assert acc_mod.verify_terminal_account(_acc(), _info()) is None
    for mode in ("netting", "exchange", None):
        why = _why(_acc(), _info(margin_mode=mode))
        assert "hedging" in why and str(mode or "unknown") in why, why


@pytest.fixture
def guarded() -> Iterator[tuple[str, PaperBroker]]:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(GuardedBroker(pb, magic_base=260100), TOKEN))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", pb
    httpd.shutdown()
    httpd.server_close()


def test_the_terminal_side_refuses_orders_goldbot_never_sends(guarded):
    url, pb = guarded
    rb = RemoteBroker(url, TOKEN)
    with pytest.raises(BridgeError, match="magic"):
        rb.place_order(_intent().model_copy(update={"magic": 999}))
    with pytest.raises(BridgeError, match="lots"):
        rb.place_order(_intent().model_copy(update={"lots": 3.5}))
    assert pb.positions() == []
    foreign = pb.place_order(_intent("manual").model_copy(update={"magic": 1}))     # the owner's own trade
    with pytest.raises(BridgeError, match="not one of"):
        rb.modify(int(foreign.position_id or 0), None, None)                   # nobody removes the stop of a foreign trade
    assert rb.close(int(foreign.position_id or 0)).ok                          # closing is allowed (kill switch: everything)
    ours = rb.place_order(_intent())
    assert ours.ok and rb.modify(int(ours.position_id or 0), 2397.0, None).ok
    assert rb.account() == pb.account()                              # reads pass through


def test_a_stale_order_is_refused_but_a_stale_read_is_served(bridge):
    url, pb = bridge
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    order = {"method": "place_order", "args": [bridge_mod._encode(_intent())], "sent_at": time.time() - 30}
    r = requests.post(f"{url}/rpc", json=order, headers=hdr, timeout=5)
    assert r.status_code == 409 and pb.positions() == []            # the engine gave up on it long ago
    r = requests.post(f"{url}/rpc", json={"method": "place_order", "args": order["args"]}, headers=hdr, timeout=5)
    assert r.status_code == 409                                      # no timestamp: refused too
    read = {"method": "last_tick", "args": ["XAUUSD"], "sent_at": time.time() - 30}
    assert requests.post(f"{url}/rpc", json=read, headers=hdr, timeout=5).status_code == 200


def test_an_idle_connection_does_not_block_the_bridge(monkeypatch):
    monkeypatch.setattr(bridge_mod, "SOCKET_TIMEOUT_S", 0.5)
    pb = PaperBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(pb, TOKEN))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        addr = ("127.0.0.1", int(httpd.server_address[1]))
        idle = socket.create_connection(addr)
        idle.sendall(b"POST /rpc HTTP/1.1\r\nHost: x\r\n")                # never finishes its headers
        r = requests.get(f"http://127.0.0.1:{httpd.server_address[1]}/health", timeout=5)
        assert r.status_code == 200
        idle.close()
        raw = socket.create_connection(addr)
        raw.sendall(f"POST /rpc HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\nContent-Length: abc\r\n\r\n".encode())
        assert raw.recv(64).startswith(b"HTTP/1.0 400")                      # a malformed length is a clean 400
        raw.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_an_order_is_refused_if_the_terminal_switched_account_after_start():
    pb = PaperBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    state: dict[str, str | None] = {"why": None}
    g = GuardedBroker(pb, magic_base=260100, verify=lambda info: state["why"])
    assert g.place_order(_intent()).ok
    state["why"] = "terminal is logged in to another account than icm-demo"
    with pytest.raises(PermissionError, match="account changed"):
        g.place_order(_intent("icm-demo-2-b"))
    assert len(pb.positions()) == 1


@pytest.mark.parametrize("trade_mode, allowed", [("demo", True), ("real", False), ("contest", False), (None, False)])
def test_guarded_broker_refuses_smoke_magic_unless_demo(trade_mode, allowed):
    """ADR 0021 F4: magic_base + 99 (the bridge smoke's) is refused unless the terminal's account reports demo; the
    engine's own magics are unaffected, and the protocol (METHODS, signatures, API_VERSION, digest) is unchanged."""
    from goldbot.execution.broker import SMOKE_MAGIC_OFFSET

    class Terminal(PaperBroker):
        def account(self) -> AccountInfo:
            return super().account().model_copy(update={"trade_mode": trade_mode})
    pb = Terminal(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    g = GuardedBroker(pb, magic_base=260100)
    smoke = _intent("icm-demo-1-smoke").model_copy(update={"magic": 260100 + SMOKE_MAGIC_OFFSET, "lots": 0.01})
    if allowed:
        assert g.place_order(smoke).ok
    else:
        with pytest.raises(PermissionError, match="smoke magic"):
            g.place_order(smoke)
        assert pb.positions() == []
    assert g.place_order(_intent()).ok                         # an engine magic: not this fence's business
    assert SMOKE_MAGIC_OFFSET == 99


@pytest.mark.parametrize("unreadable", ["raises", "none"])
def test_smoke_magic_with_an_unreadable_account_is_a_definite_reject_and_places_nothing(unreadable):
    """ADR 0021 F4 behind the one account() read (order of the checks: account -> F4): when the read raises or MT5's
    account_info() returns None, the smoke's order is refused with retcode 10006 before F4 is consulted, and nothing
    reaches the terminal."""
    from goldbot.execution.bridge import REFUSED_RETCODE
    from goldbot.execution.broker import SMOKE_MAGIC_OFFSET

    class Terminal(PaperBroker):
        def account(self) -> AccountInfo:
            if unreadable == "raises":
                raise RuntimeError("terminal not connected")
            return None  # type: ignore[return-value]
    pb = Terminal(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    g = GuardedBroker(pb, magic_base=260100)
    smoke = _intent("icm-demo-1-smoke").model_copy(update={"magic": 260100 + SMOKE_MAGIC_OFFSET, "lots": 0.01})
    res = g.place_order(smoke)
    assert not res.ok and res.retcode == REFUSED_RETCODE == 10006
    assert "unreadable:account" in res.message
    assert pb.positions() == []


def test_the_health_probe_answers_through_the_bridge(bridge):
    url, _ = bridge
    assert bridge_mod.probe_health(url) is None                     # no token needed, nothing reaches the terminal
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{s.getsockname()[1]}"
    why = bridge_mod.probe_health(dead, timeout_s=1.0)
    assert why is not None and "unreachable" in why and "127.0.0.1" not in why
    assert bridge_mod.probe_health(url + "/nope") == "HTTP 404"


def test_trading_sessions_cross_the_bridge_read_only(bridge):
    url, pb = bridge
    got = RemoteBroker(url, TOKEN).trading_sessions("XAUUSD")
    assert got == pb.trading_sessions("XAUUSD") and got                  # TRACEABILITY D13
    assert bridge_mod.METHODS["trading_sessions"][1] is True             # retry-safe: nothing reaches the market


# ---------------------------------------------------------------------------------------------- ADR 0010 on the wire
class _Reply:
    """A transport that answers every call with `result` (the bridge's wire JSON), as a newer MT5 box would."""

    def __init__(self, result: Any) -> None:
        self.result = result

    def __call__(self, url: str, data: str, **kw: Any) -> Any:
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json.dumps({"result": self.result}).encode()
        return resp


_TS = {"__ts__": "2025-03-05T10:00:00+00:00"}


def _position_wire(**extra: Any) -> dict[str, Any]:
    data = {"position_id": 7, "symbol": "XAUUSD", "side": 1, "lots": 0.1, "open_price": 2400.2, "sl": 2396.0,
            "tp": 2406.0, "magic": 260101, "comment": "c", "open_time_utc": _TS, "profit": 1.5}
    return {"__record__": "Position", "data": {**data, **extra}}


def test_a_response_from_a_newer_mt5_box_with_an_unknown_field_decodes_on_the_brain():
    """ADR 0010: the MT5 box is updated by hand and can run a newer release, so its responses are read tolerantly."""
    rb = RemoteBroker("http://bridge", TOKEN, post=_Reply([_position_wire(swap_fee=0.3)]))
    [pos] = rb.positions()
    assert pos.position_id == 7 and pos.open_time_utc == pd.Timestamp(_TS["__ts__"])
    assert "swap_fee" not in pos.model_dump()                               # dropped, not kept
    assert schema_skew()[("bridge-response", "Position", ("swap_fee",))] >= 1
    tick = {"__record__": "Tick", "data": {"ts_utc": _TS, "bid": 1, "ask": 2, "flags": 6}}
    assert RemoteBroker("http://bridge", TOKEN, post=_Reply(tick)).last_tick("XAUUSD").bid == 1
    res = {"__record__": "OrderResult", "data": {"ok": True, "retcode": 10009, "order_id": 9, "position_id": 7,
                                                 "filled_lots": 0.1, "price": 2400.2, "queue_ms": 3}}
    assert RemoteBroker("http://bridge", TOKEN, post=_Reply(res)).place_order(_intent()).ok


def test_a_wrong_type_or_a_missing_field_in_a_response_still_raises():
    """Only unknown keys are tolerated (ADR 0010): a response the brain cannot trust is never guessed."""
    with pytest.raises(ValidationError):
        RemoteBroker("http://bridge", TOKEN, post=_Reply([_position_wire(lots="a lot")])).positions()
    broken = _position_wire()
    del broken["data"]["sl"]
    with pytest.raises(ValidationError):
        RemoteBroker("http://bridge", TOKEN, post=_Reply([broken])).positions()


def test_an_order_request_record_in_a_response_is_still_decoded_strictly():
    """An OrderIntent is a request record: it is never read tolerantly, whichever way it crosses the bridge."""
    wire = bridge_mod._encode(_intent())
    wire["data"]["max_slippage"] = 5
    with pytest.raises(ValidationError, match="max_slippage"):
        RemoteBroker("http://bridge", TOKEN, post=_Reply(wire)).positions()


def test_the_bridge_server_refuses_an_order_request_with_an_unknown_field(bridge):
    """Order requests stay strict on the terminal's side: a field this release does not know is never ignored."""
    url, pb = bridge
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    wire = bridge_mod._encode(_intent())
    wire["data"]["max_slippage"] = 5
    r = requests.post(f"{url}/rpc", json={"method": "place_order", "args": [wire], "sent_at": time.time()},
                      headers=hdr, timeout=5)
    assert r.status_code == 500 and "max_slippage" in r.json()["error"] and pb.positions() == []
    plain = {"method": "place_order", "args": [bridge_mod._encode(_intent())], "sent_at": time.time()}
    r = requests.post(f"{url}/rpc", json=plain, headers=hdr, timeout=5)
    assert r.status_code == 200 and len(pb.positions()) == 1           # the same request without it is served


# ---------------------------------------------------------------------------------------------- BACKLOG 66: notional
# DESIGN.md:112: combined exposure at most 30% of equity notional at 1:20, i.e. 6x equity. The bridge enforces it on
# this terminal's own readings (equity, contract, quote, conversion, open positions), so a brain that sizes wrongly on
# a 1:5000 demo terminal cannot open more than the gate would allow. Every refusal is a definite reject (10006).
class _Terminal:
    """Every figure the ceiling reads, settable per test; `bad[name]` replaces one with a value or an exception. The
    inner send is a spy: `sent` lists every intent that reached it, `reads` every figure read."""

    def __init__(self, *, equity: float = 1_000.0, currency: str = "USD", contract: float = 100.0, bid: float = 2500.0,
                 ask: float = 2500.0, fx: float | None = 1.0, positions: list[Position] | None = None,
                 bad: dict[str, Any] | None = None) -> None:
        self.equity, self.currency, self.contract, self.bid, self.ask, self.fx = equity, currency, contract, bid, ask, fx
        self.open, self.bad = list(positions or []), dict(bad or {})
        self.sent: list[OrderIntent] = []
        self.reads: list[str] = []

    def _v(self, name: str, value: Any) -> Any:
        self.reads.append(name)
        v = self.bad.get(name, value)
        if isinstance(v, Exception):
            raise v
        return v

    def account(self) -> AccountInfo:
        return AccountInfo(login=0, equity=self._v("equity", self.equity), balance=self.equity, margin=0.0,
                           margin_free=self.equity, leverage=5000, currency=self._v("currency", self.currency),
                           server="demo", trade_mode="demo", margin_mode="hedging")

    def symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(name=symbol, digits=2, point=0.01, contract_size=self._v("contract", self.contract),
                          volume_min=0.01, volume_step=0.01, volume_max=50.0, stops_level_points=0.0,
                          filling_modes=["IOC"], trade_allowed=True)

    def last_tick(self, symbol: str) -> Tick:
        bid, ask = self._v("price", self.bid), self._v("price", self.ask)
        return Tick(ts_utc=T0, bid=bid, ask=ask)

    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float) -> float | None:
        per_lot = None if self.fx is None else self.fx * self.contract * distance
        return self._v("fx", per_lot)

    def account_positions(self) -> list[Position]:
        return self._v("positions", list(self.open))

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        return list(self.open)

    def place_order(self, intent: OrderIntent) -> OrderResult:
        self.sent.append(intent)
        return OrderResult(ok=True, retcode=10009, order_id=1, position_id=1, filled_lots=intent.lots, price=2500.0,
                           message="filled")

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        return OrderResult(ok=True, retcode=10009, order_id=None, position_id=position_id, filled_lots=0.0, price=None,
                           message="modified")

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        return OrderResult(ok=True, retcode=10009, order_id=None, position_id=position_id, filled_lots=lots or 0.0,
                           price=2500.0, message="closed")


def _pos(lots: float, *, side: int = 1, magic: int = 260101, symbol: str = "XAUUSD", pid: int = 7) -> Position:
    return Position(position_id=pid, symbol=symbol, side=side, lots=lots, open_price=2500.0, sl=None, tp=None,
                    magic=magic, comment="", open_time_utc=T0, profit=0.0)


def _order(lots: float, side: int = 1) -> OrderIntent:
    return OrderIntent(client_order_id="icm-demo-66-a", symbol="XAUUSD", side=side, lots=lots, sl=2400.0, tp=2600.0,
                       magic=260101)


def _refused(res: OrderResult, t: _Terminal) -> bool:
    """A ceiling refusal: a definite reject that names itself, and nothing reached the terminal's send."""
    return (not res.ok and res.retcode == 10006 and res.order_id is None and res.position_id is None
            and res.filled_lots == 0 and res.message.startswith("bridge_notional_ceiling") and t.sent == [])


def test_the_bridge_multiple_is_the_gates_combined_notional_fraction_at_the_leverage_cap():
    limits = RiskLimits()
    assert bridge_mod.MAX_NOTIONAL_EQUITY_MULT == limits.max_combined_notional_frac * limits.leverage_cap == 6.0


def test_at_1000_equity_one_hundredth_lot_passes_and_two_hundredths_is_refused():
    # at 2500 USD/oz: 0.01 lot = 2,500 = 2.5x of 1,000 (passes); 0.03 lot = 7.5x (refused). The plan's title names
    # 0.02, but at today's price 0.02 lot is 5x and fits: the boundary sits between 0.02 and 0.03 lot (0.024)
    t = _Terminal(equity=1_000.0)
    assert GuardedBroker(t, magic_base=260100).place_order(_order(0.01)).ok
    t2 = _Terminal(equity=1_000.0)
    assert GuardedBroker(t2, magic_base=260100).place_order(_order(0.02)).ok
    t3 = _Terminal(equity=1_000.0)
    assert _refused(GuardedBroker(t3, magic_base=260100).place_order(_order(0.03)), t3)
    t4 = _Terminal(equity=500.0)                    # 0.02 lot = 10x of 500
    res = GuardedBroker(t4, magic_base=260100).place_order(_order(0.02))
    assert _refused(res, t4) and "6 x equity" in res.message


def test_notional_exactly_six_times_equity_passes_and_one_cent_over_is_refused():
    # float-exact: 0.04 x 100 x 2500 x 0.6 == 6000.0 == 6 x 1000 (strict >, as the gate)
    assert 0.04 * 100.0 * 2500.0 * 0.6 == 6.0 * 1_000.0
    t = _Terminal(equity=1_000.0, currency="GBP", contract=100.0, bid=2500.0, ask=2500.0, fx=0.6)
    assert GuardedBroker(t, magic_base=260100).place_order(_order(0.04)).ok and len(t.sent) == 1
    over = _Terminal(equity=1_000.0, currency="GBP", contract=100.0, bid=2500.01, ask=2500.01, fx=0.6)
    assert _refused(GuardedBroker(over, magic_base=260100).place_order(_order(0.04)), over)


def test_ceiling_scales_with_equity_at_10000():
    t = _Terminal(equity=10_000.0)                  # 6x = 60,000 = 0.24 lot at 2500
    assert GuardedBroker(t, magic_base=260100).place_order(_order(0.24)).ok
    t2 = _Terminal(equity=10_000.0)
    assert _refused(GuardedBroker(t2, magic_base=260100).place_order(_order(0.25)), t2)


def test_a_sell_is_priced_at_the_bid_and_a_buy_at_the_ask():
    # 0.04 lot x 100 x fx 0.6: at the bid 2500 exactly 6x (passes), at the ask 2500.5 above it (refused)
    sell = _Terminal(equity=1_000.0, currency="GBP", bid=2500.0, ask=2500.5, fx=0.6)
    assert GuardedBroker(sell, magic_base=260100).place_order(_order(0.04, side=-1)).ok
    buy = _Terminal(equity=1_000.0, currency="GBP", bid=2500.0, ask=2500.5, fx=0.6)
    assert _refused(GuardedBroker(buy, magic_base=260100).place_order(_order(0.04, side=1)), buy)


def test_open_position_notional_counts_toward_the_ceiling():
    t = _Terminal(equity=1_000.0, positions=[_pos(0.02)])          # 0.02 open + 0.01 = 7.5x
    assert _refused(GuardedBroker(t, magic_base=260100).place_order(_order(0.01)), t)
    alone = _Terminal(equity=1_000.0)
    assert GuardedBroker(alone, magic_base=260100).place_order(_order(0.01)).ok


@pytest.mark.parametrize(("position", "counts"), [
    (_pos(0.02, side=-1), True),                    # the opposite side: gross, not net
    (_pos(0.02, magic=1), True),                    # another magic (the owner's manual trade): still exposure
    (_pos(0.02, magic=999_999), True),
    (_pos(0.02, symbol="XAGUSD"), False),           # another symbol: not this instrument's notional
])
def test_every_position_on_the_symbol_counts_whatever_its_side_or_magic_and_other_symbols_do_not(position, counts):
    t = _Terminal(equity=1_000.0, positions=[position])
    res = GuardedBroker(t, magic_base=260100).place_order(_order(0.01))
    assert _refused(res, t) is counts and res.ok is not counts


def test_gbp_account_converts_with_the_terminal_figure():
    # 0.03 lot x 100 x 2500 = 7,500 USD; at 0.75 GBP per USD = 5,625 GBP = 5.6x of 1,000 GBP: inside
    t = _Terminal(equity=1_000.0, currency="GBP", fx=0.75)
    assert GuardedBroker(t, magic_base=260100).place_order(_order(0.03)).ok
    t2 = _Terminal(equity=1_000.0, currency="GBP", fx=0.85)          # 6,375 GBP: refused
    assert _refused(GuardedBroker(t2, magic_base=260100).place_order(_order(0.03)), t2)


_NAN = float("nan")


@pytest.mark.parametrize(("bad", "field"), [
    ({"equity": _NAN}, "equity"), ({"equity": 0.0}, "equity"), ({"equity": -5.0}, "equity"),
    ({"equity": RuntimeError("account_info None")}, "account"),
    ({"price": _NAN}, "price"), ({"price": 0.0}, "price"), ({"price": RuntimeError("no tick")}, "price"),
    ({"contract": _NAN}, "contract"), ({"contract": 0.0}, "contract"),
    ({"contract": RuntimeError("symbol_info None")}, "contract"),
    ({"fx": _NAN}, "fx"), ({"fx": 0.0}, "fx"), ({"fx": None}, "fx"), ({"fx": RuntimeError("calc_profit")}, "fx"),
    ({"positions": RuntimeError("positions_get None")}, "positions"),
    ({"positions": [_pos(_NAN)]}, "positions"),
])
def test_unreadable_equity_price_contract_fx_or_positions_refuses(bad, field):
    t = _Terminal(equity=10_000.0, bad=bad)
    res = GuardedBroker(t, magic_base=260100).place_order(_order(0.01))
    assert _refused(res, t), res
    assert f"unreadable:{field}" in res.message


@pytest.mark.parametrize(("currency", "fx"), [
    ("USD", 1.02),                                  # a USD account whose conversion is not 1 within 1%
    ("", 1.0),                                      # a blank deposit currency is never sized
    ("   ", 1.0),
])
def test_a_conversion_the_sizing_would_refuse_is_refused_at_the_bridge(currency, fx):
    t = _Terminal(equity=10_000.0, currency=currency, fx=fx)
    res = GuardedBroker(t, magic_base=260100).place_order(_order(0.01))
    assert _refused(res, t) and "unreadable:fx" in res.message


def test_a_usd_account_with_no_terminal_conversion_is_refused_not_assumed_one():
    t = _Terminal(equity=10_000.0, currency="USD", fx=None)
    res = GuardedBroker(t, magic_base=260100).place_order(_order(0.01))
    assert _refused(res, t) and "unreadable:fx" in res.message


def test_the_account_is_read_once_for_every_order_even_without_a_verifier():
    t = _Terminal(equity=10_000.0)
    seen: list[AccountInfo] = []

    def verify(info: AccountInfo) -> str | None:
        seen.append(info)
        return None

    assert GuardedBroker(t, magic_base=260100).place_order(_order(0.01)).ok
    assert t.reads.count("equity") == 1
    t2 = _Terminal(equity=10_000.0)
    assert GuardedBroker(t2, magic_base=260100, verify=verify).place_order(_order(0.01)).ok
    assert t2.reads.count("equity") == 1 and len(seen) == 1         # the verifier sees the same single read


@pytest.fixture
def guarded_terminal() -> Iterator[tuple[str, _Terminal]]:
    t = _Terminal(equity=1_000.0)
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(GuardedBroker(t, magic_base=260100), TOKEN))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", t
    httpd.shutdown()
    httpd.server_close()


def test_an_account_read_that_raises_is_a_definite_reject_not_an_http_500(guarded_terminal):
    url, t = guarded_terminal
    t.bad["equity"] = RuntimeError("account_info returned None")
    res = RemoteBroker(url, TOKEN).place_order(_order(0.01))       # an HTTP 500 would raise BridgeError here
    assert _refused(res, t) and "unreadable:account" in res.message


def test_an_account_read_that_returns_nothing_is_a_definite_reject_before_the_verifier(guarded_terminal):
    url, t = guarded_terminal
    seen: list[Any] = []
    t.account = lambda: None                                    # MT5 account_info() returning None
    g = GuardedBroker(t, magic_base=260100, verify=lambda info: seen.append(info))
    res = g.place_order(_order(0.01))
    assert _refused(res, t) and "unreadable:account" in res.message and seen == []
    res = RemoteBroker(url, TOKEN).place_order(_order(0.01))    # over the wire: 10006, not an HTTP 500
    assert _refused(res, t) and "unreadable:account" in res.message


def test_a_changed_verified_account_still_raises():
    t = _Terminal(equity=10_000.0)
    g = GuardedBroker(t, magic_base=260100, verify=lambda info: "terminal is logged in to another account")
    with pytest.raises(PermissionError, match="account changed"):
        g.place_order(_order(0.01))
    assert t.sent == []


def test_three_lot_outer_bound_still_refuses_above_three():
    t = _Terminal(equity=1_000_000.0)                # the ceiling alone would allow 3.01 lots
    with pytest.raises(PermissionError, match="lots"):
        GuardedBroker(t, magic_base=260100).place_order(_order(3.01))
    assert t.sent == [] and t.reads == []           # refused before the ceiling read anything
    with pytest.raises(PermissionError, match="magic"):
        GuardedBroker(t, magic_base=260100).place_order(_order(0.01).model_copy(update={"magic": 5}))
    assert t.sent == [] and t.reads == []


def test_close_of_a_three_lot_position_goes_through():
    t = _Terminal(equity=1.0, positions=[_pos(3.0)])                # far above the ceiling: closing only reduces risk
    res = GuardedBroker(t, magic_base=260100).close(7)
    assert res.ok and t.reads == []


def test_modify_is_never_sized():
    t = _Terminal(equity=1.0, positions=[_pos(3.0)])
    res = GuardedBroker(t, magic_base=260100).modify(7, 2450.0, None)
    assert res.ok and t.reads == []


def test_refusal_is_a_definite_reject_and_crosses_the_wire_with_its_reason(guarded_terminal):
    from goldbot.engine.runner import DEFINITE_REJECT_RETCODES
    url, t = guarded_terminal
    res = RemoteBroker(url, TOKEN).place_order(_order(0.03))          # 7.5x of 1,000
    assert _refused(res, t)
    assert res.retcode in DEFINITE_REJECT_RETCODES                    # the engine records `rejected`, never "maybe sent"
    assert "7500.00 USD > 6 x equity 1000.00" in res.message, res.message
    assert RemoteBroker(url, TOKEN).place_order(_order(0.01)).ok and len(t.sent) == 1


def test_an_order_that_aged_out_during_the_ceiling_reads_is_refused_before_the_send(monkeypatch):
    # C6: the handler's age check runs before the ceiling's terminal reads; the age is checked again just before the
    # inner send, so a slow terminal cannot turn a fresh request into a stale order
    monkeypatch.setattr(bridge_mod, "ORDER_MAX_AGE_S", 0.5)

    class Slow(_Terminal):
        def account_positions(self) -> list[Position]:
            time.sleep(0.4)
            return super().account_positions()

    t = Slow(equity=10_000.0)
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(GuardedBroker(t, magic_base=260100), TOKEN))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url, hdr = f"http://127.0.0.1:{httpd.server_address[1]}/rpc", {"Authorization": f"Bearer {TOKEN}"}
        body = {"method": "place_order", "args": [bridge_mod._encode(_order(0.01))], "sent_at": time.time() - 0.2}
        r = requests.post(url, json=body, headers=hdr, timeout=5)
        assert r.status_code == 200
        res = bridge_mod._decode(r.json()["result"], response=True)
        assert not res.ok and res.retcode == 10006 and res.message.startswith("bridge_stale_order") and t.sent == []
        body["sent_at"] = time.time()                                      # fresh: sent
        r = requests.post(url, json=body, headers=hdr, timeout=5)
        assert r.status_code == 200 and len(t.sent) == 1
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_caller_can_drop_the_read_retry_and_shorten_the_timeout():
    """The tick record (goldbot/data/ticks.py) bounds each copy_ticks page by ticks.page_limit_s: one attempt, its own
    client timeout, so a slow page never holds the client twice as long (trading-safety review of bf2bf242)."""
    read = _Flaky(fail=1)
    rb = RemoteBroker("http://bridge", TOKEN, post=read)
    assert rb.read_attempts == 2
    rb.read_attempts = 1
    with pytest.raises(BridgeUnavailable):
        rb.last_tick("XAUUSD")
    assert read.calls == 1
