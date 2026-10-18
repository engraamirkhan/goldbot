"""Box strategy features and order blocks (goldbot/features/smc_boxes.py): hand-made bars and boxes."""
from __future__ import annotations

import numpy as np

from goldbot.features import smc_boxes as B

BOX = (np.array([110.0]), np.array([100.0]))        # one box 100-110, height 10


def _run(closes, highs=None, lows=None, boxes=None, **kw):
    c = np.asarray(closes, dtype=float)
    h = np.asarray(highs if highs is not None else c + 0.5, dtype=float)
    lo = np.asarray(lows if lows is not None else c - 0.5, dtype=float)
    boxes = boxes or [BOX] * len(c)
    return B.sr_box_features(h, lo, c, np.full(len(c), 2.0), boxes, np.full(len(c), 50.0), np.full(len(c), 60.0), **kw)


def test_a_close_20_percent_through_the_box_from_below_is_a_break():
    f = _run([95, 102.5])                                  # 2.5 into a 10 high box = 25%
    assert f.sr_break_depth.iloc[1] == 0.25 and f.sr_break.iloc[1] == 1 and f.sr_rip.iloc[1] == 0


def test_a_close_only_10_percent_through_is_not_a_break_but_a_rip_when_the_wick_touched():
    f = _run([95, 101.0], highs=[95.5, 104.0])
    assert f.sr_break.iloc[1] == 0 and f.sr_break_depth.iloc[1] == 0.1
    assert f.sr_rip.iloc[1] == -1                          # touched the box from below, did not get through


def test_break_depth_is_a_parameter_and_downward_breaks_are_negative():
    assert _run([115, 108.0], min_depth=0.3).sr_break.iloc[1] == 0
    f = _run([115, 107.0], min_depth=0.3)                  # 3 of 10 down into the box from above
    assert f.sr_break_depth.iloc[1] == -0.3 and f.sr_break.iloc[1] == -1


def test_rip_from_above_is_a_long_idea():
    f = _run([115, 111.0], lows=[114.5, 109.0])
    assert f.sr_rip.iloc[1] == 1 and f.sr_break.iloc[1] == 0


def test_next_box_distances_and_the_far_edge_of_the_box_just_broken_are_in_atr():
    boxes = [(np.array([110.0, 140.0]), np.array([100.0, 130.0]))] * 2
    f = _run([95, 104.0], boxes=boxes)
    assert f.next_box_up_atr.iloc[1] == 13.0 and np.isnan(f.next_box_down_atr.iloc[1])
    assert f.broken_box_far_edge_atr.iloc[1] == 2.0         # (104 - 100) / ATR 2: the stop sits back at the box's bottom


def test_golden_zone_flag_is_one_between_the_two_retracement_levels():
    f = _run([55.0, 70.0], boxes=[(np.array([]), np.array([]))] * 2)
    assert f.in_golden_zone.tolist() == [1, 0]


def test_mfi_regime_is_attached_when_given():
    assert _run([95, 102.5], mfi_regime=np.array([1, -1])).mfi_regime.tolist() == [1, -1]


def _ob_events(n, bull_at=None):
    names = [f"{k}_{d}_{s}" for k in ("bos", "choch") for d in ("bull", "bear") for s in ("internal", "swing")]
    ev = {k: np.zeros(n, dtype=bool) for k in names}
    if bull_at is not None:
        ev["bos_bull_internal"][bull_at] = True
    return ev


def _zigzag():
    up = 100.0 + np.arange(7) * 1.5                          # bars 0-6 rise to a peak at bar 6
    down = up[-1] - 2.0 * np.arange(1, 7)                    # bars 7-12 fall to the low at bar 12
    rise = down[-1] + 1.5 * np.arange(1, 28)                 # then rise again
    c = np.concatenate([up, down, rise])
    return c + 1.0, c - 1.0, c


def test_bull_order_block_is_the_lowest_bar_between_the_broken_pivot_and_the_break_and_dies_on_a_close_below():
    h, lo, c = _zigzag()
    n = len(c)
    ob = B.order_blocks(h, lo, c, _ob_events(n, 20), internal_len=3, swing_len=20, atr_len=5)
    assert ob.ob_internal_bull_n.iloc[19] == 0                # nothing before the break bar
    assert ob.ob_internal_bull_n.iloc[20] == 1
    assert ob.ob_internal_bull_btm.iloc[20] == lo[12] and ob.ob_internal_bull_top.iloc[20] == h[12]
    c2 = c.copy()
    c2[25] = lo[12] - 5                                       # closes under the block's bottom
    ob2 = B.order_blocks(h, lo, c2, _ob_events(n, 20), internal_len=3, swing_len=20, atr_len=5)
    assert ob2.ob_internal_bull_break.iloc[25] == 1 and ob2.ob_internal_bull_n.iloc[25] == 0
