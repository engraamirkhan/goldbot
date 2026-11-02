"""ML + probability team, lane MLEXEC: execution variants of 11 sprint-200 trend rules (docs/research/mlprob/mlexec-prereg.md).

    .venv/bin/python scripts/mlprob/mlexec_screen.py --window explore --out ~/tmp/mlprob_mlexec/explore.json
    .venv/bin/python scripts/mlprob/mlexec_screen.py --window confirm --passers ~/tmp/mlprob_mlexec/explore.json \
        --out ~/tmp/mlprob_mlexec/confirm.json
    .venv/bin/python scripts/mlprob/mlexec_screen.py --window test_old --passers ~/tmp/mlprob_mlexec/confirm.json \
        --out ~/tmp/mlprob_mlexec/test_old.json
    .venv/bin/python scripts/mlprob/mlexec_screen.py --window explore --v0-only     (engine parity vs the trend lane)

Variants (prereg section 2): V0 market (baseline), V1 limit 0.30 R / 60 min, V2 limit 0.15 R / 30 min, V3 swap-cut
for longs before each rollover unless +0.5 R, V4 stop 1.5x, V5 walk-forward LightGBM chooser V0/V1. The signal code is
copied unchanged from scripts/sprint200/trend_screen.py at commit ccaf6b26 (the 11 gross-positive rules and their
helpers). Costs: cor-2026-10-31.2 ICM upper bound + overshoot + shipped swap (gated), and the W2 bps version (reported).
No row at or after 2025-10-01 is read. Aggregates only leave the process (JSON under ~/tmp, never committed).
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
from goldbot.data.store import asof_join  # noqa: E402
from goldbot.labels import SwapSpec, rollover_nights  # noqa: E402
from goldbot.research import arms as A  # noqa: E402
from goldbot.research.metrics import MIN_TRADES_FOR_DSR, DsrInputs  # noqa: E402

COST_VERSION = "cor-2026-10-31.2"
BROKER = "icm"
N_TWIN = 4
K_DSR = 3_400
BPS_REF_PRICE = 4_000.0
MIN = 60_000_000_000
HOUR = 60 * MIN
DAYNS = 24 * HOUR
BIG = np.iinfo(np.int64).max
NYZ = "America/New_York"
HOLDOUT = pd.Timestamp("2025-10-01", tz="UTC")
WINDOWS = {  # data from, first decision, end (exclusive)
    "explore": (pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2010-01-01", tz="UTC"),
                pd.Timestamp("2018-01-01", tz="UTC")),
    "confirm": (pd.Timestamp("2016-07-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC"), HOLDOUT),
    "test_old": (pd.Timestamp("2005-07-01", tz="UTC"), pd.Timestamp("2006-02-01", tz="UTC"),
                 pd.Timestamp("2010-01-01", tz="UTC")),
}
TF_NS = {"15m": 15 * MIN, "1h": HOUR, "4h": 4 * HOUR}
SESS = list(DEFAULT_SESSIONS.sessions_local)          # asia, london, newyork
ASIA, LONDON, NEWYORK = (SESS.index(s) for s in ("asia", "london", "newyork"))
LIMIT_THROUGH = 0.10                                  # USD/oz a limit must be traded through to count as filled
SWAPCUT_LEAD = 10 * MIN
SWAPCUT_KEEP_R = 0.5
EXEC: dict[str, dict[str, Any]] = {
    "V0": {"name": "MKT"},
    "V1": {"name": "LIM30", "depth": 0.30, "win": 60},
    "V2": {"name": "LIM15", "depth": 0.15, "win": 30},
    "V3": {"name": "SWAPCUT", "swapcut": True},
    "V4": {"name": "WIDE", "wide": 1.5},
    "V5": {"name": "LEARN"},
}
NEW_VARIANTS = ["V1", "V2", "V3", "V4", "V5"]
LGB_PARAMS: dict[str, Any] = {"n_estimators": 200, "learning_rate": 0.03, "num_leaves": 7, "min_child_samples": 100,
              "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8, "random_state": 0, "n_jobs": 1,
              "verbose": -1}
FEATURES = ["rule", "side", "hour", "weekday", "r_atrd", "vol_ratio", "clv", "bar_r", "ext_r", "ret4_r", "d1_side"]
H4_D1_RULES = {"01", "15", "28", "31"}


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


def s10(ctx: Ctx) -> Spec:
    b = ctx.bars["15m"]
    ud = (b.dec - 1) // DAYNS * DAYNS                 # UTC date of the bar
    wk = pd.to_datetime(ud, utc=True).dayofweek.to_numpy() < 5
    a0 = np.where(wk, ud + HOUR, np.nan)
    orh, orl = _range_between(ctx, a0, a0 + HOUR)
    ah = asof(ctx.bars["1h"].dec, atr(ctx.bars["1h"], 14), b.dec)
    return Spec(_session_break(ctx, "15m", a0 + HOUR, a0 + 4 * HOUR, orh, orl, orl, orh, ud, lo_incl=False,
                               atr_ref=ah, dl="utc0645"))


def s15(ctx: Ctx) -> Spec:
    b = ctx.bars["4h"]
    c, a = b.s("c"), atr(b, 14)
    m, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    bw = 4 * sd / m
    q = bw.rolling(250).quantile(0.10)
    sq = (bw.shift(1) <= q.shift(1)).to_numpy()
    return Spec(bar_rule(b, sq & (c > m + 2 * sd).to_numpy(), sq & (c < m - 2 * sd).to_numpy(), 1.5 * a,
                         trail=2.5 * a.to_numpy(), hold=30))


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


def deadline(ctx: Ctx, kind: str, k_fill: int) -> int:
    big = np.iinfo(np.int64).max
    if not kind:
        return big
    if kind == "utc0645":
        return int(ctx.t[k_fill] // DAYNS * DAYNS + 6 * HOUR + 45 * MIN)
    row: Any = ctx.day.loc[int(ctx.tday[k_fill])]
    v = float(row["flat"] if kind == "flat" else row["ny_start"])
    return int(v) if np.isfinite(v) else big


def h_u(*key: object) -> float:
    return int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:8], "big") / 2 ** 64


RULES: dict[str, Callable[[Ctx], Spec]] = {"01": s01, "03": s03, "04": s04, "10": s10, "15": s15, "19": s19,
                                           "20": s20, "24": s24, "28": s28, "31": s31, "32": s32}
NAMES = {"01": "H4-DON30-TR", "03": "H1-DON48-D1", "04": "H1-DON24-ATRX", "10": "SGE-OR", "15": "H4-BBW",
         "19": "H1-SHOCK-CONT", "20": "ITS-AL2NY", "24": "MTF-EMA-1H", "28": "ADX-ONSET-4H", "31": "PB-4H-EMA20",
         "32": "RSI2-1H"}


def sid_of(rule: str) -> str:
    return f"S200-TREND-{rule}"


# ---------------------------------------------------------------- execution engine
def rollover_checks(ctx: Ctx, server_tz: str) -> np.ndarray:
    """Minute index of the first minute at or after (rollover - 10 min) and before the rollover, for every server
    midnight that charges swap (it ends a Monday-Friday server day)."""
    t = ctx.t
    first = pd.Timestamp(int(t[0]), tz="UTC").tz_convert(server_tz).tz_localize(None).normalize()
    last = pd.Timestamp(int(t[-1]), tz="UTC").tz_convert(server_tz).tz_localize(None).normalize()
    days = pd.date_range(first, last + pd.Timedelta(days=2), freq="D")
    days = days[(days - pd.Timedelta(days=1)).dayofweek < 5]
    roll = _ns(days.tz_localize(server_tz, ambiguous="NaT", nonexistent="shift_forward").tz_convert("UTC"))
    k = np.searchsorted(t, roll - SWAPCUT_LEAD, side="left")
    ok = (k < len(t))
    k, roll = k[ok], roll[ok]
    ok = t[k] < roll
    return np.unique(k[ok]).astype(np.int64)


def limit_fill(ctx: Ctx, k0: int, side: int, ref: float, risk: float, depth: float, win_min: int,
               t_last: int) -> tuple[int, float] | None:
    """First minute in [t[k0], t[k0] + win) and <= t_last whose mid traded through the limit by LIMIT_THROUGH; the
    fill price is the limit, or the minute's open when it opened through it."""
    t = ctx.t
    lim = ref - side * depth * risk
    k_hi = min(int(np.searchsorted(t, int(t[k0]) + win_min * MIN, side="left")),
               int(np.searchsorted(t, t_last, side="right")), len(t))
    if k_hi <= k0:
        return None
    if side > 0:
        hit = np.flatnonzero(ctx.lo[k0:k_hi] <= lim - LIMIT_THROUGH)
    else:
        hit = np.flatnonzero(ctx.h[k0:k_hi] >= lim + LIMIT_THROUGH)
    if not len(hit):
        return None
    k = k0 + int(hit[0])
    o = float(ctx.o[k])
    return k, (min(lim, o) if side > 0 else max(lim, o))


def walk_px(ctx: Ctx, k0: int, side: int, e: float, risk: float, tgt: float, trail: float, t_end: int,
            flag_t: np.ndarray | None, *, first_stop_only: bool = False,
            checks: np.ndarray | None = None) -> tuple[float, str, int, int]:
    """The trend lane's `walk` with an explicit entry price `e` filled in minute k0. `first_stop_only`: in the fill
    minute only the stop counts (a limit fill: that minute's high may precede the fill). `checks`: absolute minute
    indices of swap-cut checks (a long exits at the check minute's open unless it is >= +0.5 R)."""
    t = ctx.t
    kend = min(int(np.searchsorted(t, t_end, side="left")) - 1, len(t) - 1)
    kflag = None
    if flag_t is not None:
        j = int(np.searchsorted(flag_t, t[k0], side="right"))
        if j < len(flag_t):
            kf = int(np.searchsorted(t, flag_t[j], side="left"))
            if kf <= kend:
                kflag = kf
    last = kend if kflag is None else kflag - 1
    if last < k0:
        if kflag is None:
            return 0.0, "time", k0, int(t[k0]) + MIN
        return side * (float(ctx.o[kflag]) - e), "flag", kflag, int(t[kflag])
    o, h, lo, c = (np.array(x[k0:last + 1], dtype=float) for x in (ctx.o, ctx.h, ctx.lo, ctx.c))
    if side < 0:
        o, h, lo, c, e_ = -o, -lo, -h, -c, -e
    else:
        e_ = e
    if first_stop_only:
        h[0] = e_
    stop = np.full(len(o), e_ - risk)
    if np.isfinite(trail):
        run = np.maximum.accumulate(h)
        stop = np.maximum(stop, np.r_[-np.inf, run[:-1]] - trail)
    no = 1 << 62
    s = np.flatnonzero(lo <= stop)
    js = int(s[0]) if len(s) else no
    jt = no
    if np.isfinite(tgt):
        tt = np.flatnonzero(h >= e_ + tgt * risk)
        jt = int(tt[0]) if len(tt) else no
    jc = no
    if checks is not None and len(checks):
        rel = checks[(checks > k0) & (checks <= last)] - k0
        if len(rel):
            bad = rel[o[rel] - e_ < SWAPCUT_KEEP_R * risk]
            jc = int(bad[0]) if len(bad) else no
    if jc < js and jc <= jt:
        return float(o[jc] - e_), "swapcut", k0 + jc, int(t[k0 + jc])
    if js <= jt and js < no:
        px = min(float(stop[js]), float(o[js]))
        moved = float(stop[js]) > e_ - risk + 1e-9
        return px - e_, ("trail" if moved else "stop"), k0 + js, int(t[k0 + js])
    if jt < no:
        return float(tgt * risk), "target", k0 + jt, int(t[k0 + jt])
    if kflag is not None:
        return side * (float(ctx.o[kflag]) - e), "flag", kflag, int(t[kflag])
    return float(c[-1] - e_), "time", last, int(t[last]) + MIN


@dataclass
class Base:
    dec: int
    k0: int
    side: int
    ref: float
    risk: float
    tgt: float
    trail: float
    cap: int
    dl: int
    dlkind: str
    t_end: int


def bases(ctx: Ctx, spec: Spec) -> tuple[list[Base | None], dict[int, np.ndarray] | None]:
    """Every in-window signal (sorted by decision) with its market reference fill, or None where the trend lane's
    simulate() drops it before the one-at-a-time check (no minute, past the deadline, non-positive risk)."""
    sig = spec.sig.sort_values("dec", kind="stable").reset_index(drop=True)
    sig = sig[(sig.dec >= ctx.dec_from) & (sig.dec < ctx.end)]
    flags: dict[int, np.ndarray] | None = None
    if spec.exit_flags is not None:
        fd, f_long, f_short = spec.exit_flags
        flags = {1: fd[np.asarray(f_long, dtype=bool)], -1: fd[np.asarray(f_short, dtype=bool)]}
    t = ctx.t
    out: list[Base | None] = []
    r: Any
    for r in sig.itertuples(index=False):
        dec = int(r.dec)
        k0 = int(np.searchsorted(t, dec, side="left"))
        if k0 >= len(t) - 1:
            out.append(None)
            continue
        dl = deadline(ctx, str(r.dl), k0)
        if dl < BIG and t[k0] > dl - 15 * MIN:
            out.append(None)
            continue
        side = int(r.side)
        ref = float(ctx.o[k0])
        risk = float(r.risk) if np.isfinite(r.risk) else side * (ref - float(r.stop_px))
        if not (np.isfinite(risk) and risk > 0):
            out.append(None)
            continue
        cap = int(r.cap)
        out.append(Base(dec, k0, side, ref, risk, float(r.tgt), float(r.trail), cap, dl, str(r.dl),
                        min(cap, dl, ctx.end)))
    # keep decisions of dropped signals too (they never block): the caller pairs by position
    return out, flags


def run_variant(ctx: Ctx, b: Base, var: str, flags: dict[int, np.ndarray] | None,
                chk: np.ndarray) -> dict[str, Any] | None:
    """One signal under one execution variant on its own (no one-at-a-time). None = no trade (limit not filled)."""
    fl = flags[b.side] if flags is not None else None
    ex = EXEC[var]
    if "depth" in ex:
        t_last = min(b.t_end - 1, b.dl - 15 * MIN if b.dl < BIG else BIG)
        lf = limit_fill(ctx, b.k0, b.side, b.ref, b.risk, float(ex["depth"]), int(ex["win"]), t_last)
        if lf is None:
            return None
        kf, e = lf
        risk, tgt = b.risk, b.tgt
        g, kind, _, tx = walk_px(ctx, kf, b.side, e, risk, tgt, b.trail, b.t_end, fl, first_stop_only=True)
    else:
        wide = float(ex.get("wide", 1.0))
        risk, tgt, kf, e = b.risk * wide, b.tgt / wide, b.k0, b.ref
        checks = chk if (ex.get("swapcut") and b.side > 0) else None
        g, kind, _, tx = walk_px(ctx, kf, b.side, e, risk, tgt, b.trail, b.t_end, fl, checks=checks)
    return {"t_fill": int(ctx.t[kf]), "k_fill": kf, "t_exit": tx, "side": b.side, "risk": risk, "gross": g,
            "kind": kind, "entry": e, "tgt": tgt}


def _twin_at(ctx: Ctx, kk: int, b: Base, out: dict[str, Any], hold: int | None, fl: np.ndarray | None,
             checks: np.ndarray | None, n_id: int) -> dict[str, Any] | None:
    t = ctx.t
    if kk < 0 or kk >= len(t) - 1:
        return None
    dlt = deadline(ctx, b.dlkind, kk)
    if dlt < BIG and t[kk] > dlt - 15 * MIN:
        return None
    te = min(int(t[kk]) + hold if hold is not None else BIG, dlt, ctx.end)
    e = float(ctx.o[kk])
    g, kind, _, tx = walk_px(ctx, kk, b.side, e, out["risk"], out["tgt"], b.trail, te, fl, checks=checks)
    return {"t_fill": int(t[kk]), "t_exit": tx, "side": b.side, "risk": out["risk"], "gross": g, "kind": kind,
            "entry": e, "id": n_id}


def book(ctx: Ctx, sid: str, var: str, bs: list[Base | None], outs: list[dict[str, Any] | None],
         flags: dict[int, np.ndarray] | None, chk: np.ndarray, seed_tag: str) -> dict[str, list[dict[str, Any]]]:
    """One position at a time on the variant's own exits, plus the leak-free and the drift twins of each trade."""
    t = ctx.t
    udays = np.unique(ctx.tday)
    offs = np.r_[np.arange(-20, 0), np.arange(1, 21)]
    real: list[dict[str, Any]] = []
    twins: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    busy_until = -1
    for b, out in zip(bs, outs, strict=True):
        if b is None or b.dec < busy_until or out is None:
            continue
        n_id = len(real)
        real.append({k: out[k] for k in ("t_fill", "t_exit", "side", "risk", "gross", "kind", "entry")} | {"id": n_id})
        busy_until = out["t_exit"]
        kf = int(out["k_fill"])
        fl = flags[b.side] if flags is not None else None
        checks = chk if (EXEC[var].get("swapcut") and b.side > 0) else None
        hold = (b.cap - int(t[kf])) if b.cap < BIG else None
        hi_k = int(ctx.run_end[kf])
        if hi_k - kf + 1 < 30:
            hi_k = int(np.searchsorted(t, t[kf] + 60 * MIN, side="left")) - 1
        td = int(ctx.tday[kf])
        di = int(np.searchsorted(udays, td))
        for j in range(N_TWIN):
            key = (sid, n_id, j) if seed_tag == "V0" else (sid, seed_tag, n_id, j)
            tw = _twin_at(ctx, kf + int(h_u(*key) * (hi_k - kf + 1)), b, out, hold, fl, checks, n_id)
            if tw is not None:
                twins.append(tw)
            ti = di + int(offs[int(h_u(*key, "drift") * len(offs))])
            if 0 <= ti < len(udays):
                target = int(t[kf]) + (int(udays[ti]) - td) * DAYNS
                kk = int(np.searchsorted(t, target, side="left"))
                if kk < len(t) - 1 and int(t[kk]) - target <= 30 * MIN:
                    dw = _twin_at(ctx, kk, b, out, hold, fl, checks, n_id)
                    if dw is not None:
                        drift.append(dw)
    return {"real": real, "twins": twins, "drift": drift}


# ---------------------------------------------------------------- pricing
def bps_scale(rt: np.ndarray, os_: np.ndarray, sw: np.ndarray, entry: np.ndarray) -> tuple[np.ndarray, ...]:
    """W2 price-relative costs: the cor dollar figures as a fraction of the reference price 4,000, applied to `entry`."""
    f = np.asarray(entry, dtype=float) / BPS_REF_PRICE
    return rt * f, os_ * f, sw * f


def price(rows: list[dict[str, Any]], costs: A.ArmCosts, swap: SwapSpec) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    ent = pd.DatetimeIndex(pd.to_datetime(df.t_fill.to_numpy(), utc=True))
    ext = pd.DatetimeIndex(pd.to_datetime(df.t_exit.to_numpy(), utc=True))
    sess = DEFAULT_SESSIONS.session_label(ent)
    rt, os_ = costs.screen_charges(BROKER, sess, COST_VERSION)
    nights = rollover_nights(ent, ext, swap.server_tz, swap.triple_weekday)
    sw = nights * swap.usd_per_oz(df.side.to_numpy())
    stop_exit = df.kind.isin(["stop", "trail"]).to_numpy()
    df["gross_R"] = df.gross / df.risk
    df["R"] = (df.gross + sw - rt - os_ * stop_exit) / df.risk
    df["cost_R"] = (rt + os_ * stop_exit - sw) / df.risk
    rtb, osb, swb = bps_scale(rt, os_, sw, df.entry.to_numpy())
    df["R_bps"] = (df.gross + swb - rtb - osb * stop_exit) / df.risk
    df["year"] = ent.year
    ny = ent.tz_convert(NYZ).tz_localize(None) + pd.Timedelta(hours=7)
    df["day"] = ny.normalize().to_numpy().astype("datetime64[D]").astype(np.int64)
    return df


# ---------------------------------------------------------------- statistics
def tstat(x: np.ndarray) -> float:
    if len(x) < 2:
        return float("nan")
    sd = float(np.std(x, ddof=1))
    return float(np.mean(x) / (sd / np.sqrt(len(x)))) if sd > 0 else float("nan")


def excess(real: pd.DataFrame, tw: pd.DataFrame) -> float:
    if tw is None or tw.empty or real.empty:
        return float("nan")
    m = tw.groupby("id").R.mean()
    has = real.id.isin(m.index)
    return float((real.R[has] - real.id[has].map(m)).mean()) if has.any() else float("nan")


def day_block_p(real: pd.DataFrame, n_boot: int = 2000, seed: int = 0) -> float:
    """One-sided p of mean net R > 0: share of day-block bootstrap means <= 0 (days resampled with replacement)."""
    g = real.groupby("day").R.agg(["sum", "size"])
    s, c = g["sum"].to_numpy(), g["size"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(s), size=(n_boot, len(s)))
    means = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    return float((1 + np.sum(means <= 0)) / (n_boot + 1))


def core(real: pd.DataFrame) -> dict[str, Any]:
    r = real.R.to_numpy()
    return {"n": int(len(r)), "net_R": float(r.mean()) if len(r) else float("nan"), "t": tstat(r),
            "gross_R": float(real.gross_R.mean()) if len(r) else float("nan"),
            "gross_t": tstat(real.gross_R.to_numpy())}


def summarize(rule: str, var: str, fr: dict[str, pd.DataFrame], v0: pd.DataFrame, window: str,
              n_signals: int, n_filled: int) -> dict[str, Any]:
    real, tw, dr = fr["real"], fr["twins"], fr["drift"]
    out: dict[str, Any] = {"id": f"MLX-{rule}-{var}", "rule": sid_of(rule), "rule_name": NAMES[rule], "variant": var,
                           "variant_name": EXEC[var]["name"], "signals": n_signals, "filled": n_filled}
    if real.empty:
        out.update({"n": 0, "pass": False, "verdict": "FAIL-COUNT"})
        return out
    out.update(core(real))
    v0m = float(v0.R.mean()) if not v0.empty else float("nan")
    yr = real.groupby("year").R.mean()
    out.update({
        "v0_n": int(len(v0)), "v0_net_R": v0m, "uplift_R": out["net_R"] - v0m,
        "cost_R": float(real.cost_R.mean()), "net_R_bps": float(real.R_bps.mean()),
        "t_bps": tstat(real.R_bps.to_numpy()),
        "uplift_R_bps": float(real.R_bps.mean() - v0.R_bps.mean()) if not v0.empty else float("nan"),
        "day_t": tstat(real.groupby("day").R.sum().to_numpy()),
        "control_excess": excess(real, tw), "n_twins": int(len(tw)),
        "drift_excess": excess(real, dr), "n_drift": int(len(dr)),
        "long_share": float((real.side > 0).mean()), "median_risk_usd": float(real.risk.median()),
        "exit_mix": {k: round(float(v), 3) for k, v in real.kind.value_counts(normalize=True).items()},
        "year_net_R": {str(k): round(float(v), 4) for k, v in yr.items()},
    })
    ex, ctl = out["control_excess"], out["control_excess"]
    if window == "explore":
        ok = (out["n"] >= 100 and out["net_R"] > 0 and out["t"] >= 2.0 and np.isfinite(ex) and ex >= 0.05
              and out["uplift_R"] >= 0.02)
        out["years_pos"] = int(sum(1 for y in range(2010, 2018) if y in yr.index and yr[y] > 0))
        out["pass"] = bool(ok)
        out["verdict"] = "PASS" if ok else ("FAIL-COUNT" if out["n"] < 100 else "FAIL")
    elif window == "confirm":
        r = real.R.to_numpy()
        pos = sum(1 for y in range(2018, 2026) if y in yr.index and yr[y] > 0)
        dsr = DsrInputs.of(r).dsr(K_DSR) if len(r) >= MIN_TRADES_FOR_DSR else None
        out.update({"dsr": dsr, "dsr_K": K_DSR, "years_pos": pos, "boot_p": day_block_p(real),
                    "dsr_inputs": DsrInputs.of(r).model_dump() if len(r) >= MIN_TRADES_FOR_DSR else None})
        x = real[real.year < 2025]
        xv = v0[v0.year < 2025]
        out["excl_2025"] = core(x) | {"uplift_R": float(x.R.mean() - xv.R.mean()) if len(x) and len(xv) else None}
        ok = (out["net_R"] > 0 and out["t"] >= 2.0 and dsr is not None and dsr >= 0.5 and pos >= 6
              and out["uplift_R"] > 0)
        out["pass"] = bool(ok)
        out["verdict"] = "PASS" if ok else "FAIL"
    else:
        need = 60 if rule in H4_D1_RULES else 100
        ok = out["n"] >= need and out["net_R"] > 0 and out["t"] >= 1.28 and np.isfinite(ctl) and ctl >= 0
        out["pass"] = bool(ok)
        out["verdict"] = "PASS" if ok else ("FAIL-COUNT" if out["n"] < need else "FAIL")
    return out


# ---------------------------------------------------------------- features (V5)
def features(ctx: Ctx, rule: str, bs: list[Base]) -> pd.DataFrame:
    """The 11 pre-registered point-in-time features per signal; every bar table reaches the signals through
    asof_join on available_utc = the bar's decision (close) time."""
    dec = np.array([b.dec for b in bs], dtype=np.int64)
    side = np.array([b.side for b in bs], dtype=float)
    risk = np.array([b.risk for b in bs], dtype=float)
    left = pd.DataFrame({"ts_utc": pd.to_datetime(dec, utc=True), "pos": np.arange(len(dec))})
    h1, h4, d = ctx.bars["1h"], ctx.bars["4h"], ctx.bars["1d"]
    t1 = pd.DataFrame({"available_utc": pd.to_datetime(h1.dec, utc=True), "f_atr14": atr(h1, 14).to_numpy(),
                       "f_atr100": atr(h1, 100).to_numpy(), "f_h": h1.h, "f_l": h1.lo, "f_c": h1.c,
                       "f_e20": ema(h1.s("c"), 20).to_numpy()})
    c4 = h4.s("c")
    t4 = pd.DataFrame({"available_utc": pd.to_datetime(h4.dec, utc=True), "f_c4": c4.to_numpy(),
                       "f_c4_6": c4.shift(6).to_numpy()})
    dc = d.s("c")
    e50 = ema(dc, 50)
    td = pd.DataFrame({"available_utc": pd.to_datetime(d.dec, utc=True), "f_atrd": atr(d, 20).to_numpy(),
                       "f_up": ((dc > e50) & (e50 > e50.shift(10))).astype(float).to_numpy(),
                       "f_dn": ((dc < e50) & (e50 < e50.shift(10))).astype(float).to_numpy()})
    j = asof_join(asof_join(asof_join(left, t1), t4), td).sort_values("pos")
    rng = (j.f_h - j.f_l).to_numpy()
    clv = np.where(rng > 0, (j.f_c - j.f_l).to_numpy() / np.where(rng > 0, rng, 1), 0.5)
    ts = pd.DatetimeIndex(pd.to_datetime(dec, utc=True))
    return pd.DataFrame({
        "rule": rule, "side": side, "hour": ts.hour.to_numpy(), "weekday": ts.dayofweek.to_numpy(),
        "r_atrd": risk / j.f_atrd.to_numpy(), "vol_ratio": (j.f_atr14 / j.f_atr100).to_numpy(),
        "clv": np.where(side > 0, clv, 1 - clv), "bar_r": rng / risk,
        "ext_r": side * (j.f_c - j.f_e20).to_numpy() / risk, "ret4_r": side * (j.f_c4 - j.f_c4_6).to_numpy() / risk,
        "d1_side": side * (j.f_up - j.f_dn).to_numpy()})


def fit_predict(train: pd.DataFrame, score: pd.DataFrame) -> np.ndarray:
    from lightgbm import LGBMRegressor
    cats = sorted(RULES)
    xtr, xsc = train[FEATURES].copy(), score[FEATURES].copy()
    for x in (xtr, xsc):
        x["rule"] = pd.Categorical(x["rule"], categories=cats)
    m = LGBMRegressor(**LGB_PARAMS)
    m.fit(xtr, (train.R1 - train.R0).to_numpy())
    return np.asarray(m.predict(xsc))


def learn_choices(train_pool: pd.DataFrame, score: pd.DataFrame, years: list[int] | None) -> dict[str, list[int]]:
    """Walk-forward by calendar year (prereg V5). `years` None = one frozen model on the whole `train_pool` (TEST-OLD).
    Returns per rule the decisions where V1 (limit) is chosen; every scored decision is in `scored`."""
    ok_tr = train_pool[np.isfinite(train_pool.R0) & np.isfinite(train_pool.R1)]
    sc = score[np.isfinite(score.R0) & np.isfinite(score.R1)]
    picks: list[pd.DataFrame] = []
    if years is None:
        p = fit_predict(ok_tr, sc)
        picks.append(sc.assign(pred=p))
    else:
        for y in years:
            y0 = pd.Timestamp(f"{y}-01-01", tz="UTC").value
            tr = ok_tr[(ok_tr.dec < y0) & (ok_tr.x0 < y0) & (ok_tr.x1 < y0)]
            s = sc[sc.year == y]
            if s.empty:
                continue
            picks.append(s.assign(pred=fit_predict(tr, s)))
    allp = pd.concat(picks) if picks else sc.assign(pred=np.nan).iloc[:0]
    out: dict[str, list[int]] = {}
    for rule, g in allp.groupby("rule", observed=True):
        rule = str(rule)
        out[f"{rule}:scored"] = g.dec.astype(np.int64).tolist()
        out[f"{rule}:limit"] = g.dec[g.pred > 0].astype(np.int64).tolist()
    return out


# ---------------------------------------------------------------- workers
_CTX: Ctx | None = None
_ARGS: dict[str, Any] = {}


def _init(raw: str, window: str) -> None:
    global _CTX
    _CTX = build_ctx(Path(raw), window)
    _ARGS["window"] = window
    _ARGS["swap"] = SwapSpec.shipped()
    _ARGS["costs"] = A.load_costs(ROOT / "config" / "arm_costs.yaml")
    _ARGS["chk"] = rollover_checks(_CTX, _ARGS["swap"].server_tz)


def _phase1(rule: str) -> pd.DataFrame:
    """Per signal: V0 and V1 standalone net R (unfilled V1 = 0 R) and exit times, plus the V5 features."""
    assert _CTX is not None
    ctx = _CTX
    bs, flags = bases(ctx, RULES[rule](ctx))
    keep = [b for b in bs if b is not None]
    if not keep:
        return pd.DataFrame()
    rows0, rows1, x1, filled = [], [], [], []
    for b in keep:
        o0 = run_variant(ctx, b, "V0", flags, _ARGS["chk"])
        o1 = run_variant(ctx, b, "V1", flags, _ARGS["chk"])
        assert o0 is not None
        rows0.append(o0)
        rows1.append(o1 if o1 is not None else o0)
        filled.append(o1 is not None)
        x1.append(o1["t_exit"] if o1 is not None else b.dec + 60 * MIN)
    p0 = price(rows0, _ARGS["costs"], _ARGS["swap"])
    p1 = price(rows1, _ARGS["costs"], _ARGS["swap"])
    f = features(ctx, rule, keep)
    f["dec"] = [b.dec for b in keep]
    f["year"] = pd.DatetimeIndex(pd.to_datetime(f.dec.to_numpy(), utc=True)).year
    f["R0"] = p0.R.to_numpy()
    f["R1"] = np.where(filled, p1.R.to_numpy(), 0.0)
    f["x0"] = p0.t_exit.to_numpy()
    f["x1"] = np.array(x1, dtype=np.int64)
    return f


def _phase2(job: tuple[str, list[str], dict[str, list[int]] | None]) -> dict[str, Any]:
    """Per variant: priced real trades, leak-free twins and drift twins (in process only; aggregates leave)."""
    assert _CTX is not None
    ctx = _CTX
    rule, variants, choice = job
    sid = sid_of(rule)
    bs, flags = bases(ctx, RULES[rule](ctx))
    chk = _ARGS["chk"]
    costs, swap = _ARGS["costs"], _ARGS["swap"]
    res: dict[str, Any] = {}
    cache: dict[str, list[dict[str, Any] | None]] = {}

    def outs(var: str) -> list[dict[str, Any] | None]:
        if var not in cache:
            cache[var] = [run_variant(ctx, b, var, flags, chk) if b is not None else None for b in bs]
        return cache[var]

    def priced(var_book: dict[str, list[dict[str, Any]]]) -> dict[str, pd.DataFrame]:
        return {k: price(v, costs, swap) for k, v in var_book.items()}

    n_sig = sum(1 for b in bs if b is not None)
    for var in variants:
        if var == "V5":
            if choice is None:
                continue
            scored = set(choice.get(f"{rule}:scored", []))
            lim = set(choice.get(f"{rule}:limit", []))
            bs5 = [b if (b is not None and b.dec in scored) else None for b in bs]
            o0, o1 = outs("V0"), outs("V1")
            o5 = [(o1[i] if bs5[i].dec in lim else o0[i]) if bs5[i] is not None else None  # type: ignore[union-attr]
                  for i in range(len(bs))]
            res["V5"] = priced(book(ctx, sid, "V5", bs5, o5, flags, chk, "V5"))
            res["V0r"] = priced(book(ctx, sid, "V0", bs5, o0, flags, chk, "V0"))
            res["V5:signals"] = sum(1 for b in bs5 if b is not None)
            res["V5:filled"] = sum(1 for i, b in enumerate(bs5) if b is not None and o5[i] is not None)
            res["V5:limit_share"] = len(lim) / max(len(scored), 1)
            continue
        o = outs(var)
        res[var] = priced(book(ctx, sid, var, bs, o, flags, chk, var))
        res[f"{var}:signals"] = n_sig
        res[f"{var}:filled"] = sum(1 for x in o if x is not None)
    return {"rule": rule, "res": res}


# ---------------------------------------------------------------- driver
def _out_dir() -> Path:
    return Path.home() / "tmp" / "mlprob_mlexec"


def _pool(raw: Path, window: str, workers: int) -> ProcessPoolExecutor:
    assert workers <= 2, "the shared Mac: at most 2 workers per lane"
    return ProcessPoolExecutor(workers, initializer=_init, initargs=(str(raw), window))


def _signals(raw: Path, window: str, workers: int) -> pd.DataFrame:
    cache = _out_dir() / f"signals_{window}.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    with _pool(raw, window, workers) as ex:
        df = pd.concat(list(ex.map(_phase1, list(RULES))), ignore_index=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(cache)
    return df


def _summ(out: dict[str, Any], window: str, variants: list[str]) -> list[dict[str, Any]]:
    rule, res = out["rule"], out["res"]
    rows = []
    for var in variants:
        if var not in res:
            continue
        v0 = res["V0r"]["real"] if var == "V5" else res["V0"]["real"]
        s = summarize(rule, var, res[var], v0, window, res[f"{var}:signals"], res[f"{var}:filled"])
        if var == "V5":
            s["limit_share"] = res["V5:limit_share"]
        rows.append(s)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=ROOT.parents[2] / "raw" if (ROOT.parents[2] / "raw").exists()
                    else ROOT / "raw")
    ap.add_argument("--window", choices=list(WINDOWS), default="explore")
    ap.add_argument("--out", type=Path, default=_out_dir() / "explore.json")
    ap.add_argument("--passers", type=Path, help="previous window's result JSON (confirm/test_old run passers only)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--v0-only", action="store_true", help="engine parity: V0 only (the trend lane's public numbers)")
    a = ap.parse_args()
    w = a.window
    if a.v0_only:
        with _pool(a.raw, w, a.workers) as ex:
            res = list(ex.map(_phase2, [(r, ["V0"], None) for r in RULES]))
        for o in res:
            for s in _summ(o, w, ["V0"]):
                print(s["rule"], s["n"], round(s["net_R"], 3), round(s["t"], 2), round(s["gross_R"], 3),
                      round(s["control_excess"], 3))
        return
    if w == "explore":
        todo: dict[str, list[str]] = {r: ["V0", *NEW_VARIANTS] for r in RULES}
    else:
        if a.passers is None:
            raise SystemExit(f"{w} needs --passers: it runs the previous window's passers only")
        prev = json.loads(a.passers.read_text())
        todo = {}
        for r in prev["results"]:
            if r.get("pass") and r["variant"] != "V0":
                todo.setdefault(r["rule"][-2:], ["V0"]).append(r["variant"])
        if not todo:
            print(f"no passers in {a.passers}: {w} not run")
            return
        if w == "test_old":
            need = [a.raw / f"xauusd_1m_dukascopy_{y}.parquet" for y in range(2005, 2010)]
            missing = [p.name for p in need if not p.exists()]
            if missing:
                print("TEST-OLD data missing, old-test pending:", missing)
                a.out.parent.mkdir(parents=True, exist_ok=True)
                a.out.write_text(json.dumps({"window": w, "status": "pending", "missing": missing,
                                             "todo": todo}, indent=1))
                return
    choice: dict[str, list[int]] | None = None
    if any("V5" in v for v in todo.values()):
        if w == "explore":
            sx = _signals(a.raw, "explore", a.workers)
            choice = learn_choices(sx, sx, list(range(2012, 2018)))
        elif w == "confirm":
            sx, sc = _signals(a.raw, "explore", a.workers), _signals(a.raw, "confirm", a.workers)
            choice = learn_choices(pd.concat([sx, sc], ignore_index=True), sc, list(range(2018, 2026)))
        else:
            sx, sc = _signals(a.raw, "explore", a.workers), _signals(a.raw, "confirm", a.workers)
            so = _signals(a.raw, "test_old", a.workers)
            choice = learn_choices(pd.concat([sx, sc], ignore_index=True), so, None)
    with _pool(a.raw, w, a.workers) as ex:
        outs = list(ex.map(_phase2, [(r, v, choice) for r, v in todo.items()]))
    rows = [s for o in outs for s in _summ(o, w, [v for v in todo[o["rule"]] if v != "V0" or w == "explore"])]
    if w == "confirm":
        m = len(rows)
        ps = sorted(r["boot_p"] for r in rows)
        bh = max([i + 1 for i, p in enumerate(ps) if p <= 0.10 * (i + 1) / m], default=0)
        cut = ps[bh - 1] if bh else -1.0
        for r in rows:
            r["bh_q10_pass_w2"] = bool(r["boot_p"] <= cut and r["net_R"] > 0 and r["n"] >= 100
                                       and r["years_pos"] >= 5)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"window": w, "cost_version": COST_VERSION, "K": K_DSR, "results": rows},
                                indent=1, default=float))
    for r in rows:
        print(r["id"], r["verdict"], {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()
                                      if k in ("n", "gross_R", "net_R", "t", "uplift_R", "control_excess",
                                               "drift_excess", "net_R_bps", "dsr", "years_pos")})


if __name__ == "__main__":
    main()
