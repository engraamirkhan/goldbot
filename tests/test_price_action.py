"""Opt-in price-action library (goldbot/features/price_action.py). Synthetic bars and hand-worked expectations only.

Pinned: the point-in-time property (prefix invariance on every column, a higher-timeframe level only after its bucket
closed, the registry's lookahead check), each rule against a crafted path (level areas, triangles, head and shoulders,
double tops, trendlines, Fibonacci, ABCD, event areas, channel, volume veto, candlesticks and their confirmed
variants), and the opt-in wiring (default feature set and version unchanged, a plain tail refused)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.features import price_action as PA
from goldbot.features.smc_replica import equal_levels

P2 = PA.PAParams(pivot_len=2)
P2C = PA.PAParams(pivot_len=2, eq_atr_len=14)       # short paths: equal pivots judged on a 14-bar ATR


def _walk(n: int, seed: int = 7, sd: float = 0.002, freq: str = "15min") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 2000 * np.exp(np.cumsum(rng.normal(0, sd, n)))
    o = np.concatenate(([2000.0], c[:-1])) * np.exp(rng.normal(0, sd / 4, n))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, sd / 2, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, sd / 2, n)))
    ts = pd.date_range("2024-01-02", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": h, "low": lo, "close": c,
                         "tick_count": rng.integers(50, 500, n).astype(float)})


def _path(points: list[float], flat: int = 30, start: float | None = None) -> np.ndarray:
    """Closes moving 1.0 per bar between the waypoints, after `flat` bars at the first (or `start`) value."""
    out = [float(start if start is not None else points[0])] * flat
    for a, b in zip(points[:-1], points[1:]):
        k = int(round(abs(b - a)))
        out += list(np.linspace(a, b, k + 1)[1:]) if k else []
    return np.array(out)


def _bars(close: np.ndarray, wick: float = 0.2, freq: str = "4h", vol: np.ndarray | None = None) -> pd.DataFrame:
    o = np.concatenate(([close[0]], close[:-1]))
    ts = pd.date_range("2024-01-01", periods=len(close), freq=freq, tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": np.maximum(o, close) + wick,
                         "low": np.minimum(o, close) - wick, "close": close,
                         "tick_count": vol if vol is not None else np.full(len(close), 100.0)})


def _first(x: pd.Series, value: int) -> int:
    hit = np.flatnonzero(x.to_numpy() == value)
    assert len(hit), f"{x.name} never {value}"
    return int(hit[0])


# --------------------------------------------------------------------------- point in time
@pytest.mark.parametrize("params,freq", [(PA.DEFAULT_PARAMS, "15min"), (PA.PAParams(swing_basis="close"), "15min"),
                                         (P2, "1h")])
def test_no_column_changes_when_later_bars_are_appended(params: PA.PAParams, freq: str):
    df = _walk(4000, freq=freq)
    full = PA.price_action_frame(df, params)
    assert list(full.columns) == list(PA.COLUMNS)
    for k in (450, 2222, 3999):
        part = PA.price_action_frame(df.iloc[:k], params)
        pd.testing.assert_frame_equal(part.reset_index(drop=True), full.iloc[:k].reset_index(drop=True),
                                      check_exact=True)


# crafted 4h paths on which the rare patterns fire (pivot_len 2)
CRAFTED: dict[str, list[float]] = {
    "head_and_shoulders": [100, 105, 100, 110, 100, 105, 100, 99, 92, 99.5, 80],
    "inverse_head_and_shoulders": [110, 105, 110, 100, 110, 105, 110, 111, 118, 110.5, 130],
    "triple_top": [100, 110, 104, 110, 104, 110, 103, 95],
    "triple_bottom": [110, 100, 106, 100, 106, 100, 107, 115],
    "descending_triangle": [90, 104, 100, 110, 100, 106, 100, 103, 95, 99.7, 90],
    "ascending_triangle": [120, 106, 110, 100, 110, 104, 110, 107, 115, 110.3, 120],
    "symmetrical_triangle": [90, 112, 94, 110, 97, 107, 100, 104, 112, 104.3, 115],
    "abcd": [100, 110, 105, 112, 108, 120],
    "down_trendline": [120, 112, 100, 108, 97, 104, 96, 115],
}


def _crafted_frames() -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    out = []
    for path in CRAFTED.values():
        df = _bars(_path(path, flat=30, start=path[0]))
        out.append((df, PA.price_action_frame(df, P2C, tf="4h")))
    channel = 100 + 0.5 * np.arange(200) + np.tile([0.0, 0.6, -0.3, 0.2], 50)
    out.append((_bars(channel), PA.price_action_frame(_bars(channel), P2C, tf="4h")))
    return out


def test_crafted_patterns_fire_and_are_prefix_invariant():
    fired = {name: PA.price_action_frame(_bars(_path(p, flat=30, start=p[0])), P2C, tf="4h")
             for name, p in CRAFTED.items()}
    assert (fired["head_and_shoulders"]["pa_hs_break"] == -1).any()
    assert (fired["inverse_head_and_shoulders"]["pa_hs_break"] == 1).any()
    assert (fired["triple_top"]["pa_tpl"] == -1).any() and (fired["triple_bottom"]["pa_tpl"] == 1).any()
    assert (fired["descending_triangle"]["pa_tri_desc"] == 1).any()
    assert (fired["ascending_triangle"]["pa_tri_asc"] == 1).any()
    assert (fired["ascending_triangle"]["pa_tri_breakout_close"] == 1).any()
    assert (fired["symmetrical_triangle"]["pa_tri_sym"] == 1).any()
    assert (fired["abcd"]["pa_abcd_d"] == 1).any()
    for df, full in _crafted_frames():
        for k in range(40, len(df), 7):
            part = PA.price_action_frame(df.iloc[:k], P2C, tf="4h")
            pd.testing.assert_frame_equal(part.reset_index(drop=True), full.iloc[:k].reset_index(drop=True),
                                          check_exact=True)


def test_every_column_varies_over_the_test_frames():
    frames = [f for _, f in _crafted_frames()]
    frames += [PA.price_action_frame(_walk(6000, freq=q, seed=s), P2) for q, s in (("15min", 7), ("1h", 11))]
    allf = pd.concat(frames, ignore_index=True)
    constant = [c for c in PA.COLUMNS if allf[c].nunique(dropna=True) < 2]
    assert constant == []


def test_preregistration_groups_partition_the_columns_within_the_group_cap():
    groups = PA.PREREG_GROUPS
    cols = [c for g in groups.values() for c in g]
    assert sorted(cols) == sorted(PA.COLUMNS) and len(cols) == len(set(cols))
    assert max(len(g) for g in groups.values()) <= 39 and len(groups) == 6


def test_a_higher_timeframe_bucket_is_available_only_from_the_row_that_closes_it():
    df = _walk(40, freq="15min")
    ts = pd.DatetimeIndex(df["ts_utc"])
    close_ns = PA.epoch_ns(ts) + 900 * 10**9
    h, lo, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    b = PA._buckets(ts, close_ns, h, lo, c, "1h")
    assert b.avail.tolist() == [3, 7, 11, 15, 19, 23, 27, 31, 35, 39]      # the 4th 15m bar closes each hour
    short = PA._buckets(ts[:38], close_ns[:38], h[:38], lo[:38], c[:38], "1h")
    assert short.avail[-1] == 38                                          # still open: never used in this frame
    assert b.high[0] == h[:4].max() and b.close[0] == c[3]


def test_lookahead_check_reports_no_column():
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.features.registry import FULL_HISTORY
    from goldbot.research.pipeline import build_decision_frame, lookahead_check
    names = PA.enable()
    ticks = synthetic_ticks("2025-03-03", "2025-04-04", ticks_per_minute=2, seed=5)
    bars = resample_bars(ticks_to_1m(ticks), "15m").reset_index(drop=True)
    ctx = {FULL_HISTORY: True}
    _, full = build_decision_frame(bars, None, names, ctx)
    assert set(PA.COLUMNS) <= set(full.columns)                            # every one of the columns is compared
    out = lookahead_check(bars, None, feature_names=names, ctx=ctx, full=full)
    assert out["lookahead_columns"] == [] and out["columns_checked"] == full.shape[1] - 1 >= len(PA.COLUMNS)


# --------------------------------------------------------------------------- opt-in wiring
def test_enable_registers_three_opt_in_features_without_touching_the_default_set():
    from goldbot.features import registry
    from goldbot.features.registry import FEATURES, NEEDS_STATE, build_features
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, OPT_IN_FEATURES
    before = registry.feature_version(DEFAULT_FEATURE_NAMES)
    names = PA.enable()
    assert PA.enable() == names and set(names) <= set(OPT_IN_FEATURES)
    assert all(FEATURES[n].family == PA.FAMILY and NEEDS_STATE in FEATURES[n].tags for n in names)
    assert [n for n in FEATURES if n not in OPT_IN_FEATURES] == DEFAULT_FEATURE_NAMES
    assert registry.feature_version(DEFAULT_FEATURE_NAMES) == before
    with pytest.raises(ValueError, match="needs a carried state"):
        build_features(_walk(300), [PA.NAME])                             # a plain tail is refused
    with pytest.raises(ValueError, match="already enabled"):
        PA.enable(PA.PAParams(pivot_len=3))
    cols = {n: list(FEATURES[n].fn(_walk(300), {}).columns) for n in names}
    assert cols[PA.NAME] == list(PA.LEVEL_COLUMNS) and cols[PA.NAME_CANDLES] == list(PA.CANDLE_COLUMNS)
    assert registry.signed_neutral("pa_cs_engulf_conf") == 0.0 and registry.signed_neutral("pa_cs_doji") is None


def test_params_tag_is_empty_for_defaults_and_set_otherwise():
    assert PA.PAParams().tag() == "" and PA.PAParams(pivot_len=3).tag() != ""


# --------------------------------------------------------------------------- levels
def test_a_level_is_an_area_broken_by_a_close_beyond_it_then_retested_from_the_other_side():
    # 4h bars: one swing high at 110 (pivot_len 2), price falls back, then closes through it and comes back to it
    close = _path([100, 110, 104, 116, 110.3, 118], start=100)
    df = _bars(close)
    out = PA.price_action_frame(df, P2, tf="4h")
    brk = _first(out["pa_level_break"], 1)
    assert close[brk] > 110.2 and close[brk - 1] <= 110.2 + 0.15 * 2      # close beyond the area's top
    rt = _first(out["pa_level_retest"], 1)
    assert rt > brk and df["low"].iloc[rt] <= 110.2 + 0.5 and close[rt] > 110.2
    assert out["pa_room_long_atr"].iloc[brk - 3] > 0                      # resistance above before the break


def test_event_area_is_booked_only_after_the_impulse_ends():
    close = _path([100, 100, 130, 130], flat=40)
    df = _bars(close, wick=0.3)
    h, lo, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    a = PA.wilder_atr(df, 14).to_numpy()
    ev = PA._event_areas(h, lo, c, a, PA.DEFAULT_PARAMS)
    assert len(ev) == 1
    row, zl, zh, base = ev[0]
    move = np.abs(c - np.r_[[np.nan] * 5, c[:-5]]) >= 4 * np.r_[[np.nan] * 5, a[:-5]]
    assert move[row - 1] and not move[row]                                # registered on the first quiet bar
    assert zl == pytest.approx(99.7) and zh == pytest.approx(100.3 + 1.0, abs=1.0)
    assert PA._event_areas(h[:row], lo[:row], c[:row], a[:row], PA.DEFAULT_PARAMS) == []


def test_volume_veto_needs_high_volume_near_a_level():
    df = _walk(600, freq="4h")
    df.loc[400, "tick_count"] = 10_000.0
    out = PA.price_action_frame(df, P2, tf="4h")
    assert out["pa_vol_ratio"].iloc[400] > 1.5
    near = min(out["pa_room_long_atr"].iloc[400], out["pa_room_short_atr"].iloc[400])
    assert out["pa_vol_veto"].iloc[400] == int(near <= 1.0 or out["pa_zone_strength"].iloc[400] > 0)
    assert out["pa_vol_veto"].iloc[399] == 0 or out["pa_vol_ratio"].iloc[399] > 1.5


# --------------------------------------------------------------------------- dynamic levels and channel
def test_channel_of_a_straight_uptrend_is_up_and_position_is_between_zero_and_one():
    close = 100 + 0.5 * np.arange(200) + np.tile([0.0, 0.6, -0.3, 0.2], 50)
    out = PA.price_action_frame(_bars(close), P2, tf="4h")
    assert (out["pa_chan_dir"].iloc[60:] == 1).all()
    pos = out["pa_chan_pos"].iloc[60:]
    assert ((pos >= 0) & (pos <= 1)).all()


def test_h1_ma10_is_nan_above_one_hour_and_from_closed_hours_below():
    out4 = PA.price_action_frame(_walk(300, freq="4h"), P2)
    assert out4["pa_h1_ma10_dist_atr"].isna().all()
    df = _walk(300, freq="15min")
    out = PA.price_action_frame(df, P2)
    c = df["close"].to_numpy()
    hour_closes = c[3::4]
    row = 4 * 20 + 3                                                      # closes the 21st hour
    ma = hour_closes[11:21].mean()
    atr = PA.wilder_atr(df, 14).to_numpy()[row]
    assert out["pa_h1_ma10_dist_atr"].iloc[row] == pytest.approx((c[row] - ma) / atr)
    assert out["pa_h1_ma10_dist_atr"].iloc[row - 1] == pytest.approx((c[row - 1] - hour_closes[10:20].mean()) /
                                                                     PA.wilder_atr(df, 14).to_numpy()[row - 1])


# --------------------------------------------------------------------------- chart patterns
def test_descending_triangle_breaks_down_on_a_close_and_retests_the_floor():
    close = _path([90, 104, 100, 110, 100, 106, 100, 103, 95, 99.7, 90], flat=30, start=90)
    out = PA.price_action_frame(_bars(close), P2, tf="4h")
    assert out["pa_tri_desc"].sum() > 0 and out["pa_tri_asc"].sum() == 0
    bo = _first(out["pa_tri_breakout_close"], -1)
    assert close[bo] < 99.8 <= close[bo - 1]                               # the floor is the pivot lows' 99.8
    assert out["pa_tri_failed"].sum() == 0
    rt = _first(out["pa_tri_retest"], -1)
    assert rt > bo and close[rt] == pytest.approx(99.7)


def test_descending_triangle_fails_on_a_close_above_the_falling_line():
    close = _path([90, 104, 100, 110, 100, 106, 100, 103, 101, 115], flat=30, start=90)
    out = PA.price_action_frame(_bars(close), P2, tf="4h")
    assert _first(out["pa_tri_failed"], 1) > 0 and (out["pa_tri_breakout_close"] == 0).all()


def test_head_and_shoulders_breaks_the_neckline_then_counts_down_to_the_target():
    close = _path([100, 105, 100, 110, 100, 105, 100, 99, 92, 99.5, 80], flat=30, start=100)
    out = PA.price_action_frame(_bars(close), P2, tf="4h")
    brk = _first(out["pa_hs_break"], -1)
    assert close[brk] < 99.8 and out["pa_hs"].iloc[brk] == -1
    # target = neckline - (head - neckline) = 99.8 - (110.2 - 99.8)
    left = out["pa_hs_target_dist_atr"].iloc[brk]
    atr = PA.wilder_atr(_bars(close), 14).to_numpy()[brk]
    assert left == pytest.approx((close[brk] - (99.8 - 10.4)) / atr)
    assert _first(out["pa_hs_retest"], -1) > brk


def test_right_shoulder_above_the_head_is_no_head_and_shoulders():
    close = _path([100, 105, 100, 110, 100, 111, 100, 92], flat=30, start=100)
    out = PA.price_action_frame(_bars(close), P2, tf="4h")
    assert (out["pa_hs"] != -1).all()


def test_double_top_uses_the_equal_highs_rule_and_breaks_the_neckline():
    close = _path([100, 110, 104, 110, 103, 95], flat=30, start=100)
    df = _bars(close)
    out = PA.price_action_frame(df, PA.PAParams(pivot_len=2, eq_atr_len=14), tf="4h")
    brk = _first(out["pa_dbl_break"], -1)
    assert out["pa_dbl"].iloc[brk] == -1 and close[brk] < 103.8 and out["pa_tpl"].iloc[brk] == 0


def test_equal_pivot_flags_match_smc_replica_equal_levels_on_high_low_swings():
    df = _walk(3000, freq="1h")
    h, lo, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    eqh, eql = equal_levels(h, lo, c, 3, 0.1, 200)
    p = PA.PAParams(pivot_len=3)
    ph, pl = PA.swing_pivots(h, lo, c, p)
    a_eq = PA.atr_seeded(h, lo, c, 200)
    for piv, eq in ((ph, eqh), (pl, eql)):
        idx = np.flatnonzero(~np.isnan(piv))
        mine = np.zeros(len(c), dtype=bool)
        mine[idx[1:]] = np.abs(piv[idx[1:]] - piv[idx[:-1]]) < 0.1 * a_eq[idx[1:]]
        assert (mine == eq).all()


def test_second_touch_of_a_swing_low_after_a_rally_is_the_double_bottom_retest():
    close = _path([110, 100, 106, 100.1, 108], flat=30, start=110)
    out = PA.price_action_frame(_bars(close), P2, tf="4h")
    rt = _first(out["pa_dbl_retest"], 1)
    assert close[rt] == pytest.approx(100.1)


def test_down_trendline_is_valid_only_after_a_lower_low_and_breaks_on_a_close_above():
    close = _path([120, 112, 100, 108, 97, 104, 96, 115], flat=30, start=120)
    out = PA.price_action_frame(_bars(close), P2, tf="4h")
    d = out["pa_tl_dn_dist_atr"]
    first_valid = int(np.flatnonzero(d.notna().to_numpy())[0])
    assert close[first_valid] < 99.8 or _bars(close)["low"].iloc[first_valid] < 99.8
    assert _first(out["pa_tl_break"], 1) > first_valid


def test_fibonacci_levels_of_the_last_swing_and_the_fifty_percent_confluence():
    close = _path([100, 110, 104, 120, 112], flat=30, start=100)
    df = _bars(close)
    out = PA.price_action_frame(df, PA.PAParams(pivot_len=2, fib_conf_atr=5.0), tf="4h")
    i = len(close) - 1
    atr = PA.wilder_atr(df, 14).to_numpy()[i]
    a_, b_ = 103.8, 120.2                                                   # last swing low -> high
    assert out["pa_fib_dir"].iloc[i] == 1
    assert out["pa_fib500_dist_atr"].iloc[i] == pytest.approx((close[i] - (b_ - 0.5 * (b_ - a_))) / atr)
    assert out["pa_fib618_dist_atr"].iloc[i] == pytest.approx((close[i] - (b_ - 0.618 * (b_ - a_))) / atr)
    assert out["pa_fib50_confluence"].iloc[i] == 1                          # 50% = 112 near the broken 110.2


def test_abcd_waits_at_c_and_fires_d_on_a_close_beyond_b():
    close = _path([100, 110, 105, 112], flat=30, start=100)
    out = PA.price_action_frame(_bars(close), PA.PAParams(pivot_len=2, abcd_min_atr=1.0), tf="4h")
    d = _first(out["pa_abcd_d"], 1)
    assert close[d] > 110.2 >= close[d - 1] and out["pa_abcd_c"].iloc[d - 1] == 1


# --------------------------------------------------------------------------- candles
def _cd(rows: list[tuple[float, float, float, float]], trend: int = 0, vol: float = 1.0) -> dict[str, np.ndarray]:
    """Candle codes of hand-made bars (open, high, low, close), after 12 bars moving `trend` 1.0 each."""
    pre = []
    px = 100.0
    for _ in range(12):
        nxt = px + trend
        pre.append((px, max(px, nxt) + 0.1, min(px, nxt) - 0.1, nxt))
        px = nxt
    o, h, lo, c = (np.array([r[k] for r in pre + rows]) for k in range(4))
    a = np.ones(len(o))
    return PA._candles(o, h, lo, c, a, np.full(len(o), vol), PA.DEFAULT_PARAMS)


def test_hammer_after_a_decline_and_hangman_after_a_rally():
    assert _cd([(88.0, 88.35, 86.0, 88.3)], trend=-1)["hammer"][-1] == 1
    assert _cd([(112.0, 112.35, 110.0, 112.3)], trend=1)["hammer"][-1] == -1


def test_shooting_star_after_a_rally_and_inverted_hammer_after_a_decline():
    assert _cd([(112.0, 114.0, 112.0, 112.3)], trend=1)["invhammer"][-1] == -1
    assert _cd([(88.0, 90.0, 88.0, 88.3)], trend=-1)["invhammer"][-1] == 1


def test_engulfing_needs_the_whole_prior_body_and_an_open_beyond_the_prior_close():
    down = [(89.0, 89.1, 87.9, 88.0)]
    assert _cd(down + [(88.0, 89.6, 87.9, 89.5)], trend=-1)["engulf"][-1] == 1
    assert _cd(down + [(88.2, 89.6, 88.1, 89.5)], trend=-1)["engulf"][-1] == 0     # opened above the prior close


def test_piercing_and_dark_cloud_close_past_half_the_prior_body():
    assert _cd([(90.0, 90.1, 87.9, 88.0), (87.9, 89.6, 87.8, 89.5)], trend=-1)["pierce"][-1] == 1
    assert _cd([(110.0, 112.1, 109.9, 112.0), (112.1, 112.2, 110.4, 110.5)], trend=1)["pierce"][-1] == -1


def test_morning_star_and_abandoned_baby_use_body_gaps():
    rows = [(92.0, 92.1, 89.9, 90.0), (89.8, 90.0, 89.5, 89.7), (90.0, 91.6, 89.9, 91.5)]
    assert _cd(rows)["star"][-1] == 1
    baby = [(92.0, 92.1, 89.9, 90.0), (89.75, 89.9, 89.6, 89.76), (89.9, 91.6, 89.8, 91.5)]
    assert _cd(baby)["abandoned"][-1] == 1


def test_harami_marubozu_pin_and_inside_bar():
    assert _cd([(92.0, 92.1, 89.9, 90.0), (90.2, 91.0, 90.1, 90.9)])["harami"][-1] == 1
    assert _cd([(100.0, 101.02, 99.99, 101.0)])["marubozu"][-1] == 1
    assert _cd([(100.0, 100.1, 98.0, 100.05)])["pin"][-1] == 1
    assert _cd([(100.0, 102.0, 98.0, 101.0), (100.5, 101.5, 99.0, 101.0)])["inside"][-1] == 1


def test_doji_kinds_and_spinning_top_volume_context():
    assert _cd([(100.0, 102.0, 100.0, 100.01)])["doji"][-1] == 3            # gravestone
    assert _cd([(100.0, 100.0, 98.0, 100.01)])["doji"][-1] == 4             # dragonfly
    assert _cd([(100.0, 100.8, 99.2, 100.01)])["doji"][-1] == 2             # long-legged
    spin = [(100.0, 100.6, 99.5, 100.2)]
    assert _cd(spin, vol=0.3)["spin"][-1] == 1 and _cd(spin, vol=2.0)["spin"][-1] == 3


def test_three_soldiers_and_crows_open_near_the_previous_close():
    rows = [(100.0, 101.1, 99.9, 101.0), (101.0, 102.1, 100.9, 102.0), (102.0, 103.1, 101.9, 103.0)]
    assert _cd(rows)["three"][-1] == 1
    gap = [(100.0, 101.1, 99.9, 101.0), (101.5, 102.6, 101.4, 102.5), (102.5, 103.6, 102.4, 103.5)]
    assert _cd(gap)["three"][-1] == 0


def test_fakey_strike_methods_and_tower():
    assert _cd([(100.0, 102.0, 98.0, 101.0), (100.5, 101.5, 99.0, 101.0), (101.0, 101.2, 98.5, 100.0)])["fakey"][-1] == 1
    strike = [(100.0, 101.1, 99.9, 101.0), (101.0, 102.1, 100.9, 102.0), (102.0, 103.1, 101.9, 103.0),
              (103.1, 103.2, 99.0, 99.5)]
    assert _cd(strike)["strike"][-1] == 1
    methods = [(100.0, 102.1, 99.9, 102.0), (101.9, 102.0, 101.5, 101.7), (101.7, 101.8, 101.3, 101.5),
               (101.5, 101.7, 101.2, 101.6), (101.6, 102.6, 101.5, 102.5)]
    assert _cd(methods)["methods"][-1] == 1
    tower = [(102.0, 102.1, 99.9, 100.0), (100.0, 100.3, 99.7, 100.1), (100.1, 100.3, 99.8, 100.0),
             (100.0, 100.2, 99.8, 100.1), (100.1, 102.2, 100.0, 102.1)]
    assert _cd(tower)["tower"][-1] == 1


def test_confirmed_variant_is_stamped_on_the_next_bar_only_when_it_closes_in_the_pattern_direction():
    raw = np.array([0, 1, 0, -1, 0])
    o = np.array([10.0, 10.0, 10.5, 11.0, 11.0])
    c = np.array([10.0, 10.4, 10.9, 10.8, 10.9])
    h, lo = np.maximum(o, c) + 0.1, np.minimum(o, c) - 0.1
    assert PA._confirm(raw, c, o, h, lo, code=False).tolist() == [0, 0, 1, 0, 0]      # bear signal not confirmed


def test_reversal_rank_orders_pin_at_level_above_engulfing_and_confluence_needs_a_rejection():
    df = _walk(3000, freq="1h")
    out = PA.price_action_frame(df, P2)
    r = out["pa_reversal_rank"]
    pin_lvl = out["pa_cs_pin_lvl"] != 0
    assert (r[pin_lvl] == 5).all()
    assert (r[(out["pa_cs_engulf"] != 0) & ~pin_lvl] == 4).all()
    assert (out["pa_confluence"][out["pa_sr_reject"] == 0] == 0).all()
    s = out["pa_confluence"][out["pa_sr_reject"] != 0]
    assert (np.sign(s) == out["pa_sr_reject"][out["pa_sr_reject"] != 0]).all() and (s.abs() >= 1).all()
