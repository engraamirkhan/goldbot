"""MT5 broker bridge (goldbot/execution/bridge.py): the engine on one machine, the terminal on another (Oracle free
tier: MT5 under Wine). A real HTTP bridge on localhost serves the paper broker; RemoteBroker must behave like the
broker itself, authenticate every call, expose only the Broker methods and never retry an order."""
import socket
import threading
import time
from http.server import HTTPServer
from typing import Any, Iterator

import pandas as pd
import pytest
import requests

from goldbot.engine import Engine, EngineConfig
from goldbot.execution import bridge as bridge_mod
from goldbot.execution.bridge import BridgeError, BridgeUnavailable, GuardedBroker, RemoteBroker, make_handler
from goldbot.execution.broker import AccountInfo, OrderIntent, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
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
    assert requests.get(f"{url}/health", timeout=5).json() == {"ok": True}
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
                                currency="USD", server="ICMarketsSC-Demo", trade_mode="demo")
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
