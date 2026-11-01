"""Opportunity signals screen: SA exhaustion reversion, SB weekly-open gap fade, SD volatility-regime switch.

Pre-registration: docs/research/prereg-opportunity-signals-2026-11-01.md (frozen at d4dba0f3 before any outcome).
2010-2017 only, local bars; outputs go to ~/tmp/opp_signals (nothing written to the repo).

Usage: .venv/bin/python docs/research/candidates/opportunity_signals/opp_screen.py --raw <raw folder> --cal <cal parquet>

Every walk works on a long view: a short is the same walk on negated prices (high <-> -low)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from multiprocessing import Pool
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
SWAP_LONG = 0.60
N_TWIN = 4
N_BOOT = 10_000
SEED = 20261101
K_BONF = 3
MIN = 60_000_000_000
HOUR = 60 * MIN
DAY = 1440 * MIN
WEEKS = 417.4
OUT = Path.home() / "tmp" / "opp_signals"
NONE = 10 ** 9


@dataclass
class Bars:
    ts: np.ndarray
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    vol: np.ndarray
    spread: np.ndarray
    atr_end: np.ndarray
    atr: np.ndarray

    def at(self, t: int) -> int:
        """First 1m bar opening at or after t."""
        return int(np.searchsorted(self.ts, t, side="left"))

    def last(self, t: int) -> int:
        """Last 1m bar whose close (open + 1 min) is at or before t."""
        return int(np.searchsorted(self.ts, t - MIN, side="right")) - 1

    def atr15(self, t: int) -> float:
        i = int(np.searchsorted(self.atr_end, t, side="right")) - 1
        return float(self.atr[i]) if i >= 0 else float("nan")


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
    vol = b["tick_count"].to_numpy(float)[order][uniq]
    spr = b["spread_mean"].to_numpy(float)[order][uniq]
    t = t[uniq]
    f = pd.DataFrame({"o": o, "h": h, "l": lo, "c": c}, index=pd.DatetimeIndex(t, tz="UTC"))
    q = f.resample("15min").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    pc = q["c"].shift(1)
    tr = pd.concat([q["h"] - q["l"], (q["h"] - pc).abs(), (q["l"] - pc).abs()], axis=1).max(axis=1)
    ends = epoch_ns(pd.DatetimeIndex(q.index)) + 15 * MIN
    return Bars(t, o, h, lo, c, np.nan_to_num(vol), spr, ends, tr.rolling(14).mean().to_numpy(float))


def load_costs() -> dict[str, dict[str, float]]:
    cor = yaml.safe_load((ROOT / "config" / "cost_of_record.yaml").read_text())
    assert cor["version"] == COST_VERSION, cor["version"]
    return {s: {"rt": float(v["round_trip_usd"]), "ov": float(v["overshoot_usd"])}
            for s, v in cor["cells"]["icm"].items() if s != "all"}


def session(t: int) -> str:
    mod = (t // MIN) % 1440
    return "asia" if mod < 7 * 60 else ("london" if mod < 12 * 60 + 30 else "newyork")


def rollovers(t0: int, t1: int) -> float:
    """Long swap units for 22:00 UTC weekday rollovers in (t0, t1]; Wednesday counts 3."""
    r = (t0 // DAY) * DAY + 22 * HOUR
    if r <= t0:
        r += DAY
    n = 0.0
    while r <= t1:
        wd = pd.Timestamp(r, tz="UTC").dayofweek
        if wd < 5:
            n += 3.0 if wd == 2 else 1.0
        r += DAY
    return n


def h_u(*key: object) -> float:
    return int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:8], "big") / 2 ** 64


def trade(b: Bars, costs: dict, k_in: int, k_x: int, side: int, risk: float, tgt_dist: float | None,
          extra: float = 0.0) -> dict | None:
    """Market entry at the open of minute k_in, stop `risk` away, optional target `tgt_dist` away, time exit at the
    close of minute k_x. Stops and targets are checked from the entry minute; a minute holding both is a stop."""
    if k_x <= k_in or not np.isfinite(risk) or risk <= 0:
        return None
    sl = slice(k_in, k_x + 1)
    if side > 0:
        o, h, lo, c = b.o[sl], b.h[sl], b.lo[sl], b.c[sl]
    else:
        o, h, lo, c = -b.o[sl], -b.lo[sl], -b.h[sl], -b.c[sl]
    e = o[0]
    stop = e - risk
    s = np.flatnonzero(lo <= stop)
    js = int(s[0]) if len(s) else NONE
    jt = NONE
    if tgt_dist is not None:
        tt = np.flatnonzero(h >= e + tgt_dist)
        jt = int(tt[0]) if len(tt) else NONE
    if js < NONE and js <= jt:
        gross, stopped, jx = float(min(stop, o[js]) - e), True, js
    elif jt < NONE:
        gross, stopped, jx = float(tgt_dist), False, jt   # type: ignore[arg-type]
    else:
        gross, stopped, jx = float(c[-1] - e), False, len(o) - 1
    t_in, t_out = int(b.ts[k_in]), int(b.ts[k_in + jx]) + MIN
    cc = costs[session(t_in)]
    swap = SWAP_LONG * rollovers(t_in, t_out) if side > 0 else 0.0
    net = gross - COST_MULT * cc["rt"] - (COST_MULT * cc["ov"] if stopped else 0.0) - swap - extra
    return {"t_entry": t_in, "t_exit": t_out, "side": side, "R": net / risk, "gross_R": gross / risk,
            "stopped": stopped, "risk": risk}


# ---------------------------------------------------------------- SA exhaustion reversion
def run_sa(b: Bars, costs: dict, cal: pd.DataFrame) -> tuple[list[dict], list[dict], dict]:
    grid = np.arange((b.ts[0] // (15 * MIN) + 1) * 15 * MIN, b.ts[-1], 15 * MIN)
    wd = ((grid // DAY) + 3) % 7                      # 1970-01-01 is a Thursday: 0 = Monday
    mod = (grid // MIN) % 1440
    keep = (wd < 5) & (mod <= 20 * 60 + 30)
    gaps = np.flatnonzero(np.diff(b.ts) >= 6 * HOUR) + 1
    reopen = b.ts[gaps]
    ri = np.searchsorted(reopen, grid, side="right") - 1
    since = np.where(ri >= 0, grid - reopen[np.maximum(ri, 0)], 10 ** 18)
    keep &= since >= 120 * MIN
    grid = grid[keep]
    k_t = np.searchsorted(b.ts, grid - MIN, side="right") - 1
    k_60 = np.searchsorted(b.ts, grid - 61 * MIN, side="right") - 1
    nwin = k_t - k_60
    ok = (k_60 >= 0) & (nwin >= 50) & (b.ts[np.maximum(k_t, 0)] >= grid - 60 * MIN)
    r60 = np.where(ok, b.c[np.maximum(k_t, 0)] - b.c[np.maximum(k_60, 0)], np.nan)
    df = pd.DataFrame({"t": grid, "clock": (grid // MIN) % 1440, "r": r60, "k_t": k_t, "k_60": k_60})
    df["r2"] = df.r ** 2
    g = df[df.r.notna()].groupby("clock").r2
    df["sig"] = np.nan
    roll = g.transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    df.loc[roll.index, "sig"] = np.sqrt(roll)
    ev = cal[(cal.tier <= 2) & (cal.status == "scheduled")]
    t_ev = np.sort(epoch_ns(pd.DatetimeIndex(ev.event_utc)))
    lo_i = np.searchsorted(t_ev, df.t.to_numpy() - 75 * MIN, side="left")
    hi_i = np.searchsorted(t_ev, df.t.to_numpy() + 15 * MIN, side="right")
    df["news"] = hi_i > lo_i
    df["sig_ok"] = df.r.notna() & df.sig.notna() & (df.sig > 0)
    df["fire"] = df.sig_ok & (df.r.abs() >= 3 * df.sig) & ~df.news
    allowed = df[df.r.notna()].t.to_numpy()          # twin decision times
    real: list[dict] = []
    twins: list[dict] = []
    cnt = {"grid": int(len(df)), "fires": int(df.fire.sum()), "news_blocked": int((df.sig_ok & (df.r.abs() >= 3 * df.sig)
                                                                                  & df.news).sum()),
           "skip_overlap": 0, "skip_target": 0}
    busy = -1
    fire = df[df.fire]
    for t, rr, k60, kt in zip(fire.t.to_numpy(int).tolist(), fire.r.to_numpy(float).tolist(),
                              fire.k_60.to_numpy(int).tolist(), fire.k_t.to_numpy(int).tolist()):
        if t < busy:
            cnt["skip_overlap"] += 1
            continue
        k_in = b.at(t)
        if k_in >= len(b.ts):
            continue
        atr = b.atr15(t)
        if not np.isfinite(atr) or atr <= 0:
            continue
        w = slice(k60 + 1, kt + 1)
        side = -1 if rr > 0 else 1
        entry = b.o[k_in]
        if rr > 0:
            stop = float(b.h[w].max()) + 0.25 * atr
        else:
            stop = float(b.lo[w].min()) - 0.25 * atr
        risk = max(abs(stop - entry), 0.5 * atr)
        tp = (b.h[w] + b.lo[w] + b.c[w]) / 3
        vw = b.vol[w]
        vwap = float((tp * vw).sum() / vw.sum()) if vw.sum() > 0 else float(tp.mean())
        tgt = side * (vwap - entry)
        if tgt < 0.25 * risk:
            cnt["skip_target"] += 1
            continue
        k_x = b.last(int(b.ts[k_in]) + 240 * MIN)
        tr = trade(b, costs, k_in, k_x, side, risk, tgt)
        if tr is None:
            continue
        tr["day"] = (t // DAY) * DAY
        real.append(tr)
        busy = tr["t_exit"]
        d0 = (t // DAY) * DAY
        pool = allowed[(allowed >= max(t - 180 * MIN, d0)) & (allowed <= min(t + 180 * MIN, d0 + DAY - 1))]
        for i in range(N_TWIN):
            tt = int(pool[int(h_u("SA", t, i, "t") * len(pool))])
            st = 1 if h_u("SA", t, i, "s") < 0.5 else -1
            kk = b.at(tt)
            tw = trade(b, costs, kk, b.last(int(b.ts[kk]) + 240 * MIN), st, risk, tgt)
            if tw:
                tw["day"] = d0
                twins.append(tw)
    return real, twins, cnt


# ---------------------------------------------------------------- SB weekly-open gap fade
def trading_days(b: Bars) -> pd.DataFrame:
    day = (b.ts + 2 * HOUR) // DAY
    brk = np.flatnonzero(np.r_[True, np.diff(day) > 0, True])
    rows = []
    for a, z in zip(brk[:-1], brk[1:]):
        if z - a < 300:
            continue
        rows.append((int(day[a]), a, z - 1, float(b.h[a:z].max()), float(b.lo[a:z].min()), float(b.c[z - 1])))
    td = pd.DataFrame(rows, columns=["day", "k0", "k1", "hi", "lo", "cl"])
    pc = td.cl.shift(1)
    td["tr"] = pd.concat([td.hi - td.lo, (td.hi - pc).abs(), (td.lo - pc).abs()], axis=1).max(axis=1)
    return td


def run_sb(b: Bars, costs: dict) -> tuple[list[dict], list[dict], dict]:
    td = trading_days(b)
    atr20 = td.tr.rolling(20).mean().to_numpy()
    k1s = td.k1.to_numpy()
    yr = pd.DatetimeIndex(pd.to_datetime(b.ts, utc=True)).year.to_numpy()
    mod = (b.ts // MIN) % 1440
    asia = mod < 7 * 60
    med_spr = {y: float(np.nanmedian(b.spread[(yr == y) & asia])) for y in np.unique(yr)}

    def surcharge(k: int) -> float:
        return max(0.0, float(b.spread[k]) - med_spr[int(yr[k])])

    gi = np.flatnonzero(np.diff(b.ts) >= 24 * HOUR) + 1
    real: list[dict] = []
    twins: list[dict] = []
    cnt = {"reopens": 0, "below_threshold": 0, "crossed": 0}
    for k in gi:
        t0 = int(b.ts[k])
        if pd.Timestamp(t0, tz="UTC").dayofweek != 6:
            continue
        cnt["reopens"] += 1
        f_close = float(b.c[k - 1])
        j = int(np.searchsorted(k1s, k, side="left")) - 1     # last trading day completed before the reopen
        if j < 20 or not np.isfinite(atr20[j]):
            continue
        a20 = float(atr20[j])
        t_dec = t0 + 30 * MIN
        kp = b.last(t_dec)
        g = float(b.c[kp]) - f_close
        if abs(g) < 0.20 * a20:
            cnt["below_threshold"] += 1
            continue
        side = -1 if g > 0 else 1
        k_in = b.at(t_dec)
        monday7 = (t0 // DAY) * DAY + DAY + 7 * HOUR
        k_x = b.last(monday7)
        dist = side * (f_close - float(b.o[k_in]))
        if dist <= 0:
            cnt["crossed"] += 1
            continue
        risk = max(dist, 0.20 * a20)
        tr = trade(b, costs, k_in, k_x, side, risk, dist, surcharge(k_in))
        if tr is None:
            continue
        tr["day"] = (t0 // DAY) * DAY
        tr["gap_atr"] = g / a20
        real.append(tr)
        lo_t, hi_t = t_dec, (t0 // DAY) * DAY + DAY + 5 * HOUR
        for i in range(N_TWIN):
            tt = lo_t + int(h_u("SB", t0, i, "t") * (hi_t - lo_t) // MIN) * MIN
            st = 1 if h_u("SB", t0, i, "s") < 0.5 else -1
            kk = b.at(tt)
            tw = trade(b, costs, kk, k_x, st, risk, dist, surcharge(kk))
            if tw:
                tw["day"] = tr["day"]
                twins.append(tw)
    return real, twins, cnt


# ---------------------------------------------------------------- SD volatility-regime switch
def run_sd(b: Bars, costs: dict) -> tuple[list[dict], list[dict], dict]:
    idx = pd.DatetimeIndex(b.ts, tz="UTC")
    f = pd.DataFrame({"o": b.o, "h": b.h, "l": b.lo, "c": b.c}, index=idx)
    # daily RV from 5m mid log returns within each 22:00-22:00 trading day
    m5 = f.c.resample("5min").last().dropna()
    d5 = (epoch_ns(pd.DatetimeIndex(m5.index)) + 2 * HOUR) // DAY
    lr = np.diff(np.log(m5.to_numpy()))
    same = d5[1:] == d5[:-1]
    rv = pd.Series(lr[same] ** 2).groupby(d5[1:][same]).sum()
    nmin = pd.Series(1, index=(b.ts + 2 * HOUR) // DAY).groupby(level=0).size()
    rv = rv[rv.index.isin(nmin[nmin >= 300].index)]
    x = rv.shift(1).rolling(5).mean()               # RV5 known at the day's start (prior 5 completed rows)
    xv = x.to_numpy()
    rank = np.full(len(xv), np.nan)
    for i in range(len(xv)):
        prev = xv[max(0, i - 250):i]
        prev = prev[np.isfinite(prev)]
        if np.isfinite(xv[i]) and len(prev) >= 200:
            rank[i] = float((prev <= xv[i]).mean())
    regime = pd.Series(rank, index=rv.index)
    # 1h bars
    hcount = f.c.resample("1h").count()
    hb = f.resample("1h").agg({"o": "first", "h": "max", "l": "min", "c": "last"})
    hb = hb[hcount >= 30].dropna()
    hend = epoch_ns(pd.DatetimeIndex(hb.index)) + HOUR
    pc = hb.c.shift(1)
    tr = pd.concat([hb.h - hb.l, (hb.h - pc).abs(), (hb.l - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().to_numpy()
    dch = hb.h.shift(1).rolling(20).max().to_numpy()
    dcl = hb.l.shift(1).rolling(20).min().to_numpy()
    sma = hb.c.rolling(20).mean().to_numpy()
    sd = hb.c.rolling(20).std(ddof=0).to_numpy()
    cl = hb.c.to_numpy()
    hour_end = (hend // HOUR) % 24
    tday = (hend + 2 * HOUR) // DAY
    rg = regime.reindex(tday).to_numpy()
    allowed = ~np.isin(hour_end, [21, 22]) & np.isfinite(atr) & np.isfinite(sma) & np.isfinite(dch) & (atr > 0)
    allowed &= np.isfinite(rg)
    real: list[dict] = []
    twins: list[dict] = []
    cnt = {"hours": int(allowed.sum()), "high_hours": int((allowed & (rg >= 2 / 3)).sum()),
           "low_hours": int((allowed & (rg <= 1 / 3)).sum()), "skip_overlap": 0, "skip_target": 0}
    n = len(hb)

    def exit_k(i: int, bars: int) -> int:
        return b.last(int(hend[min(i + bars, n - 1)]))

    by_day: dict[int, np.ndarray] = {}
    al = np.flatnonzero(allowed)
    for d in np.unique(tday[al]).tolist():
        by_day[int(d)] = al[tday[al] == d]
    busy = -1
    for i in al.tolist():
        t = int(hend[i])
        if rg[i] >= 2 / 3:
            leg = "trend"
            side = 1 if cl[i] > dch[i] else (-1 if cl[i] < dcl[i] else 0)
        elif rg[i] <= 1 / 3:
            leg = "rev"
            side = -1 if cl[i] > sma[i] + 2 * sd[i] else (1 if cl[i] < sma[i] - 2 * sd[i] else 0)
        else:
            continue
        if side == 0:
            continue
        if t < busy:
            cnt["skip_overlap"] += 1
            continue
        k_in = b.at(t)
        if k_in >= len(b.ts):
            continue
        risk = 1.5 * float(atr[i])
        tgt = None
        bars = 24
        if leg == "rev":
            bars = 12
            tgt = side * (float(sma[i]) - float(b.o[k_in]))
            if tgt < 0.25 * risk:
                cnt["skip_target"] += 1
                continue
        tr_ = trade(b, costs, k_in, exit_k(i, bars), side, risk, tgt)
        if tr_ is None:
            continue
        tr_["day"] = int(tday[i]) * DAY
        tr_["leg"] = leg
        real.append(tr_)
        busy = tr_["t_exit"]
        pool = by_day[int(tday[i])]
        for j in range(N_TWIN):
            ii = int(pool[int(h_u("SD", t, j, "t") * len(pool))])
            st = 1 if h_u("SD", t, j, "s") < 0.5 else -1
            kk = b.at(int(hend[ii]))
            if kk >= len(b.ts):
                continue
            tw = trade(b, costs, kk, exit_k(ii, bars), st, risk, tgt)
            if tw:
                tw["day"] = tr_["day"]
                tw["leg"] = leg
                twins.append(tw)
    return real, twins, cnt


# ---------------------------------------------------------------- statistics
def summarise(real: list[dict], twins: list[dict], rng: np.random.Generator) -> dict[str, Any]:
    df, tw = pd.DataFrame(real), pd.DataFrame(twins)
    if len(df) < 2:
        return {"n": int(len(df)), "verdict": "COUNT"}
    r = df.R.to_numpy()
    u, inv = np.unique(df.day.to_numpy(), return_inverse=True)
    s, nn = np.bincount(inv, weights=r), np.bincount(inv).astype(float)
    bi = rng.integers(0, len(u), size=(N_BOOT, len(u)))
    lb = float(np.quantile(s[bi].sum(1) / nn[bi].sum(1), 0.05))
    rz = df.groupby("day").R.sum()
    tz = tw.groupby("day").R.sum() / N_TWIN if len(tw) else pd.Series(dtype=float)
    d = (rz - tz.reindex(rz.index).fillna(0.0)).to_numpy()
    di = rng.integers(0, len(d), size=(N_BOOT, len(d)))
    dm = d[di].mean(1)
    p = float((dm <= 0).mean())
    yr = df.groupby(pd.to_datetime(df.t_exit, utc=True).dt.year).R.mean()
    out: dict[str, Any] = {
        "n": int(len(r)), "trades_per_week": len(r) / WEEKS, "gross_mean_R": float(df.gross_R.mean()),
        "mean_R": float(r.mean()), "sd_R": float(r.std(ddof=1)), "lb90_mean": lb,
        "t_gross": float(df.gross_R.mean() / (df.gross_R.std(ddof=1) / np.sqrt(len(r)))),
        "twin_mean_R": float(tw.R.mean()) if len(tw) else float("nan"),
        "twin_gross_R": float(tw.gross_R.mean()) if len(tw) else float("nan"),
        "diff_mean": float(d.mean()), "diff_lb90": float(np.quantile(dm, 0.05)), "p_diff": p,
        "p_bonf3": min(1.0, K_BONF * p), "years_pos": int((yr > 0).sum()),
        "year_means": {str(k): round(float(v), 3) for k, v in yr.items()},
        "stop_rate": float(df.stopped.mean()), "median_risk_usd": float(df.risk.median()),
    }
    passed = out["lb90_mean"] > 0 and out["p_bonf3"] < 0.10 and out["years_pos"] >= 5
    out["verdict"] = "COUNT" if out["n"] < 100 else ("PASS" if passed else "FAIL")
    if "leg" in df:
        out["legs"] = {lg: {"n": int(len(g)), "gross_mean_R": float(g.gross_R.mean()), "mean_R": float(g.R.mean()),
                            "twin_mean_R": float(tw[tw.leg == lg].R.mean())}
                       for lg, g in df.groupby("leg")}
    return out


def worker(args: tuple[str, str, str]) -> tuple[str, dict]:
    rule, raw, cal_path = args
    b = load_bars(Path(raw))
    costs = load_costs()
    if rule == "SA":
        cal = pd.read_parquet(cal_path)
        cal["event_utc"] = pd.to_datetime(cal.event_utc, utc=True)
        real, tw, cnt = run_sa(b, costs, cal)
    elif rule == "SB":
        real, tw, cnt = run_sb(b, costs)
    else:
        real, tw, cnt = run_sd(b, costs)
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(real).to_parquet(OUT / f"{rule}_real.parquet")
    res = summarise(real, tw, np.random.default_rng(SEED))
    res["counts"] = cnt
    return rule, res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--cal", type=Path, required=True)
    a = ap.parse_args()
    with Pool(3) as pool:
        res = dict(pool.map(worker, [(r, str(a.raw), str(a.cal)) for r in ("SA", "SB", "SD")]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
