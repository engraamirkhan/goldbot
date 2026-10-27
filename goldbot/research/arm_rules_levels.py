"""Wave-2 level arm rules (ARM-SPEC `arms-2026-10-25.md` section 8, amendment 8.13): `pdc_break` (KLR: PDC1 1h,
PDC4 4h, section 8.6) and `ob_retest` (OBR15, section 8.5). Section 8.0: "A level, band, pivot, block or anchor exists
only from the close of the bar that confirms it ... Each rule is one function used by the history matrix and live, and
its test proves prefix invariance (appending bars never changes a past candidate)." Mid prices of the decision bars;
ATR = Wilder ATR(14) (`goldbot.features.technical.atr`), `ATR_{j-1}` where the rule says so. Brackets are fixed prices
set at entry (`risk_usd` = |entry - stop|, `target_r`, `max_bars` from the hold).

`pdc_break` (8.6 with 8.13): PDC(D) = the mid close of the last 1m bar of trading day D-1 (trading day = New York date
after +7 h, ending 17:00 New York). "The touch window starts at the second bar of trading day D. The reopen bar opens at
about PDC by construction": the reopen bar is the decision bar holding D's first minute; the window is every later bar
that opens before D's 17:00 New York end. The approach side is the reopen bar's close against PDC; amendment 8.14 (2026-10-26) skips the day when that close
is within 0.05 x ATR_r of PDC (or ATR_r is undefined), as slice G did. Touch bar j = the
first window bar whose range contains PDC; a break-close (close_j at least 0.1 x ATR_{j-1} beyond PDC on the far side
from the approach) trades in the break direction; "otherwise (sweep, reject or stall) no trade that day". Stop = PDC -
side x 1.0 x ATR_{j-1}; target 2 x |entry - stop|; time exit 32 bars. This is the break-close event of
`key_levels_discovery.py` (slice G, `BRK_s1.0`), whose 2010-2017 count 8.13 requires within 10% before going live:
600 / 611 (4h / 1h) against slice G's 600 / 592 with the 8.14 skip (727 / 689 without it).

`ob_retest` (8.5 with 8.13), 15m: swing high at p when high_p > max(high_{p-5..p-1}) and high_p >= max(high_{p+1..p+5}),
known at the close of p+5 and usable from b = p+6 ("confirmed by the close of b-1"); the latest confirmed pivot replaces
the earlier one and a break consumes it ("one break per pivot"). Bullish BOS at b: close_b > P, close_{b-1} <= P. Leg
start s = the lowest-low bar in [p, b]; the leg s..b holds a displacement bar (range >= 1.5 x ATR_{t-1}, body/range >=
0.6, close in the outer 25% in the break direction). Block = the last bearish bar o in [s, b-1], zone [low_o, high_o],
from the close of b if b closes within 02:00-12:00 New York, for 32 bars. Entry bar j = the first bar in (b, b+32] with
low_j <= high_o; close_j <= low_o invalidates it (no trade); else long at the close of j, stop low_o - 0.25 x ATR_{j-1},
target 2R, time exit 32 bars. Bearish mirrored.

Section 8.0 also binds "no entry 16:55-17:00 or 18:00-18:30 New York" (`reopen_ok`, applied here; the common filters of
section 0 are `arm_rules.post_filter`'s). "No entry on CME metals holidays" is RiskGate's holiday window live
(`config/holidays_cme_metals.yaml` covers 2026 on only), so the history run cannot apply it: declared, not coded here.

Controls (8.5, 8.6) are borrowed arms, paired by day at read time (8.13: re-bracketed there): `CONTROLS`.

`level_break` (H-71 G-1, docs/research/prereg-2026-10-26-time-structure.md section 3; arms G1A and its control G1C),
1h: "Level set at the start of trading day D (17:00 New York): PDH, PDL, PDC of D-1; the most recent completed Asia,
London and New York session high and low; previous-week high/low (fixed Friday 17:00 New York); previous-month
high/low; every $10, $50 and $100 round number within 3 ATR14(1h) of the 17:00 price", each known only after its
period closed (`day_levels`); the break-close itself as slice G (`level_events`). Day, week and month levels come from
the decision bars, sessions from the 1m bars; the previous month needs more bars than the engine's 1m memory, so live
reads LEVEL_HISTORY_BARS (stored bars before the window, `engine.exploration_context`)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.resample import mid
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features.technical import atr
from goldbot.research.arm_rules import RuleContext, _frame, empty
from goldbot.research.arms import Arm

NY = "America/New_York"
# 8.6 / 8.5 / 8.11: the borrowed control of each arm (same timeframe); VC1H is a wave-2 control owed by its own lane
CONTROLS = {"PDC1": ("VC1H", "1h"), "PDC4": ("W0", "4h"), "OBR15": ("I0", "15m")}
K_SWING, DISP_ATR, BLOCK_BARS, BUF_ATR = 5, 1.5, 32, 0.25


def trading_day(ts: pd.DatetimeIndex) -> np.ndarray:
    """Trading day of each instant as int days: the New York date after +7 h (8.0 "Clocks")."""
    loc = ts.tz_convert(NY) + pd.Timedelta(hours=7)
    return np.asarray(loc.tz_localize(None).normalize().to_numpy().astype("datetime64[D]")).astype("int64")


def day_end_ns(day: int) -> int:
    """17:00 New York at the end of trading day `day`."""
    d = pd.Timestamp(np.datetime64(int(day), "D"))
    return int(epoch_ns(pd.DatetimeIndex([d.replace(hour=17).tz_localize(NY)]).tz_convert("UTC"))[0])


def reopen_ok(close: pd.DatetimeIndex) -> np.ndarray:
    """8.0: no entry 16:55-17:00 or 18:00-18:30 New York (the reopen spread), on the decision close, inclusive."""
    loc = close.tz_convert(NY)
    m = (loc.hour * 60 + loc.minute).to_numpy()
    return ~(((m >= 16 * 60 + 55) & (m <= 17 * 60)) | ((m >= 18 * 60) & (m <= 18 * 60 + 30)))


# ---------------------------------------------------------------- PDC1 / PDC4
def pdc_events(m: pd.DataFrame, b1: pd.DataFrame) -> pd.DataFrame:
    """Every break-close of the previous day's close (module docstring), unfiltered: idx, side, level, atr."""
    cols = ["idx", "side", "level", "atr"]
    if m.empty or b1.empty:
        return pd.DataFrame(columns=cols)
    m1 = mid(b1).reset_index(drop=True)
    t1 = utc_index(m1["ts_utc"])
    d1 = trading_day(t1)
    c1 = m1["close"].to_numpy(dtype=float)
    first_ns = epoch_ns(t1)
    days, first = np.unique(d1, return_index=True)
    last = np.r_[first[1:], len(d1)] - 1
    start = epoch_ns(utc_index(m["ts_utc"]))
    end = epoch_ns(utc_index(m["visible_at"]))
    h, lo_, c = (m[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    a = atr(m, 14).to_numpy(dtype=float)
    rows = []
    for k in range(1, len(days)):
        if pd.Timestamp(np.datetime64(int(days[k]), "D")).dayofweek >= 5:
            continue
        pdc = c1[last[k - 1]]
        t0 = first_ns[first[k]]
        r = int(np.searchsorted(end, t0, side="right"))            # the reopen bar: the first that closes after t0
        if r >= len(m) or start[r] > t0:
            continue                                                # D's first minute is in no decision bar we hold
        if not (np.isfinite(a[r]) and abs(c[r] - pdc) >= 0.05 * a[r]):
            continue                                                # 8.14: reopen close within 0.05 ATR_r of PDC
        side0 = np.sign(c[r] - pdc)
        stop_at = day_end_ns(int(days[k]))
        for j in range(r + 1, len(m)):
            if start[j] >= stop_at:
                break
            if not (lo_[j] <= pdc <= h[j]):
                continue
            aj = a[j - 1]
            if np.isfinite(aj) and -side0 * (c[j] - pdc) >= 0.1 * aj:
                rows.append((j, int(-side0), pdc, aj))
            break                                                   # first touch only, one a day
    return pd.DataFrame(rows, columns=cols)


def pdc_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ARM-SPEC 8.6 / 8.13 (module docstring): PDC1 (1h), PDC4 (4h)."""
    p = arm.params
    m = ctx.mid
    ev = pdc_events(m, ctx.b1.reset_index(drop=True))
    if ev.empty:
        return empty()
    idx = ev["idx"].to_numpy(dtype=int)
    ev = ev[reopen_ok(ctx.close_ts[idx])]
    if ev.empty:
        return empty()
    idx = ev["idx"].to_numpy(dtype=int)
    side = ev["side"].to_numpy(dtype=int)
    entry = m["close"].to_numpy(dtype=float)[idx]
    stop = ev["level"].to_numpy(dtype=float) - side * float(p.get("stop_atr", 1.0)) * ev["atr"].to_numpy(dtype=float)
    return _frame(idx, side, np.abs(entry - stop), float(p.get("target_r", 2.0)), int(p.get("hold_bars", 32)))


# ---------------------------------------------------------------- OBR15
def ob_events(m: pd.DataFrame) -> pd.DataFrame:
    """Order-block retest entries (module docstring), unfiltered: idx (entry bar j), side, stop."""
    cols = ["idx", "side", "stop"]
    o, h, lo_, c = (m[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    a = atr(m, 14).to_numpy(dtype=float)
    close_ny = utc_index(m["visible_at"]).tz_convert(NY)
    cmin = (close_ny.hour * 60 + close_ny.minute).to_numpy()
    in_hours = (cmin >= 2 * 60) & (cmin <= 12 * 60)
    n, k = len(m), K_SWING
    rows = []
    piv: dict[int, tuple[float, int] | None] = {1: None, -1: None}
    for b in range(1, n):
        p = b - 1 - k                                  # the pivot confirmed by the close of b-1
        if p - k >= 0:
            if h[p] > h[p - k:p].max() and h[p] >= h[p + 1:p + k + 1].max():
                piv[1] = (h[p], p)
            if lo_[p] < lo_[p - k:p].min() and lo_[p] <= lo_[p + 1:p + k + 1].min():
                piv[-1] = (lo_[p], p)
        for side in (1, -1):
            pv = piv[side]
            if pv is None:
                continue
            level, pi = pv
            if not (side * (c[b] - level) > 0 and side * (c[b - 1] - level) <= 0):
                continue
            piv[side] = None                           # one break per pivot
            if not in_hours[b]:
                continue
            seg = slice(pi, b + 1)
            s = pi + int(np.argmin(lo_[seg]) if side > 0 else np.argmax(h[seg]))
            t = np.arange(max(s, 1), b + 1)
            rng = h[t] - lo_[t]
            with np.errstate(invalid="ignore", divide="ignore"):
                pos = np.where(rng > 0, (c[t] - lo_[t]) / np.where(rng > 0, rng, 1.0), np.nan)
                outer = pos >= 0.75 if side > 0 else pos <= 0.25
                disp = (rng >= DISP_ATR * a[t - 1]) & (np.abs(c[t] - o[t]) >= 0.6 * rng) & outer
            if not disp.any():
                continue
            opp = [q for q in range(s, b) if (c[q] < o[q] if side > 0 else c[q] > o[q])]
            if not opp:
                continue
            ob = opp[-1]
            zlo, zhi = lo_[ob], h[ob]
            for j in range(b + 1, min(n, b + 1 + BLOCK_BARS)):
                touched = lo_[j] <= zhi if side > 0 else h[j] >= zlo
                if not touched:
                    continue
                if (c[j] <= zlo) if side > 0 else (c[j] >= zhi):
                    break                              # closed through the zone: invalidated, no trade
                aj = a[j - 1]
                if np.isfinite(aj):
                    rows.append((j, side, (zlo - BUF_ATR * aj) if side > 0 else (zhi + BUF_ATR * aj)))
                break
    return pd.DataFrame(rows, columns=cols).sort_values(["idx", "side"], kind="stable").reset_index(drop=True)


def ob_retest(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """ARM-SPEC 8.5 / 8.13 (module docstring): OBR15."""
    p = arm.params
    m = ctx.mid
    ev = ob_events(m)
    if ev.empty:
        return empty()
    ev = ev[reopen_ok(ctx.close_ts[ev["idx"].to_numpy(dtype=int)])]
    if ev.empty:
        return empty()
    idx = ev["idx"].to_numpy(dtype=int)
    entry = m["close"].to_numpy(dtype=float)[idx]
    return _frame(idx, ev["side"].to_numpy(dtype=int), np.abs(entry - ev["stop"].to_numpy(dtype=float)),
                  float(p.get("target_r", 2.0)), int(p.get("hold_bars", 32)))


# ---------------------------------------------------------------- G1A / G1C (H-71 G-1, prereg 2026-10-26 section 3)
LEVEL_CLASSES = ("pdh", "pdl", "pdc", "asia_hl", "london_hl", "ny_hl", "pwh", "pwl", "pmh", "pml",
                 "round_10_50_100_within_3atr")
LEVEL_WINDOW_FROM = ["17:00", NY]          # the window opens at the trading day's start (3.1: "17:00 New York")
LONDON, TOKYO = "Europe/London", "Asia/Tokyo"
NEAR_REF_ATR = 0.05                        # slice G: a level within 0.05 ATR of the 17:00 price has no approach side
SESSION_MIN_MINUTES = 20                   # slice G `agg`: a session with fewer 1m bars has no high or low
# slice G (`key_levels_discovery.py`, adopted by 3.1): "weekly-and-longer class (window = 5 trading days from the first
# day of the period)": previous-week and previous-month levels trade on the period's first 5 trading days only
PERIOD_LEVEL_DAYS = 5
ROUND_STEP, ROUND_ATR = 10.0, 3.0          # every $10 (so $50 and $100) number within 3 ATR14(1h) of the 17:00 price
# 1h bars the rule reads before its decision bar for live to equal history: the previous month's high needs up to two
# calendar months and ATR14(1h) forgets its start in ~300 bars; more than the engine's 1m memory holds, so the engine
# extends its window from stored bars (`engine.exploration_context`)
LEVEL_HISTORY_BARS = 2_500


def _local_ns(days: np.ndarray, tz: str, hh: int, mm: int) -> np.ndarray:
    """HH:MM on the clock of `tz` on each date (days since the epoch), as epoch ns UTC."""
    loc = pd.DatetimeIndex(np.asarray(days, dtype=np.int64).astype("datetime64[D]")) + pd.Timedelta(hours=hh, minutes=mm)
    return epoch_ns(loc.tz_localize(tz, ambiguous=True, nonexistent="shift_forward").tz_convert("UTC"))


def _sessions(b1: pd.DataFrame, days: np.ndarray) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """High and low of each trading day's Asia (Tokyo 08:00 to London 08:00), London (London 08:00 to New York 08:30)
    and New York (New York 08:30 to 17:00) session, slice G's windows; NaN with fewer than SESSION_MIN_MINUTES 1m bars.
    From the 1m bars: New York 08:30 is not a 1h boundary."""
    out = {k: (np.full(len(days), np.nan), np.full(len(days), np.nan)) for k in ("asia_hl", "london_hl", "ny_hl")}
    if b1.empty:
        return out
    m1 = mid(b1).reset_index(drop=True)
    t = epoch_ns(utc_index(m1["ts_utc"]))
    h, lo = m1["high"].to_numpy(dtype=float), m1["low"].to_numpy(dtype=float)
    edges = {"tok": _local_ns(days, TOKYO, 8, 0), "ldn": _local_ns(days, LONDON, 8, 0),
             "nyo": _local_ns(days, NY, 8, 30), "end": _local_ns(days, NY, 17, 0)}
    for name, (a, b) in (("asia_hl", ("tok", "ldn")), ("london_hl", ("ldn", "nyo")), ("ny_hl", ("nyo", "end"))):
        i, j = np.searchsorted(t, edges[a]), np.searchsorted(t, edges[b])
        for k in np.flatnonzero(j - i >= SESSION_MIN_MINUTES):
            out[name][0][k], out[name][1][k] = h[i[k]:j[k]].max(), lo[i[k]:j[k]].min()
    return out


def _periods(key: np.ndarray, trade: np.ndarray, hi: np.ndarray, lo: np.ndarray) -> dict[int, tuple[float, float]]:
    """High and low of each period (`key` per trading day) but the input's first one, which it may hold only part of."""
    out: dict[int, tuple[float, float]] = {}
    for p in np.unique(key[1:]) if len(key) else []:
        sel = (key == p) & trade
        if p != key[0] and sel.any():
            out[int(p)] = (float(hi[sel].max()), float(lo[sel].min()))
    return out


def _nth_trading_day(key: np.ndarray, trade: np.ndarray) -> np.ndarray:
    """Each day's ordinal among the trading days of its period (`key` per day, 1 = the period's first trading day)."""
    out = np.zeros(len(key), dtype=np.int64)
    seen: dict[int, int] = {}
    for i in np.flatnonzero(trade):
        seen[int(key[i])] = out[i] = seen.get(int(key[i]), 0) + 1
    return out


def day_levels(m: pd.DataFrame, b1: pd.DataFrame, classes: list[str]) -> dict[int, dict[str, Any]]:
    """Per trading day D (days since the epoch) with a trading day before it in `m`: `levels`, the (class, value)
    pairs set at D's start (3.1); `ref`, the 17:00 New York price (the close of D-1's last bar), and `ref_atr`, ATR14
    there; `start`, D's first bar; `start_ts`, D-1's last close. Point in time: each level is of a period closed by
    D's start (a week at Friday 17:00 New York, a month at its last trading day's). A day, week or month the input
    holds only part of (its first trading day, that day's week and month) gives no level: never a partial high.
    Previous-week and previous-month levels exist only on the period's first PERIOD_LEVEL_DAYS (5) trading days, as
    slice G's "window = 5 trading days from the first day of the period"."""
    t = utc_index(m["ts_utc"])
    day = trading_day(t)
    h, lo_, c = (m[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    a = atr(m, 14).to_numpy(dtype=float)
    days, first = np.unique(day, return_index=True)
    if len(days) < 2:
        return {}
    last = np.r_[first[1:], len(day)] - 1
    dhi, dlo = np.maximum.reduceat(h, first), np.minimum.reduceat(lo_, first)
    dow = pd.DatetimeIndex(days.astype("datetime64[D]")).dayofweek.to_numpy()
    trade = dow < 5
    week = days - dow                                                   # the Monday of each trading day's week
    month = days.astype("datetime64[D]").astype("datetime64[M]").astype(np.int64)
    weeks, months = _periods(week, trade, dhi, dlo), _periods(month, trade, dhi, dlo)
    nth_week, nth_month = _nth_trading_day(week, trade), _nth_trading_day(month, trade)
    sess = _sessions(b1, days) if {"asia_hl", "london_hl", "ny_hl"} & set(classes) else {}
    close_ts = utc_index(m["visible_at"])
    table: dict[int, dict[str, Any]] = {}
    for k in range(1, len(days)):
        before = np.flatnonzero(trade[:k])
        if not trade[k] or not len(before):
            continue
        p = int(before[-1])                                             # D-1: the trading day before D
        ref, ref_atr = float(c[last[p]]), float(a[last[p]])
        lv: list[tuple[str, float]] = []
        if p > 0:                                                       # the input's first day may be partial
            lv += [(n, float(v)) for n, v in (("pdh", dhi[p]), ("pdl", dlo[p]), ("pdc", c[last[p]])) if n in classes]
            for n in ("asia_hl", "london_hl", "ny_hl"):
                if n in classes:
                    lv += [(n, float(v)) for v in (sess[n][0][p], sess[n][1][p]) if np.isfinite(v)]
        pw = weeks.get(int(week[k]) - 7) if nth_week[k] <= PERIOD_LEVEL_DAYS else None
        pm = months.get(int(month[k]) - 1) if nth_month[k] <= PERIOD_LEVEL_DAYS else None
        if pw is not None:
            lv += [(n, v) for n, v in (("pwh", pw[0]), ("pwl", pw[1])) if n in classes]
        if pm is not None:
            lv += [(n, v) for n, v in (("pmh", pm[0]), ("pml", pm[1])) if n in classes]
        if "round_10_50_100_within_3atr" in classes and np.isfinite(ref_atr):
            lo_r = np.ceil((ref - ROUND_ATR * ref_atr) / ROUND_STEP) * ROUND_STEP
            lv += [("round_10_50_100_within_3atr", float(v))
                   for v in np.arange(lo_r, ref + ROUND_ATR * ref_atr + 1e-9, ROUND_STEP)]
        table[int(days[k])] = {"levels": lv, "ref": ref, "ref_atr": ref_atr, "start": int(first[k]),
                               "start_ts": close_ts[last[p]]}
    return table


def _window_end(arm: Arm) -> tuple[int, int, str]:
    """The window's end on its clock, from `level_window_local` [[17:00, America/New_York], [HH:MM, zone]]. It opens
    at the trading day's start (3.1), so any other opening is refused."""
    w = arm.params.get("level_window_local")
    if not isinstance(w, list) or len(w) != 2 or list(w[0]) != LEVEL_WINDOW_FROM:
        raise ValueError(f"level_window_local must open at 17:00 America/New_York (the trading day's start), got {w!r}")
    hhmm, tz = w[1]
    return int(hhmm[:2]), int(hhmm[3:]), str(tz)


def level_events(m: pd.DataFrame, b1: pd.DataFrame, arm: Arm) -> pd.DataFrame:
    """G1A / G1C break-closes, unfiltered: idx (the touch bar), side, level, atr (ATR14 of bar idx - 1), cls.

    3.1: the first 1h bar of day D opening in the window (D's start to `level_window_local`'s end) whose range touches
    a level and whose close is at least `break_atr` x ATR14 beyond it; side = the break direction; one a day. As
    slice G (`key_levels_discovery.py`, `BRK_s1.0`): only a level's first touch in the window counts (a touch that does
    not break spends the level), the approach side is the 17:00 price against the level, the ATR is the bar's before
    the touch, and a level within NEAR_REF_ATR x ATR of the 17:00 price is skipped. Declared choice: when one bar
    breaks several levels (all in one direction), the trade is on the one nearest its close.
    G1C (`control: random_distance_level`, 3.2): each day one of the day's real levels by `hash_u(arm id, D's start)`
    and a sign by `hash_u(arm id + ":sign", D's start)`; control level = ref + sign x |ref - that level|."""
    from goldbot.research.arm_rules import hash_u
    cols = ["idx", "side", "level", "atr", "cls"]
    p = arm.params
    classes = list(p.get("levels", LEVEL_CLASSES))
    unknown = sorted(set(classes) - set(LEVEL_CLASSES))
    if unknown:
        raise ValueError(f"unknown level classes {unknown}; known: {LEVEL_CLASSES}")
    eh, em, etz = _window_end(arm)
    brk, control = float(p.get("break_atr", 0.1)), p.get("control")
    if m.empty:
        return pd.DataFrame(columns=cols)
    t = utc_index(m["ts_utc"])
    start_ns, day = epoch_ns(t), trading_day(t)
    h, lo_, c = (m[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    a = atr(m, 14).to_numpy(dtype=float)
    rows = []
    for d, info in day_levels(m, b1, classes).items():
        ref, ref_atr = info["ref"], info["ref_atr"]
        if not np.isfinite(ref_atr):
            continue
        levels = [(n, v) for n, v in info["levels"] if abs(v - ref) >= NEAR_REF_ATR * ref_atr]
        if control is not None and levels:
            key = pd.Timestamp(info["start_ts"])
            _, v = levels[min(int(hash_u(arm.id, key) * len(levels)), len(levels) - 1)]
            sign = 1.0 if hash_u(arm.id + ":sign", key) < 0.5 else -1.0
            levels = [("control", ref + sign * abs(ref - v))]
        i0 = info["start"]
        i1 = i0 + int(np.searchsorted(start_ns[i0:], _local_ns(np.array([d]), etz, eh, em)[0]))
        win = np.arange(i0, i1)[day[i0:i1] == d]
        best: tuple[int, float, int, float, float, str] | None = None
        for name, lv in levels:
            side0 = 1 if ref > lv else -1
            hit = win[(lo_[win] <= lv) & (lv <= h[win])]
            if not len(hit):
                continue
            j = int(hit[0])
            aj = a[j - 1] if j > 0 else np.nan
            if not (np.isfinite(aj) and -side0 * (c[j] - lv) >= brk * aj):
                continue                                          # the first touch is no break-close: spent
            cand = (j, abs(c[j] - lv), -side0, lv, float(aj), name)
            if best is None or cand[:2] < best[:2]:
                best = cand
        if best is not None:
            rows.append((best[0], best[2], best[3], best[4], best[5]))
    return pd.DataFrame(rows, columns=cols).sort_values("idx", kind="stable").reset_index(drop=True)


def level_break(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """H-71 G-1 (prereg-2026-10-26-time-structure.md section 3): G1A and its control G1C (`level_events`). Market entry
    after the touch bar's close; stop = level - side x `stop_atr` x ATR14(1h), a fixed price; target `target_r` R;
    time exit `hold_bars` bars. The stop band and the common filters are `arm_rules.post_filter`'s."""
    p = arm.params
    m = ctx.mid
    ev = level_events(m, ctx.b1.reset_index(drop=True), arm)
    if ev.empty:
        return empty()
    idx = ev["idx"].to_numpy(dtype=int)
    side = ev["side"].to_numpy(dtype=int)
    entry = m["close"].to_numpy(dtype=float)[idx]
    stop = ev["level"].to_numpy(dtype=float) - side * float(p.get("stop_atr", 1.0)) * ev["atr"].to_numpy(dtype=float)
    return _frame(idx, side, np.abs(entry - stop), float(p.get("target_r", 2.0)), int(p.get("hold_bars", 32)))
