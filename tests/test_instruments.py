"""Multi-instrument demo, step 0: the frozen InstrumentSpec interface, config/instruments.yaml, and the Broker
protocol stubs (positions(symbol=None), ticks_many, SymbolInfo.currency_profit / swap_rollover3days) that keep
today's XAUUSD behaviour unchanged."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from goldbot.config import load_yaml
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
VANTAGE = ["USNote10Y", "EUB10Y", "COPPER-C", "Wheat-C", "Soybean-C", "Sugar-C", "Coffee-C"]


def test_the_instrument_file_loads_gold_and_the_26_planned_instruments() -> None:
    inst = load_instruments()
    assert len(inst.instruments) == 27
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


def test_only_gold_is_enabled_until_the_lanes_land() -> None:
    assert [k for k, i in load_instruments().instruments.items() if i.enabled] == ["XAUUSD"]


def test_every_placeholder_term_is_marked_for_the_symbol_check() -> None:
    assert all(i.verify for k, i in load_instruments().instruments.items() if k != "XAUUSD")


def test_for_broker_lists_an_accounts_instruments_and_finds_one_by_broker_symbol() -> None:
    inst = load_instruments()
    assert {i.key for i in inst.for_broker("vantage")} == {"XAUUSD", "UST10Y", "BUND10Y", "COPPER", "WHEAT",
                                                            "SOYBEAN", "SUGAR", "COFFEE"}
    found = inst.by_broker_symbol("vantage", "COPPER-C")
    assert found is not None and found.key == "COPPER"
    assert inst.by_broker_symbol("icm", "NOPE") is None


def test_every_leverage_class_has_a_leverage() -> None:
    assert {i.leverage_class for i in load_instruments().instruments.values()} <= set(LEVERAGE_BY_CLASS)
    assert LEVERAGE_BY_CLASS == {"fx": 30, "gold_index": 20, "commodity": 10, "bond": 5}


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
