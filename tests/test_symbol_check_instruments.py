"""The symbol check per instrument (multi-instrument plan 2026-11-02, lane L1: "symbol_check per symbol"). Read-only:
for every instrument an account lists, the terminal must list its broker symbol with trading enabled and the terms
config/instruments.yaml declares (contract, digits, point, minimum lot and step, and the profit currency and triple-
swap day when the terminal reports them). The account's gold symbol keeps the ADR 0062 check unchanged."""
from __future__ import annotations

from typing import Any

import pytest

from goldbot.execution.broker import SymbolInfo
from goldbot.instruments import InstrumentSpec, registry
from goldbot.ops import accounts as acc_mod
from goldbot.ops import symbol_check


def _info(spec: InstrumentSpec, broker: str, **kw: Any) -> SymbolInfo:
    base: dict[str, Any] = dict(name=spec.broker_symbols[broker], digits=spec.digits, point=spec.point,
                                contract_size=spec.contract_size, volume_min=spec.min_lot, volume_step=spec.lot_step,
                                volume_max=100.0, stops_level_points=0, filling_modes=["IOC"], trade_allowed=True,
                                currency_profit=spec.currency_profit, swap_rollover3days=spec.swap_rollover3days)
    return SymbolInfo(**{**base, **kw})


class Terminal:
    """A read-only fake terminal: the registry's own terms for every symbol of `broker`, `change` overriding one."""

    def __init__(self, broker: str, change: dict[str, dict[str, Any]] | None = None,
                 missing: tuple[str, ...] = ()) -> None:
        self.infos = {s.broker_symbols[broker]: _info(s, broker, **(change or {}).get(s.key, {}))
                      for s in registry().for_broker(broker) if s.key != "XAUUSD"}
        for k in missing:
            self.infos.pop(registry().get(k).broker_symbols[broker])
        self.calls: list[str] = []

    def symbols_get(self, pattern: str = "*") -> list[str]:
        self.calls.append("symbols_get")
        return list(self.infos)

    def symbol_info(self, symbol: str) -> SymbolInfo:
        self.calls.append("symbol_info")
        if symbol not in self.infos:
            raise RuntimeError("symbol_info None")
        return self.infos[symbol]


def _acc(aid: str) -> acc_mod.Account:
    return acc_mod.load_accounts(logins=False)[aid]


@pytest.mark.parametrize("aid, broker, n", [("icm-probe", "icm", 14), ("vantage-demo", "vantage", 10)])   # 2026-11-04
def test_every_listed_instrument_passes_on_a_terminal_with_the_declared_terms(aid: str, broker: str, n: int) -> None:
    out = symbol_check.check_instruments(_acc(aid), Terminal(broker))
    assert len(out) == n and all(p == [] for p in out.values())
    assert set(out) == set(_acc(aid).instruments)


@pytest.mark.parametrize("key, change, why", [
    ("EURUSD", {"contract_size": 10_000.0}, "contract size"),
    ("USDJPY", {"digits": 2}, "digits"),
    ("USDJPY", {"point": 0.01}, "point"),
    ("US500", {"volume_min": 1.0}, "minimum lot"),
    ("US500", {"volume_step": 0.01}, "lot step"),
    ("DE40", {"currency_profit": "USD"}, "profit currency"),
    ("XTIUSD", {"swap_rollover3days": 3}, "triple swap"),
    ("XAGUSD", {"trade_allowed": False}, "trading disabled"),
])
def test_each_term_that_differs_from_the_registry_is_named(key: str, change: dict[str, Any], why: str) -> None:
    out = symbol_check.check_instruments(_acc("icm-probe"), Terminal("icm", {key: change}))
    assert len(out[key]) == 1 and why in out[key][0]
    assert all(p == [] for k, p in out.items() if k != key)


def test_a_symbol_the_terminal_does_not_list_is_named() -> None:
    out = symbol_check.check_instruments(_acc("icm-probe"), Terminal("icm", missing=("NZDUSD",)))
    assert out["NZDUSD"] and "not listed" in out["NZDUSD"][0]


def test_terms_the_terminal_does_not_report_are_not_failed() -> None:
    """An older bridge sends no profit currency or triple-swap day: unknown is not a mismatch (the sizing still
    refuses without the broker's conversion)."""
    t = Terminal("icm", {k: {"currency_profit": None, "swap_rollover3days": None} for k in ("EURUSD", "DE40")})
    out = symbol_check.check_instruments(_acc("icm-probe"), t)
    assert out["EURUSD"] == [] and out["DE40"] == []


def test_the_check_only_reads() -> None:
    t = Terminal("icm")
    symbol_check.check_instruments(_acc("icm-probe"), t)
    assert set(t.calls) <= {"symbols_get", "symbol_info"}


def test_the_terms_print_as_a_registry_entry_to_confirm() -> None:
    spec = registry().get("EURUSD")
    line = symbol_check.terms_line(spec, _info(spec, "icm"))
    assert "contract_size: 100000" in line and "digits: 5" in line and "currency_profit: USD" in line


def test_an_account_without_instruments_checks_none() -> None:
    assert symbol_check.check_instruments(_acc("icm-demo"), Terminal("icm")) == {}
