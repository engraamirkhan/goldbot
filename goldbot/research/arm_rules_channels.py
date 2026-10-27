"""Wave-2 channel arm rules (ARM-SPEC 2026-10-25, section 8 and amendment 8.13, docs/research/arms-2026-10-25.md):
KSQ (8.2, squeeze release), DON (8.3, Donchian-55 break-close) and C30 (8.4, first touch of the 30-day low) with its
control C30C (8.9 as amended in 8.13: "Its window is the whole trading day ... hash_u < 0.00065").

8.0: "A level, band, pivot, block or anchor exists only from the close of the bar that confirms it ... Each rule is one
function used by the history matrix and live, and its test proves prefix invariance (appending bars never changes a
past candidate)." Every value here is read from bars <= the decision bar, on mid prices of the decision bars.

8.0 brackets: "Stop and target are fixed prices set at entry ... 'no target' means stop and time exit only". The arm
frame has no "no target" form, so KSQ and DON carry NO_TARGET_R (20 R at a 1 ATR stop): out of reach within their 4 to
24-bar holds (the slow history test checks no 2010-2017 candidate's favourable excursion reached it), as asia_drift's
NO_TARGET_ATR.

8.0 common filters (on top of section 0's, which `arm_rules.post_filter` applies): "no entry 16:55-17:00 or 18:00-18:30
New York" and "No entry on CME metals holidays" (the listed days of config/holidays_cme_metals.yaml, by trading day;
history before the list's validity has no listed day, and live the RiskGate's holiday window also binds).

Declared deviations: DOND decides on goldbot's 1d bars (feature day to the 13:30 New York settlement, as W1B), not on
17:00 New York days: a 17:00 New York decision close always falls in the rollover filter (20:45-22:15 UTC) and in the
16:55-17:00 New York filter, so it could never enter. C30's day context (8.4) is built from the decision 15m bars
themselves (trading day = New York date after +7 h); the first trading day of the input is dropped as possibly partial,
and fewer than 30 completed days before D gives no candidate (fail closed). Live, the engine extends its ~40-day window
with stored bars to HISTORY_BARS (C30's 15m, DOND's 1d), so both decide on the history run's input."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.timeutil import utc_index
from goldbot.features.technical import atr, ema, sma
from goldbot.research.arm_rules import NEW_YORK, RuleContext, _frame, empty, hash_u
from goldbot.research.arm_rules_weekcut import CUT_CAPPED, capped
from goldbot.research.arms import Arm

NO_TARGET_R = 20.0                  # "no target" (8.2, 8.3): 20 x a 1 ATR stop, out of reach within the hold
KSQ_HOLD = 4                        # 8.2: time exit at bar i+4
KSQ_ON_BARS = 6                     # 8.2: squeeze on at every bar i-6..i-1
DON_N = 55                          # 8.3: bars i-55..i-1
DON_HOLD = {"4h": 24, "1d": 10}     # 8.3: 24 bars at 4h, 10 at 1d
C30_DAYS = 30                       # 8.4: the 30 completed trading days before D
C30_HOLD = 460                      # 8.4: 460 15m bars
C30_STOP_ATR = 0.5                  # 8.4: stop and target 0.5 x ATR14(1d) of D-1 (1 R)
C30C_RATE = 0.00065                 # 8.13: C30C fires when hash_u < 0.00065
TIMEFRAMES: dict[str, tuple[str, ...]] = {"squeeze_release": ("1h", "4h"), "donchian_break": ("4h", "1d"),
                                          "low30_touch": ("15m",), "low30_control": ("15m",)}
HOLIDAYS = Path(__file__).parents[2] / "config" / "holidays_cme_metals.yaml"
# bars of the arm's timeframe a rule reads before its decision bar for live to equal history, beyond the engine's
# ~40-day 1m memory: C30's 30 completed trading days and ATR14(1d) of D-1 (~200 trading days of 15m, Wilder's start
# residue below 1e-5), DOND's 55 days and ATR14(1d) (300 days); served from stored bars (`engine.exploration_context`)
HISTORY_BARS: dict[tuple[str, str], int] = {("low30_touch", "15m"): 20_000, ("low30_control", "15m"): 20_000,
                                            ("donchian_break", "1d"): 300}


def spec_hold(arm: Arm) -> int:
    """The frozen hold of the arm's rule on its timeframe (decision bars)."""
    if arm.rule == "squeeze_release":
        return KSQ_HOLD
    if arm.rule == "donchian_break":
        return DON_HOLD[arm.timeframe]
    return C30_HOLD


def check_arm(arm: Arm) -> None:
    """Refused at load: a timeframe the spec does not declare, or a `hold_bars` param (the engine's time barrier for an
    adopted position) that differs from the rule's frozen hold or is missing."""
    allowed = TIMEFRAMES[arm.rule]
    if arm.timeframe not in allowed:
        raise ValueError(f"rule {arm.rule} runs on {allowed} only, not {arm.timeframe}")
    hold = arm.params.get("hold_bars")
    if hold != spec_hold(arm):
        raise ValueError(f"rule {arm.rule} on {arm.timeframe} holds {spec_hold(arm)} bars; params.hold_bars is {hold!r}")


def trading_day(ts: pd.DatetimeIndex) -> np.ndarray:
    """8.0: "trading day = NY date after +7 h (ends 17:00 New York)", as days since the epoch."""
    ny = (ts.tz_convert(NEW_YORK) + pd.Timedelta(hours=7)).normalize().tz_localize(None)
    return np.asarray(ny.to_numpy().astype("datetime64[D]")).astype(np.int64)


@lru_cache(maxsize=1)
def _listed_days() -> frozenset[int]:
    from goldbot.risk.windows import load_holidays
    hl = load_holidays(HOLIDAYS)
    return frozenset(int(np.datetime64(d, "D").astype(np.int64)) for d in hl.days)


def wave2_ok(close: pd.DatetimeIndex) -> np.ndarray:
    """8.0's added filters on the decision close: not 16:55-17:00 or 18:00-18:30 New York (inclusive), not a listed CME
    metals holiday (by the trading day of the bar the close ends)."""
    ny = close.tz_convert(NEW_YORK)
    m = (ny.hour * 60 + ny.minute).to_numpy()
    reopen = ((m >= 16 * 60 + 55) & (m <= 17 * 60)) | ((m >= 18 * 60) & (m <= 18 * 60 + 30))
    day = trading_day(close - pd.Timedelta(minutes=1))
    holiday = np.isin(day, np.fromiter(_listed_days(), dtype=np.int64))
    return ~(reopen | holiday)


def _keep(ctx: RuleContext, idx: np.ndarray) -> np.ndarray:
    return idx[wave2_ok(ctx.close_ts[idx])] if len(idx) else idx


# ---------------------------------------------------------------- 8.2 KSQ squeeze release
def squeeze_release(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """8.2: "BB = SMA20 +/- 2 x SD20; KC1.5 = EMA20 +/- 1.5 x ATR14. Squeeze on at bar j when BB upper < KC1.5 upper and
    BB lower > KC1.5 lower. Release at bar i: off at i, on at every bar i-6..i-1. Direction d = sign(close_i - (M_i +
    SMA20_i) / 2), M_i = (max high + min low of bars i-19..i) / 2; d = 0 skips." Stop 1.0 x ATR14_i; no target; time
    exit at bar i+4."""
    m = ctx.mid
    c, h, lo = m["close"], m["high"], m["low"]
    a = atr(m, 14)
    mid_bb, sd = sma(c, 20), c.rolling(20, min_periods=20).std(ddof=0)
    kc = ema(c, 20)
    with np.errstate(invalid="ignore"):
        on = ((mid_bb + 2 * sd) < (kc + 1.5 * a)) & ((mid_bb - 2 * sd) > (kc - 1.5 * a))
    on_before = on.astype(int).rolling(KSQ_ON_BARS, min_periods=KSQ_ON_BARS).sum().shift(1) == KSQ_ON_BARS
    m20 = (h.rolling(20, min_periods=20).max() + lo.rolling(20, min_periods=20).min()) / 2
    d = np.sign((c - (m20 + mid_bb) / 2).to_numpy(dtype=float))
    a_ = a.to_numpy(dtype=float)
    release = (~on & on_before).to_numpy() & np.isfinite(d) & (d != 0) & np.isfinite(a_)
    idx = _keep(ctx, np.flatnonzero(release))
    if len(idx) == 0:
        return empty()
    return _frame(idx, d[idx].astype(int), a_[idx], NO_TARGET_R, KSQ_HOLD)


# ---------------------------------------------------------------- 8.3 DON Donchian-55 break-close
def donchian_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """8.3: "U_i = max high of bars i-55..i-1, L_i = min low of bars i-55..i-1. Long when close_i > U_i and close_{i-1}
    <= U_{i-1}; short mirrored on L." Stop 1.0 x ATR14_i; no target; time exit 24 bars at 4h, 10 at 1d. DOND (1d)
    only, 8.15: "the time exit is the earlier of the frozen hold and the end of the week ... No entry after the cut"
    (`arm_rules_weekcut.capped`: Monday to Thursday settlements enter and exit at Friday's 13:30 New York settlement,
    before the cut; 10 days never fit a week). DON4 is unchanged."""
    m = ctx.mid
    c = m["close"]
    up = m["high"].rolling(DON_N, min_periods=DON_N).max().shift(1)
    dn = m["low"].rolling(DON_N, min_periods=DON_N).min().shift(1)
    with np.errstate(invalid="ignore"):
        long_ = ((c > up) & (c.shift(1) <= up.shift(1))).to_numpy()
        short = ((c < dn) & (c.shift(1) >= dn.shift(1))).to_numpy()
    a_ = atr(m, 14).to_numpy(dtype=float)
    idx = _keep(ctx, np.flatnonzero((long_ | short) & np.isfinite(a_)))
    out = empty() if len(idx) == 0 else \
        _frame(idx, np.where(long_[idx], 1, -1), a_[idx], NO_TARGET_R, DON_HOLD[arm.timeframe])
    return capped(out, ctx.close_ts, arm.timeframe) if (arm.rule, arm.timeframe) in CUT_CAPPED else out


# ---------------------------------------------------------------- 8.4 C30 first touch of the 30-day low, and C30C
def low30_context(ctx: RuleContext) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per decision bar: its trading day, L30(D) (min low of the 30 completed trading days before D, fixed at 17:00
    New York, the start of D) and ATR14(1d) of D-1, both NaN where not yet defined (the input's first day is dropped
    as possibly partial; fewer than 30 completed days before D: NaN)."""
    m = ctx.mid
    day = trading_day(utc_index(m["ts_utc"]))
    d = pd.DataFrame({"day": day, "high": m["high"].to_numpy(float), "low": m["low"].to_numpy(float),
                      "close": m["close"].to_numpy(float)})
    daily = d.groupby("day", sort=True).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    daily = daily.iloc[1:]                                     # the first day may be partial
    l30 = daily["low"].rolling(C30_DAYS, min_periods=C30_DAYS).min().shift(1)
    a_prev = atr(daily, 14).shift(1)
    return (day, l30.reindex(day).to_numpy(dtype=float), a_prev.reindex(day).to_numpy(dtype=float))


def low30_touch(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """8.4: "Touch bar j = the first 15m bar of day D with low_j <= L30(D). Short at market after the close of j; at
    most one per trading day." Stop = entry + 0.5 x ATR14(1d) of D-1, target 1 R, time exit after 460 15m bars."""
    day, l30, a_prev = low30_context(ctx)
    with np.errstate(invalid="ignore"):
        touch = (ctx.mid["low"].to_numpy(float) <= l30) & np.isfinite(a_prev)
    hit = np.flatnonzero(touch)
    if len(hit) == 0:
        return empty()
    first = hit[np.unique(day[hit], return_index=True)[1]]       # the first touch bar of each day
    idx = _keep(ctx, np.sort(first))
    if len(idx) == 0:
        return empty()
    return _frame(idx, np.full(len(idx), -1), C30_STOP_ATR * a_prev[idx], 1.0, C30_HOLD)


def low30_control(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """8.9 / 8.13: "on each 15m close ... of a trading day [the whole trading day], with the common filters, fire when
    hash_u("C30C", close) < 0.00065"; side short; C30A's stop, target and 460-bar time exit, on the same days C30A can
    trade (L30 and ATR14(1d) of D-1 defined), so the pair is read on the same calendar."""
    _, l30, a_prev = low30_context(ctx)
    ok = np.flatnonzero(np.isfinite(l30) & np.isfinite(a_prev))
    close = ctx.close_ts
    idx = np.array([i for i in ok if hash_u(arm.id, close[int(i)]) < C30C_RATE], dtype=int)
    idx = _keep(ctx, idx)
    if len(idx) == 0:
        return empty()
    return _frame(idx, np.full(len(idx), -1), C30_STOP_ATR * a_prev[idx], 1.0, C30_HOLD)


RULES = {"squeeze_release": squeeze_release, "donchian_break": donchian_break, "low30_touch": low30_touch,
         "low30_control": low30_control}
