"""H-82 read (prereg-2026-10-30-hb-exits.md sections 4-5) on the pairs `hb_exits_screen.py` wrote to ~/tmp/hb_exits.
Prints one JSON with the verdict per variant, the family/year breakdown and the descriptive tables."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path.home() / "tmp" / "hb_exits"
FAMILIES = {"S5": ["S5A", "S5B"], "Z4": ["Z4D", "Z4P"], "Z6": ["Z6D"], "Z7": ["Z7D", "Z7P"]}
VARIANTS = ["1R", "1.5R", "2R", "3R", "none"]
M_CTL, B, SEED, BAR = 4, 10_000, 20261030, 0.05
OVERSHOOTS = (0.0, 0.70)
COSTS_R = (0.05, 0.116)


def load() -> pd.DataFrame:
    frames = []
    for fam, ids in FAMILIES.items():
        for i in ids:
            df = pd.read_parquet(OUT / f"pairs_{i}.parquet")
            df["family"] = fam
            frames.append(df)
    return pd.concat(frames, ignore_index=True)


def diffs(df: pd.DataFrame, v: str, os_usd: float, kind: str = "rand", base: str | None = None) -> pd.Series:
    """d_i(V): the arm's change from its baseline (or `base`) minus the mean change of its valid controls, net R
    (round trip cancels; overshoot on stop exits does not)."""
    r = df["risk_usd"].to_numpy(float)
    assert base is not None or df["base"].nunique() == 1, "one baseline per family"
    b0 = base or str(df["base"].iloc[0])
    a_v = df[f"a_{v}"].to_numpy(float) - os_usd / r * df[f"a_{v}_stop"].to_numpy(float)
    a_b = df[f"a_{b0}"].to_numpy(float)
    a_bs = df[f"a_{b0}_stop"].to_numpy(float)
    arm = a_v - (a_b - os_usd / r * a_bs)
    ctl = np.zeros(len(df))
    cnt = np.zeros(len(df))
    for k in range(M_CTL):
        ok = df[f"c{k}_ok"].to_numpy(bool)
        cv = df[f"c{k}_{kind}_{v}"].to_numpy(float) - os_usd / r * df[f"c{k}_{kind}_{v}_stop"].to_numpy(float)
        cb = df[f"c{k}_{kind}_{b0}"].to_numpy(float) - os_usd / r * df[f"c{k}_{kind}_{b0}_stop"].to_numpy(float)
        ctl += np.where(ok, cv - cb, 0.0)
        cnt += ok
    with np.errstate(invalid="ignore", divide="ignore"):
        return pd.Series(np.where(cnt > 0, arm - ctl / cnt, np.nan), index=df.index)


def read_variant(df: pd.DataFrame, v: str, os_usd: float, kind: str = "rand", base: str | None = None,
                 boot: bool = True) -> dict:
    fams = []
    parts = []
    for fam, ids in FAMILIES.items():
        sub = df[df["family"] == fam].reset_index(drop=True)
        b = sub["base"].iloc[0] if base is None else base
        if b == v:
            continue
        d = diffs(sub, v, os_usd, kind, base)
        keep = d.notna().to_numpy()
        fams.append(fam)
        parts.append(pd.DataFrame({"fam": fam, "tday": sub["tday"].to_numpy()[keep], "year": sub["ts"].dt.year.to_numpy()[keep],
                                   "d": d.to_numpy()[keep]}))
    if not fams:
        return {}
    p = pd.concat(parts, ignore_index=True)
    fam_mean = {f: float(p.loc[p["fam"] == f, "d"].mean()) for f in fams}
    pooled = float(np.mean(list(fam_mean.values())))
    years = {}
    for y, g in p.groupby("year"):
        years[int(y)] = float(np.mean([g.loc[g["fam"] == f, "d"].mean() for f in fams if (g["fam"] == f).any()]))
    out = {"families": fams, "n_pairs": {f: int((p["fam"] == f).sum()) for f in fams}, "family_did": fam_mean,
           "pooled_did": pooled, "years_positive": int(sum(x > 0 for x in years.values())), "years": years,
           "n_dropped_no_control": int(sum(len(df[df["family"] == f]) for f in fams) - len(p))}
    if boot:
        days, di = np.unique(p["tday"].to_numpy(), return_inverse=True)
        D = len(days)
        S = np.zeros((D, len(fams)))
        N = np.zeros((D, len(fams)))
        fi = pd.Categorical(p["fam"], categories=fams).codes
        np.add.at(S, (di, fi), p["d"].to_numpy())
        np.add.at(N, (di, fi), 1.0)
        rng = np.random.default_rng(SEED)
        stats = np.empty(B)
        for s0 in range(0, B, 500):
            draws = rng.integers(0, D, size=(min(500, B - s0), D))
            W = np.stack([np.bincount(row, minlength=D) for row in draws]).astype(float)
            stats[s0:s0 + len(W)] = ((W @ S) / (W @ N)).mean(1)
        out.update({"ci90": [float(np.quantile(stats, 0.05)), float(np.quantile(stats, 0.95))],
                    "lb_bonf5_one_sided_2pct": float(np.quantile(stats, 0.02)),
                    "boot_se": float(stats.std())})
    return out


def verdict(r0: dict, r70: dict) -> str:
    if not r0:
        return "n/a"
    fams = r0["families"]
    sign_ok = sum(np.sign(x) == np.sign(r0["pooled_did"]) for x in r0["family_did"].values()) >= len(fams) - 1
    core0 = r0["pooled_did"] >= BAR and r0["ci90"][0] > 0
    core70 = r70["pooled_did"] >= BAR and r70["ci90"][0] > 0
    if core0 and core70 and r0["lb_bonf5_one_sided_2pct"] > 0 and sign_ok and r0["years_positive"] >= 5:
        return "PASS"
    if core0:
        return "INCONCLUSIVE"
    return "FAIL"


def describe(df: pd.DataFrame) -> dict:
    """Absolute net R per arm and variant (cost 0.05 R and 0.116 R, overshoot 0), random controls, exit mix, MFE."""
    out = {}
    for arm, g in df.groupby("arm"):
        row = {"n": len(g), "base": g["base"].iloc[0], "short_share": float((g["side"] < 0).mean()),
               "mfe_median_R": float(g["mfe"].median()),
               "share_mfe_ge": {str(x): float((g["mfe"] >= x).mean()) for x in (1.0, 1.5, 2.0, 3.0)}}
        for v in VARIANTS:
            gross = float(g[f"a_{v}"].mean())
            ctl = np.nanmean(np.column_stack([np.where(g[f"c{k}_ok"], g[f"c{k}_rand_{v}"], np.nan) for k in range(M_CTL)]))
            row[v] = {"arm_gross": gross, "arm_net_0.05": gross - COSTS_R[0], "arm_net_0.116": gross - COSTS_R[1],
                      "ctl_gross": float(ctl), "arm_stop_share": float(g[f"a_{v}_stop"].mean())}
        out[arm] = row
    return out


def main() -> None:
    df = load()
    res: dict = {"n_pairs_total": len(df), "variants": {}, "vs_1R_secondary": {}, "side_matched_diag": {}}
    for v in VARIANTS:
        r0 = read_variant(df, v, OVERSHOOTS[0])
        r70 = read_variant(df, v, OVERSHOOTS[1])
        res["variants"][v] = {"overshoot_0": r0, "overshoot_0.70": r70, "verdict": verdict(r0, r70)}
    for v in ("1.5R", "2R", "3R", "none"):
        res["vs_1R_secondary"][v] = read_variant(df, v, 0.0, base="1R")
        res["side_matched_diag"][v] = read_variant(df, v, 0.0, kind="match", boot=False)
    res["describe"] = describe(df)
    (OUT / "read.json").write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
