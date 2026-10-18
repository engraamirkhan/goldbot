"""Opt-in `smc_replica` feature family (goldbot/features/smc_replica.py): clean-room replica of three chart indicators,
fitted on their pulled outputs (match table in docs/research/prereg-tv-replica-2026-10-18.md). Fixtures here are
synthetic bars and hand-worked expectations only; no vendor output is stored in the repo.

What is pinned: each building block against its definition on crafted bars, the point-in-time property (a value never
changes when later bars are appended; events are stamped at their confirming bar), and the opt-in wiring (the default
feature list and its version hash are unchanged)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.features import smc_replica as R


def _walk(n: int, seed: int = 7, sd: float = 0.002, start: float = 2000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = start * np.exp(np.cumsum(rng.normal(0, sd, n)))
    o = np.concatenate(([start], c[:-1])) * np.exp(rng.normal(0, sd / 4, n))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, sd / 2, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, sd / 2, n)))
    ts = pd.date_range("2024-01-02", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": h, "low": lo, "close": c, "spread": 0.3,
                         "tick_count": rng.integers(50, 500, n).astype(float)})


# --------------------------------------------------------------------------- averages
def test_seeded_ema_is_nan_then_sma_seed_then_recursive():
    x = np.array([1.0, 2.0, 3.0, 4.0, 10.0])
    e = R.ema_seeded(x, 3)
    assert np.isnan(e[:2]).all()
    assert e[2] == pytest.approx(2.0)                      # SMA of 1, 2, 3
    assert e[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)      # alpha = 2 / (3 + 1)
    assert e[4] == pytest.approx(0.5 * 10 + 0.5 * 3.0)
    r = R.rma_seeded(x, 2)
    assert r[1] == pytest.approx(1.5) and r[2] == pytest.approx(0.5 * 3 + 0.5 * 1.5)


def test_ma_ribbon_schemes():
    c = _walk(260)["close"].to_numpy()
    k = R.ma_ribbon(c, "sma_ema")
    assert k["ma10"][-1] == pytest.approx(c[-10:].mean()) and k["ma200"][-1] == pytest.approx(c[-200:].mean())
    assert np.isnan(k["ma50"][48]) and k["ma50"][49] == pytest.approx(c[:50].mean())
    e = R.ma_ribbon(c, "ema")
    assert set(e) == {"ma5", "ma10", "ma20", "ma50", "ma200"}
    np.testing.assert_allclose(e["ma50"], k["ma50"], equal_nan=True)
    with pytest.raises(ValueError):
        R.ma_ribbon(c, "wma")


def test_crosses_need_the_previous_bar_on_the_other_side():
    a = np.array([1.0, 3.0, 3.0, 1.0, 2.0])
    b = np.full(5, 2.0)
    assert R.cross_over(a, b).tolist() == [False, True, False, False, False]
    assert R.cross_under(a, b).tolist() == [False, False, False, True, False]   # 2.0 -> 2.0 is not a cross


# --------------------------------------------------------------------------- money flow
def test_money_flow_by_hand_and_first_bar_counts_for_neither_side():
    h = np.array([10.0, 11, 12, 11, 13])
    lo = np.array([8.0, 9, 10, 9, 11])
    c = np.array([9.0, 10, 11, 10, 12])
    v = np.array([100.0, 200, 300, 400, 500])
    m = R.money_flow(h, lo, c, v, 3)
    tp = (h + lo + c) / 3                                       # 9, 10, 11, 10, 12
    assert np.isnan(m[:2]).all()
    assert m[2] == pytest.approx(100.0)                         # bars 0..2: bar 0 neither side, 1 and 2 up, no down flow
    up = tp[2] * 300 + tp[4] * 500
    dn = tp[3] * 400
    assert m[4] == pytest.approx(100 - 100 / (1 + up / dn))


def test_money_flow_alert_crosses_on_the_feature_frame():
    X = R.replica_frame(_walk(600, seed=11))
    m = X["mfi_12"].to_numpy()
    prev = np.concatenate(([np.nan], m[:-1]))
    with np.errstate(invalid="ignore"):
        assert (X["mfi_os_cross"].to_numpy() == ((m < 20) & (prev >= 20))).all()
        assert (X["mfi_ob_cross"].to_numpy() == ((m > 80) & (prev <= 80))).all()


# --------------------------------------------------------------------------- pivots
def test_leg_pivots_are_stamped_at_confirmation_and_the_first_leg_is_not_a_pivot():
    # a rise to bar 5, a fall to bar 10, a rise to bar 15 (length 2)
    up1 = np.arange(6, dtype=float)
    dn1 = 5 - np.arange(1, 6, dtype=float)
    up2 = np.arange(1, 7, dtype=float)
    mid = np.concatenate((up1, dn1, up2))
    h, lo = mid + 0.5, mid - 0.5
    top, btm = R.leg_pivots(h, lo, 2)
    # bar 5's high is first known to be above the 2 bars after it at bar 7
    assert np.flatnonzero(~np.isnan(top)).tolist() == [7] and top[7] == h[5]
    # the low at bar 10 starts the up-leg, known at bar 12
    assert np.flatnonzero(~np.isnan(btm)).tolist() == [12] and btm[12] == lo[10]
    # the very first leg (the down-leg seen at bar 7 is the first state change) sets the state only:
    # a series that starts falling records no pivot low for its first leg
    t2, b2 = R.leg_pivots(h[5:], lo[5:], 2)
    assert np.isnan(t2).all() and np.flatnonzero(~np.isnan(b2)).tolist() == [7]


def test_pivot_points_left_side_allows_ties_right_side_does_not():
    h = np.array([1.0, 5.0, 5.0, 2.0, 1.0, 0.0, 0.0])
    lo = h - 1
    ph, _ = R.pivot_points(h, lo, 2)
    # bar 2 (5.0) ties bar 1 on its left and beats its right side: a pivot, confirmed at bar 4; bar 1 is not (tie on its right)
    assert np.flatnonzero(~np.isnan(ph)).tolist() == [4] and ph[4] == 5.0


# --------------------------------------------------------------------------- structure
def _bars_from_closes(c: np.ndarray, wick: float = 0.2) -> tuple[np.ndarray, ...]:
    o = np.concatenate(([c[0]], c[:-1]))
    return o, np.maximum(o, c) + wick, np.minimum(o, c) - wick, c


def test_structure_bos_then_choch_on_the_swing_scale():
    # up to 20, down to 10, up through 20 (bullish BOS: trend 0 -> +1), down through 10 (bearish CHoCH)
    c = np.concatenate((np.linspace(10, 20, 11), np.linspace(19, 10, 10), np.linspace(11, 25, 15),
                        np.linspace(24, 5, 20)))
    o, h, lo, c = _bars_from_closes(c)
    st = R.structure_breaks(o, h, lo, c, swing_len=3, internal_len=2, candle_filter=False)
    bull = np.flatnonzero(st["bos_bull_swing"] | st["choch_bull_swing"])
    bear = np.flatnonzero(st["bos_bear_swing"] | st["choch_bear_swing"])
    assert len(bull) >= 1 and len(bear) >= 1
    first_bull, first_bear = bull[0], bear[bear > bull[0]][0]
    assert st["bos_bull_swing"][first_bull]                 # trend was 0: a BOS, not a CHoCH
    assert c[first_bull] > 20.2 >= c[first_bull - 1]        # the break bar closes above the pivot high (20 + wick)
    assert st["choch_bear_swing"][first_bear]               # trend was bullish: a CHoCH
    assert st["trend_swing"][first_bear] == -1 and st["trend_swing"][first_bear - 1] == 1
    # each pivot breaks once: no second bullish event before a new pivot high forms
    assert len(bull[(bull > first_bull) & (bull < first_bear)]) == 0


def test_internal_breaks_respect_the_candle_filter():
    c = np.concatenate((np.linspace(10, 20, 11), np.linspace(19, 10, 10), np.linspace(11, 25, 15)))
    o, h, lo, c = _bars_from_closes(c)
    free = R.structure_breaks(o, h, lo, c, swing_len=30, internal_len=3, candle_filter=False)
    filt = R.structure_breaks(o, h, lo, c, swing_len=30, internal_len=3, candle_filter=True)
    # with a 0.2 wick on both sides the bullish test is upper wick (0.2) > min(close, open - low) = 0.2: never true
    assert (free["bos_bull_internal"] | free["choch_bull_internal"]).any()
    assert not (filt["bos_bull_internal"] | filt["choch_bull_internal"]).any()


def test_equal_highs_need_a_previous_pivot_within_threshold_atr():
    n = 260
    base = np.tile([0.0, 1.0, 2.0, 3.0, 2.0, 1.0, 0.0], n // 7 + 1)[:n] + 100   # identical peaks every 7 bars
    h, lo = base + 0.5, base - 0.5
    c = base.copy()
    eqh, eql = R.equal_levels(h, lo, c, length=3, threshold=0.1, atr_len=200)
    ph, pl = R.pivot_points(h, lo, 3)
    piv = np.flatnonzero(~np.isnan(ph))
    assert eqh[piv[piv >= 200]].all()                       # equal peaks once ATR(200) exists
    assert not eqh[piv[piv < 199]].any()                    # ATR undefined before bar 199: never equal
    assert not eqh[np.setdiff1d(np.arange(n), piv)].any()


def test_fair_value_gap_three_bar_rule_with_auto_threshold():
    o = np.array([100.0, 100.0, 100.0, 101.0, 103.0, 103.0])
    c = np.array([100.0, 100.1, 100.0, 103.0, 103.5, 103.4])
    h = np.array([100.2, 100.3, 100.2, 103.1, 104.0, 103.6])
    lo = np.array([99.9, 99.9, 99.8, 100.9, 102.9, 103.2])
    bull, bear = R.fair_value_gaps(o, h, lo, c)
    # bar 4: low 102.9 > high[2] 100.2, close[3] 103 > 100.2, body of bar 3 = 1.98% > 2 x running mean |body %|
    assert bull.tolist() == [False, False, False, False, True, False] and not bear.any()
    b2, _ = R.fair_value_gaps(o, h, lo, c, auto_threshold=False)
    assert b2[4] and b2[5]                                  # bar 5: low 103.2 > high[3] 103.1, body[4] > 0


def test_range_retracement_levels():
    h = np.arange(1.0, 61.0)
    lo = h - 1
    g50, g618 = R.range_retracement(h, lo, 50, (0.5, 0.618))
    assert np.isnan(g50[48]) and g50[-1] == pytest.approx(60 - 0.5 * (60 - 10))
    assert g618[-1] == pytest.approx(60 - 0.618 * 50)


def test_candle_class_codes_and_priority():
    o = np.r_[np.full(20, 100.0), 100.0, 101.2, 100.5, 100.0, 100.0]
    c = np.r_[np.full(20, 100.1), 101.0, 99.5, 100.6, 100.2, 99.9]
    v = np.r_[np.full(20, 100.0), 200.0, 200.0, 10.0, 100.0, 100.0]
    cc = R.candle_class(o, c, v)
    assert cc[20] == R.CANDLE_HIGH_BULL                     # 200 > 1.5 x EMA20 of volume
    assert cc[21] == R.CANDLE_ENGULF                        # bearish body 101.2 -> 99.5 strictly engulfs 100 -> 101: wins over volume
    assert cc[22] == R.CANDLE_LOW_VOL
    assert cc[23] == R.CANDLE_NONE


def test_sr_channel_breaks_on_a_repeated_level():
    # a range whose highs repeatedly stop at 105, then a close through it
    cyc = np.r_[np.linspace(100, 105, 6), np.linspace(104, 100, 5)]
    mid = np.r_[np.tile(cyc, 30), np.linspace(100, 108, 9)]
    h, lo = mid + 0.05, mid - 0.05
    o = np.r_[mid[0], mid[:-1]]
    up, dn = R.sr_channel_breaks(o, h, lo, mid, pivot_len=3, width_pct=1.0, max_channels=5, lookback=300)
    brk = np.flatnonzero(up)
    assert len(brk) and mid[brk[-1]] >= 105.05 > mid[brk[-1] - 1]


# --------------------------------------------------------------------------- point in time
def test_no_value_changes_when_later_bars_are_appended():
    df = _walk(900, seed=3)
    full = R.replica_frame(df)
    for k in (350, 517, 733):
        part = R.replica_frame(df.iloc[:k].reset_index(drop=True))
        a = part.to_numpy(dtype=float)
        b = full.iloc[:k].to_numpy(dtype=float)
        np.testing.assert_allclose(a, b, equal_nan=True, rtol=0, atol=0, err_msg=f"prefix {k}")


def test_feature_columns_and_types():
    X = R.replica_frame(_walk(700, seed=5))
    assert tuple(X.columns) == R.COLUMNS
    events = [*R.STRUCTURE_EVENTS, "eqh", "eql", "fvg_bull", "fvg_bear", "mfi_os_cross", "mfi_ob_cross",
              "sr_break_up", "sr_break_dn"]
    for col in events:
        assert set(np.unique(X[col])) <= {0, 1}, col
    assert set(np.unique(X["trend_swing"])) <= {-1.0, 0.0, 1.0}
    assert X[events].to_numpy().sum() > 0


# --------------------------------------------------------------------------- opt-in wiring
def test_opt_in_registration_leaves_the_default_set_unchanged():
    from goldbot.features import FEATURES, registry
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, OPT_IN_FEATURES
    assert R.NAME in OPT_IN_FEATURES and R.NAME not in DEFAULT_FEATURE_NAMES
    before = registry.feature_version(DEFAULT_FEATURE_NAMES)
    name = R.enable()
    assert R.enable() == name and name in FEATURES                     # idempotent
    assert [n for n in FEATURES if n not in OPT_IN_FEATURES] == DEFAULT_FEATURE_NAMES
    assert registry.feature_version(DEFAULT_FEATURE_NAMES) == before


def test_lookahead_check_reports_no_column():
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.research.pipeline import lookahead_check
    name = R.enable()
    ticks = synthetic_ticks("2025-03-03", "2025-04-18", ticks_per_minute=2, seed=5)
    bars = resample_bars(ticks_to_1m(ticks), "15m").reset_index(drop=True)
    out = lookahead_check(bars, None, feature_names=[name])
    assert out["lookahead_columns"] == [] and out["columns_checked"] >= len(R.COLUMNS) - 2
