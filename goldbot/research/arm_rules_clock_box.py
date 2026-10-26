"""Wave-2 arm rules on local clocks and the box family's adaptive exits (arms expansion 2026-10-26, lane W2d). Each
rule is one function used by the history matrix and live (`arm_rules.RULE_FUNCS`); a decision on the close of bar i
reads bars <= i only, and appending bars never changes a past candidate (arms-2026-10-25.md 8.0 "Causality").

* `friday_long` (WFRI, prereg-2026-10-26-time-structure.md section 4, H-72): "long at the close of the first 1h bar
  ending at or after 19:00 New York on Thursday ... Exit at market at 16:30 New York on Friday. Stop 1.0 x ATR14(4h)
  at entry ... Not on a CME metals holiday or early close on either day. No target." Decided on 30m bars so the time
  exit lands on 16:30 New York: the candidate is the first 30m bar closing on the hour at or after 19:00 New York on
  Thursday (an hour close is a 1h bar's close: New York's offset is whole hours), its hold the 30m bars to 16:30
  Friday. ATR14(4h) is the last 4h bar closed by the decision close. The $53.2 skip is the arm row's `max_stop_usd`.
  Exit amended 2026-10-26 (prereg section 4, amendment 1): the engine's Friday weekend cut (`EngineConfig.weekend_cut`
  21:30 server, `server_tz` Europe/Athens, about 14:30 New York) is part of WFRI's exit, as live: at the cut a losing
  WFRI is closed and a winning one has its stop tightened to entry + 0.5 x (price - entry) (never loosened), then held
  to the 16:30 New York time exit. The frame keeps the 16:30 hold (`max_bars`) and carries `cut_at` (UTC); the read is
  the realised outcome under the cut, `walk_friday` on the 1m path (history) and the engine's own weekend rule (live).
* `clock_anchor` (KA15, arms 8.8 and 8.13): strata T (Tokyo 08:00, reversal), S (SGE AM auction 10:15 Shanghai,
  continuation), C (COMEX settle 13:30 New York, reversal) on 15m bars; pre = close at the anchor - close 30 min
  before (both closed 15m bars), skipped if |pre| < 0.1 x ATR14(15m); "stop = entry -/+ 1.0 x ATR14(15m); no target;
  time exit at the close of bar i+2". T only "while the US is on daylight time" (the 18:00 New York reopen at least
  60 min before). Re-measured 2026-10-26 on 2010-2017 US-daylight days (8.13 condition 1): corr -0.087, t -3.03,
  n 1,201: T stays (every usable 2010-2017 event already falls on US daylight time; in US winter the 30 min before
  08:00 Tokyo is the CME break and has no price). The `stratum` column lets a read charge T at its signal-time spread
  on both legs (8.13 condition 2) and report the family without T.
* `clock_anchor_control` (XC15, 8.9 and 8.13): KA15's decision closes, stop, band and exit, side
  `hash_side("XC15", close)`. A control entry shifted because KA15 is open on a netting host (`shift_control`) is
  logged but "excluded from pairing" (`pair_with_control`).
* `box_next` (BXR / BXT / BXF, prereg-exits-adaptive-2026-10-26.md, H-80): the entry is
  `box_break_close.decide_with_stop` with every constant unchanged; "Stop: 1.5 x ATR14 of the decision timeframe at
  the signal bar (= 1 R)"; exit `runner` (no target; `runner_stop`: break-even at +1 R to entry + side x $0.23,
  chandelier 2.5 ATR behind the best exit-side price armed at +1 R, effective from the next bar close), `struct`
  ("target the first box in force at t lying at or beyond 2 R, else 2 R", `features.box_course.next_box`) or `fixed`
  (2 R); time exit 96/72/48/24 bars.

Common to all (8.0 "Clocks"): no entry on a CME metals holiday or early close (`config/holidays_cme_metals.yaml`,
trading day = New York date after +7 h) where the list covers the date; history before the list's `valid_from` is
not filtered (no list exists for it), declared. "No target" is a target at NO_TARGET_R (a fixed-price bracket that
the measured excursions never reach: the box runner's 2010-2017 maximum was 16.1 R).

Live and history share these functions (engine lane E3, trading-safety review): the engine trails BXR's stop with
`runner_stop_at` (= `walk_runner` on the same 1m bars) through its own stop move, scores the BXT/BXF virtual twins on
each BXR fill with `twin_outcome`, and shifts XC15 behind an open KA15 on a netting host as `shift_control` does
(`goldbot.engine.exploration`); `scripts/run_arm_backtest.py` exits BXR on `walk_runner`, scores its twins on the same
entries and charges stratum T's measured spread on both legs. The Friday weekend rule is the engine's (`runner._weekend_rule`); `walk_friday` models it for WFRI only
(the cut price is the mid open of the first 1m bar at or after the cut; live decides on the bid, half a spread lower)."""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.resample import mid, resample_bars
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features.box_course import next_box
from goldbot.features.technical import atr
from goldbot.research.arm_rules import NEW_YORK, RuleContext, _frame, empty, hash_side
from goldbot.research.arms import Arm

NO_TARGET_R = 50.0
HOLIDAY_FILE = "config/holidays_cme_metals.yaml"
# WFRI
WFRI_TF = "30m"
WFRI_ENTRY_MIN = 19 * 60                    # Thursday 19:00 New York
WFRI_EXIT = (16, 30)                        # Friday 16:30 New York
WFRI_STOP_ATR4H = 1.0
WFRI_CUT_TZ, WFRI_CUT = "Europe/Athens", (21, 30)   # = EngineConfig.server_tz / weekend_cut (a test holds them equal)
WFRI_CUT_KEEP = 0.5                         # runner._weekend_rule: a winner's stop locks in half the open profit
# Rules whose exit includes the engine's Friday weekend cut (amendment 1): `exploration.crosses_friday` lets their
# entries hold across the cut (never into the weekend), and a close by the cut is their own exit, not a censoring.
FOLLOWS_FRIDAY_CUT = frozenset({"friday_long"})
# Decision bars live must hand the rule for its candidates to equal the history run's (`arm_rules.lookback_bars`):
# WFRI's stop is Wilder's ATR14 of the 4h bars, whose start weighs (13/14)^k after k bars; 200 4h bars (1,600 of its
# 30m bars) put it below 1e-6. The default 200 decision bars are 25 4h bars: a stop ~15% off history's (tested).
LOOKBACK_BARS = {"friday_long": 1_600}
# KA15: stratum -> (zone, hh, mm, side vs sign(pre))
ANCHORS: dict[str, tuple[str, int, int, int]] = {
    "T": ("Asia/Tokyo", 8, 0, -1), "S": ("Asia/Shanghai", 10, 15, 1), "C": (NEW_YORK, 13, 30, -1)}
KA_TF, KA_MIN_PRE_ATR, KA_STOP_ATR, KA_HOLD = "15m", 0.1, 1.0, 2
REOPEN_NY = ((16 * 60 + 55, 17 * 60), (18 * 60, 18 * 60 + 30))      # 8.0: no entry 16:55-17:00, 18:00-18:30 NY
# H-80
BOX_STOP_ATR, BOX_TARGET_R, BE_USD, TRAIL_ATR = 1.5, 2.0, 0.23, 2.5
BOX_EXITS = ("runner", "struct", "fixed")


@lru_cache(maxsize=1)
def _holidays() -> Any:
    from goldbot.risk.windows import load_holidays
    return load_holidays(HOLIDAY_FILE)


def cme_holiday(days: list[date] | np.ndarray) -> np.ndarray:
    """True where a date is a listed CME metals holiday or early close (only where the list covers the date)."""
    hl = _holidays()
    return np.array([hl.covers(d) and hl.thin(d) is not None for d in days], dtype=bool)


def trading_day(close: pd.DatetimeIndex) -> np.ndarray:
    """8.0: trading day = New York date after +7 h (it ends 17:00 New York)."""
    return np.asarray((close.tz_convert(NEW_YORK) + pd.Timedelta(hours=7)).date)


def _ns(ts: pd.DatetimeIndex) -> np.ndarray:
    return epoch_ns(ts)


# ---------------------------------------------------------------- WFRI
def _no_wfri() -> pd.DataFrame:
    return empty().assign(cut_at=pd.Series(dtype="datetime64[ns, UTC]"))


def friday_long(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """WFRI (module docstring): long at Thursday's first on-the-hour close at or after 19:00 New York."""
    close = ctx.close_ts
    if not len(close):
        return _no_wfri()
    ny = close.tz_convert(NEW_YORK)
    mins = (ny.hour * 60 + ny.minute).to_numpy()
    ok = (ny.dayofweek.to_numpy() == 3) & (mins >= WFRI_ENTRY_MIN) & (ny.minute.to_numpy() == 0)
    day = np.asarray(ny.date)
    cand = np.flatnonzero(ok)
    idx = cand[np.unique(day[cand], return_index=True)[1]] if len(cand) else cand      # the first per Thursday
    if not len(idx):
        return _no_wfri()
    thu = day[idx]
    fri = np.array([d + pd.Timedelta(days=1) for d in thu])
    keep = ~(cme_holiday(list(thu)) | cme_holiday(list(fri)))
    idx, fri = idx[keep], fri[keep]
    if not len(idx):
        return _no_wfri()
    exit_at = pd.DatetimeIndex([pd.Timestamp(year=d.year, month=d.month, day=d.day, hour=WFRI_EXIT[0],
                                             minute=WFRI_EXIT[1], tz=NEW_YORK) for d in fri])
    cut_at = pd.DatetimeIndex([pd.Timestamp(year=d.year, month=d.month, day=d.day, hour=WFRI_CUT[0],
                                            minute=WFRI_CUT[1], tz=WFRI_CUT_TZ) for d in fri]).tz_convert("UTC")
    hold = ((exit_at - close[idx]) / pd.Timedelta(minutes=30)).to_numpy().astype(int)
    a4 = atr4h_at(ctx, close[idx])
    out = _frame(idx, np.ones(len(idx), dtype=int), WFRI_STOP_ATR4H * a4, NO_TARGET_R, hold)
    cut = dict(zip(idx.tolist(), cut_at, strict=True))
    return out.assign(cut_at=pd.DatetimeIndex([cut[int(i)] for i in out["idx"]], tz="UTC"))


def walk_friday(path: pd.DataFrame, entry: float, risk: float, cut_at: pd.Timestamp,
                exit_at: pd.Timestamp) -> dict[str, Any]:
    """One WFRI long on its 1m mid path after entry (`path`: ts_utc, open, high, low, close) under the engine's Friday
    weekend cut (module docstring, amendment 1): at the open of the first minute at or after `cut_at` a loser (price <=
    entry) is closed there (`weekend_loser`) and a winner's stop becomes max(stop, round(entry + 0.5 x (price - entry),
    2)); then stop first in a minute, the time exit at the close of the minute ending at `exit_at`."""
    ts = utc_index(path["ts_utc"])
    o, lo, c = (path[k].to_numpy(float) for k in ("open", "low", "close"))
    stop, cut_done = entry - risk, False
    for j in range(len(path)):
        if not cut_done and ts[j] >= cut_at:
            cut_done = True
            if o[j] - entry <= 0:
                return {"exit": o[j], "kind": "weekend_loser", "at": ts[j], "stop": stop}
            stop = max(stop, round(entry + WFRI_CUT_KEEP * (o[j] - entry), 2))
        if lo[j] <= stop:
            return {"exit": stop if o[j] > stop else o[j], "kind": "stop", "at": ts[j], "stop": stop}
        if ts[j] + pd.Timedelta(minutes=1) >= exit_at:
            return {"exit": c[j], "kind": "time", "at": ts[j] + pd.Timedelta(minutes=1), "stop": stop}
    return {"exit": c[-1] if len(c) else entry, "kind": "open", "at": ts[-1] if len(ts) else None, "stop": stop}


def atr4h_at(ctx: RuleContext, at: pd.DatetimeIndex) -> np.ndarray:
    """Wilder ATR14 of the last 4h bar closed by each time in `at` (the 1m bars resampled; a 4h bar exists once its
    close has passed), NaN before the first."""
    b4 = resample_bars(ctx.b1, "4h")
    if b4.empty:
        return np.full(len(at), np.nan)
    vis = utc_index(b4["visible_at"])
    a = atr(mid(b4).reset_index(drop=True), 14).to_numpy(dtype=float)
    j = np.searchsorted(_ns(vis), _ns(at), side="right") - 1
    return np.where(j >= 0, a[np.maximum(j, 0)], np.nan)


# ---------------------------------------------------------------- KA15 / XC15
def _anchor_frame(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Every KA15 decision (module docstring) with its stratum and the rule's side."""
    strata = list(arm.params.get("strata", list(ANCHORS)))
    close = ctx.close_ts
    m = ctx.mid
    c = m["close"].to_numpy(dtype=float)
    a = atr(m, 14).to_numpy(dtype=float)
    ns = _ns(close)
    ny = close.tz_convert(NEW_YORK)
    nymin = (ny.hour * 60 + ny.minute).to_numpy()
    reopen = np.zeros(len(close), dtype=bool)
    for lo, hi in REOPEN_NY:
        reopen |= (nymin >= lo) & (nymin < hi)
    hol = cme_holiday(list(trading_day(close))) if len(close) else np.zeros(0, dtype=bool)
    rows = []
    for s in strata:
        zone, hh, mm, sgn = ANCHORS[s]
        loc = close.tz_convert(zone)
        at = (loc.hour.to_numpy() == hh) & (loc.minute.to_numpy() == mm) & (loc.dayofweek.to_numpy() < 5)
        if s == "T":
            at &= nymin >= 19 * 60                       # the 18:00 New York reopen at least 60 min before
        for i in np.flatnonzero(at & ~reopen & ~hol):
            j = int(np.searchsorted(ns, ns[i] - 30 * 60 * 10 ** 9))
            if j >= i or ns[j] != ns[i] - 30 * 60 * 10 ** 9 or not np.isfinite(a[i]):
                continue                                  # the bar closing 30 min before is not there
            pre = c[i] - c[j]
            if abs(pre) < KA_MIN_PRE_ATR * a[i] or pre == 0:
                continue
            rows.append((int(i), sgn * int(np.sign(pre)), KA_STOP_ATR * a[i], s))
    if not rows:
        return empty().assign(stratum=pd.Series(dtype=str))
    rows.sort()
    out = _frame(np.array([r[0] for r in rows]), np.array([r[1] for r in rows]), np.array([r[2] for r in rows]),
                 NO_TARGET_R, KA_HOLD)
    strat = {r[0]: r[3] for r in rows}
    return out.assign(stratum=[strat[int(i)] for i in out["idx"]])


def clock_anchor(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    return _anchor_frame(arm, ctx)


def clock_anchor_control(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """XC15: KA15's decisions (taken or skipped) with side `hash_side(arm.id, close)`; stop, band and exit unchanged."""
    out = _anchor_frame(arm, ctx)
    if out.empty:
        return out
    close = ctx.close_ts
    return out.assign(side=[hash_side(arm.id, close[int(i)]) for i in out["idx"]])


def shift_control(decision: pd.DatetimeIndex, arm_open: list[tuple[pd.Timestamp, pd.Timestamp]],
                  step: pd.Timedelta = pd.Timedelta(minutes=15)) -> pd.DataFrame:
    """XC15 on a netting host (8.9): the entry is the decision close, or, while KA15 is open ([entry, exit)), the
    next 15m close at which it is not; `shifted` marks a moved entry."""
    entry = []
    for t in decision:
        e = t
        while any(lo <= e < hi for lo, hi in arm_open):
            e = e + step
        entry.append(e)
    ent = pd.DatetimeIndex(entry)
    return pd.DataFrame({"decision": decision, "entry": ent, "shifted": np.asarray(ent != decision)})


def pair_with_control(arm_trades: pd.DataFrame, control: pd.DataFrame) -> pd.DataFrame:
    """Family-minus-control pairs on the same decision close; a shifted control entry is excluded (8.13 XC15)."""
    c = control.loc[~control["shifted"].to_numpy(dtype=bool)]
    return arm_trades.merge(c, on="decision", how="inner", suffixes=("_arm", "_control"))


# ---------------------------------------------------------------- H-80 box family
def box_next(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """BXR / BXT / BXF entries (module docstring) with R = 1.5 ATR14 and the exit of `params.exit`. Every row also
    carries `struct_r`, the STRUCT target in R at the signal bar ("the first box in force at t lying at or beyond 2 R,
    else 2 R"), so BXR's virtual BXT twin reads the same target in history and live."""
    from goldbot.features import smc_replica as smc
    from goldbot.specialists.box_break_close import HOLD_BARS, decide_with_stop
    mode = arm.params["exit"]
    bars = ctx.mid
    if len(bars) < 2:
        return empty()
    p = smc.DEFAULT_PARAMS
    frame = smc.replica_frame(bars, p)
    trace: list[tuple[np.ndarray, np.ndarray]] = []
    o, h, lo, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    smc.sr_channel_breaks(o, h, lo, c, p.sr_pivot_len, p.sr_width_pct, p.sr_max_channels, p.sr_lookback,
                          p.sr_range_window, trace)
    vol = (bars["tick_count"] if "tick_count" in bars.columns else bars["volume"]).to_numpy(float)
    mfi = smc.money_flow(h, lo, c, vol, p.mfi_len)
    a = atr(bars, 14).to_numpy(float)
    trend = frame["trend_swing"].to_numpy(float)
    f1, f2 = frame["range_f1_dist_atr"].to_numpy(float), frame["range_f2_dist_atr"].to_numpy(float)
    rows = []
    for t in range(len(bars)):
        tops, bots = trace[t]
        side, _ = decide_with_stop(t, o, c, mfi, a, tops, bots, trend[t], f1[t], f2[t])
        if not side:
            continue
        r = BOX_STOP_ATR * a[t]
        nb = next_box(tops, bots, float(c[t]) + side * BOX_TARGET_R * r, side)
        struct = side * (nb - c[t]) / r if np.isfinite(nb) else BOX_TARGET_R
        tgt = {"runner": NO_TARGET_R, "struct": struct, "fixed": BOX_TARGET_R}[mode]
        rows.append((t, side, r, tgt, struct))
    if not rows:
        return empty().assign(struct_r=pd.Series(dtype=float))
    out = _frame(np.array([x[0] for x in rows]), np.array([x[1] for x in rows]), np.array([x[2] for x in rows]),
                 np.array([x[3] for x in rows]), HOLD_BARS[arm.timeframe])
    struct_r = {x[0]: x[4] for x in rows}
    return out.assign(struct_r=[float(struct_r[int(i)]) for i in out["idx"]])


def runner_stop(stop: float, entry: float, side: int, risk: float, atr_sig: float, best: float) -> float:
    """BXR's stop after a decision-bar close (effective from the next bar): armed once the best exit-side price since
    entry reached +1 R, then break-even (entry + side x $0.23) and the 2.5 ATR chandelier behind `best`. "A policy only
    ever tightens the stop": the result is never looser than `stop`."""
    if side * (best - entry) < risk:
        return stop
    cand = max(entry + BE_USD, best - TRAIL_ATR * atr_sig) if side > 0 else min(entry - BE_USD,
                                                                                 best + TRAIL_ATR * atr_sig)
    return max(stop, cand) if side > 0 else min(stop, cand)


def walk_runner(path: pd.DataFrame, closes: pd.DatetimeIndex, entry: float, side: int, risk: float, atr_sig: float,
                target_r: float = NO_TARGET_R) -> dict[str, Any]:
    """One trade on its 1m mid path after entry (`path`: ts_utc, open, high, low, close) with decision-bar closes
    `closes` (the last is the time exit). Stop first in a minute; the stop moves only at a decision close
    (`runner_stop`); `target_r` NO_TARGET_R for BXR, the fixed/struct target for BXT/BXF on the same path."""
    ts = utc_index(path["ts_utc"]) + pd.Timedelta(minutes=1)          # a minute's close
    o, h, lo, c = (path[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    stop, best = entry - side * risk, entry
    target = entry + side * target_r * risk
    stops = [stop]
    k = 0
    for j in range(len(path)):
        adv, fav = (lo[j], h[j]) if side > 0 else (h[j], lo[j])
        if side * (adv - stop) <= 0:
            px = stop if side * (o[j] - stop) > 0 else o[j]
            return {"exit": px, "kind": "stop", "at": ts[j], "stops": stops}
        if side * (fav - target) >= 0:
            return {"exit": target, "kind": "target", "at": ts[j], "stops": stops}
        best = max(best, fav) if side > 0 else min(best, fav)
        while k < len(closes) and ts[j] >= closes[k]:
            if k == len(closes) - 1:
                return {"exit": c[j], "kind": "time", "at": ts[j], "stops": stops}
            stop = runner_stop(stop, entry, side, risk, atr_sig, best) if target_r >= NO_TARGET_R else stop
            stops.append(stop)
            k += 1
    return {"exit": c[-1] if len(c) else entry, "kind": "open", "at": ts[-1] if len(ts) else None, "stops": stops}


_NEVER = pd.DatetimeIndex([pd.Timestamp("2262-01-01", tz="UTC")])     # a close no path reaches: no time exit yet


def is_runner(arm: Arm) -> bool:
    """A BXR row: `box_next` with the RUNNER exit (real orders whose stop the engine trails)."""
    return arm.rule == "box_next" and arm.params.get("exit") == "runner"


def twin_ids(arm_id: str) -> dict[str, str]:
    """BXR's virtual twins (exits-adaptive-targets-2026-10-26.md section 5: "BXT15 ... virtual (no orders), on each
    BXR fill", "BXF15 ... virtual, on each BXR fill"): BXR<tf> -> BXT<tf> (STRUCT) and BXF<tf> (FIXED)."""
    if arm_id.startswith("BXR"):
        return {"struct": "BXT" + arm_id[3:], "fixed": "BXF" + arm_id[3:]}
    return {m: f"{arm_id}-{m}" for m in ("struct", "fixed")}


def mid_path(b1: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    """The 1m mid path from the first minute at or after `start` (the decision close: the entry minute), the input of
    `walk_runner` in history and live alike."""
    rows = b1.loc[np.asarray(utc_index(b1["ts_utc"]) >= start)]
    return mid(rows).reset_index(drop=True)[["ts_utc", "open", "high", "low", "close"]]


def decision_closes(b1: pd.DataFrame, tf: str, after: pd.Timestamp, n: int) -> pd.DatetimeIndex:
    """The first `n` closes of `tf` bars closing after `after`, built from the 1m bars as the history matrix builds
    its decision bars (`resample_bars`; intraday bars floor on the UTC clock, so a slice from a bar close gives the
    same bars as the whole history)."""
    src = b1.loc[np.asarray(utc_index(b1["ts_utc"]) >= after)]
    if src.empty:
        return pd.DatetimeIndex([], tz="UTC")
    vis = utc_index(resample_bars(src, tf)["visible_at"])
    return vis[vis > after][:n]


def runner_stop_at(path: pd.DataFrame, closes: pd.DatetimeIndex, entry: float, side: int, risk: float,
                   atr_sig: float) -> float:
    """BXR's stop after the last decision close `path` has reached: `walk_runner` on the same path and closes with no
    time exit yet. Live and history run this one function on the same bars, so the engine's trail is the history
    trail, and a restart recomputes it from the stored bars (the engine keeps the tighter of it and its own stop)."""
    return float(walk_runner(path, pd.DatetimeIndex(closes.append(_NEVER)), entry, side, risk, atr_sig)["stops"][-1])


def twin_outcome(path: pd.DataFrame, closes: pd.DatetimeIndex, hold: int, *, entry: float, side: int, risk: float,
                 target_r: float) -> dict[str, Any] | None:
    """A virtual twin's exit on the BXR fill's 1m path (prereg H-80: "BXT/BXF are computed on each BXR fill's 1m path
    from the real fill price"): fixed stop 1 R, target `target_r` R, time exit at the `hold`-th close. None while the
    path has reached none of its barriers (fewer than `hold` closes seen, no stop or target yet)."""
    seen = closes[:hold] if len(closes) >= hold else pd.DatetimeIndex(closes.append(_NEVER))
    w = walk_runner(path, seen, entry, side, risk, risk / BOX_STOP_ATR, target_r=target_r)
    return None if w["kind"] == "open" else w


# ---------------------------------------------------------------- registration and load checks
RULE_FUNCS = {"friday_long": friday_long, "clock_anchor": clock_anchor,
              "clock_anchor_control": clock_anchor_control, "box_next": box_next}


def check(arm: Arm) -> None:
    """Refused at load: a timeframe the rule is not specified on, an unknown stratum or exit mode."""
    from goldbot.specialists.box_break_close import ROSTER_TIMEFRAMES
    if arm.rule == "friday_long" and arm.timeframe != WFRI_TF:
        raise ValueError(f"friday_long runs on {WFRI_TF} decision bars (16:30 New York exit), not {arm.timeframe}")
    if arm.rule in ("clock_anchor", "clock_anchor_control"):
        if arm.timeframe != KA_TF:
            raise ValueError(f"{arm.rule} runs on {KA_TF}, not {arm.timeframe}")
        bad = set(arm.params.get("strata", list(ANCHORS))) - set(ANCHORS)
        if bad:
            raise ValueError(f"unknown clock-anchor strata {sorted(bad)}; known: {list(ANCHORS)}")
    if arm.rule == "box_next":
        if arm.timeframe not in ROSTER_TIMEFRAMES:
            raise ValueError(f"box_next runs on {ROSTER_TIMEFRAMES}, not {arm.timeframe}")
        if arm.params.get("exit") not in BOX_EXITS:
            raise ValueError(f"box_next needs params.exit in {BOX_EXITS}, got {arm.params.get('exit')!r}")
