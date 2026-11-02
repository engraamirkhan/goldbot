"""Sprint-200 lane `macro`: the 33 pre-registered macro/event strategies (docs/research/sprint200/macro-prereg.md,
frozen at commit dd9883a2 before any look). This file implements that text; it changes no rule.

    uv run python scripts/sprint200/macro_screen.py --phase explore --out ~/tmp/s200macro/explore.json [--ids 01,02]
    uv run python scripts/sprint200/macro_screen.py --phase confirm --explore ~/tmp/s200macro/explore.json \
        --out ~/tmp/s200macro/confirm.json

Explore: decisions in 2010-01-01..2017-12-31. Confirm: decisions in 2018-01-01..2025-09-30, run ONLY for ids that pass
the explore rule in the explore JSON (refused otherwise), one look. Holdout guard: no bar at or after 2025-10-01 is
loaded (asserted), the 2026 file is never opened, a trade (or twin) whose exit would reach 2025-10-01 is dropped.
Outputs are statistics only (JSON); no market data or trade rows are written into the repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.data.macro import first_release  # noqa: E402
from goldbot.data.timeutil import epoch_ns  # noqa: E402
from goldbot.labels import SwapSpec, rollover_nights  # noqa: E402
from goldbot.research import arms as A  # noqa: E402
from goldbot.research.metrics import DsrInputs  # noqa: E402

HOLDOUT = pd.Timestamp("2025-10-01", tz="UTC")
WINDOWS = {"explore": (pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC")),
           "confirm": (pd.Timestamp("2018-01-01", tz="UTC"), HOLDOUT)}
LOAD_YEARS = {"explore": (2010, 2018), "confirm": (2017, 2025)}   # 2018 in explore: exit tail only (2 weeks)
EXPLORE_TAIL = pd.Timestamp("2018-01-15", tz="UTC")
COST_VERSION = "cor-2026-10-31.2"
BROKER = "icm"
K_TOTAL = 3229
N_TWINS = 10
STOP_FLOOR = 1.50
NY, LDN = "America/New_York", "Europe/London"
MIN = 60_000_000_000                                   # one minute in ns
T1 = {"cpi", "nfp", "pce", "retail_sales", "ppi"}
ISM = {"ism_manufacturing", "ism_services"}
FED = {"fomc_statement", "fomc_minutes"}
RAW = Path("/Users/aamirkhan/myproject/goldbot/raw")
CAL = Path.home() / "tmp/kcal_out/us_calendar.parquet"
MACRO = Path.home() / "tmp/macro_dl/macro_fred.parquet"
MACRO_SHA = "47473d2c3f9c4b35ae4b20368cbcb1f19678e65f40e81ed755363a6c45eaa2ed"


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ------------------------------------------------------------------ data
@dataclass
class Bars:
    ts: np.ndarray        # int64 ns, bar open
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    sess: np.ndarray      # session label per bar ("" outside every session), for costs and twins
    day: np.ndarray       # UTC calendar day (int days since epoch)

    def first_at_or_after(self, x: int) -> int:
        return int(np.searchsorted(self.ts, x, side="left"))

    def p(self, x: int) -> float:
        """P(x): mid close of the last bar visible (open + 1m) at or before x."""
        k = int(np.searchsorted(self.ts + MIN, x, side="right")) - 1
        return float(self.c[k]) if k >= 0 else math.nan


def load_bars(phase: str) -> Bars:
    y0, y1 = LOAD_YEARS[phase]
    parts = []
    for y in range(y0, y1 + 1):
        assert y <= 2025, "the 2026 file is never opened"
        f = RAW / f"xauusd_1m_dukascopy_{y}.parquet"
        parts.append(pd.read_parquet(f, columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close",
                                                 "ask_open", "ask_high", "ask_low", "ask_close"]))
    b = pd.concat(parts, ignore_index=True)
    b["ts_utc"] = pd.to_datetime(b["ts_utc"], utc=True)
    b = b.drop_duplicates("ts_utc").sort_values("ts_utc")
    hi = EXPLORE_TAIL if phase == "explore" else HOLDOUT
    b = b[b["ts_utc"] < hi].reset_index(drop=True)
    assert b["ts_utc"].max() < HOLDOUT, "holdout bar loaded"
    idx = pd.DatetimeIndex(b["ts_utc"]).as_unit("ns")
    ts = np.asarray(epoch_ns(idx), dtype=np.int64)
    m = {k: ((b[f"bid_{k}"].to_numpy(float) + b[f"ask_{k}"].to_numpy(float)) / 2) for k in ("open", "high", "low",
                                                                                          "close")}
    lab = DEFAULT_SESSIONS.session_label(idx)
    is_open = DEFAULT_SESSIONS.is_open(idx)
    sess = np.where(is_open, lab, "").astype(object)
    return Bars(ts, m["open"], m["high"], m["low"], m["close"], sess, ts // (86_400 * 10**9))


def true_range(r: pd.DataFrame, pc: pd.Series) -> pd.Series:
    """max(h - l, abs(h - prev c), abs(l - prev c)); NaN where the previous close is missing (as np.maximum)."""
    return pd.concat([r["h"] - r["l"], (r["h"] - pc).abs(), (r["l"] - pc).abs()], axis=1).max(axis=1, skipna=False)


def resample_atr(b: Bars, freq: str, n: int) -> pd.Series:
    """ATR(n) of clock-aligned `freq` mid bars, indexed by bar CLOSE time (ns); value known at that time."""
    s = pd.DataFrame({"o": b.o, "h": b.h, "l": b.lo, "c": b.c}, index=pd.to_datetime(b.ts, utc=True))
    r = s.resample(freq, label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    pc = r["c"].shift(1)
    tr = true_range(r, pc)
    atr = tr.rolling(n).mean()
    close_t = np.asarray(epoch_ns(pd.DatetimeIndex(r.index)), dtype=np.int64) + int(pd.Timedelta(freq).value)
    return pd.Series(atr.to_numpy(), index=close_t)


def bars4h(b: Bars) -> pd.DataFrame:
    s = pd.DataFrame({"o": b.o, "h": b.h, "l": b.lo, "c": b.c}, index=pd.to_datetime(b.ts, utc=True))
    r = s.resample("4h", label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    pc = r["c"].shift(1)
    tr = true_range(r, pc)
    r["atr_prior"] = tr.rolling(14).mean().shift(1)          # 14 completed bars BEFORE this one
    r["open_ns"] = r.index.to_numpy().astype("datetime64[ns]").astype(np.int64)
    return r


def dbars(b: Bars) -> pd.DataFrame:
    """D-bars (trading days ending 17:00 New York), indexed by close time ns, with every PIT field of section 2."""
    t = pd.to_datetime(b.ts, utc=True)
    ny = t.tz_convert(NY)
    key = (ny + pd.Timedelta(hours=7)).normalize().tz_localize(None)        # NY date the D-bar closes on
    s = pd.DataFrame({"o": b.o, "h": b.h, "l": b.lo, "c": b.c, "ts": b.ts, "k": key})
    g = s.groupby("k", sort=True).agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"),
                                      first_ts=("ts", "first"), last_ts=("ts", "last"))
    close_t = [int(pd.Timestamp(d).replace(hour=17).tz_localize(NY).value) for d in g.index]
    g["close_ns"] = close_t
    pc = g["c"].shift(1)
    tr = true_range(g, pc)
    g["atrd"] = tr.rolling(20).mean()
    g["r"] = np.log(g["c"] / pc)
    g["sd60"] = g["r"].rolling(60).std()
    g["sma50"] = g["c"].rolling(50).mean()
    g["rv20"] = g["r"].rolling(20).std()
    g["rv_hi"] = g["rv20"].rolling(252).quantile(2 / 3)
    g["rv_lo"] = g["rv20"].rolling(252).quantile(1 / 3)
    g["gap_before_h"] = (g["first_ts"] - g["last_ts"].shift(1)) / 3.6e12
    g["date"] = g.index
    return g.reset_index(drop=True)


def asof(series_index: np.ndarray, values: np.ndarray, x: int) -> float:
    k = int(np.searchsorted(series_index, x, side="right")) - 1
    return float(values[k]) if k >= 0 else math.nan


class Ctx:
    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.b = load_bars(phase)
        self.a15 = resample_atr(self.b, "15min", 14)
        self.a1h = resample_atr(self.b, "1h", 14)
        self.h4 = bars4h(self.b)
        self.d = dbars(self.b)
        self.events = load_events()
        macro = pd.read_parquet(MACRO)
        self.ry = first_release(macro, "real_yield_10y")
        self.vix = first_release(macro, "VIXCLS")
        self.costs = A.load_costs(ROOT / "config" / "arm_costs.yaml")
        self.costs.require_version(COST_VERSION)
        self.swap = SwapSpec.shipped()
        self.groups = self._twin_groups()

    def _twin_groups(self) -> dict[tuple[int, str], np.ndarray]:
        names = ["", "asia", "london", "newyork"]
        code = np.zeros(len(self.b.ts), dtype=np.int64)
        for k, nm in enumerate(names):
            code[self.b.sess == nm] = k
        key = self.b.day * 4 + code
        order = np.argsort(key, kind="stable")
        uk, start = np.unique(key[order], return_index=True)
        bounds = list(start) + [len(order)]
        return {(int(u // 4), names[int(u % 4)]): order[bounds[i]:bounds[i + 1]] for i, u in enumerate(uk)}

    def atr(self, s: pd.Series, x: int) -> float:
        return asof(s.index.to_numpy(), s.to_numpy(), x)

    def dfield(self, field: str, x: int) -> float:
        return asof(self.d["close_ns"].to_numpy(), self.d[field].to_numpy(), x)

    def trend_sign(self, x: int) -> float:
        c, sma = self.dfield("c", x), self.dfield("sma50", x)
        return float(np.sign(c - sma)) if not (math.isnan(c) or math.isnan(sma)) else math.nan

    def rv_regime(self, x: int) -> str:
        rv, hi, lo = self.dfield("rv20", x), self.dfield("rv_hi", x), self.dfield("rv_lo", x)
        if any(math.isnan(v) for v in (rv, hi, lo)):
            return ""
        return "top" if rv >= hi else "bottom" if rv <= lo else "mid"

    def ry_sign(self, x: int) -> float:
        av = self.ry["available_utc"].to_numpy().astype("datetime64[ns]").astype(np.int64)
        k = int(np.searchsorted(av, x, side="right"))       # observations available at x: a prefix (cummax)
        if k < 21:
            return math.nan
        v = self.ry["value"].to_numpy(float)
        return float(np.sign(v[k - 1] - v[k - 21]))

    def vix_high(self, x: int) -> bool | None:
        av = self.vix["available_utc"].to_numpy().astype("datetime64[ns]").astype(np.int64)
        k = int(np.searchsorted(av, x, side="right"))
        if k < 252:
            return None
        v = self.vix["value"].to_numpy(float)[k - 252:k]
        return bool(v[-1] >= np.median(v))


def load_events() -> pd.DataFrame:
    cal = pd.read_parquet(CAL)
    cal["event_utc"] = pd.to_datetime(cal["event_utc"], utc=True)
    cal["available_utc"] = pd.to_datetime(cal["available_utc"], utc=True)
    canc = cal[cal["status"] == "cancelled"]
    sch = cal[cal["status"] == "scheduled"].copy()
    sch = sch[sch["available_utc"] <= sch["event_utc"] - pd.Timedelta(days=1)]
    if len(canc):
        c = canc.groupby("event_id")["available_utc"].min()
        first_c = sch["event_id"].map(c)
        sch = sch[~(first_c.notna() & (first_c < sch["event_utc"]))]
    sch = sch[sch["event_type"] != "fomc_press_conference"]
    sch = sch[sch["event_utc"] < HOLDOUT]
    g = sch.groupby("event_utc")["event_type"].apply(lambda s: frozenset(s)).reset_index()
    g["t"] = g["event_utc"].to_numpy().astype("datetime64[ns]").astype(np.int64)
    assert (g["event_utc"] < HOLDOUT).all()
    return g.sort_values("t").reset_index(drop=True)


def pool_events(ev: pd.DataFrame, pool: str) -> np.ndarray:
    def keep(types: frozenset[str]) -> bool:
        if pool == "T1":
            return bool(types & T1)
        if pool == "CLM":
            return "jobless_claims" in types and not types & T1
        if pool == "ISM":
            return bool(types & ISM)
        if pool == "FED":
            return bool(types & FED)
        if pool == "T1FED":
            return bool(types & (T1 | FED))
        if pool == "CLMISM":
            return ("jobless_claims" in types and not types & T1) or bool(types & ISM)
        if pool == "ALL":
            return True
        raise ValueError(pool)
    return ev.loc[[keep(x) for x in ev["event_type"]], "t"].to_numpy()


# ------------------------------------------------------------------ candidates
# a candidate: decision ns, side, stop distance $, target in R (nan = none), planned exit ns
Cand = dict[str, float]


def next_weekday_ny(t_ns: int, hour: int) -> int:
    d = pd.Timestamp(t_ns, tz="UTC").tz_convert(NY).tz_localize(None).normalize() + pd.Timedelta(days=1)
    while d.dayofweek >= 5:
        d = d + pd.Timedelta(days=1)
    return int(d.replace(hour=hour).tz_localize(NY).value)


def ny_at(t_ns: int, hour: int) -> int:
    d = pd.Timestamp(t_ns, tz="UTC").tz_convert(NY).tz_localize(None).normalize()
    return int(d.replace(hour=hour).tz_localize(NY).value)


def ldn_at_on_nydate(date: pd.Timestamp, hour: int) -> int:
    return int(pd.Timestamp(date).replace(hour=hour).tz_localize(LDN).value)


def ny_at_on_nydate(date: pd.Timestamp, hour: int) -> int:
    return int(pd.Timestamp(date).replace(hour=hour).tz_localize(NY).value)


def pre_range(c: Ctx, t: int) -> tuple[float, float] | None:
    b = c.b
    i0, i1 = b.first_at_or_after(t - 60 * MIN), b.first_at_or_after(t)        # opens in [t-60m, t-1m]
    if i1 - i0 < 40:
        return None
    return float(b.h[i0:i1].max()), float(b.lo[i0:i1].min())


def brk(c: Ctx, pool: str, hold_mode: str = "2r") -> list[Cand]:
    out: list[Cand] = []
    b = c.b
    for t in pool_events(c.events, pool):
        t = int(t)
        rng = pre_range(c, t)
        if rng is None:
            continue
        hh, ll = rng
        i0, i1 = b.first_at_or_after(t), b.first_at_or_after(t + 30 * MIN)
        side, k = 0, -1
        for j in range(i0, i1):
            if b.c[j] > hh:
                side, k = 1, j
                break
            if b.c[j] < ll:
                side, k = -1, j
                break
        if side == 0:
            continue
        dec = int(b.ts[k]) + MIN
        a15 = c.atr(c.a15, t)
        if math.isnan(a15):
            continue
        floor = max(STOP_FLOOR, 0.5 * a15)
        e = b.first_at_or_after(dec)
        if e >= len(b.ts):
            continue
        entry = float(b.o[e])
        if hold_mode == "2r":
            ref = (hh + ll) / 2
            stop = max(side * (entry - ref), floor)
            out.append({"dec": dec, "side": side, "stop": stop, "tgt": 2.0, "exit": t + 240 * MIN, "t": t})
        else:   # S33: opposite side of the range, no target, 16:00 NY
            ref = ll if side > 0 else hh
            stop = max(side * (entry - ref), floor)
            ex = ny_at(t, 16)
            if ex <= dec:
                continue
            out.append({"dec": dec, "side": side, "stop": stop, "tgt": math.nan, "exit": ex, "t": t})
    return out


def fade(c: Ctx, pool: str) -> list[Cand]:
    out: list[Cand] = []
    b = c.b
    for t in pool_events(c.events, pool):
        t = int(t)
        pre, post = b.p(t), b.p(t + 15 * MIN)
        a = c.atr(c.a15, t)
        if any(math.isnan(v) for v in (pre, post, a)):
            continue
        m = post - pre
        if abs(m) < 1.0 * a or m == 0:
            continue
        side = -int(np.sign(m))
        i0, i1 = b.first_at_or_after(t), b.first_at_or_after(t + 15 * MIN)
        if i1 <= i0:
            continue
        dec = t + 15 * MIN
        e = b.first_at_or_after(dec)
        if e >= len(b.ts):
            continue
        entry = float(b.o[e])
        lvl = float(b.h[i0:i1].max()) + 0.25 * a if side < 0 else float(b.lo[i0:i1].min()) - 0.25 * a
        stop = max(side * (entry - lvl), STOP_FLOOR)
        out.append({"dec": dec, "side": side, "stop": stop, "tgt": 1.0, "exit": t + 120 * MIN, "t": t})
    return out


def drift(c: Ctx, pool: str) -> list[Cand]:
    out: list[Cand] = []
    b = c.b
    for t in pool_events(c.events, pool):
        t = int(t)
        pre, p30 = b.p(t), b.p(t + 30 * MIN)
        a15, a1h = c.atr(c.a15, t), c.atr(c.a1h, t)
        if any(math.isnan(v) for v in (pre, p30, a15, a1h)):
            continue
        m = p30 - pre
        if abs(m) < 0.5 * a15 or m == 0:
            continue
        dec = t + 30 * MIN
        ex = ny_at(t, 16)
        if ex - dec < 30 * MIN:
            continue
        out.append({"dec": dec, "side": int(np.sign(m)), "stop": max(1.5 * a1h, STOP_FLOOR), "tgt": math.nan,
                    "exit": ex, "t": t})
    return out


def nextday(c: Ctx, pool: str) -> list[Cand]:
    out: list[Cand] = []
    b = c.b
    for t in pool_events(c.events, pool):
        t = int(t)
        dec = ny_at(t, 20)
        if dec <= t:
            continue
        pre, pd_ = b.p(t), b.p(dec)
        ad = c.dfield("atrd", dec)
        if any(math.isnan(v) for v in (pre, pd_, ad)):
            continue
        m = pd_ - pre
        if abs(m) < 0.5 * ad or m == 0:
            continue
        out.append({"dec": dec, "side": int(np.sign(m)), "stop": max(ad, STOP_FLOOR), "tgt": math.nan,
                    "exit": next_weekday_ny(t, 16), "t": t})
    return out


def pre_long(c: Ctx, pool: str) -> list[Cand]:
    out: list[Cand] = []
    b = c.b
    for t in pool_events(c.events, pool):
        t = int(t)
        dec = t - 24 * 60 * MIN
        k = int(np.searchsorted(b.ts + MIN, dec, side="right")) - 1
        if k < 0 or int(b.ts[k]) + MIN < dec - 5 * MIN:
            continue
        ad = c.dfield("atrd", dec)
        if math.isnan(ad):
            continue
        out.append({"dec": dec, "side": 1, "stop": max(ad, STOP_FLOOR), "tgt": math.nan, "exit": t - 5 * MIN, "t": t})
    return out


def pre_unwind(c: Ctx, pool: str) -> list[Cand]:
    out: list[Cand] = []
    b = c.b
    for t in pool_events(c.events, pool):
        t = int(t)
        pre, p24 = b.p(t), b.p(t - 24 * 60 * MIN)
        ad, a1h = c.dfield("atrd", t), c.atr(c.a1h, t)
        if any(math.isnan(v) for v in (pre, p24, ad, a1h)):
            continue
        d = pre - p24
        if abs(d) < 0.5 * ad or d == 0:
            continue
        out.append({"dec": t + 5 * MIN, "side": -int(np.sign(d)), "stop": max(1.5 * a1h, STOP_FLOOR),
                    "tgt": math.nan, "exit": t + 240 * MIN, "t": t})
    return out


def shock(c: Ctx, mode: str) -> list[Cand]:
    out: list[Cand] = []
    d = c.d
    ev_t = pool_events(c.events, "T1FED")
    for i in range(1, len(d)):
        prev = d.iloc[i - 1]
        row = d.iloc[i]
        date = pd.Timestamp(row["date"])
        dec = ldn_at_on_nydate(date, 8)
        ex = ny_at_on_nydate(date, 16)
        r = float(prev["r"])
        sd = c.dfield("sd60", dec)
        ad = c.dfield("atrd", dec)
        if any(math.isnan(v) for v in (r, sd, ad)) or r == 0:
            continue
        if mode in ("mon_fade", "mon_cont"):
            if not (row["gap_before_h"] > 24) or abs(r) < 1.0 * sd:
                continue
            side = -int(np.sign(r)) if mode == "mon_fade" else int(np.sign(r))
        elif mode == "day_fade":
            if abs(r) < 1.5 * sd:
                continue
            side = -int(np.sign(r))
        else:   # event-day fade
            lo_t, hi_t = int(d.iloc[i - 2]["close_ns"]) if i >= 2 else 0, int(prev["close_ns"])
            has = bool(((ev_t > lo_t) & (ev_t <= hi_t)).any())
            if not has or abs(r) < 1.0 * sd:
                continue
            side = -int(np.sign(r))
        if ex <= dec:
            continue
        out.append({"dec": dec, "side": side, "stop": max(ad, STOP_FLOOR), "tgt": math.nan, "exit": ex, "t": dec})
    return out


def h4_event(c: Ctx) -> list[Cand]:
    out: list[Cand] = []
    h4 = c.h4
    opens = h4["open_ns"].to_numpy()
    seen: set[int] = set()
    for t in pool_events(c.events, "T1FED"):
        t = int(t)
        bo = t - (t % (4 * 60 * MIN))
        k = int(np.searchsorted(opens, bo))
        if k >= len(opens) or opens[k] != bo or bo in seen:
            continue
        seen.add(bo)
        row = h4.iloc[k]
        body, a = float(row["c"] - row["o"]), float(row["atr_prior"])
        if math.isnan(a) or abs(body) < 0.5 * a or body == 0:
            continue
        dec = bo + 4 * 60 * MIN
        out.append({"dec": dec, "side": int(np.sign(body)), "stop": max(a, STOP_FLOOR), "tgt": 2.0,
                    "exit": dec + 8 * 60 * MIN, "t": t})
    return out


def gate(cands: list[Cand], f: Callable[[Cand], bool]) -> list[Cand]:
    return [x for x in cands if f(x)]


def strategies(c: Ctx) -> dict[str, Callable[[], list[Cand]]]:
    tr = lambda x: c.trend_sign(int(x["dec"])) == x["side"]  # noqa: E731
    return {
        "01": lambda: brk(c, "T1"), "02": lambda: brk(c, "CLM"), "03": lambda: brk(c, "ISM"),
        "04": lambda: brk(c, "FED"), "05": lambda: brk(c, "ALL"),
        "06": lambda: fade(c, "T1"), "07": lambda: fade(c, "CLM"), "08": lambda: fade(c, "ISM"),
        "09": lambda: fade(c, "ALL"),
        "10": lambda: drift(c, "T1"), "11": lambda: drift(c, "CLM"), "12": lambda: drift(c, "ISM"),
        "13": lambda: drift(c, "ALL"),
        "14": lambda: nextday(c, "T1"), "15": lambda: nextday(c, "FED"), "16": lambda: nextday(c, "ALL"),
        "17": lambda: pre_long(c, "T1FED"), "18": lambda: pre_unwind(c, "T1FED"),
        "19": lambda: pre_unwind(c, "CLMISM"),
        "20": lambda: shock(c, "mon_fade"), "21": lambda: shock(c, "mon_cont"), "22": lambda: shock(c, "day_fade"),
        "23": lambda: shock(c, "event_fade"),
        "24": lambda: gate(brk(c, "T1"), tr),
        "25": lambda: gate(brk(c, "ALL"), lambda x: c.rv_regime(int(x["dec"])) == "top"),
        "26": lambda: gate(fade(c, "ALL"), lambda x: c.rv_regime(int(x["dec"])) == "bottom"),
        "27": lambda: gate(drift(c, "ALL"), tr),
        "28": lambda: gate(nextday(c, "ALL"), tr),
        "29": lambda: h4_event(c),
        "30": lambda: gate(h4_event(c), tr),
        "31": lambda: gate(brk(c, "ALL"), lambda x: c.ry_sign(int(x["dec"])) == -x["side"]),
        "32": lambda: gate(brk(c, "ALL"), lambda x: c.vix_high(int(x["dec"])) is True),
        "33": lambda: brk(c, "T1", hold_mode="hold"),
    }


# ------------------------------------------------------------------ simulation
def simulate(b: Bars, e: int, side: int, stop: float, tgt_r: float, exit_ns: int) -> tuple[float, int, int]:
    """(move $/oz signed, exit time ns, stop_exit 0/1) from entry bar e (fill at its open)."""
    entry = float(b.o[e])
    kend = max(int(np.searchsorted(b.ts, exit_ns, side="left")), e + 1)
    sp = entry - side * stop
    tp = entry + side * stop * tgt_r if not math.isnan(tgt_r) else math.nan
    sl = slice(e, kend)
    if side > 0:
        hs = b.lo[sl] <= sp
        ht = b.h[sl] >= tp if not math.isnan(tp) else np.zeros(kend - e, bool)
    else:
        hs = b.h[sl] >= sp
        ht = b.lo[sl] <= tp if not math.isnan(tp) else np.zeros(kend - e, bool)
    js = int(np.argmax(hs)) if hs.any() else 10**12
    jt = int(np.argmax(ht)) if ht.any() else 10**12
    if js <= jt and js < 10**12:
        j = e + js
        px = min(float(b.o[j]), sp) if side > 0 else max(float(b.o[j]), sp)
        return side * (px - entry), int(b.ts[j]) + MIN, 1
    if jt < 10**12:
        j = e + jt
        px = max(float(b.o[j]), tp) if side > 0 else min(float(b.o[j]), tp)
        return side * (px - entry), int(b.ts[j]) + MIN, 0
    if kend < len(b.ts):
        return side * (float(b.o[kend]) - entry), int(b.ts[kend]), 0
    return side * (float(b.c[-1]) - entry), int(b.ts[-1]) + MIN, 0


def net_r(c: Ctx, move: np.ndarray, risk: np.ndarray, stop: np.ndarray, sess: np.ndarray, ent: np.ndarray,
          ext: np.ndarray, side: np.ndarray, overshoot: float | None = None) -> np.ndarray:
    nights = rollover_nights(pd.to_datetime(ent, utc=True), pd.to_datetime(ext, utc=True), c.swap.server_tz,
                             c.swap.triple_weekday)
    swap = nights * c.swap.usd_per_oz(side)
    return A.screen_net_r(move + swap, risk, stop, c.costs, broker=BROKER, sessions=sess, version=COST_VERSION,
                          overshoot_usd=overshoot)


def run_one(c: Ctx, sid: str, cands: list[Cand]) -> dict[str, Any]:
    lo, hi = WINDOWS[c.phase]
    b = c.b
    cands = sorted((x for x in cands if lo.value <= x["dec"] < hi.value), key=lambda x: x["dec"])
    rows: list[dict[str, Any]] = []
    last_exit = -1
    for x in cands:
        if c.phase == "confirm" and x["exit"] >= HOLDOUT.value:
            continue
        e = b.first_at_or_after(int(x["dec"]))
        if e >= len(b.ts) or int(b.ts[e]) >= int(x["exit"]):
            continue
        if int(b.ts[e]) < last_exit:
            continue
        move, ex, st = simulate(b, e, int(x["side"]), float(x["stop"]), float(x["tgt"]), int(x["exit"]))
        if c.phase == "confirm":
            assert ex < HOLDOUT.value
        last_exit = ex
        rows.append({"e": e, "ent": int(b.ts[e]), "side": int(x["side"]), "stop": float(x["stop"]),
                     "tgt": float(x["tgt"]), "hold": int(x["exit"]) - int(b.ts[e]), "move": move, "ext": ex,
                     "st": st, "sess": b.sess[e], "dec": int(x["dec"])})
    n = len(rows)
    if n == 0:
        return {"id": sid, "n": 0, "n_candidates": len(cands)}
    df = pd.DataFrame(rows)
    args = (df["move"].to_numpy(), df["stop"].to_numpy(), df["st"].to_numpy(float), df["sess"].to_numpy(),
            df["ent"].to_numpy(), df["ext"].to_numpy(), df["side"].to_numpy())
    nr = net_r(c, *args)
    nr_stress = net_r(c, *args, overshoot=A.OVERSHOOT_STRESS_USD)
    gross = df["move"].to_numpy() / df["stop"].to_numpy()
    # twins
    tw: list[tuple[float, float, int, str, int, int, int]] = []
    for i, r in enumerate(rows):
        grp = c.groups.get((int(b.day[r["e"]]), r["sess"]))
        if grp is None or len(grp) == 0:
            continue
        rng = np.random.default_rng(int(hashlib.sha256(f"{sid}|{i}".encode()).hexdigest()[:16], 16))
        for j in rng.choice(grp, size=N_TWINS, replace=True):
            j = int(j)
            exn = int(b.ts[j]) + r["hold"]
            if c.phase == "confirm" and exn >= HOLDOUT.value:
                continue
            mv, ex, st = simulate(b, j, r["side"], r["stop"], r["tgt"], exn)
            if c.phase == "confirm" and ex >= HOLDOUT.value:
                continue
            tw.append((mv, r["stop"], st, b.sess[j], int(b.ts[j]), ex, r["side"]))
    if tw:
        t_arr = list(zip(*tw, strict=True))
        tnr = net_r(c, np.array(t_arr[0]), np.array(t_arr[1]), np.array(t_arr[2], float), np.array(t_arr[3]),
                    np.array(t_arr[4]), np.array(t_arr[5]), np.array(t_arr[6]))
        control = float(np.mean(tnr))
    else:
        control = math.nan
    mean = float(nr.mean())
    sd = float(nr.std(ddof=1)) if n > 1 else math.nan
    t = mean / (sd / math.sqrt(n)) if n > 1 and sd > 0 else math.nan
    yrs = pd.to_datetime(df["dec"], utc=True).dt.year
    by_year = {int(y): [int((yrs == y).sum()), round(float(nr[(yrs == y).to_numpy()].mean()), 4)]
               for y in sorted(yrs.unique())}
    res: dict[str, Any] = {
        "id": sid, "n": n, "n_candidates": len(cands), "net_mean_r": round(mean, 4), "t": round(t, 3),
        "gross_mean_r": round(float(gross.mean()), 4), "net_mean_r_stress070": round(float(nr_stress.mean()), 4),
        "control_r": round(control, 4), "excess_r": round(mean - control, 4), "n_twins": len(tw),
        "stop_rate": round(float(df["st"].mean()), 3), "median_risk_usd": round(float(df["stop"].median()), 3),
        "long_share": round(float((df["side"] > 0).mean()), 3), "by_year": by_year,
    }
    if c.phase == "explore":
        res["pass"] = bool(n >= 100 and mean > 0 and t >= 2.0 and (mean - control) >= 0.05)
    else:
        dsr = DsrInputs.of(nr).dsr(K_TOTAL)
        yrs_pos = sum(1 for y in range(2018, 2026) if y in by_year and by_year[y][1] > 0)
        res.update({"dsr_k3229": round(dsr, 4), "dsr_k3500": round(DsrInputs.of(nr).dsr(3500), 4),
                    "years_positive": yrs_pos,
                    "pass": bool(mean > 0 and t >= 2.0 and dsr >= 0.5 and yrs_pos >= 6)})
    return res


_CTX: Ctx | None = None


def _worker(args: tuple[str, list[str]]) -> list[dict[str, Any]]:
    phase, ids = args
    global _CTX
    if _CTX is None or _CTX.phase != phase:
        _CTX = Ctx(phase)
    st = strategies(_CTX)
    out = []
    for sid in ids:
        r = run_one(_CTX, f"S200-MACRO-{sid}", st[sid]())
        print(json.dumps({k: r[k] for k in r if k != "by_year"}), flush=True)
        out.append(r)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["explore", "confirm"], required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--explore", type=Path, help="explore JSON (confirm only)")
    ap.add_argument("--ids", default="")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    assert sha256(MACRO) == MACRO_SHA, "macro file is not the pinned macro-v1"
    all_ids = [f"{i:02d}" for i in range(1, 34)]
    ids = [x.strip() for x in a.ids.split(",") if x.strip()] or all_ids
    if a.phase == "confirm":
        assert a.explore is not None, "confirm needs the explore JSON"
        ex = json.loads(a.explore.read_text())
        passers = {r["id"].split("-")[-1] for r in ex["results"] if r.get("pass")}
        bad = [i for i in ids if i not in passers]
        if a.ids:
            assert not bad, f"refused: {bad} did not pass explore"
        ids = [i for i in ids if i in passers]
    w = max(1, min(a.workers, 2))
    chunks = [ids[k::w] for k in range(w)]
    with Pool(w) as p:
        res = [r for part in p.map(_worker, [(a.phase, ch) for ch in chunks if ch]) for r in part]
    res.sort(key=lambda r: r["id"])
    out = {"phase": a.phase, "window": [str(x) for x in WINDOWS[a.phase]], "cost_version": COST_VERSION,
           "broker": BROKER, "k_total": K_TOTAL, "calendar_sha256": sha256(CAL), "macro_sha256": MACRO_SHA,
           "results": res}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
