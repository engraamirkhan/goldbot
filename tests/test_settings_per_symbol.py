"""Multi-instrument settings (plan 2026-11-02, lane L1: "point, price_tolerance, lot_fit_spread_points and the
triple-swap weekday per symbol"). XAUUSD reads the configured values as before; every other instrument reads its own
terms from config/instruments.yaml, or nothing (never gold's number in another instrument's units)."""
from __future__ import annotations

import pytest

from goldbot.config import load_settings


def test_gold_keeps_every_configured_value() -> None:
    s = load_settings()
    assert s.point_for("XAUUSD") == 0.01
    assert s.price_tolerance_for("XAUUSD") == s.ledger.price_tolerance
    assert s.swap_triple_weekday_for("XAUUSD") == s.costs.swap_triple_weekday
    assert s.lot_fit_spread_points_for("XAUUSD") == s.research.lot_fit_spread_points
    for gold in ("XAUUSD+", "XAUUSD247"):          # a broker's gold symbol is gold
        assert s.point_for(gold) == 0.01 and s.swap_triple_weekday_for(gold) == s.costs.swap_triple_weekday


def test_another_instrument_reads_its_own_point_and_one_point_of_price_tolerance() -> None:
    s = load_settings()
    assert s.point_for("EURUSD") == 0.00001 and s.price_tolerance_for("EURUSD") == 0.00001
    assert s.point_for("USDJPY") == 0.001 and s.price_tolerance_for("Wheat-C") == s.point_for("WHEAT")


def test_the_triple_swap_weekday_is_the_instruments_own() -> None:
    s = load_settings()
    assert s.swap_triple_weekday_for("EURUSD") == 2                # MT5 3 = Wednesday
    assert s.swap_triple_weekday_for("US500") == 4                 # MT5 5 = Friday


def test_the_lot_fit_spread_prior_is_gold_only_unless_configured_per_symbol() -> None:
    s = load_settings()
    assert s.lot_fit_spread_points_for("EURUSD") is None
    s2 = s.model_copy(update={"research": s.research.model_copy(
        update={"lot_fit_spread_points_by_symbol": {"EURUSD": 2.0}})})
    assert s2.lot_fit_spread_points_for("EURUSD") == 2.0


def test_an_unknown_symbol_is_refused_never_given_golds_terms() -> None:
    s = load_settings()
    for fn in (s.point_for, s.price_tolerance_for, s.swap_triple_weekday_for, s.lot_fit_spread_points_for):
        with pytest.raises(KeyError):
            fn("NOPE")
