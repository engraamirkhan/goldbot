"""Multi-instrument sizing (plan 2026-11-02, lane L1): "P&L currency sizing.py -> account currency per quote via
loss_per_lot, refuse without it", and "XAUUSD paths byte-identical".

A non-gold instrument's P&L is in its profit currency (USDJPY: JPY, DE40: EUR, UK100: GBP), so one lot's loss at the
stop in the account currency is stop x contract x (account currency per unit of profit currency), and that conversion
is the broker's own (`loss_per_lot` over a 1.0 move, per contract unit). Without it the trade is never sized.
"""
from __future__ import annotations

import math

import pytest

from goldbot.risk import sizing
from goldbot.risk.gate import RiskLimits

# --- the conversion ---

def test_a_jpy_quoted_instrument_without_the_brokers_loss_per_lot_is_refused() -> None:
    assert sizing.instrument_fx("GBP", "JPY", None) is None
    assert sizing.instrument_fx("USD", "JPY", None) is None          # never the USD account's 1


def test_a_jpy_quoted_instrument_is_converted_by_the_brokers_figure() -> None:
    # USDJPY on a GBP account: 1 JPY ~ 0.0052 GBP. loss_per_lot over 1.0 (one yen a dollar) on 100,000 = 520 GBP
    per_quote = sizing.acct_per_quote(520.0, 100_000.0)
    assert per_quote == pytest.approx(0.0052)
    assert sizing.instrument_fx("GBP", "JPY", per_quote) == pytest.approx(0.0052)


def test_a_non_gold_instrument_needs_the_brokers_figure_even_in_its_own_currency() -> None:
    """UK100 on a GBP account is 1 GBP per GBP; the figure is still required (it is the check that the declared profit
    currency and contract are what the broker trades), and one that disagrees by more than 1% is refused."""
    assert sizing.instrument_fx("GBP", "GBP", None) is None
    assert sizing.instrument_fx("GBP", "GBP", 1.0) == 1.0
    assert sizing.instrument_fx("GBP", "GBP", 1.2) is None


@pytest.mark.parametrize("acct, profit, fig", [("", "JPY", 0.005), ("GBP", None, 0.005), ("GBP", "", 0.005),
                                               ("GBP", "JPY", 0.0), ("GBP", "JPY", -1.0), ("GBP", "JPY", math.nan),
                                               ("GBP", "JPY", math.inf)])
def test_an_unknown_currency_or_an_unusable_figure_is_refused(acct: str, profit: str | None, fig: float) -> None:
    assert sizing.instrument_fx(acct, profit, fig) is None


@pytest.mark.parametrize("loss, contract", [(None, 100_000.0), (0.0, 100_000.0), (-5.0, 100_000.0),
                                            (math.nan, 100_000.0), (520.0, 0.0)])
def test_no_per_quote_rate_without_a_positive_loss_per_lot_and_contract(loss: float | None, contract: float) -> None:
    assert sizing.acct_per_quote(loss, contract) is None


# --- sizing a JPY instrument end to end ---

def _limits() -> RiskLimits:
    return RiskLimits(risk_per_trade=0.005, max_risk_per_trade=0.01, multiplier_bounds=(0.25, 1.5))


def test_usdjpy_is_sized_to_its_target_risk_in_the_account_currency() -> None:
    fx = sizing.instrument_fx("GBP", "JPY", sizing.acct_per_quote(520.0, 100_000.0))
    assert fx is not None
    eff = sizing.effective_risk(_limits(), False, 1.0)
    # a 0.30 yen stop on 100,000 USDJPY = 30,000 JPY a lot = 156 GBP; 0.5% of 10,000 GBP = 50 GBP -> 0.32 lot
    sz = sizing.size_lots(equity=10_000.0, risk=eff, stop_distance=0.30, contract_oz=100_000.0, volume_min=0.01,
                          volume_step=0.01, volume_max=100.0, acct_per_usd=fx)
    assert sz.lots == pytest.approx(0.32) and sz.risk_per_lot == pytest.approx(156.0)
    assert sz.realised_risk == pytest.approx(0.32 * 156.0 / 10_000.0) and not sz.refused


# --- XAUUSD byte-identical ---

@pytest.mark.parametrize("cur, fig", [("USD", None), ("USD", 1.004), ("USD", 1.2), ("GBP", 0.75), ("GBP", None),
                                      ("", 0.75), (" usd ", None), ("EUR", math.nan)])
def test_gold_conversion_is_unchanged(cur: str, fig: float | None) -> None:
    """account_fx keeps its rule (a USD account is 1 by definition) for XAUUSD; the strict rule is for the others."""
    expected = {("USD", None): 1.0, ("USD", 1.004): 1.0, ("USD", 1.2): None, ("GBP", 0.75): 0.75, ("GBP", None): None,
                ("", 0.75): None, (" usd ", None): 1.0, ("EUR", math.nan): None}[(cur, fig)]
    assert sizing.account_fx(cur, fig) == expected


def test_gold_lots_are_unchanged_to_the_last_bit() -> None:
    eff = sizing.effective_risk(_limits(), False, 1.0)
    sz = sizing.size_lots(equity=1000.0, risk=eff, stop_distance=7.5, contract_oz=100.0, volume_min=0.01,
                          volume_step=0.01, volume_max=50.0, acct_per_usd=0.75)
    assert (sz.lots_raw, sz.lots, sz.realised_risk, sz.refused, sz.risk_per_lot) == (
        1000.0 * 0.005 * 1.0 / (7.5 * 100.0 * 0.75), 0.01, 0.01 * 7.5 * 100.0 * 0.75 / 1000.0, False, 7.5 * 100.0 * 0.75)


def test_min_lot_feasibility_of_a_jpy_instrument_refuses_without_the_brokers_figure() -> None:
    from goldbot.research.min_lot import LotSpec, min_lot_feasibility
    lot = LotSpec.for_instrument("USDJPY")
    refused = min_lot_feasibility(price=150.0, stop_distance=0.30, risk_fraction=0.005, equity=10_000.0, lot=lot,
                                  account_currency="USD", profit_currency="JPY")
    assert refused.allowed is False and refused.reason == "fx_conversion_unavailable"
    sized = min_lot_feasibility(price=150.0, stop_distance=0.30, risk_fraction=0.005, equity=10_000.0, lot=lot,
                                account_currency="USD", profit_currency="JPY", acct_per_usd=1 / 150.0)
    assert sized.allowed is True and sized.lots == pytest.approx(0.25)    # 50 USD / (0.30 x 100,000 / 150) a lot
