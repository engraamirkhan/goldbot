"""Sprint-200 lane TIME: the 33 pre-registered time and calendar strategies (docs/research/sprint200/time-prereg.md,
committed before this code ran on market data), screened net of the cost of record cor-2026-10-31.2 (icm, ADR 0064)
against a matched random-entry control.

Usage:
  uv run python scripts/sprint200/time_screen.py explore [--ids 01,02] [--bars <raw>] [--out ~/tmp/s200time] [--workers 2]
  uv run python scripts/sprint200/time_screen.py confirm --ids <explore passers> [...]

explore reads decision dates 2010-01-01..2017-12-31; confirm reads 2018-01-01..2025-09-30 (bars from 2017 are loaded as
look-back only). Nothing at or after 2025-10-01 is loaded: the 2026 file is never opened and the 2025 file is cut and
asserted (goldbot.research.arms). Trade rows and summaries go to --out (outside the repo; never commit them)."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pandas.tseries.offsets import Easter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.data.cme_holidays import cme_metals_holidays  # noqa: E402
from goldbot.data.timeutil import epoch_ns  # noqa: E402
from goldbot.labels import SwapSpec, rollover_nights  # noqa: E402
from goldbot.research import arms as A  # noqa: E402
from goldbot.research.metrics import DsrInputs, day_summed_dsr  # noqa: E402

COST_VERSION = "cor-2026-10-31.2"
BROKER = "icm"
SEED = 20261102
N_DRAWS = 5
K_PLANNED = 3_229
K_STRESS = 5_000
MIN = 60 * 10**9
TEN_MIN = 10 * MIN
HOUR = 60 * MIN
NY, LDN, TKY, SH = (ZoneInfo(z) for z in ("America/New_York", "Europe/London", "Asia/Tokyo", "Asia/Shanghai"))
WINDOWS = {"explore": (date(2010, 1, 1), date(2017, 12, 31), 2010, 2017),
           "confirm": (date(2018, 1, 1), date(2025, 9, 30), 2017, 2025)}
HOLDOUT_NS = int(A.HOLDOUT_FROM.value)


# ---------------------------------------------------------------- calendars (rule-based, known in advance)

def _obs_sat_sun_to_mon(d: date) -> date:
    return d + timedelta(days=2) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def _nth_weekday(y: int, m: int, wd: int, n: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(wd - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y: int, m: int, wd: int) -> date:
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - wd) % 7)


@lru_cache(maxsize=64)
def uk_bank_holidays(y: int) -> frozenset[date]:
    """England and Wales bank holidays (rules plus the published exceptions listed in the pre-registration)."""
    e = (pd.Timestamp(y, 1, 1) + Easter()).date()
    out = {_obs_sat_sun_to_mon(date(y, 1, 1)), e - timedelta(days=2), e + timedelta(days=1),
           _nth_weekday(y, 5, 0, 1), _last_weekday(y, 5, 0), _last_weekday(y, 8, 0)}
    xmas, boxing = date(y, 12, 25), date(y, 12, 26)
    if xmas.weekday() == 5:          # Sat: Mon 27 and Tue 28
        out |= {date(y, 12, 27), date(y, 12, 28)}
    elif xmas.weekday() == 6:        # Sun: Boxing Mon 26, Christmas Tue 27
        out |= {date(y, 12, 26), date(y, 12, 27)}
    elif boxing.weekday() == 5:      # Fri Christmas: Boxing Mon 28
        out |= {xmas, date(y, 12, 28)}
    else:
        out |= {xmas, boxing}
    moves: dict[int, tuple[set[date], set[date]]] = {2012: ({date(2012, 5, 28)}, {date(2012, 6, 4), date(2012, 6, 5)}),
             2020: ({date(2020, 5, 4)}, {date(2020, 5, 8)}),
             2022: ({date(2022, 5, 30)}, {date(2022, 6, 2), date(2022, 6, 3), date(2022, 9, 19)}),
             2011: (set(), {date(2011, 4, 29)}), 2023: (set(), {date(2023, 5, 8)})}
    if y in moves:
        drop, add = moves[y]
        out = (out - drop) | add
    return frozenset(out)


@lru_cache(maxsize=64)
def us_holiday_days(y: int) -> dict[date, str]:
    return {d: c.kind for d, c in cme_metals_holidays(y).items()}


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and us_holiday_days(d.year).get(d) != "full"


@lru_cache(maxsize=1)
def business_days() -> tuple[date, ...]:
    d, out = date(2009, 1, 1), []
    while d <= date(2026, 1, 31):
        if is_business_day(d):
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


@lru_cache(maxsize=1)
def _bd_index() -> dict[date, int]:
    return {d: i for i, d in enumerate(business_days())}


def bd_shift(d: date, k: int) -> date:
    bds = business_days()
    return bds[_bd_index()[d] + k]


def month_bdays(y: int, m: int) -> list[date]:
    return [d for d in business_days() if d.year == y and d.month == m]


@lru_cache(maxsize=1)
def calendar_sets() -> dict[str, frozenset[date]]:
    """The calendar subsets of strategies 17-25 and 32, every year 2009-2025."""
    bds = business_days()
    hol_days = {d for y in range(2009, 2027) for d in us_holiday_days(y)}
    s: dict[str, set[date]] = {k: set() for k in ("month_last2", "month_first2", "quarter_turn", "expiry3",
                                                   "pre_fnd5", "post_fnd3", "pre_us_hol2", "thin")}
    for y in range(2009, 2026):
        for m in range(1, 13):
            md = month_bdays(y, m)
            if len(md) < 5:
                continue
            s["month_last2"] |= set(md[-2:])
            s["month_first2"] |= set(md[:2])
            if m in (3, 6, 9, 12):
                s["quarter_turn"] |= set(md[-2:])
            if m in (1, 4, 7, 10):
                s["quarter_turn"] |= set(md[:2])
            exp = md[-4]
            nxt = bd_shift(exp, 1)
            if exp.weekday() == 4 or any(exp < h <= nxt for h in hol_days):   # Friday, or the day before a holiday
                exp = bd_shift(exp, -1)
            s["expiry3"] |= {bd_shift(exp, -2), bd_shift(exp, -1), exp}
            if m in (1, 3, 5, 7, 9, 11):
                fnd = md[-1]
                s["pre_fnd5"] |= {bd_shift(fnd, -k) for k in range(1, 6)}
                s["post_fnd3"] |= {fnd, bd_shift(fnd, 1), bd_shift(fnd, 2)}
    for h in sorted(hol_days):
        if date(2009, 2, 1) <= h <= date(2025, 12, 31):
            i = int(np.searchsorted(np.array(bds, dtype="datetime64[D]"), np.datetime64(h), side="left"))
            s["pre_us_hol2"] |= {bds[i - 1], bds[i - 2]}
            if is_business_day(h):
                s["thin"].add(h)
    for y in range(2009, 2026):
        s["thin"] |= {d for d in uk_bank_holidays(y) if is_business_day(d)}
    return {k: frozenset(v) for k, v in s.items()}


# ---------------------------------------------------------------- market (mid 1m bars as numpy)

def load_market(folder: Path, y0: int, y1: int) -> pd.DataFrame:
    if y1 > A.LAST_DAY.year:
        raise SystemExit("the holdout year is never opened")
    parts = []
    for y in range(y0, y1 + 1):
        b = pd.read_parquet(folder / f"xauusd_1m_dukascopy_{y}.parquet",
                            columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close",
                                     "ask_open", "ask_high", "ask_low", "ask_close"])
        parts.append(b)
    b = pd.concat(parts, ignore_index=True)
    b = A.cut_at_holdout(b)
    A.assert_no_holdout_rows(b)
    return b


@dataclass
class Market:
    ts: np.ndarray
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    hour_end: np.ndarray
    hour_rng_cum: np.ndarray
    nyday_end: np.ndarray
    nyday_rng_cum: np.ndarray
    sess_lo: np.ndarray
    sess_hi: np.ndarray
    sess_label: np.ndarray
    cost_label: np.ndarray

    @classmethod
    def of(cls, b: pd.DataFrame) -> Market:
        b = b.sort_values("ts_utc").drop_duplicates("ts_utc").reset_index(drop=True)
        ts = epoch_ns(pd.DatetimeIndex(b["ts_utc"]))
        mid = {k: ((b[f"bid_{k}"].to_numpy(float) + b[f"ask_{k}"].to_numpy(float)) / 2) for k in ("open", "high", "low", "close")}
        o, h, lo, c = mid["open"], mid["high"], mid["low"], mid["close"]
        # hourly ranges (complete UTC hours with >= 30 bars), indexed by hour end
        hr = ts // HOUR
        hb = pd.DataFrame({"hr": hr, "h": h, "l": lo}).groupby("hr").agg(n=("h", "size"), h=("h", "max"), l=("l", "min"))
        hb = hb[hb["n"] >= 30]
        hour_end = (hb.index.to_numpy(np.int64) + 1) * HOUR
        hour_rng_cum = np.concatenate([[0.0], np.cumsum((hb["h"] - hb["l"]).to_numpy(float))])
        # NY days (17:00 NY to 17:00 NY): label = the NY date of (stamp + 7h)
        idx = pd.DatetimeIndex(b["ts_utc"]).tz_convert(NY)
        nyd = (idx + pd.Timedelta(hours=7)).normalize().tz_localize(None)
        db = pd.DataFrame({"d": nyd, "h": h, "l": lo}).groupby("d").agg(h=("h", "max"), l=("l", "min"))
        ends = [epoch_ns(pd.DatetimeIndex([pd.Timestamp(datetime.combine(d.date(), time(17, 0)), tz=NY)]))[0]
                for d in db.index]
        nyday_end = np.array(ends, dtype=np.int64)
        nyday_rng_cum = np.concatenate([[0.0], np.cumsum((db["h"] - db["l"]).to_numpy(float))])
        # session instances for the control: runs of one session code (gap = its own label), split at gaps > 60 min
        utc = pd.DatetimeIndex(b["ts_utc"]).tz_convert("UTC")
        code, _ = DEFAULT_SESSIONS.session_windows(utc)
        names = np.array(list(DEFAULT_SESSIONS.sessions_local) + ["gap"], dtype=object)
        lab = names[np.where(code >= 0, code, len(names) - 1)]
        brk = np.ones(len(ts), dtype=bool)
        brk[1:] = (lab[1:] != lab[:-1]) | (np.diff(ts) > HOUR)
        run = np.cumsum(brk) - 1
        starts = np.flatnonzero(brk)
        ends_i = np.append(starts[1:], len(ts))
        cost_label = np.where(DEFAULT_SESSIONS.is_open(utc), DEFAULT_SESSIONS.session_label(utc), "").astype(object)
        return cls(ts=ts, o=o, h=h, lo=lo, c=c, hour_end=hour_end, hour_rng_cum=hour_rng_cum, nyday_end=nyday_end,
                   nyday_rng_cum=nyday_rng_cum, sess_lo=starts[run], sess_hi=ends_i[run], sess_label=lab,
                   cost_label=cost_label)

    def price(self, t: int) -> float:
        """P(T): close of the last bar starting before T, if it started within 10 minutes of T."""
        i = int(np.searchsorted(self.ts, t, side="left")) - 1
        return float(self.c[i]) if i >= 0 and t - self.ts[i] <= TEN_MIN else float("nan")

    def entry_k(self, t: int) -> int | None:
        k = int(np.searchsorted(self.ts, t, side="left"))
        return k if k < len(self.ts) and self.ts[k] - t <= TEN_MIN else None

    def hl(self, t0: int, t1: int) -> tuple[float, float]:
        a, b = int(np.searchsorted(self.ts, t0, "left")), int(np.searchsorted(self.ts, t1, "left"))
        if b <= a:
            return float("nan"), float("nan")
        return float(self.h[a:b].max()), float(self.lo[a:b].min())

    def atrh(self, t: int) -> float:
        j = int(np.searchsorted(self.hour_end, t, side="right"))
        if j < 24:
            return float("nan")
        return float((self.hour_rng_cum[j] - self.hour_rng_cum[j - 24]) / 24)

    def atrd(self, t: int) -> float:
        j = int(np.searchsorted(self.nyday_end, t, side="right"))
        if j < 20:
            return float("nan")
        return float((self.nyday_rng_cum[j] - self.nyday_rng_cum[j - 20]) / 20)

    def last_close_before(self, t: int) -> float:
        i = int(np.searchsorted(self.ts, t, side="left")) - 1
        return float(self.c[i]) if i >= 0 else float("nan")

    def first_open_after(self, t: int) -> float:
        k = int(np.searchsorted(self.ts, t, side="left"))
        return float(self.o[k]) if k < len(self.ts) else float("nan")


def walk(m: Market, k: int, entry: float, side: int, stop_usd: float, target_usd: float | None,
         exit_ns: int) -> tuple[str, float, int] | None:
    """From entry minute k to the last minute before exit_ns: stop first, a gap through the stop fills at the minute's
    open, the target at its level; else the open of the first bar at or after exit_ns. None if that bar is missing."""
    ke = int(np.searchsorted(m.ts, exit_ns, side="left"))
    if ke >= len(m.ts):
        return None
    stop = entry - side * stop_usd
    tgt = entry + side * target_usd if target_usd is not None else None
    if ke > k:
        lo, hi, op = m.lo[k:ke], m.h[k:ke], m.o[k:ke]
        hit_s = lo <= stop if side > 0 else hi >= stop
        hit_t = (hi >= tgt if side > 0 else lo <= tgt) if tgt is not None else np.zeros(len(lo), dtype=bool)
        s_i = int(np.argmax(hit_s)) if hit_s.any() else -1
        t_i = int(np.argmax(hit_t)) if hit_t.any() else -1
        if s_i >= 0 and (t_i < 0 or s_i <= t_i):
            gap = (op[s_i] < stop) if side > 0 else (op[s_i] > stop)
            px = float(op[s_i]) if (s_i > 0 and gap) else stop
            return "stop", px, int(m.ts[k + s_i]) + MIN
        if t_i >= 0:
            return "target", float(tgt), int(m.ts[k + t_i]) + MIN   # type: ignore[arg-type]
    return "time", float(m.o[ke]), int(m.ts[ke])


# ---------------------------------------------------------------- the 33 rules

def at(d: date, z: ZoneInfo, hh: int, mm: int) -> int:
    return _at(d, z.key, hh, mm)


@lru_cache(maxsize=200_000)
def _at(d: date, zkey: str, hh: int, mm: int) -> int:
    return int(pd.Timestamp(datetime.combine(d, time(hh, mm)), tz=zkey).value)


@dataclass
class Cand:
    t: int
    side: int
    stop: float
    exit_ns: int
    target: float | None = None


Rule = Callable[[Market, date, "Ctx"], Cand | None]


class Ctx:
    """Per-strategy state for the rolling percentiles (values of prior qualifying days only)."""

    def __init__(self) -> None:
        self.hist: list[float] = []

    def pct_ok(self, v: float, q: float, above: bool) -> bool:
        """Is v at/above (or at/below) the q-th percentile of the previous <= 250 values (>= 100 needed)? v is
        appended after the comparison, so only strictly earlier days enter."""
        h = self.hist[-250:]
        ok = len(h) >= 100 and (v >= np.percentile(h, q) if above else v <= np.percentile(h, q))
        if np.isfinite(v):
            self.hist.append(v)
        return bool(ok)


def _sg(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def cont(t: int, side_src: float, ok: bool, stop: float, e: int, flip: bool = False) -> Cand | None:
    if not ok or not np.isfinite(stop) or stop <= 0 or _sg(side_src) == 0 or e <= t:
        return None
    s = _sg(side_src)
    return Cand(t, -s if flip else s, stop, e)


def r_win(m: Market, a: int, b: int) -> float:
    return m.price(b) - m.price(a)


def prev_bd(d: date) -> date | None:
    try:
        return bd_shift(d, -1) if is_business_day(d) else None
    except (KeyError, IndexError):
        return None


def next_bd(d: date) -> date | None:
    try:
        return bd_shift(d, 1)
    except (KeyError, IndexError):
        return None


def s01(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, NY, 9, 0)
    r, a = r_win(m, at(d, NY, 8, 30), t), m.atrh(t)
    return cont(t, r, abs(r) >= 0.75 * a, 1.5 * a, at(d, NY, 11, 30))


def s02(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, NY, 10, 30)
    r, a = r_win(m, at(d, NY, 10, 0), t), m.atrh(t)
    return cont(t, r, abs(r) >= 0.75 * a, 1.5 * a, at(d, NY, 13, 30))


def s03(m: Market, d: date, c: Ctx) -> Cand | None:
    p = prev_bd(d)
    if p is None:
        return None
    t = at(d, NY, 12, 30)
    r, a = m.price(t) - m.price(at(p, NY, 13, 30)), m.atrh(t)
    return cont(t, r, abs(r) >= 1.0 * a, 1.5 * a, at(d, NY, 13, 30))


def s04(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, NY, 13, 0)
    r, a = r_win(m, at(d, NY, 8, 20), at(d, NY, 8, 50)), m.atrh(t)
    if not np.isfinite(r):
        return None
    ok = c.pct_ok(abs(r), 67, above=True)
    return cont(t, r, ok, 1.5 * a, at(d, NY, 13, 30))


def s05(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, LDN, 15, 5)
    r, a = r_win(m, at(d, LDN, 14, 35), t), m.atrh(t)
    return cont(t, r, abs(r) >= 0.75 * a, 1.5 * a, at(d, LDN, 16, 0), flip=True)


def s06(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, LDN, 10, 35)
    r, a = r_win(m, at(d, LDN, 10, 5), t), m.atrh(t)
    return cont(t, r, abs(r) >= 0.75 * a, 1.5 * a, at(d, LDN, 12, 0), flip=True)


def s07(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, NY, 13, 30)
    r, a = r_win(m, at(d, NY, 13, 0), t), m.atrh(t)
    return cont(t, r, abs(r) >= 0.5 * a, 1.5 * a, at(d, NY, 14, 30), flip=True)


def s08(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, LDN, 8, 30)
    hi, lo = m.hl(at(d, TKY, 8, 0), at(d, LDN, 8, 0))
    if not np.isfinite(hi):
        return None
    narrow = c.pct_ok(hi - lo, 33, above=False)
    p, a = m.price(t), m.atrh(t)
    side = 1.0 if p > hi else -1.0 if p < lo else 0.0
    return cont(t, side, narrow, 1.5 * a, at(d, LDN, 12, 0))


def s09(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, LDN, 8, 0)
    r, a = r_win(m, at(d, TKY, 8, 0), t), m.atrh(t)
    return cont(t, r, abs(r) >= 2.0 * a, 1.5 * a, at(d, LDN, 12, 0), flip=True)


def s10(m: Market, d: date, c: Ctx) -> Cand | None:
    t, t0 = at(d, NY, 8, 25), at(d, LDN, 8, 0)
    r, a = r_win(m, t0, t), m.atrh(t)
    hi, lo = m.hl(t0, t)
    if not (np.isfinite(r) and np.isfinite(hi)) or hi <= lo:
        return None
    pos = (m.price(t) - lo) / (hi - lo)
    ext = pos >= 0.8 if r > 0 else pos <= 0.2
    return cont(t, r, abs(r) >= 2.0 * a and ext, 1.5 * a, at(d, NY, 11, 0))


def s11(m: Market, d: date, c: Ctx) -> Cand | None:
    t, t1 = at(d, NY, 9, 0), at(d, NY, 8, 25)
    big, a = r_win(m, at(d, LDN, 8, 0), t1), m.atrh(t)
    n = r_win(m, t1, t)
    ok = abs(big) >= 1.5 * a and _sg(n) == -_sg(big) and abs(n) >= 0.3 * abs(big)
    return cont(t, big, ok, 1.5 * a, at(d, NY, 12, 0), flip=True)


def s12(m: Market, d: date, c: Ctx) -> Cand | None:
    p = prev_bd(d)
    if p is None:
        return None
    t = at(d, NY, 9, 0)
    o, a = m.price(t) - m.price(at(p, NY, 16, 59)), m.atrh(t)
    if not np.isfinite(o):
        return None
    ok = c.pct_ok(abs(o), 90, above=True)
    return cont(t, o, ok, 1.5 * a, at(d, NY, 12, 0), flip=True)


def s13(m: Market, d: date, c: Ctx) -> Cand | None:
    p = prev_bd(d)
    if p is None:
        return None
    t = at(d, LDN, 8, 0)
    y, a = m.price(at(p, NY, 16, 59)) - m.price(at(p, NY, 8, 30)), m.atrh(t)
    return cont(t, y, abs(y) >= 2.0 * a, 1.5 * a, at(d, LDN, 12, 0))


def _sat_before(d: date) -> int:
    """00:00 UTC of the Saturday before date d (d is a Monday)."""
    return int(pd.Timestamp(d - timedelta(days=(d.weekday() + 2) % 7 or 7), tz="UTC").value)


def s14(m: Market, d: date, c: Ctx) -> Cand | None:      # d = Monday, Tokyo date
    if d.weekday() != 0:
        return None
    t = at(d, TKY, 8, 0)
    g, a = m.price(t) - m.last_close_before(_sat_before(d)), m.atrh(t)
    return cont(t, g, abs(g) >= 0.75 * a, 2.0 * a, at(d, LDN, 8, 0), flip=True)


def s15(m: Market, d: date, c: Ctx) -> Cand | None:
    if d.weekday() != 4:
        return None
    t = at(d, NY, 12, 0)
    w, a = m.price(t) - m.first_open_after(_sat_before(d - timedelta(days=4))), m.atrd(t)
    return cont(t, w, abs(w) >= 1.0 * a, 1.5 * m.atrh(t), at(d, NY, 16, 30), flip=True)


def s16(m: Market, d: date, c: Ctx) -> Cand | None:
    if d.weekday() != 0:
        return None
    t = at(d, LDN, 8, 0)
    g, a = m.price(t) - m.last_close_before(_sat_before(d)), m.atrh(t)
    return cont(t, g, abs(g) >= 1.0 * a, 1.5 * a, at(d, NY, 12, 0))


def _mtd(m: Market, d: date, t: int) -> float:
    return m.price(t) - m.last_close_before(int(pd.Timestamp(date(d.year, d.month, 1), tz="UTC").value))


def s17(m: Market, d: date, c: Ctx) -> Cand | None:
    if d not in calendar_sets()["month_last2"]:
        return None
    t = at(d, LDN, 14, 0)
    return cont(t, _mtd(m, d, t), True, 1.5 * m.atrh(t), at(d, LDN, 16, 0), flip=True)


def s18(m: Market, d: date, c: Ctx) -> Cand | None:
    if d not in calendar_sets()["month_last2"]:
        return None
    t = at(d, LDN, 16, 5)
    return cont(t, _mtd(m, d, t), True, 1.5 * m.atrh(t), at(d, NY, 13, 30))


def s19(m: Market, d: date, c: Ctx) -> Cand | None:
    if d not in calendar_sets()["quarter_turn"]:
        return None
    t = at(d, LDN, 8, 0)
    return cont(t, 1.0, True, 2.0 * m.atrh(t), at(d, NY, 13, 30))


def s20(m: Market, d: date, c: Ctx) -> Cand | None:
    if d not in calendar_sets()["expiry3"]:
        return None
    t = at(d, NY, 10, 0)
    p, a = m.price(t), m.atrh(t)
    if not (np.isfinite(p) and np.isfinite(a)):
        return None
    k = round(p / 25.0) * 25.0
    dist = k - p
    if not (0.5 * a <= abs(dist) <= 2.0 * a):
        return None
    e = at(d, NY, 13, 30)
    return Cand(t, _sg(dist), abs(dist), e, target=abs(dist)) if e > t else None


def s21(m: Market, d: date, c: Ctx) -> Cand | None:
    if d not in calendar_sets()["expiry3"]:
        return None
    t = at(d, NY, 11, 30)
    r, a = r_win(m, at(d, NY, 8, 30), t), m.atrh(t)
    return cont(t, r, abs(r) >= 1.0 * a, 1.5 * a, at(d, NY, 13, 30), flip=True)


def _fixed(setname: str, side: float, tz: ZoneInfo, hh: int, mm: int, stop_k: float,
           ez: ZoneInfo, ehh: int, emm: int) -> Rule:
    def rule(m: Market, d: date, c: Ctx) -> Cand | None:
        if d not in calendar_sets()[setname]:
            return None
        t = at(d, tz, hh, mm)
        return cont(t, side, np.isfinite(m.price(t)), stop_k * m.atrh(t), at(d, ez, ehh, emm))
    return rule


s22 = _fixed("pre_fnd5", -1.0, NY, 8, 35, 1.5, NY, 13, 30)
s23 = _fixed("post_fnd3", 1.0, NY, 8, 35, 1.5, NY, 13, 30)
s24 = _fixed("pre_us_hol2", 1.0, LDN, 8, 0, 2.0, NY, 12, 0)
s32 = _fixed("month_first2", 1.0, NY, 8, 35, 1.5, NY, 13, 30)


def s25(m: Market, d: date, c: Ctx) -> Cand | None:
    if d not in calendar_sets()["thin"]:
        return None
    t = at(d, LDN, 11, 0)
    return cont(t, r_win(m, at(d, LDN, 8, 0), t), True, 1.5 * m.atrh(t), at(d, LDN, 14, 0), flip=True)


def s26(m: Market, d: date, c: Ctx) -> Cand | None:
    if d.weekday() > 3:
        return None
    nb = next_bd(d) if is_business_day(d) else None
    if nb is None:
        return None
    t, t0 = at(d, NY, 16, 0), at(d, NY, 8, 30)
    dd, a = r_win(m, t0, t), m.atrh(t)
    hi, lo = m.hl(t0, t)
    if not (np.isfinite(dd) and np.isfinite(hi)) or hi <= lo:
        return None
    pos = (m.price(t) - lo) / (hi - lo)
    ext = pos >= 0.85 if dd > 0 else pos <= 0.15
    return cont(t, dd, abs(dd) >= 2.5 * a and ext, 2.0 * a, at(nb, LDN, 8, 0))


def s27(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, TKY, 9, 0)
    r, a = r_win(m, at(d, TKY, 8, 0), t), m.atrh(t)
    return cont(t, r, abs(r) >= 0.75 * a, 1.5 * a, at(d, TKY, 12, 0))


def s28(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, LDN, 12, 0)
    r, a = r_win(m, at(d, LDN, 8, 0), t), m.atrh(t)
    return cont(t, r, abs(r) >= 1.5 * a, 1.5 * a, at(d, LDN, 13, 25), flip=True)


def s29(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, NY, 13, 35)
    r, a = r_win(m, at(d, NY, 8, 30), at(d, NY, 13, 30)), m.atrh(t)
    return cont(t, r, abs(r) >= 1.5 * a, 1.5 * a, at(d, NY, 16, 30))


def s30(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, LDN, 7, 30)
    r, a = r_win(m, at(d, LDN, 7, 0), t), m.atrh(t)
    return cont(t, r, abs(r) >= 0.75 * a, 1.5 * a, at(d, LDN, 10, 0))


def s31(m: Market, d: date, c: Ctx) -> Cand | None:
    if d.weekday() != 0:
        return None
    t = at(d, LDN, 8, 0)
    sat = _sat_before(d)
    wk = m.last_close_before(sat) - m.first_open_after(sat - 7 * 24 * HOUR)
    return cont(t, wk, abs(wk) >= 1.5 * m.atrd(t), 2.0 * m.atrh(t), at(d, NY, 13, 30), flip=True)


def s33(m: Market, d: date, c: Ctx) -> Cand | None:
    t = at(d, SH, 14, 0)
    r, a = m.price(t) - m.price(at(d, TKY, 8, 0)), m.atrh(t)
    return cont(t, 1.0, r <= -1.0 * a, 1.5 * a, at(d, LDN, 8, 0))


RULES: dict[str, Rule] = {f"{i:02d}": f for i, f in enumerate(
    [s01, s02, s03, s04, s05, s06, s07, s08, s09, s10, s11, s12, s13, s14, s15, s16, s17, s18, s19, s20, s21, s22,
     s23, s24, s25, s26, s27, s28, s29, s30, s31, s32, s33], start=1)}


# ---------------------------------------------------------------- screen

def weekdays(lo: date, hi: date) -> Iterator[date]:
    d = lo
    while d <= hi:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def candidates(m: Market, sid: str, lo: date, hi: date, warm_from: date) -> list[Cand]:
    """Every candidate of strategy sid with decision date in [lo, hi]; days from warm_from feed the percentiles only."""
    rule, ctx, out = RULES[sid], Ctx(), []
    last_exit = -1
    for d in weekdays(warm_from, hi):
        cd = rule(m, d, ctx)
        if cd is None or d < lo:
            continue
        if not np.isfinite(cd.stop) or cd.stop <= 0 or cd.t < last_exit:
            continue
        out.append(cd)
        last_exit = cd.exit_ns
    return out


def cost_net(m: Market, costs: A.ArmCosts, ks: np.ndarray, sides: np.ndarray, entry: np.ndarray, exit_px: np.ndarray,
             stop_usd: np.ndarray, stop_hit: np.ndarray, t_in: np.ndarray, t_out: np.ndarray,
             overshoot: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    swap = SwapSpec.shipped()
    nights = rollover_nights(pd.DatetimeIndex(pd.to_datetime(t_in, utc=True)),
                             pd.DatetimeIndex(pd.to_datetime(t_out, utc=True)), swap.server_tz,
                             swap.triple_weekday) if len(ks) else np.zeros(0)
    move = sides * (exit_px - entry) + nights * swap.usd_per_oz(sides)
    sess = m.cost_label[ks].astype(str) if len(ks) else np.zeros(0, dtype=str)
    net = A.screen_net_r(move, stop_usd, stop_hit, costs, broker=BROKER, sessions=sess, version=COST_VERSION,
                         overshoot_usd=overshoot)
    return move / stop_usd, net


def simulate(m: Market, cands: list[Cand], hi_ns: int) -> pd.DataFrame:
    rows = []
    for cd in cands:
        k = m.entry_k(cd.t)
        if k is None:
            continue
        entry = float(m.o[k])
        w = walk(m, k, entry, cd.side, cd.stop, cd.target, cd.exit_ns)
        if w is None or w[2] >= hi_ns:
            continue
        rows.append({"k": k, "t": cd.t, "entry_ns": int(m.ts[k]), "side": cd.side, "stop_usd": cd.stop,
                     "target_usd": cd.target, "hold_ns": cd.exit_ns - cd.t, "entry": entry, "kind": w[0],
                     "exit_px": w[1], "exit_ns": w[2]})
    return pd.DataFrame(rows)


def control(m: Market, tr: pd.DataFrame, costs: A.ArmCosts, rng: np.random.Generator, hi_ns: int,
            leak_free: bool = False) -> np.ndarray:
    """Per trade: the mean net R of N_DRAWS random entries in the same session instance (same side, stop, target R,
    hold length, cost rules). A draw whose exit is missing or reaches the window end is redrawn (up to 20 tries each)."""
    rows: list[tuple[int, int, int, float, float, float, bool, int, int]] = []
    k_a, side_a = tr["k"].to_numpy(int), tr["side"].to_numpy(int)
    stop_a, hold_a = tr["stop_usd"].to_numpy(float), tr["hold_ns"].to_numpy(np.int64)
    tgt_a = pd.to_numeric(tr["target_usd"], errors="coerce").to_numpy(float)
    for j in range(len(tr)):
        lo, hi = int(m.sess_lo[k_a[j]]), int(m.sess_hi[k_a[j]])
        if leak_free:
            lo = int(k_a[j])          # amendment A1: draws at or after the signal's entry minute only
        tgt = float(tgt_a[j]) if np.isfinite(tgt_a[j]) else None
        side, stop_usd, hold = int(side_a[j]), float(stop_a[j]), int(hold_a[j])
        got, tries = 0, 0
        while got < N_DRAWS and tries < N_DRAWS * 20:
            tries += 1
            if hi - lo >= 2:
                kk = int(rng.integers(lo, hi))
            else:                     # A1 fallback: the same UTC time of day on another day within +-30 days
                off = int(rng.integers(1, 31)) * (1 if rng.random() < 0.5 else -1)
                kq = m.entry_k(int(m.ts[k_a[j]]) + off * 24 * HOUR)
                if kq is None or m.ts[kq] - m.ts[k_a[j]] - off * 24 * HOUR != 0:
                    continue
                kk = kq
            e = float(m.o[kk])
            w = walk(m, kk, e, side, stop_usd, tgt, int(m.ts[kk]) + hold)
            if w is None or w[2] >= hi_ns:
                continue
            rows.append((j, kk, side, e, w[1], stop_usd, w[0] == "stop", int(m.ts[kk]), w[2]))
            got += 1
    out = np.full(len(tr), np.nan)
    if not rows:
        return out
    c = np.array(rows, dtype=object)
    _, net = cost_net(m, costs, c[:, 1].astype(int), c[:, 2].astype(int), c[:, 3].astype(float),
                      c[:, 4].astype(float), c[:, 5].astype(float), c[:, 6].astype(bool), c[:, 7].astype(np.int64),
                      c[:, 8].astype(np.int64))
    mean = pd.Series(net).groupby(c[:, 0].astype(int)).mean()
    out[mean.index.to_numpy()] = mean.to_numpy()
    return out


def day_t(net: np.ndarray, entry_ns: np.ndarray) -> tuple[float, int]:
    days = pd.Series(net).groupby(pd.to_datetime(entry_ns, utc=True).floor("D")).sum().to_numpy()
    if len(days) < 2 or np.std(days, ddof=1) == 0:
        return float("nan"), len(days)
    return float(np.mean(days) / (np.std(days, ddof=1) / np.sqrt(len(days)))), len(days)


_M: Market | None = None
_COSTS: A.ArmCosts | None = None


def _init(bars: str, y0: int, y1: int) -> None:
    global _M, _COSTS
    _COSTS = A.load_costs(ROOT / "config" / "arm_costs.yaml")
    _COSTS.require_version(COST_VERSION)                    # before any bar is read
    _M = Market.of(load_market(Path(bars), y0, y1))


def run_one(args: tuple[str, str, str]) -> dict[str, Any]:
    sid, phase, out = args
    assert _M is not None and _COSTS is not None
    m, costs = _M, _COSTS
    lo, hi, y0, _ = WINDOWS[phase]
    warm = date(y0, 1, 1)
    hi_ns = min(int(pd.Timestamp(hi + timedelta(days=1), tz="UTC").value), HOLDOUT_NS)   # exits stay in the window
    tr = simulate(m, candidates(m, sid, lo, hi, warm), hi_ns)
    res: dict[str, Any] = {"id": f"S200-TIME-{sid}", "phase": phase, "n": len(tr), "cost_of_record": COST_VERSION,
                           "broker": BROKER}
    if tr.empty:
        return res
    ks = tr["k"].to_numpy(int)
    args_c = (m, costs, ks, tr["side"].to_numpy(int), tr["entry"].to_numpy(float), tr["exit_px"].to_numpy(float),
              tr["stop_usd"].to_numpy(float), (tr["kind"] == "stop").to_numpy(), tr["entry_ns"].to_numpy(np.int64),
              tr["exit_ns"].to_numpy(np.int64))
    gross, net = cost_net(*args_c)
    _, stress = cost_net(*args_c, overshoot=A.OVERSHOOT_STRESS_USD)
    tr["gross_r"], tr["net_r"], tr["net_r_stress"] = gross, net, stress
    rng = np.random.default_rng([SEED, int(sid), 0 if phase == "explore" else 1])
    tr["control_r"] = control(m, tr, costs, rng, hi_ns)                       # as pre-registered (reported)
    tr["control_lf_r"] = control(m, tr, costs, rng, hi_ns, leak_free=True)     # amendment A1: the gate's control
    t, n_days = day_t(net, tr["entry_ns"].to_numpy(np.int64))
    years = pd.to_datetime(tr["entry_ns"], utc=True).dt.year
    by_year = tr.groupby(years)["net_r"].mean()
    res.update({
        "n_days": n_days, "gross_mean_r": float(np.mean(gross)), "net_mean_r": float(np.mean(net)),
        "net_stress_mean_r": float(np.mean(stress)), "t_day": t,
        "t_adj_day": DsrInputs.of(pd.Series(net).groupby(pd.to_datetime(tr["entry_ns"], utc=True).dt.floor("D"))
                                  .sum().to_numpy()).t(),
        "control_mean_r": float(np.nanmean(tr["control_r"])),
        "vs_control_r": float(np.mean(net) - np.nanmean(tr["control_r"])),
        "control_lf_mean_r": float(np.nanmean(tr["control_lf_r"])),
        "vs_control_lf_r": float(np.mean(net) - np.nanmean(tr["control_lf_r"])),
        "stop_share": float((tr["kind"] == "stop").mean()), "mean_stop_usd": float(tr["stop_usd"].mean()),
        "years": dict(zip(by_year.index.to_numpy(int).tolist(), by_year.to_numpy(float).tolist(), strict=True)),
        "years_positive": int((by_year > 0).sum()), "n_years": len(by_year)})
    if phase == "explore":
        res["pass"] = bool(len(tr) >= 100 and res["net_mean_r"] > 0 and t >= 2.0 and res["vs_control_lf_r"] >= 0.05)
    else:
        entry_utc = pd.DatetimeIndex(pd.to_datetime(tr["entry_ns"], utc=True))
        dsr = day_summed_dsr(net, entry_utc, K_PLANNED)
        res["dsr_day_summed_k3229"] = dsr.dsr_day_summed if dsr else None
        res["dsr_day_summed_k5000"] = dsr.at(K_STRESS).dsr_day_summed if dsr else None
        res["pass"] = bool(res["net_mean_r"] > 0 and t >= 2.0 and dsr is not None and dsr.dsr_day_summed > 0.5
                           and res["years_positive"] >= 6)
    o = Path(out).expanduser()
    o.mkdir(parents=True, exist_ok=True)
    tr.to_parquet(o / f"S200-TIME-{sid}__{phase}.parquet", index=False)
    (o / f"S200-TIME-{sid}__{phase}.json").write_text(json.dumps(res, indent=2))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=list(WINDOWS))
    ap.add_argument("--ids", default=",".join(RULES))
    ap.add_argument("--bars", default=str(Path.home() / "myproject" / "goldbot" / "raw"))
    ap.add_argument("--out", default=str(Path.home() / "tmp" / "s200time"))
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    ids = [s.strip().zfill(2) for s in a.ids.split(",") if s.strip()]
    if a.phase == "confirm" and a.ids == ",".join(RULES):
        ap.error("confirm needs --ids: explore-passers only")
    _, _, y0, y1 = WINDOWS[a.phase]
    jobs = [(s, a.phase, a.out) for s in ids]
    with Pool(min(a.workers, 2), initializer=_init, initargs=(a.bars, y0, y1)) as pool:
        results = pool.map(run_one, jobs)
    for r in results:
        print(json.dumps({k: v for k, v in r.items() if k != "years"}))
    Path(a.out).expanduser().joinpath(f"summary_{a.phase}.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
