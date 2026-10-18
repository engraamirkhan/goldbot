"""Clean-room replica of three chart indicators the owner trades from (structure / order-flow overlays on XAUUSD),
rebuilt from their observable outputs only (plot values per bar pulled from the charting site; the vendors' source is
protected and was never seen). Generic names throughout; no vendor text. Opt-in family `smc_replica` (BACKLOG: owner
request 2026-10-18, pre-registration docs/research/prereg-tv-replica-2026-10-18.md).

Every rule below was fitted by matching these functions bar by bar against the pulled plot values on the charting
site's own bars (15m/1h/4h/1D); the per-plot match table is in the pre-registration. All functions are point in time:
a value at bar t uses bars that closed at or before t only (bar t's close included), and every event is stamped on
the bar whose close makes it known (a pivot `length` bars after its extreme, a break on the closing bar).

Ownership. Once fitted, these indicators are goldbot's own: computed from our bars in research and live, with no
dependency on the charting site. Every tunable sits in the typed block `ReplicaParams`, whose defaults are the owner's
current chart settings. `goldbot.research.replica_parity` compares the replica with pulled chart values over a window;
it is an optional health check, never an input.

Building blocks (all arrays are numpy float/bool, one value per bar):

* `ema_seeded(x, n)` - the charting site's EMA: NaN for the first n-1 bars, seeded with the SMA of the first n values,
  then alpha = 2 / (n + 1). `rma_seeded` is Wilder's average with the same seed; `atr_seeded` uses it on the true
  range (first bar: high - low).
* `ma_ribbon(close, scheme)` - "sma_ema": SMA10, SMA20, EMA50, SMA200; "ema": EMA5/10/20/50/200 (seeded).
* `money_flow(high, low, close, volume, n)` - money flow index on typical price; the first bar contributes to neither
  side (fitted: the first value, at bar n-1, matches only then). Oversold event = crossunder of 20, overbought event =
  crossover of 80. Our CFD bars carry tick counts as volume, so on our data it is a tick-flow index.
* `leg_pivots(high, low, length)` - "leg" pivots: at bar t the bar t-length starts a new down-leg when its high exceeds
  every high of the `length` bars after it (t-length+1..t), a new up-leg when its low is below every low after it.
  A pivot high is recorded when the leg turns down, a pivot low when it turns up; the very first leg seen sets the
  state without a pivot. The value is stamped at bar t (known then), never at t-length.
* `pivot_points(high, low, length)` - classic pivots: bar t-length is a pivot high when its high is >= the `length`
  highs before it and > the `length` highs after it (pivot low mirrored), stamped at bar t.
* `structure_breaks(...)` - break-of-structure (BOS) and change-of-character (CHoCH) events on two scales: swing
  (leg pivots of `swing_len`, 50) and internal (leg pivots of `internal_len`, 5). A bullish break is a close crossing
  above the latest unbroken pivot high (close > level, previous close <= the previous bar's level); it is a CHoCH when
  that scale's trend was bearish, else a BOS, and sets the trend bullish; each pivot breaks at most once. Internal
  breaks also need (a) the internal level to differ from the swing level and (b) a candle filter that the fit pinned:
  bullish when upper wick > min(close, open - low), bearish when upper wick < min(close, open - low) (an asymmetric
  wick test; reproduced exactly as observed, not "fixed"). Levels start undefined, so nothing breaks before the first
  pivot of a scale.
* `equal_levels(...)` - equal highs (lows): a classic pivot (length 3) within `threshold` (0.1) x ATR(200) of the
  previous pivot high (low), stamped at the pivot's confirmation bar.
* `fair_value_gaps(...)` - three-bar gaps confirmed on bar t: bullish when low[t] > high[t-2] and close[t-1] >
  high[t-2] and the middle bar's body (close-open)/open in % exceeds the auto threshold = 2 x the running mean of
  |body %| over bars 1..t (cumulative from the first bar of the series); bearish mirrored. Because the threshold is a
  running mean from the series start, values depend on where the series starts (they do not change when later bars
  are appended).
* `range_retracement(high, low, length, fracs)` - levels hh - f x (hh - ll) over the last `length` bars (0.5 and
  0.618 of the 50-bar range: the "golden zone").
* `candle_class(...)` - one code per bar, first match wins: 4 engulfing (body strictly engulfs the previous opposite
  body), 1 high-volume bull / 2 high-volume bear (volume > 1.5 x seeded EMA20 of volume), 3 low volume (< 0.5 x),
  5 MA traverse (optional: the span from the previous close to this close contains both seeded EMA10 and EMA20), 0 none.
* `sr_channel_breaks(...)` - support/resistance channels from classic pivots (length 5) in the last `lookback` (300)
  bars: each pivot grows a channel by absorbing pivots (newest first) while it stays within `width_pct` (1%) of the
  300-bar high-low range; strength = 20 per pivot + bars whose high or low touched it; the strongest non-overlapping
  channels are kept (`max_channels`, 15; ties to the newest pivot); recomputed only on bars that confirm a pivot.
  Resistance break: a close reaching a channel top from below (previous close < top <= close). Support break, as
  fitted: a close reaching from above (previous close > level >= close) the top of the highest channel or the bottom
  of any channel except the lowest. The support rule is the observed behaviour (99.7-99.9% of events), not a design.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.features.columns import Columns
from goldbot.features.registry import FEATURES, FeatureCtx, feature

NAME = "smc_replica"
FAMILY = "smc_replica"
VERSION = "1"

Arr = np.ndarray


# --------------------------------------------------------------------------- averages
def _f(x: Arr | pd.Series) -> Arr:
    return np.asarray(x, dtype=float)


def sma(x: Arr | pd.Series, n: int) -> Arr:
    return pd.Series(_f(x)).rolling(n, min_periods=n).mean().to_numpy()


def _seeded(x: Arr, n: int, alpha: float) -> Arr:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    acc = float(np.mean(x[:n]))
    out[n - 1] = acc
    for i in range(n, len(x)):
        acc = alpha * x[i] + (1.0 - alpha) * acc
        out[i] = acc
    return out


def ema_seeded(x: Arr | pd.Series, n: int) -> Arr:
    """EMA seeded with the SMA of the first n values (NaN before). A NaN input after the seed propagates."""
    return _seeded(_f(x), n, 2.0 / (n + 1))


def rma_seeded(x: Arr | pd.Series, n: int) -> Arr:
    """Wilder's moving average (alpha 1/n), SMA-seeded."""
    return _seeded(_f(x), n, 1.0 / n)


def atr_seeded(high: Arr, low: Arr, close: Arr, n: int) -> Arr:
    h, lo, c = _f(high), _f(low), _f(close)
    pc = np.concatenate(([np.nan], c[:-1]))
    tr = np.fmax(np.fmax(h - lo, np.abs(h - pc)), np.abs(lo - pc))
    if len(tr):
        tr[0] = h[0] - lo[0]
    return rma_seeded(tr, n)


def ma_ribbon(close: Arr | pd.Series, scheme: str = "sma_ema") -> dict[str, Arr]:
    """'sma_ema': ma10/ma20 SMA, ma50 EMA, ma200 SMA. 'ema': ma5..ma200 all seeded EMAs."""
    c = _f(close)
    if scheme == "sma_ema":
        return {"ma10": sma(c, 10), "ma20": sma(c, 20), "ma50": ema_seeded(c, 50), "ma200": sma(c, 200)}
    if scheme == "ema":
        return {f"ma{n}": ema_seeded(c, n) for n in (5, 10, 20, 50, 200)}
    raise ValueError(f"unknown MA scheme {scheme!r}")


def _prev(x: Arr) -> Arr:
    return np.concatenate(([np.nan], x[:-1])) if len(x) else x


def cross_over(a: Arr, b: Arr) -> Arr:
    """a crosses above b on this bar: a > b and a[t-1] <= b[t-1] (False where any value is NaN)."""
    with np.errstate(invalid="ignore"):
        return (a > b) & (_prev(a) <= _prev(b))


def cross_under(a: Arr, b: Arr) -> Arr:
    with np.errstate(invalid="ignore"):
        return (a < b) & (_prev(a) >= _prev(b))


# --------------------------------------------------------------------------- money flow
def money_flow(high: Arr, low: Arr, close: Arr, volume: Arr, n: int = 12) -> Arr:
    """Money flow index of typical price x volume over n bars (NaN until n bars; 0..100). The first bar has no
    change, so it adds to neither side. Zero downward flow gives 100; zero flow both ways gives NaN."""
    tp = (_f(high) + _f(low) + _f(close)) / 3.0
    v = _f(volume)
    ch = np.concatenate(([0.0], np.diff(tp))) if len(tp) else tp
    up = np.where(ch > 0, tp * v, 0.0)
    dn = np.where(ch < 0, tp * v, 0.0)
    su = pd.Series(up).rolling(n, min_periods=n).sum().to_numpy()
    sd = pd.Series(dn).rolling(n, min_periods=n).sum().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100.0 - 100.0 / (1.0 + su / sd)


# --------------------------------------------------------------------------- pivots
def leg_pivots(high: Arr, low: Arr, length: int) -> tuple[Arr, Arr]:
    """(pivot high value, pivot low value) stamped at the confirming bar, NaN elsewhere (see module docstring)."""
    h, lo = _f(high), _f(low)
    n = len(h)
    top = np.full(n, np.nan)
    btm = np.full(n, np.nan)
    if n <= length:
        return top, btm
    hh = pd.Series(h).rolling(length, min_periods=length).max().to_numpy()
    ll = pd.Series(lo).rolling(length, min_periods=length).min().to_numpy()
    j = np.arange(length, n)
    new_dn = h[j - length] > hh[j]
    new_up = ~new_dn & (lo[j - length] < ll[j])
    state = np.where(new_dn, 0.0, np.where(new_up, 1.0, np.nan))
    leg = pd.Series(state).ffill().to_numpy()                    # leg state after bar j (NaN before the first leg)
    prev = np.concatenate(([np.nan], leg[:-1]))
    known = ~np.isnan(prev)                                      # the first leg only sets the state
    turn_dn = known & (leg == 0) & (prev != 0)
    turn_up = known & (leg == 1) & (prev != 1)
    top[j[turn_dn]] = h[j[turn_dn] - length]
    btm[j[turn_up]] = lo[j[turn_up] - length]
    return top, btm


def pivot_points(high: Arr, low: Arr, length: int) -> tuple[Arr, Arr]:
    """Classic pivots: bar t-length is a pivot high when high >= the `length` highs before and > the `length` after
    (pivot low mirrored); stamped at bar t, NaN elsewhere."""
    h, lo = _f(high), _f(low)
    n = len(h)
    ph = np.full(n, np.nan)
    pl = np.full(n, np.nan)
    w = 2 * length + 1
    if n < w:
        return ph, pl
    hw = np.lib.stride_tricks.sliding_window_view(h, w)          # row r covers bars r .. r+2L, centre r+L
    lw = np.lib.stride_tricks.sliding_window_view(lo, w)
    ch, cl = hw[:, length], lw[:, length]
    is_h = (hw[:, :length] <= ch[:, None]).all(1) & (hw[:, length + 1:] < ch[:, None]).all(1)
    is_l = (lw[:, :length] >= cl[:, None]).all(1) & (lw[:, length + 1:] > cl[:, None]).all(1)
    t = np.arange(len(hw)) + 2 * length                           # confirming bar
    ph[t[is_h]] = ch[is_h]
    pl[t[is_l]] = cl[is_l]
    return ph, pl


# --------------------------------------------------------------------------- structure
STRUCTURE_EVENTS = ("bos_bull_internal", "choch_bull_internal", "bos_bear_internal", "choch_bear_internal",
                    "bos_bull_swing", "choch_bull_swing", "bos_bear_swing", "choch_bear_swing")


def structure_breaks(open_: Arr, high: Arr, low: Arr, close: Arr, swing_len: int = 50, internal_len: int = 5,
                     candle_filter: bool = True) -> dict[str, Arr]:
    """BOS / CHoCH events on the swing and internal scales plus each scale's trend state after the bar
    (`trend_swing`, `trend_internal`: +1 bullish, -1 bearish, 0 before the first break)."""
    o, h, lo, c = _f(open_), _f(high), _f(low), _f(close)
    n = len(c)
    top, btm = leg_pivots(h, lo, swing_len)
    itop, ibtm = leg_pivots(h, lo, internal_len)
    # rounded to 1e-9: a tie that double arithmetic splits by ~1e-14 counts as a tie (fitted on a 1993 daily bar)
    uw = np.round(h - np.maximum(c, o), 9)
    ref = np.round(np.minimum(c, o - lo), 9)
    bull_ok = uw > ref if candle_filter else np.ones(n, dtype=bool)
    bear_ok = uw < ref if candle_filter else np.ones(n, dtype=bool)
    out = {k: np.zeros(n, dtype=bool) for k in STRUCTURE_EVENTS}
    trend_s = np.zeros(n)
    trend_i = np.zeros(n)
    ty = by = ity = iby = np.nan              # current levels (undefined until the first pivot)
    pty = pby = pity = piby = np.nan          # the previous bar's levels
    tc = bc = itc = ibc = False               # level still unbroken
    ts = ti = 0
    cp = np.nan
    for i in range(n):
        if not np.isnan(top[i]):
            ty, tc = top[i], True
        if not np.isnan(btm[i]):
            by, bc = btm[i], True
        if not np.isnan(itop[i]):
            ity, itc = itop[i], True
        if not np.isnan(ibtm[i]):
            iby, ibc = ibtm[i], True
        ci = c[i]
        if itc and ci > ity and cp <= pity and ty != ity and bull_ok[i]:
            out["choch_bull_internal" if ti < 0 else "bos_bull_internal"][i] = True
            itc, ti = False, 1
        if tc and ci > ty and cp <= pty:
            out["choch_bull_swing" if ts < 0 else "bos_bull_swing"][i] = True
            tc, ts = False, 1
        if ibc and ci < iby and cp >= piby and by != iby and bear_ok[i]:
            out["choch_bear_internal" if ti > 0 else "bos_bear_internal"][i] = True
            ibc, ti = False, -1
        if bc and ci < by and cp >= pby:
            out["choch_bear_swing" if ts > 0 else "bos_bear_swing"][i] = True
            bc, ts = False, -1
        trend_s[i], trend_i[i] = ts, ti
        pty, pby, pity, piby, cp = ty, by, ity, iby, ci
    out["trend_swing"] = trend_s
    out["trend_internal"] = trend_i
    return out


def equal_levels(high: Arr, low: Arr, close: Arr, length: int = 3, threshold: float = 0.1,
                 atr_len: int = 200) -> tuple[Arr, Arr]:
    """(equal high, equal low) events at the confirming bar of a classic pivot within threshold x ATR of the previous
    pivot of the same kind."""
    ph, pl = pivot_points(high, low, length)
    a = atr_seeded(high, low, close, atr_len)
    n = len(a)
    eqh = np.zeros(n, dtype=bool)
    eql = np.zeros(n, dtype=bool)
    for piv, out in ((ph, eqh), (pl, eql)):
        idx = np.flatnonzero(~np.isnan(piv))
        if len(idx) < 2:
            continue
        cur, prv = piv[idx[1:]], piv[idx[:-1]]
        with np.errstate(invalid="ignore"):
            out[idx[1:]] = np.abs(cur - prv) < threshold * a[idx[1:]]
    return eqh, eql


def fair_value_gaps(open_: Arr, high: Arr, low: Arr, close: Arr, auto_threshold: bool = True) -> tuple[Arr, Arr]:
    o, h, lo, c = _f(open_), _f(high), _f(low), _f(close)
    n = len(c)
    c1, o1 = _prev(c), _prev(o)
    h2 = np.concatenate(([np.nan, np.nan], h[:-2])) if n >= 2 else np.full(n, np.nan)
    l2 = np.concatenate(([np.nan, np.nan], lo[:-2])) if n >= 2 else np.full(n, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        body = (c1 - o1) / o1 * 100.0
        if auto_threshold:
            thr = np.nancumsum(np.abs(body)) / np.arange(n) * 2.0      # bar 0: 0/0 -> NaN, never a gap
        else:
            thr = np.zeros(n)
        bull = (lo > h2) & (c1 > h2) & (body > thr)
        bear = (h < l2) & (c1 < l2) & (-body > thr)
    return bull, bear


def range_retracement(high: Arr, low: Arr, length: int = 50, fracs: tuple[float, ...] = (0.5, 0.618)) -> list[Arr]:
    hh = pd.Series(_f(high)).rolling(length, min_periods=length).max().to_numpy()
    ll = pd.Series(_f(low)).rolling(length, min_periods=length).min().to_numpy()
    return [hh - f * (hh - ll) for f in fracs]


# --------------------------------------------------------------------------- candles
CANDLE_NONE, CANDLE_HIGH_BULL, CANDLE_HIGH_BEAR, CANDLE_LOW_VOL, CANDLE_ENGULF, CANDLE_TRAVERSE = range(6)


def candle_class(open_: Arr, close: Arr, volume: Arr, vol_len: int = 20, high_mult: float = 1.5,
                 low_mult: float = 0.5, traverse: bool = False) -> Arr:
    o, c, v = _f(open_), _f(close), _f(volume)
    o1, c1 = _prev(o), _prev(c)
    ve = ema_seeded(v, vol_len)
    with np.errstate(invalid="ignore", divide="ignore"):
        engulf = ((c > o) & (c1 < o1) & (c > o1) & (o < c1)) | ((c < o) & (c1 > o1) & (c < o1) & (o > c1))
        hi = v > high_mult * ve
        lo_v = v < low_mult * ve
        conds = [engulf, hi & (c > o), hi & (c < o), lo_v]
        codes = [CANDLE_ENGULF, CANDLE_HIGH_BULL, CANDLE_HIGH_BEAR, CANDLE_LOW_VOL]
        if traverse:
            e10, e20 = ema_seeded(c, 10), ema_seeded(c, 20)
            a, b = np.minimum(c1, c), np.maximum(c1, c)
            conds.append((a < e10) & (b > e10) & (a < e20) & (b > e20))
            codes.append(CANDLE_TRAVERSE)
    return np.select(conds, codes, CANDLE_NONE).astype(np.int64)


# --------------------------------------------------------------------------- support / resistance channels
def _channels_now(pv: Arr, h_win: Arr, l_win: Arr, cw: float, max_channels: int) -> tuple[Arr, Arr]:
    """(tops, bottoms) of the strongest non-overlapping channels, strongest first, from pivot values `pv` (newest first)."""
    m = len(pv)
    lo = pv.copy()
    hi = pv.copy()
    num = np.zeros(m)
    for y in range(m):                                  # every candidate x grows in the same pivot order
        cpp = pv[y]
        below = cpp <= hi
        w = np.where(below, hi - cpp, cpp - lo)
        inc = w <= cw
        lo = np.where(inc & below, np.minimum(lo, cpp), lo)
        hi = np.where(inc & ~below, np.maximum(hi, cpp), hi)
        num += 20.0 * inc
    touch = (((h_win[None, :] <= hi[:, None]) & (h_win[None, :] >= lo[:, None]))
             | ((l_win[None, :] <= hi[:, None]) & (l_win[None, :] >= lo[:, None]))).sum(1)
    strength = num + touch
    tops: list[float] = []
    bots: list[float] = []
    for _ in range(min(m, max_channels)):
        if not (strength >= 20.0).any():
            break
        k = int(np.argmax(strength))                    # first maximum = newest pivot among ties
        top_k, bot_k = hi[k], lo[k]
        tops.append(top_k)
        bots.append(bot_k)
        strength = np.where(((hi <= top_k) & (hi >= bot_k)) | ((lo <= top_k) & (lo >= bot_k)), -1.0, strength)
    return np.asarray(tops), np.asarray(bots)


def sr_channel_breaks(open_: Arr, high: Arr, low: Arr, close: Arr, pivot_len: int = 5, width_pct: float = 1.0,
                      max_channels: int = 15, lookback: int = 300) -> tuple[Arr, Arr]:
    """(resistance break, support break) events per bar (rules in the module docstring)."""
    h, lo, c = _f(high), _f(low), _f(close)
    n = len(c)
    up = np.zeros(n, dtype=bool)
    dn = np.zeros(n, dtype=bool)
    ph, pl = pivot_points(h, lo, pivot_len)
    hh = pd.Series(h).rolling(lookback, min_periods=lookback).max().to_numpy()
    ll = pd.Series(lo).rolling(lookback, min_periods=lookback).min().to_numpy()
    pv: list[float] = []                                # pivot values, newest first
    loc: list[int] = []
    tops = bots = np.zeros(0)
    for i in range(n):
        has_h, has_l = not np.isnan(ph[i]), not np.isnan(pl[i])
        if has_h or has_l:
            pv.insert(0, float(ph[i] if has_h else pl[i]))
            loc.insert(0, i)
            while loc and i - loc[-1] > lookback:
                pv.pop()
                loc.pop()
            cw = (hh[i] - ll[i]) * width_pct / 100.0
            a0 = max(0, i - lookback)
            tops, bots = _channels_now(np.asarray(pv), h[a0:i + 1], lo[a0:i + 1], cw, max_channels)
        if i == 0 or not len(tops):
            continue
        cp, ci = c[i - 1], c[i]
        up[i] = bool(((cp < tops) & (ci >= tops)).any())
        order = np.argsort(-tops, kind="stable")        # highest channel first
        t_hi = tops[order[0]]
        b_keep = bots[order[:-1]]                        # every bottom except the lowest channel's
        dn[i] = bool((cp > t_hi >= ci) or ((cp > b_keep) & (ci <= b_keep)).any())
    return up, dn


# --------------------------------------------------------------------------- the opt-in feature
class ReplicaParams(FrozenRecord):
    """Every tunable of the replica in one typed block. The defaults are the owner's current chart settings, so
    `ReplicaParams()` reproduces the chart. Changing a value produces a different indicator, which the system owns
    and may evolve. `enable(params)` stamps a non-default block into the feature version, so a model trained on one
    block is never scored on another."""

    swing_len: int = Field(50, ge=2)                   # leg-pivot length, swing structure
    internal_len: int = Field(5, ge=2)                 # leg-pivot length, internal structure
    internal_candle_filter: bool = True                # the asymmetric wick test on internal breaks
    equal_len: int = Field(3, ge=1)                    # classic pivot length for equal highs/lows
    equal_threshold: float = Field(0.1, ge=0.0)        # x ATR(equal_atr_len)
    equal_atr_len: int = Field(200, ge=1)
    fvg_auto_threshold: bool = True
    mfi_len: int = Field(12, ge=2)
    mfi_oversold: float = 20.0
    mfi_overbought: float = 80.0
    range_len: int = Field(50, ge=2)                   # retracement range window
    range_fracs: tuple[float, float] = (0.5, 0.618)
    sr_pivot_len: int = Field(5, ge=1)
    sr_width_pct: float = Field(1.0, gt=0.0)           # channel width, % of the lookback range
    sr_max_channels: int = Field(15, ge=1)
    sr_lookback: int = Field(300, ge=10)
    candle_vol_len: int = Field(20, ge=1)
    candle_high_mult: float = 1.5
    candle_low_mult: float = 0.5
    candle_traverse: bool = False                      # the MA-traverse candle code (off in the feature frame)
    atr_len: int = Field(14, ge=1)                     # ATR used to scale the distance columns

    def tag(self) -> str:
        """'' for the defaults, else a short hash of the non-default fields (used in the feature version)."""
        diff = self.model_dump(exclude_defaults=True)
        if not diff:
            return ""
        return hashlib.sha1(json.dumps(diff, sort_keys=True, default=str).encode()).hexdigest()[:8]


DEFAULT_PARAMS = ReplicaParams()

COLUMNS = (*STRUCTURE_EVENTS, "trend_swing", "trend_internal", "eqh", "eql", "fvg_bull", "fvg_bear", "mfi",
           "mfi_os_cross", "mfi_ob_cross", "range_f1_dist_atr", "range_f2_dist_atr", "sr_break_up", "sr_break_dn",
           "candle_class", "ma_stack_sma_ema")
SIGNED = {"trend_swing": 0.0, "trend_internal": 0.0, "range_f1_dist_atr": 0.0, "range_f2_dist_atr": 0.0,
          "ma_stack_sma_ema": 0.0}


def replica_frame(df: pd.DataFrame, params: ReplicaParams = DEFAULT_PARAMS) -> pd.DataFrame:
    """All replica columns on mid bars (open/high/low/close plus `tick_count`, or `volume`, as the volume proxy).
    Events are 0/1, trends -1/0/+1. The distances are (close - level) / ATR (seeded Wilder, `atr_len`) to the two
    range levels. `mfi` is 0..100, `candle_class` uses the codes above, and `ma_stack_sma_ema` is +1 when
    SMA10 > SMA20 > EMA50 > SMA200, -1 when the reverse holds, else 0."""
    p = params
    o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    vol = df["tick_count"] if "tick_count" in df.columns else df["volume"]
    v = vol.to_numpy(dtype=float)
    out = Columns(df.index)
    st = structure_breaks(o, h, lo, c, p.swing_len, p.internal_len, p.internal_candle_filter)
    for k in STRUCTURE_EVENTS:
        out[k] = st[k].astype(np.int64)
    out["trend_swing"] = st["trend_swing"]
    out["trend_internal"] = st["trend_internal"]
    eqh, eql = equal_levels(h, lo, c, p.equal_len, p.equal_threshold, p.equal_atr_len)
    out["eqh"], out["eql"] = eqh.astype(np.int64), eql.astype(np.int64)
    fb, fs = fair_value_gaps(o, h, lo, c, p.fvg_auto_threshold)
    out["fvg_bull"], out["fvg_bear"] = fb.astype(np.int64), fs.astype(np.int64)
    m = money_flow(h, lo, c, v, p.mfi_len)
    out["mfi"] = m
    out["mfi_os_cross"] = cross_under(m, np.full(len(m), p.mfi_oversold)).astype(np.int64)
    out["mfi_ob_cross"] = cross_over(m, np.full(len(m), p.mfi_overbought)).astype(np.int64)
    a = atr_seeded(h, lo, c, p.atr_len)
    f1, f2 = range_retracement(h, lo, p.range_len, p.range_fracs)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["range_f1_dist_atr"] = (c - f1) / a
        out["range_f2_dist_atr"] = (c - f2) / a
    up, dn = sr_channel_breaks(o, h, lo, c, p.sr_pivot_len, p.sr_width_pct, p.sr_max_channels, p.sr_lookback)
    out["sr_break_up"], out["sr_break_dn"] = up.astype(np.int64), dn.astype(np.int64)
    out["candle_class"] = candle_class(o, c, v, p.candle_vol_len, p.candle_high_mult, p.candle_low_mult,
                                       p.candle_traverse)
    r = ma_ribbon(c, "sma_ema")
    with np.errstate(invalid="ignore"):
        bull = (r["ma10"] > r["ma20"]) & (r["ma20"] > r["ma50"]) & (r["ma50"] > r["ma200"])
        bear = (r["ma10"] < r["ma20"]) & (r["ma20"] < r["ma50"]) & (r["ma50"] < r["ma200"])
    out["ma_stack_sma_ema"] = np.where(bull, 1, np.where(bear, -1, 0)).astype(np.int64)
    return out.frame()


def enable(params: ReplicaParams = DEFAULT_PARAMS) -> str:
    """Register the opt-in `smc_replica` feature with `params` and return its name. It is idempotent for the same
    block and refuses a different block in the same process. It is not imported by goldbot.features and is listed
    in pipeline.OPT_IN_FEATURES, so the default feature set and its version hash are unchanged. A non-default block
    makes the version `1+<hash>`."""
    version = VERSION + (f"+{params.tag()}" if params.tag() else "")
    if NAME in FEATURES:
        if FEATURES[NAME].version != version:
            raise ValueError(f"{NAME} already enabled at version {FEATURES[NAME].version}, not {version}")
        return NAME

    def f_smc_replica(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
        return replica_frame(df, params)

    feature(NAME, FAMILY, version=version, lookback=max(600, 2 * params.sr_lookback), signed=SIGNED)(f_smc_replica)
    return NAME
