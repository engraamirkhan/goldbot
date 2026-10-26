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

Controls (8.5, 8.6) are borrowed arms, paired by day at read time (8.13: re-bracketed there): `CONTROLS`."""
from __future__ import annotations

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
