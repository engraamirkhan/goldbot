"""Slice C, part 1: do Fibonacci levels and Fibonacci time zones beat randomised ones? (docs/research/time-structure/
fib-wavelets-cycles.md). Descriptive screen, NOT a registry trial; parameters fixed before the data was read.

Levels: Asia range (00-07 UTC), previous opportunity-day range (22:00 UTC day), trailing 30/60/90-day range, each as
level = low + f * (high - low), f in the Fibonacci set. Null: levels from the SAME range at random f (drawn outside a
+-0.02 band round every Fibonacci/edge fraction), compared inside bins of distance-to-price (ATR units) so the real
and null levels have the same distance distribution. Outcome: first-passage on 1m mid after the first touch inside the
active window: bounce (price leaves the level by +b with the approach side) before break (-b), b = k * ATR14 of the
timeframe; timeout marks to market. Entry is assumed AT the level (a resting limit: optimistic), cost subtracted in R.
Everything uses information available at the start of the active window; swing/range levels never use later bars.

Usage: uv run --with scipy python docs/research/candidates/time_structure/ts_fib.py --data DIR [--nullper 20] [--seed 7]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

H = 3_600_000_000_000
MIN = 60_000_000_000
FIB_RETR = (0.236, 0.382, 0.5, 0.618, 0.786)
FIB_EXT = (1.272, 1.618, -0.272, -0.618)
EDGE = (0.0, 1.0)
COST_C, COST_K = 0.144, 0.23       # $/oz round trip, central / conservative (cost-model-1m-2026-10-25.md)
# timeframe -> (ATR timeframe, timeout hours)
TFCFG = {"15m": ("15m", 4), "1h": ("1h", 12), "4h": ("4h", 48), "1d": ("1d", 120)}


def atr_series(b: pd.DataFrame, n: int = 14) -> tuple[np.ndarray, np.ndarray]:
    """Wilder ATR on mid bars; returns (avail_ns, atr) where atr[i] is known at avail_ns[i]."""
    h, lw, c = b["high"].to_numpy(), b["low"].to_numpy(), b["close"].to_numpy()
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - lw, np.maximum(abs(h - pc), abs(lw - pc)))
    a = pd.Series(tr).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()
    return b["avail_ns"].to_numpy(), a


def first_passage(hi, lo, cl, i0, i1, level, direction, b):
    """Index scan on 1m mid. direction=+1: bounce up (long at level). Win if hi>=level+b before lo<=level-b.
    Ties inside one minute count as a loss. Returns R in units of b (timeout: mark to market at i1-1)."""
    if direction > 0:
        w = np.nonzero(hi[i0:i1] >= level + b)[0]
        s = np.nonzero(lo[i0:i1] <= level - b)[0]
    else:
        w = np.nonzero(lo[i0:i1] <= level - b)[0]
        s = np.nonzero(hi[i0:i1] >= level + b)[0]
    iw = w[0] if len(w) else 1 << 60
    isl = s[0] if len(s) else 1 << 60
    if iw == 1 << 60 and isl == 1 << 60:
        return direction * (cl[i1 - 1] - level) / b, 0
    return (1.0, 1) if iw < isl else (-1.0, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--nullper", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--k", type=float, default=0.5)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    m1 = pd.read_parquet(a.data / "mid_1m.parquet")
    t = m1["ts_ns"].to_numpy()
    hi, lo, cl = (m1[c].to_numpy() for c in ("high", "low", "close"))
    atrs = {tf: atr_series(pd.read_parquet(a.data / f"mid_{tf}.parquet")) for tf in TFCFG}
    # opportunity-day ids: day k = [k*24h+22h, k*24h+46h) i.e. 22:00 UTC to 22:00 UTC
    kid = (t - 22 * H) // (24 * H)
    days = np.unique(kid)
    starts = {int(k): int(np.searchsorted(t, k * 24 * H + 22 * H)) for k in days}
    ends = {int(k): int(np.searchsorted(t, k * 24 * H + 46 * H)) for k in days}
    rng_hl = {}
    for k in days:
        s, e = starts[int(k)], ends[int(k)]
        if e - s >= 600:
            rng_hl[int(k)] = (hi[s:e].max(), lo[s:e].min())
    dk = sorted(rng_hl)
    rows = []
    for k in dk:
        if k < dk[0] + 95 or k == dk[-1]:
            continue
        s0 = k * 24 * H + 22 * H
        ia, ib = np.searchsorted(t, [s0 + 2 * H, s0 + 9 * H])
        if ib - ia < 300:
            continue
        fam = {"asia": (hi[ia:ib].max(), lo[ia:ib].min())}
        prev = [x for x in dk if x < k]
        fam["prevday"] = rng_hl[prev[-1]]
        for n in (30, 60, 90):
            p = prev[-n:]
            fam[f"swing{n}"] = (max(rng_hl[x][0] for x in p), min(rng_hl[x][1] for x in p))
        i_act = int(np.searchsorted(t, s0 + 9 * H))
        i_end = ends[k]
        if i_act >= i_end - 60:
            continue
        p0 = cl[i_act - 1]
        refs = [(cl[starts[k] - 1] if starts[k] > 0 else np.nan, i_act),            # prior close (known)
                (cl[ia - 1], i_act),                             # Asia open
                (p0, i_act)]                                                         # London open
        ny_i = int(np.searchsorted(t, s0 + 15 * H))
        refs.append((cl[ny_i - 1], ny_i))                                            # NY open ref, known from 13:00
        for tf, (atf, tmo) in TFCFG.items():
            av, at = atrs[atf]
            j = int(np.searchsorted(av, s0 + 9 * H, side="right")) - 1
            if j < 20 or not np.isfinite(at[j]):
                continue
            atr = float(at[j])
            b = a.k * atr
            for name, (rh, rl) in fam.items():
                if tf == "1d" and name not in ("swing30", "swing60", "swing90", "prevday"):
                    continue
                rg = rh - rl
                if rg <= 0:
                    continue
                touch_end = i_end if tf != "1d" else int(np.searchsorted(t, s0 + 9 * H + 96 * H))
                fr_list = [(f, "fib_r") for f in FIB_RETR] + [(f, "fib_x") for f in FIB_EXT] + [(f, "edge") for f in EDGE]
                fixed = np.array([f for f, _ in fr_list])
                null = []
                while len(null) < a.nullper:
                    f = rng.uniform(-0.8, 1.8)
                    if np.min(abs(fixed - f)) > 0.02:
                        null.append(f)
                fr_list += [(f, "null") for f in null]
                for f, kind in fr_list:
                    L = rl + f * rg
                    d = abs(L - p0) / atr
                    if L < lo[i_act:touch_end].min() or L > hi[i_act:touch_end].max():
                        continue
                    seg = np.nonzero((lo[i_act:touch_end] <= L) & (hi[i_act:touch_end] >= L))[0]
                    if not len(seg):
                        continue
                    i = i_act + int(seg[0])
                    direction = 1 if cl[i - 1] > L else -1
                    i1 = min(len(t), i + 1 + tmo * 60)
                    r, res = first_passage(hi, lo, cl, i + 1, i1, L, direction, b)
                    # confluence with a session reference already formed at the touch
                    conf = any(np.isfinite(rf) and ri <= i and abs(L - rf) < 0.25 * atr for rf, ri in refs)
                    rows.append((k, tf, name, kind, f, d, r, res, b, int(conf), (t[i] - 22 * H) // H % 24, i, L, direction, i1))
    df = pd.DataFrame(rows, columns=["day", "tf", "fam", "kind", "f", "dist", "R", "resolved", "b", "conf", "hr", "i", "L", "dir", "i1"])
    df["year"] = pd.to_datetime((df["day"] * 24 + 22) * H, unit="ns").dt.year
    out = a.out or a.data
    df.to_parquet(out / "fib_events.parquet")
    print(len(df), "events")
    analyse(df, a.k, out)


BINS = np.array([0, 0.5, 1, 2, 4, 1e9])


def strat_diff(sub: pd.DataFrame, real_mask: np.ndarray, nboot: int = 600, seed: int = 11, cost: float = 0.0):
    """Distance-stratified real minus null mean R (weights = real counts per bin), block bootstrap by 10-day block."""
    rng = np.random.default_rng(seed)
    R = sub["R"].to_numpy() - cost / sub["b"].to_numpy()
    bn = np.clip(np.digitize(sub["dist"].to_numpy(), BINS) - 1, 0, len(BINS) - 2)
    blk = (sub["day"].to_numpy() // 10).astype(int)
    ub, inv = np.unique(blk, return_inverse=True)
    nb = len(bn) and len(BINS) - 1

    def stat(w):
        num_r = np.bincount(bn, weights=w * R * real_mask, minlength=nb)
        n_r = np.bincount(bn, weights=w * real_mask, minlength=nb)
        num_n = np.bincount(bn, weights=w * R * ~real_mask, minlength=nb)
        n_n = np.bincount(bn, weights=w * ~real_mask, minlength=nb)
        ok = (n_r > 0) & (n_n > 0)
        if not ok.any():
            return np.nan, np.nan, np.nan
        mr = num_r[ok] / n_r[ok]
        mn = num_n[ok] / n_n[ok]
        wt = n_r[ok] / n_r[ok].sum()
        return float((wt * (mr - mn)).sum()), float((wt * mr).sum()), float((wt * mn).sum())

    d0, r0, n0 = stat(np.ones(len(R)))
    boots = []
    for _ in range(nboot):
        cnt = np.bincount(rng.integers(0, len(ub), len(ub)), minlength=len(ub))
        boots.append(stat(cnt[inv].astype(float))[0])
    boots = np.array(boots)
    se = float(np.nanstd(boots))
    p = float(2 * min((boots <= 0).mean(), (boots >= 0).mean())) if se > 0 else np.nan
    return d0, r0, n0, se, p


def analyse(df: pd.DataFrame, k: float, out: Path) -> None:
    res = []
    inside = (df["f"] >= 0.1) & (df["f"] <= 0.9)
    outside = (df["f"] < -0.1) | (df["f"] > 1.1)
    for (tf, fam), g in df.groupby(["tf", "fam"]):
        gi, go = inside[g.index], outside[g.index]
        for label, mask_kind, nullsel in (("fib_r", ["fib_r"], gi), ("fib_x", ["fib_x"], go),
                                          ("edge", ["edge"], gi | go)):
            sub = g[g["kind"].isin(mask_kind) | ((g["kind"] == "null") & nullsel)]
            real = sub["kind"].isin(mask_kind).to_numpy()
            if real.sum() < 200:
                continue
            d, r, n, se, p = strat_diff(sub, real)
            rowc = {"tf": tf, "fam": fam, "set": label, "n_real": int(real.sum()), "n_null": int((~real).sum()),
                    "R_real": r, "R_null": n, "diff": d, "se": se, "p": p,
                    "b_usd": float(sub[real]["b"].mean()), "resolved": float(sub[real]["resolved"].mean())}
            for cname, c in (("central", COST_C), ("conservative", COST_K)):
                rowc[f"R_real_net_{cname}"] = strat_diff(sub, real, nboot=1, cost=c)[1]
            for lab, lo_y, hi_y in (("2010-13", 2010, 2013), ("2014-17", 2014, 2017)):
                ss_ = sub[(sub["year"] >= lo_y) & (sub["year"] <= hi_y)]
                rl = ss_["kind"].isin(mask_kind).to_numpy()
                if rl.sum() > 100 and (~rl).sum() > 100:
                    rowc[f"diff_{lab}"] = strat_diff(ss_, rl, nboot=1)[0]
            res.append(rowc)
    r = pd.DataFrame(res)
    # Benjamini-Hochberg over the fib (not edge/fib_r) cells
    m = r[r["set"].isin(["fib_r", "fib_x"])].copy()
    m = m.sort_values("p")
    m["bh_q"] = (m["p"] * len(m) / (np.arange(len(m)) + 1)).iloc[::-1].cummin().iloc[::-1].clip(upper=1)
    r = r.merge(m[["tf", "fam", "set", "bh_q"]], on=["tf", "fam", "set"], how="left")  # q over fib_r and fib_x cells only
    pd.set_option("display.width", 250, "display.max_columns", 30)
    print(r.round(4).to_string())
    r.to_csv(out / f"fib_results_k{k}.csv", index=False)
    # confluence cut (pooled over families, 1h)
    c = []
    for tf in ("15m", "1h", "4h"):
        for cf in (0, 1):
            sub = df[(df["tf"] == tf) & (df["conf"] == cf) & inside & (df["kind"].isin(["fib_r", "null"]))]
            real = (sub["kind"] == "fib_r").to_numpy()
            if real.sum() > 100:
                d, rr, nn, se, p = strat_diff(sub, real)
                c.append({"tf": tf, "confluence": cf, "n_real": int(real.sum()), "R_real": rr, "R_null": nn, "diff": d, "se": se, "p": p})
    print(pd.DataFrame(c).round(4).to_string())
    pd.DataFrame(c).to_csv(out / "fib_confluence.csv", index=False)


if __name__ == "__main__":
    main()
