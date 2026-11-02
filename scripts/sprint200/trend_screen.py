"""Sprint-200 lane TREND: the 34 pre-registered strategies (docs/research/sprint200/trend-prereg.md) on Dukascopy 1m.

    .venv/bin/python scripts/sprint200/trend_screen.py --window explore --out ~/tmp/sprint200_trend/explore.json
    .venv/bin/python scripts/sprint200/trend_screen.py --window confirm --passers ~/tmp/sprint200_trend/explore.json \
        --out ~/tmp/sprint200_trend/confirm.json
    .venv/bin/python scripts/sprint200/trend_screen.py --parity      (engine vs the harness on synthetic brackets)

Conventions (prereg section 1): mid prices; entry at the open of the first 1m bar at or after the decision close; stop
first in a shared minute, a gap through the stop fills at the minute's open; ICM cost of record cor-2026-10-31.2 via
`ArmCosts.screen_charges` (round trip upper bound, overshoot floor 0.12) plus the shipped swap RATE per rollover (on the
trade's own entry price, `SwapSpec.research_shipped`, ADR on swap as a rate). Four matched
random-entry twins per trade (same day x session x side, post-fill minutes only). No row at or after 2025-10-01 is read:
the 2026 file is never opened and the 2025 file is cut and asserted. Writes aggregate statistics only (JSON, not
committed); no trade rows leave the process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.labels import SwapSpec, rollover_nights  # noqa: E402
from goldbot.research import arms as A  # noqa: E402
from goldbot.research.metrics import MIN_TRADES_FOR_DSR, DsrInputs  # noqa: E402

COST_VERSION = "cor-2026-10-31.2"
BROKER = "icm"
STRESS_OVERSHOOT = 0.70
N_TWIN = 4
K_PREREG = 3_233
MIN = 60_000_000_000
HOUR = 60 * MIN
DAYNS = 24 * HOUR
NYZ = "America/New_York"
HOLDOUT = pd.Timestamp("2025-10-01", tz="UTC")
WINDOWS = {  # data from, first decision, end (exclusive)
    "explore": (pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2010-01-01", tz="UTC"),
                pd.Timestamp("2018-01-01", tz="UTC")),
    "confirm": (pd.Timestamp("2016-07-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC"), HOLDOUT),
}
TF_NS = {"15m": 15 * MIN, "1h": HOUR, "4h": 4 * HOUR}
SESS = list(DEFAULT_SESSIONS.sessions_local)          # asia, london, newyork
ASIA, LONDON, NEWYORK = (SESS.index(s) for s in ("asia", "london", "newyork"))


# ---------------------------------------------------------------- data
def load_minutes(raw: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    assert end <= HOLDOUT, "a window past the holdout"
    parts = []
    for y in range(start.year, end.year + 1):
        if y > 2025 or pd.Timestamp(f"{y}-01-01", tz="UTC") >= end:
            continue                                   # the 2026 file is never opened
        p = pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet",
                            columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close",
                                     "ask_open", "ask_high", "ask_low", "ask_close"])
        ts = pd.DatetimeIndex(p["ts_utc"]).as_unit("ns")
        parts.append(p.loc[np.asarray((ts >= start) & (ts < end))])
    b = pd.concat(parts, ignore_index=True)
    ts = pd.DatetimeIndex(b["ts_utc"]).as_unit("ns")
    f = pd.DataFrame({"t": _ns(ts)})
    for k in ("open", "high", "low", "close"):
        f[k[0]] = (b[f"bid_{k}"].to_numpy(float) + b[f"ask_{k}"].to_numpy(float)) / 2
    f = f.sort_values("t", kind="stable").drop_duplicates("t").reset_index(drop=True)
    assert int(f.t.max()) < HOLDOUT.value and int(f.t.max()) < end.value, "a row at or after the window end was read"
    return f


def _ns(ts: pd.DatetimeIndex) -> np.ndarray:
    from goldbot.data.timeutil import epoch_ns
    return np.asarray(epoch_ns(ts), dtype=np.int64)


@dataclass
class Bars:
    tf: str
    start: np.ndarray
    dec: np.ndarray                 # decision time (close) ns
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray

    def s(self, name: str) -> pd.Series:
        return pd.Series(getattr(self, name))


def make_bars(key: np.ndarray, dec_of_key: Callable[[np.ndarray], np.ndarray], m: pd.DataFrame, tf: str) -> Bars:
    first = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    last = np.r_[first[1:] - 1, len(key) - 1]
    o, h, lo, c = (m[x].to_numpy() for x in ("o", "h", "l", "c"))
    return Bars(tf, m.t.to_numpy()[first], dec_of_key(key[first]), o[first], np.maximum.reduceat(h, first),
                np.minimum.reduceat(lo, first), c[last])


@dataclass
class Ctx:
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    run_end: np.ndarray             # last minute index of each minute's session run (same code and instance start)
    run_start: np.ndarray           # first minute index of that run
    code: np.ndarray
    tday: np.ndarray                # trading-day number (days since epoch of the NY+7h date)
    bars: dict[str, Bars]
    day: pd.DataFrame               # per trading day: session opens/ends/ranges, FLAT, NY 12:00 / 15:00
    dec_from: int
    end: int
    extra: dict[str, Any] = field(default_factory=dict)


def build_ctx(raw: Path, window: str) -> Ctx:
    start, dec_from, end = WINDOWS[window]
    m = load_minutes(raw, start, end)
    t = m.t.to_numpy()
    idx = pd.DatetimeIndex(pd.to_datetime(t, utc=True))
    ny = idx.tz_convert(NYZ)
    tday = (ny.tz_localize(None) + pd.Timedelta(hours=7)).normalize().to_numpy().astype("datetime64[D]").astype(
        np.int64)
    code, sstart = DEFAULT_SESSIONS.session_windows(idx)
    chg = np.r_[True, (np.diff(code) != 0) | (np.diff(sstart) != 0)]
    rid = np.cumsum(chg) - 1
    ends = np.r_[np.flatnonzero(chg)[1:] - 1, len(t) - 1]
    run_end = ends[rid]
    run_start = np.flatnonzero(chg)[rid]
    bars: dict[str, Bars] = {}
    for tf, sec in TF_NS.items():
        bars[tf] = make_bars(t // sec, _closer(sec), m, tf)
    udays = np.unique(tday)
    close17 = _ny_time(udays, 17, 0)
    dmap = dict(zip(udays.tolist(), close17.tolist(), strict=True))
    bars["1d"] = make_bars(tday, lambda k: np.array([dmap[int(x)] for x in k], dtype=np.int64), m, "1d")
    # per trading day x session aggregates
    df = pd.DataFrame({"td": tday, "code": code, "start": sstart, "t": t, "o": m.o, "h": m.h, "l": m.l, "c": m.c})
    df = df[df.code >= 0]
    g = df.groupby(["td", "code"], sort=True)
    agg = g.agg(start=("start", "first"), n=("t", "size"), last=("t", "last"), o=("o", "first"), h=("h", "max"),
                l=("l", "min"), c=("c", "last"))
    day = pd.DataFrame(index=pd.Index(udays, name="td"))
    for k, nm in ((ASIA, "asia"), (LONDON, "lon"), (NEWYORK, "ny")):
        a = agg.xs(k, level="code") if k in agg.index.get_level_values("code") else pd.DataFrame()
        for col in ("start", "n", "last", "o", "h", "l", "c"):
            day[f"{nm}_{col}"] = a[col].reindex(day.index) if len(a) else np.nan
    day["flat"] = _ny_time(udays, 16, 30)
    day["ny12"] = _ny_time(udays, 12, 0)
    day["ny15"] = _ny_time(udays, 15, 0)
    return Ctx(t, m.o.to_numpy(), m.h.to_numpy(), m.l.to_numpy(), m.c.to_numpy(), run_end, run_start, code, tday,
               bars, day,
               dec_from.value, end.value)


def _closer(sec: int) -> Callable[[np.ndarray], np.ndarray]:
    def f(k: np.ndarray) -> np.ndarray:
        return (k + 1) * sec
    return f


def _ny_time(days: np.ndarray, hh: int, mm: int) -> np.ndarray:
    local = pd.DatetimeIndex(pd.to_datetime(days.astype("int64") * DAYNS)) + pd.Timedelta(hours=hh, minutes=mm)
    return _ns(local.tz_localize(NYZ, ambiguous="NaT", nonexistent="shift_forward").tz_convert("UTC"))


# ---------------------------------------------------------------- indicators
def atr(b: Bars, n: int) -> pd.Series:
    pc = pd.Series(b.c).shift(1)
    tr = pd.concat([pd.Series(b.h - b.lo), (pd.Series(b.h) - pc).abs(), (pd.Series(b.lo) - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def true_range(b: Bars) -> pd.Series:
    pc = pd.Series(b.c).shift(1)
    return pd.concat([pd.Series(b.h - b.lo), (pd.Series(b.h) - pc).abs(), (pd.Series(b.lo) - pc).abs()], axis=1).max(axis=1)


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False).mean()


def wilder(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1.0 / n, adjust=False).mean()


def rsi(c: pd.Series, n: int) -> pd.Series:
    d = c.diff()
    up, dn = wilder(d.clip(lower=0).fillna(0), n), wilder((-d).clip(lower=0).fillna(0), n)
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def asof(dec_hi: np.ndarray, val: np.ndarray | pd.Series, at: np.ndarray) -> np.ndarray:
    """Value of the higher-timeframe series as of its last bar with decision time <= `at`."""
    v = np.asarray(val, dtype=float)
    k = np.searchsorted(dec_hi, at, side="right") - 1
    out = np.where(k >= 0, v[np.clip(k, 0, None)], np.nan)
    return out


def d1_trend(ctx: Ctx, at: np.ndarray) -> np.ndarray:
    d = ctx.bars["1d"]
    c = d.s("c")
    e50 = ema(c, 50)
    up = ((c > e50) & (e50 > e50.shift(10))).astype(float).to_numpy()
    dn = ((c < e50) & (e50 < e50.shift(10))).astype(float).to_numpy()
    u, w = asof(d.dec, up, at), asof(d.dec, dn, at)
    return np.where(u == 1, 1, np.where(w == 1, -1, 0))


def atrd_asof(ctx: Ctx, at: np.ndarray, n: int = 20) -> np.ndarray:
    d = ctx.bars["1d"]
    return asof(d.dec, atr(d, n), at)


# ---------------------------------------------------------------- signals
# A signal frame: dec (ns), side, risk (USD; nan when stop_px is set), stop_px (nan or a level), tgt (R multiple, nan =
# none), trail (USD, nan = none), cap (ns, absolute time cap), dl (deadline kind: '', 'flat', 'ny_open', 'utc0645').
@dataclass
class Spec:
    sig: pd.DataFrame
    exit_flags: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None   # (bar dec, long exit, short exit)


def frame(dec: Any, side: Any, *, risk: Any = np.nan, stop_px: Any = np.nan, tgt: Any = np.nan, trail: Any = np.nan,
          cap: Any = np.iinfo(np.int64).max, dl: str = "") -> pd.DataFrame:
    dec = np.asarray(dec, dtype=np.int64)
    n = len(dec)

    def arr(x: Any, dt: Any = float) -> np.ndarray:
        a = np.asarray(x, dtype=dt)
        return np.full(n, a, dtype=dt) if a.ndim == 0 else a
    return pd.DataFrame({"dec": dec, "side": arr(side, np.int64), "risk": arr(risk), "stop_px": arr(stop_px),
                         "tgt": arr(tgt), "trail": arr(trail), "cap": arr(cap, np.int64), "dl": dl})


def capd(b: Bars, i: np.ndarray, hold: int) -> np.ndarray:
    return b.dec[np.minimum(i + hold, len(b.dec) - 1)]


def bar_rule(b: Bars, long: np.ndarray, short: np.ndarray, risk: Any, *, tgt: float = np.nan,
             trail: np.ndarray | float = np.nan, hold: int, stop_px: np.ndarray | None = None) -> pd.DataFrame:
    long, short = np.nan_to_num(long).astype(bool), np.nan_to_num(short).astype(bool)
    i = np.flatnonzero(long | short)
    side = np.where(long[i], 1, -1)
    tr = np.asarray(trail, dtype=float)
    tr = tr[i] if tr.ndim else tr
    sp = stop_px[i] if stop_px is not None else np.nan
    return frame(b.dec[i], side, risk=np.asarray(risk, dtype=float)[i] if stop_px is None else np.nan, stop_px=sp,
                 tgt=tgt, trail=tr, cap=capd(b, i, hold))


def fresh(x: pd.Series) -> np.ndarray:
    x = x.fillna(False).astype(bool)
    return (x & ~x.shift(1, fill_value=False)).to_numpy()


def s01(ctx: Ctx) -> Spec:
    b = ctx.bars["4h"]
    c, a = b.s("c"), atr(b, 14)
    hi, lo = b.s("h").rolling(30).max().shift(1), b.s("lo").rolling(30).min().shift(1)
    return Spec(bar_rule(b, fresh(c > hi), fresh(c < lo), 2 * a, trail=3 * a.to_numpy(), hold=60))


def s02(ctx: Ctx) -> Spec:
    b = ctx.bars["4h"]
    c, a = b.s("c"), atr(b, 14)
    hi, lo = b.s("h").rolling(20).max().shift(1), b.s("lo").rolling(20).min().shift(1)
    tr = d1_trend(ctx, b.dec)
    return Spec(bar_rule(b, fresh(c > hi) & (tr == 1), fresh(c < lo) & (tr == -1), 2 * a, tgt=3.0, hold=30))


def s03(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    c, a = b.s("c"), atr(b, 14)
    hi, lo = b.s("h").rolling(48).max().shift(1), b.s("lo").rolling(48).min().shift(1)
    tr = d1_trend(ctx, b.dec)
    return Spec(bar_rule(b, fresh(c > hi) & (tr == 1), fresh(c < lo) & (tr == -1), 2 * a, trail=3 * a.to_numpy(),
                         hold=48))


def s04(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    c, a = b.s("c"), atr(b, 14)
    hi, lo = b.s("h").rolling(24).max().shift(1), b.s("lo").rolling(24).min().shift(1)
    x = (true_range(b) >= 1.5 * atr(b, 20).shift(1)).to_numpy()
    return Spec(bar_rule(b, fresh(c > hi) & x, fresh(c < lo) & x, 1.5 * a, trail=2.5 * a.to_numpy(), hold=24))


def s05(ctx: Ctx) -> Spec:
    b = ctx.bars["4h"]
    c, a = b.s("c"), atr(b, 14)
    e = ema(c, 20)
    return Spec(bar_rule(b, fresh(c > e + 2 * a), fresh(c < e - 2 * a), 1.5 * a, trail=2.5 * a.to_numpy(), hold=30))


def s06(ctx: Ctx) -> Spec:
    d = ctx.bars["1d"]
    dc = d.s("c")
    up = (dc > d.s("h").rolling(20).max().shift(1)).to_numpy()
    dn = (dc < d.s("lo").rolling(20).min().shift(1)).to_numpy()
    ii = np.arange(len(dc))
    last_up = pd.Series(np.where(up, ii, np.nan)).ffill().to_numpy()
    last_dn = pd.Series(np.where(dn, ii, np.nan)).ffill().to_numpy()
    lu, ld = np.nan_to_num(last_up, nan=-1e9), np.nan_to_num(last_dn, nan=-1e9)
    st_up = ((ii - lu) <= 4) & (ld < lu)
    st_dn = ((ii - ld) <= 4) & (lu < ld)
    b = ctx.bars["4h"]
    c, a = b.s("c"), atr(b, 14)
    e = ema(c, 10)
    su, sd = asof(d.dec, st_up.astype(float), b.dec) == 1, asof(d.dec, st_dn.astype(float), b.dec) == 1
    xl = ((c.shift(1) < e.shift(1)) & (c > e)).to_numpy()
    xs = ((c.shift(1) > e.shift(1)) & (c < e)).to_numpy()
    return Spec(bar_rule(b, xl & su, xs & sd, 1.5 * a, tgt=2.0, hold=18))


def _first_per_day(dec: np.ndarray, side: np.ndarray, key: np.ndarray, extra: dict[str, np.ndarray],
                   per_side: bool = False) -> pd.DataFrame:
    df = pd.DataFrame({"dec": dec, "side": side, "key": key, **extra})
    df = df.sort_values("dec", kind="stable")
    df = df.drop_duplicates(["key", "side"] if per_side else ["key"], keep="first")
    return df


def _session_break(ctx: Ctx, tf: str, lo_t: np.ndarray, hi_t: np.ndarray, top: np.ndarray, bot: np.ndarray,
                   stop_long: np.ndarray, stop_short: np.ndarray, td_of_bar: np.ndarray, *, buffer: Any = 0.0,
                   gate: np.ndarray | None = None, lo_incl: bool = True, bounds: tuple[float, float] = (0.5, 4.0),
                   atr_ref: Any = None, tgt: float = np.nan, dl: str = "flat") -> pd.DataFrame:
    """First bar close per day inside [lo_t, hi_t] (or (lo_t, hi_t]) beyond top + buffer (long) / bot - buffer
    (short); structural stop level; skip if the decision-close risk is outside bounds x atr_ref."""
    b = ctx.bars[tf]
    in_win = ((b.dec >= lo_t) if lo_incl else (b.dec > lo_t)) & (b.dec <= hi_t)
    buf = np.asarray(buffer, dtype=float)
    L = in_win & (b.c > top + buf)
    S = in_win & (b.c < bot - buf)
    if gate is not None:
        L, S = L & (gate == 1), S & (gate == -1)
    i = np.flatnonzero(L | S)
    side = np.where(L[i], 1, -1)
    sp = np.where(side > 0, stop_long[i], stop_short[i])
    df = _first_per_day(b.dec[i], side, td_of_bar[i], {"sp": sp, "cl": b.c[i], "ar": atr_ref[i]})
    rk = (df.cl - df.sp) * df.side
    ok = (rk >= bounds[0] * df.ar) & (rk <= bounds[1] * df.ar)
    df = df[ok.to_numpy()]
    return frame(df.dec, df.side, stop_px=df.sp, tgt=tgt, dl=dl)


def _bar_day(ctx: Ctx, b: Bars) -> tuple[np.ndarray, pd.DataFrame]:
    """Trading day of each bar (of its last minute) and the day table rows aligned to the bars."""
    k = np.searchsorted(ctx.t, b.dec, side="left") - 1
    td = ctx.tday[np.clip(k, 0, None)]
    return td, ctx.day.reindex(td)


def s07(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    td, D = _bar_day(ctx, b)
    ok = (D.asia_n >= 300).to_numpy()
    hi, lo = np.where(ok, D.asia_h, np.nan), np.where(ok, D.asia_l, np.nan)
    rng, mid = hi - lo, (hi + lo) / 2
    lon = D.lon_start.to_numpy(float)
    return Spec(_session_break(ctx, "1h", lon, lon + 4 * HOUR, hi, lo, mid, mid, td, buffer=0.1 * rng,
                               atr_ref=atr(b, 14).to_numpy()))


def s08(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    td, D = _bar_day(ctx, b)
    lon = D.lon_start.to_numpy(float)
    orh, orl = _range_between(ctx, lon, lon + HOUR)
    return Spec(_session_break(ctx, "1h", lon + HOUR, lon + 5 * HOUR, orh, orl, orl, orh, td, lo_incl=False,
                               atr_ref=atr(b, 14).to_numpy()))


def _range_between(ctx: Ctx, a: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """High and low of the minutes in [a, z) per row (nan when a is nan or empty)."""
    hi = np.full(len(a), np.nan)
    lo = np.full(len(a), np.nan)
    ok = np.isfinite(a) & np.isfinite(z)
    ka = np.searchsorted(ctx.t, np.where(ok, a, 0).astype(np.int64), side="left")
    kz = np.searchsorted(ctx.t, np.where(ok, z, 0).astype(np.int64), side="left")
    cache: dict[tuple[int, int], tuple[float, float]] = {}
    for j in np.flatnonzero(ok & (kz > ka)):
        key = (int(ka[j]), int(kz[j]))
        if key not in cache:
            cache[key] = (float(ctx.h[key[0]:key[1]].max()), float(ctx.lo[key[0]:key[1]].min()))
        hi[j], lo[j] = cache[key]
    return hi, lo


def s09(ctx: Ctx) -> Spec:
    b = ctx.bars["15m"]
    td, D = _bar_day(ctx, b)
    ny = D.ny_start.to_numpy(float)
    orh, orl = _range_between(ctx, ny, ny + HOUR)
    ah = asof(ctx.bars["1h"].dec, atr(ctx.bars["1h"], 14), b.dec)
    return Spec(_session_break(ctx, "15m", ny + HOUR, ny + HOUR + 150 * MIN, orh, orl, orl, orh, td, lo_incl=False,
                               gate=d1_trend(ctx, b.dec), atr_ref=ah, tgt=2.0))


def s10(ctx: Ctx) -> Spec:
    b = ctx.bars["15m"]
    ud = (b.dec - 1) // DAYNS * DAYNS                 # UTC date of the bar
    wk = pd.to_datetime(ud, utc=True).dayofweek.to_numpy() < 5
    a0 = np.where(wk, ud + HOUR, np.nan)
    orh, orl = _range_between(ctx, a0, a0 + HOUR)
    ah = asof(ctx.bars["1h"].dec, atr(ctx.bars["1h"], 14), b.dec)
    return Spec(_session_break(ctx, "15m", a0 + HOUR, a0 + 4 * HOUR, orh, orl, orl, orh, ud, lo_incl=False,
                               atr_ref=ah, dl="utc0645"))


def s11(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    td, D = _bar_day(ctx, b)
    d = ctx.bars["1d"]
    # previous trading day's high/low: the last daily bar whose decision time <= the bar's trading-day open
    dk = np.searchsorted(d.dec, b.dec, side="left") - 1          # last completed daily bar before this bar closes
    ok = dk >= 0
    pdh = np.where(ok, d.h[np.clip(dk, 0, None)], np.nan)
    pdl = np.where(ok, d.lo[np.clip(dk, 0, None)], np.nan)
    lon, ny15 = D.lon_start.to_numpy(float), D.ny15.to_numpy(float)
    tr = d1_trend(ctx, b.dec)
    win = (b.dec >= lon + HOUR) & (b.dec <= ny15)
    L, S = win & (b.c > pdh) & (tr == 1), win & (b.c < pdl) & (tr == -1)
    i = np.flatnonzero(L | S)
    side = np.where(L[i], 1, -1)
    df = _first_per_day(b.dec[i], side, td[i], {"r": 1.5 * atr(b, 14).to_numpy()[i]}, per_side=True)
    return Spec(frame(df.dec, df.side, risk=df.r, dl="flat"))


def s12(ctx: Ctx) -> Spec:
    d = ctx.bars["1d"]
    dtd = (d.dec - 1)                                   # any instant inside the bar's trading day is its 17:00 NY - 1ns
    tdd = ctx.tday[np.clip(np.searchsorted(ctx.t, dtd, side="right") - 1, 0, None)]
    wk = (tdd + 3) // 7                                  # Monday-start week number (epoch day 0 = Thursday)
    W = pd.DataFrame({"wk": wk, "h": d.h, "l": d.lo}).groupby("wk").agg(h=("h", "max"), l=("l", "min"))
    b = ctx.bars["4h"]
    td, _ = _bar_day(ctx, b)
    bw = (td + 3) // 7
    ph, pl = W.h.reindex(bw - 1).to_numpy(), W.l.reindex(bw - 1).to_numpy()
    c, a = b.s("c"), atr(b, 14)
    return Spec(bar_rule(b, fresh(c > pd.Series(ph)), fresh(c < pd.Series(pl)), 2 * a, trail=3 * a.to_numpy(),
                         hold=30))


def s13(ctx: Ctx) -> Spec:
    b = ctx.bars["15m"]
    td, D = _bar_day(ctx, b)
    day = ctx.day
    rng_d = (day.asia_h - day.asia_l).where(day.asia_n >= 300)
    med = rng_d.dropna().rolling(20).median().shift(1).reindex(day.index)
    comp = (rng_d <= 0.6 * med).reindex(td).to_numpy(dtype=bool)
    hi = np.where(comp, D.asia_h, np.nan)
    lo = np.where(comp, D.asia_l, np.nan)
    mid = (hi + lo) / 2
    lon = D.lon_start.to_numpy(float)
    ah = asof(ctx.bars["1h"].dec, atr(ctx.bars["1h"], 14), b.dec)
    return Spec(_session_break(ctx, "15m", lon, lon + 3 * HOUR, hi, lo, mid, mid, td, lo_incl=False, atr_ref=ah))


def s14(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    td, D = _bar_day(ctx, b)
    ret = (D.lon_c - D.lon_o).to_numpy()
    hi = np.where(ret > 0, D.lon_h, np.inf)
    lo = np.where(ret < 0, D.lon_l, -np.inf)
    mid = ((D.lon_h + D.lon_l) / 2).to_numpy()
    ny = D.ny_start.to_numpy(float)
    return Spec(_session_break(ctx, "1h", ny, D.ny12.to_numpy(float), hi, lo, mid, mid, td, lo_incl=False,
                               atr_ref=atr(b, 14).to_numpy()))


def s15(ctx: Ctx) -> Spec:
    b = ctx.bars["4h"]
    c, a = b.s("c"), atr(b, 14)
    m, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    bw = 4 * sd / m
    q = bw.rolling(250).quantile(0.10)
    sq = (bw.shift(1) <= q.shift(1)).to_numpy()
    return Spec(bar_rule(b, sq & (c > m + 2 * sd).to_numpy(), sq & (c < m - 2 * sd).to_numpy(), 1.5 * a,
                         trail=2.5 * a.to_numpy(), hold=30))


def s16(ctx: Ctx) -> Spec:
    d = ctx.bars["1d"]
    rg = pd.Series(d.h - d.lo)
    nr4 = (rg <= rg.rolling(4).min()).to_numpy()
    ins = (d.h < np.r_[np.nan, d.h[:-1]]) & (d.lo > np.r_[np.nan, d.lo[:-1]])
    flag = nr4 | ins
    ad = atr(d, 20).to_numpy()
    b = ctx.bars["1h"]
    td, D = _bar_day(ctx, b)
    dk = np.searchsorted(d.dec, b.dec, side="left") - 1       # the completed day before this bar's day
    ok = (dk >= 0)
    dkc = np.clip(dk, 0, None)
    f = ok & flag[dkc]
    hi, lo = np.where(f, d.h[dkc], np.nan), np.where(f, d.lo[dkc], np.nan)
    a0 = D.asia_start.to_numpy(float) + HOUR
    return Spec(_session_break(ctx, "1h", a0, D.ny15.to_numpy(float), hi, lo, lo, hi, td, atr_ref=ad[dkc],
                               bounds=(0.3, 2.5)))


def s17(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    c = b.s("c")
    a50 = atr(b, 50)
    ratio = (atr(b, 5) / a50).shift(1)
    hi, lo = b.s("h").rolling(12).max().shift(1), b.s("lo").rolling(12).min().shift(1)
    q = (ratio < 0.6).to_numpy()
    return Spec(bar_rule(b, q & (c > hi).to_numpy(), q & (c < lo).to_numpy(), 1.5 * a50, tgt=3.0, hold=24))


def s18(ctx: Ctx) -> Spec:
    b = ctx.bars["4h"]
    a = atr(b, 14).to_numpy()
    tr = d1_trend(ctx, b.dec)
    rows = []
    n = len(b.c)
    for m in range(1, n - 2):
        if not (b.h[m + 1] < b.h[m] and b.lo[m + 1] > b.lo[m]):
            continue
        for j in range(m + 2, min(m + 5, n)):
            if b.c[j] > b.h[m] and tr[j] == 1:
                rows.append((j, 1, b.lo[m]))
                break
            if b.c[j] < b.lo[m] and tr[j] == -1:
                rows.append((j, -1, b.h[m]))
                break
    if not rows:
        return Spec(frame([], []))
    jj, side, sp = (np.array(x) for x in zip(*rows, strict=True))
    rk = (b.c[jj] - sp) * side
    ok = (rk >= a[jj]) & (rk <= 3 * a[jj])
    jj, side, sp = jj[ok], side[ok], sp[ok]
    return Spec(frame(b.dec[jj], side, stop_px=sp, tgt=2.0, cap=capd(b, jj, 18)))


def s19(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    a50p = atr(b, 50).shift(1).to_numpy()
    rg = b.h - b.lo
    pos = np.where(rg > 0, (b.c - b.lo) / np.where(rg > 0, rg, 1), 0.5)
    big = rg >= 2.5 * a50p
    mid = (b.h + b.lo) / 2
    L, S = big & (pos >= 0.75), big & (pos <= 0.25)
    rk = np.abs(b.c - mid)
    ok = (rk >= 0.5 * a50p) & (rk <= 3 * a50p)
    i = np.flatnonzero((L | S) & ok)
    return Spec(frame(b.dec[i], np.where(L[i], 1, -1), stop_px=mid[i], tgt=2.0, cap=capd(b, i, 8)))


def _price_before(ctx: Ctx, at: np.ndarray) -> np.ndarray:
    """Close of the last minute before `at` (nan if none or more than 30 minutes stale)."""
    ok = np.isfinite(at)
    k = np.searchsorted(ctx.t, np.where(ok, at, 0).astype(np.int64), side="left") - 1
    kc = np.clip(k, 0, None)
    fresh_ = ok & (k >= 0) & (np.where(ok, at, 0) - ctx.t[kc] <= 30 * MIN)
    return np.where(fresh_, ctx.c[kc], np.nan)


def _its(ctx: Ctx, dec: np.ndarray, p0: np.ndarray, thr: float, stop: float, dl: str,
         gate: np.ndarray | None = None) -> Spec:
    p1 = _price_before(ctx, dec)
    ad = atrd_asof(ctx, dec)
    ret = p1 - p0
    ok = np.isfinite(ret) & np.isfinite(ad) & (np.abs(ret) >= thr * ad) & np.isfinite(dec)
    side = np.sign(ret).astype(int)
    if gate is not None:
        ok &= side == gate
    i = np.flatnonzero(ok)
    return Spec(frame(dec[i].astype(np.int64), side[i], risk=stop * ad[i], dl=dl))


def s20(ctx: Ctx) -> Spec:
    D = ctx.day[(ctx.day.asia_n >= 300)]
    return _its(ctx, D.ny_start.to_numpy(float), D.asia_o.to_numpy(), 0.5, 0.5, "flat")


def s21(ctx: Ctx) -> Spec:
    D = ctx.day[(ctx.day.asia_n >= 300)]
    return _its(ctx, D.lon_start.to_numpy(float), D.asia_o.to_numpy(), 0.25, 0.4, "ny_open")


def s22(ctx: Ctx) -> Spec:
    d = ctx.bars["1d"]
    a = atr(d, 20)
    mv = d.s("c").diff()
    big = (mv.abs() >= 1.5 * a.shift(1)).to_numpy()
    i = np.flatnonzero(big)
    return Spec(frame(d.dec[i], np.sign(mv.to_numpy()[i]).astype(int), risk=0.75 * a.to_numpy()[i], dl="flat"))


def s23(ctx: Ctx) -> Spec:
    D = ctx.day
    dec = D.ny12.to_numpy(float)
    gate = d1_trend(ctx, np.nan_to_num(dec).astype(np.int64))
    return _its(ctx, dec, D.lon_o.to_numpy(), 0.3, 0.4, "flat", gate=gate)


def s24(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    c, a = b.s("c"), atr(b, 14)
    e9, e21 = ema(c, 9), ema(c, 21)
    b4 = ctx.bars["4h"]
    up4 = asof(b4.dec, (b4.s("c") > ema(b4.s("c"), 50)).astype(float), b.dec)
    dn4 = asof(b4.dec, (b4.s("c") < ema(b4.s("c"), 50)).astype(float), b.dec)
    tr = d1_trend(ctx, b.dec)
    xu = ((e9.shift(1) <= e21.shift(1)) & (e9 > e21)).to_numpy()
    xd = ((e9.shift(1) >= e21.shift(1)) & (e9 < e21)).to_numpy()
    return Spec(bar_rule(b, xu & (tr == 1) & (up4 == 1), xd & (tr == -1) & (dn4 == 1), 1.5 * a, tgt=3.0, hold=24))


def s25(ctx: Ctx) -> Spec:
    b = ctx.bars["4h"]
    c, a = b.s("c"), atr(b, 14)
    r = rsi(c, 14)
    tr = d1_trend(ctx, b.dec)
    L = ((r.shift(1) < 40) & (r >= 40)).to_numpy() & (tr == 1)
    S = ((r.shift(1) > 60) & (r <= 60)).to_numpy() & (tr == -1)
    return Spec(bar_rule(b, L, S, 1.5 * a, tgt=2.5, hold=18))


def s26(ctx: Ctx) -> Spec:
    b = ctx.bars["15m"]
    c = b.s("c")
    e = ema(c, 50)
    h1, h4 = ctx.bars["1h"], ctx.bars["4h"]
    u1 = asof(h1.dec, (h1.s("c") > ema(h1.s("c"), 20)).astype(float), b.dec)
    d1_ = asof(h1.dec, (h1.s("c") < ema(h1.s("c"), 20)).astype(float), b.dec)
    u4 = asof(h4.dec, (h4.s("c") > ema(h4.s("c"), 50)).astype(float), b.dec)
    d4 = asof(h4.dec, (h4.s("c") < ema(h4.s("c"), 50)).astype(float), b.dec)
    td, D = _bar_day(ctx, b)
    win = (b.dec >= D.lon_start.to_numpy(float)) & (b.dec <= D.ny15.to_numpy(float))
    xu = ((c.shift(1) <= e.shift(1)) & (c > e)).to_numpy() & (u1 == 1) & (u4 == 1) & win
    xd = ((c.shift(1) >= e.shift(1)) & (c < e)).to_numpy() & (d1_ == 1) & (d4 == 1) & win
    ah = asof(h1.dec, atr(h1, 14), b.dec)
    f = bar_rule(b, xu, xd, ah, tgt=2.0, hold=32)
    f["dl"] = "flat"
    return Spec(f)


def s27(ctx: Ctx) -> Spec:
    b = ctx.bars["4h"]
    c, a = b.s("c"), atr(b, 14)
    macd = ema(c, 12) - ema(c, 26)
    hist = macd - ema(macd, 9)
    tr = d1_trend(ctx, b.dec)
    L = ((hist.shift(1) <= 0) & (hist > 0)).to_numpy() & (tr == 1)
    S = ((hist.shift(1) >= 0) & (hist < 0)).to_numpy() & (tr == -1)
    return Spec(bar_rule(b, L, S, 2 * a, trail=3 * a.to_numpy(), hold=30))


def adx(b: Bars, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    h, lo = b.s("h"), b.s("lo")
    upm, dnm = h.diff(), -lo.diff()
    pdm = pd.Series(np.where((upm > dnm) & (upm > 0), upm, 0.0))
    ndm = pd.Series(np.where((dnm > upm) & (dnm > 0), dnm, 0.0))
    trw = wilder(true_range(b), n)
    pdi, ndi = 100 * wilder(pdm, n) / trw, 100 * wilder(ndm, n) / trw
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return wilder(dx.fillna(0), n), pdi, ndi


def s28(ctx: Ctx) -> Spec:
    b = ctx.bars["4h"]
    a = atr(b, 14)
    ax, pdi, ndi = adx(b, 14)
    on = ((ax.shift(1) < 20) & (ax >= 20)).to_numpy().copy()
    on[:50] = False                                     # Wilder warm-up
    L, S = on & (pdi > ndi).to_numpy(), on & (pdi < ndi).to_numpy()
    return Spec(bar_rule(b, L, S, 2 * a, trail=3 * a.to_numpy(), hold=30))


def s29(ctx: Ctx) -> Spec:
    d = ctx.bars["1d"]
    c = d.s("c")
    a20, a5 = atr(d, 20), atr(d, 5)
    rt = a5 / a20
    on = ((rt.shift(1) < 1.2) & (rt >= 1.2)).to_numpy()
    side = np.sign((c - c.shift(5)).to_numpy())
    L, S = on & (side > 0), on & (side < 0)
    return Spec(bar_rule(d, L, S, 1.5 * a20, trail=2.5 * a20.to_numpy(), hold=10))


def s30(ctx: Ctx) -> Spec:
    d = ctx.bars["1d"]
    dc = d.s("c")
    e20, e50 = ema(dc, 20), ema(dc, 50)
    ad = atr(d, 20)
    b = ctx.bars["1h"]
    E20 = asof(d.dec, e20, b.dec)
    E50 = asof(d.dec, e50, b.dec)
    DC = asof(d.dec, dc, b.dec)
    AD = asof(d.dec, ad, b.dec)
    pc = np.r_[np.nan, b.c[:-1]]
    up = (E20 > E50) & (DC > E20)
    dn = (E20 < E50) & (DC < E20)
    L = up & (pc > E20) & (b.lo <= E20) & (b.c > E20)
    S = dn & (pc < E20) & (b.h >= E20) & (b.c < E20)
    sp = np.where(L, E20 - 0.75 * AD, E20 + 0.75 * AD)
    return Spec(bar_rule(b, L, S, np.full(len(b.c), np.nan), tgt=2.0, hold=72, stop_px=sp))


def s31(ctx: Ctx) -> Spec:
    b = ctx.bars["4h"]
    c, a = b.s("c"), atr(b, 14)
    e20, e50 = ema(c, 20), ema(c, 50)
    L = ((c > e50) & (e20 > e50) & (b.s("lo") <= e20) & (e20 < c)).to_numpy()
    S = ((c < e50) & (e20 < e50) & (b.s("h") >= e20) & (e20 > c)).to_numpy()
    return Spec(bar_rule(b, L, S, 1.5 * a, tgt=2.0, hold=18))


def s32(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    c, a = b.s("c"), atr(b, 14)
    s200, s5 = c.rolling(200).mean(), c.rolling(5).mean()
    r2 = rsi(c, 2)
    L = ((c > s200) & (r2 < 10)).to_numpy()
    S = ((c < s200) & (r2 > 90)).to_numpy()
    f = bar_rule(b, L, S, 2 * a, hold=10)
    return Spec(f, exit_flags=(b.dec, (c > s5).to_numpy(), (c < s5).to_numpy()))


def supertrend(b: Bars, n: int = 10, mult: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    a = wilder(true_range(b), n).to_numpy()
    hl2 = (b.h + b.lo) / 2
    ub, lb = hl2 + mult * a, hl2 - mult * a
    N = len(b.c)
    fu, fl = ub.copy(), lb.copy()
    dirn = np.ones(N, dtype=int)
    line = np.full(N, np.nan)
    for i in range(1, N):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or b.c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or b.c[i - 1] < fl[i - 1]) else fl[i - 1]
        if dirn[i - 1] == 1:
            dirn[i] = -1 if b.c[i] < fl[i] else 1
        else:
            dirn[i] = 1 if b.c[i] > fu[i] else -1
        line[i] = fl[i] if dirn[i] == 1 else fu[i]
    dirn[:n + 1] = 0
    return dirn, line


def s33(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    a = atr(b, 14).to_numpy()
    d, line = supertrend(b)
    prev = np.r_[0, d[:-1]]
    flip = (d != prev) & (prev != 0) & (d != 0)
    rk = np.abs(b.c - line)
    ok = flip & (rk >= a) & (rk <= 4 * a)
    L, S = ok & (d == 1), ok & (d == -1)
    f = bar_rule(b, L, S, rk, hold=72)
    return Spec(f, exit_flags=(b.dec, d == -1, d == 1))


def s34(ctx: Ctx) -> Spec:
    b = ctx.bars["1h"]
    a = atr(b, 14)
    n = 48
    x = np.arange(n, dtype=float)
    xm = x.mean()
    sxx = ((x - xm) ** 2).sum()
    cv = np.lib.stride_tricks.sliding_window_view(b.c, n)
    ym = cv.mean(1)
    slope = ((cv - ym[:, None]) * (x - xm)).sum(1) / sxx
    resid = cv - (ym[:, None] + slope[:, None] * (x - xm))
    sse = (resid ** 2).sum(1)
    sst = ((cv - ym[:, None]) ** 2).sum(1)
    se = np.sqrt(sse / (n - 2) / sxx)
    tt = np.r_[np.full(n - 1, np.nan), np.where(se > 0, slope / np.where(se > 0, se, 1), np.nan)]
    r2 = np.r_[np.full(n - 1, np.nan), np.where(sst > 0, 1 - sse / np.where(sst > 0, sst, 1), np.nan)]
    prev = np.r_[np.nan, tt[:-1]]
    L = (prev < 3) & (tt >= 3) & (r2 >= 0.6)
    S = (prev > -3) & (tt <= -3) & (r2 >= 0.6)
    return Spec(bar_rule(b, L, S, 2 * a, trail=3 * a.to_numpy(), hold=24))


STRATS: dict[str, Callable[[Ctx], Spec]] = {f"S200-TREND-{i:02d}": fn for i, fn in enumerate(
    [s01, s02, s03, s04, s05, s06, s07, s08, s09, s10, s11, s12, s13, s14, s15, s16, s17, s18, s19, s20, s21, s22,
     s23, s24, s25, s26, s27, s28, s29, s30, s31, s32, s33, s34], start=1)}
NAMES = ["H4-DON30-TR", "H4-DON20-D1", "H1-DON48-D1", "H1-DON24-ATRX", "H4-KELT", "H4-POSTBREAK-PB", "ASIA-LON-1H",
         "LON-OR60-1H", "NY-OR60-D1", "SGE-OR", "PDHL-1H-D1", "PWHL-4H", "ASIA-SQZ-15M", "LON-HL-NY", "H4-BBW",
         "NR4ID-1H", "H1-ATRCOMP", "H4-INSIDE-D1", "H1-SHOCK-CONT", "ITS-AL2NY", "ITS-A2L", "BIGDAY-NEXT",
         "ITS-NOON-D1", "MTF-EMA-1H", "MTF-RSI-4H", "MTF-15M", "MACD-4H-D1", "ADX-ONSET-4H", "ATREXP-1D",
         "PB-D1EMA20-1H", "PB-4H-EMA20", "RSI2-1H", "SUPERTREND-1H", "LINREG-1H"]


# ---------------------------------------------------------------- engine
def deadline(ctx: Ctx, kind: str, k_fill: int) -> int:
    big = np.iinfo(np.int64).max
    if not kind:
        return big
    if kind == "utc0645":
        return int(ctx.t[k_fill] // DAYNS * DAYNS + 6 * HOUR + 45 * MIN)
    row: Any = ctx.day.loc[int(ctx.tday[k_fill])]
    v = float(row["flat"] if kind == "flat" else row["ny_start"])
    return int(v) if np.isfinite(v) else big


def walk(ctx: Ctx, k0: int, side: int, risk: float, tgt: float, trail: float, t_end: int,
         flag_t: np.ndarray | None) -> tuple[float, str, int, int]:
    """(gross USD/oz, exit kind, exit minute index, exit time ns) from the fill minute k0 (entry = its open)."""
    t = ctx.t
    kend = min(int(np.searchsorted(t, t_end, side="left")) - 1, len(t) - 1)
    kflag = None
    if flag_t is not None:
        j = int(np.searchsorted(flag_t, t[k0], side="right"))         # first flagged close strictly after the fill
        if j < len(flag_t):
            kf = int(np.searchsorted(t, flag_t[j], side="left"))
            if kf <= kend:
                kflag = kf
    last = kend if kflag is None else kflag - 1
    e = float(ctx.o[k0])
    if last < k0:                                                     # flagged exit on the very next minute
        if kflag is None:
            return 0.0, "time", k0, int(t[k0]) + MIN
        kx = kflag
        return side * (float(ctx.o[kx]) - e), "flag", kx, int(t[kx])
    o, h, lo, c = ctx.o[k0:last + 1], ctx.h[k0:last + 1], ctx.lo[k0:last + 1], ctx.c[k0:last + 1]
    if side < 0:
        o, h, lo, c, e_ = -o, -lo, -h, -c, -e
    else:
        e_ = e
    stop = np.full(len(o), e_ - risk)
    if np.isfinite(trail):
        run = np.maximum.accumulate(h)
        stop = np.maximum(stop, np.r_[-np.inf, run[:-1]] - trail)
    s = np.flatnonzero(lo <= stop)
    js = int(s[0]) if len(s) else 1 << 62
    jt = 1 << 62
    if np.isfinite(tgt):
        tt = np.flatnonzero(h >= e_ + tgt * risk)
        jt = int(tt[0]) if len(tt) else 1 << 62
    if js <= jt and js < 1 << 62:
        px = min(float(stop[js]), float(o[js]))
        moved = float(stop[js]) > e_ - risk + 1e-9
        return px - e_, ("trail" if moved else "stop"), k0 + js, int(t[k0 + js])
    if jt < 1 << 62:
        return float(tgt * risk), "target", k0 + jt, int(t[k0 + jt])
    if kflag is not None:
        return side * (float(ctx.o[kflag]) - e), "flag", kflag, int(t[kflag])
    return float(c[-1] - e_), "time", last, int(t[last]) + MIN


def h_u(*key: object) -> float:
    return int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:8], "big") / 2 ** 64


def _twin(ctx: Ctx, kk: int, r: Any, side: int, risk: float, hold: int | None, fl: np.ndarray | None,
          n_id: int) -> dict[str, Any] | None:
    t = ctx.t
    if kk >= len(t) - 1:
        return None
    dlt = deadline(ctx, str(r.dl), kk)
    if dlt < np.iinfo(np.int64).max and t[kk] > dlt - 15 * MIN:
        return None
    te = min(int(t[kk]) + hold if hold is not None else np.iinfo(np.int64).max, dlt, ctx.end)
    g2, kind2, _, tx2 = walk(ctx, kk, side, risk, float(r.tgt), float(r.trail), te, fl)
    return {"t_fill": int(t[kk]), "t_exit": tx2, "side": side, "risk": risk, "gross": g2, "kind": kind2, "id": n_id,
            "entry": float(ctx.o[kk])}


def simulate(ctx: Ctx, sid: str, spec: Spec) -> dict[str, list[dict[str, Any]]]:
    sig = spec.sig.sort_values("dec", kind="stable").reset_index(drop=True)
    sig = sig[(sig.dec >= ctx.dec_from) & (sig.dec < ctx.end)]
    flags: dict[int, np.ndarray] | None = None
    if spec.exit_flags is not None:
        fd, f_long, f_short = spec.exit_flags
        flags = {1: fd[np.asarray(f_long, dtype=bool)], -1: fd[np.asarray(f_short, dtype=bool)]}
    t = ctx.t
    real: list[dict[str, Any]] = []
    twins: list[dict[str, Any]] = []
    full: list[dict[str, Any]] = []
    busy_until = -1
    r: Any
    for r in sig.itertuples(index=False):
        dec = int(r.dec)
        if dec < busy_until:
            continue
        k0 = int(np.searchsorted(t, dec, side="left"))
        if k0 >= len(t) - 1:
            continue
        dl = deadline(ctx, str(r.dl), k0)
        if dl < np.iinfo(np.int64).max and t[k0] > dl - 15 * MIN:
            continue
        side = int(r.side)
        e = float(ctx.o[k0])
        risk = float(r.risk) if np.isfinite(r.risk) else side * (e - float(r.stop_px))
        if not (np.isfinite(risk) and risk > 0):
            continue
        cap = int(r.cap)
        t_end = min(cap, dl, ctx.end)
        tr_ = float(r.trail)
        fl = flags[side] if flags is not None else None
        g, kind, kx, tx = walk(ctx, k0, side, risk, float(r.tgt), tr_, t_end, fl)
        n_id = len(real)
        real.append({"t_fill": int(t[k0]), "t_exit": tx, "side": side, "risk": risk, "gross": g, "kind": kind,
                     "id": n_id, "entry": e})
        busy_until = tx
        # twins: same session instance from the fill minute on (>= 30 minutes, else the next 60), before deadline - 15
        hi_k = int(ctx.run_end[k0])
        if hi_k - k0 + 1 < 30:
            hi_k = int(np.searchsorted(t, t[k0] + 60 * MIN, side="left")) - 1
        hold = (cap - int(t[k0])) if cap < np.iinfo(np.int64).max else None
        for j in range(N_TWIN):
            # the pre-registered, leak-free control: post-fill minutes of the same session instance (the pass rule)
            tw = _twin(ctx, k0 + int(h_u(sid, n_id, j) * (hi_k - k0 + 1)), r, side, risk, hold, fl, n_id)
            if tw is not None:
                twins.append(tw)
            # the brief's literal same-day control (whole session instance, pre-signal minutes included): it looks
            # ahead (the side is taken from the move that triggered the signal), REPORTED ONLY (coordinator 11-02)
            a0, a1 = int(ctx.run_start[k0]), int(ctx.run_end[k0])
            tw = _twin(ctx, a0 + int(h_u(sid, n_id, j, "full") * (a1 - a0 + 1)), r, side, risk, hold, fl, n_id)
            if tw is not None:
                full.append(tw)
    return {"real": real, "twins": twins, "full": full}


def price(rows: list[dict[str, Any]], costs: A.ArmCosts, swap: SwapSpec) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    ent = pd.DatetimeIndex(pd.to_datetime(df.t_fill.to_numpy(), utc=True))
    ext = pd.DatetimeIndex(pd.to_datetime(df.t_exit.to_numpy(), utc=True))
    sess = DEFAULT_SESSIONS.session_label(ent)
    rt, os_ = costs.screen_charges(BROKER, sess, COST_VERSION)
    nights = rollover_nights(ent, ext, swap.server_tz, swap.triple_weekday)
    sw = nights * swap.usd_per_oz_at(df.side.to_numpy(), df.entry.to_numpy(dtype=float))
    stop_exit = df.kind.isin(["stop", "trail"]).to_numpy()
    df["swap"] = sw
    df["cost"] = rt + os_ * stop_exit
    df["gross_R"] = df.gross / df.risk
    df["R"] = (df.gross + sw - rt - os_ * stop_exit) / df.risk
    df["R_stress"] = (df.gross + sw - rt - STRESS_OVERSHOOT * stop_exit) / df.risk
    df["cost_R"] = (rt + os_ * stop_exit - sw) / df.risk
    df["year"] = ent.year
    return df


def _excess(real: pd.DataFrame, tw: pd.DataFrame) -> tuple[float, float]:
    if tw.empty:
        return float("nan"), float("nan")
    m = tw.groupby("id").R.mean()
    has = real.id.isin(m.index)
    ex = float((real.R[has] - real.id[has].map(m)).mean()) if has.any() else float("nan")
    return ex, float(tw.R.mean())


def summarize(sid: str, real: pd.DataFrame, tw: pd.DataFrame, window: str,
              full: pd.DataFrame | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": sid, "name": NAMES[int(sid[-2:]) - 1], "n": int(len(real))}
    if real.empty:
        out.update({"pass": False, "verdict": "FAIL-COUNT"})
        return out
    r = real.R.to_numpy()
    sd = float(r.std(ddof=1)) if len(r) > 1 else float("nan")
    t = float(r.mean() / (sd / np.sqrt(len(r)))) if len(r) > 1 and sd > 0 else float("nan")
    g = real.gross_R.to_numpy()
    gt = float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))) if len(g) > 1 and g.std(ddof=1) > 0 else float("nan")
    excess, tw_mean = _excess(real, tw)
    ex_full, full_mean = _excess(real, full if full is not None else pd.DataFrame())
    yr = real.groupby("year").R.mean()
    out.update({
        "net_R": float(r.mean()), "t": t, "sd_R": sd, "gross_R": float(g.mean()), "gross_t": gt,
        "cost_R": float(real.cost_R.mean()), "stress_net_R": float(real.R_stress.mean()),
        "twin_net_R": tw_mean, "control_excess": excess, "n_twins": int(len(tw)),
        "full_twin_net_R": full_mean, "control_excess_full_lookahead": ex_full,
        "long_share": float((real.side > 0).mean()), "median_risk_usd": float(real.risk.median()),
        "exit_mix": {k: round(float(v), 3) for k, v in real.kind.value_counts(normalize=True).items()},
        "hit": float((r > 0).mean()), "year_net_R": {str(k): round(float(v), 4) for k, v in yr.items()},
    })
    if window == "explore":
        ok = len(r) >= 100 and r.mean() > 0 and t >= 2.0 and np.isfinite(excess) and excess >= 0.05
        out["pass"] = bool(ok)
        out["verdict"] = "PASS" if ok else ("FAIL-COUNT" if len(r) < 100 else "FAIL")
    else:
        years = range(2018, 2026)
        pos = sum(1 for y in years if y in yr.index and yr[y] > 0)
        dsr = DsrInputs.of(r).dsr(K_PREREG) if len(r) >= MIN_TRADES_FOR_DSR else None
        out["dsr_inputs"] = DsrInputs.of(r).model_dump() if len(r) >= MIN_TRADES_FOR_DSR else None
        out["dsr_K"] = K_PREREG
        out["dsr"] = dsr
        out["years_pos"] = pos
        ok = r.mean() > 0 and t >= 2.0 and dsr is not None and dsr >= 0.5 and pos >= 6
        out["pass"] = bool(ok)
        out["verdict"] = "PASS" if ok else "FAIL"
    return out


# ---------------------------------------------------------------- workers
_CTX: Ctx | None = None
_ARGS: dict[str, Any] = {}


def _init(raw: str, window: str) -> None:
    global _CTX
    _CTX = build_ctx(Path(raw), window)
    _ARGS["window"] = window


def _count(sid: str) -> dict[str, Any]:
    assert _CTX is not None
    sig = STRATS[sid](_CTX).sig
    sig = sig[(sig.dec >= _CTX.dec_from) & (sig.dec < _CTX.end)]
    return {"id": sid, "signals": int(len(sig))}


def _run(sid: str) -> dict[str, Any]:
    assert _CTX is not None
    costs = A.load_costs(ROOT / "config" / "arm_costs.yaml")
    swap = SwapSpec.research_shipped()                # today's financing rate on each trade's own notional
    res = simulate(_CTX, sid, STRATS[sid](_CTX))
    real, tw, full = (price(res[k], costs, swap) for k in ("real", "twins", "full"))
    return summarize(sid, real, tw, _ARGS["window"], full)


def parity(raw: Path) -> None:
    """The engine against the harness (`triple_barrier_next_open_1m`, mid-priced) on synthetic 1h brackets in 2012:
    every 7th 1h bar, alternating sides, 1.5 ATR stop, 2 R target, 12-bar cap. Not a strategy: no outcome is read."""
    from goldbot.labels.triple_barrier import BarrierSpec, triple_barrier_next_open_1m
    ctx = build_ctx(raw, "explore")
    b = ctx.bars["1h"]
    sel = np.flatnonzero((b.dec >= pd.Timestamp("2012-01-01", tz="UTC").value)
                         & (b.dec < pd.Timestamp("2012-07-01", tz="UTC").value))[::7]
    a = atr(b, 14).to_numpy()
    sel = sel[np.isfinite(a[sel])]
    side = np.where(np.arange(len(sel)) % 2 == 0, 1, -1)
    m1 = pd.DataFrame({"ts_utc": pd.to_datetime(ctx.t, utc=True)})
    for k, col in (("open", ctx.o), ("high", ctx.h), ("low", ctx.lo), ("close", ctx.c)):
        m1[f"bid_{k}"] = m1[f"ask_{k}"] = col
    bars = pd.DataFrame({"ts_utc": pd.to_datetime(b.start, utc=True), "visible_at": pd.to_datetime(b.dec, utc=True)})
    for k, col in (("open", b.o), ("high", b.h), ("low", b.lo), ("close", b.c)):
        bars[f"bid_{k}"] = bars[f"ask_{k}"] = col
    spec = BarrierSpec(target_atr=3.0, stop_atr=1.5, max_bars=12)
    sig = pd.DataFrame({"idx": sel, "side": side})
    ref = triple_barrier_next_open_1m(bars, m1, sig, spec, pd.Series(a))
    same, diff = 0, 0
    row: Any
    for row in ref.itertuples(index=False):
        i = int(row.idx)
        k0 = int(np.searchsorted(ctx.t, b.dec[i], side="left"))
        t_end = int(b.dec[min(i + 1 + 12, len(b.dec) - 1)])
        g, kind, _, _ = walk(ctx, k0, int(row.side), 1.5 * a[i], 2.0, np.nan, t_end, None)
        hk = {"stop": "stop", "target": "target", "time": "time"}[str(row.barrier_hit)]
        ref_g = int(row.side) * (float(row.exit) - float(row.entry))
        if hk == kind and abs(ref_g - g) < 1e-6:
            same += 1
        else:
            diff += 1
            if diff <= 5:
                print("diff", i, hk, kind, round(ref_g, 4), round(g, 4))
    print(f"parity: {same} identical, {diff} different (a gap through the stop fills at the open here, at the stop "
          f"in the harness)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=ROOT.parents[2] / "raw" if (ROOT.parents[2] / "raw").exists()
                    else ROOT / "raw")
    ap.add_argument("--window", choices=list(WINDOWS), default="explore")
    ap.add_argument("--out", type=Path, default=Path.home() / "tmp" / "sprint200_trend" / "explore.json")
    ap.add_argument("--passers", type=Path, help="explore result JSON (confirm runs its PASS ids only)")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--count-only", action="store_true")
    ap.add_argument("--parity", action="store_true")
    a = ap.parse_args()
    if a.parity:
        parity(a.raw)
        return
    ids = a.ids or list(STRATS)
    if a.window == "confirm":
        if a.passers is None:
            raise SystemExit("confirm needs --passers (the explore result): it runs explore passers only")
        ex = json.loads(a.passers.read_text())
        passed = {r["id"] for r in ex["results"] if r.get("pass")}
        refused = [i for i in ids if i not in passed]
        ids = [i for i in ids if i in passed]
        if refused and a.ids:
            raise SystemExit(f"not explore passers: {refused}")
        if not ids:
            print("no explore passers: confirm not run")
            return
    assert a.workers <= 2, "the shared Mac: at most 2 workers per lane"
    with ProcessPoolExecutor(a.workers, initializer=_init, initargs=(str(a.raw), a.window)) as ex_:
        res = list(ex_.map(_count if a.count_only else _run, ids))
    if a.count_only:
        for r in res:
            print(r)
        return
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"window": a.window, "cost_version": COST_VERSION, "K": K_PREREG, "results": res},
                                indent=1, default=float))
    for r in res:
        print(r["id"], r["name"], r["verdict"], {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()
                                                  if k in ("n", "gross_R", "net_R", "t", "control_excess", "dsr",
                                                           "years_pos")})


if __name__ == "__main__":
    main()
