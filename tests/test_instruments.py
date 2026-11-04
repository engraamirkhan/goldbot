"""Multi-instrument demo, step 0: the frozen InstrumentSpec interface, config/instruments.yaml, and the Broker
protocol stubs (positions(symbol=None), ticks_many, SymbolInfo.currency_profit / swap_rollover3days) that keep
today's XAUUSD behaviour unchanged."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from goldbot.config import load_yaml
from goldbot.execution.bridge import RemoteBroker
from goldbot.execution.broker import OrderIntent, SymbolInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.instruments import (
    DEFAULT_INSTRUMENTS,
    LEVERAGE_BY_CLASS,
    Instruments,
    InstrumentSpec,
    load_instruments,
)

ICM = ["XAGUSD", "XPTUSD", "XAUEUR", "XAUAUD", "US500", "USTEC", "DE40", "UK100", "JP225", "EURUSD", "USDJPY",
       "GBPUSD", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "XTIUSD", "XBRUSD", "XNGUSD"]
# owner decision 2026-11-04: the 10 added instruments are Vantage's (one broker per instrument)
VANTAGE = ["USNote10Y", "EUB10Y", "COPPER-C", "Wheat-C", "Soybean-C", "Sugar-C", "Coffee-C",
           "GBPJPY+", "EURGBP+", "EURJPY+", "AUDJPY+", "EURAUD+", "GBPAUD+", "BTCUSD", "XAUJPY+", "USDX", "DJ30"]
# owner decision 2026-11-04: removed from trading, entries and magic blocks kept (tests/test_owner_instrument_list.py)
REMOVED = {"XPTUSD", "XAUAUD", "JP225", "XBRUSD", "XNGUSD", "UST10Y", "BUND10Y", "COPPER", "WHEAT", "SOYBEAN", "SUGAR",
           "COFFEE"}


def test_the_instrument_file_loads_gold_and_the_36_registered_instruments() -> None:
    inst = load_instruments()
    assert len(inst.instruments) == 37
    icm = [i.broker_symbols["icm"] for i in inst.instruments.values() if "icm" in i.broker_symbols]
    vantage = [i.broker_symbols["vantage"] for i in inst.instruments.values() if "vantage" in i.broker_symbols]
    assert sorted(icm) == sorted(["XAUUSD", *ICM])
    assert sorted(vantage) == sorted(["XAUUSD+", *VANTAGE])


def test_xauusd_keeps_todays_gold_terms_with_magic_block_zero() -> None:
    gold = load_instruments().get("XAUUSD")
    assert gold.magic_block == 0 and gold.enabled and not gold.verify
    assert (gold.contract_size, gold.digits, gold.point, gold.min_lot, gold.lot_step) == (100.0, 2, 0.01, 0.01, 0.01)
    assert gold.store_symbol == "XAUUSD" and gold.currency_profit == "USD" and gold.correlation_group == "gold"
    assert gold.broker_symbols["icm"] == "XAUUSD" and gold.broker_symbols["vantage"] == "XAUUSD+"


def test_magic_blocks_are_unique_and_fit_inside_one_account_range() -> None:
    blocks = [i.magic_block for i in load_instruments().instruments.values()]
    assert len(blocks) == len(set(blocks))
    assert all(0 <= b < 100 for b in blocks)          # magic_block * 100 + offset stays under the 10_000 account step


def test_a_repeated_magic_block_is_refused_at_load() -> None:
    raw = load_yaml(DEFAULT_INSTRUMENTS)
    raw["instruments"]["XAGUSD"]["magic_block"] = 0
    with pytest.raises(ValidationError, match="magic_block"):
        Instruments.model_validate(raw)


def test_a_magic_block_of_100_or_more_is_refused() -> None:
    raw = load_yaml(DEFAULT_INSTRUMENTS)
    raw["instruments"]["XAGUSD"]["magic_block"] = 100
    with pytest.raises(ValidationError):
        Instruments.model_validate(raw)


def test_a_broker_symbol_mapped_twice_on_one_broker_is_refused() -> None:
    raw = load_yaml(DEFAULT_INSTRUMENTS)
    raw["instruments"]["XAGUSD"]["broker_symbols"]["icm"] = "XAUUSD"
    with pytest.raises(ValidationError, match="broker symbol"):
        Instruments.model_validate(raw)


def test_a_store_symbol_shared_by_two_instruments_is_refused() -> None:
    raw = load_yaml(DEFAULT_INSTRUMENTS)
    raw["instruments"]["XAGUSD"]["store_symbol"] = "XAUUSD"
    with pytest.raises(ValidationError, match="store_symbol"):
        Instruments.model_validate(raw)


def test_a_key_that_differs_from_its_entry_name_is_refused() -> None:
    raw = load_yaml(DEFAULT_INSTRUMENTS)
    raw["instruments"]["XAGUSD"]["key"] = "SILVER"
    with pytest.raises(ValidationError, match="key"):
        Instruments.model_validate(raw)


def test_a_misspelt_field_fails_at_load(tmp_path: Path) -> None:
    raw = load_yaml(DEFAULT_INSTRUMENTS)
    raw["instruments"]["XAGUSD"]["contract_sise"] = 5000
    p = tmp_path / "instruments.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValidationError):
        load_instruments(p)


# the terminals' own terms (python -m goldbot.ops.symbol_check icm-probe / vantage-demo on the brain, 2026-11-02):
# key -> (contract_size, digits, point, min_lot, lot_step, currency_profit, swap_rollover3days, broker symbol)
TERMINAL_TERMS: dict[str, tuple[float, int, float, float, float, str, int, str]] = {
    "XAGUSD": (1000.0, 3, 0.001, 0.01, 0.01, "USD", 3, "XAGUSD"),
    "XPTUSD": (100.0, 2, 0.01, 0.01, 0.01, "USD", 3, "XPTUSD"),
    "XAUEUR": (100.0, 2, 0.01, 0.01, 0.01, "EUR", 3, "XAUEUR"),
    "XAUAUD": (100.0, 2, 0.01, 0.01, 0.01, "AUD", 3, "XAUAUD"),
    "US500": (1.0, 2, 0.01, 0.1, 0.1, "USD", 5, "US500"),
    "USTEC": (1.0, 2, 0.01, 0.1, 0.1, "USD", 5, "USTEC"),
    "DE40": (1.0, 2, 0.01, 0.1, 0.1, "EUR", 5, "DE40"),
    "UK100": (1.0, 2, 0.01, 0.1, 0.1, "GBP", 5, "UK100"),
    "JP225": (1.0, 2, 0.01, 1.0, 1.0, "JPY", 5, "JP225"),
    "EURUSD": (100000.0, 5, 0.00001, 0.01, 0.01, "USD", 3, "EURUSD"),
    "USDJPY": (100000.0, 3, 0.001, 0.01, 0.01, "JPY", 3, "USDJPY"),
    "GBPUSD": (100000.0, 5, 0.00001, 0.01, 0.01, "USD", 3, "GBPUSD"),
    "AUDUSD": (100000.0, 5, 0.00001, 0.01, 0.01, "USD", 3, "AUDUSD"),
    "USDCAD": (100000.0, 5, 0.00001, 0.01, 0.01, "CAD", 3, "USDCAD"),
    "USDCHF": (100000.0, 5, 0.00001, 0.01, 0.01, "CHF", 3, "USDCHF"),
    "NZDUSD": (100000.0, 5, 0.00001, 0.01, 0.01, "USD", 3, "NZDUSD"),
    "XTIUSD": (100.0, 3, 0.001, 0.01, 0.01, "USD", 5, "XTIUSD"),
    "XBRUSD": (100.0, 3, 0.001, 0.01, 0.01, "USD", 5, "XBRUSD"),
    "XNGUSD": (10000.0, 4, 0.0001, 0.01, 0.01, "USD", 5, "XNGUSD"),
    "UST10Y": (1.0, 2, 0.01, 1.0, 1.0, "USD", 3, "USNote10Y"),
    "BUND10Y": (1.0, 2, 0.01, 1.0, 1.0, "EUR", 3, "EUB10Y"),
    "COPPER": (25000.0, 4, 0.0001, 0.1, 0.1, "USD", 5, "COPPER-C"),
    "WHEAT": (1000.0, 3, 0.001, 0.1, 0.1, "USD", 5, "Wheat-C"),
    "SOYBEAN": (500.0, 3, 0.001, 0.1, 0.1, "USD", 5, "Soybean-C"),
    "SUGAR": (112000.0, 5, 0.00001, 0.1, 0.1, "USD", 5, "Sugar-C"),
    "COFFEE": (37500.0, 4, 0.0001, 0.1, 0.1, "USD", 5, "Coffee-C"),
    # owner decision 2026-11-04, read from both terminals that day (symbols_get + symbol_info, read-only, on the brain)
    "GBPJPY": (100000.0, 3, 0.001, 0.01, 0.01, "JPY", 3, "GBPJPY+"),
    "EURGBP": (100000.0, 5, 0.00001, 0.01, 0.01, "GBP", 3, "EURGBP+"),
    "EURJPY": (100000.0, 3, 0.001, 0.01, 0.01, "JPY", 3, "EURJPY+"),
    "AUDJPY": (100000.0, 3, 0.001, 0.01, 0.01, "JPY", 3, "AUDJPY+"),
    "EURAUD": (100000.0, 5, 0.00001, 0.01, 0.01, "AUD", 3, "EURAUD+"),
    "GBPAUD": (100000.0, 5, 0.00001, 0.01, 0.01, "AUD", 3, "GBPAUD+"),
    "BTCUSD": (1.0, 2, 0.01, 0.01, 0.01, "USD", 5, "BTCUSD"),
    "XAUJPY": (100.0, 0, 1.0, 0.01, 0.01, "JPY", 3, "XAUJPY+"),
    "USINDEX": (100.0, 3, 0.001, 0.1, 0.1, "USD", 5, "USDX"),
    "US30": (1.0, 2, 0.01, 0.1, 0.1, "USD", 5, "DJ30"),
}
# the terminal did not report it (symbol_info failed: BridgeUnavailable), so its terms stay placeholders
UNCONFIRMED: set[str] = set()


@pytest.mark.parametrize("key", sorted(TERMINAL_TERMS))
def test_each_instruments_terms_are_the_terminals_own(key: str) -> None:
    i = load_instruments().get(key)
    contract, digits, point, min_lot, step, ccy, triple, symbol = TERMINAL_TERMS[key]
    assert (i.contract_size, i.digits, i.min_lot, i.lot_step, i.currency_profit, i.swap_rollover3days) == (
        contract, digits, min_lot, step, ccy, triple)
    assert i.point == pytest.approx(point, rel=1e-12) and symbol in i.broker_symbols.values()


def test_an_instrument_the_terminal_confirmed_is_enabled_and_one_it_did_not_stays_a_placeholder() -> None:
    inst = load_instruments().instruments
    assert {k for k, i in inst.items() if i.enabled} == {"XAUUSD", *TERMINAL_TERMS} - REMOVED
    assert {k for k, i in inst.items() if i.verify} == UNCONFIRMED
    assert all(not inst[k].enabled for k in UNCONFIRMED)


def test_no_enabled_instrument_carries_placeholder_terms() -> None:
    assert all(not i.verify for i in load_instruments().instruments.values() if i.enabled)


# the spread ceiling in price units (max_spread_points x point) after the point correction: unchanged where only the
# point's decimals moved (XNGUSD, UST10Y); Sugar and Coffee moved from cents to dollars per lb (contract x 100), so
# the same ceiling in cents is a hundredth in dollars; Wheat and Soybean read as dollars per bushel (contract
# 1000 / 500 bushels at the minimum lot of 0.1), the same 3-cent ceiling
SPREAD_CEILING_PRICE = {"XNGUSD": 0.02, "UST10Y": 0.09, "WHEAT": 0.03, "SOYBEAN": 0.03, "SUGAR": 0.0015,
                        "COFFEE": 0.015}


@pytest.mark.parametrize("key", sorted(SPREAD_CEILING_PRICE))
def test_the_spread_ceiling_keeps_its_price_width_when_the_point_is_corrected(key: str) -> None:
    i = load_instruments().get(key)
    assert i.max_spread_points * i.point == pytest.approx(SPREAD_CEILING_PRICE[key], rel=1e-9)


def test_for_broker_lists_an_accounts_instruments_and_finds_one_by_broker_symbol() -> None:
    inst = load_instruments()
    assert {i.key for i in inst.for_broker("vantage")} == {"XAUUSD", "UST10Y", "BUND10Y", "COPPER", "WHEAT",
                                                            "SOYBEAN", "SUGAR", "COFFEE", "GBPJPY", "EURGBP",
                                                            "EURJPY", "AUDJPY", "EURAUD", "GBPAUD", "BTCUSD",
                                                            "XAUJPY", "USINDEX", "US30"}
    found = inst.by_broker_symbol("vantage", "COPPER-C")
    assert found is not None and found.key == "COPPER"
    assert inst.by_broker_symbol("icm", "NOPE") is None


def test_every_leverage_class_has_a_leverage() -> None:
    assert {i.leverage_class for i in load_instruments().instruments.values()} <= set(LEVERAGE_BY_CLASS)
    assert LEVERAGE_BY_CLASS == {"fx": 30, "gold_index": 20, "commodity": 10, "bond": 5, "crypto": 2}


def test_an_instrument_spec_is_immutable() -> None:
    gold = load_instruments().get("XAUUSD")
    assert isinstance(gold, InstrumentSpec)
    with pytest.raises(ValidationError):
        gold.magic_block = 5      # type: ignore[misc]


# --- Broker protocol stubs keep today's XAUUSD behaviour ---

def _tick(ts: str, bid: float = 2000.0) -> Tick:
    return Tick(ts_utc=pd.Timestamp(ts, tz="UTC"), bid=bid, ask=bid + 0.2)


def test_symbol_info_reports_no_profit_currency_or_triple_swap_day_unless_the_broker_does() -> None:
    info = PaperBroker().symbol_info("XAUUSD")
    assert info.currency_profit is None and info.swap_rollover3days is None
    assert SymbolInfo.model_fields["currency_profit"].default is None
    assert SymbolInfo.model_fields["swap_rollover3days"].default is None


def test_positions_without_a_symbol_still_returns_the_engines_own_symbol_only() -> None:
    b = PaperBroker()
    b.on_tick(_tick("2026-11-02 10:00"))
    b.set_quote("XAGUSD", _tick("2026-11-02 10:00", bid=30.0))
    for sym, cid in (("XAUUSD", "a"), ("XAGUSD", "b")):
        r = b.place_order(OrderIntent(client_order_id=cid, symbol=sym, side=1, lots=0.01, sl=0.0, tp=0.0, magic=1))
        assert r.ok
    assert [p.symbol for p in b.positions()] == ["XAUUSD"]
    assert [p.symbol for p in b.positions(symbol="XAGUSD")] == ["XAGUSD"]
    assert b.positions(magic_prefix=1) == b.positions()


def test_paper_ticks_many_returns_each_symbols_newer_quote_and_empty_otherwise() -> None:
    b = PaperBroker()
    b.on_tick(_tick("2026-11-02 10:00:01"))
    out = b.ticks_many(["XAUUSD", "EURUSD"], pd.Timestamp("2026-11-02 10:00", tz="UTC"))
    assert [t.bid for t in out["XAUUSD"]] == [2000.0] and out["EURUSD"] == []
    assert b.ticks_many(["XAUUSD"], pd.Timestamp("2026-11-02 10:00:01", tz="UTC")) == {"XAUUSD": []}


def test_the_bridge_client_sends_todays_positions_call_unless_a_symbol_is_asked_for() -> None:
    calls: list[tuple[object, ...]] = []

    class Recording(RemoteBroker):
        def __init__(self) -> None:    # no network: only the wire arguments are under test
            pass

        def _call(self, method: str, *args: Any) -> Any:
            calls.append((method, *args))
            return []

    b = Recording()
    b.positions(260100)
    b.positions(260100, symbol="XAGUSD")
    assert calls == [("positions", 260100), ("positions", 260100, "XAGUSD")]
    # lane L4 put ticks_many on the wire (protocol v10): one call for every symbol
    b._ticks_many_served = True
    calls.clear()
    since = pd.Timestamp("2026-11-02", tz="UTC")
    def record(method: str, *args: Any) -> Any:
        calls.append((method, *args))
        return {}
    b._call = record  # type: ignore[method-assign]
    assert b.ticks_many(["XAUUSD"], since) == {"XAUUSD": []}
    assert calls == [("ticks_many", ["XAUUSD"], since)]
