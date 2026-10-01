"""Technical feature families: returns/volatility, moving averages, trend, mean-reversion, breakout,
microstructure, money flow. All use only data up to and including the current bar."""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.features.registry import feature


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def wma(s: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


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
    flow = tp * df["tick_count"].clip(lower=1)
    pos = flow.where(tp > tp.shift(1), 0.0)
    neg = flow.where(tp < tp.shift(1), 0.0)
    ratio = pos.rolling(n, min_periods=n).sum() / neg.rolling(n, min_periods=n).sum().replace(0, np.nan)
    return 100 - 100 / (1 + ratio)


# --------------------------------------------------------------------------- returns & volatility
@feature("returns", "volatility", lookback=96)
def f_returns(df, ctx):
    c = np.log(df["close"])
    out = pd.DataFrame(index=df.index)
    for k in (1, 4, 16, 96):
        out[f"ret_{k}"] = c.diff(k)
    return out


@feature("atr", "volatility", lookback=200)
def f_atr(df, ctx):
    a = atr(df, 14)
    out = pd.DataFrame({"atr14": a, "atr14_pct": a / df["close"]})
    out["atr_ratio_14_100"] = a / atr(df, 100)
    return out


@feature("realised_vol", "volatility", lookback=400)
def f_realised_vol(df, ctx):
    r = np.log(df["close"]).diff()
    out = pd.DataFrame(index=df.index)
    out["rv_20"] = r.rolling(20, min_periods=20).std()
    out["rv_100"] = r.rolling(100, min_periods=100).std()
    out["rv_ratio"] = out["rv_20"] / out["rv_100"]
    # vol regime tercile over the last 400 bars (0 low, 1 mid, 2 high), computed on history only
    out["vol_tercile"] = out["rv_20"].rolling(400, min_periods=100).apply(
        lambda x: 0 if x[-1] <= np.nanpercentile(x[:-1], 33) else (2 if x[-1] > np.nanpercentile(x[:-1], 67) else 1), raw=True)
    hl = np.log(df["high"] / df["low"])
    out["parkinson_20"] = np.sqrt((hl ** 2).rolling(20, min_periods=20).mean() / (4 * np.log(2)))
    return out


# --------------------------------------------------------------------------- moving averages
@feature("moving_averages", "trend", lookback=300)
def f_mas(df, ctx):
    """Several MA families and lengths; distances are in ATR so they compare across regimes."""
    a = atr(df, 14)
    c = df["close"]
    out = pd.DataFrame(index=df.index)
    for n in (20, 50, 100, 200):
        out[f"dist_ema{n}_atr"] = (c - ema(c, n)) / a
        out[f"slope_ema{n}"] = ema(c, n).diff(5) / a
    for n in (50, 200):
        out[f"dist_sma{n}_atr"] = (c - sma(c, n)) / a
    out["dist_hma55_atr"] = (c - hma(c, 55)) / a
    # EMA stack / ribbon state (K-RB style): +1 fully bullish order, -1 fully bearish, 0 mixed
    e = [ema(c, n) for n in (8, 21, 50, 100, 200)]
    bull = np.all([e[i] > e[i + 1] for i in range(4)], axis=0)
    bear = np.all([e[i] < e[i + 1] for i in range(4)], axis=0)
    out["ribbon_state"] = np.where(bull, 1, np.where(bear, -1, 0))
    out["ribbon_width_atr"] = (e[0] - e[-1]).abs() / a
    out["bars_since_ribbon_flip"] = _bars_since_change(pd.Series(out["ribbon_state"].values, index=df.index))
    out["sma50_ema50_cross"] = (sma(c, 50) - ema(c, 50)) / a
    return out


def _bars_since_change(s: pd.Series) -> pd.Series:
    changed = s != s.shift(1)
    grp = changed.cumsum()
    return s.groupby(grp).cumcount()


@feature("trend_strength", "trend", lookback=200)
def f_trend(df, ctx):
    out = pd.DataFrame(index=df.index)
    out["adx14"] = adx(df, 14)
    out["adx14_bucket"] = pd.cut(out["adx14"], [-1, 18, 25, 40, 200], labels=False)
    n = 20
    hi, lo = df["high"].rolling(n).max(), df["low"].rolling(n).min()
    out["donchian_pos_20"] = (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    return out


# --------------------------------------------------------------------------- mean reversion
@feature("mean_reversion", "mean_reversion", lookback=100)
def f_mr(df, ctx):
    out = pd.DataFrame(index=df.index)
    c = df["close"]
    m, s = sma(c, 20), c.rolling(20, min_periods=20).std()
    out["bb_z_20"] = (c - m) / s.replace(0, np.nan)
    out["bb_pctb_20"] = (c - (m - 2 * s)) / (4 * s).replace(0, np.nan)
    out["rsi14"] = rsi(c, 14)
    out["rsi14_extreme"] = np.where(out["rsi14"] > 75, 1, np.where(out["rsi14"] < 25, -1, 0))
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vwap_like = (tp * df["tick_count"]).rolling(48).sum() / df["tick_count"].rolling(48).sum().replace(0, np.nan)
    out["dist_vwap48_atr"] = (c - vwap_like) / atr(df, 14)
    return out


@feature("money_flow", "mean_reversion", lookback=50)
def f_mfi(df, ctx):
    """KOG-MFI replica: MFI(12) with zones 20/40/60/80."""
    m = mfi(df, 12)
    out = pd.DataFrame({"mfi12": m})
    out["mfi_zone"] = pd.cut(m, [-1, 20, 40, 60, 80, 101], labels=False)
    out["mfi_slope"] = m.diff(3)
    out["bars_since_mfi_zone_cross"] = _bars_since_change(out["mfi_zone"])
    return out


# --------------------------------------------------------------------------- breakout
@feature("breakout", "breakout", lookback=100)
def f_breakout(df, ctx):
    out = pd.DataFrame(index=df.index)
    a = atr(df, 14)
    for n in (8, 24, 96):
        hi, lo = df["high"].rolling(n).max().shift(1), df["low"].rolling(n).min().shift(1)
        out[f"range_width_{n}_atr"] = (hi - lo) / a
        out[f"dist_high_{n}_atr"] = (df["close"] - hi) / a
        out[f"dist_low_{n}_atr"] = (df["close"] - lo) / a
    out["compression_8_96"] = out["range_width_8_atr"] / out["range_width_96_atr"]
    out["tick_vol_ratio_20"] = df["tick_count"] / df["tick_count"].rolling(20).median().replace(0, np.nan)
    return out


# --------------------------------------------------------------------------- microstructure
@feature("microstructure", "microstructure", lookback=50)
def f_micro(df, ctx):
    out = pd.DataFrame(index=df.index)
    out["spread_atr"] = df["spread"] / atr(df, 14)
    out["spread_rel_median_48"] = df["spread"] / df["spread"].rolling(48).median().replace(0, np.nan)
    sd = df["tick_count"].rolling(48).std()
    out["tick_count_z_48"] = ((df["tick_count"] - df["tick_count"].rolling(48).mean()) / sd.replace(0, np.nan)).where(sd.notna()).fillna(0.0).where(sd.notna())
    return out
