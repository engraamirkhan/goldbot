"""Slice C follow-up: does the first touch of a 30/60/90-day extreme continue, once the limit-at-level fill is removed?
Reads fib_events.parquet written by ts_fib.py. Entry = close of the 1m bar 15 minutes after the first touch (a market
order after confirmation), continuation direction, barriers +-0.5 ATR(1d) and timeout 5 days (as in ts_fib.py).
Also prints the lag sweep (0, 1, 5, 15 min) that shows the intraday 'edge' is a touch-minute artefact.
Usage: uv run --with scipy python docs/research/candidates/time_structure/ts_fib_breakout_check.py --data DIR
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ts_fib import first_passage  # noqa: E402


def run(g: pd.DataFrame, hi, lo, cl, lag: int) -> np.ndarray:
    n = len(cl)
    out = []
    for r in g.itertuples():
        j = r.i + lag
        i1 = min(n, r.i1 + lag)
        if j + 2 >= i1:
            out.append(np.nan)
            continue
        ent = r.L if lag == 0 else cl[j]
        out.append(first_passage(hi, lo, cl, j + 1, i1, ent, -r.dir, r.b)[0])
    return np.array(out)


def block_t(x: np.ndarray, blk: np.ndarray) -> tuple[float, float, int, int]:
    ok = ~np.isnan(x)
    x, blk = x[ok], blk[ok]
    mu = x.mean()
    s = pd.Series(x - mu).groupby(blk).sum()
    g = len(s)
    se = np.sqrt((s ** 2).sum()) / len(x) * np.sqrt(g / (g - 1))
    return float(mu), float(mu / se), len(x), g


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    a = ap.parse_args()
    m = pd.read_parquet(a.data / "mid_1m.parquet")
    hi, lo, cl = (m[c].to_numpy() for c in ("high", "low", "close"))
    d = pd.read_parquet(a.data / "fib_events.parquet")
    e = d[d.kind == "edge"]
    print("lag sweep: continuation R (t, 10-day-block clustered) by entry delay in minutes")
    for tf in ("1h", "1d"):
        for fam in ("prevday", "swing30", "swing60", "swing90"):
            g = e[(e.tf == tf) & (e.fam == fam)].sort_values("day")
            cells = []
            for lag in (0, 1, 5, 15):
                mu, t, n, _ = block_t(run(g, hi, lo, cl, lag), (g.day // 10).to_numpy())
                cells.append(f"{lag}m {mu:+.3f} (t {t:+.1f})")
            print(f"{tf} {fam} n={len(g)}: " + " | ".join(cells))
    for fam in ("swing30", "swing60", "swing90"):
        g = e[(e.tf == "1d") & (e.fam == fam)].sort_values("day").copy()
        g["R15"] = run(g, hi, lo, cl, 15)
        blk = (g.day // 10).to_numpy()
        keep, last = [], -99
        for r in g.itertuples():
            keep.append(r.day - last >= 5)
            if keep[-1]:
                last = r.day
        gk = g[np.array(keep)]
        print(fam, "all", block_t(g.R15.to_numpy(), blk), "| non-overlapping 5d", block_t(gk.R15.to_numpy(), (gk.day // 10).to_numpy()),
              "| mean $/oz", round(float((gk.R15 * gk.b).mean()), 2), "b $", round(float(gk.b.mean()), 2))
        for nm, sel in (("break high", g.dir == -1), ("break low", g.dir == 1)):
            x = g[sel]
            print("   ", nm, block_t(x.R15.to_numpy(), (x.day // 10).to_numpy()))
        print("    by year", g.groupby("year").R15.mean().round(2).to_dict())


if __name__ == "__main__":
    main()
