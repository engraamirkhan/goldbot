"""Multi-instrument costs (plan 2026-11-02, lane L1: "Generalise CONTRACT_OZ execution/costs.py, min_lot.py"): the
commission per lot is spread over the instrument's own contract, never a fixed 100 oz; XAUUSD tables read as before."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from goldbot.execution import costs
from goldbot.execution.costs import CostTable, SlippageStat, SpreadStat, prior_extra_cost_usd
from goldbot.research import min_lot


def _table(**kw: object) -> CostTable:
    base: dict[str, object] = dict(account_id="icm-probe", built_utc=pd.Timestamp("2026-11-02", tz="UTC"),
                                   spread={"london": SpreadStat(median=0.2, p90=0.3, n=100)},
                                   slippage={"london:market": SlippageStat(mean=0.05, n=60, from_prior=False)},
                                   commission_per_lot_side_usd=3.5, slippage_prior_usd=0.15)
    return CostTable.model_validate({**base, **kw})


def test_neither_module_keeps_a_contract_constant() -> None:
    assert not hasattr(costs, "CONTRACT_OZ") and not hasattr(min_lot, "CONTRACT_OZ")


def test_a_gold_table_spreads_commission_over_100_oz_as_before() -> None:
    t = _table()
    assert t.contract_size == 100.0 and t.symbol == "XAUUSD"
    assert t.round_trip_usd_per_oz("london") == pytest.approx(0.2 + 2 * 0.05 + 2 * 3.5 / 100.0)
    assert t.extra_cost_usd() == pytest.approx(2 * 0.05 + 2 * 3.5 / 100.0)


def test_a_table_saved_before_the_instrument_fields_loads_as_gold(tmp_path: object) -> None:
    raw = json.loads(_table().model_dump_json())
    raw.pop("contract_size")
    raw.pop("symbol")
    t = CostTable.model_validate(raw)
    assert (t.symbol, t.contract_size) == ("XAUUSD", 100.0)


def test_a_eurusd_table_spreads_commission_over_its_100000_units() -> None:
    t = _table(symbol="EURUSD", contract_size=100_000.0, spread={"london": SpreadStat(median=0.00002, p90=0.00004, n=9)},
               slippage={"london:market": SlippageStat(mean=0.00001, n=60, from_prior=False)})
    assert t.round_trip_usd_per_oz("london") == pytest.approx(0.00002 + 2 * 0.00001 + 2 * 3.5 / 100_000.0)


def test_the_prior_extra_cost_takes_the_instruments_contract() -> None:
    assert prior_extra_cost_usd(0.15, 3.5) == pytest.approx(2 * 0.15 + 2 * 3.5 / 100.0)          # gold default
    assert prior_extra_cost_usd(0.00001, 3.5, contract_size=100_000.0) == pytest.approx(2 * 0.00001 + 7.0 / 100_000)


def test_the_min_lot_spec_defaults_to_gold_and_reads_any_instrument_from_the_registry() -> None:
    assert min_lot.LotSpec().contract_oz == 100.0
    jp = min_lot.LotSpec.for_instrument("JP225")
    assert (jp.contract_oz, jp.volume_min, jp.volume_step) == (1.0, 1.0, 1.0)


def test_only_the_gold_table_is_published_for_research() -> None:
    t = _table(symbol="EURUSD", contract_size=100_000.0, swap_long_usd_per_lot=-5.0, swap_short_usd_per_lot=1.0)
    pub, why = costs.publishable(t, "icm")
    assert pub is None and "EURUSD" in why
    pub, why = costs.publishable(_table(swap_long_usd_per_lot=-5.0, swap_short_usd_per_lot=1.0), "icm")
    assert pub is not None and why == "ok"
