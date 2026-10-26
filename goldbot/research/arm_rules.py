"""Candidate generators of the exploration arms (ARM-SPEC 2026-10-25, docs/research/arms-2026-10-25.md). Each returns a
frame of candidates on the arm's decision bars: idx, side, risk_usd (the candidate's own stop distance in USD, R),
target_r (target in R), max_bars (the time barrier, in `triple_barrier_1m`'s count: the exit is the close of bar
idx + 1 + max_bars, so a spec hold of N bars is max_bars N - 1). Decision on the close of bar idx (`visible_at`), every
value from bars <= idx. The common filters (tier-1 blackout, no entry 20:45-22:15 UTC, none on Friday from 19:00 UTC)
apply to every arm, the random controls included. Existing specialists run their own rule code."""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.resample import mid
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features.technical import atr
from goldbot.research.arms import CLOCK_BOX_RULES, Arm, local_window

SEED_PREFIX = "goldbot-arms-2026-10-25"
GBP_PER_USD = 0.752
LONDON, NEW_YORK = "Europe/London", "America/New_York"
COLUMNS = ["idx", "side", "risk_usd", "target_r", "max_bars"]


@dataclass
class RuleContext:
    bars: pd.DataFrame            # decision bars, store schema (bid_/ask_ columns), positional index
    b1: pd.DataFrame              # the 1m bars, store schema
    blackout_lo: np.ndarray       # tier-1 blackout windows [lo, hi), epoch ns
    blackout_hi: np.ndarray
    # point-in-time tables a `specialist` arm's family reads, as research.pipeline.prepare's `ctx` (e.g. the engine's
    # carried smc replica state, ctx["smc_replica_state"]); None: none (research builds from the series start)
    spec_ctx: dict[str, Any] | None = None

    @property
    def mid(self) -> pd.DataFrame:
        return mid(self.bars).reset_index(drop=True)

    @property
    def close_ts(self) -> pd.DatetimeIndex:
        return utc_index(self.bars["visible_at"])


def empty() -> pd.DataFrame:
    return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int), "risk_usd": pd.Series(dtype=float),
                         "target_r": pd.Series(dtype=float), "max_bars": pd.Series(dtype=int)})


def hash_u(arm_id: str, close: pd.Timestamp) -> float:
    """The seeded, restart-safe draw of the ARM-SPEC (identical in history and live)."""
    key = f"{SEED_PREFIX}|{arm_id}|{close:%Y-%m-%dT%H:%M}Z"
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2 ** 64


def hash_side(arm_id: str, close: pd.Timestamp) -> int:
    return 1 if hash_u(arm_id, close) < 0.5 else -1


def _minutes_utc(ts: pd.DatetimeIndex) -> np.ndarray:
    return (ts.hour * 60 + ts.minute).to_numpy()


def parse_hhmm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def in_window(close: pd.DatetimeIndex, window: list[str] | None) -> np.ndarray:
    """Decision close time of day (UTC) within [from, to] inclusive; None: always."""
    if not window:
        return np.ones(len(close), dtype=bool)
    m = _minutes_utc(close)
    return (m >= parse_hhmm(window[0])) & (m <= parse_hhmm(window[1]))


def arm_window(close: pd.DatetimeIndex, params: dict[str, Any]) -> np.ndarray:
    """The arm's decision window on its closes, [from, to] inclusive: `window_local` on city clocks, converted per date
    so it follows Europe/London's and America/New_York's own DST switches (research note time-structure/sessions.md
    A-4: a UTC window slides an hour against London in winter), else the pre-registered `window_utc` in UTC hours
    (ARM-SPEC: "Clock times are UTC unless a local session clock is named"), else always."""
    w = local_window(params)
    if w is None:
        return in_window(close, params.get("window_utc"))
    return w.covers(close, include_close=True)


def common_filter_ok(close: pd.DatetimeIndex, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """True where a new entry is allowed: not in a tier-1 blackout, not 20:45-22:15 UTC, not Friday from 19:00 UTC."""
    m = _minutes_utc(close)
    rollover = (m >= 20 * 60 + 45) & (m <= 22 * 60 + 15)
    friday = (close.dayofweek.to_numpy() == 4) & (m >= 19 * 60)
    ns = epoch_ns(close)
    black = np.zeros(len(ns), dtype=bool)
    if len(lo):
        # a window [lo, hi) covers t when the last lo <= t is still before its hi: sweep over sorted t
        order = np.argsort(ns, kind="stable")
        t = ns[order]
        delta = np.zeros(len(t) + 1, dtype=np.int64)
        np.add.at(delta, np.searchsorted(t, lo, side="left"), 1)
        np.add.at(delta, np.searchsorted(t, hi, side="left"), -1)
        covered = np.cumsum(delta)[:-1] > 0
        black[order] = covered
    return ~(rollover | friday | black)


def blackout_windows(cal: pd.DataFrame | None) -> tuple[np.ndarray, np.ndarray]:
    """Tier-1 blackout windows [event - 15 min, event + 30 min) as known point in time: a calendar row holds from its
    available_utc until the event's next row; only rows in status `scheduled` black out (the E-1 rule of
    scripts/count_owed_events.py)."""
    if cal is None or len(cal) == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    c = cal[cal["tier"].astype(int) == 1].copy()
    c["_av"] = epoch_ns(utc_index(c["available_utc"]))
    c["_ev"] = epoch_ns(utc_index(c["event_utc"]))
    c = c.sort_values(["event_id", "_av"], kind="stable")
    nxt = c.groupby("event_id", sort=False)["_av"].shift(-1).to_numpy(dtype=float)
    valid_to = np.full(len(nxt), np.iinfo(np.int64).max, dtype=np.int64)
    valid_to[~np.isnan(nxt)] = nxt[~np.isnan(nxt)].astype(np.int64)
    sched = (c["status"].astype(str) == "scheduled").to_numpy()
    lo = np.maximum(c["_ev"].to_numpy() - pd.Timedelta(minutes=15).value, c["_av"].to_numpy())
    hi = np.minimum(c["_ev"].to_numpy() + pd.Timedelta(minutes=30).value, valid_to)
    keep = sched & (lo < hi)
    return lo[keep].astype(np.int64), hi[keep].astype(np.int64)


# ---------------------------------------------------------------- random brackets (S0, S0R, I0, W0)
def random_bracket(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Seeded random control: a fixed schedule of decision closes (`minutes` of the hour within `window_utc`, Mon-Fri),
    side from the arm's hash, a fixed bracket (`stop_usd` or `stop_atr` x ATR of the decision bars)."""
    p = arm.params
    close = ctx.close_ts
    mins = close.minute.to_numpy()
    ok = np.isin(mins, p.get("minutes", [0])) & arm_window(close, p) & (close.dayofweek.to_numpy() < 5)
    ok &= common_filter_ok(close, ctx.blackout_lo, ctx.blackout_hi)
    idx = np.flatnonzero(ok)
    if len(idx) == 0:
        return empty()
    side = np.array([hash_side(arm.id, close[int(i)]) for i in idx])
    if "stop_usd" in p:
        risk = np.full(len(idx), float(p["stop_usd"]))
    else:
        risk = float(p["stop_atr"]) * atr(ctx.mid, 14).to_numpy()[idx]
    return _frame(idx, side, risk, p["target_r"], p["hold_bars"])


def _frame(idx: np.ndarray, side: np.ndarray, risk: np.ndarray, target_r: Any, hold_bars: Any) -> pd.DataFrame:
    """Candidates with finite positive risk; `hold_bars` (spec count) becomes triple_barrier_1m's max_bars."""
    n = len(idx)
    out = pd.DataFrame({"idx": np.asarray(idx, dtype=int), "side": np.asarray(side, dtype=int),
                        "risk_usd": np.asarray(risk, dtype=float),
                        "target_r": np.broadcast_to(np.asarray(target_r, dtype=float), (n,)).copy(),
                        "max_bars": np.broadcast_to(np.asarray(hold_bars, dtype=int) - 1, (n,)).copy()})
    return out[np.isfinite(out["risk_usd"]) & (out["risk_usd"] > 0) & np.isfinite(out["target_r"])].reset_index(drop=True)


def post_filter(arm: Arm, ctx: RuleContext, cands: pd.DataFrame) -> pd.DataFrame:
    """The arm's decision window, the common filters and its stop band / cap (candidates beyond them are skipped, logged
    in the runner as stop_lt_floor / stop_gt_cap)."""
    if cands.empty:
        return cands
    p = arm.params
    close = ctx.close_ts[cands["idx"].to_numpy()]
    ok = arm_window(close, p) & common_filter_ok(close, ctx.blackout_lo, ctx.blackout_hi)
    if "min_stop_usd" in p:
        ok &= cands["risk_usd"].to_numpy() >= float(p["min_stop_usd"]) - 1e-9
    if "max_stop_usd" in p:
        ok &= cands["risk_usd"].to_numpy() <= float(p["max_stop_usd"]) + 1e-9
    return cands[ok].reset_index(drop=True)


# ---------------------------------------------------------------- S1 session-VWAP fade
def session_vwap_fade(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ARM-SPEC 1.1: fade the first 5m close outside the session VWAP +/- 2 sigma band (anchor: the first bar at or after
    London open each weekday; tick-count weights; defined from the 6th bar after the anchor; no signal after 20:00 UTC and
    none before 30 min after London open). Stop clip(1.0 ATR5m, $3, $6); target the VWAP (`target: vwap`, kept if 1-3 R,
    capped at 3 R) or 1.0 R; at most one signal per side per 6 bars."""
    p = arm.params
    m = ctx.mid
    ts = utc_index(m["ts_utc"])
    loc = ts.tz_convert(LONDON)
    lmin = (loc.hour * 60 + loc.minute).to_numpy()
    day = np.asarray(loc.normalize().tz_localize(None).to_numpy().astype("datetime64[D]")).astype("int64")
    live = (lmin >= 8 * 60) & (loc.dayofweek.to_numpy() < 5)
    tp = ((m["high"] + m["low"] + m["close"]) / 3).to_numpy(dtype=float)
    w = m["tick_count"].to_numpy(dtype=float)
    df = pd.DataFrame({"day": day, "w": np.where(live, w, 0.0), "wt": np.where(live, w * tp, 0.0),
                       "wt2": np.where(live, w * tp * tp, 0.0), "live": live})
    g = df.groupby("day")
    sw, swt, swt2 = g["w"].cumsum().to_numpy(), g["wt"].cumsum().to_numpy(), g["wt2"].cumsum().to_numpy()
    k = (df["live"].astype(int).groupby(df["day"]).cumsum().to_numpy() - 1)        # 0 at the anchor bar
    with np.errstate(invalid="ignore", divide="ignore"):
        vwap = swt / sw
        sigma = np.sqrt(np.maximum(swt2 / sw - vwap * vwap, 0.0))
    defined = live & (k >= 6) & np.isfinite(vwap) & (sigma > 0)
    c = m["close"].to_numpy(dtype=float)
    lo_b, hi_b = vwap - 2 * sigma, vwap + 2 * sigma
    prev_def = np.concatenate(([False], defined[:-1])) & (np.concatenate(([0], k[:-1])) >= 6)
    prev_c = np.concatenate(([np.nan], c[:-1]))
    prev_lo, prev_hi = np.concatenate(([np.nan], lo_b[:-1])), np.concatenate(([np.nan], hi_b[:-1]))
    with np.errstate(invalid="ignore"):
        long_ = defined & prev_def & (c < lo_b) & (prev_c >= prev_lo)
        short_ = defined & prev_def & (c > hi_b) & (prev_c <= prev_hi)
    # "no signal after 20:00 UTC" on the bar's own start: on GMT the London day is the UTC day, so the 23:55 bar is in
    # the session and its close (00:00) is minute 0 of the next UTC day, which a test on the close's minute let through
    after_open = (lmin + 5) >= 8 * 60 + 30            # the bar closes at least 30 min after London open
    timing = after_open & (_minutes_utc(ts) + 5 <= 20 * 60)
    a5 = atr(m, 14).to_numpy()
    rows = []
    for side, flag in ((1, long_), (-1, short_)):
        last = -10 ** 9
        for i in np.flatnonzero(flag & timing):
            if i - last < 6:
                continue
            last = int(i)
            stop = float(np.clip(a5[i], p.get("stop_min", 3.0), p.get("stop_max", 6.0))) if np.isfinite(a5[i]) else np.nan
            if p.get("target") == "vwap":
                dist = side * (vwap[i] - c[i])
                tr = dist / stop if np.isfinite(stop) else np.nan
                if not (tr >= 1.0):
                    continue
                tr = min(tr, 3.0)
            else:
                tr = float(p.get("target_r", 1.0))
            rows.append((int(i), side, stop, tr))
    if not rows:
        return empty()
    r = sorted(rows)
    return _frame(np.array([x[0] for x in r]), np.array([x[1] for x in r]), np.array([x[2] for x in r]),
                  np.array([x[3] for x in r]), p["hold_bars"])


# ---------------------------------------------------------------- S2 opening-range break
def opening_range_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ARM-SPEC 1.2: range = high/low of the first `range_bars` 5m bars from London (08:00 London) and NY (08:30 New
    York) open; the first 5m close beyond it within 18 bars after the range ends; one trade per session; stop at the
    range mid; target 2 R; hard flat at 20:00 UTC (the hold is cut to it)."""
    p = arm.params
    m = ctx.mid
    ts = utc_index(m["ts_utc"])
    step = pd.Timedelta(minutes=5)
    n = int(p["range_bars"])
    h, lo_, c = (m[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    close = ctx.close_ts
    pos = pd.Series(np.arange(len(ts)), index=ts)
    rows = []
    for day in pd.DatetimeIndex(ts.normalize().unique()):
        if day.dayofweek >= 5:
            continue
        for tz, hhmm in ((LONDON, (8, 0)), (NEW_YORK, (8, 30))):
            local = pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=hhmm[0], minute=hhmm[1], tz=tz)
            start = local.tz_convert("UTC")
            first = int(pos.index.searchsorted(start))
            if first + n >= len(ts) or ts[first + n - 1] != start + (n - 1) * step:
                continue                                         # the range's bars are not all there
            rng = slice(first, first + n)
            rh, rl = h[rng].max(), lo_[rng].min()
            end = first + n
            for t in range(end, min(end + 18, len(ts))):
                if ts[t] != start + (t - first) * step:
                    break                                         # a gap inside the break window ends the search
                side = 1 if c[t] > rh else -1 if c[t] < rl else 0
                if not side:
                    continue
                flat = close[t].normalize() + pd.Timedelta(hours=20)
                left = int((flat - close[t]) / step)
                hold = min(int(p["hold_bars"]), left)
                if hold >= 2:
                    rows.append((t, side, abs(c[t] - (rh + rl) / 2), float(p["target_r"]), hold))
                break
    if not rows:
        return empty()
    rows.sort()
    out = _frame(np.array([r[0] for r in rows]), np.array([r[1] for r in rows]), np.array([r[2] for r in rows]),
                 np.array([r[3] for r in rows]), np.array([r[4] for r in rows]))
    return out


# ---------------------------------------------------------------- S3 / MSA15 sweep_absorb (existing rule code)
def sweep_absorb_rule(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ARM-SPEC 1.4: `sweep_absorb`'s own scan (depth 0.5, reclaim 2, gap 4, stop = extreme + 0.25 ATR kept at 0.6-1.0
    ATR, London/NY window) on the arm's timeframe; the $ band, target and hold come from the arm."""
    from goldbot.specialists.sweep_absorb import SweepAbsorbSpecialist
    p = arm.params
    scan = SweepAbsorbSpecialist()._scan(ctx.mid)
    if scan.empty:
        return empty()
    return _frame(scan["idx"].to_numpy(), scan["side"].to_numpy(), scan["risk_usd"].to_numpy(),
                  p["target_r"], p["hold_bars"])


# ---------------------------------------------------------------- S4 / I2 / W2 / MB box_break_close (existing rule code)
def box_break_close_rule(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ARM-SPEC 1.4 / 2: `box_break_close`'s own decision (`decide_with_stop`) on the arm's timeframe; I2A forces the money
    flow check on (`force_mfi`), I2B sets `stop_atr` 1.0; target 2 R."""
    from goldbot.specialists.box_break_close import STOP_ATR, BoxBreakCloseSpecialist
    p = arm.params
    c = BoxBreakCloseSpecialist().candidates_with_risk(ctx.mid, force_mfi=bool(p.get("force_mfi", False)),
                                                      stop_atr=float(p.get("stop_atr", STOP_ATR)))
    if c.empty:
        return empty()
    return _frame(c["idx"].to_numpy(), c["side"].to_numpy(), c["risk_usd"].to_numpy(), p.get("target_r", 2.0),
                  p["hold_bars"])


# ---------------------------------------------------------------- S5 EMA pullback
def ema_pullback(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ARM-SPEC 1.3 on 1m bars: with the last CLOSED 15m bar's trend (EMA20 > EMA50 and close > EMA50, mirrored), a pullback
    to EMA20(1m) (low <= EMA20) that closes back above it as a bullish bar; mirrored; one signal per side per 10 bars."""
    from goldbot.data.resample import resample_bars
    p = arm.params
    m = ctx.mid
    c, o, h, lo_ = (m[k].to_numpy(dtype=float) for k in ("close", "open", "high", "low"))
    ema = pd.Series(c).ewm(span=20, adjust=False).mean().to_numpy()
    b15 = mid(resample_bars(ctx.bars, "15m")).reset_index(drop=True)
    c15 = b15["close"]
    e20, e50 = c15.ewm(span=20, adjust=False).mean(), c15.ewm(span=50, adjust=False).mean()
    trend = np.where((e20 > e50) & (c15 > e50), 1, np.where((e20 < e50) & (c15 < e50), -1, 0))
    vis15 = epoch_ns(utc_index(b15["visible_at"]))
    j = np.searchsorted(vis15, epoch_ns(ctx.close_ts), side="right") - 1          # the last 15m bar closed by now
    tr = np.where(j >= 0, trend[np.maximum(j, 0)], 0)
    with np.errstate(invalid="ignore"):
        up = (tr == 1) & (lo_ <= ema) & (c > ema) & (c > o)
        dn = (tr == -1) & (h >= ema) & (c < ema) & (c < o)
    rows = []
    for side, flag in ((1, up), (-1, dn)):
        last = -10 ** 9
        for i in np.flatnonzero(flag):
            if i - last < 10:
                continue
            last = int(i)
            rows.append((int(i), side))
    if not rows:
        return empty()
    rows.sort()
    idx = np.array([r[0] for r in rows])
    return _frame(idx, np.array([r[1] for r in rows]), np.full(len(idx), float(p["stop_usd"])), p["target_r"],
                  p["hold_bars"])


# ---------------------------------------------------------------- W1 tsmom (existing rule code)
def tsmom_rule(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ARM-SPEC 3: the `tsmom` specialist's own candidates and barrier ATR for `params.config` (its stop 1.5 ATR, target
    3.0 ATR = 2 R, its own `max_bars` clock); every day is a candidate (no meta filter)."""
    from goldbot.data.resample import resample_bars
    from goldbot.research.pipeline import build_decision_frame
    from goldbot.specialists import SPECIALISTS
    spec = SPECIALISTS["tsmom"](timeframe=arm.timeframe, **arm.params.get("config", {}))
    context = {lab: resample_bars(ctx.b1, tf) for lab, tf in (("h1", "1h"), ("h4", "4h"), ("d1", "1d"))}
    m, x = build_decision_frame(ctx.bars.reset_index(drop=True), context)
    cands = spec.candidates_in_context(m, x, context, None)
    if cands.empty:
        return empty()
    own = spec.barrier_atr(m, context)
    a = (atr(m, 14) if own is None else own).to_numpy(dtype=float)
    ls = spec.label_spec
    idx = cands["idx"].to_numpy(dtype=int)
    out = _frame(idx, cands["side"].to_numpy(), ls.stop_atr * a[idx], ls.target_atr / ls.stop_atr, ls.max_bars + 1)
    return out


# ---------------------------------------------------------------- wave 1 (arms expansion 2026-10-26): any specialist
def specialist(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Arms expansion 2026-10-26, section 3: the named specialist's own candidates and barrier form
    (`goldbot.research.arm_rules_specialist`)."""
    from goldbot.research.arm_rules_specialist import specialist_rule
    return specialist_rule(arm, ctx)


# ---------------------------------------------------------------- wave 2 (ARM-SPEC section 8): level rules
def pdc_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ARM-SPEC 8.6 / 8.13: PDC1 / PDC4 (`goldbot.research.arm_rules_levels`)."""
    from goldbot.research.arm_rules_levels import pdc_break as rule
    return rule(arm, ctx)


def ob_retest(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ARM-SPEC 8.5 / 8.13: OBR15 (`goldbot.research.arm_rules_levels`)."""
    from goldbot.research.arm_rules_levels import ob_retest as rule
    return rule(arm, ctx)


def channel(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ARM-SPEC section 8 (wave 2): KSQ, DON, C30 and C30C (`goldbot.research.arm_rules_channels`)."""
    from goldbot.research.arm_rules_channels import RULES
    return RULES[arm.rule](arm, ctx)


def clock_box(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Wave 2 lane W2d: WFRI, KA15/XC15 and the H-80 box family (`goldbot.research.arm_rules_clock_box`)."""
    from goldbot.research.arm_rules_clock_box import RULE_FUNCS as CLOCK_BOX
    return CLOCK_BOX[arm.rule](arm, ctx)


def band_rule(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """Time-structure band breaks F1A/F1C/F2A/F2C (`goldbot.research.arm_rules_bands`)."""
    from goldbot.research.arm_rules_bands import RULES as BANDS
    return BANDS[arm.rule](arm, ctx)




RULE_FUNCS: dict[str, Callable[[Arm, RuleContext], pd.DataFrame]] = {
    "random_bracket": random_bracket, "session_vwap_fade": session_vwap_fade,
    "opening_range_break": opening_range_break, "sweep_absorb": sweep_absorb_rule,
    "box_break_close": box_break_close_rule, "ema_pullback": ema_pullback, "tsmom": tsmom_rule,
    "specialist": specialist, "pdc_break": pdc_break, "ob_retest": ob_retest,
    "level_break": lambda arm, ctx: __import__("goldbot.research.arm_rules_levels", fromlist=["x"]).level_break(arm, ctx),
    **dict.fromkeys(("squeeze_release", "donchian_break", "low30_touch", "low30_control"), channel),
    **dict.fromkeys(CLOCK_BOX_RULES, clock_box),
    **dict.fromkeys(("hurst_band_break", "hurst_band_break_control", "htf_stretch_break", "htf_stretch_break_control"),
                    band_rule),
}


def candidates(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """The arm's candidates after its window, the common filters and its stop band."""
    if arm.status != "run":
        raise ValueError(f"{arm.id} is blocked: {arm.note}")
    raw = RULE_FUNCS[arm.rule](arm, ctx)
    return post_filter(arm, ctx, raw)


def lookback_bars(arm: Arm, default: int) -> int:
    """Bars of the arm's timeframe its rule needs live for its candidates to equal the history run's (bounded by the
    engine's `exploration.lookback`): a `specialist` family's warm-up (`arm_rules_specialist.WARMUP_MIN`), a band
    rule's LOOKBACK_BARS, else `default`."""
    if arm.rule == "specialist":
        from goldbot.research.arm_rules_specialist import warmup_bars
        return max(default, warmup_bars(arm))
    from goldbot.research.arm_rules_bands import LOOKBACK_BARS
    return max(default, LOOKBACK_BARS.get(arm.rule, 0))
