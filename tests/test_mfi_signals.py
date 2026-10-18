"""Money-flow signals from the owner's notes (goldbot/features/mfi_signals.py), on hand-made lines."""
from __future__ import annotations

import numpy as np

from goldbot.features.mfi_signals import mfi_signals


def _sig(m: list[float], pivot_len: int = 2):
    x = np.array(m, dtype=float)
    return mfi_signals(x, x + 1, x - 1, pivot_len)


def test_regime_is_bull_above_60_bear_below_40_and_ranging_between():
    s = _sig([30, 39.9, 40, 50, 60, 60.1, 90])
    assert s.mfi_regime.tolist() == [-1, -1, 0, 0, 0, 1, 1]


def test_entering_overbought_and_oversold_each_draw_one_circle():
    s = _sig([70, 85, 90, 50, 15, 10, 50])
    assert s.mfi_extreme_circle.tolist() == [0, 1, 0, 0, -1, 0, 0]


def test_falling_back_into_range_from_overbought_is_a_second_circle_short_idea():
    s = _sig([70, 85, 90, 78, 70])
    assert s.mfi_double_circle.tolist() == [0, 0, 0, -1, 0]


def test_rising_back_into_range_from_oversold_is_a_second_circle_long_idea():
    s = _sig([30, 15, 10, 22, 30])
    assert s.mfi_double_circle.tolist() == [0, 0, 0, 1, 0]


def test_crossing_50_is_signed_by_direction():
    s = _sig([40, 55, 45, 60])
    assert s.mfi_cross50.tolist() == [0, 1, -1, 1]


def test_bull_pullback_that_holds_above_40_then_turns_up_signals_long_once():
    s = _sig([55, 70, 85, 65, 50, 45, 52, 58])
    assert s.mfi_pullback_hold.tolist() == [0, 0, 0, 0, 0, 0, 1, 0]


def test_pullback_that_breaks_below_40_is_not_a_hold():
    s = _sig([55, 70, 85, 65, 45, 35, 42, 50])
    assert (s.mfi_pullback_hold == 0).all()


def test_bear_pullback_that_holds_below_60_then_turns_down_signals_short():
    s = _sig([45, 30, 15, 35, 50, 55, 48, 42])
    assert s.mfi_pullback_hold.tolist() == [0, 0, 0, 0, 0, 0, -1, 0]


def test_bearish_divergence_is_stamped_on_the_confirming_bar_not_the_pivot_bar():
    # price pivot highs at bars 2 and 8 (second higher); the line is lower at the second
    price = np.array([1, 2, 5, 2, 1, 2, 3, 4, 6, 4, 2, 1, 1], dtype=float)
    m = np.array([50, 60, 80, 60, 50, 55, 60, 65, 70, 60, 50, 45, 45], dtype=float)
    s = mfi_signals(m, price, price - 0.5, pivot_len=2)
    assert s.mfi_divergence.tolist()[8] == 0 and s.mfi_divergence.tolist()[10] == -1
