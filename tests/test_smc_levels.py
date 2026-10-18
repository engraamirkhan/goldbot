"""Point-in-time structure context (goldbot/features/smc_levels.py): the multi-timeframe trend table, the swing
zones with strong/weak extremes, and previous day/week levels. Synthetic bars only."""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.features import smc_levels as L


def _bars(close: np.ndarray, tf: str, start: str = "2024-01-01 00:00") -> pd.DataFrame:
    ts = pd.date_range(start, periods=len(close), freq=tf, tz="UTC")
    return pd.DataFrame({"bar_time": ts, "open": close, "high": close + 0.5, "low": close - 0.5, "close": close,
                         "available_utc": ts + pd.Timedelta(tf)})


def test_adx_trend_is_up_in_a_rising_series_and_down_in_a_falling_one():
    up = _bars(np.linspace(100, 200, 120), "15min")
    dn = _bars(np.linspace(200, 100, 120), "15min")
    assert L.adx_trend(up.high.to_numpy(), up.low.to_numpy(), up.close.to_numpy())[-1] == 1
    assert L.adx_trend(dn.high.to_numpy(), dn.low.to_numpy(), dn.close.to_numpy())[-1] == -1


def test_adx_trend_is_zero_during_warm_up():
    b = _bars(np.linspace(100, 200, 120), "15min")
    assert (L.adx_trend(b.high.to_numpy(), b.low.to_numpy(), b.close.to_numpy())[:10] == 0).all()


def test_higher_timeframe_trend_is_visible_only_after_its_bar_closed():
    base = _bars(np.linspace(100, 200, 800), "15min")
    h1 = _bars(np.linspace(100, 200, 200), "1h")
    full = L.trend_table(base, {"1h": h1})
    # change the last 1h bar the base frame can see: the earlier 15m rows must not move (no look-ahead)
    h1b = h1.copy()
    h1b.loc[h1b.index[-1], ["high", "low", "close"]] = [0.0, -50.0, -40.0]
    alt = L.trend_table(base, {"1h": h1b})
    last_close = h1.available_utc.iloc[-1]
    before = base.available_utc < last_close
    assert (full.loc[before, "trend_1h"] == alt.loc[before, "trend_1h"]).all()


def test_trend_table_has_one_column_per_timeframe():
    base = _bars(np.linspace(100, 200, 400), "15min")
    t = L.trend_table(base, {"1h": _bars(np.linspace(100, 200, 100), "1h")})
    assert list(t.columns) == ["trend_15m", "trend_1h"]


def test_swing_zones_put_premium_at_the_top_and_discount_at_the_bottom_of_the_swing_range():
    x = np.concatenate([np.linspace(100, 200, 60), np.linspace(200, 120, 60), np.linspace(120, 150, 20)])
    z = L.swing_zones(x + 0.5, x - 0.5, np.full(len(x), -1), swing_len=10).iloc[-1]
    assert z.swing_top >= 200 and z.swing_btm <= 120
    assert z.premium_lo > z.equilibrium_hi > z.swing_mid > z.equilibrium_lo > z.discount_hi
    assert z.strong_high == 1 and z.strong_low == 0


def test_strong_low_follows_a_bullish_swing_trend():
    x = np.linspace(100, 200, 80)
    z = L.swing_zones(x + 0.5, x - 0.5, np.full(80, 1), swing_len=5)
    assert z.strong_low.iloc[-1] == 1 and z.strong_high.iloc[-1] == 0



def _winter_frame(days: int = 3, offset_hours: int = 1):
    ts = pd.date_range("2024-01-08 21:00", periods=96 * days, freq="15min", tz="UTC") + pd.Timedelta(hours=offset_hours)
    c = np.linspace(100, 200, len(ts))
    return pd.Series(ts), c + 1, c - 1, c


def test_the_day_rolls_at_17_new_york_in_winter_and_in_summer():
    for start, off in (("2024-01-08 21:00", 1), ("2024-07-08 21:00", 0)):    # 17:00 NY = 22:00 / 21:00 UTC
        ts = pd.date_range(start, periods=96 * 3, freq="15min", tz="UTC") + pd.Timedelta(hours=off)
        c = np.linspace(100, 200, len(ts))
        lv = L.period_levels(pd.Series(ts), c + 1, c - 1, c)
        assert np.isnan(lv.pdh.iloc[0])
        assert lv.pdh.iloc[96] == (c[:96] + 1).max() and lv.pdh.iloc[95] != lv.pdh.iloc[96]


def test_running_levels_never_use_later_bars():
    s, h, lo, c = _winter_frame(4)
    full = L.period_levels(s, h, lo, c)
    cut = 150
    part = L.period_levels(s.iloc[:cut], h[:cut], lo[:cut], c[:cut])
    pd.testing.assert_frame_equal(full.iloc[:cut], part, check_exact=True)
    assert full.day_high.iloc[10] == h[:11].max() and full.day_low.iloc[10] == lo[:11].min()


def test_previous_week_is_the_closed_week_before_the_bars_own():
    ts = pd.date_range("2024-01-07 22:00", "2024-01-19 21:45", freq="15min", tz="UTC")   # Sun 17:00 NY ... Fri 16:45 NY
    ts = ts[~((ts.dayofweek == 5) | ((ts.dayofweek == 4) & (ts.hour >= 22)))]
    c = np.arange(len(ts), dtype=float)
    lv = L.period_levels(pd.Series(ts), c + 1, c - 1, c)
    wk2 = np.asarray(ts >= pd.Timestamp("2024-01-14 22:00", tz="UTC"))
    assert np.isnan(lv.pwh[~wk2]).all()
    assert (lv.pwh[wk2] == c[~wk2].max() + 1).all() and (lv.pwl[wk2] == c[~wk2].min() - 1).all()


def test_floor_pivots_follow_the_standard_formulas_from_the_previous_day():
    s, h, lo, c = _winter_frame(3)
    day2 = L.period_levels(s, h, lo, c).iloc[96]
    ph, pl, pc = h[:96].max(), lo[:96].min(), c[95]
    pp = (ph + pl + pc) / 3
    assert day2.pivot_pp == pp and day2.pivot_r1 == 2 * pp - pl and day2.pivot_s1 == 2 * pp - ph
    assert day2.pivot_r2 == pp + (ph - pl) and day2.pivot_s2 == pp - (ph - pl)
    assert day2.pivot_r3 == ph + 2 * (pp - pl) and day2.pivot_s3 == pl - 2 * (ph - pp)


def test_pivot_features_give_day_bias_and_the_nearest_level():
    vals = (100.0, 110, 120, 130, 90, 80, 70)
    lv = pd.DataFrame({f"pivot_{n}": [v] for n, v in zip(L.PIVOT_NAMES, vals, strict=True)})
    f = L.pivot_features(np.array([108.0]), np.array([2.0]), lv)
    assert f.day_bias.iloc[0] == 1 and f.pivot_nearest_dist_atr.iloc[0] == -1.0      # 108 is 1 ATR under R1


def test_ma_crosses_flag_golden_then_death_cross():
    x = np.concatenate([np.linspace(200, 100, 300), np.linspace(100, 300, 300), np.linspace(300, 50, 300)])
    m = L.ma_crosses(x).ma_cross_50_200
    assert m[m == 1].index[0] < m[m == -1].index[0]


def test_swing_labels_mark_higher_lows_in_an_up_drift_at_the_confirming_bar():
    t = np.arange(400.0)
    x = 100 + 30 * np.sin(t / 12) + t * 0.5
    lab = L.swing_labels(x + 0.5, x - 0.5, swing_len=5)
    assert (lab.swing_low_label[lab.swing_low_label != 0] == 1).all()
    assert (lab.swing_high_label[lab.swing_high_label != 0] == 1).all()


def test_lower_timeframe_gap_counts_only_in_a_decision_bar_that_has_closed_after_it():
    ts = pd.date_range("2024-01-02", periods=6, freq="15min", tz="UTC")
    o = np.array([10, 10, 11, 20, 20, 20.0])
    c = np.array([10.5, 10.5, 20, 20.5, 20.5, 20.5])
    h = np.array([11, 11, 21, 21, 21, 21.0])
    lo = np.array([10, 10, 10.5, 19, 19, 19.0])
    lower = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "available_utc": ts + pd.Timedelta("15min")})
    dec = pd.Series(pd.to_datetime(["2024-01-02 00:30", "2024-01-02 01:00", "2024-01-02 02:00"], utc=True))
    got = L.fvg_projected(dec, lower)
    # the gap is confirmed on bar 3 (known 01:00): the 00:30 decision bar must not see it
    assert got.fvg_bull_lower.tolist() == [0, 1, 0]
