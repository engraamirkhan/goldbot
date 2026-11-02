"""The four key-level vocabulary primitives (goldbot/research/level_vocab.py; sprint-3000 brief, ADDENDUM A).

Design rule: every primitive at a decision close reads only zones known by then (`key_levels`, point in time):
entries compare the bar with the zones of the PREVIOUS decision close (a zone is broken or rejected only if it existed
before the bar), filters read the book at the decision close itself.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features import key_levels as kl
from goldbot.research import level_vocab as lv

NAN = np.nan


def _zones(**cols: list[float]) -> pd.DataFrame:
    """A hand-made zone frame (the columns key_level_frame gives) for three decision bars."""
    base = {f"{s}_{f}": [NAN] * 3 for s in ("sup", "res") for f in ("price", "lo", "hi", "strength_pct", "dist_atr")}
    base.update(atr_1d=[10.0] * 3)
    base.update(cols)
    return pd.DataFrame(base)


def _bars(close: list[float], high: list[float] | None = None, low: list[float] | None = None) -> pd.DataFrame:
    return pd.DataFrame({"close": close, "high": high or close, "low": low or close})


# ------------------------------------------------------------------------------------------- level_break
def test_level_break_goes_long_on_a_close_above_the_resistance_known_at_the_previous_close():
    prev = _zones(res_lo=[NAN, 100.0, 100.0], res_hi=[NAN, 101.0, 101.0], sup_lo=[NAN, 90.0, 90.0],
                  sup_hi=[NAN, 91.0, 91.0])
    side = lv.level_break(_bars([99.0, 101.5, 100.5]), prev)
    assert side.tolist() == [0, 1, 0]                      # bar 1 closes above hi; bar 2 only inside the zone


def test_level_break_goes_short_on_a_close_below_the_support():
    prev = _zones(res_lo=[NAN, 110.0, NAN], res_hi=[NAN, 111.0, NAN], sup_lo=[NAN, 95.0, NAN], sup_hi=[NAN, 96.0, NAN])
    assert lv.level_break(_bars([97.0, 94.0, 94.0]), prev).tolist() == [0, -1, 0]   # no zone known: no break


# ------------------------------------------------------------------------------------------- level_reject
def test_level_reject_fades_a_bar_that_enters_resistance_and_closes_back_below():
    prev = _zones(res_lo=[NAN, 100.0, 100.0], res_hi=[NAN, 101.0, 101.0], sup_lo=[NAN, 90.0, 90.0],
                  sup_hi=[NAN, 91.0, 91.0])
    bars = _bars([98.0, 99.0, 99.5], high=[98.5, 100.4, 99.9], low=[97.0, 98.0, 98.0])
    assert lv.level_reject(bars, prev).tolist() == [0, -1, 0]   # bar 2 never reached the zone


def test_level_reject_buys_a_bar_that_dips_into_support_and_closes_back_above():
    prev = _zones(res_lo=[NAN, 110.0, 110.0], res_hi=[NAN, 111.0, 111.0], sup_lo=[NAN, 95.0, 95.0],
                  sup_hi=[NAN, 96.0, 96.0])
    bars = _bars([97.0, 97.0, 95.5], high=[98.0, 98.0, 97.0], low=[96.5, 95.8, 95.2])
    assert lv.level_reject(bars, prev).tolist() == [0, 1, 0]    # bar 2 closed inside: not back out


def test_level_reject_needs_the_previous_close_outside_the_zone():
    prev = _zones(res_lo=[NAN, 100.0, NAN], res_hi=[NAN, 101.0, NAN])
    bars = _bars([100.5, 99.0, 99.0], high=[100.8, 100.6, 99.0], low=[100.0, 98.0, 98.0])
    assert lv.level_reject(bars, prev).tolist() == [0, 0, 0]    # it started inside: a move out, not a rejection


# ------------------------------------------------------------------------------------------- near_level
def test_near_level_measures_to_the_zone_edge_in_atr_of_the_entry_timeframe():
    cur = _zones(res_lo=[105.0, 105.0, 120.0], res_hi=[106.0, 106.0, 121.0], sup_lo=[80.0, 99.0, 80.0],
                 sup_hi=[81.0, 100.5, 81.0], res_price=[105.5, 105.5, 120.5], sup_price=[80.5, 99.5, 80.5])
    atr_entry = np.array([2.0, 2.0, 2.0])
    got = lv.near_level(_bars([100.0, 100.0, 100.0]), cur, atr_entry, max_dist_atr=0.5)
    # bar 0: 5.0 away (2.5 ATR); bar 1: inside the support zone (0); bar 2: 19 / 20 away
    assert got.tolist() == [False, True, False]


# ------------------------------------------------------------------------------------------- room_to_level
def test_room_to_level_passes_a_long_only_when_the_next_zone_above_is_min_r_stops_away():
    cur = _zones(res_lo=[106.0, 103.0, NAN], res_hi=[107.0, 104.0, NAN], sup_lo=[90.0, 90.0, 90.0],
                 sup_hi=[91.0, 91.0, 91.0])
    side = np.array([1, 1, 1])
    stop = np.array([3.0, 3.0, 3.0])
    got = lv.room_to_level(_bars([100.0, 100.0, 100.0]), cur, side, stop, min_r=2.0)
    assert got.tolist() == [True, False, True]             # 6 >= 2 x 3; 3 < 6; no zone above: unlimited room


def test_room_to_level_counts_a_zone_the_close_sits_in_as_no_room():
    cur = _zones(res_lo=[110.0, 110.0, 110.0], res_hi=[111.0, 111.0, 111.0], sup_lo=[99.0, 99.0, 90.0],
                 sup_hi=[100.5, 100.5, 91.0])
    got_long = lv.room_to_level(_bars([100.0] * 3), cur, np.array([1, 1, 1]), np.full(3, 1.0), min_r=2.0)
    got_short = lv.room_to_level(_bars([100.0] * 3), cur, np.array([-1, -1, -1]), np.full(3, 1.0), min_r=2.0)
    assert got_long.tolist() == [False, False, True]       # the support zone reaches above the close: no room up
    assert got_short.tolist() == [False, False, True]      # and the short starts inside it: no room down


def test_room_to_level_fails_closed_while_the_book_warms_up_and_for_no_side():
    cur = _zones(atr_1d=[NAN, 10.0, 10.0])
    got = lv.room_to_level(_bars([100.0] * 3), cur, np.array([1, 0, -1]), np.full(3, 1.0), min_r=2.0)
    assert got.tolist() == [False, False, True]


# ------------------------------------------------------------------------------------------- the context
@pytest.fixture(scope="module")
def bars_1m() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2025-01-06", "2025-03-22", ticks_per_minute=1, seed=5))


def test_the_context_is_the_key_level_frame_at_each_decision_close_in_one_pass(bars_1m):
    h1 = mid(resample_bars(bars_1m, "1h"))
    h1 = h1[h1["visible_at"] <= bars_1m["visible_at"].iloc[-1]].reset_index(drop=True)
    specs = [{"entry": {"type": "level_break", "params": {"min_strength_pct": 50, "min_tf": "1h"}},
              "filters": [{"type": "room_to_level", "params": {"min_r": 2.0}}]},
             {"entry": {"type": "donchian_break", "params": {"n": 10}},
              "filters": [{"type": "near_level", "params": {"max_dist_atr": 0.5, "min_strength_pct": 75}}]}]
    filters = lv.filters_for(specs)
    assert set(filters) == {kl.LevelFilter(50.0, "1h"), kl.LevelFilter(lv.ROOM_MIN_STRENGTH_PCT, None),
                            kl.LevelFilter(75.0, None)}
    ctx = lv.LevelContext.build(bars_1m, h1, filters)
    full = kl.key_level_frame(bars_1m, min_strength_pct=50.0, min_tf="1h")
    vis = pd.to_datetime(bars_1m["visible_at"], utc=True)
    for i in (300, len(h1) - 1):
        row = full[vis <= h1["visible_at"].iloc[i]].iloc[-1]
        got = ctx.at(kl.LevelFilter(50.0, "1h")).iloc[i]
        for c in ("res_price", "res_lo", "sup_hi", "res_strength_pct", "atr_1d"):
            assert got[c] == row[c] or (np.isnan(got[c]) and np.isnan(row[c]))
        prev = ctx.prev(kl.LevelFilter(50.0, "1h")).iloc[i]
        assert prev["res_price"] == ctx.at(kl.LevelFilter(50.0, "1h")).iloc[i - 1]["res_price"] or \
            np.isnan(prev["res_price"])
    assert np.isnan(ctx.prev(kl.LevelFilter(50.0, "1h"))["res_price"].iloc[0])


def test_frames_for_several_filters_in_one_pass_equal_one_frame_per_filter(bars_1m):
    fs = [kl.LevelFilter(0.0, None), kl.LevelFilter(75.0, "4h")]
    at = pd.to_datetime(bars_1m["visible_at"], utc=True).iloc[::97]
    many = kl.key_level_frames(bars_1m, fs, at=at)
    for f in fs:
        one = kl.key_level_frame(bars_1m, min_strength_pct=f.min_strength_pct, min_tf=f.min_tf)
        pd.testing.assert_frame_equal(many[f].drop(columns=["available_utc"]).reset_index(drop=True),
                                      one.iloc[::97].drop(columns=["ts_utc", "available_utc"]).reset_index(drop=True),
                                      check_exact=True)


def test_every_level_block_of_the_sprint3000_catalogue_maps_to_a_zone_view():
    from pathlib import Path

    import yaml
    cat = yaml.safe_load(Path("config/research/sprint3000_catalogue.yaml").read_text())
    specs = cat["specs"]
    level = [s for s in specs if s["entry"]["type"] in lv.ENTRY_TYPES
             or any(f["type"] in lv.FILTER_TYPES for f in s.get("filters") or [])]
    assert len(level) == 379                                    # prereg: 379 level-based specs
    views = lv.filters_for(specs)
    assert kl.LevelFilter(lv.ROOM_MIN_STRENGTH_PCT, None) in views
    assert all(f.min_tf is None or f.min_tf in kl.TF_RANK for f in views)
