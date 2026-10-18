"""Money-flow signals from the owner's course notes (docs/research/indicator-coverage-2026-10-18.md), all point in
time: a value at bar t uses bars closed at or before t, and a pivot-based signal is stamped on the bar that confirms
the pivot (never on the pivot bar). Input is the `money_flow` line (`goldbot.features.smc_replica.money_flow`) and
the bars. Signed columns are +1 (bullish / long idea) or -1 (bearish / short idea), 0 otherwise.

* `mfi_regime` - +1 above 60 (bullish), -1 below 40 (bearish), 0 between (ranging).
* `mfi_extreme_circle` - the circle on entering an extreme: -1 on entering oversold (crossing under 20), +1 on
  entering overbought (crossing over 80). Entering OB/OS is also the notes' exit signal for the trade in that
  direction; this column is the entry event itself, not a trade direction.
* `mfi_double_circle` - the second circle: after an overbought entry, the bar that falls back under 80 is -1
  (possible short); after an oversold entry, the bar that rises back over 20 is +1 (possible long).
* `mfi_cross50` - +1 crossing over 50, -1 crossing under 50 (the notes: take the cross in the MFI direction).
* `mfi_pullback_hold` - trend pullback entry. Bullish (+1): the line was above 60, has not been below 40 since,
  dipped under 60 and the bar before this one was the dip low (>= 40) with this bar turning up. Bearish (-1) mirrored
  (below 40, never above 60 since, dip low is the high of the bounce <= 60, turning down).
* `mfi_divergence` - a confirmed price pivot (`pivot_len` bars each side) against the line at the pivot bar:
  -1 when a pivot high is higher than the previous pivot high while the line is lower there, +1 when a pivot low is
  lower than the previous pivot low while the line is higher. Stamped at the pivot's confirmation bar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.features.smc_replica import Arr, cross_over, cross_under, pivot_points

BULL, BEAR, MID = 60.0, 40.0, 50.0
OVERBOUGHT, OVERSOLD = 80.0, 20.0
COLUMNS = ("mfi_regime", "mfi_extreme_circle", "mfi_double_circle", "mfi_cross50", "mfi_pullback_hold",
           "mfi_divergence")


def _level(m: Arr, x: float) -> Arr:
    return np.full(len(m), x)


def _double_circle(m: Arr) -> Arr:
    out = np.zeros(len(m), dtype=np.int64)
    was_ob = was_os = False
    for i in range(1, len(m)):
        if np.isnan(m[i]) or np.isnan(m[i - 1]):
            continue
        if m[i] > OVERBOUGHT:
            was_ob = True
        elif was_ob and m[i] <= OVERBOUGHT:
            out[i], was_ob = -1, False
        if m[i] < OVERSOLD:
            was_os = True
        elif was_os and m[i] >= OVERSOLD:
            out[i], was_os = 1, False
    return out


def _pullback_hold(m: Arr) -> Arr:
    out = np.zeros(len(m), dtype=np.int64)
    bull = bear = False
    dipped = bounced = False
    for i in range(2, len(m)):
        if np.isnan(m[i]) or np.isnan(m[i - 1]) or np.isnan(m[i - 2]):
            continue
        if m[i] > BULL:
            bull, dipped = True, False
        elif m[i] < BEAR:
            bull = False
        elif bull:
            dipped = True
        if m[i] < BEAR:
            bear, bounced = True, False
        elif m[i] > BULL:
            bear = False
        elif bear:
            bounced = True
        low = m[i - 1]
        if bull and dipped and low < BULL and low >= BEAR and m[i - 2] > low and m[i] > low:
            out[i], dipped = 1, False
        elif bear and bounced and low > BEAR and low <= BULL and m[i - 2] < low and m[i] < low:
            out[i], bounced = -1, False
    return out


def _divergence(m: Arr, high: Arr, low: Arr, pivot_len: int) -> Arr:
    out = np.zeros(len(m), dtype=np.int64)
    ph, pl = pivot_points(high, low, pivot_len)
    prev_h = prev_l = None                      # (price, line value) at the previous pivot of each kind
    for i in range(len(m)):
        j = i - pivot_len                       # the pivot bar, known only now
        if not np.isnan(ph[i]) and j >= 0 and not np.isnan(m[j]):
            if prev_h is not None and ph[i] > prev_h[0] and m[j] < prev_h[1]:
                out[i] = -1
            prev_h = (ph[i], m[j])
        if not np.isnan(pl[i]) and j >= 0 and not np.isnan(m[j]):
            if prev_l is not None and pl[i] < prev_l[0] and m[j] > prev_l[1]:
                out[i] = 1
            prev_l = (pl[i], m[j])
    return out


def mfi_signals(mfi: Arr, high: Arr, low: Arr, pivot_len: int = 5) -> pd.DataFrame:
    """The six columns above for a money-flow series and its bars (NaN warm-up gives 0)."""
    m = np.asarray(mfi, dtype=float)
    with np.errstate(invalid="ignore"):
        regime = np.where(m > BULL, 1, np.where(m < BEAR, -1, 0))
    circle = cross_over(m, _level(m, OVERBOUGHT)).astype(np.int64) - cross_under(m, _level(m, OVERSOLD)).astype(np.int64)
    c50 = cross_over(m, _level(m, MID)).astype(np.int64) - cross_under(m, _level(m, MID)).astype(np.int64)
    return pd.DataFrame({
        "mfi_regime": regime.astype(np.int64), "mfi_extreme_circle": circle, "mfi_double_circle": _double_circle(m),
        "mfi_cross50": c50, "mfi_pullback_hold": _pullback_hold(m),
        "mfi_divergence": _divergence(m, np.asarray(high, dtype=float), np.asarray(low, dtype=float), pivot_len)})
