"""Scalp-volume arm rules of H-81 (docs/research/prereg-2026-10-27-scalp-volume.md, frozen 2026-10-27, with amendments
(a) and (b)): the ten 1m/5m families Z1-Z10, their per-broker strata and the random controls ZCD/ZCV. Each rule is one
function used by the history matrix and live (arms-2026-10-25.md 8.0: "Each rule is one function used by the history
matrix and live, and its test proves prefix invariance"); a decision on the close of bar t reads bars <= t only.

Conventions (prereg section 0; the arm row carries what differs per stratum):

* Brackets: "Stop and target are fixed prices set at entry ... The time exit is at the close of the hold-th decision
  bar. There is no trailing and no break-even." "Stops are fixed dollars per broker, not ATR multiples": the row's
  `stop_usd` (ICM $3.00 on 1m and $4.50 on 5m, Vantage $7.00), `target_r` and `hold_bars`.
* Volume: "v = the bar's tick volume (Dukascopy `tick_count` in history; MT5 `tick_volume` live). It is only ever
  compared with its own trailing median on the same feed, never across feeds or brokers."
* Clock: amendment (b) 1, "τ is the 5m bar's close time. Dukascopy bars are labelled by their open, so history adds
  the bar length before any clock test. The live engine uses the same function": every clock test and every hash here
  reads `decision_close` (ts_utc + the bar length).
* Window W: "from 19:00 New York ... to 16:00 New York, Monday to Friday trading days (the NY 17:00 day of section
  8.0)", i.e. a decision close in [19:00, 16:00) New York on a Monday-Friday trading day (NY date after +7 h), with
  the common filters' clock part (no entry 20:45-22:15 UTC or Friday from 19:00 UTC; the 16:55-17:00 and 18:00-18:30
  New York reopen filters lie outside W) and no entry on a CME metals holiday or early close. These apply BEFORE the
  "at most 1 per N bars" spacing, as the 2010-2017 census did; the tier-1 blackout is applied after it
  (`arm_rules.post_filter`: the census counted "before the blackout and spread gate").
* Spread gate: "skip the candidate when the spread at decision is above 2 x the median spread of the account's own
  last 60 minutes. Each skip is journalled `spread_wide` with its propensity. History uses Dukascopy `spread_mean` the
  same way." The spread at decision is the `spread_mean` of the last 1m bar closed by the decision close; the median
  is over the 60 1m bars before that one (fewer than 60: not gated). A gated candidate keeps its spacing slot and is
  returned with `skip = "spread_wide"`, which `arm_rules.screened` routes to the skipped frame: the engine journals it
  (`exploration_skipped:spread_wide`, with its propensity) and the history run never enters it.
* Higher timeframes (Z9, Z10): "A higher-timeframe value comes from the last **closed** bar of that timeframe via
  `asof_join` on `available_utc`" (8.0), the bars resampled from the 1m bars the rule is handed.
* ATR = Wilder ATR(14) of the decision bars on mid prices, at t unless the rule reads ATR_{t-1} or ATR_{t-3}.

Controls (ZCD p 0.07, ZCV p 0.05): "at each 5m close in W with the common filters, enter when `hash_u(id, close) < p`;
side `hash_side(id, close)`". Read literally, the side draw is the entry draw (`hash_side` is `hash_u(id, close) <
0.5`), so every control entry (u < 0.07) would be LONG: a one-sided control is no null. The side is drawn on its own
key, `hash_side(id + ":side", close)` (G1C's precedent, `arm_rules_levels`: "a sign by hash_u(arm id + ':sign', D's
start)"); flagged to the quant reviewer for a dated amendment that records it.

Declared readings where the frozen text is silent:
* Z4: a bar that touches both handles and closes inside both rejection bands has no side and is no event.
* Z7: "the previous 10 trading days" are the 10 Monday-Friday trading days before the decision's own; a day is
  present when both 5m closes bounding its half-hour exist. The return is close(τ' + 30 min) - close(τ') on mid.
* Z10: a bar's "return" is its close minus the previous bar's close (the census's `diff`).

Live reads `LOOKBACK_BARS` decision bars (`arm_rules.lookback_bars`): enough for Wilder's ATR14 (400), Z7's ten
trading days of 5m closes (3,600 > 11 x 288) and Z9's 1h EMA50 (4,500 5m bars, about 375 1h bars: (49/51)^375 is
below 1e-6), each inside the engine's 1m memory (tested)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

import numpy as np
import pandas as pd

from goldbot.config import tf_seconds
from goldbot.data.resample import mid, resample_bars
from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features.technical import atr, ema
from goldbot.research.arm_rules import (
    NEW_YORK,
    RuleContext,
    _frame,
    common_filter_ok,
    empty,
    hash_side,
    hash_u,
)
from goldbot.research.arm_rules_bands import spaced
from goldbot.research.arm_rules_clock_box import cme_holiday
from goldbot.research.arms import SCALP_RULES, Arm, Timeframe

SPREAD_WIDE = "spread_wide"
SPREAD_X = 2.0                      # "above 2 x the median spread"
SPREAD_MINUTES = 60                 # "of the account's own last 60 minutes"
W_FROM, W_TO = 19 * 60, 16 * 60     # W on the New York clock, [19:00, 16:00)
# "at most 1 per N bars", per family (section 1)
GAP = {"tick_surge": 10, "run_fade": 10, "round_break": 3, "round_reject": 15, "extreme_break": 6, "nr4_break": 3,
       "absorb_fade": 10, "htf_reclaim": 6, "three_horizon": 6}
# the families' decision timeframes; the rule names are arms.SCALP_RULES (the loader's list, held equal by a test)
RULE_TF: dict[str, Timeframe] = {"tick_surge": "1m", "run_fade": "1m", "round_break": "5m", "round_reject": "1m", "extreme_break": "5m",
           "nr4_break": "5m", "halfhour_persist": "5m", "absorb_fade": "1m", "htf_reclaim": "5m",
           "three_horizon": "5m", "scalp_control": "5m"}
# Z1 / Z8 / Z5 volume surges
SURGE_X, SURGE_N = 2.5, 60          # v_t >= 2.5 x median(v, t-60..t-1)
Z1_BODY_ATR, Z1_OUTER = 0.8, 0.7    # |c - o| >= 0.8 ATR_{t-1}; close in the outer 30% of the range
Z2_RUN_ATR = 2.5                    # |c_t - c_{t-3}| >= 2.5 ATR_{t-3}
Z3_BEYOND_ATR = 0.15                # beyond the handle by 0.15 ATR_t
Z4_BACK_ATR = 0.25                  # closes 0.25 ATR_{t-1} back from the handle
Z5_LOOK, Z5_BEYOND_ATR, Z5_VOL_X, Z5_VOL_N = 12, 0.1, 1.5, 48
Z7_DAYS, Z7_MIN_PRESENT, Z7_SPAN = 10, 8, 30
Z8_BODY_ATR, Z8_PRIOR, Z8_PRIOR_ATR = 0.2, 5, 1.5   # P = c_{t-1} - c_{t-6}
Z9_BARS_BELOW = 3
Z10_ATR = 1.0
HANDLE_USD = 10.0                   # "handles = multiples of $10.00"
# decision bars live hands the rule for its candidates to equal the history run's (module docstring)
LOOKBACK_BARS = {**dict.fromkeys(SCALP_RULES, 400), "halfhour_persist": 3_600, "htf_reclaim": 4_500}


# ---------------------------------------------------------------- shared definitions
def decision_close(bars: pd.DataFrame, tf: str) -> pd.DatetimeIndex:
    """Amendment (b) 1: the decision bar's close, its open label + the bar length (history and live alike)."""
    return utc_index(bars["ts_utc"]) + pd.Timedelta(seconds=tf_seconds(tf))


def trading_days(close: pd.DatetimeIndex) -> np.ndarray:
    """8.0's trading day (New York date after +7 h, ending 17:00 New York) as days since 1970-01-01."""
    loc = (close.tz_convert(NEW_YORK) + pd.Timedelta(hours=7)).tz_localize(None)
    return np.asarray(loc.to_numpy().astype("datetime64[D]").astype(np.int64))


def in_w(close: pd.DatetimeIndex) -> np.ndarray:
    """Window W with the clock filters (module docstring): [19:00, 16:00) New York on a Monday-Friday trading day,
    outside 20:45-22:15 UTC and Friday from 19:00 UTC, not on a CME metals holiday or early close."""
    if not len(close):
        return np.zeros(0, dtype=bool)
    ny = close.tz_convert(NEW_YORK)
    nm = (ny.hour * 60 + ny.minute).to_numpy()
    day = trading_days(close)
    weekday = (day + 3) % 7                              # 1970-01-01 was a Thursday: Monday = 0
    uniq, inv = np.unique(day, return_inverse=True)
    hol = cme_holiday(list(uniq.astype("datetime64[D]").astype(object)))[inv]
    no = np.zeros(0, dtype=np.int64)
    return ((nm >= W_FROM) | (nm < W_TO)) & (weekday < 5) & ~hol & common_filter_ok(close, no, no)


def spread_wide(ctx: RuleContext, close: pd.DatetimeIndex) -> np.ndarray:
    """The spread gate at each decision close: the last 1m bar's `spread_mean` above SPREAD_X x the median of the
    SPREAD_MINUTES 1m bars before it; never gated with fewer bars before it."""
    out = np.zeros(len(close), dtype=bool)
    b1 = ctx.b1
    if not len(close) or len(b1) <= SPREAD_MINUTES:
        return out
    s = b1["spread_mean"].to_numpy(dtype=float)
    j = np.searchsorted(epoch_ns(decision_close(b1, "1m")), epoch_ns(close), side="right") - 1
    has = j >= SPREAD_MINUTES
    if has.any():
        win = np.lib.stride_tricks.sliding_window_view(s, SPREAD_MINUTES)[j[has] - SPREAD_MINUTES]
        with np.errstate(invalid="ignore"):
            out[has] = s[j[has]] > SPREAD_X * np.median(win, axis=1)
    return out


def trailing_median(x: np.ndarray, n: int) -> np.ndarray:
    """median(x, t-n..t-1): the trailing n bars before t, NaN until n exist."""
    return pd.Series(x).shift(1).rolling(n, min_periods=n).median().to_numpy(dtype=float)


def _lag(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:len(x) - k]
    return out


@dataclass
class _Bars:
    """The decision bars on mid prices, as arrays, with the rule's window mask."""
    arm: Arm
    ctx: RuleContext

    @cached_property
    def m(self) -> pd.DataFrame:
        return mid(self.ctx.bars).reset_index(drop=True)

    def col(self, k: str) -> np.ndarray:
        return self.m[k].to_numpy(dtype=float)

    @cached_property
    def close(self) -> pd.DatetimeIndex:
        return decision_close(self.ctx.bars, self.arm.timeframe)

    @cached_property
    def atr(self) -> np.ndarray:
        return atr(self.m, 14).to_numpy(dtype=float)

    @cached_property
    def ok(self) -> np.ndarray:
        return in_w(self.close)


def _brackets(arm: Arm, ctx: RuleContext, close: pd.DatetimeIndex, idx: np.ndarray, side: np.ndarray) -> pd.DataFrame:
    """The row's fixed bracket on each event, with the spread gate's verdict in `skip`."""
    if len(idx) == 0:
        return empty().assign(skip=pd.Series(dtype=object))
    p = arm.params
    out = _frame(idx, side, np.full(len(idx), float(p["stop_usd"])), float(p["target_r"]), int(p["hold_bars"]))
    skip = np.full(len(out), None, dtype=object)
    skip[spread_wide(ctx, close[out["idx"].to_numpy()])] = SPREAD_WIDE
    return out.assign(skip=skip)


def _events(b: _Bars, fire: np.ndarray, side: np.ndarray) -> pd.DataFrame:
    """Events in W with a side, at most one per the family's GAP bars (the first wins), bracketed."""
    with np.errstate(invalid="ignore"):
        hit = np.asarray(fire, dtype=bool) & b.ok & np.isfinite(side) & (side != 0)
    idx = spaced(np.flatnonzero(hit), GAP[b.arm.rule])
    return _brackets(b.arm, b.ctx, b.close, idx, np.asarray(side)[idx].astype(int))


def _htf(close: pd.DatetimeIndex, values: pd.DataFrame) -> pd.DataFrame:
    """`values` (one row per higher-timeframe bar, with its `visible_at`) of the last such bar closed by each decision
    close, via `asof_join` on `available_utc` (8.0); NaN before the first."""
    right = values.rename(columns={"visible_at": "available_utc"})
    left = pd.DataFrame({"ts_utc": close, "_i": np.arange(len(close))})
    return asof_join(left, right, on="ts_utc").sort_values("_i").reset_index(drop=True)


def _htf_bars(ctx: RuleContext, tf: str) -> pd.DataFrame:
    return mid(resample_bars(ctx.b1, tf)).reset_index(drop=True)


# ---------------------------------------------------------------- Z1 TSC, Z2 RXF, Z8 ABS (1m)
def tick_surge(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Z1: "v_t >= 2.5 x median(v, t-60..t-1); |c_t - o_t| >= 0.8 ATR_{t-1}; close in the outer 30% of the bar's range
    in the body's direction; at most 1 per 10 bars"; side sign(c_t - o_t)."""
    b = _Bars(arm, ctx)
    o, h, lo, c, v = b.col("open"), b.col("high"), b.col("low"), b.col("close"), b.col("tick_count")
    body, rng = c - o, h - lo
    with np.errstate(invalid="ignore", divide="ignore"):
        loc = np.where(rng > 0, (c - lo) / rng, np.nan)
        outer = ((body > 0) & (loc >= Z1_OUTER)) | ((body < 0) & (loc <= 1 - Z1_OUTER))
        fire = (v >= SURGE_X * trailing_median(v, SURGE_N)) & (np.abs(body) >= Z1_BODY_ATR * _lag(b.atr, 1)) & outer
    return _events(b, fire, np.sign(body))


def run_fade(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Z2: "three consecutive closes in one direction (all nonzero and the same sign); |c_t - c_{t-3}| >= 2.5
    ATR_{t-3}; v_t < v_{t-2} (participation fading); 1 per 10"; against the run."""
    b = _Bars(arm, ctx)
    c, v = b.col("close"), b.col("tick_count")
    d1, d2, d3 = c - _lag(c, 1), _lag(c, 1) - _lag(c, 2), _lag(c, 2) - _lag(c, 3)
    s = np.sign(d1)
    with np.errstate(invalid="ignore"):
        fire = ((s != 0) & (s == np.sign(d2)) & (s == np.sign(d3))
                & (np.abs(c - _lag(c, 3)) >= Z2_RUN_ATR * _lag(b.atr, 3)) & (v < _lag(v, 2)))
    return _events(b, fire, -s)


def absorb_fade(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Z8: "v_t >= 2.5 x median(v, t-60..t-1); |c_t - o_t| <= 0.2 ATR_{t-1}; P = c_{t-1} - c_{t-6} with |P| >= 1.5
    ATR_{t-1}; 1 per 10"; side -sign(P)."""
    b = _Bars(arm, ctx)
    o, c, v = b.col("open"), b.col("close"), b.col("tick_count")
    a1 = _lag(b.atr, 1)
    prior = _lag(c, 1) - _lag(c, 1 + Z8_PRIOR)
    with np.errstate(invalid="ignore"):
        fire = ((v >= SURGE_X * trailing_median(v, SURGE_N)) & (np.abs(c - o) <= Z8_BODY_ATR * a1)
                & (np.abs(prior) >= Z8_PRIOR_ATR * a1))
    return _events(b, fire, -np.sign(prior))


# ---------------------------------------------------------------- Z3 RNB (5m), Z4 RNR (1m): round numbers
def _handle(arm: Arm) -> tuple[float, float]:
    """(handle spacing, offset): $10 handles; Z4P (amendment (a)) "the $5-offset handles (prices = 5.00 mod 10.00)"."""
    return float(arm.params.get("handle_usd", HANDLE_USD)), float(arm.params.get("handle_offset_usd", 0.0))


def round_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Z3: "Long when floor(c_t/10) > floor(c_{t-1}/10) and c_t - 10 floor(c_t/10) >= 0.15 ATR_t; short mirrored
    (c_t <= 10 floor(c_{t-1}/10) - 0.15 ATR_t); 1 per 3"; the break side."""
    b = _Bars(arm, ctx)
    hs, off = _handle(arm)
    c = b.col("close")
    with np.errstate(invalid="ignore"):
        now, prev = np.floor((c - off) / hs), np.floor((_lag(c, 1) - off) / hs)
        up = (now > prev) & ((c - off) - now * hs >= Z3_BEYOND_ATR * b.atr)
        dn = (prev * hs + off) - c >= Z3_BEYOND_ATR * b.atr
    return _events(b, up | dn, np.where(up, 1, np.where(dn, -1, 0)))


def round_reject(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Z4: "U = 10 ceil(c_{t-1}/10), L = 10 floor(c_{t-1}/10). Short when h_t >= U, c_{t-1} < U and c_t <= U - 0.25
    ATR_{t-1}; long mirrored at L; 1 per 15"; the rejection side (both at once: no side, module docstring)."""
    b = _Bars(arm, ctx)
    hs, off = _handle(arm)
    h, lo, c = b.col("high"), b.col("low"), b.col("close")
    c1, a1 = _lag(c, 1), _lag(b.atr, 1)
    with np.errstate(invalid="ignore"):
        up_h, lo_h = hs * np.ceil((c1 - off) / hs) + off, hs * np.floor((c1 - off) / hs) + off
        short = (h >= up_h) & (c1 < up_h) & (c <= up_h - Z4_BACK_ATR * a1)
        long_ = (lo <= lo_h) & (c1 > lo_h) & (c >= lo_h + Z4_BACK_ATR * a1)
    side = np.where(short & ~long_, -1, np.where(long_ & ~short, 1, 0))
    return _events(b, short | long_, side)


# ---------------------------------------------------------------- Z5 XBT, Z6 NRB (5m)
def extreme_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Z5: "HH_t = max high of bars t-12..t-1 (LL mirrored). Long when c_t > HH_t + 0.1 ATR_t and c_{t-1} <= HH_{t-1}
    and v_t >= 1.5 x median(v, t-48..t-1); 1 per 6"; the break side."""
    b = _Bars(arm, ctx)
    h, lo, c, v = b.col("high"), b.col("low"), b.col("close"), b.col("tick_count")
    hh = pd.Series(h).shift(1).rolling(Z5_LOOK, min_periods=Z5_LOOK).max().to_numpy(dtype=float)
    ll = pd.Series(lo).shift(1).rolling(Z5_LOOK, min_periods=Z5_LOOK).min().to_numpy(dtype=float)
    c1 = _lag(c, 1)
    with np.errstate(invalid="ignore"):
        vol = v >= Z5_VOL_X * trailing_median(v, Z5_VOL_N)
        up = vol & (c > hh + Z5_BEYOND_ATR * b.atr) & (c1 <= _lag(hh, 1))
        dn = vol & (c < ll - Z5_BEYOND_ATR * b.atr) & (c1 >= _lag(ll, 1))
    return _events(b, up | dn, np.where(up, 1, np.where(dn, -1, 0)))


def nr4_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Z6: "range_{t-1} = min of the ranges of t-4..t-1, and bar t-1 is inside bar t-2; long when c_t > h_{t-1}, short
    when c_t < l_{t-1}; 1 per 3"; the break side."""
    b = _Bars(arm, ctx)
    h, lo, c = b.col("high"), b.col("low"), b.col("close")
    r1 = _lag(h - lo, 1)
    min4 = pd.Series(r1).rolling(4, min_periods=4).min().to_numpy(dtype=float)
    h1, l1 = _lag(h, 1), _lag(lo, 1)
    with np.errstate(invalid="ignore"):
        setup = (r1 <= min4) & (h1 <= _lag(h, 2)) & (l1 >= _lag(lo, 2))
        up, dn = setup & (c > h1), setup & (c < l1)
    return _events(b, up | dn, np.where(up, 1, np.where(dn, -1, 0)))


# ---------------------------------------------------------------- Z7 HHP (5m)
def halfhour_persist(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Z7: "at each 5m close on a New York half-hour (:00, :30): S = sum over the previous 10 trading days of the mid
    return over the same New York half-hour [τ, τ+30 min); >= 8 of the 10 days present, else skip; S = 0 skips";
    side sign(S); no spacing (one decision per half-hour). Z7P (amendment (a)) samples :15/:45 (`slots_ny`). τ is the
    bar's close (amendment (b) 1)."""
    b = _Bars(arm, ctx)
    close, c = b.close, b.col("close")
    ny = close.tz_convert(NEW_YORK)
    nm = (ny.hour * 60 + ny.minute).to_numpy().astype(np.int64)
    key = trading_days(close) * 1440 + nm                  # (trading day, New York wall minute) of each close
    last = pd.Series(np.arange(len(key)), index=key)
    pos = last[~last.index.duplicated(keep="last")]
    slots = [int(x) for x in arm.params.get("slots_ny", [0, 30])]
    dec = np.flatnonzero(b.ok & np.isin(ny.minute.to_numpy(), slots))
    if not len(dec):
        return _brackets(arm, ctx, close, dec, dec)
    day = key[dec] // 1440
    start, end = nm[dec], (nm[dec] + Z7_SPAN) % 1440
    total, present = np.zeros(len(dec)), np.zeros(len(dec), dtype=int)
    for k in range(1, Z7_DAYS + 1):
        prev = np.busday_offset(day.astype("datetime64[D]"), -k, roll="forward").astype(np.int64)
        i0 = pos.reindex(prev * 1440 + start).to_numpy(dtype=float)
        i1 = pos.reindex(prev * 1440 + end).to_numpy(dtype=float)
        ok = np.isfinite(i0) & np.isfinite(i1)
        r = np.zeros(len(dec))
        r[ok] = c[i1[ok].astype(int)] - c[i0[ok].astype(int)]
        total += r
        present += ok
    side = np.where(present >= Z7_MIN_PRESENT, np.sign(total), 0).astype(int)
    keep = side != 0
    return _brackets(arm, ctx, close, dec[keep], side[keep])


# ---------------------------------------------------------------- Z9 HPB, Z10 THM (5m, higher-timeframe votes)
def htf_reclaim(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Z9: "1h trend from the last closed 1h bar (`asof_join` on `available_utc`): up when EMA20 > EMA50 and close >
    EMA50, down mirrored. Long (trend up) when closes t-3..t-1 < EMA20(5m) and c_t > EMA20(5m)_t; short mirrored;
    1 per 6"; the trend side."""
    b = _Bars(arm, ctx)
    h1 = _htf_bars(ctx, "1h")
    c1h = h1["close"].astype(float)
    e20, e50 = ema(c1h, 20), ema(c1h, 50)
    trend = np.where((e20 > e50) & (c1h > e50), 1, np.where((e20 < e50) & (c1h < e50), -1, 0))
    tr = _htf(b.close, pd.DataFrame({"visible_at": h1["visible_at"], "trend": trend.astype(float)}))
    t = tr["trend"].to_numpy(dtype=float)
    c = b.col("close")
    e = ema(pd.Series(c), 20).to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        below = np.ones(len(c), dtype=bool)
        above = np.ones(len(c), dtype=bool)
        for k in range(1, Z9_BARS_BELOW + 1):
            below &= _lag(c, k) < _lag(e, k)
            above &= _lag(c, k) > _lag(e, k)
        up = (t == 1) & below & (c > e)
        dn = (t == -1) & above & (c < e)
    return _events(b, up | dn, np.where(up, 1, np.where(dn, -1, 0)))


def three_horizon(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Z10: "|c_t - c_{t-1}| >= 1.0 ATR_{t-1}, and its sign equals the sign of the return of the last closed 1h bar
    and of the 15m bar before the last closed one (so the 15m vote excludes bar t); 1 per 6"; that sign."""
    b = _Bars(arm, ctx)
    c = b.col("close")
    votes = []
    for tf, back in (("1h", 0), ("15m", 1)):
        hb = _htf_bars(ctx, tf)
        diff = hb["close"].astype(float).diff()
        ret = pd.Series(np.sign(diff.to_numpy()), index=diff.index).shift(back)
        votes.append(_htf(b.close, pd.DataFrame({"visible_at": hb["visible_at"], "v": ret}))["v"]
                     .to_numpy(dtype=float))
    s = np.sign(c - _lag(c, 1))
    with np.errstate(invalid="ignore"):
        fire = (np.abs(c - _lag(c, 1)) >= Z10_ATR * _lag(b.atr, 1)) & (s == votes[0]) & (s == votes[1])
    return _events(b, fire, s)


# ---------------------------------------------------------------- controls ZCD / ZCV
def scalp_control(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ZCD / ZCV: every 5m close in W with the common filters enters when hash_u(id, close) < p (the row's `p`); the
    side is drawn on its own key (module docstring); the row's 5m bracket."""
    b = _Bars(arm, ctx)
    close = b.close
    p = float(arm.params["p"])
    idx = np.array([i for i in np.flatnonzero(b.ok) if hash_u(arm.id, close[int(i)]) < p], dtype=int)
    side = np.array([hash_side(arm.id + ":side", close[int(i)]) for i in idx], dtype=int)
    return _brackets(arm, ctx, close, idx, side)


# ---------------------------------------------------------------- registration and load checks
RULE_FUNCS = {"tick_surge": tick_surge, "run_fade": run_fade, "round_break": round_break,
              "round_reject": round_reject, "extreme_break": extreme_break, "nr4_break": nr4_break,
              "halfhour_persist": halfhour_persist, "absorb_fade": absorb_fade, "htf_reclaim": htf_reclaim,
              "three_horizon": three_horizon, "scalp_control": scalp_control}


def check(arm: Arm) -> None:
    """Refused at load: a timeframe the family is not specified on, a bracket that is not a positive fixed price and
    hold, a Z7 slot off the 5m grid, a control without its entry probability in (0, 1)."""
    p: dict[str, Any] = arm.params
    if arm.timeframe != RULE_TF[arm.rule]:
        raise ValueError(f"{arm.rule} runs on {RULE_TF[arm.rule]} decision bars, not {arm.timeframe}")
    if not (float(p.get("stop_usd", 0)) > 0 and float(p.get("target_r", 0)) > 0 and int(p.get("hold_bars", 0)) >= 1):
        raise ValueError(f"{arm.rule} needs a fixed bracket: stop_usd > 0, target_r > 0, hold_bars >= 1")
    if arm.rule == "halfhour_persist" and not {int(s) for s in p.get("slots_ny", [0, 30])} <= set(range(0, 60, 5)):
        raise ValueError("halfhour_persist slots_ny must be minutes on the 5m grid")
    if arm.rule == "scalp_control" and not 0 < float(p.get("p", 0)) < 1:
        raise ValueError("scalp_control needs its entry probability p in (0, 1)")
