"""Diagnostic only (no verdict, no new trial): split each matched-control draw by whether its decision bar is before or
after the real signal bar in the same day x session, explore window only.

Usage: python scripts/sprint200/patterns_ctrl_diag.py <raw folder> S200-PATTERNS-22 ...  (after the explore run)"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import patterns_screen as P  # noqa: E402

ctx = P.init_ctx(Path(sys.argv[1]), "explore")
P.CTX = ctx
costs = P.A.load_costs(P.ROOT / "config" / "arm_costs.yaml")
swap = P.SwapSpec.shipped()
for sid in sys.argv[2:]:
    s = P.BY_ID[sid]
    F = ctx.frames[s.tf]
    lab = ctx.labels[s.tf]
    tr = pd.read_parquet(P.OUT / f"explore__{sid}.parquet")
    day = F.close_ns // P.DAY_NS
    rng = np.random.default_rng(7)
    ml, ms = P.oriented(ctx.m1, 1), P.oriented(ctx.m1, -1)
    before: list[dict] = []
    after: list[dict] = []
    for i, side, d in tr[["i", "side", "d"]].itertuples(index=False):
        pool = np.flatnonzero((day == day[i]) & (lab == lab[i]))
        pool = pool[pool != i]
        if not len(pool):
            continue
        atr = float(F.atr[i])
        tgt = np.nan
        for j in rng.choice(pool, size=min(4, len(pool)), replace=False):
            r = P.trade_at(ctx, F, s, int(side), int(j), float(d), atr, tgt, ml if side > 0 else ms)
            if r is not None:
                (before if j < i else after).append(r)
    for name, rows in (("before", before), ("after", after)):
        p = P.price(rows, costs, swap)
        print(sid, name, len(p), round(float(p["gross_r"].mean()), 3), round(float(p["net_r"].mean()), 3))
