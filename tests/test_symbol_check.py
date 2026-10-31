"""ADR 0062 item 3: the read-only symbol check of a new account's terminal. The bridge serves `symbols_get(pattern)`
(read-only, optional: an older bridge refuses it and nothing else changes) and SymbolInfo carries the swap and margin
terms, so `goldbot.ops.symbol_check` can say, before an engine starts, whether the account's symbol exists on its
terminal with the declared contract size and trading enabled."""
from __future__ import annotations

import json
from collections import namedtuple
from types import SimpleNamespace
from typing import Any

import pytest

from goldbot.execution import mt5_adapter
from goldbot.execution.bridge import API_VERSION, METHODS, MIN_COMPATIBLE, OPTIONAL_METHODS, RemoteBroker
from goldbot.execution.broker import SymbolInfo
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.ops import symbol_check
from goldbot.ops.bridge_compat import REQUIRED_METHODS

Sym = namedtuple("Sym", "name visible digits point trade_contract_size volume_min volume_step volume_max "
                        "trade_stops_level filling_mode trade_mode swap_mode swap_long swap_short margin_initial "
                        "margin_maintenance")


def _sym(**kw: Any) -> Sym:
    base: dict[str, Any] = dict(name="XAUUSD247", visible=True, digits=2, point=0.01, trade_contract_size=1.0,
                                volume_min=0.01, volume_step=0.01, volume_max=100.0, trade_stops_level=0,
                                filling_mode=2, trade_mode=4, swap_mode=1, swap_long=-30.0, swap_short=10.0,
                                margin_initial=0.0, margin_maintenance=0.0)
    return Sym(**{**base, **kw})


def _adapter(monkeypatch: pytest.MonkeyPatch, sym: Sym, names: tuple[str, ...] = ("XAUUSD247", "XAUUSD")) -> Any:
    fake = SimpleNamespace(initialize=lambda **k: True, symbol_select=lambda s, on: True, symbol_info=lambda s: sym,
                           last_error=lambda: (1, "err"), shutdown=lambda: None,
                           symbols_get=lambda group="*": tuple(SimpleNamespace(name=n) for n in names
                                                               if group in ("*", n) or (group.endswith("*")
                                                                                        and n.startswith(group[:-1]))))
    monkeypatch.setattr(mt5_adapter, "mt5", fake)
    return mt5_adapter.MT5Broker(terminal_path="", login=None, password=None, server="s", server_tz="NY+7",
                                 symbol="XAUUSD247", account_label="vantage-247", verify=None)


def test_symbol_info_carries_the_swap_and_margin_terms(monkeypatch):
    info = _adapter(monkeypatch, _sym()).symbol_info("XAUUSD247")
    assert (info.swap_mode, info.swap_long, info.swap_short) == (1, -30.0, 10.0)
    assert info.margin_initial == 0.0 and info.margin_maintenance == 0.0 and info.contract_size == 1.0
    # an older reply without them still reads (tolerant): None, never a guessed zero
    old = SymbolInfo(name="X", digits=2, point=0.01, contract_size=100, volume_min=0.01, volume_step=0.01,
                     volume_max=50, stops_level_points=0, filling_modes=["IOC"], trade_allowed=True)
    assert old.swap_long is None and old.margin_initial is None


def test_symbols_get_lists_the_terminals_symbol_names_for_a_pattern(monkeypatch):
    b = _adapter(monkeypatch, _sym())
    assert b.symbols_get("XAUUSD2*") == ["XAUUSD247"]
    assert sorted(b.symbols_get("*")) == ["XAUUSD", "XAUUSD247"]


def test_the_bridge_serves_symbols_get_read_only_and_optional_on_a_bumped_protocol():
    assert METHODS["symbols_get"] == ("list[str]", True)
    assert "symbols_get" in OPTIONAL_METHODS and "symbols_get" not in REQUIRED_METHODS
    assert API_VERSION >= 8 and MIN_COMPATIBLE == 5                 # an older bridge stays compatible


def test_the_client_asks_the_bridge_for_symbols_get():
    sent: list[dict] = []

    def post(url: str, data: str, **kw: Any) -> Any:
        sent.append(json.loads(data))
        return SimpleNamespace(status_code=200, json=lambda: {"result": ["XAUUSD247"]})

    assert RemoteBroker("http://b", "t", post=post).symbols_get("XAUUSD2*") == ["XAUUSD247"]
    assert sent[0]["method"] == "symbols_get" and sent[0]["args"] == ["XAUUSD2*"]


def _acc() -> acc_mod.Account:
    return acc_mod.load_accounts(logins=False)["vantage-247"]


def test_the_lane_symbol_passes_on_a_terminal_that_lists_it_with_one_ounce(monkeypatch):
    assert symbol_check.check_symbol(_acc(), _adapter(monkeypatch, _sym())) == []


@pytest.mark.parametrize("sym, names, why", [
    (_sym(), ("XAUUSD+",), "not listed"),
    (_sym(trade_contract_size=100.0), ("XAUUSD247",), "contract size"),
    (_sym(trade_mode=0), ("XAUUSD247",), "trading disabled"),
])
def test_a_missing_symbol_another_contract_or_disabled_trading_is_named(monkeypatch, sym, names, why):
    problems = symbol_check.check_symbol(_acc(), _adapter(monkeypatch, sym, names))
    assert len(problems) == 1 and why in problems[0]


def test_a_broker_without_symbols_get_is_checked_on_symbol_info_alone():
    pb = PaperBroker(symbol="XAUUSD247", contract=1.0)
    assert symbol_check.check_symbol(_acc(), pb) == []
