"""Sprint-200 lane `patterns`: 34 candle/bar patterns, each paired with a managed exit, screened against a matched
random-entry control under the same exit (docs/research/sprint200/patterns-prereg.md, committed before this code).

Usage:
  uv run python scripts/sprint200/patterns_screen.py explore [--raw raw] [--workers 2] [--only S200-PATTERNS-01,...]
  uv run python scripts/sprint200/patterns_screen.py confirm [--raw raw] [--workers 2]
      (confirm runs ONLY the explore-passers recorded in the explore output; it refuses anything else)

Outputs: ~/tmp/sprint200_patterns/<mode>.json (aggregates) and <mode>__<id>.parquet (per-trade rows, never committed).
Nothing at or after 2025-10-01 is read: the loader cuts and asserts. Explore cuts at 2018-01-01 (no confirm bar is read).

Every walk works on a long-only view: a short is the same walk on negated prices (high <-> -low)."""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.timeutil import epoch_ns  # noqa: E402
from goldbot.labels import SwapSpec, rollover_nights  # noqa: E402
from goldbot.research import arms as A  # noqa: E402
from goldbot.research.metrics import DsrInputs  # noqa: E402

COST_VERSION = "cor-2026-10-31.2"
BROKER = "icm"
SEED = 20261102
N_CTRL = 4
K_TOTAL = 3_029 + 6 * 34          # prereg section 5: upper bound on the program's K after the sprint
MIN_NS = 60_000_000_000
DAY_NS = 86_400 * 10**9
OUT = Path.home() / "tmp" / "sprint200_patterns"
WINDOWS = {
    "explore": (pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC"), 2010, 2017),
    "confirm": (pd.Timestamp("2018-01-01", tz="UTC"), pd.Timestamp("2025-10-01", tz="UTC"), 2017, 2025),
}
TF_MIN = {"15m": 15, "1h": 60, "4h": 240}


# ---------------------------------------------------------------- strategies (prereg section 4)
@dataclass(frozen=True)
class Strat:
    id: str
    pattern: str
    context: str
    tf: str
    stop: str          # "ST" or "A<k>"
    exit: str          # CH3 | AT2 | BE2 | BE3 | PT | FT<x> | TS | VT2 | MR
    hold: int


def _s(n: int, pattern: str, context: str, tf: str, stop: str, exit_: str, hold: int) -> Strat:
    return Strat(f"S200-PATTERNS-{n:02d}", pattern, context, tf, stop, exit_, hold)


STRATS: tuple[Strat, ...] = (
    _s(1, "ENG", "trend", "1h", "ST", "CH3", 48),
    _s(2, "ENG", "trend", "1h", "ST", "BE2", 24),
    _s(3, "ENG", "pdlevel", "1h", "ST", "FT2", 24),
    _s(4, "ENG", "trend", "4h", "ST", "CH3", 30),
    _s(5, "ENG", "trend_lo_nyo", "15m", "A1.0", "TS", 8),
    _s(6, "PIN", "trend_ema20", "1h", "ST", "CH3", 48),
    _s(7, "PIN", "pdlevel", "1h", "ST", "BE2", 24),
    _s(8, "PIN", "trend", "4h", "ST", "PT", 30),
    _s(9, "PIN", "lo_nyo", "15m", "A1.0", "FT1.5", 16),
    _s(10, "PIN", "stretch", "1h", "ST", "MR", 12),
    _s(11, "IBB", "trend", "1h", "ST", "CH3", 48),
    _s(12, "IBB", "trend", "4h", "ST", "PT", 30),
    _s(13, "IBB", "lo", "15m", "ST", "FT2", 16),
    _s(14, "FAKEY", "any", "1h", "ST", "FT2", 24),
    _s(15, "OB", "trend", "1h", "ST", "CH3", 48),
    _s(16, "OB", "pdlevel", "1h", "ST", "BE2", 24),
    _s(17, "OB", "trend", "4h", "A1.5", "TS", 6),
    _s(18, "R3", "pdlevel", "1h", "ST", "BE3", 24),
    _s(19, "R3", "trend", "1h", "ST", "CH3", 48),
    _s(20, "R3", "trend", "4h", "ST", "PT", 30),
    _s(21, "R3", "nyo", "15m", "A1.0", "TS", 8),
    _s(22, "MARU", "any", "1h", "A1.0", "CH3", 48),
    _s(23, "MARU", "any", "1h", "A1.5", "TS", 4),
    _s(24, "MARU", "trend", "4h", "A1.5", "PT", 30),
    _s(25, "MARU", "lo", "15m", "A1.0", "AT2", 16),
    _s(26, "MARU-PD", "any", "1h", "A1.0", "CH3", 48),
    _s(27, "MARU-F", "any", "1h", "A1.0", "FT1", 4),
    _s(28, "3S", "trend", "1h", "A1.5", "CH3", 48),
    _s(29, "3S", "any", "4h", "A1.5", "TS", 12),
    _s(30, "NRB7", "trend", "1h", "ST", "CH3", 48),
    _s(31, "NRB4", "trend", "4h", "ST", "BE3", 30),
    _s(32, "PIN>ENG", "any", "1h", "ST", "CH3", 48),
    _s(33, "ENG", "daily_pullback", "1h", "ST", "VT2", 24),
    _s(34, "PIN", "pwlevel", "4h", "ST", "CH3", 30),
)
BY_ID = {s.id: s for s in STRATS}


# ---------------------------------------------------------------- data
@dataclass
class M1:
    ts: np.ndarray                       # int64 ns, 1m bar open
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray


def load_m1(raw: Path, y0: int, y1: int, cut: pd.Timestamp) -> M1:
    """1m mid bars for y0..y1 (error-flagged bars dropped by read_bar_files), every row before `cut` (asserted), and
    never a row at or after the holdout (2025-10-01)."""
    assert cut <= A.HOLDOUT_FROM
    b = read_bar_files(raw, y0, y1)
    ts = epoch_ns(pd.DatetimeIndex(b["ts_utc"]))
    keep = ts < cut.value
    b, ts = b.loc[keep].reset_index(drop=True), ts[keep]
    assert len(ts) and ts.max() < cut.value, "a bar at or after the cut was read"
    A.assert_no_holdout_rows(b)
    mid = {k: ((b[f"bid_{k}"].to_numpy(float) + b[f"ask_{k}"].to_numpy(float)) / 2) for k in ("open", "high", "low", "close")}
    return M1(ts, mid["open"], mid["high"], mid["low"], mid["close"])


@dataclass
class Frame:
    """TF bars in one orientation (long view; the short view negates prices) with point-in-time context."""
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    atr: np.ndarray                      # ATR(14) over bars i-14..i-1
    e20: np.ndarray
    e50: np.ndarray
    e200: np.ndarray
    pdh: np.ndarray
    pdl: np.ndarray
    pwh: np.ndarray
    pwl: np.ndarray
    dclose: np.ndarray                   # previous complete UTC day's close
    dema: np.ndarray                     # EMA20 of daily closes through that day
    open_ns: np.ndarray
    close_ns: np.ndarray
    extra: dict[str, np.ndarray] = field(default_factory=dict)

    def neg(self) -> Frame:
        return Frame(-self.o, -self.l, -self.h, -self.c, self.atr, -self.e20, -self.e50, -self.e200, -self.pdl,
                     -self.pdh, -self.pwl, -self.pwh, -self.dclose, -self.dema, self.open_ns, self.close_ns)


def _ema(x: pd.Series, span: int) -> np.ndarray:
    return x.ewm(span=span, adjust=False).mean().to_numpy(float)


def daily_levels(m: M1) -> pd.DataFrame:
    """Per UTC date with >= 360 1m bars (a complete day; Sunday stubs are not levels): high, low, close, EMA20(close)."""
    f = pd.DataFrame({"h": m.h, "l": m.l, "c": m.c, "day": m.ts // DAY_NS})
    g = f.groupby("day").agg(h=("h", "max"), l=("l", "min"), c=("c", "last"), n=("c", "size"))
    g = g[g["n"] >= 360].copy()
    g["ema"] = _ema(g["c"], 20)
    return g


def weekly_levels(m: M1) -> pd.DataFrame:
    """Per Mon-Sun UTC week (key = days since epoch of its Monday): high and low."""
    day = m.ts // DAY_NS
    monday = day - ((day + 3) % 7)                       # 1970-01-01 was a Thursday
    f = pd.DataFrame({"h": m.h, "l": m.l, "w": monday})
    return f.groupby("w").agg(h=("h", "max"), l=("l", "min"))


def build_frame(m: M1, tf: str, dl: pd.DataFrame, wl: pd.DataFrame) -> Frame:
    minutes = TF_MIN[tf]
    f = pd.DataFrame({"o": m.o, "h": m.h, "l": m.l, "c": m.c}, index=pd.DatetimeIndex(m.ts, tz="UTC"))
    q = f.resample(f"{minutes}min", label="left", closed="left").agg(
        {"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    pc = q["c"].shift(1)
    tr = pd.concat([q["h"] - q["l"], (q["h"] - pc).abs(), (q["l"] - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().shift(1).to_numpy(float)          # bars i-14..i-1
    open_ns = epoch_ns(pd.DatetimeIndex(q.index))
    close_ns = open_ns + minutes * MIN_NS
    # previous complete day relative to the UTC day of the bar's open (days strictly before it)
    bday = open_ns // DAY_NS
    days = dl.index.to_numpy(np.int64)
    k = np.searchsorted(days, bday, side="left") - 1
    ok = k >= 0
    kk = np.clip(k, 0, None)

    def pick(col: str, frame: pd.DataFrame, idx: np.ndarray, valid: np.ndarray) -> np.ndarray:
        v = frame[col].to_numpy(float)[idx]
        return np.where(valid, v, np.nan)
    bweek = bday - ((bday + 3) % 7)
    weeks = wl.index.to_numpy(np.int64)
    kw = np.searchsorted(weeks, bweek, side="left") - 1
    okw = kw >= 0
    kwk = np.clip(kw, 0, None)
    c = q["c"]
    return Frame(q["o"].to_numpy(float), q["h"].to_numpy(float), q["l"].to_numpy(float), c.to_numpy(float), atr,
                 _ema(c, 20), _ema(c, 50), _ema(c, 200), pick("h", dl, kk, ok), pick("l", dl, kk, ok),
                 pick("h", wl, kwk, okw), pick("l", wl, kwk, okw), pick("c", dl, kk, ok), pick("ema", dl, kk, ok),
                 open_ns, close_ns)


# ---------------------------------------------------------------- patterns (long view; prereg section 2)
def sh(x: np.ndarray, k: int) -> np.ndarray:
    """x shifted k bars back (x[i-k] at i); NaN for the first k."""
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:len(x) - k]
    return out


def candle(F: Frame) -> dict[str, np.ndarray]:
    o, h, l, c = F.o, F.h, F.l, F.c  # noqa: E741
    rng = h - l
    body = np.abs(c - o)
    return {"rng": rng, "body": body, "uw": h - np.maximum(o, c), "lw": np.minimum(o, c) - l}


def is_pin(F: Frame, p: dict[str, np.ndarray]) -> np.ndarray:
    return (p["lw"] >= 0.6 * p["rng"]) & (p["body"] <= 0.3 * p["rng"]) & (p["rng"] >= 0.8 * F.atr) & (p["rng"] > 0)


def is_eng(F: Frame, p: dict[str, np.ndarray]) -> np.ndarray:
    o1, c1 = sh(F.o, 1), sh(F.c, 1)
    return ((c1 < o1) & (F.c > F.o) & (F.o <= c1) & (F.c >= o1) & (p["body"] > np.abs(c1 - o1))
            & (p["rng"] >= 0.5 * F.atr))


def is_maru(F: Frame, p: dict[str, np.ndarray], min_rng: float) -> np.ndarray:
    return (F.c > F.o) & (p["body"] >= 0.8 * p["rng"]) & (p["rng"] >= min_rng * F.atr)


def pattern(F: Frame, s: Strat) -> tuple[np.ndarray, np.ndarray]:
    """(signal mask, structure stop level) for the long view."""
    p = candle(F)
    o, h, l, c, a = F.o, F.h, F.l, F.c, F.atr  # noqa: E741
    l1, l2, h1, h2 = sh(l, 1), sh(l, 2), sh(h, 1), sh(h, 2)
    nan = np.full(len(c), np.nan)
    name = s.pattern
    if name == "ENG":
        return is_eng(F, p), np.fmin(l, l1) - 0.1 * a
    if name == "PIN":
        return is_pin(F, p), l - 0.1 * a
    if name == "IBB":
        inside = (h1 <= h2) & (l1 >= l2)
        return inside & (c > h2), l2 - 0.1 * a
    if name == "FAKEY":
        inside = (h1 <= h2) & (l1 >= l2)
        return inside & (l < l2) & (c > l2) & (c < h2) & (c > o), l - 0.1 * a
    if name == "OB":
        return (h > h1) & (l < l1) & (c >= l + 0.75 * p["rng"]) & (p["rng"] >= 1.0 * a), np.fmin(l, l1) - 0.1 * a
    if name == "R3":
        o2, c2 = sh(o, 2), sh(c, 2)
        return (c2 < o2) & (l1 < l2) & (c > h1) & (c > o), np.fmin(np.fmin(l, l1), l2) - 0.1 * a
    if name == "MARU":
        return is_maru(F, p, 1.5), nan
    if name == "MARU-PD":
        return is_maru(F, p, 1.5) & (c > F.pdh) & (sh(c, 1) <= F.pdh), nan
    if name == "MARU-F":        # a BEAR marubozu >= 2.5 A in this view -> long (the fade)
        bear = (c < o) & (p["body"] >= 0.8 * p["rng"]) & (p["rng"] >= 2.5 * a)
        return bear, nan
    if name == "3S":
        bull = (c > o) & (p["body"] >= 0.5 * p["rng"]) & (c >= h - 0.3 * p["rng"]) & (p["rng"] > 0)
        b1, b2 = sh(bull.astype(float), 1) == 1, sh(bull.astype(float), 2) == 1
        return bull & b1 & b2 & (c > sh(c, 1)) & (sh(c, 1) > sh(c, 2)), nan
    if name in ("NRB7", "NRB4"):
        n = 7 if name == "NRB7" else 4
        rng1 = sh(p["rng"], 1)
        minrng = pd.Series(p["rng"]).rolling(n).min().shift(1).to_numpy(float)    # min over bars i-n..i-1
        inside = (h1 <= h2) & (l1 >= l2)
        return inside & (rng1 <= minrng) & (c > h1), l1 - 0.1 * a
    if name == "PIN>ENG":
        pin1 = sh(is_pin(F, p).astype(float), 1) == 1
        return pin1 & is_eng(F, p), np.fmin(l, l1) - 0.1 * a
    raise ValueError(name)


def context(F: Frame, s: Strat) -> np.ndarray:
    tu = (F.e50 > F.e200) & (F.c > F.e50)
    mod = (F.close_ns // MIN_NS) % 1440
    lo = (mod >= 7 * 60) & (mod < 10 * 60)
    nyo = (mod >= 12 * 60 + 30) & (mod < 15 * 60 + 30)
    ctx = s.context
    if ctx == "any":
        return np.ones(len(F.c), bool)
    if ctx == "trend":
        return tu
    if ctx == "trend_lo_nyo":
        return tu & (lo | nyo)
    if ctx == "lo_nyo":
        return lo | nyo
    if ctx == "lo":
        return lo
    if ctx == "nyo":
        return nyo
    if ctx == "trend_ema20":
        return tu & (F.l <= F.e20) & (F.e20 < F.c)
    if ctx == "pdlevel":
        return (F.l <= F.pdl + 0.25 * F.atr) & (F.c > F.pdl)
    if ctx == "pwlevel":
        return (F.l <= F.pwl + 0.25 * F.atr) & (F.c > F.pwl)
    if ctx == "stretch":
        return F.c < F.e20 - 2.5 * F.atr
    if ctx == "daily_pullback":
        return (F.dclose > F.dema) & (sh(F.c, 1) < sh(F.e20, 1))
    raise ValueError(ctx)


def signals(F: Frame, s: Strat) -> pd.DataFrame:
    """Long-view candidates: bar index, stop distance d, target distance (NaN = none)."""
    mask, level = pattern(F, s)
    mask = mask & context(F, s) & np.isfinite(F.atr) & (F.atr > 0)
    if s.stop == "ST":
        d = F.c - level
    else:
        d = float(s.stop[1:]) * F.atr
    tgt = np.full(len(F.c), np.nan)
    ex = s.exit
    if ex.startswith("FT"):
        tgt = float(ex[2:]) * d
    elif ex.startswith("BE"):
        tgt = float(ex[2:]) * d
    elif ex == "VT2":
        tgt = 2.0 * F.atr
    elif ex == "MR":
        tgt = F.e20 - F.c
        mask = mask & (tgt >= 0.5 * d)
    ok = mask & np.isfinite(d) & (d >= 0.3 * F.atr) & (d <= 3.0 * F.atr) & (d >= 1.0)
    idx = np.flatnonzero(ok)
    return pd.DataFrame({"i": idx, "d": d[idx], "tgt": tgt[idx], "atr": F.atr[idx]})


# ---------------------------------------------------------------- the 1m walk (prereg section 3)
@dataclass
class Leg:
    move: float          # oriented price move per oz (exit - entry), before swap and costs
    stop: bool
    exit_ts: int         # ns, end of the exit minute
    kind: str


def walk(m: tuple[np.ndarray, ...], k0: int, k1: int, d: float, atr: float, tgt: float, ex: str,
         tf_close: np.ndarray, tf_h: np.ndarray, tf_c: np.ndarray) -> tuple[list[tuple[float, Leg]], int]:
    """One trade on the oriented 1m arrays m = (ts, o, h, l, c) over minutes k0..k1-1 (k1-1 = the time-exit minute).
    tf_close/tf_h/tf_c: closes, oriented highs and closes of TF bars i+1..i+H-1 (trail updates). Returns [(weight,
    leg)] and the last exit minute index."""
    ts, o, h, l, c = (x[k0:k1] for x in m)  # noqa: E741
    n = k1 - k0
    entry = float(o[0])
    init = entry - d
    trail = None
    if ex in ("CH3", "PT"):
        trail = np.maximum.accumulate(tf_h - 3.0 * atr) if len(tf_h) else np.zeros(0)
    elif ex == "AT2":
        trail = np.maximum.accumulate(tf_c - 2.0 * atr) if len(tf_c) else np.zeros(0)
    if trail is not None:
        seq = np.concatenate([[init], np.maximum(init, trail)])
        seg = np.searchsorted(tf_close, ts, side="right")
        trail_min = seq[seg]
    else:
        trail_min = np.full(n, init)
    one_r = np.flatnonzero(h >= entry + d)
    trig = int(one_r[0]) if len(one_r) else n

    def run(stop_min: np.ndarray, target: float | None) -> tuple[Leg, int]:
        hs = np.flatnonzero(l <= stop_min)
        js = int(hs[0]) if len(hs) else n
        jt = n
        if target is not None:
            ht = np.flatnonzero(h >= entry + target)
            jt = int(ht[0]) if len(ht) else n
        if js < n and js <= jt:
            px = min(float(stop_min[js]), float(o[js]))
            return Leg(px - entry, True, int(ts[js]) + MIN_NS, "stop"), js
        if jt < n:
            return Leg(float(target), False, int(ts[jt]) + MIN_NS, "target"), jt  # type: ignore[arg-type]
        return Leg(float(c[n - 1]) - entry, False, int(ts[n - 1]) + MIN_NS, "time"), n - 1

    if ex == "PT":
        leg_a, ja = run(np.full(n, init), d)
        sb = np.full(n, init)
        if trig < n - 1:
            sb[trig + 1:] = np.maximum(trail_min[trig + 1:], entry)
        leg_b, jb = run(sb, None)
        return [(0.5, leg_a), (0.5, leg_b)], max(ja, jb)
    stop_min = trail_min.copy()
    if ex.startswith("BE") and trig < n - 1:
        stop_min[trig + 1:] = np.maximum(stop_min[trig + 1:], entry)
    target = None if not np.isfinite(tgt) else float(tgt)
    leg, j = run(stop_min, target)
    return [(1.0, leg)], j


# ---------------------------------------------------------------- one strategy
def _u(*key: object) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:8], "big")


@dataclass
class Ctx:
    m1: M1
    frames: dict[str, Frame]
    labels: dict[str, np.ndarray]        # TF close session label (DEFAULT_SESSIONS.session_label)
    lo: int                              # decision window [lo, hi) ns
    hi: int


CTX: Ctx | None = None


def oriented(m1: M1, side: int) -> tuple[np.ndarray, ...]:
    if side > 0:
        return (m1.ts, m1.o, m1.h, m1.l, m1.c)
    return (m1.ts, -m1.o, -m1.l, -m1.h, -m1.c)


def trade_at(ctx: Ctx, F: Frame, s: Strat, side: int, i: int, d: float, atr: float, tgt: float,
             m: tuple[np.ndarray, ...]) -> dict[str, Any] | None:
    """Walk one entry decided at the close of TF bar i; None when censored (bar i+H missing or no 1m path)."""
    if i + s.hold >= len(F.c):
        return None
    k0 = int(np.searchsorted(ctx.m1.ts, F.close_ns[i], side="left"))
    k1 = int(np.searchsorted(ctx.m1.ts, F.close_ns[i + s.hold], side="left"))
    if k1 - k0 < 1 or k0 >= len(ctx.m1.ts):
        return None
    tf_close = F.close_ns[i + 1:i + s.hold]
    hh = F.h if side > 0 else -F.l
    cc = F.c if side > 0 else -F.c
    legs, j = walk(m, k0, k1, d, atr, tgt, s.exit, tf_close, hh[i + 1:i + s.hold], cc[i + 1:i + s.hold])
    return {"i": i, "side": side, "d": d, "atr": atr, "tgt": tgt, "entry_ns": int(ctx.m1.ts[k0]),
            "exit_ns": max(lg.exit_ts for _, lg in legs), "legs": legs}


def price(rows: list[dict[str, Any]], costs: A.ArmCosts, swap: SwapSpec) -> pd.DataFrame:
    """Net R per trade at the pinned cost of record (screen_net_r), swap per rollover night per leg."""
    if not rows:
        return pd.DataFrame()
    side = np.array([r["side"] for r in rows])
    d = np.array([r["d"] for r in rows], float)
    ent = pd.DatetimeIndex(pd.to_datetime([r["entry_ns"] for r in rows], utc=True))
    rate = swap.usd_per_oz(side)
    move = np.zeros(len(rows))
    stopw = np.zeros(len(rows))
    kinds = []
    nleg = max(len(r["legs"]) for r in rows)
    for li in range(nleg):
        w = np.array([r["legs"][li][0] if li < len(r["legs"]) else 0.0 for r in rows])
        mv = np.array([r["legs"][li][1].move if li < len(r["legs"]) else 0.0 for r in rows])
        st = np.array([r["legs"][li][1].stop if li < len(r["legs"]) else False for r in rows], float)
        xt = pd.DatetimeIndex(pd.to_datetime([r["legs"][li][1].exit_ts if li < len(r["legs"]) else r["entry_ns"]
                                              for r in rows], utc=True))
        nights = rollover_nights(ent, xt, swap.server_tz, swap.triple_weekday)
        move += w * (mv + nights * rate)
        stopw += w * st
    for r in rows:
        kinds.append("+".join(lg.kind for _, lg in r["legs"]))
    sess = np.where(DEFAULT_SESSIONS.is_open(ent), DEFAULT_SESSIONS.session_label(ent), "")
    net = A.screen_net_r(move, d, stopw, costs, broker=BROKER, sessions=sess, version=COST_VERSION)
    stress = A.screen_net_r(move, d, stopw, costs, broker=BROKER, sessions=sess, version=COST_VERSION,
                            overshoot_usd=A.OVERSHOOT_STRESS_USD)
    return pd.DataFrame({"i": [r["i"] for r in rows], "side": side, "d": d, "entry_utc": ent,
                         "exit_utc": pd.to_datetime([r["exit_ns"] for r in rows], utc=True),
                         "gross_r": move / d, "net_r": net, "net_r_stress": stress, "stop_w": stopw, "kind": kinds,
                         "session": sess})


def tstat(r: np.ndarray) -> float:
    if len(r) < 2:
        return float("nan")
    sd = float(np.std(r, ddof=1))
    return float(np.mean(r) / sd * np.sqrt(len(r))) if sd > 0 else float("nan")


def run_strategy(sid: str) -> dict[str, Any]:
    ctx = CTX
    assert ctx is not None
    s = BY_ID[sid]
    costs = A.load_costs(ROOT / "config" / "arm_costs.yaml")
    costs.require_version(COST_VERSION)
    swap = SwapSpec.shipped()
    Fl = ctx.frames[s.tf]
    lab = ctx.labels[s.tf]
    cands = []
    for side, F in ((1, Fl), (-1, Fl.neg())):
        sg = signals(F, s)
        sg["side"] = side
        cands.append(sg)
    cd = pd.concat(cands, ignore_index=True)
    cd = cd[(Fl.close_ns[cd["i"].to_numpy()] >= ctx.lo) & (Fl.close_ns[cd["i"].to_numpy()] < ctx.hi)]
    both = cd["i"].duplicated(keep=False)                  # long and short on one bar: neither
    cd = cd[~both].sort_values("i").reset_index(drop=True)
    n_cands = len(cd)
    m_long, m_short = oriented(ctx.m1, 1), oriented(ctx.m1, -1)
    rows: list[dict[str, Any]] = []
    busy_until = -1
    for i, d, tgt, atr, side in cd[["i", "d", "tgt", "atr", "side"]].itertuples(index=False):
        i = int(i)
        if Fl.close_ns[i] < busy_until:
            continue
        r = trade_at(ctx, Fl, s, int(side), i, float(d), float(atr), float(tgt), m_long if side > 0 else m_short)
        if r is None:
            continue
        rows.append(r)
        busy_until = r["exit_ns"]
    trades = price(rows, costs, swap)
    # matched random control: same UTC day x session label of the decision close, same side/d/atr/target/exit
    day = Fl.close_ns // DAY_NS
    kl: dict[tuple[int, str], list[int]] = {}
    dl_: dict[int, list[int]] = {}
    for b, (dd, ll) in enumerate(zip(day.tolist(), lab.tolist(), strict=True)):
        kl.setdefault((int(dd), str(ll)), []).append(b)
        dl_.setdefault(int(dd), []).append(b)
    keyed = {k: np.asarray(v, dtype=int) for k, v in kl.items()}
    dayed = {k: np.asarray(v, dtype=int) for k, v in dl_.items()}
    rng = np.random.default_rng(_u(SEED, sid) % 2**63)
    crow: list[dict[str, Any]] = []
    for r in rows:
        i = r["i"]
        pool = keyed.get((int(day[i]), str(lab[i])), np.zeros(0, int))
        pool = pool[pool != i]
        if not len(pool):
            pool = dayed.get(int(day[i]), np.zeros(0, int))
            pool = pool[pool != i]
        if not len(pool):
            continue
        for _ in range(N_CTRL):
            j = int(pool[rng.integers(len(pool))])
            cr = trade_at(ctx, Fl, s, r["side"], j, r["d"], r["atr"], r["tgt"], m_long if r["side"] > 0 else m_short)
            if cr is None:
                continue
            crow.append(cr)
    ctrl = price(crow, costs, swap)
    return summarize(s, trades, ctrl, n_cands)


def summarize(s: Strat, tr: pd.DataFrame, ctrl: pd.DataFrame, n_cands: int) -> dict[str, Any]:
    out: dict[str, Any] = {"id": s.id, "pattern": s.pattern, "context": s.context, "tf": s.tf, "stop": s.stop,
                           "exit": s.exit, "hold": s.hold, "n_candidates": n_cands, "n": len(tr)}
    if len(tr):
        r = tr["net_r"].to_numpy(float)
        years = tr.groupby(tr["entry_utc"].dt.year)["net_r"].agg(["mean", "size"])
        out.update({
            "net_mean": float(r.mean()), "net_t": tstat(r), "gross_mean": float(tr["gross_r"].mean()),
            "gross_t": tstat(tr["gross_r"].to_numpy(float)), "stress_mean": float(tr["net_r_stress"].mean()),
            "win_rate": float((r > 0).mean()), "median_d": float(tr["d"].median()),
            "long_n": int((tr["side"] > 0).sum()), "long_mean": float(tr.loc[tr["side"] > 0, "net_r"].mean())
            if (tr["side"] > 0).any() else None,
            "short_mean": float(tr.loc[tr["side"] < 0, "net_r"].mean()) if (tr["side"] < 0).any() else None,
            "exit_kinds": {str(k): float(v) for k, v in tr["kind"].value_counts(normalize=True).items()},
            "years": {int(str(y)): {"mean": float(v["mean"]), "n": int(v["size"])} for y, v in years.iterrows()},
            "years_pos": int((years["mean"] > 0).sum()), "n_years": int(len(years)),
        })
        if len(r) >= 3:
            di = DsrInputs.of(r)
            out["dsr_inputs"] = di.model_dump()
            out["dsr_prob_at_K"] = di.dsr(K_TOTAL)
            out["K"] = K_TOTAL
    if len(ctrl):
        cr = ctrl["net_r"].to_numpy(float)
        out.update({"ctrl_n": len(ctrl), "ctrl_mean": float(cr.mean()), "ctrl_gross_mean": float(ctrl["gross_r"].mean())})
        if len(tr):
            out["excess_vs_ctrl"] = out["net_mean"] - out["ctrl_mean"]
    out["_trades"] = tr
    return out


def explore_pass(o: dict[str, Any]) -> bool:
    return bool(o.get("n", 0) >= 100 and o.get("net_mean", -1) > 0 and o.get("net_t", 0) >= 2.0
                and o.get("excess_vs_ctrl", -1) >= 0.05)


def confirm_pass(o: dict[str, Any]) -> bool:
    return bool(o.get("n", 0) >= 1 and o.get("net_mean", -1) > 0 and o.get("net_t", 0) >= 2.0
                and o.get("dsr_prob_at_K", 0) > 0.5 and o.get("years_pos", 0) >= 6)


# ---------------------------------------------------------------- main
def init_ctx(raw: Path, mode: str) -> Ctx:
    lo, hi, y0, y1 = WINDOWS[mode]
    m1 = load_m1(raw, y0, y1, hi)
    dl, wl = daily_levels(m1), weekly_levels(m1)
    frames, labels = {}, {}
    for tf in sorted({s.tf for s in STRATS}):
        F = build_frame(m1, tf, dl, wl)
        frames[tf] = F
        labels[tf] = np.asarray(DEFAULT_SESSIONS.session_label(pd.DatetimeIndex(pd.to_datetime(F.close_ns, utc=True))),
                                dtype=str)
    return Ctx(m1, frames, labels, lo.value, hi.value)


def main() -> None:
    global CTX
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("explore", "confirm"))
    ap.add_argument("--raw", default=str(ROOT.parents[2] / "raw") if (ROOT.parents[2] / "raw").exists() else "raw")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    assert args.workers <= 2, "brief rule 6: at most 2 worker processes"
    OUT.mkdir(parents=True, exist_ok=True)
    if args.mode == "confirm":
        ex = json.loads((OUT / "explore.json").read_text())
        ids = [o["id"] for o in ex["results"] if o["explore_pass"]]
        if args.only:
            raise SystemExit("confirm runs exactly the explore-passers; --only is refused")
    else:
        ids = [s.id for s in STRATS] if not args.only else args.only.split(",")
    CTX = init_ctx(Path(args.raw), args.mode)
    if ids:
        with mp.get_context("fork").Pool(args.workers) as pool:
            res = pool.map(run_strategy, ids)
    else:
        res = []
    results = []
    for o in res:
        tr = o.pop("_trades")
        if len(tr):
            tr.to_parquet(OUT / f"{args.mode}__{o['id']}.parquet", index=False)
        o["explore_pass" if args.mode == "explore" else "confirm_pass"] = (
            explore_pass(o) if args.mode == "explore" else confirm_pass(o))
        results.append(o)
    lo, hi, _, _ = WINDOWS[args.mode]
    doc = {"mode": args.mode, "window": [str(lo.date()), str(hi.date())], "cost_of_record": COST_VERSION,
           "broker": BROKER, "seed": SEED, "n_ctrl_draws": N_CTRL, "K": K_TOTAL,
           "bars_to": str(pd.Timestamp(int(CTX.m1.ts.max()), tz="UTC")), "results": results}
    (OUT / f"{args.mode}.json").write_text(json.dumps(doc, indent=1, default=float))
    for o in results:
        flag = o.get("explore_pass", o.get("confirm_pass"))
        print(f"{o['id']} {o['pattern']:8s} {o['tf']:4s} {o['exit']:5s} n={o['n']:5d} "
              f"net={o.get('net_mean', float('nan')):+.3f} t={o.get('net_t', float('nan')):+.2f} "
              f"ctrl={o.get('ctrl_mean', float('nan')):+.3f} ex={o.get('excess_vs_ctrl', float('nan')):+.3f} "
              f"yrs+={o.get('years_pos')}/{o.get('n_years')} pass={flag}")


if __name__ == "__main__":
    main()
