"""Technical feature families: returns/volatility, moving averages, trend, mean-reversion, breakout,
microstructure, money flow. All use only data up to and including the current bar."""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.features.columns import Columns
from goldbot.features.registry import FeatureCtx, feature


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ATR. True range = NaN-skipping max of high-low, |high-prev close|, |low-prev close| (np.fmax gives
    the same values as the row-wise DataFrame max, without building a frame)."""
    h, lo, c = (df[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    prev_close = np.concatenate(([np.nan], c[:-1])) if len(c) else c
    tr = np.fmax(np.fmax(h - lo, np.abs(h - prev_close)), np.abs(lo - prev_close))
    return pd.Series(tr, index=df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def wma(s: pd.Series, n: int) -> pd.Series:
    """Linearly weighted MA over n bars (NaN until n valid values). The same np.dot per full window as
    `rolling(n, min_periods=n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)`, bit for bit, without pandas'
    per-window apply machinery."""
    w = np.arange(1, n + 1, dtype=float)
    v = s.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) >= n:
        win = np.lib.stride_tricks.sliding_window_view(v, n)
        ws = w.sum()
        dot = np.dot
        for j in np.flatnonzero(~np.isnan(win).any(axis=1)):
            out[j + n - 1] = dot(win[j], w) / ws
    return pd.Series(out, index=s.index)


def hma(s: pd.Series, n: int) -> pd.Series:
    half, sq = max(int(n / 2), 1), max(int(np.sqrt(n)), 1)
    return wma(2 * wma(s, half) - wma(s, n), sq)


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def mfi(df: pd.DataFrame, n: int = 12) -> pd.Series:
    """Money Flow Index on tick count as the volume proxy (CFD volume is tick count, never 'volume')."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    # a tiny floor (not 1): Dukascopy volumes are fractional, and a floor of 1 would flatten them all to equal weight
    flow = tp * df["tick_count"].astype(float).clip(lower=1e-9)
    pos = flow.where(tp > tp.shift(1), 0.0)
    neg = flow.where(tp < tp.shift(1), 0.0)
    ratio = pos.rolling(n, min_periods=n).sum() / neg.rolling(n, min_periods=n).sum().replace(0, np.nan)
    return 100 - 100 / (1 + ratio)


# --------------------------------------------------------------------------- returns & volatility
@feature("returns", "volatility", lookback=96, signed={r"ret_\d+": 0.0})
def f_returns(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    c = np.log(df["close"])
    out = Columns(df.index)
    for k in (1, 4, 16, 96):
        out[f"ret_{k}"] = c.diff(k)
    return out.frame()


@feature("atr", "volatility", lookback=200)
def f_atr(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    a = atr(df, 14)
    out = Columns(df.index, {"atr14": a, "atr14_pct": a / df["close"]})
    out["atr_ratio_14_100"] = a / atr(df, 100)
    return out.frame()


@feature("realised_vol", "volatility", lookback=400)
def f_realised_vol(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    r = np.log(df["close"]).diff()
    out = Columns(df.index)
    out["rv_20"] = r.rolling(20, min_periods=20).std()
    out["rv_100"] = r.rolling(100, min_periods=100).std()
    out["rv_ratio"] = out["rv_20"] / out["rv_100"]
    # vol regime tercile over the last 400 bars (0 low, 1 mid, 2 high), computed on history only
    out["vol_tercile"] = _vol_tercile(out["rv_20"].to_numpy(dtype=float), 400, 100)
    hl = pd.Series(np.log(df["high"] / df["low"]), index=df.index)
    out["parkinson_20"] = np.sqrt((hl ** 2).rolling(20, min_periods=20).mean() / (4 * np.log(2)))
    return out.frame()


def _nan_percentiles_sorted(srt: np.ndarray, k: np.ndarray, q: float) -> np.ndarray:
    """Row-wise np.nanpercentile(row, q) (method 'linear') of rows sorted ascending with their k valid values first
    (NaN sorts last). Replicates numpy's arithmetic step by step (virtual index (k-1)*q/100, floor/next with numpy's
    bounds rules, gamma, _lerp), so each value is bit-identical to the 1-D call on the row's valid values."""
    qq = np.true_divide(q, 100)
    virtual = (k - 1) * qq
    prev = np.floor(virtual)
    nxt = prev + 1
    above = virtual >= k - 1
    prev[above] = -1
    nxt[above] = -1
    below = virtual < 0
    prev[below] = 0
    nxt[below] = 0
    gamma = virtual - prev
    # numpy indexes the 1-D array of valid values, where -1 is its last valid value
    pi = np.where(prev < 0, k - 1, prev).astype(np.intp)
    ni = np.where(nxt < 0, k - 1, nxt).astype(np.intp)
    rows = np.arange(len(srt))
    a = srt[rows, np.clip(pi, 0, srt.shape[1] - 1)]
    b = srt[rows, np.clip(ni, 0, srt.shape[1] - 1)]
    diff = b - a
    res = np.add(a, diff * gamma)
    np.subtract(b, diff * (1 - gamma), out=res, where=gamma >= 0.5)
    res[k == 0] = np.nan
    return res


def _vol_tercile(rv: np.ndarray, window: int, min_periods: int, chunk: int = 8192) -> np.ndarray:
    """Vol-regime tercile of each bar against the previous window-1 bars (history only): 0 if the value is at or
    below their 33rd nan-percentile, 2 if above the 67th, else 1 (also when the value itself is NaN); NaN while the
    window holds fewer than min_periods valid values. Identical to the rolling(window, min_periods).apply of
    `0 if x[-1] <= nanpercentile(x[:-1], 33) else (2 if x[-1] > nanpercentile(x[:-1], 67) else 1)`, vectorised."""
    n = len(rv)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    valid = ~np.isnan(rv)
    csum = np.concatenate(([0], np.cumsum(valid)))
    idx = np.arange(n)
    count = csum[idx + 1] - csum[np.maximum(idx + 1 - window, 0)]      # valid values in the window ending at i
    todo = np.flatnonzero(count >= min_periods)
    if not len(todo):
        return out
    padded = np.concatenate((np.full(window - 1, np.nan), rv))
    hist = np.lib.stride_tricks.sliding_window_view(padded, window - 1)    # hist[i] = rv[i-window+1 .. i-1]
    for s in range(0, len(todo), chunk):
        rows = todo[s:s + chunk]
        srt = np.sort(hist[rows], axis=1)
        k = (~np.isnan(srt)).sum(axis=1)
        p33 = _nan_percentiles_sorted(srt, k, 33)
        p67 = _nan_percentiles_sorted(srt, k, 67)
        x = rv[rows]
        out[rows] = np.where(x <= p33, 0.0, np.where(x > p67, 2.0, 1.0))
    return out


# --------------------------------------------------------------------------- moving averages
@feature("moving_averages", "trend", lookback=300,
         signed={r"dist_(ema|sma|hma)\d+_atr": 0.0, r"slope_ema\d+": 0.0, "ribbon_state": 0.0, "sma50_ema50_cross": 0.0})
def f_mas(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """Several MA families and lengths; distances are in ATR so they compare across regimes."""
    a = atr(df, 14)
    c = df["close"]
    out = Columns(df.index)
    em = {n: ema(c, n) for n in (8, 20, 21, 50, 100, 200)}      # each EMA once (deterministic, reused below)
    sm = {n: sma(c, n) for n in (50, 200)}
    for n in (20, 50, 100, 200):
        out[f"dist_ema{n}_atr"] = (c - em[n]) / a
        out[f"slope_ema{n}"] = em[n].diff(5) / a
    for n in (50, 200):
        out[f"dist_sma{n}_atr"] = (c - sm[n]) / a
    out["dist_hma55_atr"] = (c - hma(c, 55)) / a
    # EMA stack / ribbon state (K-RB style): +1 fully bullish order, -1 fully bearish, 0 mixed
    e = [em[n] for n in (8, 21, 50, 100, 200)]
    bull = np.all([e[i] > e[i + 1] for i in range(4)], axis=0)
    bear = np.all([e[i] < e[i + 1] for i in range(4)], axis=0)
    out["ribbon_state"] = np.where(bull, 1, np.where(bear, -1, 0))
    out["ribbon_width_atr"] = (e[0] - e[-1]).abs() / a
    out["bars_since_ribbon_flip"] = _bars_since_change(pd.Series(out["ribbon_state"].to_numpy(), index=df.index))
    out["sma50_ema50_cross"] = (sm[50] - em[50]) / a
    return out.frame()


def _bars_since_change(s: pd.Series) -> pd.Series:
    changed = s != s.shift(1)
    grp = changed.cumsum()
    return s.groupby(grp).cumcount()


@feature("trend_strength", "trend", lookback=200, signed={"donchian_pos_20": 0.5})
def f_trend(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    out = Columns(df.index)
    out["adx14"] = adx(df, 14)
    out["adx14_bucket"] = pd.cut(out["adx14"], [-1, 18, 25, 40, 200], labels=False)
    n = 20
    hi, lo = df["high"].rolling(n).max(), df["low"].rolling(n).min()
    out["donchian_pos_20"] = (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    return out.frame()


# --------------------------------------------------------------------------- mean reversion
@feature("mean_reversion", "mean_reversion", lookback=100,
         signed={"bb_z_20": 0.0, "bb_pctb_20": 0.5, "rsi14": 50.0, "rsi14_extreme": 0.0, "dist_vwap48_atr": 0.0})
def f_mr(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    out = Columns(df.index)
    c = df["close"]
    m, s = sma(c, 20), c.rolling(20, min_periods=20).std()
    out["bb_z_20"] = (c - m) / s.replace(0, np.nan)
    out["bb_pctb_20"] = (c - (m - 2 * s)) / (4 * s).replace(0, np.nan)
    out["rsi14"] = rsi(c, 14)
    out["rsi14_extreme"] = np.where(out["rsi14"] > 75, 1, np.where(out["rsi14"] < 25, -1, 0))
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vwap_like = (tp * df["tick_count"]).rolling(48).sum() / df["tick_count"].rolling(48).sum().replace(0, np.nan)
    out["dist_vwap48_atr"] = (c - vwap_like) / atr(df, 14)
    return out.frame()


@feature("money_flow", "mean_reversion", version="2", lookback=50,
         signed={"mfi12": 50.0, "mfi_zone": 2.0, "mfi_slope": 0.0})   # v2: fractional volumes keep their weight
def f_mfi(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    """KOG-MFI replica: MFI(12) with zones 20/40/60/80."""
    m = mfi(df, 12)
    out = Columns(df.index, {"mfi12": m})
    out["mfi_zone"] = pd.cut(m, [-1, 20, 40, 60, 80, 101], labels=False)
    out["mfi_slope"] = m.diff(3)
    out["bars_since_mfi_zone_cross"] = _bars_since_change(out["mfi_zone"])
    return out.frame()


# --------------------------------------------------------------------------- breakout
@feature("breakout", "breakout", lookback=100)
def f_breakout(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    out = Columns(df.index)
    a = atr(df, 14)
    for n in (8, 24, 96):
        hi, lo = df["high"].rolling(n).max().shift(1), df["low"].rolling(n).min().shift(1)
        out[f"range_width_{n}_atr"] = (hi - lo) / a
        out[f"dist_high_{n}_atr"] = (df["close"] - hi) / a
        out[f"dist_low_{n}_atr"] = (df["close"] - lo) / a
    out["compression_8_96"] = out["range_width_8_atr"] / out["range_width_96_atr"]
    out["tick_vol_ratio_20"] = df["tick_count"] / df["tick_count"].rolling(20).median().replace(0, np.nan)
    return out.frame()


# --------------------------------------------------------------------------- microstructure
@feature("microstructure", "microstructure", lookback=50)
def f_micro(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    out = Columns(df.index)
    out["spread_atr"] = df["spread"] / atr(df, 14)
    out["spread_rel_median_48"] = df["spread"] / df["spread"].rolling(48).median().replace(0, np.nan)
    sd = df["tick_count"].rolling(48).std()
    out["tick_count_z_48"] = ((df["tick_count"] - df["tick_count"].rolling(48).mean()) / sd.replace(0, np.nan)).where(sd.notna()).fillna(0.0).where(sd.notna())
    return out.frame()
