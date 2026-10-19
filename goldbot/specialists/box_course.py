"""Box and price-action course families (hypotheses.md A11; docs/research/box-mfi-families-2026-10-18.md, sections
3-5, written before any count; research director ruling 2026-10-18, section 10 of that file).

Owner, 2026-10-18: his course notes are "baselines, not rules to obey". Each family is one trial running exactly one
pre-registered count-selected rung: "Rungs relax only in the order fixed here ... Counts select the rung; nothing else
does" (section 4). So the only setting of a family is its `rung` (presets "L0".."Ln"); nothing else can be tuned.

| family | hypothesis | decision bars | rungs |
|---|---|---|---|
| `box_break` | H-37 box break, pullback entry (P1 1h boxes / 15m entry; P2 4h boxes / 1h trigger) | 15m | L0-L6 |
| `box_rip` | H-38 rejection at a box (Q1 1h boxes; Q2 4h boxes, 1h candles) | 1h | L0-L6 |
| `box_retest` | H-39 broken level, first rejected retest | 1h | L0-L3 |
| `fakey` | H-44 false break of an inside bar (4h; pooled with 1h from L1) | 1h | L0-L4 |
| `trend_level_signal` | H-45 trend + level + 1h-close signal (course Long A / Short A; B setups from L1) | 1h | L0-L3 |
| `box_course` | H-40 filter discovery: the union of the rungs that run, with the 21 pre-registered flag groups | 1h (15m with `box_break`) | presets |

Shared rules (section 3), implemented once (`goldbot.features.box_course`):

* Point in time: "At a 15m decision bar, 1h/4h values are those of the last bar with `visible_at` <= the 15m bar's
  close; boxes, pivots and MFI are stamped on the bar whose close makes them known." Each rule is evaluated on its own
  timeframe's bars and placed on the decision bar that closes at the same instant (a 4h signal on the 1h bar ending
  with it). Nothing after the signal bar's close is read (tests: appending bars never changes a past candidate).
* Entry at the signal close, as every family's text says, except H-45: "entry at the **next** 1h bar's open"
  (`entry_at_next_open`: the labels fill at the first 1m open after the close, `pipeline._label`).
* Stop: each family's structural stop. R = |signal close - stop| (`barrier_atr`, so `BarrierSpec.stop_atr` is 1).
* Target, **K-T fallback fixed in section 3**: "if the hook is not merged by dispatch, the target is exactly 2R (the
  floor) and the verdict is labelled '2R proxy for next-box target'". The next box must exist and lie >= 2R away
  (the reward floor), but the label's target is 2R. H-45 likewise runs its **K-X fallback** ("TP1-only proxy": 100%
  at the target, no runner, no breakeven step), at the same 2R.
* Time exit per signal timeframe: 96 bars (15m) / 48 (1h) / 24 (4h), carried per candidate (`max_bars`), so pooled
  rungs keep each member's own exit.
* One position at a time in total (`labels.one_at_a_time`); stop first when stop and target fall in one bar
  (`labels.triple_barrier`). Placeable: the engine's own entry gate (target >= 2.5 x (bar spread + 0.37 $/oz), stop >=
  $0.20). Lot fit at 0.01 lot is reported by `research.yml -f lot_fit=true` (H-27 overlay: a stop the minimum lot cannot
  carry is skipped, never upsized); candidates are not filtered by it, so they equal the pre-registered definitions.
* Calendar: candidates on a decision bar inside the tier-1 blackout are dropped when the frame carries `in_blackout`
  (opt-in `calendar_events`); research.yml runs without the calendar, as the section 9 counts did.
* Two signals on one decision bar: one side kept in member order; opposite sides drop both (no rule chooses).

Readings of under-specified text (fixed here, before any count by this code; for the quant reviewer): "farthest box
crossed" is the highest top (long) / lowest bottom (short) crossed; H-37 P2's break is a 1h close through a 4h box
(the header "map 4h, trigger 1h"), so its K3 confirmation is the trigger itself and K2 reads the 4h money flow; a
signal timeframe's targets use that timeframe's boxes (H-39 4h member: next 4h box; H-44 1h member: next 1h box);
H-45's "4h trend" for the B setups is T1; several H-45 levels: the nearest one (highest below a long close); H-44
counter-trend fakeys keep the EMA8/21 alignment with the trade until L3 drops it.

Research only: `screening` keeps every family out of the default founders, the pooled models, the loop grid
(`research.generator._eligible`; GRID_V1 unchanged) and `ops.jobs.research_families`.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import pandas as pd
from pydantic import ValidationError, field_validator

from goldbot.base import FrozenRecord
from goldbot.config import tf_seconds
from goldbot.data.resample import mid
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features import box_course as bc
from goldbot.features import smc_replica as smc
from goldbot.features.mfi_signals import COLUMNS as MFI_COLUMNS
from goldbot.features.mfi_signals import mfi_signals
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.base import TIMEFRAME_KEY, AgentIdentity, Specialist, register
from goldbot.specialists.smc_structure import SmcStructureSpecialist

HOLD_BARS = {"15m": 96, "1h": 48, "4h": 24}       # section 3 "Exits": time exit per entry timeframe
TARGET_R = 2.0                                    # K-T fallback: "the target is exactly 2R (the floor)"
REWARD_FLOOR = 2.0
BUFFER_ATR = 0.1                                  # every structural stop's buffer, x ATR of the stated timeframe
CTX_TF = {"h1": "1h", "h4": "4h", "d1": "1d"}
BREAK_DEPTH = 0.2                                 # section 3 "Break": d = 0.2 beyond the far edge
MFI_EXTREMES = {"80/20": (80.0, 20.0), "70/30": (70.0, 30.0)}


class Event(FrozenRecord):
    """One signal on its own timeframe, before it is placed on a decision bar."""

    close_ns: int                 # the signal bar's close (when it is known)
    tf: str                       # the signal (entry) timeframe
    i: int                        # row of the signal bar in that timeframe
    side: int
    entry: float                  # the signal close
    stop: float
    target: float                 # the next box (reward floor checked against it)
    member: str
    lo: float                     # the level or box the rule acted at (for `major_level`)
    hi: float
    info: dict[str, float] = {}   # family facts the H-40 flags read (depth, volume, anchor, confluence count)

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)


# --------------------------------------------------------------------------------------------- the bars of a call
class Course:
    """Every timeframe a rule reads, built once from the decision bars and the context (store schema, `visible_at`),
    with the derived per-row arrays the families share. The daily bars drop the Friday stubs as tsmom does."""

    def __init__(self, m: pd.DataFrame, dec_tf: str, context: dict[str, pd.DataFrame] | None,
                 box_tfs: Sequence[str]) -> None:
        from goldbot.specialists.time_series_momentum import drop_stub_days
        frames: dict[str, pd.DataFrame] = {dec_tf: m.reset_index(drop=True)}
        for label, bars in (context or {}).items():
            tf = CTX_TF.get(label)
            if tf is None or tf == dec_tf:
                continue
            bars = bars.reset_index(drop=True)
            frames[tf] = mid(drop_stub_days(bars) if tf == "1d" else bars)
        missing = [tf for tf in ("1h", "4h", "1d") if tf not in frames]
        if missing:
            raise ValueError(f"the course families need the {', '.join(missing)} bars (context h1/h4/d1)")
        self.dec_tf = dec_tf
        self.b = {tf: bc.TfBars(f, tf, with_boxes=tf in box_tfs) for tf, f in frames.items()}
        d, h4 = self.b["1d"], self.b["4h"]
        self.d_trend = bc.structure_trend(d.h, d.l, 3)
        trend4 = bc.structure_trend(h4.h, h4.l, 3)
        e10, e20 = h4.ema(10), h4.ema(20)
        with np.errstate(invalid="ignore"):
            self.t1 = np.where((trend4 == 1) & (h4.c > e10) & (h4.c > e20), 1,
                               np.where((trend4 == -1) & (h4.c < e10) & (h4.c < e20), -1, 0))
        self.pp, self.r1, self.s1 = bc.floor_pivots(d)
        self.hi50 = {tf: bc.rolling(b.h, 50, "max") for tf, b in self.b.items()}
        self.lo50 = {tf: bc.rolling(b.l, 50, "min") for tf, b in self.b.items()}
        self.golden = {tf: smc.range_retracement(b.h, b.l, 50) for tf, b in self.b.items()}

    def row(self, tf: str, close_ns: int) -> int:
        return int(bc.visible(close_ns, self.b[tf])[0])

    def d0(self, close_ns: int) -> int:
        """Section 3 D0: the daily trend visible at `close_ns` (+1 HH+HL, -1 LH+LL, 0 range)."""
        j = self.row("1d", close_ns)
        return int(self.d_trend[j]) if j >= 0 else 0

    def range_extreme(self, close_ns: int, price: float, side: int) -> bool:
        """Section 3 (note 5, "in a range, trade only the extremes"): within the outer 20% of the last 20 feature days'
        high-low range, the top for a short, the bottom for a long."""
        j = self.row("1d", close_ns)
        if j < 19:
            return False
        d = self.b["1d"]
        hi, lo = float(d.h[j - 19:j + 1].max()), float(d.l[j - 19:j + 1].min())
        if not hi > lo:
            return False
        pos = (price - lo) / (hi - lo)
        return pos >= 0.8 if side < 0 else pos <= 0.2

    def major(self, lo: float, hi: float, close_ns: int) -> bool:
        """Section 5 `major_level`: the level overlaps a 4h box, or lies within 0.25 ATR1h of the previous day's floor
        pivot R1/S1 or of the 50-bar 1h extreme (all as visible at `close_ns`)."""
        j4, j1, jd = self.row("4h", close_ns), self.row("1h", close_ns), self.row("1d", close_ns)
        tops, bots = self.b["4h"].box_set(j4)
        if len(tops) and bool(((bots <= hi) & (tops >= lo)).any()):
            return True
        if j1 < 0:
            return False
        tol = 0.25 * float(self.b["1h"].atr[j1])
        levels = [self.hi50["1h"][j1], self.lo50["1h"][j1]]
        if jd >= 0:
            levels += [self.r1[jd], self.s1[jd]]
        return any(np.isfinite(x) and bc.interval_gap(lo, hi, float(x)) <= tol for x in levels)

    def boxes_at(self, box_tf: str, close_ns: int) -> tuple[np.ndarray, np.ndarray]:
        return self.b[box_tf].box_set(self.row(box_tf, close_ns))

    def golden_zone(self, tf: str, close_ns: int, price: float) -> bool:
        j = self.row(tf, close_ns)
        if j < 0:
            return False
        g50, g618 = self.golden[tf]
        return bc.in_golden_zone(price, float(g50[j]), float(g618[j]))


def _finish(c: Course, out: list[Event], *, tf: str, i: int, side: int, stop: float, target: float, member: str,
            lo: float, hi: float, info: dict[str, float] | None = None, floor: float = REWARD_FLOOR) -> bool:
    """The checks every family ends with (reward floor against the next box, placeable); appends the event."""
    b = c.b[tf]
    entry = float(b.c[i])
    rr = bc.reward_ratio(entry, stop, target, side)
    if not (np.isfinite(rr) and rr >= floor and bc.placeable(entry, stop, target, float(b.spread[i]))):
        return False
    out.append(Event(close_ns=int(b.close_ns[i]), tf=tf, i=i, side=side, entry=entry, stop=float(stop),
                     target=float(target), member=member, lo=float(lo), hi=float(hi),
                     info={"rr": float(rr), **(info or {})}))
    return True


def _breaks(c_prev: float, c_now: float, o_now: float, tops: np.ndarray, bots: np.ndarray, heff: np.ndarray,
            depth: float) -> tuple[int, int]:
    """Section 3 "Break" on one bar: (side, box index) of the farthest box crossed with the body by `depth` box heights
    beyond the far edge, (0, -1) for none."""
    if not len(tops):
        return 0, -1
    with np.errstate(invalid="ignore"):
        up = (c_prev <= tops) & (c_now >= tops + depth * heff) & (c_now > o_now)
        dn = (c_prev >= bots) & (c_now <= bots - depth * heff) & (c_now < o_now)
    if up.any():
        return 1, int(np.argmax(np.where(up, tops, -np.inf)))
    if dn.any():
        return -1, int(np.argmin(np.where(dn, bots, np.inf)))
    return 0, -1


# ------------------------------------------------------------------------------------------------ H-39 box_retest
RETEST_RUNGS: dict[str, tuple[tuple[str, ...], int, bool]] = {    # box timeframes, window (1h bars), D0
    "L0": (("1h",), 24, True), "L1": (("1h", "4h"), 24, True), "L2": (("1h", "4h"), 48, True),
    "L3": (("1h", "4h"), 48, False)}


def scan_retest(c: Course, rung: str) -> list[Event]:
    """Section 4.3, long (short mirrored): a 1h close beyond the top of box k at bar s; within the next `window` 1h bars
    the first bar t with low <= T_k, close > T_k and bullish, with no close below B_k between s and t. Entry close[t];
    stop low[t] - 0.1 ATR1h; target the next box of the box timeframe; one candidate per break."""
    srcs, window, daily = RETEST_RUNGS[rung]
    b = c.b["1h"]
    out: list[Event] = []
    for src in srcs:
        for s in range(1, b.n):
            tops, bots = c.boxes_at(src, int(b.close_ns[s]))
            if not len(tops):
                continue
            up = (b.c[s - 1] <= tops) & (tops < b.c[s])
            dn = (b.c[s - 1] >= bots) & (bots > b.c[s])
            if up.any():
                k, side = int(np.argmax(np.where(up, tops, -np.inf))), 1
            elif dn.any():
                k, side = int(np.argmin(np.where(dn, bots, np.inf))), -1
            else:
                continue
            top, bot = float(tops[k]), float(bots[k])
            for t in range(s + 1, min(s + window, b.n - 1) + 1):
                if (side > 0 and b.c[t] < bot) or (side < 0 and b.c[t] > top):
                    break                                   # the broken level failed before any retest
                if side > 0:
                    hit = b.l[t] <= top and b.c[t] > top and b.c[t] > b.o[t]
                else:
                    hit = b.h[t] >= bot and b.c[t] < bot and b.c[t] < b.o[t]
                if not hit:
                    continue
                close = int(b.close_ns[t])
                if daily and c.d0(close) != side:
                    break
                stop = b.l[t] - BUFFER_ATR * b.atr[t] if side > 0 else b.h[t] + BUFFER_ATR * b.atr[t]
                tt, bb = c.boxes_at(src, close)
                level = top if side > 0 else bot
                _finish(c, out, tf="1h", i=t, side=side, stop=stop, target=bc.next_box(tt, bb, float(b.c[t]), side),
                        member=f"box_retest:{rung}:{src}", lo=bot, hi=top, info={"level": level})
                break
    return out


# --------------------------------------------------------------------------------------------------- H-38 box_rip
RIP_RUNGS: dict[str, dict[str, Any]] = {
    "L0": {"srcs": ("1h",), "ext": "80/20", "counter": False, "golden": True, "mfi": True, "daily": True},
    "L1": {"srcs": ("1h", "4h"), "ext": "80/20", "counter": False, "golden": True, "mfi": True, "daily": True},
    "L2": {"srcs": ("1h", "4h"), "ext": "80/20", "counter": True, "golden": True, "mfi": True, "daily": True},
    "L3": {"srcs": ("1h", "4h"), "ext": "70/30", "counter": True, "golden": True, "mfi": True, "daily": True},
    "L4": {"srcs": ("1h", "4h"), "ext": "70/30", "counter": True, "golden": False, "mfi": True, "daily": True},
    "L5": {"srcs": ("1h", "4h"), "ext": "70/30", "counter": True, "golden": False, "mfi": False, "daily": True},
    "L6": {"srcs": ("1h", "4h"), "ext": "70/30", "counter": True, "golden": False, "mfi": False, "daily": False}}


def _rip_mfi(c: Course, src: str, t: int, side: int, ob: float, os_: float) -> bool:
    """J3: Q1, the 1h MFI at the extreme on any of t-2..t or the last visible 4h MFI there; Q2, the 4h or the daily MFI
    (short: >= ob; long: <= os). A year without tick counts has no money flow (section 3)."""
    b = c.b["1h"]
    close = int(b.close_ns[t])

    def at(tf: str, rows: Sequence[int]) -> bool:
        x = c.b[tf]
        ok = [j for j in rows if j >= 0 and x.mfi_ok[j]]
        with np.errstate(invalid="ignore"):
            return any((x.mfi[j] >= ob) if side < 0 else (x.mfi[j] <= os_) for j in ok)
    if src == "1h":
        return at("1h", range(t - 2, t + 1)) or at("4h", [c.row("4h", close)])
    return at("4h", [c.row("4h", close)]) or at("1d", [c.row("1d", close)])


def scan_rip(c: Course, rung: str) -> list[Event]:
    """Section 4.2, short (long mirrored): bar t-1 bullish touches box k from below without a close through
    (close[t-2] < B_k, high[t-1] >= B_k, close[t-1] < T_k); bar t bearish closes back below B_k; J3 money flow at the
    extreme; stop max(high[t-1], high[t]) + 0.1 ATR1h; target the next box below; not in the golden zone; J0 the daily
    rule (with-trend fade, or the range-extreme rule; from L2 also counter-trend fades at a major level)."""
    r = RIP_RUNGS[rung]
    ob, os_ = MFI_EXTREMES[r["ext"]]
    b = c.b["1h"]
    hv = bc.high_volume(b)
    out: list[Event] = []
    for src in r["srcs"]:
        for t in range(2, b.n):
            tops, bots = c.boxes_at(src, int(b.close_ns[t - 1]))
            if not len(tops):
                continue
            side, k = 0, -1
            with np.errstate(invalid="ignore"):
                sh = (b.c[t - 1] > b.o[t - 1]) & (b.c[t - 2] < bots) & (b.h[t - 1] >= bots) & (b.c[t - 1] < tops)
                lg = (b.c[t - 1] < b.o[t - 1]) & (b.c[t - 2] > tops) & (b.l[t - 1] <= tops) & (b.c[t - 1] > bots)
            if sh.any():
                k = int(np.argmin(np.where(sh, bots, np.inf)))
                if b.c[t] < b.o[t] and b.c[t] < bots[k]:
                    side = -1
            if side == 0 and lg.any():
                k = int(np.argmax(np.where(lg, tops, -np.inf)))
                if b.c[t] > b.o[t] and b.c[t] > tops[k]:
                    side = 1
            if side == 0:
                continue
            top, bot = float(tops[k]), float(bots[k])
            close = int(b.close_ns[t])
            if r["mfi"] and not _rip_mfi(c, src, t, side, ob, os_):
                continue
            if r["golden"] and c.golden_zone(src, close, float(b.c[t])):
                continue
            if r["daily"]:
                d = c.d0(close)
                with_trend = d == side or (d == 0 and c.range_extreme(close, float(b.c[t]), side))
                counter = r["counter"] and d == -side and c.major(bot, top, close)
                if not (with_trend or counter):
                    continue
            stop = (max(b.h[t - 1], b.h[t]) + BUFFER_ATR * b.atr[t] if side < 0
                    else min(b.l[t - 1], b.l[t]) - BUFFER_ATR * b.atr[t])
            tt, bb = c.boxes_at(src, close)
            approach = hv[max(0, t - 4):t]                   # the 3 approach bars before t-1 and t-1 itself
            _finish(c, out, tf="1h", i=t, side=side, stop=stop, target=bc.next_box(tt, bb, float(b.c[t]), side),
                    member=f"box_rip:{rung}:{src}", lo=bot, hi=top,
                    info={"vol": float(not (approach == -side).any())})
    return out


# ------------------------------------------------------------------------------------------------------ H-44 fakey
FAKEY_RUNGS: dict[str, dict[str, Any]] = {
    "L0": {"tfs": ("4h",), "counter": False, "ema": True, "daily": True},
    "L1": {"tfs": ("4h", "1h"), "counter": False, "ema": True, "daily": True},
    "L2": {"tfs": ("4h", "1h"), "counter": True, "ema": True, "daily": True},
    "L3": {"tfs": ("4h", "1h"), "counter": True, "ema": False, "daily": True},
    "L4": {"tfs": ("4h", "1h"), "counter": True, "ema": False, "daily": False}}


def scan_fakey(c: Course, rung: str) -> list[Event]:
    """Section 4.8, long (short mirrored): an inside bar at t-k (k = 1..3) with mother m = t-k-1, no close outside the
    mother range between them, and bar t false-breaks below the mother (low[t] < low[m]) and closes back above it; F0
    daily trend with the trade and EMA8 > EMA21 on the signal timeframe; stop low[t] - 0.1 ATR; target the next box
    edge of the signal timeframe; entry at the close of bar t. L2 adds counter-trend fakeys (range >= 1.5 ATR, a major
    level in the mother range, the inside bar not sitting on EMA20)."""
    r = FAKEY_RUNGS[rung]
    out: list[Event] = []
    for tf in r["tfs"]:
        x = c.b[tf]
        e8, e20, e21 = x.ema(8), x.ema(20), x.ema(21)
        for t in range(2, x.n):
            found = None
            for k in (1, 2, 3):
                ib, m = t - k, t - k - 1
                if m < 0:
                    break
                if not (x.h[ib] <= x.h[m] and x.l[ib] >= x.l[m]):
                    continue
                between = x.c[ib + 1:t]
                if ((between < x.l[m]) | (between > x.h[m])).any():
                    continue
                lg = x.l[t] < x.l[m] and x.c[t] > x.l[m]
                sh = x.h[t] > x.h[m] and x.c[t] < x.h[m]
                if lg or sh:                         # the nearest mother that bar t false-breaks decides
                    found = (1 if lg else -1, ib, m) if lg != sh else None
                    break
            if found is None:
                continue
            side, ib, m = found
            close = int(x.close_ns[t])
            ema_ok = (not r["ema"]) or side * (e8[t] - e21[t]) > 0
            if r["daily"]:
                d = c.d0(close)
                trend = d == side and ema_ok
                counter = (r["counter"] and d == -side and ema_ok and x.h[t] - x.l[t] >= 1.5 * x.atr[t]
                           and c.major(float(x.l[m]), float(x.h[m]), close)
                           and not (x.l[ib] <= e20[ib] <= x.h[ib]))
                if not (trend or counter):
                    continue
            elif not ema_ok:
                continue
            stop = x.l[t] - BUFFER_ATR * x.atr[t] if side > 0 else x.h[t] + BUFFER_ATR * x.atr[t]
            tops, bots = x.box_set(t)
            _finish(c, out, tf=tf, i=t, side=side, stop=stop, target=bc.next_box(tops, bots, float(x.c[t]), side),
                    member=f"fakey:{rung}:{tf}", lo=float(x.l[m]), hi=float(x.h[m]))
    return out


# ----------------------------------------------------------------------------------------- H-45 trend_level_signal
TLS_RUNGS: dict[str, dict[str, bool]] = {
    "L0": {"b": False, "mfi": True, "daily": True}, "L1": {"b": True, "mfi": True, "daily": True},
    "L2": {"b": True, "mfi": False, "daily": True}, "L3": {"b": True, "mfi": False, "daily": False}}
LONG_SIGNALS = ("hammer", "bull_engulf", "piercing", "morning_star", "inv_hammer")
SHORT_SIGNALS = ("shooting_star", "bear_engulf", "dark_cloud", "evening_star")
BROKEN_WINDOW = 48                                # "a box broken ... in the last 48 1h bars"


class _Level(FrozenRecord):
    level: float
    zone_lo: float
    zone_hi: float
    kind: str                                     # box1h | box4h | broken | ema20 | half


def _broken_boxes(c: Course) -> list[list[tuple[int, float, float]]]:
    """Per 1h row s, every 1h or (visible) 4h box a 1h close crossed at s: (side, top, bottom)."""
    b = c.b["1h"]
    out: list[list[tuple[int, float, float]]] = [[] for _ in range(b.n)]
    for s in range(1, b.n):
        for src in ("1h", "4h"):
            tops, bots = c.boxes_at(src, int(b.close_ns[s]))
            for tp, bt in zip(tops, bots):
                if b.c[s - 1] <= tp < b.c[s]:
                    out[s].append((1, float(tp), float(bt)))
                elif b.c[s - 1] >= bt > b.c[s]:
                    out[s].append((-1, float(tp), float(bt)))
    return out


def _tls_levels(c: Course, t: int, side: int, broken: list[list[tuple[int, float, float]]]) -> list[_Level]:
    """L1's key levels at 1h bar t (long: box tops below the prior close; short mirrored), nearest first."""
    b = c.b["1h"]
    close = int(b.close_ns[t])
    out: list[_Level] = []
    for src in ("1h", "4h"):
        tops, bots = c.boxes_at(src, close)
        for tp, bt in zip(tops, bots):
            if side > 0 and tp < b.c[t - 1]:
                out.append(_Level(level=float(tp), zone_lo=float(bt), zone_hi=float(tp), kind=f"box{src}"))
            elif side < 0 and bt > b.c[t - 1]:
                out.append(_Level(level=float(bt), zone_lo=float(bt), zone_hi=float(tp), kind=f"box{src}"))
    for s in range(max(1, t - BROKEN_WINDOW), t):
        for sd, tp, bt in broken[s]:
            if sd == side:
                out.append(_Level(level=tp if side > 0 else bt, zone_lo=bt, zone_hi=tp, kind="broken"))
    for kind, val in (("ema20", b.ema(20)[t]), ("half", c.golden["1h"][0][t])):
        if np.isfinite(val):
            out.append(_Level(level=float(val), zone_lo=float(val), zone_hi=float(val), kind=kind))
    return sorted(out, key=lambda x: -side * x.level)           # nearest to the price first, stable for ties


def _touches(b: bc.TfBars, t: int, side: int, lv: float) -> bool:
    """L1: the bar touches the level from the trade's side and closes on it or beyond (0.1 ATR1h tolerance)."""
    tol = BUFFER_ATR * b.atr[t]
    return bool(b.l[t] <= lv + tol and b.c[t] >= lv) if side > 0 else bool(b.h[t] >= lv - tol and b.c[t] <= lv)


def _tls_count(c: Course, t: int, side: int, lv: _Level, signal: str) -> int:
    """Section 4.0's confluence count (12 factors; event area and trendline are not built and count 0)."""
    b = c.b["1h"]
    close = int(b.close_ns[t])
    j4, jd = c.row("4h", close), c.row("1d", close)
    tol = 0.25 * b.atr[t]
    pivots = [float(x) for x in ((c.r1[jd], c.s1[jd], c.pp[jd]) if jd >= 0 else ()) if np.isfinite(x)]
    w1 = b.mfi[max(0, t - 4):t + 1]
    w4 = c.b["4h"].mfi[max(0, j4 - 4):j4 + 1] if j4 >= 0 else np.zeros(0)
    with np.errstate(invalid="ignore"):
        extreme = (bool(((w1 <= 20).any() or (w4 <= 20).any())) if side > 0
                   else bool(((w1 >= 80).any() or (w4 >= 80).any())))
    vol_down = t >= 13 and bool(np.nanmean(b.tick[t - 3:t]) < np.nanmean(b.tick[t - 13:t - 3]))
    off_line = (b.l[t] < lv.level < b.c[t]) if side > 0 else (b.h[t] > lv.level > b.c[t])
    pin = int(bc.pin_bar(b.o[t:t + 1], b.h[t:t + 1], b.l[t:t + 1], b.c[t:t + 1])[0]) == side
    factors = [
        j4 >= 0 and c.t1[j4] == side,
        lv.kind in ("box1h", "box4h", "broken"),
        lv.kind == "box4h" or any(abs(lv.level - p) <= tol for p in pivots),
        abs(b.ema(20)[t] - lv.level) <= tol,
        abs(c.golden["1h"][0][t] - lv.level) <= tol,
        pin or signal in ("bull_engulf", "bear_engulf"),
        extreme,
        lv.kind == "broken",
        False,                                                 # event area: not built (section 4.0)
        vol_down,
        bool(off_line),
        False,                                                 # trendline touch: not built (section 4.0)
    ]
    return int(sum(bool(f) for f in factors))


def _tls_money_flow(c: Course, t: int, j4: int, side: int) -> tuple[bool, bool]:
    """(M1 holds, X's money-flow invalidation holds) at 1h bar t with the visible 4h row j4."""
    b, h4 = c.b["1h"], c.b["4h"]
    m1, m4 = b.mfi, h4.mfi
    with np.errstate(invalid="ignore"):
        if side > 0:
            ok = bool((b.mfi_ok[t] and (m1[t - 4:t + 1] < 40).any() and m1[t] > m1[t - 1])
                      or (h4.mfi_ok[j4] and (m4[max(0, j4 - 4):j4 + 1] < 40).any() and m4[j4] > m4[j4 - 1])
                      or (b.mfi_ok[t] and m1[t - 1] <= 50 < m1[t]))
            return ok, bool(m1[t] > 60)
        ok = bool(b.mfi_ok[t] and (m1[t - 4:t + 1] >= 60).any() and m1[t] < m1[t - 1])
        return ok, bool(m4[j4] < 30)


def _tls_a(c: Course, t: int, side: int, rung: str, sig: dict[str, np.ndarray], parts: dict[str, np.ndarray],
           broken: list[list[tuple[int, float, float]]], out: list[Event]) -> bool:
    """Long A / Short A at 1h bar t; True when a candidate was added."""
    r = TLS_RUNGS[rung]
    b = c.b["1h"]
    close = int(b.close_ns[t])
    j4 = c.row("4h", close)
    if c.t1[j4] != side or (r["daily"] and c.d0(close) != side):
        return False
    hi50, lo50 = c.hi50["1h"][t], c.lo50["1h"][t]
    if hi50 > lo50 and 0.4 <= (b.c[t] - lo50) / (hi50 - lo50) <= 0.6:
        return False                                             # X: mid-range
    m_ok, invalid = _tls_money_flow(c, t, j4, side)
    if invalid or (r["mfi"] and not m_ok):
        return False
    lv = next((x for x in _tls_levels(c, t, side, broken) if _touches(b, t, side, x.level)), None)
    if lv is None:
        return False
    signal = next((n for n in (LONG_SIGNALS if side > 0 else SHORT_SIGNALS) if sig[n][t]), None)
    if side < 0 and signal is None:
        if b.h[t] > lv.level + BUFFER_ATR * b.atr[t] and b.c[t] < lv.level:
            signal = "false_break"
        elif parts["upper"][t] >= 0.5 and max(b.o[t], b.c[t]) <= lv.level < b.h[t]:
            signal = "wick_off_line"
    if signal is None:
        return False
    stop = (min(b.l[t], lv.zone_lo) - BUFFER_ATR * b.atr[t] if side > 0
            else max(b.h[t], lv.zone_hi) + BUFFER_ATR * b.atr[t])
    tops, bots = b.box_set(t)
    return _finish(c, out, tf="1h", i=t, side=side, stop=stop, target=bc.next_box(tops, bots, float(b.c[t]), side),
                   member=f"trend_level_signal:{rung}:A", lo=lv.zone_lo, hi=lv.zone_hi,
                   info={"tlv_count": float(_tls_count(c, t, side, lv, signal))})


def _tls_b(c: Course, t: int, side: int, rung: str, sig: dict[str, np.ndarray], parts: dict[str, np.ndarray],
           broken: list[list[tuple[int, float, float]]], cross20: np.ndarray, out: list[Event]) -> bool:
    """Long B (counter-trend, long only) / Short B (false break at major resistance, short only), both with the 4h
    trend down (T1), exactly as the course writes them; True when a candidate was added."""
    b, h4 = c.b["1h"], c.b["4h"]
    close = int(b.close_ns[t])
    j4 = c.row("4h", close)
    if c.t1[j4] != -1:
        return False
    tops, bots = b.box_set(t)
    if side > 0:
        with np.errstate(invalid="ignore"):
            ok = bool(h4.mfi[j4] < 30 and cross20[max(0, j4 - 11):j4 + 1].sum() >= 2 and b.mfi[t] < 30
                      and b.mfi_ok[t] and h4.mfi_ok[j4] and sig["hammer"][t])
        if not ok:
            return False
        lv = next((x for x in _tls_levels(c, t, 1, broken)
                   if b.l[t] < x.level < b.c[t] and c.major(x.zone_lo, x.zone_hi, close)), None)
        if lv is None:
            return False
        return _finish(c, out, tf="1h", i=t, side=1, stop=min(b.l[t], lv.zone_lo) - BUFFER_ATR * b.atr[t],
                       target=bc.next_box(tops, bots, float(b.c[t]), 1), member=f"trend_level_signal:{rung}:B",
                       lo=lv.zone_lo, hi=lv.zone_hi, info={"tlv_count": float(_tls_count(c, t, 1, lv, "hammer"))})
    if not parts["upper"][t] >= 0.5:
        return False
    for src in ("1h", "4h"):
        tt, bb = c.boxes_at(src, close)
        hit = [(float(x), float(y)) for x, y in zip(tt, bb)
               if b.h[t] > x and b.c[t] < y and c.major(float(y), float(x), close)]
        if hit:
            tp, bt = min(hit, key=lambda z: z[1])
            lv = _Level(level=bt, zone_lo=bt, zone_hi=tp, kind=f"box{src}")
            return _finish(c, out, tf="1h", i=t, side=-1, stop=b.h[t] + BUFFER_ATR * b.atr[t],
                           target=bc.next_box(tops, bots, float(b.c[t]), -1), member=f"trend_level_signal:{rung}:B",
                           lo=bt, hi=tp, info={"tlv_count": float(_tls_count(c, t, -1, lv, "false_break"))})
    return False


def scan_tls(c: Course, rung: str) -> list[Event]:
    """Section 4.0: Long A / Short A (T1 4h trend, T2 daily trend, L1 touch of a key level, S1 1h signal candle, M1
    money flow, X invalidations; stop beyond the wick and the zone; TP1 the next 1h box, reward >= 2), plus Long B /
    Short B from L1; L2 drops M1, L3 drops T2. Entry at the next 1h open (`entry_at_next_open`). One candidate per bar:
    A before B, long before short."""
    b, h4 = c.b["1h"], c.b["4h"]
    sig = bc.course_signals(b.o, b.h, b.l, b.c)
    parts = bc.candle_parts(b.o, b.h, b.l, b.c)
    broken = _broken_boxes(c)
    cross20 = smc.cross_under(h4.mfi, np.full(h4.n, 20.0))
    out: list[Event] = []
    for t in range(5, b.n):
        if c.row("4h", int(b.close_ns[t])) < 1:
            continue
        if _tls_a(c, t, 1, rung, sig, parts, broken, out) or _tls_a(c, t, -1, rung, sig, parts, broken, out):
            continue
        if TLS_RUNGS[rung]["b"]:
            _ = (_tls_b(c, t, 1, rung, sig, parts, broken, cross20, out)
                 or _tls_b(c, t, -1, rung, sig, parts, broken, cross20, out))
    return out


# --------------------------------------------------------------------------------------------------- H-37 box_break
BREAK_RUNGS: dict[str, dict[str, Any]] = {
    "L0": {"pairs": ("P1",), "ext": "80/20", "golden": True, "confirm": True, "mfi": True, "daily": True},
    "L1": {"pairs": ("P1", "P2"), "ext": "80/20", "golden": True, "confirm": True, "mfi": True, "daily": True},
    "L2": {"pairs": ("P1", "P2"), "ext": "70/30", "golden": True, "confirm": True, "mfi": True, "daily": True},
    "L3": {"pairs": ("P1", "P2"), "ext": "70/30", "golden": False, "confirm": True, "mfi": True, "daily": True},
    "L4": {"pairs": ("P1", "P2"), "ext": "70/30", "golden": False, "confirm": False, "mfi": True, "daily": True},
    "L5": {"pairs": ("P1", "P2"), "ext": "70/30", "golden": False, "confirm": False, "mfi": False, "daily": True},
    "L6": {"pairs": ("P1", "P2"), "ext": "70/30", "golden": False, "confirm": False, "mfi": False, "daily": False}}
# map (box) timeframe, entry timeframe, pullback window in entry bars
PAIRS: dict[str, dict[str, Any]] = {"P1": {"map": "1h", "entry": "15m", "window": 16},
                                    "P2": {"map": "4h", "entry": "1h", "window": 8}}
CONFIRM_BEFORE_S, CONFIRM_AFTER_S = 3600, 4 * 3600   # K3: a 15m bar closing in (T - 1h, T + 4h]


def _confirm_15m(c: Course, close_1h: int, side: int, ob: float, os_: float, use_mfi: bool) -> int:
    """K3: the arming time, the later of T and the close of the earliest 15m break the same way (15m boxes, d = 0.2)
    closing in (T - 1h, T + 4h] with the 15m money flow aligned there; -1 for none."""
    q = c.b["15m"]
    lo = int(np.searchsorted(q.close_ns, close_1h - CONFIRM_BEFORE_S * bc.NS, side="right"))
    hi = int(np.searchsorted(q.close_ns, close_1h + CONFIRM_AFTER_S * bc.NS, side="right"))
    for j in range(max(lo, 1), hi):
        tops, bots = q.box_set(j)
        heff = np.maximum(tops - bots, 0.1 * q.atr[j])
        sd, _ = _breaks(q.c[j - 1], q.c[j], q.o[j], tops, bots, heff, BREAK_DEPTH)
        if sd != side:
            continue
        if use_mfi and not (q.mfi_ok[j] and bc.mfi_aligned(q.mfi, j, side, ob, os_)):
            continue
        return max(close_1h, int(q.close_ns[j]))
    return -1


def scan_break(c: Course, rung: str) -> list[Event]:
    """Section 4.1, long (short mirrored): K1 a 1h close breaks box k of the map timeframe by 0.2 box heights beyond the
    far edge with the body; K2 the map timeframe's money flow came from the opposite extreme; K3 (P1) a 15m break the
    same way with aligned 15m money flow closing in (T - 1h, T + 4h]; K4 armed at the later of the two, the first entry
    bar within the window whose low reaches P = min(T_k, EMA20) and closes above it, entry at its close; K5 stop B_k -
    0.1 ATR of the entry timeframe; K6 the next box of the map timeframe, reward >= 2; K7 not in the map timeframe's
    golden zone; K0 the daily rule. One candidate per break (the first pullback only)."""
    r = BREAK_RUNGS[rung]
    ob, os_ = MFI_EXTREMES[r["ext"]]
    trig = c.b["1h"]
    hv = bc.high_volume(trig)
    out: list[Event] = []
    for pair in r["pairs"]:
        p = PAIRS[pair]
        mp, en = c.b[p["map"]], c.b[p["entry"]]
        ema20 = en.ema(20)
        for s in range(1, trig.n):
            close_s = int(trig.close_ns[s])
            jm = c.row(p["map"], close_s)
            tops, bots = mp.box_set(jm)
            if not len(tops):
                continue
            heff = np.maximum(tops - bots, 0.1 * mp.atr[jm])
            side, k = _breaks(trig.c[s - 1], trig.c[s], trig.o[s], tops, bots, heff, BREAK_DEPTH)
            if side == 0:
                continue
            top, bot = float(tops[k]), float(bots[k])
            if r["mfi"] and not (mp.mfi_ok[jm] and bc.mfi_aligned(mp.mfi, jm, side, ob, os_)):
                continue
            armed = close_s
            if r["confirm"] and pair == "P1":
                armed = _confirm_15m(c, close_s, side, ob, os_, use_mfi=r["mfi"])
                if armed < 0:
                    continue
            a = int(bc.visible(armed, en)[0])
            for t in range(a + 1, min(a + p["window"], en.n - 1) + 1):
                anchor = np.fmin(top, ema20[t]) if side > 0 else np.fmax(bot, ema20[t])
                if side > 0 and not (en.l[t] <= anchor < en.c[t]):
                    continue
                if side < 0 and not (en.h[t] >= anchor > en.c[t]):
                    continue
                close_t = int(en.close_ns[t])
                stop = bot - BUFFER_ATR * en.atr[t] if side > 0 else top + BUFFER_ATR * en.atr[t]
                ok = side * (en.c[t] - stop) > 0
                ok = ok and not (r["golden"] and c.golden_zone(p["map"], close_t, float(en.c[t])))
                ok = ok and not (r["daily"] and c.d0(close_t) != side)
                if ok:
                    tt, bb = c.boxes_at(p["map"], close_t)
                    depth = (trig.c[s] - top) / heff[k] if side > 0 else (bot - trig.c[s]) / heff[k]
                    touched = en.l[t] <= top if side > 0 else en.h[t] >= bot
                    _finish(c, out, tf=p["entry"], i=t, side=side, stop=stop,
                            target=bc.next_box(tt, bb, float(en.c[t]), side), member=f"box_break:{rung}:{pair}",
                            lo=bot, hi=top, info={"depth": float(depth), "vol": float(hv[s] == side),
                                                  "anchor": float(touched)})
                break                                            # the first pullback only
    return out


SCANNERS: dict[str, Callable[[Course, str], list[Event]]] = {
    "box_break": scan_break, "box_rip": scan_rip, "box_retest": scan_retest, "fakey": scan_fakey,
    "trend_level_signal": scan_tls}
RUNGS: dict[str, tuple[str, ...]] = {"box_break": tuple(BREAK_RUNGS), "box_rip": tuple(RIP_RUNGS),
                                     "box_retest": tuple(RETEST_RUNGS), "fakey": tuple(FAKEY_RUNGS),
                                     "trend_level_signal": tuple(TLS_RUNGS)}
DECISION_TF = {"box_break": "15m", "box_rip": "1h", "box_retest": "1h", "fakey": "1h", "trend_level_signal": "1h"}


def box_timeframes(family: str, rung: str) -> set[str]:
    """The timeframes whose boxes a rung reads (the channel trace is the costly part, so only these are traced)."""
    if family == "box_break" and BREAK_RUNGS[rung]["confirm"]:
        return {"1h", "4h", "15m"}
    return {"1h", "4h"}


# ------------------------------------------------------------------------------------- H-40 flags (sections 4-5)
# column -> group; 21 groups (section 5.1: "K_eff = groups screened (21)"). A flag is 1/0 for the candidate's own
# side, NaN where the pre-registration does not apply it to the candidate's family ("Applies to", section 5).
FLAG_GROUPS: dict[str, str] = {
    "trend_with": "trend_with", "major_level": "major_level", "vol_flag": "volume", "depth_30": "depth_30",
    "rr_3": "rr_3", "day_bias": "day_bias", "premium_discount": "premium_discount", "engulf": "engulf",
    "anchor_box": "anchor_box", "fib50_confluence": "fib50_confluence", "pin_ema20": "pin_ema20",
    "pin_retest": "pin_retest", "pin_inside": "pin_inside", "ema8_21": "ema8_21", "tlv_count": "tlv_count",
    "daily_with": "daily_with", "session_ln_ny": "session_ln_ny", "chop": "chop", "stretch": "stretch",
    "atr_expansion": "atr_expansion"}
MFI_GROUP = "mfi_signals"
BX, RP, RT = ("box_break",), ("box_rip",), ("box_retest",)
APPLIES: dict[str, tuple[str, ...]] = {"trend_with": BX + RT + RP, "vol_flag": BX + RP, "depth_30": BX,
                                       "rr_3": BX + RP + RT, "premium_discount": RP + RT, "engulf": RP + RT,
                                       "anchor_box": BX, "tlv_count": ("trend_level_signal",)}
MFI_TFS = {"15m": ("15m", "1h", "4h"), "1h": ("1h", "4h")}      # the mfi_signals group on the timeframes at hand
TF_TAG = {"15m": "m15", "1h": "h1", "4h": "h4"}


def mfi_columns(dec_tf: str) -> tuple[str, ...]:
    return tuple(f"{name}_{TF_TAG[tf]}" for tf in MFI_TFS[dec_tf] for name in MFI_COLUMNS)


class _FlagTables:
    """Per-timeframe arrays the flags read, computed once per call."""

    def __init__(self, c: Course) -> None:
        h1 = c.b["1h"]
        self.trend1h = bc.structure_trend(h1.h, h1.l, 5)
        self.ph, self.pl = smc.pivot_points(h1.h, h1.l, 5)
        self.fib50 = (bc.last_two(self.ph)[0] + bc.last_two(self.pl)[0]) / 2.0   # 50% of the last confirmed swing
        self.mfi_sig = {tf: mfi_signals(c.b[tf].mfi, c.b[tf].h, c.b[tf].l).to_numpy(dtype=float)
                        for tf in MFI_TFS.get(c.dec_tf, ("1h", "4h")) if tf in c.b}
        self.engulf = {tf: smc.candle_class(b.o, b.c, np.nan_to_num(b.tick)) == smc.CANDLE_ENGULF
                       for tf, b in c.b.items() if tf != "1d"}
        self.pins = {tf: bc.pin_bar(b.o, b.h, b.l, b.c) for tf, b in c.b.items() if tf != "1d"}
        h4 = c.b["4h"]
        self.atr4_mean = bc.rolling(h4.atr, 14, "mean")
        hi_prev = pd.Series(h1.h).rolling(50, min_periods=50).max().shift(1).to_numpy()
        lo_prev = pd.Series(h1.l).rolling(50, min_periods=50).min().shift(1).to_numpy()
        with np.errstate(invalid="ignore"):
            up = (h1.h > hi_prev) & (h1.c < hi_prev)
            dn = (h1.l < lo_prev) & (h1.c > lo_prev)
        self.chop = (bc.rolling(up.astype(float), 24, "max") > 0) & (bc.rolling(dn.astype(float), 24, "max") > 0)


def _broken_near(c: Course, ft: _FlagTables, j1: int, side: int, price: float, tol: float) -> bool:
    """A previous confirmed 1h swing level (pivot 5) within the last 100 1h bars that a later 1h close has broken in the
    trade direction and that lies within `tol` of `price`."""
    h1 = c.b["1h"]
    lv = ft.ph if side > 0 else ft.pl
    for s in range(max(0, j1 - 100), j1 + 1):
        v = lv[s]
        if np.isfinite(v) and abs(price - v) <= tol:
            later = h1.c[s:j1 + 1]
            if (later > v).any() if side > 0 else (later < v).any():
                return True
    return False


def _pin_through(b: bc.TfBars, i: int, side: int, pins: np.ndarray, level: float) -> bool:
    return bool(i >= 0 and pins[i] == side and np.isfinite(level)
                and ((b.l[i] < level < b.c[i]) if side > 0 else (b.h[i] > level > b.c[i])))


def event_flags(c: Course, ft: _FlagTables, ev: Event, session: bool) -> dict[str, float]:
    """The 21 pre-registered H-40 groups for one candidate, each read at its signal close (`session`: section 4.12's
    London/New York gate at that close)."""
    fam = ev.member.split(":", 1)[0]
    side, close = ev.side, ev.close_ns
    b, h1, h4 = c.b[ev.tf], c.b["1h"], c.b["4h"]
    i, j1, j4, jd = ev.i, c.row("1h", close), c.row("4h", close), c.row("1d", close)
    atr1 = float(h1.atr[j1])
    tol = 0.25 * atr1
    hi50, lo50 = c.hi50["1h"][j1], c.lo50["1h"][j1]
    e20 = b.ema(20)
    fib50 = ft.fib50[j1]
    retest_levels = [lvl for s in range(max(1, i - 24), i + 1) for lvl in _crossed(b, s, side)]
    out = {
        "trend_with": float(ft.trend1h[j1] == side),
        "major_level": float(c.major(ev.lo, ev.hi, close)),
        "vol_flag": ev.info.get("vol", np.nan),
        "depth_30": float(ev.info.get("depth", np.nan) >= 0.3) if "depth" in ev.info else np.nan,
        "rr_3": float(ev.info["rr"] >= 3.0),
        "day_bias": float(jd >= 0 and side * (ev.entry - c.pp[jd]) > 0),
        "premium_discount": float(side * ((hi50 + lo50) / 2.0 - ev.entry) > 0),
        "engulf": float(ft.engulf[ev.tf][i]),
        "anchor_box": ev.info.get("anchor", np.nan),
        "fib50_confluence": float(np.isfinite(fib50) and abs(ev.entry - fib50) <= tol
                                  and _broken_near(c, ft, j1, side, ev.entry, tol)),
        "pin_ema20": float(_pin_through(b, i, side, ft.pins[ev.tf], float(e20[i]))),
        "pin_retest": float(any(_pin_through(b, i, side, ft.pins[ev.tf], lvl) for lvl in retest_levels)),
        "pin_inside": float(i >= 2 and b.h[i - 1] <= b.h[i - 2] and b.l[i - 1] >= b.l[i - 2]
                            and any(_pin_through(b, i - 2, side, ft.pins[ev.tf], lvl)
                                    for lvl in (*_edges(b, i - 2), float(e20[i - 2]), float(c.golden[ev.tf][0][i - 2])))),
        "ema8_21": float(side * (b.ema(8)[i] - b.ema(21)[i]) > 0),
        "tlv_count": ev.info.get("tlv_count", np.nan),
        "daily_with": float(c.d0(close) == side),
        "session_ln_ny": float(session),
        "chop": float(ft.chop[j1]),
        "stretch": float(side * (ev.entry - h1.ema(50)[j1]) / atr1 > 3.0),
        "atr_expansion": float(j4 >= 0 and h4.atr[j4] > 1.3 * ft.atr4_mean[j4]),
    }
    for col, fams in APPLIES.items():
        if fam not in fams:
            out[col] = np.nan
    for tf, arr in ft.mfi_sig.items():
        r = c.row(tf, close)
        for k, name in enumerate(MFI_COLUMNS):
            out[f"{name}_{TF_TAG[tf]}"] = side * float(arr[r, k]) if r >= 0 else np.nan
    return out


def _crossed(b: bc.TfBars, s: int, side: int) -> list[float]:
    """Box edges of `b` a close crossed at row s in the trade direction (tops for a long, bottoms for a short)."""
    tops, bots = b.box_set(s)
    if s < 1 or not len(tops):
        return []
    if side > 0:
        return [float(x) for x in tops[(b.c[s - 1] <= tops) & (tops < b.c[s])]]
    return [float(x) for x in bots[(b.c[s - 1] >= bots) & (bots > b.c[s])]]


def _edges(b: bc.TfBars, i: int) -> list[float]:
    tops, bots = b.box_set(i)
    return [float(x) for x in (*tops, *bots)]


# ------------------------------------------------------------------------------------------------- the specialists
class CourseConfig(FrozenRecord):
    """A family's only setting: the pre-registered rung (section 4: "Counts select the rung; nothing else does")."""

    rung: str = "L0"


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int),
                         "risk_usd": pd.Series(dtype=float), "max_bars": pd.Series(dtype=int),
                         "member": pd.Series(dtype=object)})


def _closes(m: pd.DataFrame) -> np.ndarray:
    return epoch_ns(utc_index(m["visible_at"]))


def place(events: Sequence[Event], m: pd.DataFrame, dec_tf: str) -> tuple[pd.DataFrame, list[Event]]:
    """Events on the decision bars that close with them: (frame of idx, side, risk_usd, max_bars, member; the kept
    events). One candidate per bar: same-side events keep the first (member order), opposite sides keep none. A
    signal's time exit is its own timeframe's, in decision bars."""
    if not len(events) or not len(m):
        return _empty(), []
    m_close = _closes(m)
    ev_close = np.array([e.close_ns for e in events], dtype=np.int64)
    pos = np.searchsorted(m_close, ev_close, side="left")
    rows: dict[int, list[int]] = {}
    for k, j in enumerate(pos):
        if j < len(m_close) and m_close[j] == ev_close[k]:
            rows.setdefault(int(j), []).append(k)
    keep = [(j, ks[0]) for j, ks in sorted(rows.items()) if len({events[k].side for k in ks}) == 1]
    kept = [events[k] for _, k in keep]
    dec_s = tf_seconds(dec_tf)
    frame = pd.DataFrame({
        "idx": np.array([j for j, _ in keep], dtype=int),
        "side": np.array([e.side for e in kept], dtype=int),
        "risk_usd": np.array([e.risk for e in kept], dtype=float),
        "max_bars": np.array([int(np.ceil(HOLD_BARS[e.tf] * tf_seconds(e.tf) / dec_s)) for e in kept], dtype=int),
        "member": pd.Series([e.member for e in kept], dtype=object)})
    return frame, kept


class _CourseSpecialist(Specialist):
    """A course family (module docstring): its rung is its only setting; structural stop as R, 2R target (K-T
    fallback), each signal timeframe's own time exit."""
    candidate_r = True                # R is the structural stop per candidate (`barrier_atr`)

    screening = True
    timeframe = "1h"
    default_config = CourseConfig().model_dump()
    # declared meta-model inputs (a model is pre-registered only after the rule-only screen passes); 1h decision bars
    # have no h1_ context columns
    model_features = tuple(f for f in SmcStructureSpecialist.model_features if not f.startswith("h1_"))

    def __init__(self, identity: AgentIdentity | None = None, **overrides: Any) -> None:
        super().__init__(identity, **overrides)
        self._cache: tuple[tuple[int, int, int], pd.DataFrame, list[Event]] | None = None
        self.setup()

    def setup(self) -> None:
        """Validate the configuration: the rung must be one of the family's pre-registered rungs."""
        try:
            self.params = CourseConfig(**{k: v for k, v in self.config.items() if k in CourseConfig.model_fields})
        except ValidationError as exc:
            raise ValueError(f"{self.family}: {exc}") from None
        if self.params.rung not in RUNGS[self.family]:
            raise ValueError(f"{self.family}: rung {self.params.rung!r} is not pre-registered; one of "
                             f"{list(RUNGS[self.family])}")

    @property
    def label_spec(self) -> BarrierSpec:
        """Stop 1 R, target 2 R (K-T fallback), the decision timeframe's time exit (each candidate carries its own)."""
        return BarrierSpec(target_atr=TARGET_R, stop_atr=1.0, max_bars=HOLD_BARS[self.timeframe], name=self.family)

    def box_tfs(self) -> set[str]:
        return box_timeframes(self.family, self.params.rung)

    def events(self, c: Course) -> list[Event]:
        return SCANNERS[self.family](c, self.params.rung)

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        raise ValueError(f"{self.family} reads the 1h/4h/daily context bars: use candidates_in_context")

    def _scan(self, m: pd.DataFrame, context: dict[str, pd.DataFrame] | None) -> tuple[pd.DataFrame, list[Event]]:
        """The placed candidates of these decision bars, computed once for `candidates_in_context` and `barrier_atr`
        (research.pipeline.prepare calls both on the same frame)."""
        if not len(m):
            return _empty(), []
        ns = _closes(m)
        key = (len(m), int(ns[0]), int(ns[-1]))
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1], self._cache[2]
        c = Course(m, self.timeframe, context, sorted(self.box_tfs()))
        frame, kept = place(self.events(c), m, self.timeframe)
        frame = self.annotate(c, frame, kept)
        self._cache = (key, frame, kept)
        return frame, kept

    def annotate(self, c: Course, frame: pd.DataFrame, kept: list[Event]) -> pd.DataFrame:
        """Extra candidate columns (the H-40 flags); none for a single family."""
        return frame

    def candidates_in_context(self, mid_bars: pd.DataFrame, features: pd.DataFrame,
                              context: dict[str, pd.DataFrame] | None,
                              ctx: dict[str, Any] | None = None) -> pd.DataFrame:
        """The rung's candidates on the decision bars (module docstring); a bar in the tier-1 blackout is dropped when
        the frame carries `in_blackout`."""
        frame, _ = self._scan(mid_bars, context)
        if len(frame) and "in_blackout" in features.columns:
            blk = features["in_blackout"].to_numpy(dtype=float)[frame["idx"].to_numpy()]
            frame = frame[~(blk == 1)].reset_index(drop=True)
        return frame

    def barrier_atr(self, mid_bars: pd.DataFrame, context: dict[str, pd.DataFrame] | None) -> pd.Series:
        """R per decision bar: |signal close - structural stop| on the candidate bars, NaN elsewhere (no label)."""
        frame, _ = self._scan(mid_bars, context)
        r = np.full(len(mid_bars), np.nan)
        r[frame["idx"].to_numpy(dtype=int)] = frame["risk_usd"].to_numpy(dtype=float)
        return pd.Series(r, index=mid_bars.index)


def _presets(family: str) -> dict[str, dict[str, Any]]:
    return {r: {"rung": r} for r in RUNGS[family]}


@register
class BoxRetestSpecialist(_CourseSpecialist):
    """H-39 `box_retest`: a broken 1h (from L1 also 4h) box, its first rejected retest within 24 (L2: 48) 1h bars."""

    family = "box_retest"
    presets = _presets("box_retest")


@register
class BoxRipSpecialist(_CourseSpecialist):
    """H-38 `box_rip`: a touch of a box without a close through, then an opposite candle closing back outside."""

    family = "box_rip"
    presets = _presets("box_rip")


@register
class FakeySpecialist(_CourseSpecialist):
    """H-44 `fakey`: an inside bar false-broken against the trend and closed back inside (4h; from L1 also 1h)."""

    family = "fakey"
    presets = _presets("fakey")


@register
class BoxBreakSpecialist(_CourseSpecialist):
    """H-37 `box_break`: a box break with a pullback entry on 15m (P1) and, from L1, on 1h under 4h boxes (P2).

    Research director ruling 2026-10-18, item 2: "Cost-gate it first: if gross expectancy per trade is below 0.85R on
    the pre-registered feature screen, retire it without a trial." `cost_gate_r` makes the screen report that reading
    (`research.screen.cost_gate`) on the run rung."""

    family = "box_break"
    timeframe = "15m"
    model_features = SmcStructureSpecialist.model_features
    presets = _presets("box_break")
    cost_gate_r = 0.85


@register
class TrendLevelSignalSpecialist(_CourseSpecialist):
    """H-45 `trend_level_signal`: the course's Long A / Short A checklists (B setups from L1). "E: entry at the
    **next** 1h bar's open" (section 4.0), so research fills at the first 1m open after the signal close."""

    family = "trend_level_signal"
    entry_at_next_open = True
    presets = _presets("trend_level_signal")


# ------------------------------------------------------------------------------------------- H-40 the union
# The rungs the dispatch brief of 2026-10-18 names for the families the research director funded (ruling item 1); the
# union must equal the rungs that actually run, so a different decision needs a new preset before H-40 runs.
A11_MEMBERS = ("trend_level_signal:L0", "box_retest:L3", "box_rip:L6", "fakey:L4")


class UnionConfig(FrozenRecord):
    """The families' rungs that run, in priority order (a bar two members fire on keeps the first)."""

    members: tuple[str, ...] = A11_MEMBERS

    @field_validator("members")
    @classmethod
    def _known(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for m in v:
            fam, _, rung = m.partition(":")
            if fam not in RUNGS or rung not in RUNGS[fam]:
                raise ValueError(f"member {m!r} is not a pre-registered family:rung")
        if not v or len({m.partition(":")[0] for m in v}) != len(v):
            raise ValueError("members must be non-empty, one rung per family")
        return v


@register
class BoxCourseDiscoverySpecialist(_CourseSpecialist):
    """H-40 `box_filter_discovery` (section 5.1): the candidates of the rungs that run, each carrying the 21
    pre-registered flag groups as candidate features, for ONE discovery trial (`research.yml -f discover=true` with
    DiscoveryConfig `pool: candidate`, so exactly these groups are screened: "K_eff = groups screened (21)"). Decision
    bars 1h, or 15m when H-37 (`box_break`, 15m entries) is a member. Every member enters at its signal close (H-45's
    next-open entry is its own trial's setting; the difference is the gap between a close and the next open)."""

    family = "box_course"
    timeframes = ("15m",)
    default_config = {"members": list(A11_MEMBERS)}
    presets = {"a11": {"members": list(A11_MEMBERS)},
               "a11_box_break": {"members": [*A11_MEMBERS, "box_break:L6"], TIMEFRAME_KEY: "15m"}}

    def setup(self) -> None:
        try:
            self.union = UnionConfig(members=tuple(self.config["members"]))
        except ValidationError as exc:
            raise ValueError(f"{self.family}: {exc}") from None
        if any(m.startswith("box_break:") for m in self.union.members) != (self.timeframe == "15m"):
            raise ValueError(f"{self.family}: decision bars are 15m exactly when box_break is a member")
        self.candidate_features = (*FLAG_GROUPS, *mfi_columns(self.timeframe))
        self.candidate_feature_groups = {**FLAG_GROUPS, **{c: MFI_GROUP for c in mfi_columns(self.timeframe)}}

    def box_tfs(self) -> set[str]:
        out: set[str] = set()
        for m in self.union.members:
            fam, _, rung = m.partition(":")
            out |= box_timeframes(fam, rung)
        return out

    def events(self, c: Course) -> list[Event]:
        out: list[Event] = []
        for m in self.union.members:
            fam, _, rung = m.partition(":")
            out += SCANNERS[fam](c, rung)
        order = {m: k for k, m in enumerate(self.union.members)}
        return sorted(out, key=lambda e: (e.close_ns, order[e.member.rsplit(":", 1)[0]]))

    def annotate(self, c: Course, frame: pd.DataFrame, kept: list[Event]) -> pd.DataFrame:
        cols = list(self.candidate_features)
        if not kept:
            return pd.concat([frame, pd.DataFrame(columns=cols, dtype=float)], axis=1)
        ft = _FlagTables(c)
        session = bc.session_ln_ny(np.array([e.close_ns for e in kept], dtype=np.int64))
        flags = pd.DataFrame([event_flags(c, ft, e, bool(s)) for e, s in zip(kept, session)], columns=cols)
        return pd.concat([frame.reset_index(drop=True), flags], axis=1)
