"""Point-in-time building blocks of the box and price-action course families (H-37..H-45; shared definitions in
docs/research/box-mfi-families-2026-10-18.md section 3, written before any count). The rules themselves live in
`goldbot.specialists.box_course`; this module only turns bars into per-bar arrays and answers "what was visible at".

Design rule (section 3, "Point in time"): "At a 15m decision bar, 1h/4h values are those of the last bar with
`visible_at` <= the 15m bar's close; boxes, pivots and MFI are stamped on the bar whose close makes them known."

* `TfBars` holds one timeframe's mid bars (`data.resample.mid`) as arrays: OHLC, spread, tick count, Wilder ATR(14)
  (`smc_replica.atr_seeded`), the money-flow line (`smc_replica.money_flow`, n = 12 on tick counts), seeded EMAs on
  demand, and optionally the boxes in force at every bar (the replica's channel trace, owner settings: pivot 5, width
  1% of the 300-bar range, 15 boxes, lookback 300). Every array at row i uses rows <= i only (window statistics or
  recursions from the first bar), so appending bars never changes a past value.
* `visible(src_close_ns, dst)` is the one mapping between timeframes: the row of `dst` whose close is the last at or
  before each source close (-1 before the first).
* `structure_trend` is section 3's daily-trend reading on any timeframe: classic pivots of `length` stamped on their
  confirming bar; +1 when the last two confirmed pivot highs and the last two pivot lows are both higher (HH+HL), -1
  when both lower, else 0.
* Candle shapes, the floor pivots, the golden zone, the next box and the placeable rule are written here once so every
  family reads the same definition.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features import smc_replica as smc

ATR_LEN = 14
MFI_LEN = 12                    # the owner's money-flow period
MFI_TICK_COVERAGE = 0.95        # section 3: "MFI years with tick counts missing on > 5% of bars are excluded"
NS = 1_000_000_000

# The engine's entry gate as research charges it (section 3, "Placeable"): target >= risk.min_target_over_cost x
# (entry-bar spread + the round-trip cost prior), stop >= stops level 0 + the 20-point spread prior. Pinned to the
# settings by tests/test_box_course.py, so a settings change cannot drift silently from these rules.
MIN_TARGET_OVER_COST = 2.5
COST_PRIOR_USD = 0.37
MIN_STOP_USD = 0.20


class TfBars:
    """One timeframe's bars as arrays (module docstring). `boxes[i]` = (tops, bottoms) in force at row i."""

    def __init__(self, m: pd.DataFrame, tf: str, with_boxes: bool = False) -> None:
        m = m.reset_index(drop=True)
        self.tf = tf
        self.n = len(m)
        self.ts_ns = epoch_ns(utc_index(m["ts_utc"]))
        self.close_ns = epoch_ns(utc_index(m["visible_at"]))
        self.o, self.h, self.l, self.c = (m[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        self.spread = m["spread"].to_numpy(dtype=float) if "spread" in m else np.zeros(self.n)
        self.tick = m["tick_count"].to_numpy(dtype=float) if "tick_count" in m else np.full(self.n, np.nan)
        self.atr = smc.atr_seeded(self.h, self.l, self.c, ATR_LEN) if self.n else np.zeros(0)
        self.mfi = smc.money_flow(self.h, self.l, self.c, self.tick, MFI_LEN) if self.n else np.zeros(0)
        self.mfi_ok = _tick_years_ok(self.ts_ns, self.tick)
        self._ema: dict[int, np.ndarray] = {}
        self.boxes: list[tuple[np.ndarray, np.ndarray]] | None = None
        if with_boxes:
            trace: list[tuple[np.ndarray, np.ndarray]] = []
            if self.n:
                smc.sr_channel_breaks(self.o, self.h, self.l, self.c, trace=trace)
            self.boxes = trace

    def ema(self, n: int) -> np.ndarray:
        """Seeded EMA(n) of the close (the replica's own EMA)."""
        if n not in self._ema:
            self._ema[n] = smc.ema_seeded(self.c, n) if self.n else np.zeros(0)
        return self._ema[n]

    def box_set(self, i: int) -> tuple[np.ndarray, np.ndarray]:
        """(tops, bottoms) in force at row i; empty before the first row or without boxes."""
        if self.boxes is None or i < 0:
            return np.zeros(0), np.zeros(0)
        return self.boxes[i]


def _tick_years_ok(ts_ns: np.ndarray, tick: np.ndarray) -> np.ndarray:
    """Per row: its calendar year has a tick count on at least MFI_TICK_COVERAGE of its bars. A year's share is a fact
    of the whole year, known only at its end; it is a data-quality exclusion fixed before any count (section 3) that
    reads no price, so it is the same whatever the candidate's outcome."""
    if not len(ts_ns):
        return np.zeros(0, dtype=bool)
    year = pd.DatetimeIndex(ts_ns, tz="UTC").year.to_numpy()
    have = np.isfinite(tick) & (tick > 0)
    share = pd.Series(have).groupby(year).transform("mean").to_numpy()
    return np.asarray(share >= MFI_TICK_COVERAGE)


def visible(src_close_ns: np.ndarray | int, dst: TfBars) -> np.ndarray:
    """Row of `dst` whose close is the last at or before each source close (-1 before the first)."""
    return np.searchsorted(dst.close_ns, np.atleast_1d(np.asarray(src_close_ns, dtype=np.int64)), side="right") - 1


def last_two(levels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(latest, previous) confirmed value at each row of a series that is NaN except on confirming rows."""
    n = len(levels)
    last, prev = np.full(n, np.nan), np.full(n, np.nan)
    cur_last = cur_prev = np.nan
    for i in range(n):
        if not np.isnan(levels[i]):
            cur_prev, cur_last = cur_last, levels[i]
        last[i], prev[i] = cur_last, cur_prev
    return last, prev


def structure_trend(high: np.ndarray, low: np.ndarray, length: int) -> np.ndarray:
    """+1 HH+HL, -1 LH+LL, 0 otherwise, from classic pivots of `length` stamped on their confirming bar."""
    ph, pl = smc.pivot_points(high, low, length)
    h1, h0 = last_two(ph)
    l1, l0 = last_two(pl)
    with np.errstate(invalid="ignore"):
        up = (h1 > h0) & (l1 > l0)
        dn = (h1 < h0) & (l1 < l0)
    return np.where(up, 1, np.where(dn, -1, 0)).astype(int)


def floor_pivots(d: TfBars) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(PP, R1, S1) of each daily row from its own high, low and close: at a later bar the last visible daily row is
    the previous day, so these are "the previous day's floor pivots"."""
    pp = (d.h + d.l + d.c) / 3.0
    return pp, 2 * pp - d.l, 2 * pp - d.h


def rolling(x: np.ndarray, n: int, how: str) -> np.ndarray:
    s = pd.Series(x).rolling(n, min_periods=n)
    return (s.max() if how == "max" else s.min() if how == "min" else s.mean()).to_numpy()


# ------------------------------------------------------------------------------------------------------ candles
def candle_parts(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> dict[str, np.ndarray]:
    """Range, body and wicks of every bar (range 0 gives NaN shares, which compare False)."""
    rng = h - lo
    with np.errstate(invalid="ignore", divide="ignore"):
        inv = np.where(rng > 0, 1.0 / np.where(rng > 0, rng, 1.0), np.nan)
    return {"range": rng, "body": np.abs(c - o) * inv, "upper": (h - np.maximum(o, c)) * inv,
            "lower": (np.minimum(o, c) - lo) * inv}


def course_signals(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> dict[str, np.ndarray]:
    """H-45's S1 candles (section 4.0), each a boolean per bar, written as the table states them; the short list is
    not the long list mirrored (the course's asymmetric rules are kept)."""
    p = candle_parts(o, h, lo, c)
    o1, c1, h1, l1 = (np.r_[np.nan, x[:-1]] for x in (o, c, h, lo))
    o2, c2, h2, l2 = (np.r_[np.nan, np.nan, x[:-2]] for x in (o, c, h, lo))
    p2 = candle_parts(o2, h2, l2, c2)
    p1 = candle_parts(o1, h1, l1, c1)
    with np.errstate(invalid="ignore"):
        bear1, bull1 = c1 < o1, c1 > o1
        mid1 = (o1 + c1) / 2.0
        mid2 = (o2 + c2) / 2.0
        return {
            "hammer": (p["lower"] >= 0.6) & (p["body"] <= 0.35),
            "bull_engulf": bear1 & (o <= c1) & (c >= o1),
            "piercing": bear1 & (o <= c1) & (c > mid1) & (c < o1),
            "morning_star": (c2 < o2) & (p2["body"] >= 0.5) & (p1["body"] <= 0.3) & (c > o) & (c > mid2),
            "inv_hammer": (p["upper"] >= 0.6) & (p["body"] <= 0.35) & (p["lower"] <= 0.1),
            "shooting_star": (p["upper"] >= 0.6) & (p["body"] <= 0.35),
            "bear_engulf": bull1 & (o >= c1) & (c <= o1),
            "dark_cloud": bull1 & (o >= c1) & (c < mid1) & (c > o1),
            "evening_star": (c2 > o2) & (p2["body"] >= 0.5) & (p1["body"] <= 0.3) & (c < o) & (c < mid2),
        }


def pin_bar(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, body_max: float = 0.25) -> np.ndarray:
    """H-42's N1 pin, signed: +1 lower wick >= 0.65 range and body <= `body_max` range, -1 mirrored, else 0."""
    p = candle_parts(o, h, lo, c)
    with np.errstate(invalid="ignore"):
        bull = (p["lower"] >= 0.65) & (p["body"] <= body_max)
        bear = (p["upper"] >= 0.65) & (p["body"] <= body_max)
    return np.where(bull, 1, np.where(bear, -1, 0)).astype(int)


def high_volume(b: TfBars) -> np.ndarray:
    """The box indicator's own high-volume candle: tick count > 1.5 x EMA20 of the tick count (signed by the candle)."""
    ve = smc.ema_seeded(np.nan_to_num(b.tick), 20) if b.n else np.zeros(0)
    with np.errstate(invalid="ignore"):
        hv = b.tick > 1.5 * ve
    return np.where(hv & (b.c > b.o), 1, np.where(hv & (b.c < b.o), -1, 0)).astype(int)


# ------------------------------------------------------------------------------------------------------ levels
def next_box(tops: np.ndarray, bots: np.ndarray, price: float, side: int) -> float:
    """Section 3 "Next box": long, the lowest bottom above `price`; short, the highest top below it; NaN if none."""
    if side > 0:
        above = bots[bots > price]
        return float(above.min()) if len(above) else float("nan")
    below = tops[tops < price]
    return float(below.max()) if len(below) else float("nan")


def reward_ratio(entry: float, stop: float, target: float, side: int) -> float:
    """(target - entry) / (entry - stop) for a long, mirrored for a short; NaN when the risk is not positive."""
    risk = side * (entry - stop)
    if not np.isfinite(target) or not risk > 0:
        return float("nan")
    return side * (target - entry) / risk


def placeable(entry: float, stop: float, target: float, spread: float) -> bool:
    """Section 3 "Placeable": target distance >= 2.5 x (bar spread + 0.37 $/oz) and stop distance >= $0.20."""
    cost = (spread if np.isfinite(spread) else 0.0) + COST_PRIOR_USD
    return bool(abs(target - entry) >= MIN_TARGET_OVER_COST * cost and abs(entry - stop) >= MIN_STOP_USD)


def in_golden_zone(close: float, g50: float, g618: float) -> bool:
    """Section 3 "Golden zone": the close lies between the 0.5 and 0.618 retracement of the last 50 bars."""
    if not (np.isfinite(g50) and np.isfinite(g618)):
        return False
    return bool(min(g50, g618) <= close <= max(g50, g618))


def mfi_aligned(mfi: np.ndarray, i: int, side: int, ob: float, os_: float, window: int = 10) -> bool:
    """Section 3 "MFI alignment": over rows i-9..i the most recent row at an extreme (>= ob or <= os) was the
    opposite extreme of the trade (<= os for a long, >= ob for a short). No extreme in the window: neutral, False."""
    lo = max(0, i - window + 1)
    w = mfi[lo:i + 1]
    with np.errstate(invalid="ignore"):
        ext = np.flatnonzero((w >= ob) | (w <= os_))
    if not len(ext):
        return False
    v = w[ext[-1]]
    return bool(v <= os_) if side > 0 else bool(v >= ob)


def interval_gap(lo: float, hi: float, level: float) -> float:
    """Distance from `level` to the interval [lo, hi] (0 inside)."""
    return max(lo - level, level - hi, 0.0)


def session_ln_ny(close_ns: np.ndarray) -> np.ndarray:
    """Section 4.12: the bar closes inside the London or New York window (DEFAULT_SESSIONS, DST-aware)."""
    from goldbot.data.calendar import DEFAULT_SESSIONS
    if not len(close_ns):
        return np.zeros(0, dtype=bool)
    lab = DEFAULT_SESSIONS.session_label(pd.DatetimeIndex(np.asarray(close_ns, dtype=np.int64), tz="UTC"))
    return np.isin(lab, ("london", "newyork"))
