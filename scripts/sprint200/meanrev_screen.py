"""Sprint-200 lane `meanrev`: 33 mean-reversion / level strategies, explore then confirm (frozen rules:
docs/research/sprint200/meanrev-prereg.md, committed before this script ran).

    uv run python scripts/sprint200/meanrev_screen.py explore --raw ../../raw   # 2010-2017, all 33
    uv run python scripts/sprint200/meanrev_screen.py confirm --raw ../../raw   # 2018-2025-09, explore passers only

Explore writes docs/research/sprint200/meanrev-explore.json (aggregates only); confirm refuses any id that file does not
record as a passer and writes meanrev-confirm.json. Prices are 1m mids; costs are the pinned cost of record
(cor-2026-10-31.2, ICM, ADR 0064 overshoot) plus swap; every trade gets 4 matched random-entry twins. Nothing at or
after 2025-10-01 is read (asserted). At most 2 worker processes (sprint Mac-load rule).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.timeutil import NY_PLUS_7, epoch_ns, server_wall  # noqa: E402
from goldbot.labels import SwapSpec, rollover_nights  # noqa: E402
from goldbot.research import arms as A  # noqa: E402
from goldbot.research.metrics import DsrInputs  # noqa: E402

COST_VERSION = "cor-2026-10-31.2"
BROKER = "icm"
STOP_FLOOR = 2.50
MIN_TARGET_R = 0.5
N_TWINS = 4
SEED = 20261102
K_SPRINT = 3229               # program ~3,029 + the sprint's 200 (prereg s.2); + this lane's extra variants (0)
K_STRICT = 4000
NS_MIN = 60_000_000_000
NS_H = 60 * NS_MIN
ROLL_LO, ROLL_HI = 23 * 60 + 45, 65          # server minute-of-day window [23:45, 01:05) with no entry
OUT_DIR = ROOT / "docs" / "research" / "sprint200"
WINDOWS = {"explore": (pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC"), 2010, 2017),
           "confirm": (pd.Timestamp("2018-01-01", tz="UTC"), pd.Timestamp("2025-10-01", tz="UTC"), 2017, 2025)}


# ---------------------------------------------------------------------------------------------------------- data
@dataclass
class Data:
    ts: np.ndarray                 # int64 UTC ns, 1m bar start
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    w: np.ndarray                  # tick weight
    sday: np.ndarray               # server day as int (days since epoch of the NY+7 wall date)
    smin: np.ndarray               # server minute of day
    wday: np.ndarray               # server weekday, 0 = Monday
    label: np.ndarray              # cost-session label ("" closed)
    london_anchor: np.ndarray      # UTC ns of the latest London open at or before the minute
    start_ns: int                  # first admissible entry
    end_ns: int                    # every deadline must be before this
    day_end: dict[int, int] = field(default_factory=dict)       # server day -> UTC ns of server 23:45
    day_first: dict[int, int] = field(default_factory=dict)     # server day -> first 1m index
    day_0105: dict[int, int] = field(default_factory=dict)      # server day -> first 1m index at/after 01:05
    days: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    pool: dict[tuple[int, str], np.ndarray] = field(default_factory=dict)  # (day, label) -> admissible twin minutes


def load(raw: Path, mode: str) -> Data:
    start, end, y0, y1 = WINDOWS[mode]
    b = read_bar_files(raw, y0, y1)
    b = A.cut_at_holdout(b)
    A.assert_no_holdout_rows(b)
    ts_idx = pd.DatetimeIndex(b["ts_utc"])
    if mode == "explore":                       # explore never reads a 2018 price
        keep = np.asarray(ts_idx < end)
        b, ts_idx = b.loc[keep].reset_index(drop=True), ts_idx[keep]
    assert ts_idx.max() < A.HOLDOUT_FROM
    mid = {k: ((b[f"bid_{k}"].to_numpy(float) + b[f"ask_{k}"].to_numpy(float)) / 2) for k in ("open", "high", "low",
                                                                                            "close")}
    wall = server_wall(ts_idx, NY_PLUS_7)
    sday = (wall.normalize().to_numpy().astype("datetime64[D]").astype(np.int64))
    smin = (wall.hour * 60 + wall.minute).to_numpy().astype(np.int64)
    wday = wall.dayofweek.to_numpy().astype(np.int64)
    is_open = DEFAULT_SESSIONS.is_open(ts_idx)
    lab = DEFAULT_SESSIONS.session_label(ts_idx)
    label = np.where(is_open, lab, "").astype(object)
    d = Data(ts=epoch_ns(ts_idx).astype(np.int64), o=mid["open"], h=mid["high"], lo=mid["low"], c=mid["close"],
             w=np.maximum(b["tick_count"].fillna(0).to_numpy(float), 1e-6), sday=sday, smin=smin, wday=wday,
             label=label, london_anchor=DEFAULT_SESSIONS.last_open_ns(ts_idx, "london").astype(np.int64),
             start_ns=int(start.value), end_ns=int(end.value))
    days, first = np.unique(sday, return_index=True)
    d.days = days
    d.day_first = dict(zip(days.tolist(), first.tolist(), strict=True))
    for dd in days.tolist():   # server 23:45 of day dd = New York 16:45 on the same calendar date
        cal = pd.Timestamp(np.datetime64(int(dd), "D")) + pd.Timedelta(hours=16, minutes=45)
        d.day_end[dd] = int(cal.tz_localize("America/New_York").tz_convert("UTC").value)
    ok = (smin >= ROLL_HI) & (smin < ROLL_LO)
    idx = np.flatnonzero(ok & (smin >= ROLL_HI))
    pos = np.searchsorted(sday[idx], days, side="left")
    for dd, p in zip(days.tolist(), pos.tolist(), strict=True):
        if p < len(idx) and sday[idx[p]] == dd:
            d.day_0105[dd] = int(idx[p])
    keyframe = pd.DataFrame({"d": sday[ok], "l": label[ok], "i": np.flatnonzero(ok)})
    for key, g in keyframe.groupby(["d", "l"], sort=False):
        dd, ll = key
        d.pool[(int(dd), str(ll))] = g["i"].to_numpy()  # type: ignore[call-overload]
    return d


@dataclass
class Bars:
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    first: np.ndarray              # 1m index of the first minute
    last: np.ndarray               # 1m index of the last minute (the decision minute)
    day: np.ndarray                # server day of the first minute


def resample(d: Data, minutes: int) -> Bars:
    """Bins of the server wall clock (`minutes` = 1440 for server days), split wherever the bin key changes."""
    if minutes >= 1440:
        key = d.sday
    else:
        key = d.sday * 1440 + d.smin // minutes * minutes
    brk = np.flatnonzero(np.diff(key) != 0) + 1
    first = np.concatenate([[0], brk])
    last = np.concatenate([brk - 1, [len(key) - 1]])
    return Bars(o=d.o[first], h=np.maximum.reduceat(d.h, first), lo=np.minimum.reduceat(d.lo, first), c=d.c[last],
                first=first, last=last, day=d.sday[first])


def atr(b: Bars, n: int = 14) -> np.ndarray:
    pc = np.concatenate([[np.nan], b.c[:-1]])
    tr = np.nanmax(np.vstack([b.h - b.lo, np.abs(b.h - pc), np.abs(b.lo - pc)]), axis=0)
    return pd.Series(tr).rolling(n, min_periods=n).mean().to_numpy()


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def rsi2(c: np.ndarray, n: int = 2) -> np.ndarray:
    dlt = np.diff(c, prepend=np.nan)
    up = pd.Series(np.where(dlt > 0, dlt, 0.0))
    dn = pd.Series(np.where(dlt < 0, -dlt, 0.0))
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out[(ad == 0) & (au > 0)] = 100.0
    return out.to_numpy()


def asof(b: Bars, values: np.ndarray, minute: int) -> float:
    """The value of the last bar of `b` complete at 1m index `minute` (its last minute <= minute)."""
    k = int(np.searchsorted(b.last, minute, side="right")) - 1
    return float(values[k]) if k >= 0 else float("nan")


def prior_period_levels(b1d: Bars, period: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per server day: high, low, close and a validity flag of the PRIOR period (`period` id per day, e.g. week)."""
    df = pd.DataFrame({"p": period, "h": b1d.h, "l": b1d.lo, "c": b1d.c})
    g = df.groupby("p", sort=False).agg(h=("h", "max"), l=("l", "min"), c=("c", "last"))
    prev = g.shift(1)
    m = prev.reindex(period)
    return m["h"].to_numpy(), m["l"].to_numpy(), m["c"].to_numpy(), np.isfinite(m["h"].to_numpy())


def week_id(days: np.ndarray) -> np.ndarray:
    return (days + 3) // 7          # epoch day 0 is a Thursday: weeks start Monday


def month_id(days: np.ndarray) -> np.ndarray:
    dt = days.astype("datetime64[D]")
    return dt.astype("datetime64[M]").astype(np.int64)


# ---------------------------------------------------------------------------------------------------------- signals
@dataclass
class Signal:
    entry: int                     # 1m index of the entry minute
    side: int
    stop_px: float                 # rule stop price (floored to STOP_FLOOR from the fill)
    target_px: float | None = None     # a frozen level
    target_r: float | None = None      # or a multiple of R
    cap_h: float | None = None
    cap_days: int | None = None
    day_end: bool = False


@dataclass
class Spec:
    sid: str
    tf: str
    gen: Callable[[Data, dict[str, Bars]], list[Signal]]


def nxt(b: Bars, i: int) -> int:
    return int(b.last[i]) + 1


def daily_entry(d: Data, b1d: Bars, i: int) -> int | None:
    pos = int(np.searchsorted(d.days, b1d.day[i], side="right"))
    if pos >= len(d.days):
        return None
    return d.day_0105.get(int(d.days[pos]))


class Once:
    """'first per <period> per side'."""

    def __init__(self) -> None:
        self.seen: set[tuple[int, int]] = set()

    def take(self, period: int, side: int) -> bool:
        if (period, side) in self.seen:
            return False
        self.seen.add((period, side))
        return True


def _both(sig_s: bool, sig_l: bool) -> int:
    """Side of a bar: -1 / +1, or 0 when none or both fire (an ambiguous bar is skipped)."""
    if sig_s and not sig_l:
        return -1
    if sig_l and not sig_s:
        return 1
    return 0


def vwap_bands(d: Data, anchor: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Tick-weighted VWAP, sigma and minutes since the anchor, per 1m bar, grouped by `anchor` (contiguous runs)."""
    brk = np.flatnonzero(np.diff(anchor) != 0) + 1
    grp = np.zeros(len(anchor), dtype=np.int64)
    grp[brk] = 1
    grp = np.cumsum(grp)
    s = pd.DataFrame({"g": grp, "w": d.w, "wp": d.w * d.c, "wp2": d.w * d.c * d.c})
    cs = s.groupby("g")[["w", "wp", "wp2"]].cumsum()
    v = cs["wp"].to_numpy() / cs["w"].to_numpy()
    var = np.maximum(cs["wp2"].to_numpy() / cs["w"].to_numpy() - v * v, 0.0)
    first_ts = pd.Series(d.ts).groupby(grp).transform("first").to_numpy()
    return v, np.sqrt(var), (d.ts - first_ts) / NS_MIN, grp


def gen_vwap(d: Data, b: Bars, anchor: np.ndarray, k: float, tgt_kind: str, cap_h: float, day_end: bool,
             ny_only: bool = False) -> list[Signal]:
    v, sg, age, grp = vwap_bands(d, anchor)
    a = atr(b)
    out: list[Signal] = []
    for i in range(1, len(b.c)):
        m0, m1 = int(b.last[i - 1]), int(b.last[i])
        if grp[m0] != grp[m1] or age[m0] < 60 or not np.isfinite(a[i]) or sg[m0] <= 0:
            continue
        if ny_only and d.label[m1] != "newyork":
            continue
        up0, up1 = v[m0] + k * sg[m0], v[m1] + k * sg[m1]
        dn0, dn1 = v[m0] - k * sg[m0], v[m1] - k * sg[m1]
        side = _both(b.c[i - 1] > up0 and b.c[i] < up1, b.c[i - 1] < dn0 and b.c[i] > dn1)
        if side == 0:
            continue
        stop = max(b.h[i - 1], b.h[i]) + 0.1 * a[i] if side < 0 else min(b.lo[i - 1], b.lo[i]) - 0.1 * a[i]
        out.append(Signal(nxt(b, i), side, stop, target_px=float(v[m1]), cap_h=cap_h, day_end=day_end))
    return out


def g01(d: Data, B: dict[str, Bars]) -> list[Signal]:
    return gen_vwap(d, B["15m"], d.sday, 2.5, "vwap", 4, True)


def g02(d: Data, B: dict[str, Bars]) -> list[Signal]:
    return gen_vwap(d, B["1h"], week_id(d.sday), 2.0, "vwap", 48, False)


def g03(d: Data, B: dict[str, Bars]) -> list[Signal]:
    return gen_vwap(d, B["15m"], d.london_anchor, 3.0, "vwap", 3, True, ny_only=True)


def g04(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b1h = B["1h"]
    a1h = atr(b1h)
    out: list[Signal] = []
    days = d.days
    for p in range(1, len(days)):
        dd, prev = int(days[p]), int(days[p - 1])
        k = d.day_0105.get(dd)
        if k is None or k + 1 >= len(d.ts) or d.sday[k + 1] != dd:
            continue
        pe = d.day_end[prev]
        j = int(np.searchsorted(d.ts, pe, side="left")) - 1          # last 1m bar before server 23:45 of prev day
        if j < 0 or d.sday[j] != prev:
            continue
        a = asof(b1h, a1h, k)
        if not np.isfinite(a):
            continue
        gap = d.o[k] - d.c[j]
        if abs(gap) < 0.3 * a:
            continue
        side = -1 if gap > 0 else 1
        e = k + 1                                                     # decision at the 01:05 open, fill next minute
        out.append(Signal(e, side, d.o[e] - side * a, target_px=float(d.c[j]), cap_h=4))
    return out


def gen_level_reclaim(d: Data, b: Bars, hi_lvl: np.ndarray, lo_lvl: np.ndarray, period: np.ndarray, once: bool,
                      target_r: float, **exit_kw: Any) -> list[Signal]:
    """Bar trades beyond the level and closes back inside -> fade. Levels are per bar (already prior-period)."""
    a = atr(b)
    o = Once()
    out: list[Signal] = []
    for i in range(len(b.c)):
        H, L = hi_lvl[i], lo_lvl[i]
        if not (np.isfinite(H) and np.isfinite(L) and np.isfinite(a[i])):
            continue
        side = _both(b.h[i] > H and b.c[i] < H, b.lo[i] < L and b.c[i] > L)
        if side == 0 or (once and not o.take(int(period[i]), side)):
            continue
        stop = b.h[i] + 0.1 * a[i] if side < 0 else b.lo[i] - 0.1 * a[i]
        out.append(Signal(nxt(b, i), side, stop, target_r=target_r, **exit_kw))
    return out


def per_bar_prior(B: dict[str, Bars], tf: str, kind: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Prior day / week / month high, low, close for each bar of `tf` (by the bar's server day) and its period id."""
    b1d, b = B["1d"], B[tf]
    period = {"day": b1d.day, "week": week_id(b1d.day), "month": month_id(b1d.day)}[kind]
    ph, pl, pc, _ = prior_period_levels(b1d, period)
    pos = np.searchsorted(b1d.day, b.day)
    pos = np.clip(pos, 0, len(b1d.day) - 1)
    ok = b1d.day[pos] == b.day
    pick = lambda x: np.where(ok, x[pos], np.nan)  # noqa: E731
    return pick(ph), pick(pl), pick(pc), period[pos]


def g05(d: Data, B: dict[str, Bars]) -> list[Signal]:
    ph, pl, _, per = per_bar_prior(B, "1h", "day")
    return gen_level_reclaim(d, B["1h"], ph, pl, per, True, 2.0, day_end=True)


def g06(d: Data, B: dict[str, Bars]) -> list[Signal]:
    ph, pl, _, per = per_bar_prior(B, "4h", "day")
    return gen_level_reclaim(d, B["4h"], ph, pl, per, True, 2.0, cap_days=2)


def g07(d: Data, B: dict[str, Bars]) -> list[Signal]:
    ph, pl, _, per = per_bar_prior(B, "1h", "week")
    return gen_level_reclaim(d, B["1h"], ph, pl, per, True, 2.0, cap_days=3)


def g08(d: Data, B: dict[str, Bars]) -> list[Signal]:
    ph, pl, _, per = per_bar_prior(B, "4h", "week")
    return gen_level_reclaim(d, B["4h"], ph, pl, per, True, 3.0, cap_days=5)


def g09(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["1h"]
    ph, pl, _, per = per_bar_prior(B, "1h", "day")
    a = atr(b)
    o = Once()
    out: list[Signal] = []
    for i in range(len(b.c)):
        H, L, x = ph[i], pl[i], a[i]
        if not (np.isfinite(H) and np.isfinite(L) and np.isfinite(x)):
            continue
        s = (b.h[i] < H) and (b.h[i] >= H - 0.15 * x) and (b.c[i] <= b.h[i] - 0.5 * x)
        lg = (b.lo[i] > L) and (b.lo[i] <= L + 0.15 * x) and (b.c[i] >= b.lo[i] + 0.5 * x)
        side = _both(s, lg)
        if side == 0 or not o.take(int(per[i]), side):
            continue
        stop = H + 0.2 * x if side < 0 else L - 0.2 * x
        out.append(Signal(nxt(b, i), side, stop, target_r=1.5, day_end=True))
    return out


def g10(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["1d"]
    a = atr(b)
    out: list[Signal] = []
    for i in range(21, len(b.c)):
        if not np.isfinite(a[i]):
            continue
        win_l, win_h = b.lo[i - 20:i], b.h[i - 20:i]
        jl, jh = int(np.argmin(win_l)), int(np.argmax(win_h))
        L, H = win_l[jl], win_h[jh]
        old_l, old_h = (i - (i - 20 + jl)) >= 4, (i - (i - 20 + jh)) >= 4
        side = _both(old_h and b.h[i] > H and b.c[i] < H, old_l and b.lo[i] < L and b.c[i] > L)
        if side == 0:
            continue
        e = daily_entry(d, b, i)
        if e is None:
            continue
        stop = b.h[i] + 0.1 * a[i] if side < 0 else b.lo[i] - 0.1 * a[i]
        out.append(Signal(e, side, stop, target_r=2.0, cap_days=5))
    return out


def gen_donchian_fail(d: Data, b: Bars, n: int, target_r: float, **kw: Any) -> list[Signal]:
    hh = pd.Series(b.h).rolling(n, min_periods=n).max().shift(1).to_numpy()
    ll = pd.Series(b.lo).rolling(n, min_periods=n).min().shift(1).to_numpy()
    per = np.arange(len(b.c))
    return gen_level_reclaim(d, b, hh, ll, per, False, target_r, **kw)


def g11(d: Data, B: dict[str, Bars]) -> list[Signal]:
    return gen_donchian_fail(d, B["4h"], 20, 2.0, cap_days=3)


def g12(d: Data, B: dict[str, Bars]) -> list[Signal]:
    return gen_donchian_fail(d, B["1h"], 20, 1.5, cap_h=8, day_end=True)


def g13(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["15m"]
    a = atr(b)
    lon = d.label == "london"
    df = pd.DataFrame({"d": d.sday[lon], "h": d.h[lon], "l": d.lo[lon]}).groupby("d").agg(h=("h", "max"),
                                                                                        l=("l", "min"))
    o = Once()
    out: list[Signal] = []
    for i in range(len(b.c)):
        m = int(b.last[i])
        if d.label[m] != "newyork" or d.label[int(b.first[i])] != "newyork" or not np.isfinite(a[i]):
            continue
        dd = int(b.day[i])
        if dd not in df.index:
            continue
        H, L = float(df["h"].loc[dd]), float(df["l"].loc[dd])
        side = _both(b.h[i] > H and b.c[i] < H, b.lo[i] < L and b.c[i] > L)
        if side == 0 or not o.take(dd, side):
            continue
        stop = b.h[i] + 0.1 * a[i] if side < 0 else b.lo[i] - 0.1 * a[i]
        out.append(Signal(nxt(b, i), side, stop, target_px=(H + L) / 2, day_end=True))
    return out


def g14(d: Data, B: dict[str, Bars]) -> list[Signal]:
    ph, pl, _, per = per_bar_prior(B, "4h", "month")
    return gen_level_reclaim(d, B["4h"], ph, pl, per, True, 2.0, cap_days=5)


def gen_round(d: Data, b: Bars, step: float, margin_atr: float, target_r: float, max_pierce_atr: float | None,
              a: np.ndarray | None = None, entry_fn: Callable[[int], int | None] | None = None,
              **kw: Any) -> list[Signal]:
    """Bar opens below a multiple of `step`, trades above it, closes below it by `margin_atr` x ATR -> short (mirror).
    The highest crossed level for the short, the lowest for the long; a bar firing both ways is skipped."""
    a = atr(b) if a is None else a
    out: list[Signal] = []
    for i in range(len(b.c)):
        x = a[i]
        if not np.isfinite(x):
            continue
        ls = np.floor(b.h[i] / step) * step               # highest multiple <= high
        s = ls > b.o[i] and b.h[i] > ls and b.c[i] < ls - margin_atr * x
        if s and max_pierce_atr is not None:
            s = (b.h[i] - ls) <= max_pierce_atr * x
        ll = np.ceil(b.lo[i] / step) * step                # lowest multiple >= low
        lg = ll < b.o[i] and b.lo[i] < ll and b.c[i] > ll + margin_atr * x
        if lg and max_pierce_atr is not None:
            lg = (ll - b.lo[i]) <= max_pierce_atr * x
        side = _both(bool(s), bool(lg))
        if side == 0:
            continue
        e = nxt(b, i) if entry_fn is None else entry_fn(i)
        if e is None:
            continue
        stop = b.h[i] + 0.1 * x if side < 0 else b.lo[i] - 0.1 * x
        out.append(Signal(e, side, stop, target_r=target_r, **kw))
    return out


def g15(d: Data, B: dict[str, Bars]) -> list[Signal]:
    return gen_round(d, B["1h"], 50.0, 0.1, 1.5, None, cap_h=8, day_end=True)


def g16(d: Data, B: dict[str, Bars]) -> list[Signal]:
    return gen_round(d, B["4h"], 100.0, 0.1, 2.0, None, cap_days=3)


def g17(d: Data, B: dict[str, Bars]) -> list[Signal]:
    return gen_round(d, B["15m"], 10.0, 0.0, 1.0, 0.5, cap_h=2)


def g18(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["1d"]
    return gen_round(d, b, 50.0, 0.2, 2.0, None, entry_fn=lambda i: daily_entry(d, b, i), cap_days=3)


def gen_pivot(d: Data, B: dict[str, Bars], tf: str, kind: str, which: str, **kw: Any) -> list[Signal]:
    b = B[tf]
    ph, pl, pc, per = per_bar_prior(B, tf, kind)
    P = (ph + pl + pc) / 3
    if which == "2":
        R, S = P + (ph - pl), P - (ph - pl)
    else:
        R, S = 2 * P - pl, 2 * P - ph
    a = atr(b)
    o = Once()
    out: list[Signal] = []
    for i in range(len(b.c)):
        if not (np.isfinite(P[i]) and np.isfinite(a[i])):
            continue
        side = _both(b.h[i] >= R[i] and b.c[i] < R[i], b.lo[i] <= S[i] and b.c[i] > S[i])
        if side == 0 or not o.take(int(per[i]), side):
            continue
        stop = b.h[i] + 0.1 * a[i] if side < 0 else b.lo[i] - 0.1 * a[i]
        out.append(Signal(nxt(b, i), side, stop, target_px=float(P[i]), **kw))
    return out


def g19(d: Data, B: dict[str, Bars]) -> list[Signal]:
    return gen_pivot(d, B, "1h", "day", "2", day_end=True)


def g20(d: Data, B: dict[str, Bars]) -> list[Signal]:
    return gen_pivot(d, B, "1h", "day", "1", day_end=True)


def g21(d: Data, B: dict[str, Bars]) -> list[Signal]:
    return gen_pivot(d, B, "4h", "week", "1", cap_days=5)


def g22(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["15m"]
    ph, pl, pc, per = per_bar_prior(B, "15m", "day")
    rng = ph - pl
    H3, H4 = pc + 1.1 * rng / 4, pc + 1.1 * rng / 2
    L3, L4 = pc - 1.1 * rng / 4, pc - 1.1 * rng / 2
    a = atr(b)
    o = Once()
    out: list[Signal] = []
    for i in range(len(b.c)):
        if not (np.isfinite(pc[i]) and np.isfinite(a[i])):
            continue
        s = H3[i] < b.h[i] < H4[i] and b.c[i] < H3[i]
        lg = L4[i] < b.lo[i] < L3[i] and b.c[i] > L3[i]
        side = _both(bool(s), bool(lg))
        if side == 0 or not o.take(int(per[i]), side):
            continue
        stop = H4[i] + 0.1 * a[i] if side < 0 else L4[i] - 0.1 * a[i]
        out.append(Signal(nxt(b, i), side, stop, target_px=float(pc[i]), day_end=True))
    return out


def gen_zscore(d: Data, b: Bars, n: int, z: float, a: np.ndarray, entry_fn: Callable[[int], int | None],
               stop_atr: float, **kw: Any) -> list[Signal]:
    m = sma(b.c, n)
    out: list[Signal] = []
    for i in range(3, len(b.c)):
        if not (np.isfinite(m[i - 1]) and np.isfinite(a[i - 1]) and np.isfinite(a[i]) and a[i - 1] > 0):
            continue
        zz = (b.c[i - 1] - m[i - 1]) / a[i - 1]
        side = _both(zz >= z and b.c[i] < b.lo[i - 1], zz <= -z and b.c[i] > b.h[i - 1])
        if side == 0:
            continue
        e = entry_fn(i)
        if e is None:
            continue
        stop = b.h[i - 2:i + 1].max() + stop_atr * a[i] if side < 0 else b.lo[i - 2:i + 1].min() - stop_atr * a[i]
        out.append(Signal(e, side, stop, target_px=float(m[i]), **kw))
    return out


def g23(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["1h"]
    return gen_zscore(d, b, 20, 3.0, atr(b), lambda i: nxt(b, i), 0.2, cap_h=12)


def g24(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["4h"]
    return gen_zscore(d, b, 20, 3.0, atr(b), lambda i: nxt(b, i), 0.2, cap_days=3)


def g25(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["1d"]
    return gen_zscore(d, b, 10, 2.5, atr(b), lambda i: daily_entry(d, b, i), 0.2, cap_days=5)


def gen_rsi(d: Data, b: Bars, lo_th: float, hi_th: float, trend_n: int, stop_atr: float,
            entry_fn: Callable[[int], int | None], **kw: Any) -> list[Signal]:
    r, m, a = rsi2(b.c), sma(b.c, trend_n), atr(b)
    out: list[Signal] = []
    for i in range(len(b.c)):
        if not (np.isfinite(r[i]) and np.isfinite(m[i]) and np.isfinite(a[i])):
            continue
        side = _both(r[i] > hi_th and b.c[i] < m[i], r[i] < lo_th and b.c[i] > m[i])
        if side == 0:
            continue
        e = entry_fn(i)
        if e is None:
            continue
        out.append(Signal(e, side, d.o[e] - side * stop_atr * a[i], target_r=1.0, **kw))
    return out


def g26(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["1d"]
    return gen_rsi(d, b, 5, 95, 200, 2.0, lambda i: daily_entry(d, b, i), cap_days=5)


def g27(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["4h"]
    return gen_rsi(d, b, 3, 97, 50, 1.5, lambda i: nxt(b, i), cap_days=2)


def g28(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["1h"]
    m = sma(b.c, 20)
    sd = pd.Series(b.c).rolling(20, min_periods=20).std(ddof=0).to_numpy()
    up, dn = m + 2.5 * sd, m - 2.5 * sd
    a = atr(b)
    out: list[Signal] = []
    for i in range(1, len(b.c)):
        if not (np.isfinite(up[i - 1]) and np.isfinite(a[i])):
            continue
        side = _both(b.c[i - 1] > up[i - 1] and b.c[i] <= up[i], b.c[i - 1] < dn[i - 1] and b.c[i] >= dn[i])
        if side == 0:
            continue
        stop = max(b.h[i - 1], b.h[i]) + 0.1 * a[i] if side < 0 else min(b.lo[i - 1], b.lo[i]) - 0.1 * a[i]
        out.append(Signal(nxt(b, i), side, stop, target_px=float(m[i]), cap_h=12))
    return out


def g29(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["1d"]
    a = atr(b)
    out: list[Signal] = []
    for i in range(len(b.c)):
        rng = b.h[i] - b.lo[i]
        if not np.isfinite(a[i]) or rng <= 0:
            continue
        ibs = (b.c[i] - b.lo[i]) / rng
        side = _both(ibs >= 0.9, ibs <= 0.1)
        if side == 0:
            continue
        e = daily_entry(d, b, i)
        if e is None:
            continue
        out.append(Signal(e, side, d.o[e] - side * a[i], day_end=True))
    return out


def g30(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b, b1d, b1h = B["15m"], B["1d"], B["1h"]
    a15, a1d, a1h = atr(b), atr(b1d), atr(b1h)
    prior_atrd = dict(zip(b1d.day.tolist(), np.concatenate([[np.nan], a1d[:-1]]).tolist(), strict=True))
    day_open = {int(dd): float(d.o[k]) for dd, k in d.day_first.items()}
    out: list[Signal] = []
    cur, hi, lo, armed_s, armed_l = -1, -np.inf, np.inf, False, False
    o = Once()
    for i in range(len(b.c)):
        dd = int(b.day[i])
        if dd != cur:
            cur, hi, lo, armed_s, armed_l = dd, -np.inf, np.inf, False, False
        hi, lo = max(hi, b.h[i]), min(lo, b.lo[i])
        ad = prior_atrd.get(dd, np.nan)
        if not (np.isfinite(ad) and np.isfinite(a15[i])):
            continue
        op = day_open[dd]
        armed_s = armed_s or hi >= op + 1.2 * ad
        armed_l = armed_l or lo <= op - 1.2 * ad
        s = armed_s and b.c[i] <= hi - 0.3 * a15[i] and (dd, -1) not in o.seen
        lg = armed_l and b.c[i] >= lo + 0.3 * a15[i] and (dd, 1) not in o.seen
        side = _both(bool(s), bool(lg))
        if side == 0:
            continue
        o.take(dd, side)
        x = asof(b1h, a1h, int(b.last[i]))
        if not np.isfinite(x):
            continue
        stop = hi + 0.25 * x if side < 0 else lo - 0.25 * x
        out.append(Signal(nxt(b, i), side, stop, target_r=2.0, day_end=True))
    return out


def gen_run(d: Data, b: Bars, n: int, min_move_atr: float | None, stop_kind: str, stop_atr: float, target_r: float,
            entry_fn: Callable[[int], int | None], **kw: Any) -> list[Signal]:
    a = atr(b)
    up = np.concatenate([[False], b.c[1:] > b.c[:-1]])
    dn = np.concatenate([[False], b.c[1:] < b.c[:-1]])
    ru = pd.Series(up.astype(int)).rolling(n, min_periods=n).sum().to_numpy() == n
    rd = pd.Series(dn.astype(int)).rolling(n, min_periods=n).sum().to_numpy() == n
    out: list[Signal] = []
    for i in range(n, len(b.c)):
        if not np.isfinite(a[i]):
            continue
        s, lg = bool(ru[i]), bool(rd[i])
        if min_move_atr is not None:
            mv = b.c[i] - b.c[i - n]
            s, lg = s and mv >= min_move_atr * a[i], lg and -mv >= min_move_atr * a[i]
        side = _both(s, lg)
        if side == 0:
            continue
        e = entry_fn(i)
        if e is None:
            continue
        if stop_kind == "run":
            stop = b.h[i - n + 1:i + 1].max() + stop_atr * a[i] if side < 0 else b.lo[i - n + 1:i + 1].min() - \
                stop_atr * a[i]
        elif stop_kind == "last":
            stop = b.h[i] + stop_atr * a[i] if side < 0 else b.lo[i] - stop_atr * a[i]
        else:                                                         # "entry": stop_atr x ATR from the fill
            stop = d.o[e] - side * stop_atr * a[i]
        out.append(Signal(e, side, stop, target_r=target_r, **kw))
    return out


def g31(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["15m"]
    return gen_run(d, b, 8, 2.5, "run", 0.25, 1.0, lambda i: nxt(b, i), cap_h=3)


def g32(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["1h"]
    return gen_run(d, b, 6, None, "last", 0.5, 1.0, lambda i: nxt(b, i), cap_h=6)


def g33(d: Data, B: dict[str, Bars]) -> list[Signal]:
    b = B["1d"]
    return gen_run(d, b, 4, None, "entry", 1.5, 1.0, lambda i: daily_entry(d, b, i), cap_days=3)


GENS = [g01, g02, g03, g04, g05, g06, g07, g08, g09, g10, g11, g12, g13, g14, g15, g16, g17, g18, g19, g20, g21, g22,
        g23, g24, g25, g26, g27, g28, g29, g30, g31, g32, g33]
SPECS = {f"S200-MEANREV-{k + 1:02d}": g for k, g in enumerate(GENS)}


# ---------------------------------------------------------------------------------------------------------- simulation
def deadline(d: Data, e: int, sig: Signal) -> int:
    t0 = int(d.ts[e])
    dd, wd = int(d.sday[e]), int(d.wday[e])
    fri = dd + (4 - wd) if wd <= 4 else dd
    dl = _day_end(d, fri)
    if sig.day_end:
        dl = min(dl, _day_end(d, dd))
    if sig.cap_h is not None:
        dl = min(dl, t0 + int(sig.cap_h * NS_H))
    if sig.cap_days is not None:
        target_day, k = dd, 0
        while k < sig.cap_days:
            target_day += 1
            if (target_day + 3) % 7 < 5:                              # Mon..Fri (epoch day 0 = Thursday)
                k += 1
        dl = min(dl, _day_end(d, target_day))
    return dl


def _day_end(d: Data, dd: int) -> int:
    v = d.day_end.get(dd)
    if v is None:
        cal = pd.Timestamp(np.datetime64(int(dd), "D")) + pd.Timedelta(hours=16, minutes=45)
        v = int(cal.tz_localize("America/New_York").tz_convert("UTC").value)
        d.day_end[dd] = v
    return v


def walk(d: Data, e: int, side: int, sd: float, td: float | None, dl: int) -> tuple[float, float, bool, int] | None:
    """(signed move per oz, exit price, stop exit, exit index) on the 1m mid path from entry minute e to the last
    bar before `dl`; None when there is no bar to hold."""
    last = int(np.searchsorted(d.ts, dl, side="left")) - 1
    if last < e:
        return None
    px = d.o[e]
    stop = px - side * sd
    hs, ls, os_ = d.h[e:last + 1], d.lo[e:last + 1], d.o[e:last + 1]
    hit_s = ls <= stop if side > 0 else hs >= stop
    s_i = int(np.argmax(hit_s)) if hit_s.any() else -1
    t_i = -1
    if td is not None:
        tgt = px + side * td
        hit_t = hs >= tgt if side > 0 else ls <= tgt
        t_i = int(np.argmax(hit_t)) if hit_t.any() else -1
    if s_i >= 0 and (t_i < 0 or s_i <= t_i):
        o = os_[s_i]
        fill = min(stop, o) if side > 0 else max(stop, o)
        return side * (fill - px), fill, True, e + s_i
    if t_i >= 0:
        return float(td), px + side * td, False, e + t_i                 # type: ignore[operator]
    return side * (d.c[last] - px), d.c[last], False, last


@dataclass
class Trade:
    entry: int
    exit: int
    side: int
    move: float
    sd: float
    stop_exit: bool
    label: str


def admit(d: Data, sig: Signal, e: int) -> tuple[float, float | None, int] | None:
    """(stop distance, target distance, deadline) of a signal entered at minute e, or None if not admissible."""
    if e >= len(d.ts) or d.ts[e] < d.start_ns:
        return None
    if d.smin[e] >= ROLL_LO or d.smin[e] < ROLL_HI:
        return None
    px = d.o[e]
    sd = max(sig.side * (px - sig.stop_px), STOP_FLOOR)
    td: float | None = None
    if sig.target_px is not None:
        td = sig.side * (sig.target_px - px)
        if td < MIN_TARGET_R * sd:
            return None
    elif sig.target_r is not None:
        td = sig.target_r * sd
    dl = deadline(d, e, sig)
    if dl >= d.end_ns or dl <= int(d.ts[e]):
        return None
    return sd, td, dl


def run_trades(d: Data, sigs: list[Signal]) -> tuple[list[Trade], list[tuple[Signal, float, float | None]]]:
    sigs = sorted(sigs, key=lambda s: s.entry)
    out: list[Trade] = []
    meta: list[tuple[Signal, float, float | None]] = []
    busy_until = -1
    for s in sigs:
        if s.entry <= busy_until:
            continue
        a = admit(d, s, s.entry)
        if a is None:
            continue
        sd, td, dl = a
        w = walk(d, s.entry, s.side, sd, td, dl)
        if w is None:
            continue
        move, _, stop_exit, x = w
        out.append(Trade(s.entry, x, s.side, move, sd, stop_exit, str(d.label[s.entry])))
        meta.append((s, sd, td))
        busy_until = x
    return out, meta


def twins(d: Data, trades: list[Trade], meta: list[tuple[Signal, float, float | None]], seed: int) -> list[Trade]:
    rng = np.random.Generator(np.random.PCG64(seed))
    out: list[Trade] = []
    for t, (s, sd, td) in zip(trades, meta, strict=True):
        pool = d.pool.get((int(d.sday[t.entry]), t.label))
        if pool is None or not len(pool):
            continue
        for e in rng.choice(pool, size=N_TWINS, replace=True).tolist():
            if d.ts[e] < d.start_ns:
                continue
            dl = deadline(d, e, s)
            if dl >= d.end_ns or dl <= int(d.ts[e]):
                continue
            w = walk(d, e, s.side, sd, td, dl)
            if w is None:
                continue
            move, _, stop_exit, x = w
            out.append(Trade(e, x, s.side, move, sd, stop_exit, str(d.label[e])))
    return out


def net_r(d: Data, trades: list[Trade], costs: A.ArmCosts, overshoot: float | None = None) -> np.ndarray:
    if not trades:
        return np.zeros(0)
    ent = pd.DatetimeIndex(pd.to_datetime(np.array([d.ts[t.entry] for t in trades]), utc=True))
    ext = pd.DatetimeIndex(pd.to_datetime(np.array([d.ts[t.exit] + NS_MIN for t in trades]), utc=True))
    swap = SwapSpec.shipped()
    nights = rollover_nights(ent, ext, swap.server_tz, swap.triple_weekday)
    side = np.array([t.side for t in trades])
    move = np.array([t.move for t in trades]) + nights * swap.usd_per_oz(side)
    return A.screen_net_r(move, np.array([t.sd for t in trades]), np.array([t.stop_exit for t in trades]), costs,
                          broker=BROKER, sessions=np.array([t.label for t in trades], dtype=object),
                          version=COST_VERSION, overshoot_usd=overshoot)


def tstat(r: np.ndarray) -> float:
    if len(r) < 2 or np.std(r, ddof=1) == 0:
        return 0.0
    return float(np.mean(r) / (np.std(r, ddof=1) / np.sqrt(len(r))))


# ---------------------------------------------------------------------------------------------------------- driver
_D: Data | None = None
_B: dict[str, Bars] | None = None
_COSTS: A.ArmCosts | None = None


def evaluate(sid: str) -> dict[str, Any]:
    assert _D is not None and _B is not None and _COSTS is not None
    d, B, costs = _D, _B, _COSTS
    num = int(sid.rsplit("-", 1)[1])
    sigs = SPECS[sid](d, B)
    trades, meta = run_trades(d, sigs)
    r = net_r(d, trades, costs)
    tw = twins(d, trades, meta, SEED + num)
    rc = net_r(d, tw, costs)
    gross = np.array([t.move / t.sd for t in trades]) if trades else np.zeros(0)
    years = pd.DatetimeIndex(pd.to_datetime(np.array([d.ts[t.entry] for t in trades], dtype=np.int64), utc=True)).year
    by_year = {int(y): {"n": int((years == y).sum()), "net_r": float(r[years == y].mean())}
               for y in sorted(set(years.tolist()))}
    stress = net_r(d, trades, costs, overshoot=A.OVERSHOOT_STRESS_USD)
    res: dict[str, Any] = {
        "id": sid, "n_signals": len(sigs), "n": len(trades),
        "gross_r": float(gross.mean()) if len(gross) else None,
        "net_r": float(r.mean()) if len(r) else None, "t": tstat(r),
        "control_n": len(tw), "control_net_r": float(rc.mean()) if len(rc) else None,
        "vs_control": float(r.mean() - rc.mean()) if len(r) and len(rc) else None,
        "net_r_stress_os070": float(stress.mean()) if len(stress) else None,
        "median_stop_usd": float(np.median([t.sd for t in trades])) if trades else None,
        "stop_exit_share": float(np.mean([t.stop_exit for t in trades])) if trades else None,
        "long_share": float(np.mean([t.side > 0 for t in trades])) if trades else None,
        "by_year": by_year,
    }
    if len(r) >= 3:
        inp = DsrInputs.of(r)
        res["dsr_k3229"] = inp.dsr(K_SPRINT)
        res["dsr_k4000"] = inp.dsr(K_STRICT)
        res["sr_per_trade"] = inp.sr
    return res


def explore_pass(x: dict[str, Any]) -> bool:
    return (x["n"] >= 100 and x["net_r"] is not None and x["net_r"] > 0 and x["t"] >= 2.0
            and x["vs_control"] is not None and x["vs_control"] >= 0.05)


def confirm_pass(x: dict[str, Any]) -> dict[str, bool]:
    yrs = [x["by_year"].get(y, {"n": 0, "net_r": 0.0}) for y in range(2018, 2026)]
    pos = sum(1 for v in yrs if v["n"] > 0 and v["net_r"] > 0)
    x["years_positive"] = pos
    legs = {"net_gt_0": x["net_r"] is not None and x["net_r"] > 0, "t_ge_2": x["t"] >= 2.0,
            "dsr_gt_0.5": x.get("dsr_k3229", 0.0) > 0.5, "years_ge_6": pos >= 6}
    return legs


def _init(raw: str, mode: str) -> None:
    global _D, _B, _COSTS
    _D = load(Path(raw), mode)
    _B = {"15m": resample(_D, 15), "1h": resample(_D, 60), "4h": resample(_D, 240), "1d": resample(_D, 1440)}
    _COSTS = A.load_costs(ROOT / "config" / "arm_costs.yaml")
    _COSTS.require_version(COST_VERSION)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["explore", "confirm", "count"])
    ap.add_argument("--raw", default=str(ROOT / "raw"))
    ap.add_argument("--ids", default="")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args(argv)
    if a.workers > 2:
        raise SystemExit("at most 2 worker processes (sprint Mac-load rule)")
    mode = "explore" if a.mode == "count" else a.mode
    if a.mode == "confirm":
        prior = json.loads((OUT_DIR / "meanrev-explore.json").read_text())
        ids = [x["id"] for x in prior["results"] if x["explore_pass"]]
        if a.ids:
            asked = a.ids.split(",")
            bad = [i for i in asked if i not in ids]
            if bad:
                raise SystemExit(f"refused: not explore passers {bad}")
            ids = asked
    else:
        ids = a.ids.split(",") if a.ids else list(SPECS)
    if not ids:
        print("no ids to run")
        return
    if a.mode == "count":                    # signal counts only, no outcome
        _init(a.raw, mode)
        assert _D is not None and _B is not None
        for sid in ids:
            print(sid, len(SPECS[sid](_D, _B)))
        return
    ctx = get_context("fork")
    _init(a.raw, mode)
    with ctx.Pool(a.workers) as pool:
        results = pool.map(evaluate, ids, chunksize=1)
    for x in results:
        if mode == "explore":
            x["explore_pass"] = explore_pass(x)
        else:
            x["confirm_legs"] = confirm_pass(x)
            x["confirm_pass"] = all(x["confirm_legs"].values())
    out = {"mode": mode, "cost_version": COST_VERSION, "broker": BROKER, "k_sprint": K_SPRINT,
           "window": [str(WINDOWS[mode][0].date()), str(WINDOWS[mode][1].date())], "results": results}
    path = OUT_DIR / f"meanrev-{mode}.json"
    path.write_text(json.dumps(out, indent=1, default=float) + "\n")
    for x in results:
        print(x["id"], x["n"], x.get("net_r"), round(x["t"], 2), x.get("vs_control"),
              x.get("explore_pass", x.get("confirm_pass")))


if __name__ == "__main__":
    main()
