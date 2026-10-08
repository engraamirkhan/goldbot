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
from goldbot.data.timeutil import epoch_ns, feature_day, feature_day_close, floor_tf, utc_index

BAR_COLUMNS = [
    "ts_utc", "visible_at", "bid_open", "bid_high", "bid_low", "bid_close",
    "ask_open", "ask_high", "ask_low", "ask_close", "tick_count", "spread_mean", "spread_max",
]


def ticks_to_1m(ticks: pd.DataFrame, sessions: SessionTable = DEFAULT_SESSIONS) -> pd.DataFrame:
    """ticks: columns ts_utc (tz-aware), bid, ask. Returns 1-minute bars (only in-session minutes)."""
    if ticks.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    fast = _ticks_to_1m_sorted(ticks, sessions)
    if fast is not None:
        return fast
    t = ticks.sort_values("ts_utc").copy()
    t["ts_utc"] = pd.to_datetime(t["ts_utc"], utc=True)
    t = t[(t["bid"] > 0) & (t["ask"] >= t["bid"])]
    t["spread"] = t["ask"] - t["bid"]
    t["minute"] = floor_tf(pd.DatetimeIndex(t["ts_utc"]), 60)
    g = t.groupby("minute", sort=True)
    if len(t) and t["bid"].dtype == np.float64 and t["ask"].dtype == np.float64:
        bars = _minute_bars(t, g["spread"].mean())
    else:
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


def _ticks_to_1m_sorted(ticks: pd.DataFrame, sessions: SessionTable) -> pd.DataFrame | None:
    """ticks_to_1m for the common case of float quotes with strictly increasing timestamps (the live engine's tick
    buffer), in numpy: the time sort is then the identity, each minute a contiguous run, and first/last/max/min/count
    are exact. The mean spread is pandas' groupby mean on the same values in the same order (its compensated sum).
    None (use the general path) for anything else."""
    if ticks["bid"].dtype != np.float64 or ticks["ask"].dtype != np.float64:
        return None
    ns = epoch_ns(utc_index(ticks["ts_utc"]))
    if len(ns) > 1 and not bool((np.diff(ns) > 0).all()):
        return None
    bid, ask = ticks["bid"].to_numpy(), ticks["ask"].to_numpy()
    ok = (bid > 0) & (ask >= bid)
    if not ok.any():
        return None
    ns, bid, ask = ns[ok], bid[ok], ask[ok]
    spread = ask - bid
    minute = (ns // 60_000_000_000) * 60_000_000_000
    starts = np.flatnonzero(np.concatenate(([True], minute[1:] != minute[:-1])))
    ends = np.append(starts[1:], len(minute)) - 1
    codes = np.repeat(np.arange(len(starts)), ends - starts + 1)
    keys = pd.DatetimeIndex(pd.to_datetime(minute[starts], unit="ns", utc=True))
    bars = pd.DataFrame({
        "ts_utc": keys, "visible_at": keys + pd.Timedelta(seconds=60),
        "bid_open": bid[starts], "bid_high": np.maximum.reduceat(bid, starts), "bid_low": np.minimum.reduceat(bid, starts),
        "bid_close": bid[ends],
        "ask_open": ask[starts], "ask_high": np.maximum.reduceat(ask, starts), "ask_low": np.minimum.reduceat(ask, starts),
        "ask_close": ask[ends],
        "tick_count": (ends - starts + 1).astype(np.int64),
        "spread_mean": pd.Series(spread).groupby(codes, sort=True).mean().to_numpy(),
        "spread_max": np.maximum.reduceat(spread, starts),
    })
    return bars[sessions.is_open(keys)].reset_index(drop=True)


def _minute_bars(t: pd.DataFrame, spread_mean: pd.Series) -> pd.DataFrame:
    """The per-minute OHLC/size/max aggregates of ticks_to_1m with numpy on the time-sorted ticks (each minute is
    one contiguous run; the valid-quote filter leaves no NaN, so first/last/max/min equal the groupby's). The mean
    spread keeps pandas' groupby mean (its compensated sum), passed in. Same frame, a fraction of the fixed cost of
    ten groupby aggregations, which dominated the engine's 10-second bar refresh."""
    minute = epoch_ns(pd.DatetimeIndex(t["minute"]))
    starts = np.flatnonzero(np.concatenate(([True], minute[1:] != minute[:-1])))
    ends = np.append(starts[1:], len(minute)) - 1
    out = {}
    for side in ("bid", "ask"):
        v = t[side].to_numpy()
        out[f"{side}_open"], out[f"{side}_high"] = v[starts], np.maximum.reduceat(v, starts)
        out[f"{side}_low"], out[f"{side}_close"] = np.minimum.reduceat(v, starts), v[ends]
    sp = t["spread"].to_numpy()
    out["tick_count"] = (ends - starts + 1).astype(np.int64)
    out["spread_mean"] = spread_mean.to_numpy()
    out["spread_max"] = np.maximum.reduceat(sp, starts)
    order = ["bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close",
             "tick_count", "spread_mean", "spread_max"]
    return pd.DataFrame({k: out[k] for k in order}, index=spread_mean.index)


def _agg(bars: pd.DataFrame, key: pd.DatetimeIndex | pd.Series) -> pd.DataFrame:
    b = bars.copy()
    # a tz-aware key goes in as a datetime column: np.asarray would box every row into a Timestamp object and the
    # groupby would parse them back (most of the cost of an intraday resample); same groups, same labels
    b["_k"] = key if isinstance(key, pd.DatetimeIndex) and key.tz is not None else np.asarray(key)
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
    idx = utc_index(bars_1m["ts_utc"])
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


_AGG_INPUTS = [c for c in BAR_COLUMNS if c not in ("ts_utc", "visible_at")]   # what a resampled bar is made of


def _group_keys(idx: pd.DatetimeIndex, tf: str, feature_day_tz: str, feature_day_anchor: str) -> np.ndarray:
    """The group key of each 1m bar in resample_bars, as int64 ns (sorted key order = output row order)."""
    if tf == "1d":
        return epoch_ns(feature_day(idx, feature_day_tz, feature_day_anchor))
    return epoch_ns(floor_tf(idx, tf_seconds(tf)))


class IncrementalResampler:
    """`resample_bars(bars_1m, tf)` for a 1m history that grows at the end and is trimmed at the start (the live
    engine's rolling buffer): only the groups whose 1m bars changed are aggregated again.

    Each call checks the new frame against the 1m bars of the previous call (timestamps, every aggregated column,
    dtypes); the frame may drop leading rows and append new ones. A group that lost leading rows and the last group
    (which may have gained rows) are re-aggregated from their 1m bars, every other group is reused, so the output is
    exactly resample_bars on the full frame: a group's bar is a function of its own 1m bars only. Anything else
    (rows replaced or reordered, non-increasing timestamps, dtype change) falls back to a full resample."""

    def __init__(self, tf: str, *, feature_day_tz: str = "America/New_York", feature_day_anchor: str = "13:30"):
        self.tf = tf
        self._tz, self._anchor = feature_day_tz, feature_day_anchor
        self._reset()
        self.full_builds = 0                     # diagnostics for tests: how often the cache could not be used

    def _reset(self) -> None:
        self._ts: np.ndarray | None = None       # int64 ns of the 1m bars covered
        self._cols: dict[str, np.ndarray] = {}
        self._dtypes: tuple = ()
        self._keys = np.empty(0, dtype=np.int64)  # group key per 1m bar
        self._out = pd.DataFrame()

    def _resample(self, bars: pd.DataFrame) -> pd.DataFrame:
        return resample_bars(bars, self.tf, feature_day_tz=self._tz, feature_day_anchor=self._anchor)

    def __call__(self, bars: pd.DataFrame) -> pd.DataFrame:
        if bars.empty or any(c not in bars.columns for c in ("ts_utc", *_AGG_INPUTS)):
            self._reset()
            return self._resample(bars)
        idx = utc_index(bars["ts_utc"])
        ts = epoch_ns(idx)
        if len(ts) > 1 and not bool((np.diff(ts) > 0).all()):
            self._reset()
            return self._resample(bars)
        cols = {c: bars[c].to_numpy() for c in _AGG_INPUTS}
        dtypes = tuple(bars[c].dtype for c in ("ts_utc", *_AGG_INPUTS))
        out = self._update(bars, idx, ts, cols, dtypes)
        if out is None:
            out = self._full(bars, idx, ts, cols, dtypes)
        return out.copy(deep=False)

    def _full(self, bars: pd.DataFrame, idx: pd.DatetimeIndex, ts: np.ndarray, cols: dict[str, np.ndarray],
              dtypes: tuple) -> pd.DataFrame:
        self.full_builds += 1
        out = self._resample(bars)
        keys = _group_keys(idx, self.tf, self._tz, self._anchor)
        if (len(keys) > 1 and not bool((np.diff(keys) >= 0).all())) or len(np.unique(keys)) != len(out):
            self._reset()                        # keys out of time order: never reuse groups
            return out
        self._ts, self._dtypes, self._keys, self._out = ts.copy(), dtypes, keys, out
        self._cols = {c: np.array(v, copy=True) for c, v in cols.items()}
        return out

    def _update(self, bars: pd.DataFrame, idx: pd.DatetimeIndex, ts: np.ndarray, cols: dict[str, np.ndarray],
                dtypes: tuple) -> pd.DataFrame | None:
        old = self._ts
        if old is None or dtypes != self._dtypes:
            return None
        k = int(np.searchsorted(old, ts[0]))
        m = len(old) - k                         # cached rows still present
        if k >= len(old) or old[k] != ts[0] or len(ts) < m or not np.array_equal(old[k:], ts[:m]):
            return None
        for c, v in cols.items():
            prev = self._cols[c][k:]
            if not np.array_equal(prev, v[:m], equal_nan=v.dtype.kind == "f"):
                return None
        new_keys = _group_keys(idx[m:], self.tf, self._tz, self._anchor) if len(ts) > m else np.empty(0, np.int64)
        keys = np.concatenate((self._keys[k:], new_keys))
        if len(new_keys) and (new_keys[0] < self._keys[-1] or not bool((np.diff(new_keys) >= 0).all())):
            return None
        first_key, last_key = keys[0], self._keys[-1]
        head_dirty = k > 0 and self._keys[k - 1] == first_key
        tail_dirty = len(ts) > m
        if first_key == last_key and (head_dirty or tail_dirty):
            return None                           # one group both trimmed and extended: rebuild it whole
        out_keys = np.unique(self._keys)
        lo = out_keys > first_key if head_dirty else out_keys >= first_key
        hi = out_keys < last_key if tail_dirty else out_keys <= last_key
        pieces = []
        if head_dirty:
            pieces.append(self._resample(bars.iloc[:int(np.searchsorted(keys, first_key, side="right"))]))
        pieces.append(self._out[lo & hi])
        if tail_dirty:
            pieces.append(self._resample(bars.iloc[int(np.searchsorted(keys, last_key, side="left")):]))
        pieces = [x for x in pieces if not x.empty] or [self._out.iloc[0:0]]
        out = pd.concat(pieces, ignore_index=True) if len(pieces) > 1 else pieces[0].reset_index(drop=True)
        if len(out) != len(np.unique(keys)):
            return None
        self._ts = ts.copy()
        self._cols = {c: np.concatenate((self._cols[c][k:], np.asarray(v[m:]))) for c, v in cols.items()}
        self._keys, self._out = keys, out
        return out


def mid(bars: pd.DataFrame) -> pd.DataFrame:
    """Derived mid OHLC, computed at feature time rather than stored."""
    m = pd.DataFrame({"ts_utc": bars["ts_utc"], "visible_at": bars["visible_at"]})
    for c in ("open", "high", "low", "close"):
        m[c] = (bars[f"bid_{c}"] + bars[f"ask_{c}"]) / 2.0
    m["spread"] = bars["spread_mean"]
    m["tick_count"] = bars["tick_count"]
    return m
