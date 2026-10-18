"""Price-action feature library (opt-in, family `price_action`): support/resistance, dynamic levels, channels,
trendlines, Fibonacci retracements, chart patterns and candlestick patterns, built from the owner's trading-course
notes (kept outside the repo; every name and rule here is our own wording). BACKLOG: owner request 2026-10-18.

Design rule (CLAUDE.md): "Every join to bars via asof_join on available_utc" and nothing is known before its bar
closed. Here that means: a value at row t uses bars that closed at or before row t's close only (row t's own close
included); a pattern is stamped on the bar whose close completes or confirms it, never on the extreme it refers to
(a swing pivot `pivot_len` bars after its extreme, a "confirmed" candle one bar after the pattern). Higher-timeframe
levels (1h/4h/1d built from the decision bars) enter a row only once their bucket has closed (`visible_at` <= the
row's close: the same visibility rule as goldbot.features.mtf). The test of record is prefix invariance: computing
on bars[:k] gives exactly the first k rows of computing on all bars (tests/test_price_action.py).

State. The level book, pattern machines and EMAs carry memory from the series start, so a fixed tail cannot
reproduce the full-history value: the features are tagged NEEDS_STATE and built only on a frame declared to start
the series (research). Serving them live needs a carried state (as goldbot.features.smc_live does); not built yet.

Three registered features (one `enable()` registers all three), so a pre-registration can group them by feature:

* `price_action` - levels and dynamic support/resistance:
  - level book: swing pivots (classic pivots of `pivot_len`, goldbot.features.smc_replica.pivot_points, on highs and
    lows or, with `swing_basis="close"`, on closes only: the line-chart swings) of the decision timeframe and of the
    longer 1h/4h/1d ones, booked only for timeframes weighing at least `level_min_weight` (default: 4h and 1d, "plan
    on the longer timeframes"), plus "event areas" (the congestion range a large impulse started from, registered
    once the impulse has ended). Every level is an AREA: its price +- `level_half_atr` x ATR; touches, breaks
    (a close beyond the whole area), retests, rejections and closes-off-the-line are judged against the area. Each
    level has a weight (longer timeframe = heavier, `tf_weights`) and a recency (half-life `recency_halflife_h` since
    its last touch); strength = weight x recency. Levels within `zone_atr` x ATR merge into a zone (width = zone
    height). Columns: room to the next opposing zone
    for a long / a short (ATR), the strength and width of the nearest zone below and above, the strength of the zone
    the close is in, `pa_level_break` (close crosses a level; the level flips role), `pa_level_retest` (within
    `retest_bars` of a break the bar comes back to the level from the new side and closes away), distance from the
    bar to the nearest level (ATR), and the structure stop: distance to the swing whose break invalidates a long
    (last swing low) or a short (last swing high), for sizing.
  - dynamic levels: distance and slope of EMA10/20/50/200 (ATR), EMA20 and EMA200 touch, EMA10-EMA50 and EMA10-EMA20
    gap and the change of the latter (widening > 0), distance to the 1h SMA10 (decision timeframes up to 1h; NaN
    above), `pa_ema_pullback` (EMA trend + touch of EMA10/20 + a reversal candle in the trend direction),
    `pa_ema_cross_pa` (EMA10/20 cross this or the previous bar + pin bar or engulfing in its direction), daily
    momentum sign (completed feature-days only).
  - channel: a parallel channel fitted on the last `channel_len` bars (least-squares mid line, offsets through the
    extreme high and low); direction, position 0..1, distance to the midpoint, width.
  - trendlines: down line through the last two confirmed swing highs when the second is lower (high, low, lower
    high), valid only after a later low below the low between them; up line mirrored. Distance, touch, touch with a
    reversal candle, close through the line (the line is then spent).
  - Fibonacci: 23.6/38.2/50/61.8% retracements of the last confirmed swing (alternating pivots), the distance to
    each, and `pa_fib50_confluence` (the 50% level within `fib_conf_atr` x ATR of the previous swing extreme the
    move broke, now flipped support/resistance).
  - volume: tick volume over the mean of the previous `vol_avg_bars` bars, and the veto (ratio > `vol_veto_mult`
    while within `veto_near_atr` of a level: a high-volume approach can run through, so no fade).
* `price_action_patterns` - chart patterns on confirmed pivots: triangles (descending: >= 2, preferably 3, swing lows
  in a flat band and >= 2 lower highs; ascending mirrored; symmetrical: lower highs and higher lows; no close outside
  the boundaries before the break; break = a CLOSE beyond, wicks do not count; retest of the broken boundary;
  failure = close through the opposite boundary of a flat-sided triangle; false breakout = close back inside within
  `fbo_bars`), head and shoulders and inverse (the head beyond both shoulders, shoulders within `shoulder_tol_atr`;
  neckline through the two troughs; break, retest, distance left to the head-to-neckline target), double and triple
  tops/bottoms (smc_replica.equal_levels: consecutive pivots within `eq_atr` x ATR; neckline, break) and the
  second-touch retest, ABCD legs (A-B impulse >= `abcd_min_atr` ATR, B-C retracing `abcd_lo`..`abcd_hi`, D = close
  beyond B), trend perfection (share of higher highs/lows minus lower ones over the last `perf_pivots` pivots).
* `price_action_candles` - candlestick patterns, each as +1 bullish / -1 bearish (doji, spinning top and inside bar
  are codes), with a `_conf` column (stamped one bar later, when the next bar confirms) and a `_lvl` column (the
  pattern while the bar is within `cs_level_atr` x ATR of a key level: book level or event area (as areas), latest
  swing, EMA20/50/200, the 50% retracement; EMA10 is left out, it sits inside most bars). Gold trades almost continuously, so a "gap" between bodies is defined on
  bodies: an open at or beyond the previous close, a star body at or beyond the previous body (strictly beyond for
  the abandoned baby). Also `pa_reversal_rank` (5 pin bar at a level, 4 engulfing, 3 doji at a level, 2 head and
  shoulders break, 1 double/triple top or bottom event; the last two only on 1h or longer bars) and its direction,
  `pa_wick_rejection`, `pa_off_the_line` (wick through a key level, body closes back on the original side),
  `pa_sr_reject` and `pa_confluence` (signed score; 0 without a rejection of a level).
  Not built (they need true price gaps or a variable shape): mat hold, tristar doji, dumpling top / frypan bottom,
  stick sandwich, separating line, meeting line, homing pigeon, stalled pattern.

All tunables sit in `PAParams`; `enable(params)` stamps a non-default block into the feature version.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.config import TF_SECONDS
from goldbot.data.timeutil import epoch_ns, feature_day, feature_day_close, floor_tf, utc_index
from goldbot.features.columns import Columns
from goldbot.features.registry import FEATURES, NEEDS_STATE, FeatureCtx, feature
from goldbot.features.smc_replica import atr_seeded, pivot_points
from goldbot.features.technical import atr as wilder_atr
from goldbot.features.technical import ema

NAME = "price_action"
NAME_PATTERNS = "price_action_patterns"
NAME_CANDLES = "price_action_candles"
FAMILY = "price_action"
VERSION = "1"
HIGHER_TFS = ("1h", "4h", "1d")
Arr = np.ndarray


class PAParams(FrozenRecord):
    """Every tunable of the price-action library. Distances and tolerances are in ATR(`atr_len`) of the decision bars."""

    pivot_len: int = Field(5, ge=1)                     # swing pivot: extreme of 2 x pivot_len + 1 bars
    atr_len: int = Field(14, ge=1)
    # level book
    level_half_atr: float = Field(0.15, ge=0.0)         # every level is an area: price +- this many ATR
    swing_basis: Literal["high_low", "close"] = "high_low"   # swings on highs/lows or on closes (line chart)
    zone_atr: float = Field(0.5, ge=0.0)                # levels closer than this merge into one zone
    max_levels: int = Field(8, ge=1)                    # newest levels kept per timeframe
    recency_halflife_h: float = Field(72.0, gt=0.0)
    level_min_weight: float = Field(3.0, ge=0.0)        # book only timeframes weighing at least this (4h and up)
    tf_weights: dict[str, float] = Field(default_factory=lambda: {"1m": 0.5, "5m": 0.5, "15m": 1.0, "30m": 1.0,
                                                                   "1h": 2.0, "4h": 3.0, "1d": 4.0})
    retest_bars: int = Field(20, ge=1)
    # event areas: the congestion an impulse started from
    event_move_atr: float = Field(4.0, gt=0.0)          # impulse = close moves this many ATR within event_bars
    event_bars: int = Field(5, ge=1)
    event_base_bars: int = Field(5, ge=1)               # bars of congestion before the impulse
    event_max_width_atr: float = Field(2.0, gt=0.0)     # a wider base is not a congestion zone
    event_weight: float = Field(2.0, ge=0.0)
    max_events: int = Field(10, ge=1)
    # dynamic levels
    ema_slope_bars: int = Field(5, ge=1)
    h1_ma_len: int = Field(10, ge=1)
    daily_mom_days: int = Field(5, ge=1)
    # channel
    channel_len: int = Field(50, ge=3)
    channel_trend_atr: float = Field(1.0, ge=0.0)       # mid-line rise over the window, below this = sideways
    # chart patterns
    tri_window: int = Field(80, ge=5)                   # bars a triangle may span
    tri_band_atr: float = Field(0.5, ge=0.0)            # flat side: pivots within this band
    fbo_bars: int = Field(5, ge=1)
    shoulder_tol_atr: float = Field(1.5, ge=0.0)
    hs_window: int = Field(100, ge=5)
    pattern_max_bars: int = Field(60, ge=1)
    eq_atr: float = Field(0.1, ge=0.0)                  # equal highs/lows (smc_replica.equal_levels)
    eq_atr_len: int = Field(200, ge=1)
    dbl_bounce_atr: float = Field(1.0, ge=0.0)          # rally off the first bottom before a second touch counts
    fib_conf_atr: float = Field(0.5, ge=0.0)
    abcd_min_atr: float = Field(2.0, ge=0.0)
    abcd_lo: float = Field(0.382, ge=0.0)
    abcd_hi: float = Field(0.618, le=1.0)
    perf_pivots: int = Field(4, ge=2)
    # volume
    vol_avg_bars: int = Field(10, ge=1)
    vol_veto_mult: float = Field(1.5, gt=0.0)
    veto_near_atr: float = Field(1.0, ge=0.0)
    # candles
    trend_bars: int = Field(10, ge=1)                   # prior trend: close change over this many bars ...
    trend_atr: float = Field(1.0, ge=0.0)               # ... beyond this many ATR
    doji_body: float = Field(0.1, ge=0.0)               # body <= this x range
    tiny_shadow: float = Field(0.1, ge=0.0)             # "no shadow": <= this x range
    long_shadow: float = Field(0.6, ge=0.0)             # gravestone / dragonfly shadow >= this x range
    ll_shadow: float = Field(0.3, ge=0.0)               # long-legged doji: both shadows >= this x range ...
    ll_range_atr: float = Field(1.0, ge=0.0)            # ... and range >= this many ATR
    shadow_mult: float = Field(2.0, ge=1.0)             # hammer family: shadow >= this x body
    star_long_atr: float = Field(0.5, ge=0.0)           # star / three methods / tower: a "long" body
    star_small: float = Field(0.3, ge=0.0)              # a "small" body, x the long body
    maru_body: float = Field(0.9, ge=0.0, le=1.0)
    min_range_atr: float = Field(0.5, ge=0.0)           # marubozu / pin bar need a real bar
    spin_body: float = Field(0.3, ge=0.0, le=1.0)
    vol_low: float = Field(0.5, ge=0.0)                 # spinning-top volume context, x mean volume
    vol_high: float = Field(1.5, ge=0.0)
    near_open_atr: float = Field(0.1, ge=0.0)           # three soldiers / crows: open near the previous close
    pin_wick: float = Field(0.65, ge=0.0, le=1.0)       # pin bar: wick >= this x range
    pin_body: float = Field(0.25, ge=0.0, le=1.0)
    cs_level_atr: float = Field(0.1, ge=0.0)            # "at a level"
    wick_bars: int = Field(5, ge=1)                     # wick rejection: compared with this many earlier bars

    def tag(self) -> str:
        """'' for the defaults, else a short hash of the non-default fields (used in the feature version)."""
        diff = self.model_dump(exclude_defaults=True)
        if not diff:
            return ""
        return hashlib.sha1(json.dumps(diff, sort_keys=True, default=str).encode()).hexdigest()[:8]


DEFAULT_PARAMS = PAParams()

FIBS = (0.236, 0.382, 0.5, 0.618)
LEVEL_COLUMNS = (
    "pa_room_long_atr", "pa_room_short_atr", "pa_sup_strength", "pa_res_strength", "pa_sup_width_atr",
    "pa_res_width_atr", "pa_zone_strength", "pa_level_break", "pa_level_retest", "pa_level_near_atr",
    "pa_stop_long_atr", "pa_stop_short_atr",
    *(f"pa_ema{n}_dist_atr" for n in (10, 20, 50, 200)), *(f"pa_ema{n}_slope_atr" for n in (10, 20, 50, 200)),
    "pa_ema20_touch", "pa_ema200_touch", "pa_ema10_50_gap_atr", "pa_ema10_20_gap_atr", "pa_ema10_20_gap_chg_atr",
    "pa_h1_ma10_dist_atr", "pa_ema_pullback", "pa_ema_cross_pa", "pa_daily_mom",
    "pa_chan_dir", "pa_chan_pos", "pa_chan_mid_dist_atr", "pa_chan_width_atr",
    "pa_tl_dn_dist_atr", "pa_tl_up_dist_atr", "pa_tl_touch", "pa_tl_touch_rev", "pa_tl_break",
    "pa_fib_dir", *(f"pa_fib{round(f * 1000)}_dist_atr" for f in FIBS), "pa_fib50_confluence",
    "pa_vol_ratio", "pa_vol_veto")
PATTERN_COLUMNS = (
    "pa_tri_sym", "pa_tri_asc", "pa_tri_desc", "pa_tri_apex_bars", "pa_tri_height_atr", "pa_tri_breakout_close",
    "pa_tri_fbo", "pa_tri_retest", "pa_tri_failed",
    "pa_hs", "pa_hs_neck_dist_atr", "pa_hs_break", "pa_hs_retest", "pa_hs_target_dist_atr",
    "pa_dbl", "pa_tpl", "pa_dbl_neck_dist_atr", "pa_dbl_break", "pa_dbl_retest",
    "pa_abcd_c", "pa_abcd_d", "pa_trend_perfection")
SIGNED_CANDLES = ("hammer", "invhammer", "engulf", "pierce", "star", "abandoned", "harami", "marubozu", "three",
                  "pin", "fakey", "strike", "methods", "tower")
CODE_CANDLES = ("doji", "spin", "inside")
CANDLE_COLUMNS = (
    *(f"pa_cs_{p}{s}" for p in (*SIGNED_CANDLES, *CODE_CANDLES) for s in ("", "_conf", "_lvl")),
    "pa_cs_three_best", "pa_cs_at_level", "pa_reversal_rank", "pa_reversal_dir", "pa_wick_rejection",
    "pa_off_the_line", "pa_sr_reject", "pa_confluence")
COLUMNS = (*LEVEL_COLUMNS, *PATTERN_COLUMNS, *CANDLE_COLUMNS)

# directional columns (positive favours a long): side-aligned for meta-label models (registry.SIGNED)
SIGNED = {
    r"pa_ema\d+_(dist|slope)_atr": 0.0, r"pa_ema10_(20|50)_gap_atr": 0.0, "pa_h1_ma10_dist_atr": 0.0,
    "pa_ema_pullback": 0.0, "pa_ema_cross_pa": 0.0, "pa_daily_mom": 0.0, "pa_chan_dir": 0.0,
    "pa_level_break": 0.0, "pa_level_retest": 0.0, r"pa_tl_(touch|touch_rev|break)": 0.0, "pa_fib_dir": 0.0,
    "pa_fib50_confluence": 0.0, r"pa_tri_(breakout_close|fbo|retest)": 0.0, r"pa_hs(_break|_retest)?": 0.0,
    r"pa_(dbl|tpl|dbl_break|dbl_retest)": 0.0, r"pa_abcd_[cd]": 0.0, "pa_trend_perfection": 0.0,
    rf"pa_cs_({'|'.join(SIGNED_CANDLES)})(_conf|_lvl)?": 0.0, r"pa_cs_(doji|spin|inside)_conf": 0.0,
    "pa_cs_three_best": 0.0, "pa_reversal_dir": 0.0, "pa_wick_rejection": 0.0, "pa_off_the_line": 0.0,
    "pa_sr_reject": 0.0, "pa_confluence": 0.0}


# --------------------------------------------------------------------------- helpers
def _sh(x: Arr, k: int) -> Arr:
    """x shifted k bars later (row t holds x[t-k]); NaN-padded (floats) or False-padded (bools)."""
    if k == 0:
        return x
    pad = np.zeros(min(k, len(x)), dtype=bool) if x.dtype == bool else np.full(min(k, len(x)), np.nan)
    return np.concatenate((pad, x[:-k])) if len(x) > k else pad


def _ffill(x: Arr) -> Arr:
    return pd.Series(x).ffill().to_numpy()


def _sgn(bull: Arr, bear: Arr) -> Arr:
    return np.where(bull & ~bear, 1, np.where(bear & ~bull, -1, 0)).astype(np.int64)


def decision_tf(ts: pd.DatetimeIndex) -> str:
    """The timeframe of the bars: the smallest gap between consecutive bars, named by goldbot.config.TF_SECONDS."""
    ns = epoch_ns(ts)
    d = np.diff(ns)
    d = d[d > 0]
    sec = int(d.min() // 1_000_000_000) if len(d) else 60
    names = {v: k for k, v in TF_SECONDS.items()}
    if sec not in names:
        raise ValueError(f"bars spaced {sec}s are not a known timeframe ({sorted(TF_SECONDS)})")
    return names[sec]


class _Buckets(FrozenRecord):
    """Completed higher-timeframe bars built from the decision rows. `avail[j]` is the first row whose close is at or
    after bucket j's close (len(rows) when the bucket is still open in this frame: never used)."""

    high: Any
    low: Any
    close: Any
    end_ns: Any
    avail: Any


def _buckets(ts: pd.DatetimeIndex, close_ns: Arr, h: Arr, lo: Arr, c: Arr, tf: str) -> _Buckets:
    if tf == "1d":
        key = feature_day(ts)
        end = epoch_ns(feature_day_close(key))
        k = epoch_ns(key)                                   # naive day labels as a group key
    else:
        key = floor_tf(ts, TF_SECONDS[tf])
        k = epoch_ns(key)
        end = k + TF_SECONDS[tf] * 1_000_000_000
    starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]]) if len(k) else np.zeros(0, dtype=int)
    last = np.r_[starts[1:], len(k)] - 1
    if not len(starts):
        e = np.zeros(0)
        return _Buckets(high=e, low=e, close=e, end_ns=e.astype(np.int64), avail=e.astype(np.int64))
    end_ns = end[starts]
    return _Buckets(high=np.maximum.reduceat(h, starts), low=np.minimum.reduceat(lo, starts), close=c[last],
                    end_ns=end_ns, avail=np.searchsorted(close_ns, end_ns, side="left"))


def _at_rows(n: int, rows: Arr, vals: Arr) -> Arr:
    """Values placed at their availability rows (later values win on a shared row) and carried forward."""
    out = np.full(n, np.nan)
    ok = rows < n
    out[rows[ok]] = vals[ok]
    return _ffill(out)


def _channel(h: Arr, lo: Arr, c: Arr, w: int) -> tuple[Arr, Arr, Arr]:
    """(top, bottom, slope per bar) of the parallel channel fitted on each trailing w-bar window. Each window is
    computed on its own (bit-identical whatever bar the series starts at or ends)."""
    n = len(c)
    top, bot, slope = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    if n < w:
        return top, bot, slope
    x = np.arange(w, dtype=float)
    xc = x - x.mean()
    sxx = float((xc ** 2).sum())
    view = np.lib.stride_tricks.sliding_window_view
    chunk = 20_000
    for s in range(0, n - w + 1, chunk):
        e = min(s + chunk, n - w + 1)
        cw, hw, lw = (view(a[s:e + w - 1], w) for a in (c, h, lo))
        b = (cw * xc).sum(axis=1) / sxx
        a0 = cw.mean(axis=1) - b * x.mean()
        line = a0[:, None] + b[:, None] * x
        rows = np.arange(s, e) + w - 1
        last = a0 + b * (w - 1)
        top[rows] = last + (hw - line).max(axis=1)
        bot[rows] = last + (lw - line).min(axis=1)
        slope[rows] = b
    return top, bot, slope


def swing_pivots(h: Arr, lo: Arr, c: Arr, p: PAParams) -> tuple[Arr, Arr]:
    """(pivot high, pivot low) stamped at the confirming bar: smc_replica.pivot_points of `pivot_len` on the bars'
    highs and lows, or on closes only (a line chart) when `swing_basis` is "close"."""
    return pivot_points(c, c, p.pivot_len) if p.swing_basis == "close" else pivot_points(h, lo, p.pivot_len)


# --------------------------------------------------------------------------- candles
def _candles(o: Arr, h: Arr, lo: Arr, c: Arr, a: Arr, vr: Arr, p: PAParams) -> dict[str, Arr]:
    """Raw candlestick patterns (no level context), stamped on the closing bar of the pattern."""
    with np.errstate(invalid="ignore", divide="ignore"):
        body = np.abs(c - o)
        rng = h - lo
        up = h - np.maximum(o, c)
        dn = np.minimum(o, c) - lo
        bull, bear = c > o, c < o
        o1, h1, l1, c1, b1, r1 = (_sh(x, 1) for x in (o, h, lo, c, body, rng))
        o2, h2, l2, c2, b2 = (_sh(x, 2) for x in (o, h, lo, c, body))
        bull1, bear1, bull2, bear2 = _sh(bull, 1), _sh(bear, 1), _sh(bull, 2), _sh(bear, 2)
        move = (c - _sh(c, p.trend_bars)) / a
        trend = np.where(move > p.trend_atr, 1, np.where(move < -p.trend_atr, -1, 0))
        tr = {k: _sh(trend.astype(float), k) for k in (1, 2, 3)}             # prior trend before a k-bar pattern
        real = rng > 0
        doji = real & (body <= p.doji_body * rng)
        grave = doji & (dn <= p.tiny_shadow * rng) & (up >= p.long_shadow * rng)
        dragon = doji & (up <= p.tiny_shadow * rng) & (dn >= p.long_shadow * rng)
        ll = doji & (up >= p.ll_shadow * rng) & (dn >= p.ll_shadow * rng) & (rng >= p.ll_range_atr * a)
        out: dict[str, Arr] = {}
        out["doji"] = np.select([grave, dragon, ll, doji], [3, 4, 2, 1], 0).astype(np.int64)
        hammer_shape = real & ~doji & (dn >= p.shadow_mult * body) & (up <= p.tiny_shadow * rng)
        out["hammer"] = _sgn(hammer_shape & (tr[1] == -1), hammer_shape & (tr[1] == 1))      # hammer / hangman
        inv_shape = real & ~doji & (up >= p.shadow_mult * body) & (dn <= p.tiny_shadow * rng)
        out["invhammer"] = _sgn(inv_shape & (tr[1] == -1), inv_shape & (tr[1] == 1))       # inverted hammer / star
        out["engulf"] = _sgn(bear1 & bull & (o <= c1) & (c > o1) & (body > b1) & (tr[2] == -1),
                             bull1 & bear & (o >= c1) & (c < o1) & (body > b1) & (tr[2] == 1))
        mid1 = (o1 + c1) / 2
        out["pierce"] = _sgn(bear1 & bull & (o <= c1) & (c > mid1) & (c < o1) & (tr[2] == -1),       # piercing
                             bull1 & bear & (o >= c1) & (c < mid1) & (c > o1) & (tr[2] == 1))        # dark cloud
        long2 = b2 >= p.star_long_atr * _sh(a, 2)
        small1 = b1 <= p.star_small * b2
        mid2 = (o2 + c2) / 2
        top1, bot1 = np.maximum(o1, c1), np.minimum(o1, c1)
        out["star"] = _sgn(bear2 & long2 & small1 & (top1 <= c2) & bull & (c > mid2),           # morning star
                           bull2 & long2 & small1 & (bot1 >= c2) & bear & (c < mid2))           # evening star
        doji1 = _sh(doji, 1)
        out["abandoned"] = _sgn(bear2 & long2 & doji1 & (top1 < c2) & bull & (o > top1) & (c > mid2),
                                bull2 & long2 & doji1 & (bot1 > c2) & bear & (o < bot1) & (c < mid2))
        out["harami"] = _sgn(bear1 & bull & (o >= c1) & (c <= o1) & (body < b1),
                             bull1 & bear & (o <= c1) & (c >= o1) & (body < b1))
        maru = real & (body >= p.maru_body * rng) & (rng >= p.min_range_atr * a)
        out["marubozu"] = _sgn(maru & bull, maru & bear)
        spin = real & ~doji & (body <= p.spin_body * rng) & (up >= body) & (dn >= body)
        out["spin"] = np.where(spin, np.where(vr < p.vol_low, 1, np.where(vr > p.vol_high, 3, 2)), 0).astype(np.int64)
        near1 = np.abs(o - c1) <= p.near_open_atr * a
        near2 = _sh(near1, 1)
        solid = body >= 0.5 * rng
        solid1, solid2 = _sh(solid, 1), _sh(solid, 2)
        soldiers = bull & bull1 & bull2 & (c > c1) & (c1 > c2) & near1 & near2 & solid & solid1 & solid2
        crows = bear & bear1 & bear2 & (c < c1) & (c1 < c2) & near1 & near2 & solid & solid1 & solid2
        out["three"] = _sgn(soldiers, crows)
        out["pin"] = _sgn(real & (dn >= p.pin_wick * rng) & (body <= p.pin_body * rng) & (rng >= p.min_range_atr * a),
                          real & (up >= p.pin_wick * rng) & (body <= p.pin_body * rng) & (rng >= p.min_range_atr * a))
        inside = (h <= h1) & (lo >= l1) & ((h < h1) | (lo > l1))
        out["inside"] = inside.astype(np.int64)
        inside1 = _sh(inside, 1)
        out["fakey"] = _sgn(inside1 & (lo < l1) & (c > l1) & (h <= h1), inside1 & (h > h1) & (c < h1) & (lo >= l1))
        o3, c3 = _sh(o, 3), _sh(c, 3)
        up3 = bull1 & bull2 & _sh(bull, 3) & (c1 > c2) & (c2 > c3)
        dn3 = bear1 & bear2 & _sh(bear, 3) & (c1 < c2) & (c2 < c3)
        out["strike"] = _sgn(up3 & bear & (o >= c1) & (c < o3), dn3 & bull & (o <= c1) & (c > o3))
        o4, h4, l4, c4, b4 = (_sh(x, 4) for x in (o, h, lo, c, body))
        long4 = b4 >= p.star_long_atr * _sh(a, 4)
        small3 = (b1 <= p.star_small * b4) & (b2 <= p.star_small * b4) & (_sh(body, 3) <= p.star_small * b4)
        inside3 = ((np.fmax(np.fmax(h1, h2), _sh(h, 3)) <= h4) & (np.fmin(np.fmin(l1, l2), _sh(lo, 3)) >= l4))
        bull4, bear4 = _sh(bull, 4), _sh(bear, 4)
        long0 = body >= p.star_long_atr * a
        out["methods"] = _sgn(bull4 & long4 & small3 & inside3 & bull & (c > c4),
                              bear4 & long4 & small3 & inside3 & bear & (c < c4))
        out["tower"] = _sgn(bear4 & long4 & small3 & bull & long0, bull4 & long4 & small3 & bear & long0)
        out["_trend"] = trend.astype(float)
        out["_trend3"] = tr[3]
    return out


def _confirm(raw: Arr, c: Arr, o: Arr, h: Arr, lo: Arr, code: bool) -> Arr:
    """The pattern of the previous bar, stamped on this bar when this bar confirms it: a signed pattern when this bar
    closes in its direction beyond the pattern bar's close; a code (doji, spinning top, inside bar) when this bar
    closes beyond the pattern bar's high (+1) or low (-1)."""
    r1 = _sh(raw.astype(float), 1)
    c1, h1, l1 = _sh(c, 1), _sh(h, 1), _sh(lo, 1)
    with np.errstate(invalid="ignore"):
        if code:
            return np.where(r1 > 0, np.where(c > h1, 1, np.where(c < l1, -1, 0)), 0).astype(np.int64)
        ok = ((r1 > 0) & (c > o) & (c > c1)) | ((r1 < 0) & (c < o) & (c < c1))
    return np.where(ok, r1, 0).astype(np.int64)


# --------------------------------------------------------------------------- event areas
def _event_areas(h: Arr, lo: Arr, c: Arr, a: Arr, p: PAParams) -> list[tuple[int, float, float, int]]:
    """(row registered, zone low, zone high, base end row) per impulse. An impulse is a run of bars whose close moved
    >= event_move_atr x ATR over event_bars bars; it is complete (and its origin zone becomes a level) on the first
    bar after the run. The origin zone is the range of the event_base_bars bars ending where the first move began."""
    k = p.event_bars
    with np.errstate(invalid="ignore"):
        mv = np.abs(c - _sh(c, k)) >= p.event_move_atr * _sh(a, k)
    out = []
    i, n = 0, len(c)
    while i < n:
        if not mv[i]:
            i += 1
            continue
        first = i
        while i < n and mv[i]:
            i += 1
        if i >= n:                       # still running at the end of the frame: not complete yet
            break
        s = first - k
        b0 = s - p.event_base_bars + 1
        if b0 >= 0:
            zl, zh = float(lo[b0:s + 1].min()), float(h[b0:s + 1].max())
            if zh - zl <= p.event_max_width_atr * a[s]:
                out.append((i, zl, zh, s))
    return out


# --------------------------------------------------------------------------- the level book
def _levels(ts: pd.DatetimeIndex, o: Arr, h: Arr, lo: Arr, c: Arr, a: Arr, tf: str, p: PAParams) -> dict[str, Arr]:
    n = len(c)
    sec = TF_SECONDS[tf]
    close_ns = epoch_ns(ts) + sec * 1_000_000_000
    # (row available, low, high, weight, origin ns, group)
    adds: list[tuple[int, float, float, float, int, int]] = []
    # key levels only: timeframes weighing at least level_min_weight (on 15m bars the 1h/4h/1d swings; "trade only
    # significant levels from the longer timeframes"), plus event areas
    tfs = [t for t in [tf, *(x for x in HIGHER_TFS if TF_SECONDS[x] > sec)]
           if p.tf_weights.get(t, 1.0) >= p.level_min_weight]
    for g, t in enumerate(tfs):
        w = p.tf_weights.get(t, 1.0)
        if t == tf:
            ph, pl = swing_pivots(h, lo, c, p)
            rows, ends = np.arange(n), close_ns
        else:
            b = _buckets(ts, close_ns, h, lo, c, t)
            ph, pl = swing_pivots(b.high, b.low, b.close, p)
            rows, ends = b.avail, b.end_ns
        for piv in (ph, pl):
            for j in np.flatnonzero(~np.isnan(piv)):
                if rows[j] < n:
                    adds.append((int(rows[j]), float(piv[j]), float(piv[j]), w, int(ends[j - p.pivot_len]), g))
    ev_group = len(tfs)
    for row, ev_lo, ev_hi, base in _event_areas(h, lo, c, a, p):
        adds.append((row, ev_lo, ev_hi, p.event_weight, int(close_ns[base]), ev_group))
    adds.sort(key=lambda x: x[0])
    caps = {g: p.max_levels for g in range(len(tfs))} | {ev_group: p.max_events}

    keys = ("room_long", "room_short", "sup_strength", "res_strength", "sup_width", "res_width", "zone_strength",
            "near", "near_low", "near_high")
    out = {k: np.full(n, np.nan) for k in keys}
    for k in ("brk", "retest", "reject", "offline"):
        out[k] = np.zeros(n, dtype=np.int64)
    PL = np.zeros(0)
    PH = np.zeros(0)
    W = np.zeros(0)
    T = np.zeros(0, dtype=np.int64)
    G = np.zeros(0, dtype=np.int64)
    BD = np.zeros(0, dtype=np.int64)
    BR = np.zeros(0, dtype=np.int64)
    hl_ns = p.recency_halflife_h * 3600e9
    q = 0
    for i in range(n):
        if q < len(adds) and adds[q][0] == i:
            new = []
            while q < len(adds) and adds[q][0] == i:
                new.append(adds[q])
                q += 1
            PL = np.r_[PL, [x[1] for x in new]]
            PH = np.r_[PH, [x[2] for x in new]]
            W = np.r_[W, [x[3] for x in new]]
            T = np.r_[T, [x[4] for x in new]].astype(np.int64)
            G = np.r_[G, [x[5] for x in new]].astype(np.int64)
            BD = np.r_[BD, np.zeros(len(new), dtype=np.int64)]
            BR = np.r_[BR, np.zeros(len(new), dtype=np.int64)]
            keep = np.ones(len(PL), dtype=bool)
            for g, cap in caps.items():                       # newest `cap` levels per group (arrays are in age order)
                idx = np.flatnonzero(G == g)
                if len(idx) > cap:
                    keep[idx[:-cap]] = False
            PL, PH, W, T, G, BD, BR = (x[keep] for x in (PL, PH, W, T, G, BD, BR))
        if not len(PL) or not np.isfinite(a[i]) or a[i] <= 0:
            continue
        ai, ci, hi, li = a[i], c[i], h[i], lo[i]
        hw = p.level_half_atr * ai                     # every level is an area [low - hw, high + hw]
        AL, AH, AM = PL - hw, PH + hw, (PL + PH) / 2
        touched = (li <= AH) & (hi >= AL)
        T[touched] = close_ns[i]
        S = W * 0.5 ** ((close_ns[i] - T) / hl_ns)
        if i > 0:
            up = (c[i - 1] <= AH) & (ci > AH)            # a close beyond the whole area breaks it
            dn = (c[i - 1] >= AL) & (ci < AL)
            out["brk"][i] = int(up.any()) - int(dn.any())
            age = i - BR
            rt_up = (BD == 1) & (age >= 1) & (age <= p.retest_bars) & (li <= AH) & (ci > AH)
            rt_dn = (BD == -1) & (age >= 1) & (age <= p.retest_bars) & (hi >= AL) & (ci < AL)
            out["retest"][i] = int(rt_up.any()) - int(rt_dn.any())
            BD[rt_up | rt_dn] = 0                                  # one retest per break
            BD[up], BR[up] = 1, i
            BD[dn], BR[dn] = -1, i
        bt, bb = max(o[i], ci), min(o[i], ci)
        # rejection: the wick reaches into the area and the body stays outside it; off the line: the wick goes
        # through the level's middle and the body closes back outside the area
        out["reject"][i] = int(((li <= AH) & (bb > AH)).any()) - int(((hi >= AL) & (bt < AL)).any())
        out["offline"][i] = int(((li < AM) & (bb > AH)).any()) - int(((hi > AM) & (bt < AL)).any())
        gap = np.maximum(np.maximum(AL - hi, li - AH), 0.0)
        out["near"][i] = gap.min() / ai
        # zones: levels sorted by their low edge, merged while the next starts within zone_atr of the zone's top
        order = np.argsort(PL, kind="stable")
        zl, zh, zs = PL[order], np.maximum.accumulate(PH[order]), S[order]
        newz = np.r_[True, zl[1:] > zh[:-1] + p.zone_atr * ai]
        st = np.flatnonzero(newz)
        zlo, zhi, zst = zl[st], np.maximum.reduceat(zh, st), np.add.reduceat(zs, st)
        inside = (zlo <= ci) & (ci <= zhi)
        out["zone_strength"][i] = zst[inside].sum() if inside.any() else 0.0
        below = np.flatnonzero(zhi < ci)
        above = np.flatnonzero(zlo > ci)
        if len(below):
            k = below[-1]
            out["room_short"][i] = (ci - zhi[k]) / ai
            out["sup_strength"][i], out["sup_width"][i] = zst[k], (zhi[k] - zlo[k]) / ai
            out["near_low"][i] = zhi[k]
        if len(above):
            k = above[0]
            out["room_long"][i] = (zlo[k] - ci) / ai
            out["res_strength"][i], out["res_width"][i] = zst[k], (zhi[k] - zlo[k]) / ai
            out["near_high"][i] = zlo[k]
    return out


# --------------------------------------------------------------------------- chart patterns, trendlines, swings
def _patterns(h: Arr, lo: Arr, c: Arr, a: Arr, sig: Arr, p: PAParams) -> dict[str, Arr]:
    """One pass over the bars with the confirmed pivots; every state change happens on a bar's close."""
    n = len(c)
    L = p.pivot_len
    hs_, ls_ = (c, c) if p.swing_basis == "close" else (h, lo)       # the series swings are read from
    ph, pl = swing_pivots(h, lo, c, p)
    a_eq = atr_seeded(h, lo, c, p.eq_atr_len)
    f = {k: np.full(n, np.nan) for k in ("tri_apex", "tri_height", "hs_neck", "hs_target", "dbl_neck", "tl_dn",
                                         "tl_up", "fib_dir", "fib236", "fib382", "fib500", "fib618", "perf",
                                         "swing_hi", "swing_lo", "fib50")}
    e = {k: np.zeros(n, dtype=np.int64) for k in ("tri_sym", "tri_asc", "tri_desc", "tri_bo", "tri_fbo", "tri_rt",
                                                  "tri_fail", "hs", "hs_break", "hs_rt", "dbl", "tpl", "dbl_break",
                                                  "dbl_rt", "abcd_c", "abcd_d", "tl_touch", "tl_touch_rev",
                                                  "tl_break", "fib_conf")}
    highs: list[tuple[int, float]] = []        # (extreme bar, price), in confirmation order
    lows: list[tuple[int, float]] = []
    high_eq: list[bool] = []
    low_eq: list[bool] = []
    swings: list[tuple[int, int, float]] = []  # alternating (kind +1 high / -1 low, bar, price)
    tri: dict[str, Any] | None = None
    tri_bo: dict[str, Any] | None = None
    hs: dict[str, Any] | None = None
    dbl: dict[str, Any] | None = None
    abcd: dict[str, Any] | None = None
    tl: dict[int, dict[str, Any] | None] = {-1: None, 1: None}
    rt_used = {1: -1, -1: -1}                  # bar of the pivot whose second touch already fired

    def line(d: dict[str, Any], x: int) -> float:
        return float(d["p"] + d["s"] * (x - d["x"]))

    def respected(lo_line: Any, hi_line: Any, x0: int, x1: int) -> bool:
        xs = np.arange(x0, x1 + 1)
        return bool(np.all((c[xs] >= lo_line(xs)) & (c[xs] <= hi_line(xs))))

    def form_triangle(i: int, ai: float) -> dict[str, Any] | None:
        band = p.tri_band_atr * ai
        if len(highs) < 2 or len(lows) < 2:
            return None

        def flat(pts: list[tuple[int, float]]) -> list[tuple[int, float]]:
            run = [pts[-1]]
            for q in reversed(pts[:-1]):
                if abs(q[1] - pts[-1][1]) > band or len(run) == 3:
                    break
                run.append(q)
            return run if len(run) >= 2 else []

        (xa, ha), (xb, hb) = highs[-2], highs[-1]
        (xc, la), (xd, lb) = lows[-2], lows[-1]
        floor, ceil = flat(lows), flat(highs)
        cand: list[tuple[str, int, dict[str, float], Any]] = []      # (kind, start bar, sloped line, other side)
        if floor and hb < ha - band:            # descending: flat floor, falling highs
            x0 = min(floor[-1][0], xa)
            fl = min(q[1] for q in floor)
            cand.append(("desc", x0, {"x": xb, "p": hb, "s": (hb - ha) / (xb - xa)}, fl))
        if ceil and lb > la + band:             # ascending: flat ceiling, rising lows
            x0 = min(ceil[-1][0], xc)
            cl = max(q[1] for q in ceil)
            cand.append(("asc", x0, {"x": xd, "p": lb, "s": (lb - la) / (xd - xc)}, cl))
        if hb < ha - band and lb > la + band:   # symmetrical: falling highs, rising lows
            cand.append(("sym", min(xa, xc), {"x": xb, "p": hb, "s": (hb - ha) / (xb - xa)},
                         {"x": xd, "p": lb, "s": (lb - la) / (xd - xc)}))
        for kind, x0, ln, other in cand:
            if i - x0 > p.tri_window:
                continue
            if kind == "desc":
                lo_f, hi_f = (lambda xs, v=other: np.full(len(xs), v)), (lambda xs, d=ln: d["p"] + d["s"] * (xs - d["x"]))
                ok = line(ln, i) > other
            elif kind == "asc":
                lo_f, hi_f = (lambda xs, d=ln: d["p"] + d["s"] * (xs - d["x"])), (lambda xs, v=other: np.full(len(xs), v))
                ok = line(ln, i) < other
            else:
                hi_f, lo_f = (lambda xs, d=ln: d["p"] + d["s"] * (xs - d["x"])), \
                    (lambda xs, d=other: d["p"] + d["s"] * (xs - d["x"]))
                ok = line(ln, i) > line(other, i)
            # boundaries respected from the later of the first pivots on (no close outside before the break)
            if ok and respected(lo_f, hi_f, max(xa, xc), i):
                return {"kind": kind, "x0": x0, "ln": ln, "other": other}
        return None

    for i in range(n):
        ai = a[i]
        ok_a = bool(np.isfinite(ai) and ai > 0)
        new_hi = not np.isnan(ph[i])
        new_lo = not np.isnan(pl[i])
        # equal consecutive pivots within eq_atr x ATR(eq_atr_len): smc_replica.equal_levels' rule (on high/low
        # swings the flags are identical to it, tests/test_price_action.py)
        if new_hi:
            eq = bool(highs) and bool(abs(ph[i] - highs[-1][1]) < p.eq_atr * a_eq[i])
            highs.append((i - L, float(ph[i])))
            high_eq.append(eq)
        if new_lo:
            eq = bool(lows) and bool(abs(pl[i] - lows[-1][1]) < p.eq_atr * a_eq[i])
            lows.append((i - L, float(pl[i])))
            low_eq.append(eq)
        fresh = ([(1, highs[-1])] if new_hi else []) + ([(-1, lows[-1])] if new_lo else [])
        for kind, (x, v) in sorted(fresh, key=lambda z: z[1][0]):      # in the order of their extremes
            if swings and swings[-1][0] == kind:
                if (kind == 1 and v > swings[-1][2]) or (kind == -1 and v < swings[-1][2]):
                    swings[-1] = (kind, x, v)
            else:
                swings.append((kind, x, v))
            # ABCD: A, B, C alternating with C just confirmed
            if len(swings) >= 3 and ok_a:
                (_, _, A), (_, _, B), (kc, _, C) = swings[-3], swings[-2], swings[-1]
                d = -kc                                 # C is a low: bullish ABCD
                imp = d * (B - A)
                if imp >= p.abcd_min_atr * ai and d * (C - A) > 0:
                    r = d * (B - C) / imp
                    if p.abcd_lo <= r <= p.abcd_hi:
                        abcd = {"d": d, "B": B, "C": C, "at": i}
        if new_hi and len(highs) >= 2:
            (x1, h1v), (x2, h2v) = highs[-2], highs[-1]
            tl[-1] = None
            if h2v < h1v and x2 > x1:
                mid = float(ls_[x1:x2 + 1].min())
                tl[-1] = {"x": x2, "p": h2v, "s": (h2v - h1v) / (x2 - x1), "mid": mid, "valid": False}
        if new_lo and len(lows) >= 2:
            (x1, l1v), (x2, l2v) = lows[-2], lows[-1]
            tl[1] = None
            if l2v > l1v and x2 > x1:
                mid = float(hs_[x1:x2 + 1].max())
                tl[1] = {"x": x2, "p": l2v, "s": (l2v - l1v) / (x2 - x1), "mid": mid, "valid": False}
        if (new_hi or new_lo) and ok_a:
            # head and shoulders (+ inverse) on the last three pivots of a kind
            for kind, pts in ((-1, highs if new_hi else []), (1, lows if new_lo else [])):
                if len(pts) < 3:
                    continue
                (a1, P1), (a2, P2), (a3, P3) = pts[-3], pts[-2], pts[-1]
                s = -kind                                  # +1 for a top pattern (prices compared upward)
                if a3 - a1 > p.hs_window or not (s * P2 > s * P1 and s * P2 > s * P3):
                    continue
                if abs(P1 - P3) > p.shoulder_tol_atr * ai:
                    continue
                seg1, seg2 = (ls_, ls_) if kind == -1 else (hs_, hs_)
                pick = np.argmin if kind == -1 else np.argmax
                t1 = a1 + int(pick(seg1[a1:a2 + 1]))
                t2 = a2 + int(pick(seg2[a2:a3 + 1]))
                if t2 == t1:
                    continue
                v1, v2 = float(seg1[t1]), float(seg2[t2])
                neck = {"x": t2, "p": v2, "s": (v2 - v1) / (t2 - t1)}
                hs = {"d": kind, "neck": neck, "head": P2, "head_x": a2, "at": i, "brk": -1, "target": np.nan,
                      "rt": False}
            # double / triple tops and bottoms (equal consecutive pivots)
            for kind, pts, eqs in ((-1, highs, high_eq) if new_hi else (0, [], []),
                                   (1, lows, low_eq) if new_lo else (0, [], [])):
                if not kind or not eqs or not eqs[-1] or len(pts) < 2:
                    continue
                (x1, v1), (x2, v2) = pts[-2], pts[-1]
                dneck = float(ls_[x1:x2 + 1].min()) if kind == -1 else float(hs_[x1:x2 + 1].max())
                triple = len(eqs) >= 2 and eqs[-2]
                dbl = {"d": kind, "level": max(v1, v2) if kind == -1 else min(v1, v2), "neck": dneck, "at": i,
                       "triple": triple, "brk": False}
            tri = form_triangle(i, ai)
            # trend perfection over the last perf_pivots highs and lows
            hs_p = [v for _, v in highs[-p.perf_pivots:]]
            ls_p = [v for _, v in lows[-p.perf_pivots:]]
            steps = [np.sign(b - a_) for seq in (hs_p, ls_p) for a_, b in zip(seq[:-1], seq[1:])]
            f["perf"][i] = float(np.mean(steps)) if steps else np.nan
        if highs:
            f["swing_hi"][i] = highs[-1][1]
        if lows:
            f["swing_lo"][i] = lows[-1][1]
        if not ok_a:
            continue
        tol = p.level_half_atr * ai
        ci, hi, li = c[i], h[i], lo[i]
        # ---- triangle
        if tri is not None:
            kind, ln, other = tri["kind"], tri["ln"], tri["other"]
            lv = line(ln, i)
            if kind == "desc":
                up_b, lo_b = lv, other
                apex = (lv - other) / -ln["s"] if ln["s"] < 0 else np.nan
                height = line(ln, tri["x0"]) - other
            elif kind == "asc":
                up_b, lo_b = other, lv
                apex = (other - lv) / ln["s"] if ln["s"] > 0 else np.nan
                height = other - line(ln, tri["x0"])
            else:
                up_b, lo_b = lv, line(other, i)
                conv = other["s"] - ln["s"]
                apex = (up_b - lo_b) / conv if conv > 0 else np.nan
                height = line(ln, tri["x0"]) - line(other, tri["x0"])
            e[f"tri_{kind}"][i] = 1
            f["tri_apex"][i] = apex
            f["tri_height"][i] = height / ai
            d = 1 if ci > up_b else -1 if ci < lo_b else 0
            if d:
                if (kind == "desc" and d == 1) or (kind == "asc" and d == -1):
                    e["tri_fail"][i] = d                   # close through the sloping side of a flat triangle
                else:
                    e["tri_bo"][i] = d
                    tri_bo = {"d": d, "at": i, "fixed": (up_b if d == 1 else lo_b) if kind != "sym" else None,
                              "ln": (ln if d == 1 else other) if kind == "sym" else None, "failed": False,
                              "rt": False}
                tri = None
        if tri_bo is not None and tri_bo["at"] < i:
            age = i - tri_bo["at"]
            lvl = tri_bo["fixed"] if tri_bo["fixed"] is not None else line(tri_bo["ln"], i)
            d = tri_bo["d"]
            if not tri_bo["failed"] and age <= p.fbo_bars and d * (ci - lvl) < 0:
                e["tri_fbo"][i] = -d                       # failed break: implied move the other way
                tri_bo["failed"] = True
            elif not tri_bo["failed"] and not tri_bo["rt"] and age <= p.retest_bars:
                if (d == 1 and li <= lvl + tol and ci > lvl) or (d == -1 and hi >= lvl - tol and ci < lvl):
                    e["tri_rt"][i] = d
                    tri_bo["rt"] = True
            if age > p.retest_bars:
                tri_bo = None
        # ---- head and shoulders
        if hs is not None:
            d = hs["d"]
            nk = line(hs["neck"], i)
            expired = i - (hs["at"] if hs["brk"] < 0 else hs["brk"]) > p.pattern_max_bars
            beyond_head = (d == -1 and hi > hs["head"]) or (d == 1 and li < hs["head"])
            if expired or (hs["brk"] < 0 and beyond_head):
                hs = None
            else:
                e["hs"][i] = d
                f["hs_neck"][i] = (ci - nk) / ai
                if hs["brk"] < 0 and d * (ci - nk) > 0:
                    e["hs_break"][i] = d
                    hs["brk"] = i
                    hs["target"] = nk + d * abs(hs["head"] - line(hs["neck"], hs["head_x"]))
                elif hs["brk"] >= 0 and not hs["rt"] and i - hs["brk"] <= p.retest_bars:
                    if (d == -1 and hi >= nk - tol and ci < nk) or (d == 1 and li <= nk + tol and ci > nk):
                        e["hs_rt"][i] = d
                        hs["rt"] = True
                if hs["brk"] >= 0:
                    left = d * (hs["target"] - ci) / ai
                    f["hs_target"][i] = left
                    if left <= 0:
                        hs = None                          # target reached
        # ---- double / triple
        if dbl is not None:
            d = dbl["d"]
            gone = (d == -1 and ci > dbl["level"] + tol) or (d == 1 and ci < dbl["level"] - tol)
            if gone or i - dbl["at"] > p.pattern_max_bars:
                dbl = None
            else:
                e["dbl"][i] = d
                e["tpl"][i] = d if dbl["triple"] else 0
                f["dbl_neck"][i] = (ci - dbl["neck"]) / ai
                if not dbl["brk"] and d * (ci - dbl["neck"]) > 0:
                    e["dbl_break"][i] = d
                    dbl["brk"] = True
        # second touch of the last swing low (high) after a rally (drop) off it: the live 2nd-bottom retest
        for d, pts in ((1, lows), (-1, highs)):
            if not pts or rt_used[d] == pts[-1][0]:
                continue
            x, v = pts[-1]
            seg_ext = hs_[x:i + 1].max() - v if d == 1 else v - ls_[x:i + 1].min()
            if seg_ext >= p.dbl_bounce_atr * ai and (
                    (d == 1 and v - tol <= li <= v + tol and ci > v) or (d == -1 and v - tol <= hi <= v + tol and ci < v)):
                e["dbl_rt"][i] += d
                rt_used[d] = x
        # ---- ABCD
        if abcd is not None:
            d = abcd["d"]
            if d * (ci - abcd["B"]) > 0:
                e["abcd_d"][i] = d
                abcd = None
            elif d * (ci - abcd["C"]) < 0 or i - abcd["at"] > p.pattern_max_bars:
                abcd = None
            else:
                e["abcd_c"][i] = d
        # ---- trendlines
        for d in (-1, 1):
            t = tl[d]
            if t is None:
                continue
            if not t["valid"] and ((d == -1 and li < t["mid"]) or (d == 1 and hi > t["mid"])):
                t["valid"] = True                          # lower low (higher high) made after the line's second pivot
            if not t["valid"]:
                continue
            lv = line(t, i)
            f["tl_dn" if d == -1 else "tl_up"][i] = d * (ci - lv) / ai     # >= 0 on the line's own side
            if (d == -1 and ci > lv) or (d == 1 and ci < lv):
                e["tl_break"][i] = -d
                tl[d] = None
                continue
            if (d == -1 and hi >= lv - tol) or (d == 1 and li <= lv + tol):
                e["tl_touch"][i] += d
                if sig[i] == d:
                    e["tl_touch_rev"][i] += d
        # ---- Fibonacci on the last confirmed swing
        if len(swings) >= 2:
            (_, _, A), (kb, _, B) = swings[-2], swings[-1]
            d = 1 if kb == 1 else -1
            f["fib_dir"][i] = d
            for fr, key in zip(FIBS, ("fib236", "fib382", "fib500", "fib618")):
                lvl = B - d * fr * abs(B - A)
                f[key][i] = (ci - lvl) / ai
            f50 = B - d * 0.5 * abs(B - A)
            f["fib50"][i] = f50
            if len(swings) >= 3:
                prev = swings[-3][2]                       # the previous extreme of B's kind, broken by B
                if d * (B - prev) > 0 and abs(f50 - prev) <= p.fib_conf_atr * ai:
                    e["fib_conf"][i] = d
    e["tl_dn"] = f.pop("tl_dn")
    e["tl_up"] = f.pop("tl_up")
    return {**f, **e}


# --------------------------------------------------------------------------- the frame
def price_action_frame(df: pd.DataFrame, params: PAParams = DEFAULT_PARAMS, tf: str | None = None) -> pd.DataFrame:
    """All price-action columns (COLUMNS) for mid bars `df` (ts_utc, open, high, low, close; tick_count or volume
    optional), one row per bar. `tf`: the bars' timeframe (default: inferred from the bar spacing)."""
    p = params
    ts = utc_index(df["ts_utc"]) if len(df) else pd.DatetimeIndex([], tz="UTC")
    tf = tf or decision_tf(ts)
    n = len(df)
    o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    vcol = "tick_count" if "tick_count" in df.columns else "volume" if "volume" in df.columns else None
    v = df[vcol].to_numpy(dtype=float) if vcol else np.full(n, np.nan)
    a = wilder_atr(df, p.atr_len).to_numpy()
    cs = pd.Series(c)
    em = {k: ema(cs, k).to_numpy() for k in (10, 20, 50, 200)}
    out = Columns(df.index)
    with np.errstate(invalid="ignore", divide="ignore"):
        vavg = _sh(pd.Series(v).rolling(p.vol_avg_bars, min_periods=p.vol_avg_bars).mean().to_numpy(), 1)
        vr = v / vavg
        cd = _candles(o, h, lo, c, a, vr, p)
        sig = _sgn(np.any([cd[k] == 1 for k in ("pin", "engulf", "hammer", "invhammer", "pierce", "star", "abandoned",
                                                "fakey")], axis=0),
                   np.any([cd[k] == -1 for k in ("pin", "engulf", "hammer", "invhammer", "pierce", "star",
                                                 "abandoned", "fakey")], axis=0))
        lv = _levels(ts, o, h, lo, c, a, tf, p)
        pt = _patterns(h, lo, c, a, sig, p)
        # ---- levels
        out["pa_room_long_atr"], out["pa_room_short_atr"] = lv["room_long"], lv["room_short"]
        out["pa_sup_strength"], out["pa_res_strength"] = lv["sup_strength"], lv["res_strength"]
        out["pa_sup_width_atr"], out["pa_res_width_atr"] = lv["sup_width"], lv["res_width"]
        out["pa_zone_strength"] = lv["zone_strength"]
        out["pa_level_break"], out["pa_level_retest"] = lv["brk"], lv["retest"]
        out["pa_level_near_atr"] = lv["near"]
        # structure stop: distance to the swing whose break invalidates the trade (last swing low for a long)
        out["pa_stop_long_atr"] = (c - pt["swing_lo"]) / a
        out["pa_stop_short_atr"] = (pt["swing_hi"] - c) / a
        # ---- dynamic levels
        for k in (10, 20, 50, 200):
            out[f"pa_ema{k}_dist_atr"] = (c - em[k]) / a
        for k in (10, 20, 50, 200):
            out[f"pa_ema{k}_slope_atr"] = (em[k] - _sh(em[k], p.ema_slope_bars)) / a
        touch = {k: (lo <= em[k]) & (em[k] <= h) for k in (10, 20, 200)}
        out["pa_ema20_touch"] = touch[20].astype(np.int64)
        out["pa_ema200_touch"] = touch[200].astype(np.int64)
        out["pa_ema10_50_gap_atr"] = (em[10] - em[50]) / a
        g1020 = (em[10] - em[20]) / a
        out["pa_ema10_20_gap_atr"] = g1020
        out["pa_ema10_20_gap_chg_atr"] = np.abs(g1020) - np.abs(_sh(g1020, p.ema_slope_bars))
        sec = TF_SECONDS[tf]
        if sec < TF_SECONDS["1h"]:
            b = _buckets(ts, epoch_ns(ts) + sec * 1_000_000_000, h, lo, c, "1h")
            ma = pd.Series(b.close).rolling(p.h1_ma_len, min_periods=p.h1_ma_len).mean().to_numpy()
            h1ma = _at_rows(n, b.avail, ma)
        elif sec == TF_SECONDS["1h"]:
            h1ma = cs.rolling(p.h1_ma_len, min_periods=p.h1_ma_len).mean().to_numpy()
        else:
            h1ma = np.full(n, np.nan)
        out["pa_h1_ma10_dist_atr"] = (c - h1ma) / a
        up_tr = (em[10] > em[20]) & (em[20] > em[50]) & (em[20] > _sh(em[20], p.ema_slope_bars))
        dn_tr = (em[10] < em[20]) & (em[20] < em[50]) & (em[20] < _sh(em[20], p.ema_slope_bars))
        t1020 = touch[10] | touch[20]
        out["pa_ema_pullback"] = _sgn(up_tr & t1020 & (sig == 1), dn_tr & t1020 & (sig == -1))
        xu = (em[10] > em[20]) & (_sh(em[10], 1) <= _sh(em[20], 1))
        xd = (em[10] < em[20]) & (_sh(em[10], 1) >= _sh(em[20], 1))
        xu, xd = xu | _sh(xu, 1), xd | _sh(xd, 1)
        pin_eng_up = (cd["pin"] == 1) | (cd["engulf"] == 1)
        pin_eng_dn = (cd["pin"] == -1) | (cd["engulf"] == -1)
        out["pa_ema_cross_pa"] = _sgn(xu & pin_eng_up, xd & pin_eng_dn)
        k = p.daily_mom_days
        if sec < TF_SECONDS["1d"]:
            b = _buckets(ts, epoch_ns(ts) + sec * 1_000_000_000, h, lo, c, "1d")
            mom = np.sign(b.close - _sh(b.close, k))
            dmom = _at_rows(n, b.avail, mom)
        else:
            dmom = np.sign(c - _sh(c, k))
        out["pa_daily_mom"] = np.nan_to_num(dmom).astype(np.int64)
        # ---- channel
        top, bot, slope = _channel(h, lo, c, p.channel_len)
        rise = slope * (p.channel_len - 1) / a
        out["pa_chan_dir"] = np.where(rise > p.channel_trend_atr, 1, np.where(rise < -p.channel_trend_atr, -1, 0))
        out["pa_chan_pos"] = (c - bot) / (top - bot)
        out["pa_chan_mid_dist_atr"] = (c - (top + bot) / 2) / a
        out["pa_chan_width_atr"] = (top - bot) / a
        # ---- trendlines, Fibonacci
        out["pa_tl_dn_dist_atr"], out["pa_tl_up_dist_atr"] = pt["tl_dn"], pt["tl_up"]
        out["pa_tl_touch"], out["pa_tl_touch_rev"], out["pa_tl_break"] = (pt["tl_touch"], pt["tl_touch_rev"],
                                                                           pt["tl_break"])
        out["pa_fib_dir"] = np.nan_to_num(pt["fib_dir"]).astype(np.int64)
        for fr, key in zip(FIBS, ("fib236", "fib382", "fib500", "fib618")):
            out[f"pa_fib{round(fr * 1000)}_dist_atr"] = pt[key]
        out["pa_fib50_confluence"] = pt["fib_conf"]
        # ---- volume
        out["pa_vol_ratio"] = vr
        near_any = np.fmin(np.fmin(lv["room_long"], lv["room_short"]), np.where(lv["zone_strength"] > 0, 0.0, np.nan))
        out["pa_vol_veto"] = ((vr > p.vol_veto_mult) & (near_any <= p.veto_near_atr)).astype(np.int64)
        # ---- chart patterns
        for col, key in (("pa_tri_sym", "tri_sym"), ("pa_tri_asc", "tri_asc"), ("pa_tri_desc", "tri_desc"),
                         ("pa_tri_apex_bars", "tri_apex"), ("pa_tri_height_atr", "tri_height"),
                         ("pa_tri_breakout_close", "tri_bo"), ("pa_tri_fbo", "tri_fbo"), ("pa_tri_retest", "tri_rt"),
                         ("pa_tri_failed", "tri_fail"), ("pa_hs", "hs"), ("pa_hs_neck_dist_atr", "hs_neck"),
                         ("pa_hs_break", "hs_break"), ("pa_hs_retest", "hs_rt"), ("pa_hs_target_dist_atr", "hs_target"),
                         ("pa_dbl", "dbl"), ("pa_tpl", "tpl"), ("pa_dbl_neck_dist_atr", "dbl_neck"),
                         ("pa_dbl_break", "dbl_break"), ("pa_dbl_retest", "dbl_rt"), ("pa_abcd_c", "abcd_c"),
                         ("pa_abcd_d", "abcd_d"), ("pa_trend_perfection", "perf")):
            out[col] = pt[key]
        out["pa_trend_perfection"] = _ffill(pt["perf"])
        # ---- candles: at a key level = within cs_level_atr x ATR of the bar's range
        keys = [lv["near"] * a, *(np.abs(np.fmax(np.fmax(x - h, lo - x), 0.0)) for x in
                                  (pt["swing_hi"], pt["swing_lo"], em[20], em[50], em[200], pt["fib50"]))]
        dist = np.fmin.reduce(np.vstack(keys), axis=0)
        at_level = dist <= p.cs_level_atr * a
        for name in (*SIGNED_CANDLES, *CODE_CANDLES):
            raw = cd[name]
            out[f"pa_cs_{name}"] = raw
            out[f"pa_cs_{name}_conf"] = _confirm(raw, c, o, h, lo, name in CODE_CANDLES)
            out[f"pa_cs_{name}_lvl"] = np.where(at_level, raw, 0).astype(np.int64)
        lvl2 = _sh(at_level, 2)
        out["pa_cs_three_best"] = _sgn((cd["three"] == 1) & (cd["_trend3"] == -1) & lvl2,
                                       (cd["three"] == -1) & (cd["_trend3"] == 1) & lvl2)
        out["pa_cs_at_level"] = at_level.astype(np.int64)
        # reversal reliability rank: pin at level 5 > engulfing 4 > doji at level 3 > H&S 2 > double/triple 1
        long_tf = sec >= TF_SECONDS["1h"]
        doji_dir = np.where(cd["doji"] == 3, -1, np.where(cd["doji"] == 4, 1, -cd["_trend"]))
        dbl_ev = np.where(pt["dbl_break"] != 0, pt["dbl_break"], pt["dbl_rt"])
        ranks = [(at_level & (cd["pin"] != 0), 5, cd["pin"]), (cd["engulf"] != 0, 4, cd["engulf"]),
                 (at_level & (cd["doji"] > 0), 3, doji_dir), (long_tf & (pt["hs_break"] != 0), 2, pt["hs_break"]),
                 (long_tf & (dbl_ev != 0), 1, dbl_ev)]
        rank = np.select([m for m, _, _ in ranks], [r for _, r, _ in ranks], 0)
        rdir = np.select([m for m, _, _ in ranks], [np.nan_to_num(d) for _, _, d in ranks], 0)
        out["pa_reversal_rank"] = rank.astype(np.int64)
        out["pa_reversal_dir"] = np.sign(rdir).astype(np.int64)
        # wick rejection: the longer wick over the mean range of the previous wick_bars bars, signed (upper = -),
        # only when that wick protrudes beyond those bars
        w = p.wick_bars
        rng_prev = _sh(pd.Series(h - lo).rolling(w, min_periods=w).mean().to_numpy(), 1)
        hh_prev = _sh(pd.Series(h).rolling(w, min_periods=w).max().to_numpy(), 1)
        ll_prev = _sh(pd.Series(lo).rolling(w, min_periods=w).min().to_numpy(), 1)
        upw, dnw = h - np.maximum(o, c), np.minimum(o, c) - lo
        wr = np.where((upw > dnw) & (h > hh_prev), -upw / rng_prev,
                      np.where((dnw > upw) & (lo < ll_prev), dnw / rng_prev, 0.0))
        out["pa_wick_rejection"] = np.nan_to_num(wr)
        # closes off the line: book levels and event areas (from the level pass) plus EMA10/20 and the 50% level
        bt, bb = np.maximum(o, c), np.minimum(o, c)
        dyn_bull = np.zeros(n, dtype=bool)
        dyn_bear = np.zeros(n, dtype=bool)
        hw = p.level_half_atr * a
        for x in (em[10], em[20], pt["fib50"]):
            dyn_bull |= (lo < x) & (bb > x + hw)
            dyn_bear |= (h > x) & (bt < x - hw)
        off = _sgn((lv["offline"] == 1) | dyn_bull, (lv["offline"] == -1) | dyn_bear)
        out["pa_off_the_line"] = off
        out["pa_sr_reject"] = lv["reject"]
        # confluence: a rejected level is required; + daily momentum, + EMA10/20 or 50% at the bar, + off the line,
        # + reversal rank, each counted only in the rejection's direction
        d = lv["reject"]
        near_dyn = np.zeros(n, dtype=bool)
        for x in (em[10], em[20], pt["fib50"]):
            near_dyn |= np.fmax(np.fmax(x - h, lo - x), 0.0) <= p.level_half_atr * a
        score = (1 + (out["pa_daily_mom"].to_numpy() == d) + near_dyn + (off == d)
                 + np.where(np.sign(rdir) == d, rank, 0))
        out["pa_confluence"] = np.where(d != 0, d * score, 0).astype(np.int64)
    return out.frame()[list(COLUMNS)]


_MEMO: dict[str, Any] = {}


def _memo_frame(df: pd.DataFrame, params: PAParams, tf: str | None) -> pd.DataFrame:
    """price_action_frame, computed once for the three registered features of one frame (keyed by content)."""
    cols = [k for k in ("ts_utc", "open", "high", "low", "close", "tick_count", "volume") if k in df.columns]
    hsh = hashlib.sha1(repr((params.tag(), tf, len(df), cols)).encode())
    hsh.update(epoch_ns(utc_index(df["ts_utc"])).tobytes() if len(df) else b"")
    for k in cols[1:]:
        hsh.update(df[k].to_numpy(dtype=float).tobytes())
    key = hsh.hexdigest()
    if _MEMO.get("key") != key:
        _MEMO.clear()
        _MEMO["key"], _MEMO["frame"] = key, price_action_frame(df, params, tf)
    frame: pd.DataFrame = _MEMO["frame"]
    out = frame.copy()
    out.index = df.index
    return out


def enable(params: PAParams = DEFAULT_PARAMS) -> list[str]:
    """Register the three opt-in features (`price_action`, `price_action_patterns`, `price_action_candles`) with
    `params` and return their names. Idempotent for the same block; a different block in the same process is refused.
    Not imported by goldbot.features and listed in pipeline.OPT_IN_FEATURES, so the default feature set and its
    version hash are unchanged. A non-default block makes the version `1+<hash>`. Tagged NEEDS_STATE: built only on
    a frame declared to start the series (ctx[registry.FULL_HISTORY]); ctx["decision_tf"] overrides the inferred
    timeframe."""
    version = VERSION + (f"+{params.tag()}" if params.tag() else "")
    names = [NAME, NAME_PATTERNS, NAME_CANDLES]
    if NAME in FEATURES:
        if FEATURES[NAME].version != version:
            raise ValueError(f"{NAME} already enabled at version {FEATURES[NAME].version}, not {version}")
        return names
    lookback = max(200, params.channel_len, params.eq_atr_len) + 2 * params.pivot_len + 1
    for name, cols in ((NAME, LEVEL_COLUMNS), (NAME_PATTERNS, PATTERN_COLUMNS), (NAME_CANDLES, CANDLE_COLUMNS)):
        def fn(df: pd.DataFrame, ctx: FeatureCtx, cols: tuple[str, ...] = cols) -> pd.DataFrame:
            return _memo_frame(df, params, ctx.get("decision_tf"))[list(cols)]
        signed = {k: v for k, v in SIGNED.items() if any(_full(k, c) for c in cols)}
        feature(name, FAMILY, version=version, lookback=lookback, tags=(NEEDS_STATE,), signed=signed)(fn)
    return names


def _full(pattern: str, col: str) -> bool:
    return re.fullmatch(pattern, col) is not None
