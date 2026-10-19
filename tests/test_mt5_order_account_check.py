"""Trading-safety finding (MEDIUM, money path): the direct MT5 adapter checked the terminal's account only when the
engine started. A terminal switched afterwards to another login, or to a real account, would have taken an
owner-approved entry under the demo account's limits. `MT5Broker.place_order` now runs the same per-order check as the
bridge's GuardedBroker (`accounts.verify_terminal_account`) and refuses the entry as a definite reject; closes are
never checked (closing only reduces risk; the kill switch closes everything), exactly as the bridge.

The logins below are made up."""
import sys
from collections import namedtuple
from types import SimpleNamespace
from typing import Any

import pytest

from goldbot.execution import mt5_adapter
from goldbot.execution.broker import OrderIntent
from goldbot.ops import accounts

LOGIN, OTHER_LOGIN, SERVER = 11112222, 33334444, "Example-Demo"
DEMO, REAL, HEDGING = 0, 2, 2
ACC = accounts.Account(account_id="icm-demo", broker="icm", mode="demo", server=SERVER, login=LOGIN,
                       terminal_path="", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)
MTick = namedtuple("MTick", "time_msc bid ask flags")
Sym = namedtuple("Sym", "name digits point trade_contract_size volume_min volume_step volume_max trade_stops_level "
                        "filling_mode trade_mode visible")
Send = namedtuple("Send", "retcode order deal volume price comment")
Chk = namedtuple("Chk", "retcode comment")
Pos = namedtuple("Pos", "ticket symbol type volume price_open sl tp magic comment time_msc profit")
Acc = namedtuple("Acc", "login equity balance margin margin_free leverage currency server trade_mode margin_mode")


def _terminal(login: int = LOGIN, trade_mode: int = DEMO) -> SimpleNamespace:
    """A terminal logged in to `login` (`trade_mode` 0 demo, 2 real) holding one open position, 777."""
    sent: list[dict] = []
    book = [Pos(777, "XAUUSD", 0, 0.1, 2400.0, 2390.0, 2430.0, 260101, "x", 1_741_168_800_000, 0.0)]

    def order_send(req: dict) -> Send:
        sent.append(req)
        return Send(10009, 222, 111, req.get("volume", 0.0), 2400.2, "done")

    return SimpleNamespace(
        initialize=lambda **k: True, symbol_select=lambda s, on: True, last_error=lambda: (1, "err"),
        shutdown=lambda: None, ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1, POSITION_TYPE_BUY=0, TRADE_ACTION_DEAL=1,
        TRADE_ACTION_SLTP=6, ORDER_TIME_GTC=0, ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1, ORDER_FILLING_RETURN=2,
        symbol_info=lambda s: Sym(s, 2, 0.01, 100.0, 0.01, 0.01, 50.0, 0, 2, 4, True),
        symbol_info_tick=lambda s: MTick(1_741_168_800_000, 2400.0, 2400.2, 6),
        account_info=lambda: Acc(login, 1_000.0, 1_000.0, 0.0, 1_000.0, 500, "GBP", SERVER, trade_mode, HEDGING),
        order_check=lambda req: Chk(0, ""), order_send=order_send, positions_get=lambda **kw: list(book),
        history_deals_get=lambda *a, **kw: (), sent=sent)


def _broker(monkeypatch: pytest.MonkeyPatch, term: SimpleNamespace, *, verify: Any = None) -> mt5_adapter.MT5Broker:
    monkeypatch.setattr(mt5_adapter, "mt5", term)
    check = (lambda info: accounts.verify_terminal_account(ACC, info)) if verify is None else verify
    return mt5_adapter.MT5Broker(terminal_path="", login=LOGIN, password="not-a-password", server=SERVER,
                                 server_tz="Europe/Athens", symbol="XAUUSD", account_label="icm-demo", verify=check)


def _intent() -> OrderIntent:
    return OrderIntent(client_order_id="icm-demo-x", symbol="XAUUSD", side=1, lots=0.1, sl=2390.0, tp=2430.0,
                       magic=260101)


def test_an_entry_on_the_registered_demo_account_is_sent(monkeypatch):
    term = _terminal()
    res = _broker(monkeypatch, term).place_order(_intent())
    assert res.ok and len(term.sent) == 1


def test_an_entry_is_refused_when_the_terminal_was_switched_to_another_login(monkeypatch):
    term = _terminal(login=OTHER_LOGIN)
    res = _broker(monkeypatch, term).place_order(_intent())
    assert not res.ok and res.retcode == mt5_adapter.RETCODE_REJECT     # a definite reject: never retried as unknown
    assert res.message.startswith(f"{mt5_adapter.ACCOUNT_REFUSAL}: ") and "another account" in res.message
    assert str(OTHER_LOGIN) not in res.message and str(LOGIN) not in res.message
    assert term.sent == []


def test_an_entry_is_refused_when_the_terminal_is_real_under_a_demo_account(monkeypatch):
    term = _terminal(trade_mode=REAL)
    res = _broker(monkeypatch, term).place_order(_intent())
    assert not res.ok and res.retcode == mt5_adapter.RETCODE_REJECT
    assert "terminal account is real, icm-demo is demo" in res.message
    assert term.sent == []


def test_the_refusal_is_logged(monkeypatch, caplog):
    with caplog.at_level("WARNING", logger=mt5_adapter.log.name):
        _broker(monkeypatch, _terminal(trade_mode=REAL)).place_order(_intent())
    assert "icm-demo-x refused" in caplog.text and mt5_adapter.ACCOUNT_REFUSAL in caplog.text


@pytest.mark.parametrize("why", ["account_info None", "account_info raises", "verify raises"])
def test_an_entry_is_refused_when_the_account_cannot_be_read_or_checked(monkeypatch, why):
    term = _terminal()
    verify = None
    if why == "account_info None":
        term.account_info = lambda: None
    elif why == "account_info raises":
        term.account_info = lambda: (_ for _ in ()).throw(RuntimeError("IPC timeout"))
    else:
        def verify(info: Any) -> str | None:
            raise ValueError("bad record")
    res = _broker(monkeypatch, term, verify=verify).place_order(_intent())
    assert not res.ok and res.retcode == mt5_adapter.RETCODE_REJECT and "unverifiable" in res.message, why
    assert term.sent == []


@pytest.mark.parametrize(("login", "trade_mode"), [(OTHER_LOGIN, DEMO), (LOGIN, REAL), (OTHER_LOGIN, REAL)])
def test_a_close_is_sent_whatever_account_the_terminal_is_on(monkeypatch, login, trade_mode):
    term = _terminal(login=login, trade_mode=trade_mode)
    term.account_info = lambda: (_ for _ in ()).throw(AssertionError("a close never reads the account"))
    res = _broker(monkeypatch, term).close(777)
    assert res.ok and len(term.sent) == 1 and term.sent[0]["position"] == 777


def test_a_stop_change_is_not_gated_by_the_account_check(monkeypatch):
    # as the bridge: modify is never account-checked (moving a stop on an open position must not be blocked)
    term = _terminal(login=OTHER_LOGIN, trade_mode=REAL)
    res = _broker(monkeypatch, term).modify(777, 2395.0, None)
    assert res.ok and term.sent[0]["sl"] == 2395.0


def test_without_a_check_the_adapter_sends_as_before(monkeypatch):
    # the bridge (GuardedBroker verifies above it) and the scheduler's read-only broker pass verify=None
    term = _terminal(login=OTHER_LOGIN, trade_mode=REAL)
    monkeypatch.setattr(mt5_adapter, "mt5", term)
    broker = mt5_adapter.MT5Broker(terminal_path="", login=None, password=None, server=SERVER,
                                   server_tz="Europe/Athens", symbol="XAUUSD", account_label="icm-demo", verify=None)
    assert broker.place_order(_intent()).ok


# ------------------------------------------------------------------------------------- the engine's direct terminal
class _Built(Exception):
    pass


def test_the_engine_builds_its_direct_terminal_broker_with_the_per_order_account_check(monkeypatch, tmp_path):
    from goldbot.ops import run as run_mod
    from goldbot.ops.services import engine as engine_service
    seen: dict[str, Any] = {}

    def built(**kw: Any) -> None:
        seen.update(kw)
        raise _Built

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(accounts, "load_accounts", lambda path=None: {"icm-demo": ACC})
    monkeypatch.setattr(accounts, "bridge_endpoint", lambda aid: None)
    monkeypatch.setattr(accounts, "ensure_login", lambda acc: acc)
    monkeypatch.setattr(accounts, "account_password", lambda acc: "not-a-password")
    monkeypatch.setattr(mt5_adapter, "MT5Broker", built)
    # the direct-terminal branch, wherever the test runs: only the engine service sees win32 (the store's lock does not)
    monkeypatch.setattr(engine_service, "sys", SimpleNamespace(**{**vars(sys), "platform": "win32"}))
    with pytest.raises(_Built):
        run_mod.run_engine("icm-demo")
    monkeypatch.undo()
    verify = seen["verify"]
    assert callable(verify)
    def info(login: int = LOGIN, mode: str = "demo") -> SimpleNamespace:   # as MT5Broker.account() reports it
        return SimpleNamespace(login=login, server=SERVER, trade_mode=mode, margin_mode="hedging")

    assert verify(info()) is None
    assert "another account" in verify(info(login=OTHER_LOGIN))
    assert "terminal account is real" in verify(info(mode="real"))
