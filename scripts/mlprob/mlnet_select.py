"""MLNET walk-forward net-R selection and statistics (docs/research/mlprob/mlnet-prereg.md sections 4-5, 8).

    .venv/bin/python scripts/mlprob/mlnet_select.py --window explore --out ~/tmp/mlprob_mlnet/explore.json
    .venv/bin/python scripts/mlprob/mlnet_select.py --window confirm --passers ~/tmp/mlprob_mlnet/explore.json \
        --out ~/tmp/mlprob_mlnet/confirm.json
    .venv/bin/python scripts/mlprob/mlnet_select.py --window test_old --passers ~/tmp/mlprob_mlnet/confirm.json \
        --out ~/tmp/mlprob_mlnet/test_old.json

Reads the pools written by `mlnet_pool.py`; writes aggregate statistics only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.research.metrics import MIN_TRADES_FOR_DSR, DsrInputs  # noqa: E402

POOL = Path.home() / "tmp" / "mlprob_mlnet"
RULES = ["S200-TREND-01", "S200-TREND-03", "S200-TREND-04", "S200-TREND-10", "S200-TREND-15", "S200-TREND-19",
         "S200-TREND-20", "S200-TREND-24", "S200-TREND-28", "S200-TREND-31", "S200-TREND-32"]
NUM = ["side", "cost_R_ex", "log_stop_atrd", "long_swap_days", "hour_sin", "hour_cos", "weekday", "session",
       "d1_align", "ext50", "ret1", "ret5", "ret20", "atrd_pct", "vol_ratio_1h", "adx4", "chan4", "dbreak",
       "cons_same", "cons_opp", "rule_mom", "pool_mom"]
VARIANTS = ["MLNET-V1", "MLNET-V2", "MLNET-V3", "MLNET-V4"]
NAMES = {"MLNET-V1": "RIDGE-POS", "MLNET-V2": "HGB-POS", "MLNET-V3": "HGB-TOP3", "MLNET-V4": "RULEPICK"}
K = 3_237
assert len(NUM) + len(RULES) == 33 <= 40


def ts(y: int) -> int:
    return pd.Timestamp(f"{y}-01-01", tz="UTC").value


# ---------------------------------------------------------------- models
class Fitted:
    def __init__(self, train: pd.DataFrame, shuffle: bool = False) -> None:
        y = train.R.clip(-2, 5).to_numpy()
        if shuffle:
            y = np.random.default_rng(0).permutation(y)
        Xr = self._ridge_X(train)
        self.med = np.nanmedian(Xr, axis=0)
        Xr = np.where(np.isnan(Xr), self.med, Xr)
        self.mu, self.sd = Xr.mean(0), Xr.std(0)
        self.sd[self.sd == 0] = 1
        self.ridge = Ridge(alpha=10.0).fit((Xr - self.mu) / self.sd, y)
        self.hgb = HistGradientBoostingRegressor(max_depth=3, learning_rate=0.03, max_iter=200, min_samples_leaf=80,
                                                 l2_regularization=1.0, random_state=0,
                                                 categorical_features=[0]).fit(self._hgb_X(train), y)
        self.top3 = float(np.quantile(self.hgb.predict(self._hgb_X(train)), 2 / 3))
        g = train.groupby("rule").R.agg(["mean", "size"])
        self.rule_ok = {r for r, row in g.iterrows() if row["size"] >= 30 and row["mean"] > 0}

    @staticmethod
    def _ridge_X(d: pd.DataFrame) -> np.ndarray:
        oh = np.stack([(d.rule == r).to_numpy(float) for r in RULES], axis=1)
        return np.hstack([oh, d[NUM].to_numpy(float)])

    @staticmethod
    def _hgb_X(d: pd.DataFrame) -> np.ndarray:
        ri = d.rule.map({r: i for i, r in enumerate(RULES)}).to_numpy(float)[:, None]
        return np.hstack([ri, d[NUM].to_numpy(float)])

    def score(self, d: pd.DataFrame) -> pd.DataFrame:
        Xr = self._ridge_X(d)
        Xr = np.where(np.isnan(Xr), self.med, Xr)
        p1 = self.ridge.predict((Xr - self.mu) / self.sd)
        p2 = self.hgb.predict(self._hgb_X(d))
        return pd.DataFrame({"p1": p1, "p2": p2, "MLNET-V1": p1 > 0, "MLNET-V2": p2 > 0, "MLNET-V3": p2 >= self.top3,
                             "MLNET-V4": d.rule.isin(self.rule_ok).to_numpy()}, index=d.index)


def walk_forward(train_pool: pd.DataFrame, test: pd.DataFrame, years: list[int], shuffle: bool = False) -> pd.DataFrame:
    out = []
    for y in years:
        tr = train_pool[train_pool.t_exit < ts(y)]
        te = test[(test.t_fill >= ts(y)) & (test.t_fill < ts(y + 1))]
        if te.empty:
            continue
        out.append(pd.concat([te, Fitted(tr, shuffle).score(te)], axis=1))
    return pd.concat(out)


# ---------------------------------------------------------------- statistics
def cl_t(r: np.ndarray, cl: np.ndarray) -> float:
    """Cluster-robust (CR1) t of the mean, clusters = trading day."""
    n = len(r)
    if n < 3:
        return float("nan")
    e = r - r.mean()
    s = pd.Series(e).groupby(cl).sum().to_numpy()
    g = len(s)
    if g < 2:
        return float("nan")
    se = np.sqrt((s ** 2).sum() * g / (g - 1)) / n
    return float(r.mean() / se) if se > 0 else float("nan")


def plain_t(r: np.ndarray) -> float:
    return float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 2 and r.std(ddof=1) > 0 else float("nan")


def day_t(r: np.ndarray, cl: np.ndarray) -> float:
    """W2: t of the mean of DAY-SUMMED net R (days with a selected trade)."""
    return plain_t(pd.Series(r).groupby(cl).sum().to_numpy())


def boot_p(r: np.ndarray, cl: np.ndarray, n: int = 10_000) -> float:
    """W2: one-sided day-block bootstrap p of mean net R <= 0 (resample trading days with replacement, seed 0)."""
    g = pd.DataFrame({"r": r, "c": cl}).groupby("c").r.agg(["sum", "size"])
    s, k = g["sum"].to_numpy(), g["size"].to_numpy()
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(s), size=(n, len(s)))
    means = s[idx].sum(1) / k[idx].sum(1)
    centred = means - means.mean()                           # null-centred bootstrap
    return float((centred >= r.mean()).mean())


def core(sel: pd.DataFrame, uns: pd.DataFrame) -> dict[str, Any]:
    r = sel.R.to_numpy()
    tw = sel.dropna(subset=["twin_R"])
    dr = sel.dropna(subset=["drift_R"])
    yr = sel.groupby(pd.DatetimeIndex(pd.to_datetime(sel.t_fill, utc=True)).year).R.mean()
    yr_u = uns.groupby(pd.DatetimeIndex(pd.to_datetime(uns.t_fill, utc=True)).year).R.mean()
    cl = sel.tday.to_numpy()
    return {
        "n": int(len(sel)), "net_R": float(r.mean()), "t_cluster": cl_t(r, cl), "t_plain": plain_t(r),
        "t_daysum": day_t(r, cl), "gross_R": float(sel.gross_R.mean()), "cost_R": float(sel.cost_R.mean()),
        "stress_net_R": float(sel.R_stress.mean()), "net_R_bps": float(sel.R_bps.mean()),
        "unselected_net_R": float(uns.R.mean()) if len(uns) else float("nan"),
        "vs_pool": float(r.mean() - uns.R.mean()) if len(uns) else float("nan"),
        "twin_net_R": float(tw.twin_R.mean()), "vs_control": float((tw.R - tw.twin_R).mean()),
        "vs_control_bps": float((tw.R_bps - tw.twin_R_bps).mean()),
        "drift_net_R": float(dr.drift_R.mean()), "vs_drift": float((dr.R - dr.drift_R).mean()),
        "drift_adj_gross_R": float((dr.gross_R - dr.drift_gross_R).mean()), "n_drift": int(len(dr)),
        "long_share": float((sel.side > 0).mean()), "hit": float((r > 0).mean()),
        "n_per_rule": sel.rule.value_counts().sort_index().to_dict(),
        "year_net_R": {str(k): round(float(x), 4) for k, x in yr.items()},
        "year_vs_unselected": {str(k): round(float(yr[k] - yr_u[k]), 4) for k in yr.index if k in yr_u.index},
    }


def stat(sc: pd.DataFrame, v: str, window: str) -> dict[str, Any]:
    sel, uns = sc[sc[v]], sc[~sc[v]]
    out: dict[str, Any] = {"id": v, "name": NAMES[v], "pool_n": int(len(sc)), "pool_net_R": float(sc.R.mean())}
    if len(sel) < 3:
        out.update({"n": int(len(sel)), "pass": False, "verdict": "FAIL-COUNT"})
        return out
    out.update(core(sel, uns))
    r = sel.R.to_numpy()
    pcol = "p1" if v == "MLNET-V1" else "p2"
    out["spearman_pred_R"] = float(stats.spearmanr(sc[pcol], sc.R).statistic) if v != "MLNET-V4" else None
    yr = sel.groupby(pd.DatetimeIndex(pd.to_datetime(sel.t_fill, utc=True)).year).R.mean()
    tc = out["t_cluster"]
    if window == "explore":
        ok = len(r) >= 100 and r.mean() > 0 and tc >= 2.0 and out["vs_pool"] >= 0.05 and out["vs_control"] >= 0.05
        out["w2_pass"] = bool(len(r) >= 100 and r.mean() > 0 and out["t_daysum"] >= 2.0 and out["vs_control"] >= 0)
    elif window == "confirm":
        dsr = DsrInputs.of(r).dsr(K) if len(r) >= MIN_TRADES_FOR_DSR else None
        pos = sum(1 for y in range(2018, 2026) if y in yr.index and yr[y] > 0)
        out.update({"dsr": dsr, "dsr_K": K, "years_pos": pos, "boot_p": boot_p(r, sel.tday.to_numpy()),
                    "dsr_inputs": DsrInputs.of(r).model_dump() if len(r) >= MIN_TRADES_FOR_DSR else None})
        ok = (len(r) >= 100 and r.mean() > 0 and tc >= 2.0 and dsr is not None and dsr >= 0.5 and pos >= 6
              and out["vs_control"] > 0)
        y25 = pd.DatetimeIndex(pd.to_datetime(sc.t_fill, utc=True)).year == 2025
        s2 = sc[~y25]
        out["ex2025"] = core(s2[s2[v]], s2[~s2[v]]) if s2[v].sum() >= 3 else None
    else:
        ok = len(r) >= 100 and r.mean() > 0 and out["t_daysum"] >= 1.28 and out["vs_control"] >= 0
    out["pass"] = bool(ok)
    floor = 100
    out["verdict"] = "PASS" if ok else ("FAIL-COUNT" if len(r) < floor else "FAIL")
    return out


def bh(res: list[dict[str, Any]], m: int, q: float = 0.10) -> None:
    """W2 confirm: Benjamini-Hochberg at q over m = the number of explore passers (reported beside the frozen rule)."""
    ps = sorted((r["boot_p"], i) for i, r in enumerate(res) if r.get("boot_p") is not None)
    kmax = max((j + 1 for j, (p, _) in enumerate(ps) if p <= q * (j + 1) / m), default=0)
    for j, (_, i) in enumerate(ps):
        r = res[i]
        r["w2_pass"] = bool(j < kmax and r["net_R"] > 0 and r["n"] >= 100
                            and sum(1 for x in r["year_net_R"].values() if x > 0) >= 5)


def load(w: str) -> pd.DataFrame:
    return pd.read_parquet(POOL / f"pool_{w}.parquet")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", choices=["explore", "confirm", "test_old"], required=True)
    ap.add_argument("--passers", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    variants = VARIANTS
    if a.window != "explore":
        if a.passers is None:
            raise SystemExit("needs --passers (explore result for confirm, confirm result for test_old)")
        src = json.loads(a.passers.read_text())
        need = "explore" if a.window == "confirm" else "confirm"
        if src["window"] != need:
            raise SystemExit(f"{a.window} reads {need} passers only, got a {src['window']} result")
        variants = [r["id"] for r in src["results"] if r.get("pass")]
        if not variants:
            print(f"no passers: {a.window} not run")
            return
    ex = load("explore")
    extra: dict[str, Any] = {}
    if a.window == "explore":
        sc = walk_forward(ex, ex, list(range(2012, 2018)))
        sh = walk_forward(ex, ex, list(range(2012, 2018)), shuffle=True)
        extra["shuffled_label_V2"] = {"n": int(sh["MLNET-V2"].sum()),
                                      "vs_pool": float(sh.R[sh["MLNET-V2"]].mean() - sh.R[~sh["MLNET-V2"]].mean())}
    elif a.window == "confirm":
        cf = load("confirm")
        sc = walk_forward(pd.concat([ex, cf], ignore_index=True), cf, list(range(2018, 2026)))
    else:
        cf = load("confirm")
        train = pd.concat([ex, cf], ignore_index=True)
        train = train[train.t_exit < pd.Timestamp("2025-10-01", tz="UTC").value]
        p = POOL / "pool_test_old.parquet"
        if not p.exists():
            raise SystemExit("TEST-OLD pool missing (pre-2010 data not on disk): old-test pending")
        d = pd.read_parquet(p)
        old = d[(d.t_fill >= pd.Timestamp("2006-02-01", tz="UTC").value) & (d.t_fill < ts(2010))]
        sc = pd.concat([old, Fitted(train).score(old)], axis=1)
    res = [stat(sc, v, a.window) for v in variants]
    if a.window == "confirm":
        bh(res, m=len(variants))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"window": a.window, "K": K, "results": res, **extra}, indent=1, default=float))
    for r in res:
        print(r["id"], r["name"], r["verdict"], {k: (round(x, 4) if isinstance(x, float) else x) for k, x in r.items()
                                                  if k in ("n", "pool_net_R", "net_R", "t_cluster", "t_daysum",
                                                           "vs_pool", "vs_control", "vs_drift", "net_R_bps", "dsr",
                                                           "years_pos", "boot_p", "w2_pass", "spearman_pred_R")})
    if extra:
        print(extra)


if __name__ == "__main__":
    main()
