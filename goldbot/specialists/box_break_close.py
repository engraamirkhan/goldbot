"""`box_break_close`: the owner's own box strategy as a rule agent (owner directive 2026-10-19; pre-registration
docs/research/shadow-roster-2026-10-19.md), on 15m, 30m, 1h and 4h decision bars (30m: amendment 2026-10-19b),
SHADOW ONLY. Not 5m: the engine decides every timeframe only at its 15m base-clock closes and advances shadow trades
one bar per such close, so a 5m agent would skip two bars in three and misstate its outcomes.

The rule ("a box breaks AND closes", docs/research/knowledge-base/support-resistance-and-zones.md R3/R4, money flow
M7: "box = where, mfi = which side carries the risk"), fixed here before any count, one parameter set:

* Boxes: the replica's support/resistance channels (`smc_replica`, the owner's chart settings `ReplicaParams()`), the
  boxes in force at the decision bar t (after that bar's pivot update, as the replica's own break columns read them).
* Break and close, long: the previous close is below a box top and bar t closes at or above it by at least
  BREAK_DEPTH x the box height, with a bullish body (close > open); the farthest box crossed (highest top) is the
  broken box. Short mirrored (a close through a box bottom, the lowest bottom crossed, a bearish body).
* Money flow confirms: long only when the mfi (n = 12 on tick counts) was at or below MFI_LOW on one of bars t-4..t and
  turns up on t (the move out of oversold); short: at or above MFI_HIGH and turning down.
* Structure context: never against the swing structure (long needs swing trend != -1, short != +1).
* Not inside the golden zone (the close strictly between the 50% and 61.8% retracement of the last 50 bars).
* Stop: just beyond the broken box's far side, at STOP_ATR x ATR(14) of the decision bars; a signal whose structural
  stop (the far side minus BUFFER_ATR x ATR) lies farther than that is skipped, so the stop always sits beyond the box.
  Target: TARGET_ATR = 2 x the stop (the course's 2:1 minimum, a fixed R multiple). Time exit HOLD_BARS by timeframe.

Point in time: every value is the replica's, stamped on the bar whose close makes it known; bar t's own close is the
last price read. Research runs `candidates` on the whole series; the engine resumes the replica from the state carried
one bar back (`state_lag` 1, `engine.replica_context.ReplicaShadow`) and decides on bar t only: both give the same
decision (tests/test_box_break_close.py, prefix invariance). Shadow only: `replica_params` makes `shadow_only` true, so
the engine gives it share 0 and only the shadow book trades it; its registry model is a `RuleModel` (ADR 0038).
`screening` keeps it out of the loop grid, the pools and the default founders (the trial budget is untouched); the
frozen roster (`ROSTER_TIMEFRAMES`) is what makes its four founders.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.features import smc_replica as smc
from goldbot.features.smc_live import STATE_KEY
from goldbot.features.technical import atr
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import Specialist, register

FAMILY = "box_break_close"
BREAK_DEPTH = 0.2          # course: the body closes 20-30% of the box height beyond the far edge
MFI_LOW, MFI_HIGH = 30.0, 70.0   # course: oversold ~20-30, overbought ~70-80
MFI_WINDOW = 5             # bars t-4..t
BUFFER_ATR = 0.1           # the structural stop's buffer beyond the box (box_course section 3)
STOP_ATR = 1.5
TARGET_ATR = 3.0           # 2R
HOLD_BARS = {"15m": 96, "30m": 72, "1h": 48, "4h": 24}    # 24 h, 36 h, 48 h, 96 h
ROSTER_TIMEFRAMES = ("15m", "30m", "1h", "4h")


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int)})


def decide_with_stop(t: int, o: np.ndarray, c: np.ndarray, mfi: np.ndarray, a: np.ndarray, tops: np.ndarray,
                     bots: np.ndarray, trend: float, f1: float, f2: float, *, force_mfi: bool = False,
                     stop_atr: float = STOP_ATR) -> tuple[int, float]:
    """(side, structural stop distance in price units) the rule takes at bar t; (0, nan) for none. `force_mfi` and
    `stop_atr` exist for the exploration arms (I2A: money-flow confirmation off, I2B: stop 1.0 ATR); the defaults are the
    roster rule."""
    if t < MFI_WINDOW or not len(tops) or not np.isfinite(a[t]) or a[t] <= 0:
        return 0, float("nan")
    if np.isfinite(f1) and np.isfinite(f2) and f1 * f2 < 0:
        return 0, float("nan")                                      # inside the golden zone
    hgt = tops - bots
    with np.errstate(invalid="ignore"):
        up = (c[t - 1] < tops) & (c[t] >= tops + BREAK_DEPTH * hgt) & (c[t] > o[t])
        dn = (c[t - 1] > bots) & (c[t] <= bots - BREAK_DEPTH * hgt) & (c[t] < o[t])
    w = mfi[t - MFI_WINDOW + 1:t + 1]
    if not (np.isfinite(mfi[t]) and np.isfinite(mfi[t - 1])):
        return 0, float("nan")
    if up.any() and not dn.any():
        k = int(np.argmax(np.where(up, tops, -np.inf)))
        mfi_ok = force_mfi or bool(np.nanmin(w) <= MFI_LOW and mfi[t] > mfi[t - 1])
        stop = bots[k] - BUFFER_ATR * a[t]
        if mfi_ok and trend != -1 and c[t] - stop <= stop_atr * a[t]:
            return 1, float(c[t] - stop)
    if dn.any() and not up.any():
        k = int(np.argmin(np.where(dn, bots, np.inf)))
        mfi_ok = force_mfi or bool(np.nanmax(w) >= MFI_HIGH and mfi[t] < mfi[t - 1])
        stop = tops[k] + BUFFER_ATR * a[t]
        if mfi_ok and trend != 1 and stop - c[t] <= stop_atr * a[t]:
            return -1, float(stop - c[t])
    return 0, float("nan")


def decide(t: int, o: np.ndarray, c: np.ndarray, mfi: np.ndarray, a: np.ndarray, tops: np.ndarray,
           bots: np.ndarray, trend: float, f1: float, f2: float) -> int:
    """The side the rule takes at bar t (0: none); arrays are the window's, `tops`/`bots` the boxes in force at t."""
    return decide_with_stop(t, o, c, mfi, a, tops, bots, trend, f1, f2)[0]


@register
class BoxBreakCloseSpecialist(Specialist):
    family = FAMILY
    timeframe = "15m"
    timeframes = ("30m", "1h", "4h")
    default_config: dict[str, Any] = {}
    screening = True               # out of the loop grid, pools and default founders: the roster seeds it
    frozen_config = True           # one parameter set: never cloned with mutations (shadow roster rule)
    state_lag = 1

    @property
    def replica_params(self) -> smc.ReplicaParams:
        return smc.DEFAULT_PARAMS

    @property
    def label_spec(self) -> BarrierSpec:
        return BarrierSpec(target_atr=TARGET_ATR, stop_atr=STOP_ATR, max_bars=HOLD_BARS[self.timeframe], name=FAMILY)

    def _arrays(self, bars: pd.DataFrame) -> tuple[np.ndarray, ...]:
        p = self.replica_params
        o, h, lo, c = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        vol = (bars["tick_count"] if "tick_count" in bars.columns else bars["volume"]).to_numpy(dtype=float)
        return o, c, smc.money_flow(h, lo, c, vol, p.mfi_len), atr(bars, 14).to_numpy(dtype=float)

    def candidates_with_risk(self, mid_bars: pd.DataFrame, *, force_mfi: bool = False,
                             stop_atr: float = STOP_ATR) -> pd.DataFrame:
        """`candidates` plus each candidate's structural stop distance (`risk_usd`), for the exploration arms."""
        bars = mid_bars.reset_index(drop=True)
        if len(bars) < 2:
            return _empty().assign(risk_usd=pd.Series(dtype=float))
        p = self.replica_params
        frame = smc.replica_frame(bars, p)
        trace: list[tuple[np.ndarray, np.ndarray]] = []
        smc.sr_channel_breaks(bars["open"].to_numpy(float), bars["high"].to_numpy(float), bars["low"].to_numpy(float),
                              bars["close"].to_numpy(float), p.sr_pivot_len, p.sr_width_pct, p.sr_max_channels,
                              p.sr_lookback, p.sr_range_window, trace)
        o, c, mfi, a = self._arrays(bars)
        trend = frame["trend_swing"].to_numpy(dtype=float)
        f1, f2 = frame["range_f1_dist_atr"].to_numpy(float), frame["range_f2_dist_atr"].to_numpy(float)
        res = [decide_with_stop(t, o, c, mfi, a, trace[t][0], trace[t][1], trend[t], f1[t], f2[t],
                                force_mfi=force_mfi, stop_atr=stop_atr) for t in range(len(bars))]
        sides = np.array([r[0] for r in res], dtype=int)
        risk = np.array([r[1] for r in res], dtype=float)
        idx = np.flatnonzero(sides)
        return pd.DataFrame({"idx": idx.astype(int), "side": sides[idx].astype(int), "risk_usd": risk[idx]})

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        """Research: the rule on every bar of `mid_bars`, the replica run from its first bar."""
        return self.candidates_with_risk(mid_bars).drop(columns="risk_usd")

    def candidates_in_context(self, mid_bars: pd.DataFrame, features: pd.DataFrame,
                              context: dict[str, pd.DataFrame] | None,
                              ctx: dict[str, Any] | None = None) -> pd.DataFrame:
        """With `ctx["smc_replica_state"]` (the state after bar t-1, the engine): the decision on the last bar only."""
        state = (ctx or {}).get(STATE_KEY)
        if state is None:
            return self.candidates(mid_bars, features)
        bars = mid_bars.reset_index(drop=True)
        frame, end = smc.replica_run(bars, self.replica_params, state)
        if len(frame) != 1:
            return _empty()                     # decide only the bar right after the snapshot
        t = len(bars) - 1
        o, c, mfi, a = self._arrays(bars)
        row = frame.iloc[-1]
        side = decide(t, o, c, mfi, a, np.asarray(end.channels.tops, float), np.asarray(end.channels.bottoms, float),
                      float(row["trend_swing"]), float(row["range_f1_dist_atr"]), float(row["range_f2_dist_atr"]))
        return pd.DataFrame({"idx": [t], "side": [side]}) if side else _empty()
