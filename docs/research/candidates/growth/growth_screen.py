"""Growth screens C1, C2, C4 (prereg-growth-2026-11-01.md; parent growth-sprint-2026-11-01.md section 2).
2010-2017 only, local bars, nothing written to the repo: outputs go to ~/tmp/growth_screens.

Usage: .venv/bin/python docs/research/candidates/growth/growth_screen.py --raw <raw folder> --cal <us_calendar.parquet>

Every simulator works on a long-only view: a short is the same walk on negated prices (high <-> -low)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from goldbot.data.timeutil import epoch_ns  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
START, END = pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC")
COST_VERSION = "cor-2026-10-31.2"
COST_MULT = 1.5
SWAP_LONG = 0.60          # $/oz per 22:00 UTC rollover held (settings swap_long_usd_per_lot -60), triple Wednesday
N_TWIN = 4
N_BOOT = 10_000
SEED = 20261101
K_HOLM = 7
MIN = 60_000_000_000      # one minute in ns
OUT = Path.home() / "tmp" / "growth_screens"
# Intrabar bracket (disclosed, not a verdict): False = the frozen conservative order (adds before the stop in a minute;
# stop-entry stops checked from the fill minute). True = the optimistic bound (stop before adds; stops from the next
# minute). Set by --optimistic; results go to results_optimistic.json.
OPTIMISTIC = False


# ---------------------------------------------------------------- data
@dataclass
class Bars:
    ts: np.ndarray        # int64 ns, 1m bar open
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    atr_end: np.ndarray   # 15m bar end (ns)
    atr: np.ndarray       # ATR(14) of 15m mid bars ending at atr_end

    def at(self, t: int) -> int:
        """First 1m bar opening at or after t."""
        return int(np.searchsorted(self.ts, t, side="left"))

    def atr15(self, t: int) -> float:
        """ATR15 of the last 15m bar completed at or before t."""
        i = int(np.searchsorted(self.atr_end, t, side="right")) - 1
        return float(self.atr[i]) if i >= 0 else float("nan")

    def view(self, k0: int, k1: int, side: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """o, h, l, c of minutes k0..k1 inclusive, as a long would see them (negated for a short)."""
        sl = slice(k0, k1 + 1)
        if side > 0:
            return self.o[sl], self.h[sl], self.lo[sl], self.c[sl]
        return -self.o[sl], -self.lo[sl], -self.h[sl], -self.c[sl]


def load_bars(raw: Path) -> Bars:
    frames = [pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet") for y in range(2010, 2018)]
    b = pd.concat(frames, ignore_index=True)
    ts = pd.DatetimeIndex(b["ts_utc"]).as_unit("ns")
    keep = (ts >= START) & (ts < END)
    b, ts = b.loc[keep].reset_index(drop=True), ts[keep]
    assert ts.max() < END, "a 2018+ bar was read"
    m = {k: ((b[f"bid_{k}"] + b[f"ask_{k}"]) / 2).to_numpy(float) for k in ("open", "high", "low", "close")}
    t = epoch_ns(ts)
    order = np.argsort(t, kind="stable")
    t = t[order]
    uniq = np.r_[True, np.diff(t) > 0]
    o, h, lo, c = (m[k][order][uniq] for k in ("open", "high", "low", "close"))
    t = t[uniq]
    f = pd.DataFrame({"o": o, "h": h, "l": lo, "c": c}, index=pd.DatetimeIndex(t, tz="UTC"))
    q = f.resample("15min").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    pc = q["c"].shift(1)
    tr = pd.concat([q["h"] - q["l"], (q["h"] - pc).abs(), (q["l"] - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    ends = epoch_ns(pd.DatetimeIndex(q.index)) + 15 * MIN
    return Bars(t, o, h, lo, c, ends, atr.to_numpy(float))


def load_costs() -> dict[str, dict[str, float]]:
    cor = yaml.safe_load((ROOT / "config" / "cost_of_record.yaml").read_text())
    assert cor["version"] == COST_VERSION, cor["version"]
    return {s: {"rt": float(v["round_trip_usd"]), "ov": float(v["overshoot_usd"]), "spread": float(v["spread_usd"])}
            for s, v in cor["cells"]["icm"].items() if s != "all"}


def session(t: int) -> str:
    mod = (t // MIN) % 1440
    return "asia" if mod < 7 * 60 else ("london" if mod < 12 * 60 + 30 else "newyork")


def rollovers(t0: int, t1: int) -> float:
    """Long swap units for the 22:00 UTC rollovers in (t0, t1]; Wednesday's counts 3."""
    d0 = pd.Timestamp(t0, tz="UTC")
    first = d0.normalize() + pd.Timedelta(hours=22)
    if first <= d0:
        first += pd.Timedelta(days=1)
    n = 0.0
    r = first
    while r.value <= t1:
        if r.dayofweek < 5:
            n += 3.0 if r.dayofweek == 2 else 1.0
        r += pd.Timedelta(days=1)
    return n


def h_u(*key: object) -> float:
    return int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:8], "big") / 2 ** 64


# ---------------------------------------------------------------- walks (long view)
def walk_pyramid(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, entry: float, risk: float,
                 n_adds: int = 3) -> tuple[float, int, bool, int]:
    """C2: market entry at `entry`, stop entry - risk, adds at +1..+n_adds R, common stop raised to avg - risk/units on
    each add (never lowered); adds first within a minute, then the stop in force at the minute's start.
    Returns (gross pnl per oz summed over units, units, stopped, exit offset)."""
    n = len(o)
    stop = np.full(n, entry - risk)
    fills = [entry]
    cur = entry - risk
    add_at: list[int] = []
    j0 = 0
    for i in range(1, n_adds + 1):
        lvl = entry + i * risk
        hit = np.flatnonzero(h[j0:] >= lvl)
        if not len(hit):
            break
        j = j0 + int(hit[0])
        fills.append(max(lvl, o[j]))
        add_at.append(j)
        cur = max(cur, float(np.mean(fills)) - risk / len(fills))
        if j + 1 < n:
            stop[j + 1:] = cur
        j0 = j
    hit = np.flatnonzero(lo <= stop)
    if len(hit):
        js = int(hit[0])
        units = 1 + sum(1 for a in add_at if (a < js if OPTIMISTIC else a <= js))
        px = min(stop[js], o[js])
        return float(sum(px - f for f in fills[:units])), units, True, js
    return float(sum(c[-1] - f for f in fills)), len(fills), False, n - 1


def walk_trail(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, j_in: int, entry: float, stop0: float,
               trail: float) -> tuple[float, bool, int]:
    """C1: from fill minute j_in, stop = max(stop0, best high before the minute - trail); checked from the fill minute."""
    hh = h[j_in:]
    best = np.maximum.accumulate(np.r_[entry, hh[:-1]])
    best = np.maximum(best, entry)
    stop = np.maximum(stop0, best - trail)
    hit = np.flatnonzero(lo[j_in:] <= stop)
    if OPTIMISTIC:
        hit = hit[hit > 0]
    if len(hit):
        js = int(hit[0])
        return float(min(stop[js], o[j_in + js]) - entry), True, j_in + js
    return float(c[-1] - entry), False, len(o) - 1


def walk_target(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, j_in: int, entry: float, stop: float,
                tgt: float) -> tuple[float, bool, int]:
    """C4: fixed stop and target from the fill minute; a minute holding both is a stop."""
    s = np.flatnonzero(lo[j_in:] <= stop)
    t = np.flatnonzero(h[j_in:] >= tgt)
    if OPTIMISTIC:
        s, t = s[s > 0], t[t > 0]
    js = int(s[0]) if len(s) else 10 ** 9
    jt = int(t[0]) if len(t) else 10 ** 9
    if js <= jt and js < 10 ** 9:
        return float(min(stop, o[j_in + js]) - entry), True, j_in + js
    if jt < 10 ** 9:
        return float(tgt - entry), False, j_in + jt
    return float(c[-1] - entry), False, len(o) - 1


# ---------------------------------------------------------------- rules
def day_starts(b: Bars) -> np.ndarray:
    """UTC midnights (ns) of every calendar day with bars."""
    return np.unique((b.ts // (1440 * MIN)) * (1440 * MIN))


def c2_trade(b: Bars, costs: dict, d0: int, k_e: int, side: int, t_dec: int, k_end: int) -> dict | None:
    atr = b.atr15(t_dec)
    if not np.isfinite(atr) or atr <= 0 or k_e > k_end:
        return None
    risk = 1.5 * atr
    o, h, lo, c = b.view(k_e, k_end, side)
    gross, units, stopped, jx = walk_pyramid(o, h, lo, c, o[0], risk)
    cc = costs[session(int(b.ts[k_e]))]
    net = gross - units * COST_MULT * cc["rt"] - (units * COST_MULT * cc["ov"] if stopped else 0.0)
    return {"day": d0, "t_entry": int(b.ts[k_e]), "t_exit": int(b.ts[k_e + jx]), "side": side, "units": units,
            "R": net / risk, "gross_R": gross / risk, "stopped": stopped}


def run_c2(b: Bars, costs: dict, variant: str) -> tuple[list[dict], list[dict]]:
    if variant == "C2a":
        r0, r1, w0, w1, end = -60, 7 * 60, 7 * 60 + 15, 12 * 60 + 30, 16 * 60
    else:
        r0, r1, w0, w1, end = 7 * 60, 12 * 60 + 30, 12 * 60 + 45, 19 * 60, 21 * 60
    real, twins = [], []
    for d0 in day_starts(b):
        d0 = int(d0)
        ka, kb = b.at(d0 + r0 * MIN), b.at(d0 + r1 * MIN)
        k_end = b.at(d0 + end * MIN) - 1
        kw1 = b.at(d0 + w1 * MIN)
        if kb - ka < 60 or k_end <= kw1 or pd.Timestamp(d0).dayofweek >= 5:
            continue
        hi, lo_ = float(b.h[ka:kb].max()), float(b.lo[ka:kb].min())
        closes = list(range(w0, w1 + 1, 15))
        hit = None
        for m in closes:      # first 15m close beyond the range
            kc = b.at(d0 + m * MIN) - 1
            if kc < kb:
                continue
            px = b.c[kc]
            if px > hi:
                hit = (m, 1)
                break
            if px < lo_:
                hit = (m, -1)
                break
        if hit is not None:
            m, side = hit
            tr = c2_trade(b, costs, d0, b.at(d0 + m * MIN), side, d0 + m * MIN, k_end)
            if tr:
                real.append(tr)
                for i in range(N_TWIN):
                    mt = closes[int(h_u(variant, d0, i, "t") * len(closes))]
                    st = 1 if h_u(variant, d0, i, "s") < 0.5 else -1
                    tw = c2_trade(b, costs, d0, b.at(d0 + mt * MIN), st, d0 + mt * MIN, k_end)
                    if tw:
                        twins.append(tw)
    return real, twins


def trading_days(b: Bars) -> pd.DataFrame:
    """22:00 UTC -> 22:00 UTC days: first/last minute index, high, low."""
    day = (b.ts + 2 * 60 * MIN) // (1440 * MIN)
    brk = np.flatnonzero(np.r_[True, np.diff(day) > 0, True])
    rows = []
    for a, z in zip(brk[:-1], brk[1:]):
        if z - a < 300:
            continue
        rows.append((int(day[a]), a, z - 1, float(b.h[a:z].max()), float(b.lo[a:z].min())))
    return pd.DataFrame(rows, columns=["day", "k0", "k1", "hi", "lo"])


def c4_one(b: Bars, costs: dict, td: pd.DataFrame, i: int, j_mkt: int | None = None, side_mkt: int = 0
           ) -> dict | None:
    """NR7 day i -> trade on day i+1. Real: OCO at day i's high/low. Twin: market entry at minute j_mkt, side_mkt."""
    hi, lo_ = float(td.hi.iloc[i]), float(td.lo.iloc[i])
    risk = hi - lo_
    nx = td.iloc[i + 1]
    k0, k1 = int(nx.k0), int(nx.k1)
    kx = int(td.k1.iloc[min(i + 6, len(td) - 1)])     # close of the 5th trading day after the entry day
    if j_mkt is None:
        up = np.flatnonzero(b.h[k0:k1 + 1] >= hi)
        dn = np.flatnonzero(b.lo[k0:k1 + 1] <= lo_)
        ju = int(up[0]) if len(up) else 10 ** 9
        jd = int(dn[0]) if len(dn) else 10 ** 9
        if ju == jd == 10 ** 9:
            return None
        side = 1 if ju <= jd else -1
        k_in = k0 + min(ju, jd)
        lvl = hi if side > 0 else lo_
        entry_px = max(lvl, b.o[k_in]) if side > 0 else min(lvl, b.o[k_in])
    else:
        side, k_in = side_mkt, k0 + j_mkt
        entry_px = b.o[k_in]
    o, h, lo, c = b.view(k_in, kx, side)
    e = side * entry_px
    gross, stopped, jx = walk_target(o, h, lo, c, 0, e, e - risk, e + 4 * risk)
    cc = costs[session(int(b.ts[k_in]))]
    t_in, t_out = int(b.ts[k_in]), int(b.ts[k_in + jx])
    swap = SWAP_LONG * rollovers(t_in, t_out) if side > 0 else 0.0
    net = gross - COST_MULT * cc["rt"] - (COST_MULT * cc["ov"] if stopped else 0.0) - swap
    return {"day": int(td.day.iloc[i + 1]) * 1440 * MIN, "t_entry": t_in, "t_exit": t_out, "side": side, "units": 1,
            "R": net / risk, "gross_R": gross / risk, "stopped": stopped}


def run_c4(b: Bars, costs: dict) -> tuple[list[dict], list[dict]]:
    td = trading_days(b)
    rng = (td.hi - td.lo).to_numpy()
    real, twins = [], []
    for i in range(6, len(td) - 1):
        if rng[i] <= 0 or rng[i] > rng[i - 6:i].min():
            continue
        tr = c4_one(b, costs, td, i)
        if tr is None:
            continue
        real.append(tr)
        n = int(td.k1.iloc[i + 1] - td.k0.iloc[i + 1])
        for t in range(N_TWIN):
            j = int(h_u("C4", i, t, "t") * n)
            s = 1 if h_u("C4", i, t, "s") < 0.5 else -1
            tw = c4_one(b, costs, td, i, j, s)
            if tw:
                twins.append(tw)
    return real, twins


def c1_one(b: Bars, costs: dict, t_ev: int, k: float, release: bool) -> dict | None:
    """Straddle around t_ev: reference = close of the bar opening at t_ev - 2 min; OCO live T-1 .. T+30."""
    kr = b.at(t_ev - 2 * MIN)
    if kr >= len(b.ts) or b.ts[kr] != t_ev - 2 * MIN:
        return None
    ref = b.c[kr]
    atr = b.atr15(t_ev - MIN)
    if not np.isfinite(atr) or atr <= 0:
        return None
    up_l, dn_l = ref + k * atr, ref - k * atr
    k0, kw = kr + 1, b.at(t_ev + 31 * MIN) - 1
    day_end = (t_ev // (1440 * MIN)) * 1440 * MIN + 21 * 60 * MIN
    kx = b.at(min(t_ev + 240 * MIN, day_end)) - 1
    if kw <= k0 or kx < kw:
        return None
    up = np.flatnonzero(b.h[k0:kw + 1] >= up_l)
    dn = np.flatnonzero(b.lo[k0:kw + 1] <= dn_l)
    ju = int(up[0]) if len(up) else 10 ** 9
    jd = int(dn[0]) if len(dn) else 10 ** 9
    if ju == jd == 10 ** 9:
        return {"filled": False}
    side = 1 if ju <= jd else -1          # a minute touching both: long first, then stopped (conservative)
    k_in = k0 + min(ju, jd)
    o, h, lo, c = b.view(k_in, kx, side)
    lvl, opp = (up_l, dn_l) if side > 0 else (-dn_l, -up_l)
    entry = max(lvl, o[0])
    risk = 2 * k * atr
    gross, stopped, jx = walk_trail(o, h, lo, c, 0, entry, opp, atr)
    # whipsaw: the opposite level touched after the fill, before T+30
    wl = lo[: max(kw - k_in + 1, 1)]
    whip = bool((wl <= opp).any())
    cc = costs["newyork"]
    extra = 0.0
    if release:
        rel = {t_ev - MIN, t_ev, t_ev + MIN}
        extra += 3 * cc["spread"] * (int(b.ts[k_in]) in rel)
        extra += 3 * cc["spread"] * (stopped and int(b.ts[k_in + jx]) in rel)
    cs = costs[session(int(b.ts[k_in]))]
    net = gross - COST_MULT * cs["rt"] - (COST_MULT * cs["ov"] if stopped else 0.0) - extra
    return {"filled": True, "day": (t_ev // (1440 * MIN)) * 1440 * MIN, "t_entry": int(b.ts[k_in]),
            "t_exit": int(b.ts[k_in + jx]), "side": side, "units": 1, "R": net / risk, "gross_R": gross / risk,
            "stopped": stopped, "whipsaw": whip}


def run_c1(b: Bars, costs: dict, cal: pd.DataFrame, k: float, name: str) -> tuple[list[dict], list[dict], dict]:
    ev_all = cal[(cal.tier == 1) & (cal.status == "scheduled")]
    ev_all = ev_all[(ev_all.event_utc >= START) & (ev_all.event_utc < END)]
    t_all = np.sort(epoch_ns(pd.DatetimeIndex(ev_all.event_utc)))
    ev = ev_all[ev_all.event_type.isin(["nfp", "cpi", "fomc_statement"])]
    real, twins, cnt = [], [], {"events": 0, "no_bar": 0, "unfilled": 0}
    for t_ev in np.sort(epoch_ns(pd.DatetimeIndex(ev.event_utc))):
        t_ev = int(t_ev)
        cnt["events"] += 1
        tr = c1_one(b, costs, t_ev, k, release=True)
        if tr is None:
            cnt["no_bar"] += 1
            continue
        if not tr["filled"]:
            cnt["unfilled"] += 1
        tr["event"] = t_ev
        real.append(tr)
        d0 = (t_ev // (1440 * MIN)) * 1440 * MIN
        for i in range(N_TWIN):
            for attempt in range(50):
                m = 13 * 60 + int(h_u(name, t_ev, i, attempt) * 360)
                tt = d0 + m * MIN
                if np.abs(t_all - tt).min() >= 90 * MIN:
                    break
            else:
                continue
            tw = c1_one(b, costs, tt, k, release=False)
            if tw is not None:
                tw["event"] = t_ev
                twins.append(tw)
    return real, twins, cnt


# ---------------------------------------------------------------- statistics
def boot_mean_lb(day: np.ndarray, r: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    """Day-block bootstrap of the per-trade mean: (5% quantile, one-sided p of mean <= 0)."""
    u, inv = np.unique(day, return_inverse=True)
    s = np.bincount(inv, weights=r)
    n = np.bincount(inv).astype(float)
    idx = rng.integers(0, len(u), size=(N_BOOT, len(u)))
    m = s[idx].sum(1) / n[idx].sum(1)
    return float(np.quantile(m, 0.05)), float((m <= 0).mean())


def stats(real: list[dict], twins: list[dict], key: str, rng: np.random.Generator) -> dict:
    df = pd.DataFrame([t for t in real if t.get("filled", True)])
    tw = pd.DataFrame([t for t in twins if t.get("filled", True)])
    r = df.R.to_numpy()
    lb, _ = boot_mean_lb(df.day.to_numpy(), r, rng)
    # paired: per matching key, real R (0 if no fill) minus mean twin R (0 if no fill)
    allr = pd.DataFrame(real)
    allr["Rz"] = allr.R.fillna(0.0)          # an unfilled straddle (C1) has no R: it counts 0
    allt = pd.DataFrame(twins)
    allt["Rz"] = allt.R.fillna(0.0) if "R" in allt else 0.0
    rz = allr.groupby(key).Rz.sum()
    tz = allt.groupby(key).Rz.sum() / N_TWIN
    d = (rz - tz.reindex(rz.index).fillna(0.0)).to_numpy()
    idx = rng.integers(0, len(d), size=(N_BOOT, len(d)))
    dm = d[idx].mean(1)
    p_diff = float((dm <= 0).mean())
    years = pd.to_datetime(df.t_exit, utc=True).dt.year
    yr = df.groupby(years).R.mean()
    srt = np.sort(r)[::-1]
    top = srt[: max(1, int(round(0.1 * len(r))))].sum()
    weeks = 417.4
    out: dict[str, Any] = {
        "n": int(len(r)), "trades_per_week": len(r) / weeks, "mean_R": float(r.mean()), "sd_R": float(r.std(ddof=1)),
        "skew": float(np.mean(((r - r.mean()) / r.std(ddof=0)) ** 3)), "p_ge_5R": float((r >= 5).mean()), "gross_mean_R": float(df.gross_R.mean()),
        "lb90_mean": lb, "twin_mean_R": float(tw.R.mean()) if len(tw) else float("nan"),
        "diff_mean": float(d.mean()), "diff_lb90": float(np.quantile(dm, 0.05)), "p_diff": p_diff,
        "p_diff_bonf7": min(1.0, K_HOLM * p_diff), "years_pos": int((yr > 0).sum()),
        "year_means": {str(k): round(float(v), 3) for k, v in zip(yr.index, yr.to_numpy(float))},
        "top_decile_share": float(top / r.sum()) if r.sum() != 0 else float("nan"), "sum_R": float(r.sum()),
        "mean_units": float(df.units.mean()), "stop_rate": float(df.stopped.mean()),
    }
    if "whipsaw" in df:
        out["whipsaw"] = float(df.whipsaw.mean())
    out["pass"] = bool(out["lb90_mean"] > 0 and out["p_diff_bonf7"] < 0.10 and out["years_pos"] >= 5
                       and out.get("whipsaw", 0.0) <= 0.45)
    out["ledger"] = ledger(df)
    return out


def ledger(df: pd.DataFrame) -> dict:
    t = pd.to_datetime(df.t_exit, utc=True)
    wk = (t.dt.tz_localize(None).dt.to_period("W-SUN")).astype(str)
    all_weeks = pd.period_range("2010-01-04", "2017-12-31", freq="W-SUN").astype(str)
    out = {}
    order = np.argsort(df.t_exit.to_numpy(), kind="stable")
    r, w = df.R.to_numpy()[order], wk.to_numpy()[order]
    for f in (0.05, 0.10, 0.20):
        mult = pd.Series(1.0, index=all_weeks)
        g = pd.Series(np.maximum(1 + f * r, 0.0)).groupby(w).prod()
        mult.loc[mult.index.intersection(g.index)] = g.reindex(mult.index.intersection(g.index)).to_numpy()
        m = mult.to_numpy()
        geo = float(np.exp(np.mean(np.log(np.maximum(m, 1e-12)))))
        out[f"{int(f * 100)}%"] = {"p_double": float((m >= 2).mean()), "p_half": float((m <= 0.5).mean()),
                                    "median": float(np.median(m)), "geo_mean": geo, "worst": float(m.min()),
                                    "best": float(m.max())}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--cal", type=Path, required=True)
    ap.add_argument("--optimistic", action="store_true")
    a = ap.parse_args()
    global OPTIMISTIC
    OPTIMISTIC = bool(a.optimistic)
    tag = "_optimistic" if OPTIMISTIC else ""
    OUT.mkdir(parents=True, exist_ok=True)
    b = load_bars(a.raw)
    costs = load_costs()
    cal = pd.read_parquet(a.cal)
    cal["event_utc"] = pd.to_datetime(cal.event_utc, utc=True)
    rng = np.random.default_rng(SEED)
    res: dict[str, dict] = {}
    for v in ("C2a", "C2b"):
        real, tw = run_c2(b, costs, v)
        res[v] = stats(real, tw, "day", rng)
        pd.DataFrame(real).to_parquet(OUT / f"{v}_real{tag}.parquet")
    real, tw = run_c4(b, costs)
    res["C4"] = stats(real, tw, "day", rng)
    pd.DataFrame(real).to_parquet(OUT / f"C4_real{tag}.parquet")
    for k, v in ((0.5, "C1a"), (1.0, "C1b")):
        real, tw, cnt = run_c1(b, costs, cal, k, v)
        res[v] = stats(real, tw, "event", rng)
        res[v]["events"] = cnt
        pd.DataFrame(real).to_parquet(OUT / f"{v}_real{tag}.parquet")
    (OUT / f"results{tag}.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
