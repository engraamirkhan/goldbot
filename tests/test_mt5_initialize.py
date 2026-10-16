"""The MT5 Python API attaches to the RUNNING terminal only when `initialize()` is given that terminal's path: on the
Wine box (16 Oct) `mt5.initialize()` without `path=` failed with IPC timeout -10005, and
`initialize(path=r"C:\\MT5\\ICMarkets\\terminal64.exe", timeout=60000)` attached
(docs/incidents/2026-10-16-mt5-login-generic-installer.md). The adapter passes both, explicitly, so a package default
can never change what was verified on the box."""
from collections import namedtuple
from types import SimpleNamespace
from typing import Any

import pytest

from goldbot.execution import mt5_adapter

Sym = namedtuple("Sym", "name digits point trade_contract_size volume_min volume_step volume_max trade_stops_level "
                        "filling_mode trade_mode visible")
TERMINAL = r"C:\MT5\ICMarkets\terminal64.exe"


def _fake(calls: list[dict[str, Any]], ok: bool = True) -> SimpleNamespace:
    def initialize(*args: Any, **kw: Any) -> bool:
        assert not args, "path is passed by keyword"
        calls.append(kw)
        return ok
    return SimpleNamespace(initialize=initialize, symbol_select=lambda s, on: True, last_error=lambda: (-10005, "IPC timeout"),
                           shutdown=lambda: None,
                           symbol_info=lambda s: Sym(s, 2, 0.01, 100.0, 0.01, 0.01, 50.0, 0, 2, 4, True))


def _broker(login: int | None) -> mt5_adapter.MT5Broker:
    return mt5_adapter.MT5Broker(terminal_path=TERMINAL, login=login, password="pw" if login else None,
                                 server="ICMarketsSC-Demo", server_tz="Europe/Athens", symbol="XAUUSD",
                                 account_label="icm-demo")


@pytest.mark.parametrize("login", [None, 1234], ids=["bridge: the terminal's saved login", "with credentials"])
def test_initialize_attaches_to_the_terminal_by_path_with_an_explicit_timeout(monkeypatch, login):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(mt5_adapter, "mt5", _fake(calls))
    _broker(login)
    assert len(calls) == 1
    assert calls[0]["path"] == TERMINAL
    assert calls[0]["timeout"] == mt5_adapter.MT5_INIT_TIMEOUT_MS == 60_000


def test_an_initialize_failure_names_the_terminal_error(monkeypatch):
    monkeypatch.setattr(mt5_adapter, "mt5", _fake([], ok=False))
    with pytest.raises(RuntimeError, match="-10005"):
        _broker(None)
