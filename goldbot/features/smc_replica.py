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

Point in time in the engine (tail invariance). Several pieces carry unbounded memory: the running FVG threshold, the
seeded averages, the structure state machine (levels, unbroken flags, trend) and the channel book. A tail of N bars
alone therefore cannot reproduce the full-history value. These pieces are made resumable instead. `replica_run(df,
params, state)` returns the frame and a `ReplicaState`, which is a JSON-serialisable snapshot after the last bar. Given
that snapshot, `replica_frame(tail, params, state)` computes the bars after the snapshot bar and gives exactly the
values of a full-history run, for every column (`tests/test_smc_replica.py`). The tail must hold at least
`context_bars(params)` bars up to and including the snapshot bar, for the windowed pieces. That is 311 with the
defaults: 300-bar channel lookback, plus 2 x 5 pivot bars, plus 1. The engine carries the state from bar to bar. It
bootstraps the state once from the research history and persists it with its other state. A fresh call without a
state is the research path on full history.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
from pydantic import ConfigDict, Field

from goldbot.base import FrozenRecord
from goldbot.features.columns import Columns
from goldbot.features.registry import FEATURES, FeatureCtx, feature

NAME = "smc_replica"
FAMILY = "smc_replica"
VERSION = "1"

Arr = np.ndarray
Seed = tuple[float, int]        # (running sum before the seed / EMA value after it, values consumed)
NO_SEED: Seed = (0.0, 0)


# --------------------------------------------------------------------------- averages
def _f(x: Arr | pd.Series) -> Arr:
    return np.asarray(x, dtype=float)


def window_sum(x: Arr, n: int) -> Arr:
    """Sum of each trailing n-bar window, NaN for the first n-1 bars. Every window is summed on its own, so a value
    is bit-identical whatever bar the series starts at (an online rolling sum is not)."""
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.lib.stride_tricks.sliding_window_view(x, n).sum(axis=1)
    return out


def sma(x: Arr | pd.Series, n: int) -> Arr:
    return window_sum(_f(x), n) / n


def _seeded_run(x: Arr, n: int, alpha: float, init: Seed = NO_SEED, start: int = 0) -> tuple[Arr, Seed]:
    """SMA-seeded recursive average over x[start:], resumed from `init`; NaN before the seed and before `start`."""
    out = np.full(len(x), np.nan)
    acc, cnt = init
    for i in range(start, len(x)):
        cnt += 1
        if cnt < n:
            acc += x[i]
        elif cnt == n:
            acc = (acc + x[i]) / n
            out[i] = acc
        else:
            acc = alpha * x[i] + (1.0 - alpha) * acc
            out[i] = acc
    return out, (float(acc), cnt)


def ema_seeded(x: Arr | pd.Series, n: int) -> Arr:
    """EMA seeded with the SMA of the first n values (NaN before). A NaN input propagates."""
    return _seeded_run(_f(x), n, 2.0 / (n + 1))[0]


def rma_seeded(x: Arr | pd.Series, n: int) -> Arr:
    """Wilder's moving average (alpha 1/n), SMA-seeded."""
    return _seeded_run(_f(x), n, 1.0 / n)[0]


def _true_range(high: Arr, low: Arr, close: Arr) -> Arr:
    h, lo, c = _f(high), _f(low), _f(close)
    pc = np.concatenate(([np.nan], c[:-1]))
    tr = np.fmax(np.fmax(h - lo, np.abs(h - pc)), np.abs(lo - pc))
    if len(tr):
        tr[0] = h[0] - lo[0]            # the series' first bar (a resumed run never consumes row 0)
    return tr


def atr_seeded(high: Arr, low: Arr, close: Arr, n: int) -> Arr:
    return rma_seeded(_true_range(high, low, close), n)


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
    change, so it adds to neither side. Zero downward flow gives 100; zero flow both ways gives NaN. A window
    statistic: exact on any tail from its n-th bar on (a resumed tail's first bar is context, never output)."""
    tp = (_f(high) + _f(low) + _f(close)) / 3.0
    v = _f(volume)
    ch = np.concatenate(([0.0], np.diff(tp))) if len(tp) else tp
    up = np.where(ch > 0, tp * v, 0.0)
    dn = np.where(ch < 0, tp * v, 0.0)
    su = window_sum(up, n)
    sd = window_sum(dn, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100.0 - 100.0 / (1.0 + su / sd)


# --------------------------------------------------------------------------- pivots
def _leg_run(high: Arr, low: Arr, length: int, init: float = np.nan, start: int = 0) -> tuple[Arr, Arr, float]:
    """Leg pivots from row `start` on, resumed from the leg state `init` (NaN = no leg seen yet). Returns (pivot high,
    pivot low, leg state after the last row)."""
    h, lo = _f(high), _f(low)
    n = len(h)
    top = np.full(n, np.nan)
    btm = np.full(n, np.nan)
    j0 = max(length, start)
    if n <= j0:
        return top, btm, init
    hh = pd.Series(h).rolling(length, min_periods=length).max().to_numpy()
    ll = pd.Series(lo).rolling(length, min_periods=length).min().to_numpy()
    j = np.arange(j0, n)
    new_dn = h[j - length] > hh[j]
    new_up = ~new_dn & (lo[j - length] < ll[j])
    state = np.where(new_dn, 0.0, np.where(new_up, 1.0, np.nan))
    leg = pd.Series(np.concatenate(([init], state))).ffill().to_numpy()     # leg[0] = state before row j0
    prev, leg = leg[:-1], leg[1:]
    known = ~np.isnan(prev)                                      # the first leg only sets the state
    turn_dn = known & (leg == 0) & (prev != 0)
    turn_up = known & (leg == 1) & (prev != 1)
    top[j[turn_dn]] = h[j[turn_dn] - length]
    btm[j[turn_up]] = lo[j[turn_up] - length]
    return top, btm, float(leg[-1])


def leg_pivots(high: Arr, low: Arr, length: int) -> tuple[Arr, Arr]:
    """(pivot high value, pivot low value) stamped at the confirming bar, NaN elsewhere (see module docstring)."""
    top, btm, _ = _leg_run(high, low, length)
    return top, btm


def pivot_points(high: Arr, low: Arr, length: int) -> tuple[Arr, Arr]:
    """Classic pivots: bar t-length is a pivot high when high >= the `length` highs before and > the `length` after
    (pivot low mirrored); stamped at bar t, NaN elsewhere. A window statistic (2 x length + 1 bars)."""
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


# --------------------------------------------------------------------------- resumable state
class StructureState(FrozenRecord):
    """The BOS/CHoCH machine after a bar: leg states, current levels (NaN undefined), unbroken flags, trends."""

    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_inf_nan="constants")
    leg_swing: float = np.nan
    leg_internal: float = np.nan
    top: float = np.nan
    btm: float = np.nan
    itop: float = np.nan
    ibtm: float = np.nan
    top_live: bool = False
    btm_live: bool = False
    itop_live: bool = False
    ibtm_live: bool = False
    trend_swing: int = 0
    trend_internal: int = 0
    close: float = np.nan


class ChannelState(FrozenRecord):
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_inf_nan="constants")
    pivots: tuple[float, ...] = ()        # newest first
    pivot_bars: tuple[int, ...] = ()      # global bar index of each pivot's confirmation
    tops: tuple[float, ...] = ()          # current channels, strongest first
    bottoms: tuple[float, ...] = ()


class StaleReplicaState(ValueError):
    """A carried state that this code must not resume: written by another code VERSION or for another parameter
    block. The caller re-bootstraps from the series start (`goldbot.features.smc_live`)."""


class ReplicaState(FrozenRecord):
    """Snapshot after bar `bar_index` (global index from the series' first bar) at `bar_time`, for `params_tag`.
    `code_version` (the module's VERSION) and `params_hash` (`ReplicaParams.digest`, every field, defaults included)
    are required, so a state written before they existed, by other code or for other parameters never loads as current
    (quant review 2026-10-18: "refuse stale state on mismatch")."""

    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_inf_nan="constants")
    bar_time: str
    bar_index: int
    params_tag: str = ""
    code_version: str
    params_hash: str
    structure: StructureState = StructureState()
    eq_last_high: float = np.nan
    eq_last_low: float = np.nan
    seeds: dict[str, Seed] = Field(default_factory=dict)     # atr, atr_eq, vol, ema50, ema10, ema20
    fvg_cum: float = 0.0                                     # sum of |middle-bar body %| through bar_index
    channels: ChannelState = ChannelState()


# --------------------------------------------------------------------------- structure
STRUCTURE_EVENTS = ("bos_bull_internal", "choch_bull_internal", "bos_bear_internal", "choch_bear_internal",
                    "bos_bull_swing", "choch_bull_swing", "bos_bear_swing", "choch_bear_swing")


def _structure_run(o: Arr, h: Arr, lo: Arr, c: Arr, swing_len: int, internal_len: int, candle_filter: bool,
                   init: StructureState, start: int) -> tuple[dict[str, Arr], StructureState]:
    n = len(c)
    top, btm, leg_s = _leg_run(h, lo, swing_len, init.leg_swing, start)
    itop, ibtm, leg_i = _leg_run(h, lo, internal_len, init.leg_internal, start)
    # rounded to 1e-9: a tie that double arithmetic splits by ~1e-14 counts as a tie (fitted on a 1993 daily bar)
    uw = np.round(h - np.maximum(c, o), 9)
    ref = np.round(np.minimum(c, o - lo), 9)
    bull_ok = uw > ref if candle_filter else np.ones(n, dtype=bool)
    bear_ok = uw < ref if candle_filter else np.ones(n, dtype=bool)
    out = {k: np.zeros(n, dtype=bool) for k in STRUCTURE_EVENTS}
    trend_s = np.zeros(n)
    trend_i = np.zeros(n)
    s = init
    ty, by, ity, iby = s.top, s.btm, s.itop, s.ibtm                 # current levels (undefined until a pivot)
    pty, pby, pity, piby = ty, by, ity, iby                         # the previous bar's levels
    tc, bc, itc, ibc = s.top_live, s.btm_live, s.itop_live, s.ibtm_live
    ts, ti = s.trend_swing, s.trend_internal
    cp = s.close
    for i in range(start, n):
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
    end = StructureState(leg_swing=leg_s, leg_internal=leg_i, top=float(ty), btm=float(by), itop=float(ity),
                         ibtm=float(iby), top_live=bool(tc), btm_live=bool(bc), itop_live=bool(itc),
                         ibtm_live=bool(ibc), trend_swing=int(ts), trend_internal=int(ti), close=float(cp))
    return out, end


def structure_breaks(open_: Arr, high: Arr, low: Arr, close: Arr, swing_len: int = 50, internal_len: int = 5,
                     candle_filter: bool = True) -> dict[str, Arr]:
    """BOS / CHoCH events on the swing and internal scales plus each scale's trend state after the bar
    (`trend_swing`, `trend_internal`: +1 bullish, -1 bearish, 0 before the first break)."""
    out, _ = _structure_run(_f(open_), _f(high), _f(low), _f(close), swing_len, internal_len, candle_filter,
                            StructureState(), 0)
    return out


def _equal_run(high: Arr, low: Arr, close: Arr, length: int, threshold: float, atr_len: int, last_h: float,
               last_l: float, atr_seed: Seed, start: int) -> tuple[Arr, Arr, float, float, Seed]:
    ph, pl = pivot_points(high, low, length)
    a, seed = _seeded_run(_true_range(high, low, close), atr_len, 1.0 / atr_len, atr_seed, start)
    n = len(a)
    eqh = np.zeros(n, dtype=bool)
    eql = np.zeros(n, dtype=bool)
    lasts = []
    for piv, out, last in ((ph, eqh, last_h), (pl, eql, last_l)):
        idx = np.flatnonzero(~np.isnan(piv[start:])) + start
        if len(idx):
            prv = np.concatenate(([last], piv[idx[:-1]]))
            with np.errstate(invalid="ignore"):
                out[idx] = np.abs(piv[idx] - prv) < threshold * a[idx]
            last = float(piv[idx[-1]])
        lasts.append(last)
    return eqh, eql, lasts[0], lasts[1], seed


def equal_levels(high: Arr, low: Arr, close: Arr, length: int = 3, threshold: float = 0.1,
                 atr_len: int = 200) -> tuple[Arr, Arr]:
    """(equal high, equal low) events at the confirming bar of a classic pivot within threshold x ATR of the previous
    pivot of the same kind."""
    eqh, eql, _, _, _ = _equal_run(high, low, close, length, threshold, atr_len, np.nan, np.nan, NO_SEED, 0)
    return eqh, eql


def _fvg_run(o: Arr, h: Arr, lo: Arr, c: Arr, auto_threshold: bool, cum0: float, base: int,
             start: int) -> tuple[Arr, Arr, float]:
    """Gaps from row `start` on; row i is global bar base + i; `cum0` = sum of |body %| through global bar base +
    start - 1 (body % of bar g is that of its middle bar g-1)."""
    n = len(c)
    c1, o1 = _prev(c), _prev(o)
    h2 = np.concatenate(([np.nan, np.nan], h[:-2])) if n >= 2 else np.full(n, np.nan)
    l2 = np.concatenate(([np.nan, np.nan], lo[:-2])) if n >= 2 else np.full(n, np.nan)
    bull = np.zeros(n, dtype=bool)
    bear = np.zeros(n, dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        body = (c1 - o1) / o1 * 100.0
        if base + start == 0 and n:
            body[start] = np.nan                       # the series' first bar has no middle bar
        cum = cum0 + np.nancumsum(np.abs(body[start:]))
        g = np.arange(start, n) + base
        thr = cum / g * 2.0 if auto_threshold else np.zeros(n - start)   # global bar 0: 0/0 -> NaN, never a gap
        b = body[start:]
        bull[start:] = (lo[start:] > h2[start:]) & (c1[start:] > h2[start:]) & (b > thr)
        bear[start:] = (h[start:] < l2[start:]) & (c1[start:] < l2[start:]) & (-b > thr)
    return bull, bear, float(cum[-1]) if len(cum) else cum0


def fair_value_gaps(open_: Arr, high: Arr, low: Arr, close: Arr, auto_threshold: bool = True) -> tuple[Arr, Arr]:
    bull, bear, _ = _fvg_run(_f(open_), _f(high), _f(low), _f(close), auto_threshold, 0.0, 0, 0)
    return bull, bear


def range_retracement(high: Arr, low: Arr, length: int = 50, fracs: tuple[float, ...] = (0.5, 0.618)) -> list[Arr]:
    hh = pd.Series(_f(high)).rolling(length, min_periods=length).max().to_numpy()
    ll = pd.Series(_f(low)).rolling(length, min_periods=length).min().to_numpy()
    return [hh - f * (hh - ll) for f in fracs]


# --------------------------------------------------------------------------- candles
CANDLE_NONE, CANDLE_HIGH_BULL, CANDLE_HIGH_BEAR, CANDLE_LOW_VOL, CANDLE_ENGULF, CANDLE_TRAVERSE = range(6)


def _candle_run(o: Arr, c: Arr, v: Arr, vol_len: int, high_mult: float, low_mult: float, traverse: bool,
                seeds: dict[str, Seed], start: int) -> tuple[Arr, dict[str, Seed]]:
    o1, c1 = _prev(o), _prev(c)
    ve, s_vol = _seeded_run(v, vol_len, 2.0 / (vol_len + 1), seeds.get("vol", NO_SEED), start)
    out_seeds = {"vol": s_vol}
    with np.errstate(invalid="ignore", divide="ignore"):
        engulf = ((c > o) & (c1 < o1) & (c > o1) & (o < c1)) | ((c < o) & (c1 > o1) & (c < o1) & (o > c1))
        hi = v > high_mult * ve
        lo_v = v < low_mult * ve
        conds = [engulf, hi & (c > o), hi & (c < o), lo_v]
        codes = [CANDLE_ENGULF, CANDLE_HIGH_BULL, CANDLE_HIGH_BEAR, CANDLE_LOW_VOL]
        if traverse:
            e10, out_seeds["ema10"] = _seeded_run(c, 10, 2.0 / 11, seeds.get("ema10", NO_SEED), start)
            e20, out_seeds["ema20"] = _seeded_run(c, 20, 2.0 / 21, seeds.get("ema20", NO_SEED), start)
            a, b = np.minimum(c1, c), np.maximum(c1, c)
            conds.append((a < e10) & (b > e10) & (a < e20) & (b > e20))
            codes.append(CANDLE_TRAVERSE)
    return np.select(conds, codes, CANDLE_NONE).astype(np.int64), out_seeds


def candle_class(open_: Arr, close: Arr, volume: Arr, vol_len: int = 20, high_mult: float = 1.5,
                 low_mult: float = 0.5, traverse: bool = False) -> Arr:
    return _candle_run(_f(open_), _f(close), _f(volume), vol_len, high_mult, low_mult, traverse, {}, 0)[0]


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


def _channel_run(h: Arr, lo: Arr, c: Arr, pivot_len: int, width_pct: float, max_channels: int, lookback: int,
                 init: ChannelState, base: int, start: int, range_window: int | None = None,
                 trace: list[tuple[Arr, Arr]] | None = None) -> tuple[Arr, Arr, ChannelState]:
    n = len(c)
    up = np.zeros(n, dtype=bool)
    dn = np.zeros(n, dtype=bool)
    ph, pl = pivot_points(h, lo, pivot_len)
    rw = range_window or lookback                       # the width is a % of the last `rw` bars' range (own input)
    hh = pd.Series(h).rolling(rw, min_periods=rw).max().to_numpy()
    ll = pd.Series(lo).rolling(rw, min_periods=rw).min().to_numpy()
    pv = list(init.pivots)                              # pivot values, newest first
    loc = list(init.pivot_bars)                         # their global bar indices
    tops, bots = np.asarray(init.tops, dtype=float), np.asarray(init.bottoms, dtype=float)
    for i in range(start, n):
        g = base + i
        has_h, has_l = not np.isnan(ph[i]), not np.isnan(pl[i])
        if has_h or has_l:
            pv.insert(0, float(ph[i] if has_h else pl[i]))
            loc.insert(0, g)
            while loc and g - loc[-1] > lookback:
                pv.pop()
                loc.pop()
            cw = (hh[i] - ll[i]) * width_pct / 100.0
            a0 = max(0, i - lookback)
            tops, bots = _channels_now(np.asarray(pv), h[a0:i + 1], lo[a0:i + 1], cw, max_channels)
        if trace is not None:
            trace.append((tops, bots))                    # the boxes in force at bar i (research / render only)
        if g == 0 or i == 0 or not len(tops):
            continue
        cp, ci = c[i - 1], c[i]
        up[i] = bool(((cp < tops) & (ci >= tops)).any())
        order = np.argsort(-tops, kind="stable")        # highest channel first
        t_hi = tops[order[0]]
        b_keep = bots[order[:-1]]                        # every bottom except the lowest channel's
        dn[i] = bool((cp > t_hi >= ci) or ((cp > b_keep) & (ci <= b_keep)).any())
    end = ChannelState(pivots=tuple(pv), pivot_bars=tuple(loc), tops=tuple(float(x) for x in tops),
                       bottoms=tuple(float(x) for x in bots))
    return up, dn, end


def sr_channel_breaks(open_: Arr, high: Arr, low: Arr, close: Arr, pivot_len: int = 5, width_pct: float = 1.0,
                      max_channels: int = 15, lookback: int = 300, range_window: int | None = None,
                      trace: list[tuple[Arr, Arr]] | None = None) -> tuple[Arr, Arr]:
    """(resistance break, support break) events per bar (rules in the module docstring)."""
    up, dn, _ = _channel_run(_f(high), _f(low), _f(close), pivot_len, width_pct, max_channels, lookback,
                             ChannelState(), 0, 0, range_window, trace)
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
    sr_lookback: int = Field(300, ge=10)               # loopback: bars of pivots/touches used to build the boxes
    sr_range_window: int = Field(300, ge=10)           # width is a % of the high-low range of the last N bars
    candle_vol_len: int = Field(20, ge=1)
    candle_high_mult: float = 1.5
    candle_low_mult: float = 0.5
    candle_traverse: bool = False                      # the MA-traverse candle code (off in the feature frame)
    atr_len: int = Field(14, ge=1)                     # ATR used to scale the distance columns

    def digest(self) -> str:
        """A hash of every field, defaults included: a state is resumed only by the exact block that wrote it."""
        return hashlib.sha1(json.dumps(self.model_dump(), sort_keys=True, default=str).encode()).hexdigest()[:16]

    def tag(self) -> str:
        """'' for the defaults, else a short hash of the non-default fields (used in the feature version)."""
        diff = self.model_dump(exclude_defaults=True)
        if not diff:
            return ""
        return hashlib.sha1(json.dumps(diff, sort_keys=True, default=str).encode()).hexdigest()[:8]


DEFAULT_PARAMS = ReplicaParams()


def context_bars(params: ReplicaParams = DEFAULT_PARAMS) -> int:
    """Bars a resumed tail must hold up to and including the snapshot bar: the longest window of a windowed piece
    (channel lookback + pivot bars + the previous close; SMA200; range; money flow; pivots), plus one."""
    p = params
    return max(p.sr_lookback + 2 * p.sr_pivot_len + 1, p.sr_range_window, 200, p.range_len, p.mfi_len + 1, 2 * p.equal_len + 1,
               p.swing_len + 1, p.internal_len + 1) + 1


COLUMNS = (*STRUCTURE_EVENTS, "trend_swing", "trend_internal", "eqh", "eql", "fvg_bull", "fvg_bear", "mfi",
           "mfi_os_cross", "mfi_ob_cross", "range_f1_dist_atr", "range_f2_dist_atr", "sr_break_up", "sr_break_dn",
           "candle_class", "ma_stack_sma_ema")
SIGNED = {"trend_swing": 0.0, "trend_internal": 0.0, "range_f1_dist_atr": 0.0, "range_f2_dist_atr": 0.0,
          "ma_stack_sma_ema": 0.0}


def _times(df: pd.DataFrame) -> pd.DatetimeIndex:
    col = "ts_utc" if "ts_utc" in df.columns else "bar_time"
    return pd.DatetimeIndex(pd.to_datetime(df[col], utc=True))


def replica_run(df: pd.DataFrame, params: ReplicaParams = DEFAULT_PARAMS,
                state: ReplicaState | None = None) -> tuple[pd.DataFrame, ReplicaState]:
    """Replica columns and the state after the last bar.

    Without `state`, df is the whole series from its first bar, and the frame covers every row. With `state`, df must
    contain the snapshot bar (`state.bar_time`) and at least `context_bars(params) - 1` bars before it. The frame
    then covers only the rows after the snapshot bar. Its values equal a full-history run's exactly.

    Columns: events are 0/1 and trends -1/0/+1. The distances are (close - level) / ATR (seeded Wilder, `atr_len`).
    `mfi` is 0..100 and `candle_class` uses the codes above. `ma_stack_sma_ema` is +1 when SMA10 > SMA20 > EMA50 >
    SMA200, -1 when the reverse holds, else 0."""
    p = params
    ts = _times(df)
    if state is None:
        start, base, s = 0, 0, ReplicaState(bar_time="", bar_index=-1, params_tag=p.tag(), code_version=VERSION,
                                            params_hash=p.digest())
    else:
        if state.code_version != VERSION:
            raise StaleReplicaState(f"state was written by replica code {state.code_version!r}, not {VERSION!r}")
        if state.params_hash != p.digest():
            raise StaleReplicaState(f"state was built for params {state.params_hash!r}, not {p.digest()!r}")
        if state.params_tag != p.tag():
            raise ValueError(f"state was built for params {state.params_tag!r}, not {p.tag()!r}")
        hit = np.flatnonzero(ts == pd.Timestamp(state.bar_time))
        if not len(hit):
            raise ValueError(f"snapshot bar {state.bar_time} is not in the frame")
        snap = int(hit[0])
        if snap + 1 < context_bars(p):
            raise ValueError(f"need {context_bars(p)} bars up to the snapshot bar, got {snap + 1}")
        start, base, s = snap + 1, state.bar_index - snap, state
    o, h, lo, c = (df[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    vol = df["tick_count"] if "tick_count" in df.columns else df["volume"]
    v = vol.to_numpy(dtype=float)
    out = Columns(df.index)
    st, st_end = _structure_run(o, h, lo, c, p.swing_len, p.internal_len, p.internal_candle_filter, s.structure,
                                start)
    for k in STRUCTURE_EVENTS:
        out[k] = st[k].astype(np.int64)
    out["trend_swing"] = st["trend_swing"]
    out["trend_internal"] = st["trend_internal"]
    eqh, eql, last_h, last_l, s_atr_eq = _equal_run(h, lo, c, p.equal_len, p.equal_threshold, p.equal_atr_len,
                                                    s.eq_last_high, s.eq_last_low, s.seeds.get("atr_eq", NO_SEED),
                                                    start)
    out["eqh"], out["eql"] = eqh.astype(np.int64), eql.astype(np.int64)
    fb, fs, cum = _fvg_run(o, h, lo, c, p.fvg_auto_threshold, s.fvg_cum, base, start)
    out["fvg_bull"], out["fvg_bear"] = fb.astype(np.int64), fs.astype(np.int64)
    m = money_flow(h, lo, c, v, p.mfi_len)
    out["mfi"] = m
    out["mfi_os_cross"] = cross_under(m, np.full(len(m), p.mfi_oversold)).astype(np.int64)
    out["mfi_ob_cross"] = cross_over(m, np.full(len(m), p.mfi_overbought)).astype(np.int64)
    a, s_atr = _seeded_run(_true_range(h, lo, c), p.atr_len, 1.0 / p.atr_len, s.seeds.get("atr", NO_SEED), start)
    f1, f2 = range_retracement(h, lo, p.range_len, p.range_fracs)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["range_f1_dist_atr"] = (c - f1) / a
        out["range_f2_dist_atr"] = (c - f2) / a
    up, dn, ch_end = _channel_run(h, lo, c, p.sr_pivot_len, p.sr_width_pct, p.sr_max_channels, p.sr_lookback,
                                  s.channels, base, start, p.sr_range_window)
    out["sr_break_up"], out["sr_break_dn"] = up.astype(np.int64), dn.astype(np.int64)
    cc, s_candle = _candle_run(o, c, v, p.candle_vol_len, p.candle_high_mult, p.candle_low_mult, p.candle_traverse,
                               s.seeds, start)
    out["candle_class"] = cc
    e50, s_e50 = _seeded_run(c, 50, 2.0 / 51, s.seeds.get("ema50", NO_SEED), start)
    s10, s20, s200 = sma(c, 10), sma(c, 20), sma(c, 200)
    with np.errstate(invalid="ignore"):
        bull = (s10 > s20) & (s20 > e50) & (e50 > s200)
        bear = (s10 < s20) & (s20 < e50) & (e50 < s200)
    out["ma_stack_sma_ema"] = np.where(bull, 1, np.where(bear, -1, 0)).astype(np.int64)
    frame = out.frame().iloc[start:]
    n = len(df)
    end = ReplicaState(
        bar_time=ts[-1].isoformat() if n else s.bar_time, bar_index=base + n - 1 if n else s.bar_index,
        params_tag=p.tag(), code_version=VERSION, params_hash=p.digest(), structure=st_end, eq_last_high=last_h, eq_last_low=last_l,
        seeds={**s.seeds, "atr": s_atr, "atr_eq": s_atr_eq, "ema50": s_e50, **s_candle}, fvg_cum=cum,
        channels=ch_end)
    return frame, end


def replica_frame(df: pd.DataFrame, params: ReplicaParams = DEFAULT_PARAMS,
                  state: ReplicaState | None = None) -> pd.DataFrame:
    """The columns of `replica_run` (see there)."""
    return replica_run(df, params, state)[0]


def enable(params: ReplicaParams = DEFAULT_PARAMS) -> str:
    """Register the opt-in `smc_replica` feature with `params` and return its name. It is idempotent for the same
    block and refuses a different block in the same process. It is not imported by goldbot.features and is listed
    in pipeline.OPT_IN_FEATURES, so the default feature set and its version hash are unchanged. A non-default block
    makes the version `1+<hash>`.

    The registered function is the research path: it runs on the frame it is given, which research gives from the
    series' first bar. A live engine must use `replica_run` with a carried `ReplicaState` instead of a fixed tail.
    `lookback` is the warm-up of the windowed pieces only (`context_bars`); the recursive pieces need the state."""
    version = VERSION + (f"+{params.tag()}" if params.tag() else "")
    if NAME in FEATURES:
        if FEATURES[NAME].version != version:
            raise ValueError(f"{NAME} already enabled at version {FEATURES[NAME].version}, not {version}")
        return NAME

    def f_smc_replica(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
        return replica_frame(df, params)

    feature(NAME, FAMILY, version=version, lookback=context_bars(params), signed=SIGNED,
            tags=("needs_state",))(f_smc_replica)
    return NAME


def state_json(state: ReplicaState) -> str:
    return state.model_dump_json()


def state_from_json(text: str | bytes) -> ReplicaState:
    return ReplicaState.model_validate_json(text)
