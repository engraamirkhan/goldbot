"""Band-break arm rules of the time-structure programme (slice F, docs/research/time-structure/volatility-bands.md):
H-70 F-1 (arms F1A / F1C, `prereg-2026-10-26-time-structure.md` section 2) and F-2 (arms F2A / F2C,
`arms-2026-10-25.md` amendment 8.13). One function per rule, used by the history matrix and live (ARM-SPEC 8.0:
"Each rule is one function used by the history matrix and live, and its test proves prefix invariance").

Shared definitions (prereg 2.1, "identical in history and live"), on the mid prices of the 15m decision bars:

* KC = EMA20(close) +/- 2.0 x ATR14 (Wilder); BB = SMA20(close) +/- 2 x SD20(close) (population SD); DC20 = highest
  high / lowest low of bars i-20..i-1.
* Break event: "close_i outside at least one band while close_{i-1} was inside that band. Side of the break = +1
  above, -1 below. At most one event per 4 bars." The side of a bar breaking several bands is the sign of their sum
  (slice F's union).
* Exit: "stop = entry -/+ 1.0 x ATR14(15m) at bar i ... Time exit at the close of bar i+4. No target." The barrier form
  needs a target, so it is set out of reach (`NO_TARGET_R`, as `asia_drift`'s NO_TARGET_ATR); the arm's `hold_bars`
  (4) carries the time exit. The stop band ($3.00-8.64 for F1, $3.00-22.60 for F2) is the arm's `min_stop_usd` /
  `max_stop_usd`, applied by `arm_rules.post_filter` with the common filters.

`hurst_band_break` (F1A): "`hurst_128` = `goldbot.features.survey.rolling_hurst` of 15m log mid returns. Its tercile is
the percentile rank among bars i-249..i: <= 1/3 low, > 2/3 high." Low tercile: trade with the break; high: against
it; middle: no trade. The 4-bar spacing is counted between traded events (slice F's `dedup` ran on the X1 events, and
that is the event set whose count the prereg quotes).

`hurst_band_break_control` (F1C): "fires at the same decision times as every F1A candidate (taken or skipped). Side
from the frozen hash with arm id `F1C`; same exits and stop band." The netting-host shift is the runner's, not the
rule's.

`htf_stretch_break` (F2A): "pos4 = (close - EMA20) / (2 x ATR14) of the last closed 4h bar, joined via `asof_join` on
`available_utc`. Entry: when the break side is opposite the sign of pos4 and |pos4| >= 1, trade with the 4h stretch
(side = sign(pos4))." The 4-bar spacing is counted between F2A events (slice F's X2 `dedup`). As a wave-2 arm it also
takes 8.0's "no entry 16:55-17:00 or 18:00-18:30 New York" (decision close, inclusive).

`htf_stretch_break_control` (F2C): "fires on a 15m close where |pos4| >= 1, no F2A event occurs and
`hash_u("F2C", close) < 0.10`. Side = sign(pos4). F2A's stop, band and time exit."

Live reads only `LOOKBACK_BARS` decision bars of history: the Hurst tercile is defined from bar 378 and the EMA/ATR
recursions must have forgotten their start, so a shorter window never fires (F1) or fires on different values (F2's
4h EMA20/ATR14). `LOOKBACK_BARS` is the window at which live equals the history run (tested)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.config import tf_seconds
from goldbot.data.resample import mid, resample_bars
from goldbot.data.store import asof_join
from goldbot.data.timeutil import utc_index
from goldbot.features.survey import rolling_hurst
from goldbot.features.technical import atr, ema
from goldbot.research.arm_rules import NEW_YORK, RuleContext, _frame, empty, hash_side, hash_u
from goldbot.research.arms import Arm

BAND_N = 20                 # KC/BB/DC lookback
KC_K = BB_K = 2.0           # band widths (ATR for KC, SD for BB)
ATR_N = 14
GAP_BARS = 4                # "At most one event per 4 bars"
HURST_RANK_BARS = 250       # "percentile rank among bars i-249..i"
STOP_ATR = 1.0              # "stop = entry -/+ 1.0 x ATR14(15m) at bar i"
NO_TARGET_R = 50.0          # "No target": 50 R of a 1 x ATR15 stop is out of reach in 4 bars
HTF = "4h"                  # F2's stretched timeframe
STRETCH = 1.0               # "|pos4| >= 1"
CONTROL_RATE = 0.10         # F2C: hash_u(arm id, close) < 0.10
# 8.0: "no entry 16:55-17:00 or 18:00-18:30 New York" (minutes of the New York day, decision close, inclusive)
NY_REOPEN = ((16 * 60 + 55, 17 * 60), (18 * 60, 18 * 60 + 30))
# decision bars live must hand the rule for its candidates to equal the history run's (module docstring): F1 needs
# 378 for the tercile; F2's 4h EMA20/ATR14 need about 250 closed 4h bars (16 decision bars each) to forget their start
LOOKBACK_BARS = {"hurst_band_break": 1_000, "hurst_band_break_control": 1_000,
                 "htf_stretch_break": 4_000, "htf_stretch_break_control": 4_000}


# ---------------------------------------------------------------- shared definitions
def band_breaks(m: pd.DataFrame) -> np.ndarray:
    """+1 / -1 / 0 per decision bar: the close leaves KC, BB or DC20 from inside (prereg 2.1), before any spacing."""
    c = m["close"].astype(float)
    a = atr(m, ATR_N)
    e = ema(c, BAND_N)
    sma = c.rolling(BAND_N, min_periods=BAND_N).mean()
    sd = c.rolling(BAND_N, min_periods=BAND_N).std(ddof=0)
    bands = ((e + KC_K * a, e - KC_K * a), (sma + BB_K * sd, sma - BB_K * sd),
             (m["high"].rolling(BAND_N).max().shift(1), m["low"].rolling(BAND_N).min().shift(1)))
    total = np.zeros(len(m), dtype=int)
    c1 = c.shift(1)
    for up, lo in bands:
        valid = (up.notna() & lo.notna()).to_numpy()
        brk_up = ((c > up) & (c1 <= up.shift(1))).to_numpy()
        brk_dn = ((c < lo) & (c1 >= lo.shift(1))).to_numpy()
        total += np.where(valid & brk_up, 1, np.where(valid & brk_dn, -1, 0))
    return np.sign(total).astype(int)


def hurst_tercile(m: pd.DataFrame) -> np.ndarray:
    """0 low / 1 middle / 2 high / -1 undefined: `hurst_128` of the log mid returns ranked among bars i-249..i (pandas
    average rank over the window's defined values, as `hurst_proxy_check_2026-10-26.py`); <= 1/3 low, > 2/3 high."""
    r = pd.Series(np.log(m["close"].to_numpy(dtype=float))).diff().to_numpy(dtype=float)
    h = pd.Series(rolling_hurst(r))
    rank = h.rolling(HURST_RANK_BARS, min_periods=HURST_RANK_BARS).rank(pct=True).to_numpy(dtype=float)
    out = np.where(rank <= 1 / 3, 0, np.where(rank > 2 / 3, 2, 1))
    return np.where(np.isfinite(rank) & np.isfinite(h.to_numpy()), out, -1)


def spaced(idx: np.ndarray, gap: int = GAP_BARS) -> np.ndarray:
    """Events at least `gap` bars after the last kept one, in time order (slice F's `dedup`): causal, a kept event
    depends only on earlier events."""
    keep: list[int] = []
    last = -10 ** 9
    for i in idx:
        if i - last >= gap:
            keep.append(int(i))
            last = int(i)
    return np.asarray(keep, dtype=int)


def htf_kc_pos(ctx: RuleContext) -> np.ndarray:
    """pos4 = (close - EMA20) / (2 x ATR14) of the last 4h bar closed by each decision close, joined with `asof_join`
    on the 4h bars' `visible_at` (their availability); NaN before the first. A 4h bar still forming at the decision
    close is visible only at its nominal close, so it never joins early."""
    b4 = mid(resample_bars(ctx.b1, HTF)).reset_index(drop=True)
    c4 = b4["close"].astype(float)
    pos = (c4 - ema(c4, BAND_N)) / (KC_K * atr(b4, ATR_N))
    right = pd.DataFrame({"available_utc": utc_index(b4["visible_at"]), "pos4": pos.to_numpy(dtype=float)})
    left = pd.DataFrame({"ts_utc": ctx.close_ts, "_i": np.arange(len(ctx.bars))})
    joined = asof_join(left, right, on="ts_utc").sort_values("_i")
    return joined["pos4"].to_numpy(dtype=float)


def _ny_reopen(close: pd.DatetimeIndex) -> np.ndarray:
    loc = close.tz_convert(NEW_YORK)
    mins = (loc.hour * 60 + loc.minute).to_numpy()
    out = np.zeros(len(close), dtype=bool)
    for lo, hi in NY_REOPEN:
        out |= (mins >= lo) & (mins <= hi)
    return out


def _brackets(arm: Arm, m: pd.DataFrame, idx: np.ndarray, side: np.ndarray) -> pd.DataFrame:
    """1.0 x ATR14 of the decision bar as the stop, no target, the arm's `hold_bars` as the time exit."""
    if len(idx) == 0:
        return empty()
    a = atr(m, ATR_N).to_numpy(dtype=float)
    return _frame(idx, side, STOP_ATR * a[idx], NO_TARGET_R, int(arm.params["hold_bars"]))


# ---------------------------------------------------------------- H-70 F-1 (F1A, F1C)
def _f1_events(ctx: RuleContext) -> tuple[np.ndarray, np.ndarray]:
    m = ctx.mid
    brk, terc = band_breaks(m), hurst_tercile(m)
    side = np.where(terc == 0, brk, np.where(terc == 2, -brk, 0))
    idx = spaced(np.flatnonzero(side != 0))
    return idx, side[idx]


def hurst_band_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """F1A (prereg 2.1): a band break traded with it in the low Hurst tercile and against it in the high one."""
    idx, side = _f1_events(ctx)
    return _brackets(arm, ctx.mid, idx, side)


def hurst_band_break_control(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """F1C (prereg 2.2): F1A's decision times, side from the arm's own hash, F1A's stop and time exit."""
    idx, _ = _f1_events(ctx)
    close = ctx.close_ts
    side = np.array([hash_side(arm.id, close[int(i)]) for i in idx], dtype=int)
    return _brackets(arm, ctx.mid, idx, side)


# ---------------------------------------------------------------- F-2 (F2A, F2C; amendment 8.13)
def _f2_state(arm: Arm, ctx: RuleContext) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """F2A's events (idx, side) and pos4 on every decision bar."""
    if tf_seconds(arm.timeframe) >= tf_seconds(HTF):
        raise ValueError(f"{arm.id}: {arm.rule} decides below {HTF}, got {arm.timeframe}")
    brk = band_breaks(ctx.mid)
    pos = htf_kc_pos(ctx)
    with np.errstate(invalid="ignore"):
        stretched = np.isfinite(pos) & (np.abs(pos) >= STRETCH)
        against = stretched & (brk != 0) & (np.sign(pos) == -brk)
    idx = spaced(np.flatnonzero(against))
    ok = ~_ny_reopen(ctx.close_ts[idx])
    return idx[ok], np.sign(pos[idx[ok]]).astype(int), pos


def htf_stretch_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """F2A (8.13): a 15m break against a 4h bar stretched beyond its Keltner band, traded with the stretch."""
    idx, side, _ = _f2_state(arm, ctx)
    return _brackets(arm, ctx.mid, idx, side)


def htf_stretch_break_control(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """F2C (8.13): slice F's state-matched control, a seeded 10% of the stretched closes with no F2A event, with the
    stretch; F2A's stop and time exit."""
    events, _, pos = _f2_state(arm, ctx)
    close = ctx.close_ts
    with np.errstate(invalid="ignore"):
        stretched = np.isfinite(pos) & (np.abs(pos) >= STRETCH)
    stretched[events] = False
    stretched &= ~_ny_reopen(close)
    cand = np.flatnonzero(stretched)
    idx = np.array([int(i) for i in cand if hash_u(arm.id, close[int(i)]) < CONTROL_RATE], dtype=int)
    side = np.sign(pos[idx]).astype(int) if len(idx) else np.zeros(0, dtype=int)
    return _brackets(arm, ctx.mid, idx, side)


RULES = {"hurst_band_break": hurst_band_break, "hurst_band_break_control": hurst_band_break_control,
         "htf_stretch_break": htf_stretch_break, "htf_stretch_break_control": htf_stretch_break_control}
