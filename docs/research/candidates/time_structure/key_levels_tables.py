"""Tables for docs/research/time-structure/key-levels.md from the parquet files written by key_levels_discovery.py.

Cells: (tf, family or condition, strategy, exit). Per cell: real vs control gross R (day-block bootstrap of the
difference), net R under the research prior (bar spread + 0.37 $/oz), the measured-fill cost (0.16 $/oz: spread 0.10 +
commission 0.06) on the 2010-2017 stops, and the same measured cost at today's ATR (15m 8, 1h 16, 4h 32 $; same R
multiples). Usage: uv run python docs/research/candidates/time_structure/key_levels_tables.py --in ~/tmp/key_levels --out ~/tmp/key_levels
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

TODAY = {"15min": 8.0, "1h": 16.0, "4h": 32.0}
MEAS = 0.16
rng = np.random.default_rng(7)


def add_costs(t: pd.DataFrame) -> pd.DataFrame:
    t = t.copy()
    t["c_prior"] = (t["spr"] + 0.37) / t["risk"]
    t["c_meas"] = MEAS / t["risk"]
    t["c_today"] = MEAS / (t["risk"] / t["atr"] * t["tf"].map(TODAY))
    t["risk_atr"] = t["risk"] / t["atr"]
    for e in ("r2", "rpt"):
        t[f"{e}_np"] = t[e] - t["c_prior"]
        t[f"{e}_nm"] = t[e] - t["c_meas"]
        t[f"{e}_nt"] = t[e] - t["c_today"]
    return t


def cr1_t(x: np.ndarray, day: np.ndarray) -> float:
    n = len(x)
    if n < 10:
        return float("nan")
    m = x.mean()
    s = pd.Series(x - m).groupby(day).sum()
    g = len(s)
    var = (g / (g - 1)) * (s**2).sum() / n**2
    return float(m / np.sqrt(var)) if var > 0 else float("nan")


def boot_diff(real: pd.DataFrame, ctrl: pd.DataFrame, col: str, reps: int = 400) -> tuple[float, float]:
    r = real.groupby("k")[col].agg(["sum", "count"])
    c = ctrl.groupby("k")[col].agg(["sum", "count"])
    days = r.index.union(c.index)
    r, c = r.reindex(days, fill_value=0), c.reindex(days, fill_value=0)
    rs, rc, cs, cc = r["sum"].to_numpy(), r["count"].to_numpy(), c["sum"].to_numpy(), c["count"].to_numpy()
    idx = rng.integers(0, len(days), size=(reps, len(days)))
    est = rs[idx].sum(1) / np.maximum(rc[idx].sum(1), 1) - cs[idx].sum(1) / np.maximum(cc[idx].sum(1), 1)
    return float(np.percentile(est, 2.5)), float(np.percentile(est, 97.5))


def cell(t: pd.DataFrame, exit_: str, boot: bool = True) -> dict | None:
    real, ctrl = t[t.kind == 0], t[t.kind == 1]
    if len(real) < 30 or len(ctrl) < 30:
        return None
    g = exit_
    out = {"n": len(real), "n_ctrl": len(ctrl), "gross": real[g].mean(), "t": cr1_t(real[g].to_numpy(), real["k"].to_numpy()),
           "ctrl_gross": ctrl[g].mean(), "diff": real[g].mean() - ctrl[g].mean(),
           "net_prior": real[f"{g}_np"].mean(), "ctrl_net_prior": ctrl[f"{g}_np"].mean(),
           "net_meas": real[f"{g}_nm"].mean(), "net_today": real[f"{g}_nt"].mean(),
           "ctrl_net_meas": ctrl[f"{g}_nm"].mean(),
           "risk_usd_med": real["risk"].median(), "risk_atr_med": real["risk_atr"].median(),
           "cost_prior_med": real["c_prior"].median(), "cost_meas_med": real["c_meas"].median()}
    if boot and len(real) >= 200:
        lo, hi = boot_diff(real, ctrl, g)
        out["diff_lo"], out["diff_hi"] = lo, hi
        lo, hi = boot_diff(real, ctrl, f"{g}_nm")
        out["diffm_lo"], out["diffm_hi"] = lo, hi
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    t = add_costs(pd.read_parquet(a.inp / "trades.parquet"))
    to = pd.read_parquet(a.inp / "touches.parquet")
    rows = []
    for (tf, fam, strat), g in t.groupby(["tf", "fam", "strat"]):
        for ex in ("r2", "rpt"):
            c = cell(g, ex)
            if c:
                rows.append({"tf": tf, "fam": fam, "strat": strat, "exit": ex, **c})
    for (tf, strat), g in t.groupby(["tf", "strat"]):
        for ex in ("r2", "rpt"):
            c = cell(g, ex)
            if c:
                rows.append({"tf": tf, "fam": "ALL", "strat": strat, "exit": ex, **c})
    pd.DataFrame(rows).to_csv(a.out / "cells_family.csv", index=False)
    # touch classification and P(touch)
    rows = []
    for (tf, fam), g in to.groupby(["tf", "fam"]):
        for kind in (0, 1):
            x = g[g.kind == kind]
            tt = x[x.touched == 1]
            rows.append({"tf": tf, "fam": fam, "kind": kind, "n_levels": len(x), "p_touch": x.touched.mean(),
                         "break": (tt.cat == 0).mean(), "sweep": (tt.cat == 1).mean(), "reject": (tt.cat == 2).mean(),
                         "stall": (tt.cat == 3).mean(), "ft8_break_dir_atr": tt.ft.mean(), "n_touch": len(tt)})
    pd.DataFrame(rows).to_csv(a.out / "touch_class.csv", index=False)
    # conditioning (break-close s0.5 and FADE, fixed 2R exit and partial/trail)
    rows = []
    for strat in ("BRK_s0.5", "BRK_s1.0", "FADE", "RET_s1.0"):
        s = t[(t.strat == strat)].copy()
        s["rs_bin"] = pd.cut(s["rs"], [0, 0.4, 0.8, 1.2, 99], labels=["<0.4", "0.4-0.8", "0.8-1.2", ">1.2"])
        s["dist_bin"] = pd.cut(s["dist"], [0, 0.3, 0.6, 1.0, 99], labels=["<0.3", "0.3-0.6", "0.6-1.0", ">1.0"])
        s["sess_n"] = s["sess"].map({0: "asia", 1: "london", 2: "ny"})
        s["agree_n"] = s["agree"].map({1.0: "with_daily_trend", 0.0: "against_daily_trend"})
        for cond in ("sess_n", "rs_bin", "dist_bin", "agree_n"):
            for (tf, v), g in s.groupby(["tf", cond], observed=True):
                for ex in ("r2", "rpt"):
                    c = cell(g, ex, boot=True)
                    if c:
                        rows.append({"strat": strat, "tf": tf, "cond": cond, "val": v, "exit": ex, **c})
    pd.DataFrame(rows).to_csv(a.out / "cells_cond.csv", index=False)
    # stop-size distribution
    q = t[t.kind == 0].groupby(["tf", "strat"]).agg(risk_p10=("risk", lambda x: x.quantile(.1)),
                                                      risk_med=("risk", "median"), risk_p90=("risk", lambda x: x.quantile(.9)),
                                                      atr_med=("risk_atr", "median"), cost_prior_med=("c_prior", "median"),
                                                      cost_meas_med=("c_meas", "median"), n=("risk", "size"))
    q.to_csv(a.out / "stop_dist.csv")
    print(len(pd.DataFrame(rows)), "conditioning cells;", "done")


if __name__ == "__main__":
    main()
