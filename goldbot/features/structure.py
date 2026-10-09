"""Price-structure features: swing points, support/resistance levels, gaps, candle anatomy, patterns.

Levels are built from *confirmed* swing points only (a swing high at bar i is confirmed at bar i+lag),
so a level never exists before the system could have known it.
"""
from __future__ import annotations

from typing import Iterator

import numpy as np
import pandas as pd

from goldbot.data.timeutil import epoch_ns
from goldbot.features.columns import Columns
from goldbot.features.registry import FeatureCtx, feature
from goldbot.features.technical import atr


def swing_points(df: pd.DataFrame, lag: int = 5) -> tuple[pd.Series, pd.Series]:
    """Boolean series marking swing highs/lows at their own bar; confirmation happens `lag` bars later."""
    h, lo = df["high"], df["low"]
    is_high = (h == h.rolling(2 * lag + 1, center=True).max())
    is_low = (lo == lo.rolling(2 * lag + 1, center=True).min())
    return is_high.fillna(False), is_low.fillna(False)


def _walk_levels(df: pd.DataFrame, lag: int, max_levels: int) -> Iterator[tuple[int, list[list[float]], bool]]:
    """The level book bar by bar: yields (i, known) with known = [[price, touches, created_idx], ...] as it stands
    at bar i (the same list object, updated in place) and whether the book changed at bar i. Levels within 0.3 ATR
    merge, touch count grows."""
    is_high, is_low = swing_points(df, lag)
    hi_flag, lo_flag = is_high.to_numpy(), is_low.to_numpy()
    a = atr(df, 14).bfill().to_numpy()
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    known: list[list[float]] = []  # [price, touches, created_idx]
    for i in range(len(df)):
        j = i - lag  # swing at j is confirmed now
        changed = False
        if j >= 0:
            for flag, px in ((hi_flag[j], highs[j]), (lo_flag[j], lows[j])):
                if not flag:
                    continue
                changed = True
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
        yield i, known, changed


def confirmed_levels(df: pd.DataFrame, lag: int = 5, max_levels: int = 60) -> list[list[tuple[float, int, int]]]:
    """For each bar, the list of (price, touches, age_bars) of confirmed swing levels known at that bar.

    O(n * max_levels); fine for a few hundred thousand bars. Levels within 0.3 ATR merge, touch count grows.
    """
    return [[(float(lv[0]), int(lv[1]), i - int(lv[2])) for lv in known] for i, known, _ in _walk_levels(df, lag, max_levels)]


@feature("support_resistance", "structure", lookback=600)
def f_levels(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Nearest confirmed level above (resistance) and at/below (support) the close in ATR, with its touches and
    age, and the number of levels within 1 ATR. One pass over the level book as `confirmed_levels` builds it
    (the first lowest level above / first highest at or below in book order, as min/max over the snapshot pick)."""
    lag = ctx.get("swing_lag", 5)
    a = atr(df, 14).bfill().to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    up = np.full(n, np.nan)
    dn = np.full(n, np.nan)
    up_t = np.zeros(n)
    dn_t = np.zeros(n)
    up_age = np.full(n, np.nan)
    dn_age = np.full(n, np.nan)
    n_near = np.zeros(n)
    # the book only changes on bars that confirm a swing: keep one snapshot per change, then score every bar
    # against its snapshot with array operations (same comparisons and arithmetic per level as a scan in book order)
    snaps: list[list[tuple[float, float, float]]] = []
    snap_of = np.zeros(n, dtype=np.intp)
    for i, known, changed in _walk_levels(df, lag, 60):
        if changed or not snaps:
            snaps.append([(float(lv[0]), float(lv[1]), float(lv[2])) for lv in known])
        snap_of[i] = len(snaps) - 1
    width = max((len(x) for x in snaps), default=0)
    if n == 0 or width == 0:
        return pd.DataFrame({"dist_res_atr": up, "res_touches": up_t, "res_age": up_age, "dist_sup_atr": dn,
                             "sup_touches": dn_t, "sup_age": dn_age, "levels_within_1atr": n_near}, index=df.index)
    book = np.full((len(snaps), 3, width), np.nan)             # price, touches, created bar; NaN = empty slot
    for k, lvls in enumerate(snaps):
        if lvls:
            book[k, :, :len(lvls)] = np.array(lvls).T
    for s0 in range(0, n, 20000):
        rows = np.arange(s0, min(s0 + 20000, n))
        b = book[snap_of[rows]]
        px, touches, created = b[:, 0], b[:, 1], b[:, 2]
        c, ai = close[rows][:, None], a[rows][:, None]
        n_near[rows] = (np.abs(px - c) <= 1.0 * ai).sum(axis=1)
        for above, out_d, out_t, out_age in ((True, up, up_t, up_age), (False, dn, dn_t, dn_age)):
            mask = px > c if above else px <= c                  # NaN prices and NaN closes match neither side
            has = mask.any(axis=1)
            if not has.any():
                continue
            best = np.where(mask, px, np.inf if above else -np.inf)
            best = best.min(axis=1) if above else best.max(axis=1)
            pick = np.argmax(mask & (px == best[:, None]), axis=1)   # first such level in book order
            r = np.flatnonzero(has)
            lvl = px[r, pick[r]]
            cc, aa = close[rows[r]], a[rows[r]]
            out_d[rows[r]] = (lvl - cc) / aa if above else (cc - lvl) / aa
            out_t[rows[r]] = touches[r, pick[r]]
            out_age[rows[r]] = rows[r] - created[r, pick[r]]
    return pd.DataFrame({
        "dist_res_atr": up, "res_touches": up_t, "res_age": up_age,
        "dist_sup_atr": dn, "sup_touches": dn_t, "sup_age": dn_age,
        "levels_within_1atr": n_near,
    }, index=df.index)


@feature("swings", "structure", lookback=200, signed={"hh": 0.5, "hl": 0.5, "structure_state": 0.0})
def f_swings(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    lag = ctx.get("swing_lag", 5)
    is_high, is_low = swing_points(df, lag)
    # only confirmed swings are usable: shift by lag
    ch, cl = is_high.shift(lag).fillna(False), is_low.shift(lag).fillna(False)
    sh = df["high"].shift(lag).where(ch).ffill()
    sl = df["low"].shift(lag).where(cl).ffill()
    prev_sh = df["high"].shift(lag).where(ch).shift(1).ffill()
    prev_sl = df["low"].shift(lag).where(cl).shift(1).ffill()
    a = atr(df, 14)
    out = Columns(df.index)
    out["hh"] = (sh > prev_sh).astype(int)
    out["hl"] = (sl > prev_sl).astype(int)
    out["structure_state"] = out["hh"] + out["hl"] - 1  # +1 uptrend structure, -1 downtrend, 0 mixed
    out["dist_last_swing_high_atr"] = (df["close"] - sh) / a
    out["dist_last_swing_low_atr"] = (df["close"] - sl) / a
    return out.frame()


@feature("gaps", "structure", lookback=20, signed={"gap_atr": 0.0, "last_gap_level_dist_atr": 0.0})
def f_gaps(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Gaps between consecutive bars (session break / weekend / news) in ATR, and whether filled."""
    a = atr(df, 14)
    gap = df["open"] - df["close"].shift(1)
    out = Columns(df.index)
    out["gap_atr"] = gap / a
    # time gap in bars implied by timestamp (large => session break or weekend)
    dt = pd.Series(epoch_ns(df["ts_utc"]), index=df.index).diff() / 1e9
    med = dt.rolling(50, min_periods=5).median()
    out["after_break"] = (dt > 3 * med).astype(int)
    # most recent unfilled gap: level and distance
    gap_lvl = df["close"].shift(1).where(gap.abs() > 0.5 * a)
    out["last_gap_level_dist_atr"] = (df["close"] - gap_lvl.ffill()) / a
    filled = ((gap > 0) & (df["low"] <= gap_lvl)) | ((gap < 0) & (df["high"] >= gap_lvl))
    out["gap_filled_same_bar"] = filled.astype(int)
    return out.frame()


@feature("candles", "structure", lookback=5, signed={"body_pct": 0.0, "engulfing": 0.0, "pin_bar": 0.0})
def f_candles(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    body = (df["close"] - df["open"])
    out = Columns(df.index)
    out["body_pct"] = body / rng
    out["upper_wick_pct"] = (df["high"] - df[["open", "close"]].max(axis=1)) / rng
    out["lower_wick_pct"] = (df[["open", "close"]].min(axis=1) - df["low"]) / rng
    out["inside_bar"] = ((df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))).astype(int)
    out["outside_bar"] = ((df["high"] > df["high"].shift(1)) & (df["low"] < df["low"].shift(1))).astype(int)
    out["engulfing"] = np.sign(body) * ((body.abs() > body.shift(1).abs()) & (np.sign(body) != np.sign(body.shift(1)))).astype(int)
    out["pin_bar"] = np.where(out["lower_wick_pct"] > 0.66, 1, np.where(out["upper_wick_pct"] > 0.66, -1, 0))
    out["consec_same_dir"] = _consecutive(pd.Series(np.sign(body), index=df.index))
    return out.frame()


def _consecutive(sign: pd.Series) -> pd.Series:
    grp = (sign != sign.shift(1)).cumsum()
    return sign.groupby(grp).cumcount() + 1
