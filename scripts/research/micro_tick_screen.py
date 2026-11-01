"""Tick-proxy microstructure screen on 2010-2017 (frozen rules: docs/research/prereg-2026-11-01-micro-tick.md).

Three rules on Dukascopy 1m bid/ask bars, whose per-minute tick volume (`tick_count`), mean and max spread are the
tick-level information held locally (raw ticks for 2010-2017 are not on this machine):

    VAC  liquidity-vacuum reversal: spread blowout (minute mean spread >= 1.5 x its 60-minute median) with a fast
         3-minute move (>= 3 x ATR1m), faded; stop beyond the move's extreme, 1.5 R target, 15-minute time exit
    TIM  tick-imbalance continuation: tick-weighted share of up minutes over 5 minutes |I5| >= 0.6 with activity
         >= 1.5 x its median, traded with the imbalance for 5 minutes; stop 2 x ATR1m
    SRR  stop-run reversal: first pierce of the prior trading day's high (low) with a tick burst (>= 2 x median), a
         1m close back inside within 5 minutes, faded; stop beyond the sweep extreme, 2 R target, 60-minute exit

Decisions use completed bars only; the fill is the next 1m bar's open (one bar of latency), mid price, plus 1.5 x the
cost-of-record ICM session cell and a spread-state surcharge (see `trade_cost`). No 2018+ bar is read (asserted).
Writes statistics to --out (JSON; not committed). `--count-only` prints signal counts without any outcome.

    .venv/bin/python scripts/research/micro_tick_screen.py --raw raw --out ~/tmp/micro_tick/result.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from goldbot.data.timeutil import epoch_ns

ROOT = Path(__file__).resolve().parents[2]
END = pd.Timestamp("2018-01-01", tz="UTC")
YEARS = tuple(range(2010, 2018))
COST_VERSION = "cor-2026-10-31.2"
COST_MULT = 1.5
N_TWIN = 4
N_BOOT = 10_000
SEED = 20261101
K_HOLM = 3
MIN = 60_000_000_000                 # one minute in ns
DAY = 1440 * MIN
WIN0, WIN1, FLAT = 7 * 60, 20 * 60 + 30, 21 * 60   # fill window [07:00, 20:30] UTC; flat before 21:00
RULES: dict[str, dict[str, float | None]] = {
    "VAC": {"tgt": 1.5, "hold": 15},
    "TIM": {"tgt": None, "hold": 5},
    "SRR": {"tgt": 2.0, "hold": 60},
}


# ---------------------------------------------------------------- data
def frame_from_raw(b: pd.DataFrame) -> pd.DataFrame:
    """Mid OHLC, tick volume and spreads per 1m bar, sorted and de-duplicated, with the trailing 60-bar features
    (each uses bars t-60..t-1 only)."""
    ts = pd.DatetimeIndex(b["ts_utc"]).as_unit("ns")
    assert ts.max() < END, "a 2018+ bar was read"
    f = pd.DataFrame({
        "t": epoch_ns(ts),
        "o": ((b["bid_open"] + b["ask_open"]) / 2).to_numpy(float),
        "h": ((b["bid_high"] + b["ask_high"]) / 2).to_numpy(float),
        "l": ((b["bid_low"] + b["ask_low"]) / 2).to_numpy(float),
        "c": ((b["bid_close"] + b["ask_close"]) / 2).to_numpy(float),
        "tc": b["tick_count"].to_numpy(float),
        "spm": b["spread_mean"].to_numpy(float),
        "spx": b["spread_max"].to_numpy(float),
        "spo": (b["ask_open"] - b["bid_open"]).to_numpy(float),
    })
    f = f.sort_values("t", kind="stable").drop_duplicates("t").reset_index(drop=True)
    f["atr1"] = (f.h - f.l).rolling(60).mean().shift(1)
    f["med_sp"] = f.spm.rolling(60).median().shift(1)
    f["med_tc"] = f.tc.rolling(60).median().shift(1)
    return f


def load_year(raw: Path, y: int) -> tuple[pd.DataFrame, int]:
    """Year y plus the last 4 days of y-1 (warm-up and the prior day's range). Returns (frame, first index of y)."""
    parts = []
    if y - 1 in YEARS:
        p = pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y - 1}.parquet")
        parts.append(p[pd.DatetimeIndex(p["ts_utc"]) >= pd.Timestamp(f"{y - 1}-12-28", tz="UTC")])
    parts.append(pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet"))
    f = frame_from_raw(pd.concat(parts, ignore_index=True))
    y0 = int(pd.Timestamp(f"{y}-01-01", tz="UTC").value)
    return f, int(np.searchsorted(f.t.to_numpy(), y0))


def mod(t: int) -> int:
    return int((t // MIN) % 1440)


def session(t: int) -> str:
    m = mod(t)
    return "asia" if m < 7 * 60 else ("london" if m < 12 * 60 + 30 else "newyork")


def load_costs() -> dict[str, dict[str, float]]:
    cor = yaml.safe_load((ROOT / "config" / "cost_of_record.yaml").read_text())
    assert cor["version"] == COST_VERSION, cor["version"]
    return {s: {"rt": float(v["round_trip_usd"]), "ov": float(v["overshoot_usd"]), "spread": float(v["spread_usd"])}
            for s, v in cor["cells"]["icm"].items() if s != "all"}


def h_u(*key: object) -> float:
    return int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:8], "big") / 2 ** 64


# ---------------------------------------------------------------- signals: (k_signal, side, R in $) on completed bars
def fill_ok(f: pd.DataFrame, k: int) -> bool:
    """The fill bar k+1 exists, opens within 5 minutes of the signal bar and inside the fill window."""
    t = f.t.to_numpy()
    if k + 1 >= len(t) or t[k + 1] - t[k] > 5 * MIN:
        return False
    return WIN0 <= mod(int(t[k + 1])) <= WIN1


def sig_vac(f: pd.DataFrame, k0: int) -> list[tuple[int, int, float]]:
    o, h, lo, c = (f[x].to_numpy() for x in ("o", "h", "l", "c"))
    spm, msp, atr = f.spm.to_numpy(), f.med_sp.to_numpy(), f.atr1.to_numpy()
    out: list[tuple[int, int, float]] = []
    nxt = max(k0, 3)
    for k in range(nxt, len(c) - 1):
        if k < nxt or not (np.isfinite(atr[k]) and atr[k] > 0 and np.isfinite(msp[k]) and msp[k] > 0):
            continue
        mv = c[k] - c[k - 3]
        if spm[k] < 1.5 * msp[k] or abs(mv) < 3 * atr[k] or not fill_ok(f, k):
            continue
        side = -1 if mv > 0 else 1
        e = o[k + 1]
        stop = (h[k - 2:k + 1].max() + 0.5 * atr[k]) if side < 0 else (lo[k - 2:k + 1].min() - 0.5 * atr[k])
        out.append((k, side, max(abs(stop - e) if (stop - e) * side < 0 else 0.0, atr[k])))
        nxt = k + 15
    return out


def sig_tim(f: pd.DataFrame, k0: int) -> list[tuple[int, int, float]]:
    o, c, tc = f.o.to_numpy(), f.c.to_numpy(), f.tc.to_numpy()
    mtc, atr = f.med_tc.to_numpy(), f.atr1.to_numpy()
    sg = np.sign(c - o)
    out: list[tuple[int, int, float]] = []
    nxt = max(k0, 4)
    for k in range(nxt, len(c) - 1):
        if k < nxt or not (np.isfinite(atr[k]) and atr[k] > 0 and np.isfinite(mtc[k - 4]) and mtc[k - 4] > 0):
            continue
        w = tc[k - 4:k + 1]
        sw = w.sum()
        if sw <= 0 or sw < 1.5 * 5 * mtc[k - 4]:
            continue
        i5 = float((w * sg[k - 4:k + 1]).sum() / sw)
        if abs(i5) < 0.6 or np.sign(c[k] - o[k - 4]) != np.sign(i5) or not fill_ok(f, k):
            continue
        out.append((k, 1 if i5 > 0 else -1, 2 * atr[k]))
        nxt = k + 6
    return out


def sig_srr(f: pd.DataFrame, k0: int) -> list[tuple[int, int, float]]:
    t, o, h, lo, c = (f[x].to_numpy() for x in ("t", "o", "h", "l", "c"))
    tc, mtc, atr = f.tc.to_numpy(), f.med_tc.to_numpy(), f.atr1.to_numpy()
    tday = (t + 2 * 60 * MIN) // DAY                      # trading day: 22:00 UTC to 22:00 UTC
    days, starts = np.unique(tday, return_index=True)
    ends = np.r_[starts[1:], len(t)]
    out: list[tuple[int, int, float]] = []
    for d in range(1, len(days)):
        a, b = int(starts[d]), int(ends[d])
        if b <= k0:
            continue
        pa, pb = int(starts[d - 1]), int(ends[d - 1])
        pdh, pdl = float(h[pa:pb].max()), float(lo[pa:pb].min())
        for side, beyond in ((-1, lambda k: h[k] > pdh), (1, lambda k: lo[k] < pdl)):
            for k in range(max(a, k0), b):
                m = mod(int(t[k]))
                if m < WIN0 or m > 19 * 60 + 54 or not beyond(k):
                    continue
                if not (np.isfinite(mtc[k]) and mtc[k] > 0 and tc[k] >= 2 * mtc[k] and np.isfinite(atr[k])):
                    break                               # the first pierce had no stop-trigger burst: side done today
                for s in range(k, min(k + 6, b)):
                    if t[s] - t[k] > 5 * MIN:
                        break
                    back = c[s] < pdh if side < 0 else c[s] > pdl
                    if back:
                        if fill_ok(f, s):
                            e = o[s + 1]
                            ext = h[k:s + 1].max() + 0.5 * atr[s] if side < 0 else lo[k:s + 1].min() - 0.5 * atr[s]
                            dist = (ext - e) * (-side)
                            out.append((s, side, max(dist if dist > 0 else 0.0, atr[s])))
                        break
                break                                   # only the first pierce per side per day
    return sorted(out)


SIGNALS = {"VAC": sig_vac, "TIM": sig_tim, "SRR": sig_srr}


# ---------------------------------------------------------------- walk and cost
def walk(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, side: int, risk: float,
         tgt: float | None) -> tuple[float, str, int]:
    """Bars from the fill minute to the last allowed minute; entry at o[0]. Stop at entry -+ risk, optional target at
    tgt x risk; a minute holding both is a stop; a gap through the stop fills at that minute's open.
    Returns (gross USD/oz, exit kind 'stop'/'target'/'time', exit offset)."""
    e = o[0]
    if side < 0:
        o, h, lo, c = -o, -lo, -h, -c
        e = -e
    stop = e - risk
    s = np.flatnonzero(lo <= stop)
    js = int(s[0]) if len(s) else 10 ** 9
    jt = 10 ** 9
    if tgt is not None:
        tt = np.flatnonzero(h >= e + tgt * risk)
        jt = int(tt[0]) if len(tt) else 10 ** 9
    if js <= jt and js < 10 ** 9:
        return float(min(stop, o[js]) - e), "stop", js
    if jt < 10 ** 9:
        return float(tgt * risk) if tgt is not None else 0.0, "target", jt   # a gap through the target: level only
    return float(c[-1] - e), "time", len(o) - 1


def trade_cost(cell: dict[str, float], kind: str, sp_in: float, med_in: float, sp_out: float, med_out: float) -> float:
    """1.5 x (cell round trip + overshoot on a stop exit) + 1.5 x half the cell spread x the excess of the Dukascopy
    spread over its 60-minute median, on each leg (a blown-out spread costs proportionally more)."""
    def excess(sp: float, med: float) -> float:
        return max(0.0, sp / med - 1.0) if np.isfinite(sp) and np.isfinite(med) and med > 0 else 0.0
    base = cell["rt"] + (cell["ov"] if kind == "stop" else 0.0)
    return COST_MULT * (base + 0.5 * cell["spread"] * (excess(sp_in, med_in) + excess(sp_out, med_out)))


def simulate(f: pd.DataFrame, costs: dict[str, dict[str, float]], kf: int, side: int, risk: float,
             tgt: float | None, hold: int) -> dict[str, Any] | None:
    t = f.t.to_numpy()
    tf = int(t[kf])
    day0 = (tf // DAY) * DAY
    t_end = min(tf + hold * MIN, day0 + FLAT * MIN)
    ke = int(np.searchsorted(t, t_end, side="left")) - 1
    if ke < kf or not np.isfinite(risk) or risk <= 0:
        return None
    sl = slice(kf, ke + 1)
    gross, kind, j = walk(f.o.to_numpy()[sl], f.h.to_numpy()[sl], f.l.to_numpy()[sl], f.c.to_numpy()[sl],
                          side, risk, tgt)
    kx = kf + j
    spo, spm, msp = f.spo.to_numpy(float), f.spm.to_numpy(float), f.med_sp.to_numpy(float)
    cost = trade_cost(costs[session(tf)], kind, float(spo[kf]), float(msp[kf]), float(spm[kx]), float(msp[kx]))
    return {"day": int(day0 // DAY), "t_fill": tf, "t_exit": int(t[kx]), "side": side, "risk": float(risk),
            "gross_R": gross / risk, "R": (gross - cost) / risk, "kind": kind, "cost_R": cost / risk}


def run_year(args: tuple[str, int, bool]) -> dict[str, Any]:
    raw, y, count_only = args
    f, k0 = load_year(Path(raw), y)
    t = f.t.to_numpy()
    costs = load_costs()
    out: dict[str, Any] = {"year": y}
    for name, fn in SIGNALS.items():
        sigs = [s for s in fn(f, k0) if t[s[0]] >= t[k0]]
        if count_only:
            out[name] = len(sigs)
            continue
        tgt, hold = RULES[name]["tgt"], int(RULES[name]["hold"] or 0)
        real, twins = [], []
        for i, (k, side, risk) in enumerate(sigs):
            r = simulate(f, costs, k + 1, side, risk, tgt, hold)
            if r is None:
                continue
            rid = f"{name}-{y}-{i}"
            r["id"] = rid
            real.append(r)
            d0 = (int(t[k + 1]) // DAY) * DAY
            pa = int(np.searchsorted(t, d0 + WIN0 * MIN, side="left"))
            pb = int(np.searchsorted(t, d0 + WIN1 * MIN, side="right"))      # same-day bars 07:00..20:30
            for j in range(N_TWIN):
                kk = pa + int(h_u(name, rid, j, "k") * (pb - pa))
                tw_side = 1 if h_u(name, rid, j, "side") < 0.5 else -1
                tw = simulate(f, costs, kk, tw_side, risk, tgt, hold)
                if tw is not None:
                    tw["id"] = rid
                    twins.append(tw)
        out[name] = {"real": real, "twins": twins}
    return out


# ---------------------------------------------------------------- statistics
def boot(day: np.ndarray, x: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    """Day-block bootstrap of the per-trade mean: (5% quantile, one-sided p of mean <= 0)."""
    u, inv = np.unique(day, return_inverse=True)
    s = np.bincount(inv, weights=x)
    n = np.bincount(inv).astype(float)
    idx = rng.integers(0, len(u), size=(N_BOOT, len(u)))
    m = s[idx].sum(1) / n[idx].sum(1)
    return float(np.quantile(m, 0.05)), float((m <= 0).mean())


def holm(p: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values."""
    names = sorted(p, key=lambda k: p[k])
    adj: dict[str, float] = {}
    run = 0.0
    for i, k in enumerate(names):
        run = max(run, min(1.0, (len(p) - i) * p[k]))
        adj[k] = run
    return adj


def stats(real: list[dict[str, Any]], twins: list[dict[str, Any]], rng: np.random.Generator) -> dict[str, Any]:
    df, tw = pd.DataFrame(real), pd.DataFrame(twins)
    r = df.R.to_numpy()
    lb, p = boot(df.day.to_numpy(), r, rng)
    tmean = tw.groupby("id").R.mean()
    df["diff"] = df.R - df.id.map(tmean).fillna(0.0)
    dlb, dp = boot(df.day.to_numpy(), df["diff"].to_numpy(), rng)
    yr = df.groupby(pd.to_datetime(df.t_fill, utc=True).dt.year).R.mean()
    sd = float(r.std(ddof=1))
    return {
        "n": int(len(r)), "trades_per_week": len(r) / 417.4, "gross_mean_R": float(df.gross_R.mean()),
        "gross_t": float(df.gross_R.mean() / (df.gross_R.std(ddof=1) / np.sqrt(len(r)))),
        "cost_mean_R": float(df.cost_R.mean()), "mean_R": float(r.mean()), "sd_R": sd,
        "t_net": float(r.mean() / (sd / np.sqrt(len(r)))), "lb90_mean": lb, "p_mean": p,
        "twin_mean_R": float(tw.R.mean()), "twin_gross_R": float(tw.gross_R.mean()), "diff_mean": float(df["diff"].mean()),
        "diff_lb90": dlb, "p_diff": dp, "years_pos": int((yr > 0).sum()),
        "year_means": {str(k): round(float(v), 3) for k, v in yr.items()},
        "median_risk_usd": float(df.risk.median()), "exit_mix": df.kind.value_counts(normalize=True).round(3).to_dict(),
        "hit_rate": float((r > 0).mean()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path.home() / "tmp" / "micro_tick" / "result.json")
    ap.add_argument("--count-only", action="store_true")
    a = ap.parse_args()
    with Pool(4) as pool:
        res = pool.map(run_year, [(str(a.raw), y, a.count_only) for y in YEARS])
    if a.count_only:
        for name in SIGNALS:
            print(name, {r["year"]: r[name] for r in res}, "total", sum(r[name] for r in res))
        return
    rng = np.random.default_rng(SEED)
    out: dict[str, Any] = {}
    for name in SIGNALS:
        real = [x for r in res for x in r[name]["real"]]
        twins = [x for r in res for x in r[name]["twins"]]
        out[name] = stats(real, twins, rng)
    pm = holm({k: v["p_mean"] for k, v in out.items()})
    pd_ = holm({k: v["p_diff"] for k, v in out.items()})
    for k, v in out.items():
        v["p_mean_holm"], v["p_diff_holm"] = pm[k], pd_[k]
        v["pass"] = bool(pm[k] < 0.10 and v["lb90_mean"] > 0 and pd_[k] < 0.10 and v["years_pos"] >= 5)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1, default=float))
    for k, v in out.items():
        print(k, {x: (round(y, 4) if isinstance(y, float) else y) for x, y in v.items()})


if __name__ == "__main__":
    main()
