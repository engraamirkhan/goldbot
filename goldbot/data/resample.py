"""Ticks -> 1m bars -> higher timeframes.

* Bars are labelled by their OPEN (`label='left', closed='left'`); a bar is complete only at
  `ts_utc + tf`. `visible_at` is written on every bar for that reason.
* Bid and ask OHLC are kept separately; mid is derived at feature time.
* No bars are formed while the session table says the market is closed, and no placeholder
  bars are ever written.
* Daily bars use the feature-day (COMEX settlement 13:30 New York); weekly bars run Sunday to Friday.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.timeutil import feature_day, feature_day_close, floor_tf

BAR_COLUMNS = [
    "ts_utc", "visible_at", "bid_open", "bid_high", "bid_low", "bid_close",
    "ask_open", "ask_high", "ask_low", "ask_close", "tick_count", "spread_mean", "spread_max",
]


def ticks_to_1m(ticks: pd.DataFrame, sessions: SessionTable = DEFAULT_SESSIONS) -> pd.DataFrame:
    """ticks: columns ts_utc (tz-aware), bid, ask. Returns 1-minute bars (only in-session minutes)."""
    if ticks.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    t = ticks.sort_values("ts_utc").copy()
    t["ts_utc"] = pd.to_datetime(t["ts_utc"], utc=True)
    t = t[(t["bid"] > 0) & (t["ask"] >= t["bid"])]
    t["spread"] = t["ask"] - t["bid"]
    t["minute"] = floor_tf(pd.DatetimeIndex(t["ts_utc"]), 60)
    g = t.groupby("minute", sort=True)
    bars = pd.DataFrame({
        "bid_open": g["bid"].first(), "bid_high": g["bid"].max(), "bid_low": g["bid"].min(), "bid_close": g["bid"].last(),
        "ask_open": g["ask"].first(), "ask_high": g["ask"].max(), "ask_low": g["ask"].min(), "ask_close": g["ask"].last(),
        "tick_count": g["bid"].size(), "spread_mean": g["spread"].mean(), "spread_max": g["spread"].max(),
    })
    bars.index.name = "ts_utc"
    bars = bars.reset_index()
    bars = bars[sessions.is_open(pd.DatetimeIndex(bars["ts_utc"]))]
    bars.insert(1, "visible_at", bars["ts_utc"] + pd.Timedelta(seconds=60))
    return bars.reset_index(drop=True)[BAR_COLUMNS]


def _agg(bars: pd.DataFrame, key: pd.DatetimeIndex | pd.Series) -> pd.DataFrame:
    b = bars.copy()
    b["_k"] = np.asarray(key)
    tc = b["tick_count"].to_numpy(dtype=float)
    b["_w"] = np.where(tc > 0, tc, 1e-9)        # volume-weighted spread; fractional volumes (Dukascopy) keep their weight
    b["_sw"] = b["spread_mean"].to_numpy() * b["_w"].to_numpy()
    g = b.groupby("_k", sort=True)
    out = pd.DataFrame({
        "bid_open": g["bid_open"].first(), "bid_high": g["bid_high"].max(), "bid_low": g["bid_low"].min(),
        "bid_close": g["bid_close"].last(),
        "ask_open": g["ask_open"].first(), "ask_high": g["ask_high"].max(), "ask_low": g["ask_low"].min(),
        "ask_close": g["ask_close"].last(),
        "tick_count": g["tick_count"].sum(),
        "spread_mean": g["_sw"].sum() / g["_w"].sum(),
        "spread_max": g["spread_max"].max(),
        "_last_minute": g["ts_utc"].last(),
    })
    out.index.name = "ts_utc"
    return out.reset_index()


def resample_bars(bars_1m: pd.DataFrame, tf: str, *, feature_day_tz: str = "America/New_York",
                  feature_day_anchor: str = "13:30") -> pd.DataFrame:
    """Aggregate 1m bars to `tf`. Intraday timeframes floor on the UTC clock; 1d uses the feature-day."""
    if bars_1m.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    idx = pd.DatetimeIndex(pd.to_datetime(bars_1m["ts_utc"], utc=True))
    if tf == "1d":
        day = feature_day(idx, feature_day_tz, feature_day_anchor)
        out = _agg(bars_1m, day)
        # ts_utc of a daily bar = the open of its feature-day (previous settlement), visible at its own settlement
        out["visible_at"] = feature_day_close(pd.DatetimeIndex(out["ts_utc"]), feature_day_tz, feature_day_anchor)
        out["ts_utc"] = feature_day_close(pd.DatetimeIndex(out["ts_utc"]) - pd.Timedelta(days=1), feature_day_tz, feature_day_anchor)
    elif tf == "1w":
        wk = floor_tf(idx, tf_seconds("1w"))
        out = _agg(bars_1m, wk)
        out["ts_utc"] = pd.DatetimeIndex(out["ts_utc"]).tz_localize("UTC") if pd.DatetimeIndex(out["ts_utc"]).tz is None else out["ts_utc"]
        out["visible_at"] = out["ts_utc"] + pd.Timedelta(days=7)
    else:
        sec = tf_seconds(tf)
        out = _agg(bars_1m, floor_tf(idx, sec))
        out["visible_at"] = out["ts_utc"] + pd.Timedelta(seconds=sec)
    # a bar whose last constituent minute is before the nominal close is still only visible at the nominal close
    return out.drop(columns=["_last_minute"])[BAR_COLUMNS].reset_index(drop=True)


def mid(bars: pd.DataFrame) -> pd.DataFrame:
    """Derived mid OHLC, computed at feature time rather than stored."""
    m = pd.DataFrame({"ts_utc": bars["ts_utc"], "visible_at": bars["visible_at"]})
    for c in ("open", "high", "low", "close"):
        m[c] = (bars[f"bid_{c}"] + bars[f"ask_{c}"]) / 2.0
    m["spread"] = bars["spread_mean"]
    m["tick_count"] = bars["tick_count"]
    return m
