"""Price-structure features: swing points, support/resistance levels, gaps, candle anatomy, patterns.

Levels are built from *confirmed* swing points only (a swing high at bar i is confirmed at bar i+lag),
so a level never exists before the system could have known it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.features.registry import feature
from goldbot.features.technical import atr


def swing_points(df: pd.DataFrame, lag: int = 5) -> tuple[pd.Series, pd.Series]:
    """Boolean series marking swing highs/lows at their own bar; confirmation happens `lag` bars later."""
    h, l = df["high"], df["low"]
    is_high = (h == h.rolling(2 * lag + 1, center=True).max())
    is_low = (l == l.rolling(2 * lag + 1, center=True).min())
    return is_high.fillna(False), is_low.fillna(False)


def confirmed_levels(df: pd.DataFrame, lag: int = 5, max_levels: int = 60) -> list[list[tuple[float, int, int]]]:
    """For each bar, the list of (price, touches, age_bars) of confirmed swing levels known at that bar.

    O(n * max_levels); fine for a few hundred thousand bars. Levels within 0.3 ATR merge, touch count grows.
    """
    is_high, is_low = swing_points(df, lag)
    a = atr(df, 14).bfill().values
    highs, lows = df["high"].values, df["low"].values
    known: list[list[float]] = []  # [price, touches, created_idx]
    per_bar: list[list[tuple[float, int, int]]] = []
    n = len(df)
    for i in range(n):
        j = i - lag  # swing at j is confirmed now
        if j >= 0:
            for flag, px in ((is_high.iat[j], highs[j]), (is_low.iat[j], lows[j])):
                if not flag:
                    continue
                merged = False
                for lv in known:
                    if abs(lv[0] - px) <= 0.3 * a[i]:
                        lv[0] = (lv[0] * lv[1] + px) / (lv[1] + 1)
                        lv[1] += 1
                        merged = True
                        break
                if not merged:
                    known.append([px, 1, i])
                if len(known) > max_levels:
                    known.sort(key=lambda x: (-x[1], -x[2]))
                    del known[max_levels:]
        per_bar.append([(lv[0], lv[1], i - lv[2]) for lv in known])
    return per_bar


@feature("support_resistance", "structure", lookback=600)
def f_levels(df, ctx):
    lag = ctx.get("swing_lag", 5)
    levels = confirmed_levels(df, lag)
    a = atr(df, 14).bfill().values
    close = df["close"].values
    up = np.full(len(df), np.nan)
    dn = np.full(len(df), np.nan)
    up_t = np.zeros(len(df))
    dn_t = np.zeros(len(df))
    up_age = np.full(len(df), np.nan)
    dn_age = np.full(len(df), np.nan)
    n_near = np.zeros(len(df))
    for i, lvls in enumerate(levels):
        above = [lv for lv in lvls if lv[0] > close[i]]
        below = [lv for lv in lvls if lv[0] <= close[i]]
        if above:
            lv = min(above, key=lambda x: x[0])
            up[i], up_t[i], up_age[i] = (lv[0] - close[i]) / a[i], lv[1], lv[2]
        if below:
            lv = max(below, key=lambda x: x[0])
            dn[i], dn_t[i], dn_age[i] = (close[i] - lv[0]) / a[i], lv[1], lv[2]
        n_near[i] = sum(1 for lv in lvls if abs(lv[0] - close[i]) <= 1.0 * a[i])
    return pd.DataFrame({
        "dist_res_atr": up, "res_touches": up_t, "res_age": up_age,
        "dist_sup_atr": dn, "sup_touches": dn_t, "sup_age": dn_age,
        "levels_within_1atr": n_near,
    }, index=df.index)


@feature("swings", "structure", lookback=200)
def f_swings(df, ctx):
    lag = ctx.get("swing_lag", 5)
    is_high, is_low = swing_points(df, lag)
    # only confirmed swings are usable: shift by lag
    ch, cl = is_high.shift(lag).fillna(False), is_low.shift(lag).fillna(False)
    sh = df["high"].shift(lag).where(ch).ffill()
    sl = df["low"].shift(lag).where(cl).ffill()
    prev_sh = df["high"].shift(lag).where(ch).shift(1).ffill()
    prev_sl = df["low"].shift(lag).where(cl).shift(1).ffill()
    a = atr(df, 14)
    out = pd.DataFrame(index=df.index)
    out["hh"] = (sh > prev_sh).astype(int)
    out["hl"] = (sl > prev_sl).astype(int)
    out["structure_state"] = out["hh"] + out["hl"] - 1  # +1 uptrend structure, -1 downtrend, 0 mixed
    out["dist_last_swing_high_atr"] = (df["close"] - sh) / a
    out["dist_last_swing_low_atr"] = (df["close"] - sl) / a
    return out


@feature("gaps", "structure", lookback=20)
def f_gaps(df, ctx):
    """Gaps between consecutive bars (session break / weekend / news) in ATR, and whether filled."""
    a = atr(df, 14)
    gap = df["open"] - df["close"].shift(1)
    out = pd.DataFrame(index=df.index)
    out["gap_atr"] = gap / a
    # time gap in bars implied by timestamp (large => session break or weekend)
    dt = pd.Series(pd.DatetimeIndex(df["ts_utc"]).asi8, index=df.index).diff() / 1e9
    med = dt.rolling(50, min_periods=5).median()
    out["after_break"] = (dt > 3 * med).astype(int)
    # most recent unfilled gap: level and distance
    gap_lvl = df["close"].shift(1).where(gap.abs() > 0.5 * a)
    out["last_gap_level_dist_atr"] = (df["close"] - gap_lvl.ffill()) / a
    filled = ((gap > 0) & (df["low"] <= gap_lvl)) | ((gap < 0) & (df["high"] >= gap_lvl))
    out["gap_filled_same_bar"] = filled.astype(int)
    return out


@feature("candles", "structure", lookback=5)
def f_candles(df, ctx):
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    body = (df["close"] - df["open"])
    out = pd.DataFrame(index=df.index)
    out["body_pct"] = body / rng
    out["upper_wick_pct"] = (df["high"] - df[["open", "close"]].max(axis=1)) / rng
    out["lower_wick_pct"] = (df[["open", "close"]].min(axis=1) - df["low"]) / rng
    out["inside_bar"] = ((df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))).astype(int)
    out["outside_bar"] = ((df["high"] > df["high"].shift(1)) & (df["low"] < df["low"].shift(1))).astype(int)
    out["engulfing"] = np.sign(body) * ((body.abs() > body.shift(1).abs()) & (np.sign(body) != np.sign(body.shift(1)))).astype(int)
    out["pin_bar"] = np.where(out["lower_wick_pct"] > 0.66, 1, np.where(out["upper_wick_pct"] > 0.66, -1, 0))
    out["consec_same_dir"] = _consecutive(np.sign(body))
    return out


def _consecutive(sign: pd.Series) -> pd.Series:
    grp = (sign != sign.shift(1)).cumsum()
    return sign.groupby(grp).cumcount() + 1
