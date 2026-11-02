"""Trading-safety review of PR #307, M3 (a): a registry entry whose terms contradict each other fails at load.

* the gold correlation group trades at the gold leverage (`leverage_class: gold_index`);
* a six-letter pair whose last three letters are a currency (XAUEUR, USDJPY) is quoted in that currency, so its
  `currency_profit` (the P&L currency the RiskGate converts from) must be it.
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from goldbot.instruments import InstrumentSpec, load_instruments


def _spec(**kw: Any) -> InstrumentSpec:
    base: dict[str, Any] = dict(key="XAUEUR", broker_symbols={"icm": "XAUEUR"}, store_symbol="XAUEUR",
                                asset_class="metal", correlation_group="gold", magic_block=3, contract_size=100.0,
                                digits=2, point=0.01, min_lot=0.01, lot_step=0.01, currency_profit="EUR",
                                swap_rollover3days=3, session_profile="metals", news_currencies=("USD", "EUR"),
                                leverage_class="gold_index")
    base.update(kw)
    return InstrumentSpec(**base)


def test_the_shipped_registry_obeys_the_rules():
    assert load_instruments().instruments


def test_gold_group_requires_the_gold_leverage_class():
    with pytest.raises(ValidationError, match="gold_index"):
        _spec(leverage_class="commodity")


@pytest.mark.parametrize("key, cur, ok", [("XAUEUR", "EUR", True), ("XAUEUR", "USD", False), ("USDJPY", "JPY", True),
                                          ("USDJPY", "USD", False), ("COPPER", "USD", True), ("US500", "USD", True)])
def test_currency_profit_matches_the_symbols_quote(key, cur, ok):
    kw: dict[str, Any] = dict(key=key, broker_symbols={"icm": key}, store_symbol=key, currency_profit=cur)
    if key not in ("XAUEUR",):
        kw.update(correlation_group="fx", leverage_class="fx")
    if ok:
        assert _spec(**kw).currency_profit == cur
    else:
        with pytest.raises(ValidationError, match="currency_profit"):
            _spec(**kw)
