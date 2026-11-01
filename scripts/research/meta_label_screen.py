"""Meta-label filter screen ML-M1 / ML-M2 (frozen rule: docs/research/prereg-ml-meta-label-2026-11-01.md).

2010-2017 only; the loader asserts no 2018+ bar, positioning row or calendar row. Records nothing in any registry and
writes statistics only (R units) to --out; nothing is committed. Order: lookahead test (stops on any mismatch) ->
labels -> shuffled-label validity gate -> the one read.

    .venv/bin/python scripts/research/meta_label_screen.py --raw raw/ \
        --positioning raw/positioning/positioning.parquet --cal ~/tmp/ts-data/cal/us_calendar.parquet \
        --out ~/tmp/meta_label/result.json --workers 4
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.data.store import asof_join  # noqa: E402
from goldbot.data.timeutil import epoch_ns, utc_index  # noqa: E402
from goldbot.features import positioning as P  # noqa: E402
from goldbot.features import us_calendar as C  # noqa: E402
from goldbot.features.registry import build_features, side_align  # noqa: E402
from goldbot.research import arm_rules as R  # noqa: E402
from goldbot.research import arms as A  # noqa: E402
from goldbot.research import meta_label_screen as M  # noqa: E402
from goldbot.research.model import check_feature_cap  # noqa: E402
from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES  # noqa: E402

END = pd.Timestamp("2018-01-01", tz="UTC")
COST_VERSION, BROKER = "cor-2026-10-31.2", "icm"
MODELS = {"M1": "I0", "M2": "I2B", "M1c": "I0"}
# model -> (decision timeframe, time limit in decision bars, context columns, control alpha, side leg (g))
SPECS: dict[str, tuple[str, int, dict[str, tuple[str, ...]], float, bool]] = {
    "M1": ("15m", 96, M.CONTEXT_FEATURES, M.ALPHA_CONTROL, False),
    "M2": ("15m", 96, M.CONTEXT_FEATURES, M.ALPHA_CONTROL, False),
    "M1c": ("1h", 24, M.CONTEXT_FEATURES_1H, M.ALPHA_SINGLE, True),    # prereg-ml-meta-label-m1c-2026-11-01.md
}
BOTH_SIDES = ("M1", "M1c")
MAX_WALL_NS = 26 * 3600 * 10**9
N_BOOT, BOOT_SEED, N_CTRL, CTRL_SEED, SHUF_SEED = 10_000, 20261101, 2_000, 20261102, 20261103
N_CUTS, CUT_ROWS = 12, 50
GBP_PER_USD = 0.75                     # settings lot_fit_acct_per_usd
MIN_NS = 60 * 10**9


# ---------------------------------------------------------------- data
def load_inputs(raw: Path, pos_path: Path, cal_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    b1 = read_bar_files(raw, 2010, 2017)
    b1 = b1[utc_index(b1["ts_utc"]) < END].reset_index(drop=True)
    assert utc_index(b1["ts_utc"]).max() < END, "a 2018+ bar was read"
    pos = pd.read_parquet(pos_path)
    pos = pos[pd.to_datetime(pos["available_utc"], utc=True) < END].reset_index(drop=True)
    cal = pd.read_parquet(cal_path)
    cal = cal[pd.to_datetime(cal["available_utc"], utc=True) < END].reset_index(drop=True)
    assert pd.to_datetime(pos["available_utc"], utc=True).max() < END
    assert pd.to_datetime(cal["available_utc"], utc=True).max() < END
    return b1, pos, cal


def feature_frame(b1: pd.DataFrame, pos: pd.DataFrame, cal: pd.DataFrame, tf: str = "15m",
                  context: dict[str, tuple[str, ...]] = M.CONTEXT_FEATURES) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(decision bars at `tf`, their 39 market columns as known at each bar's close, aligned to the bars' index)."""
    b15 = resample_bars(b1, tf).reset_index(drop=True)
    m15 = mid(b15).reset_index(drop=True)
    names = list(DEFAULT_FEATURE_NAMES) + [P.enable()] + C.enable()[:1]
    X = build_features(m15, names, ctx={"positioning": pos, "us_calendar": cal})
    out = X[list(M.DECISION_FEATURES) + list(M.POSITIONING_FEATURES) + list(M.CALENDAR_FEATURES)].copy()
    probe = pd.DataFrame({"ts_utc": utc_index(b15["visible_at"]).as_unit("ns"), "_row": np.arange(len(b15))})
    for ctf, cols in context.items():
        hb = resample_bars(b1, ctf).reset_index(drop=True)
        hf = build_features(mid(hb).reset_index(drop=True), list(DEFAULT_FEATURE_NAMES))
        pre = M.CONTEXT_PREFIX[ctf]
        right = pd.DataFrame({f"{pre}_{c}": hf[c].to_numpy() for c in cols})
        right["available_utc"] = utc_index(hb["visible_at"]).as_unit("ns")
        j = asof_join(probe, right).sort_values("_row")
        for c in cols:
            out[f"{pre}_{c}"] = j[f"{pre}_{c}"].to_numpy()
    return b15, out


def _cut_check(args: tuple[str, str, str, int, str]) -> dict[str, Any]:
    """Rebuild the features on the 1m series cut at decision close `cut_ns`; compare the last CUT_ROWS decision bars
    with the full-series values (saved by the parent)."""
    raw, pos_path, cal_path, cut_ns, model = args
    b1, pos, cal = load_inputs(Path(raw), Path(pos_path), Path(cal_path))
    cut = pd.Timestamp(cut_ns, tz="UTC")
    b1c = b1[utc_index(b1["ts_utc"]) < cut].reset_index(drop=True)
    b15, F = feature_frame(b1c, pos, cal, SPECS[model][0], SPECS[model][2])
    vis = utc_index(b15["visible_at"])
    keep = np.flatnonzero(vis <= cut)[-CUT_ROWS:]
    F = F.iloc[keep].copy()
    F["visible_at"] = vis[keep]
    return {"cut": cut_ns, "frame": F}


def lookahead_test(raw: Path, pos_path: Path, cal_path: Path, b15: pd.DataFrame, F: pd.DataFrame,
                   workers: int, model: str = "M1") -> dict[str, Any]:
    vis = utc_index(b15["visible_at"])
    qs = np.linspace(0.06, 0.98, N_CUTS)
    cuts = [int(epoch_ns(pd.DatetimeIndex([vis[int(q * (len(vis) - 1))]]))[0]) for q in qs]
    full = F.copy()
    full["visible_at"] = vis
    full = full.set_index("visible_at")
    bad: list[dict[str, Any]] = []
    with ProcessPoolExecutor(workers) as ex:
        for res in ex.map(_cut_check, [(str(raw), str(pos_path), str(cal_path), c, model) for c in cuts]):
            part = res["frame"].set_index("visible_at")
            ref = full.loc[part.index, part.columns]
            a, b = part.to_numpy(float), ref.to_numpy(float)
            same = (np.isnan(a) & np.isnan(b)) | (np.abs(a - b) <= 1e-9)
            if not same.all():
                cols = [part.columns[k] for k in np.flatnonzero(~same.all(0))]
                bad.append({"cut": str(pd.Timestamp(res["cut"], tz="UTC")), "columns": cols})
    return {"cuts": len(cuts), "rows_per_cut": CUT_ROWS, "mismatches": bad, "ok": not bad}


# ---------------------------------------------------------------- candidates and labels
def candidates(model: str, b15: pd.DataFrame, b1: pd.DataFrame) -> pd.DataFrame:
    arm = next(a for a in A.load_arms(ROOT / "config" / "exploration_arms.yaml") if a.id == MODELS[model])
    if model == "M1c":      # the I0 rule code on 1h bars, 24-bar time limit (prereg M1c s.1)
        arm = arm.model_copy(update={"timeframe": "1h", "params": {**arm.params, "hold_bars": 24}})
    z = np.zeros(0, dtype=np.int64)
    c = R.candidates(arm, R.RuleContext(bars=b15, b1=b1, blackout_lo=z, blackout_hi=z))
    c = c[["idx", "side", "risk_usd", "target_r"]].copy()
    if model in BOTH_SIDES:     # every scheduled decision both ways; the model chooses the side
        c = pd.concat([c.assign(side=1), c.assign(side=-1)], ignore_index=True)
    return c.sort_values(["idx", "side"]).reset_index(drop=True)


def _walk_chunk(args: tuple[Any, ...]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return M.walk(*args)


def label(c: pd.DataFrame, b15: pd.DataFrame, b1: pd.DataFrame, costs: A.ArmCosts, workers: int,
          hold: int = 96) -> pd.DataFrame:
    close = epoch_ns(utc_index(b15["visible_at"]))
    t1 = epoch_ns(utc_index(b1["ts_utc"]))
    idx = c["idx"].to_numpy(int)
    ok = idx + hold < len(close)
    tb = np.where(ok, close[np.minimum(idx + hold, len(close) - 1)], 0)
    k0 = np.searchsorted(t1, close[idx], side="left")
    k1 = np.searchsorted(t1, tb, side="left")
    ok &= (tb - close[idx] <= MAX_WALL_NS) & (k1 > k0) & (k1 <= len(t1) - 1)
    c = c[ok].reset_index(drop=True)
    k0, k1 = k0[ok], k1[ok]
    o, h, lo, cl = ((b1[f"bid_{k}"].to_numpy(float) + b1[f"ask_{k}"].to_numpy(float)) / 2
                    for k in ("open", "high", "low", "close"))
    side, risk, tgt = c["side"].to_numpy(int), c["risk_usd"].to_numpy(float), c["target_r"].to_numpy(float)
    parts = np.array_split(np.arange(len(c)), max(1, workers * 4))
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(_walk_chunk, [(o, h, lo, cl, k0[p], k1[p], side[p], risk[p], tgt[p]) for p in parts]))
    gross = np.concatenate([r[0] for r in res])
    exit_k = np.concatenate([r[1] for r in res])
    stop = np.concatenate([r[2] for r in res])
    entry_ns = t1[k0]
    exit_ns = t1[exit_k] + MIN_NS
    mod = (entry_ns // MIN_NS) % 1440
    sess = np.where(mod < 7 * 60, "asia", np.where(mod < 12 * 60 + 30, "london", "newyork"))
    rt, os_ = costs.screen_charges(BROKER, sess, COST_VERSION)
    nights = M.rollovers(entry_ns, exit_ns)
    c["dec_ns"] = close[c["idx"].to_numpy(int)]
    c["exit_ns"] = exit_ns
    c["gross"] = gross
    c["stop"] = stop
    c["net"] = M.net_r(gross, stop, side, risk, rt, os_, nights)
    c["net_x15"] = M.net_r(gross, stop, side, risk, rt, os_, nights, mult=1.5)
    c["year"] = pd.DatetimeIndex(pd.to_datetime(c["dec_ns"], utc=True)).year.to_numpy()
    c["week"] = (c["dec_ns"].to_numpy() + 3 * 86400 * 10**9) // (7 * 86400 * 10**9)    # Monday-start weeks
    return c


# ---------------------------------------------------------------- model
def _fit_predict(args: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int | None]) -> np.ndarray:
    import lightgbm as lgb
    X, y, train, test, shuffle_seed = args
    yt = y[train].copy()
    if shuffle_seed is not None:
        np.random.default_rng(shuffle_seed).shuffle(yt)
    mdl = lgb.LGBMRegressor(**M.MODEL_PARAMS)
    mdl.fit(X[train], yt)
    out = np.full(len(y), np.nan)
    out[test] = mdl.predict(X[test])
    return out


def run_folds(X: np.ndarray, y: np.ndarray, folds: list[tuple[int, np.ndarray, np.ndarray]], workers: int,
              shuffle: bool = False) -> np.ndarray:
    jobs = [(X, y, tr, te, (SHUF_SEED + yr) if shuffle else None) for yr, tr, te in folds]
    pred = np.full(len(y), np.nan)
    with ProcessPoolExecutor(workers) as ex:
        for p in ex.map(_fit_predict, jobs):
            m = np.isfinite(p)
            pred[m] = p[m]
    return pred


def importances(X: np.ndarray, y: np.ndarray, cols: list[str]) -> list[tuple[str, float]]:
    import lightgbm as lgb
    mdl = lgb.LGBMRegressor(**{**M.MODEL_PARAMS, "importance_type": "gain"}).fit(X, y)
    g = mdl.feature_importances_ / max(mdl.feature_importances_.sum(), 1e-12)
    return sorted(((c, round(float(v), 4)) for c, v in zip(cols, g)), key=lambda t: -t[1])[:10]


def read(c: pd.DataFrame, pred: np.ndarray, pred_fwd: np.ndarray, pred_shuf: np.ndarray,
         alpha: float = M.ALPHA_CONTROL, with_side_leg: bool = False) -> dict[str, Any]:
    from scipy.stats import spearmanr
    from sklearn.metrics import roc_auc_score
    net, gross, years, side = (c[k].to_numpy() for k in ("net", "gross", "year", "side"))
    pool = np.isfinite(pred)
    sel = M.top_decile(pred, years)
    boot = M.week_block_bootstrap(net[sel], c["week"].to_numpy()[sel], N_BOOT, BOOT_SEED)
    ctrl = M.matched_control(net, years, side, sel, pool, N_CTRL, CTRL_SEED)
    obs = float(net[sel].mean())
    p_ctrl = float((1 + (ctrl >= obs).sum()) / (1 + len(ctrl)))
    ym = {int(y): (float(net[sel & (years == y)].mean()), int((sel & (years == y)).sum())) for y in np.unique(years)}
    fsel = M.top_decile(pred_fwd, years)
    fwd_mean = float(net[fsel].mean()) if fsel.any() else float("nan")
    fctrl = M.matched_control(net, years, side, fsel, np.isfinite(pred_fwd), N_CTRL, CTRL_SEED) if fsel.any() else None
    ssel = M.top_decile(pred_shuf, years)
    sctrl = M.matched_control(net, years, side, ssel, pool, N_CTRL, CTRL_SEED)
    s_obs = float(net[ssel].mean())
    p_shuf = float((1 + (sctrl >= s_obs).sum()) / (1 + len(sctrl)))
    sides = M.side_leg(net, side, sel)
    v = M.verdict(len(c), net[sel], float(np.quantile(boot, 0.05)), p_ctrl, ym, fwd_mean, alpha=alpha,
                  side_ok=sides["ok"] if with_side_leg else None)
    deciles = []
    for y in np.unique(years):
        m = years == y
        q = np.quantile(pred[m], np.linspace(0, 1, 11)[1:-1])
        dec = np.searchsorted(q, pred[m], side="right")
        deciles.append(pd.DataFrame({"d": dec, "net": net[m], "gross": gross[m]}))
    dt = pd.concat(deciles).groupby("d").agg(n=("net", "size"), net=("net", "mean"), gross=("gross", "mean"))
    ic = {int(y): float(spearmanr(pred[years == y], net[years == y]).statistic) for y in np.unique(years)}
    auc = float(roc_auc_score(net > 0, pred))
    oaat = M.one_at_a_time(c["dec_ns"].to_numpy()[sel], c["exit_ns"].to_numpy()[sel])
    sel_risk = c["risk_usd"].to_numpy()[sel]
    usd = net[sel] * sel_risk
    return {
        "n_candidates": int(len(c)), "n_selected": int(sel.sum()),
        "pool_net_mean": float(net.mean()), "pool_gross_mean": float(gross.mean()),
        "top_net_mean": obs, "top_net_lb90": float(np.quantile(boot, 0.05)), "top_gross_mean": float(gross[sel].mean()),
        "top_net_x15_mean": float(c["net_x15"].to_numpy()[sel].mean()),
        "control_mean": float(ctrl.mean()), "control_q975": float(np.quantile(ctrl, 0.975)), "p_control": p_ctrl,
        "year_means": {str(k): v_ for k, v_ in ym.items()},
        "forward_top_net_mean": fwd_mean, "forward_n": int(fsel.sum()),
        "forward_control_mean": float(fctrl.mean()) if fctrl is not None else None,
        "forward_p_control": float((1 + (fctrl >= fwd_mean).sum()) / (1 + len(fctrl))) if fctrl is not None else None,
        "shuffle_top_net_mean": s_obs, "shuffle_p_control": p_shuf, "void_leakage": bool(p_shuf <= alpha),
        "top_by_side": sides,
        "long_share_top": float((side[sel] > 0).mean()), "long_share_pool": float((side > 0).mean()),
        "stop_share_top": float(c["stop"].to_numpy()[sel].mean()),
        "median_risk_usd_top": float(np.median(sel_risk)),
        "usd_per_trade_001lot": float(usd.mean()), "gbp_per_trade_001lot": float(usd.mean() * GBP_PER_USD),
        "one_at_a_time": {"n": int(oaat.sum()), "net_mean": float(net[sel][oaat].mean()) if oaat.any() else None},
        "rank_ic_by_year": ic, "auc_net_pos": auc, "deciles": dt.reset_index().to_dict("records"), **v,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--positioning", type=Path, required=True)
    ap.add_argument("--cal", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry", action="store_true", help="counts and feature coverage only: no label statistic, no fit")
    ap.add_argument("--models", nargs="+", default=["M1", "M2"], choices=list(MODELS),
                    help="M1 M2 (prereg-ml-meta-label-2026-11-01.md) or M1c (prereg-ml-meta-label-m1c-2026-11-01.md)")
    a = ap.parse_args()
    tfs = {SPECS[m][0] for m in a.models}
    if len(tfs) != 1:
        raise SystemExit("run models of one decision timeframe at a time")
    tf, hold, context, alpha, side_leg = SPECS[a.models[0]]
    cols = M.model_columns(context)
    check_feature_cap(cols)
    costs = A.load_costs(ROOT / "config" / "arm_costs.yaml")
    b1, pos, cal = load_inputs(a.raw, a.positioning, a.cal)
    b15, F = feature_frame(b1, pos, cal, tf, context)
    look = lookahead_test(a.raw, a.positioning, a.cal, b15, F, a.workers, a.models[0])
    prereg = "prereg-ml-meta-label-m1c-2026-11-01.md" if a.models == ["M1c"] else "prereg-ml-meta-label-2026-11-01.md"
    res: dict[str, Any] = {"prereg": prereg, "cost_version": COST_VERSION, "decision_tf": tf,
                           "lookahead": look, "columns": cols, "models": {}}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    if not look["ok"]:
        a.out.write_text(json.dumps(res, indent=1, default=str))
        raise SystemExit(f"lookahead test failed: {look['mismatches'][:3]}")
    for model in a.models:
        tf, hold, context, alpha, side_leg = SPECS[model]
        c = label(candidates(model, b15, b1), b15, b1, costs, a.workers, hold)
        Xf = F.iloc[c["idx"].to_numpy(int)].reset_index(drop=True).copy()
        Xf["side"] = c["side"].to_numpy()
        Xf = side_align(Xf)
        X = Xf[cols].to_numpy(float)
        if a.dry:
            nan = {k: round(float(v), 3) for k, v in Xf[cols].isna().mean().items() if v > 0}
            print(model, "candidates", len(c), "by year", c["year"].value_counts().sort_index().to_dict(),
                  "median risk", round(float(c["risk_usd"].median()), 2), "NaN shares", nan, flush=True)
            continue
        y = c["net"].to_numpy(float)
        dec, ex, yrs = c["dec_ns"].to_numpy(), c["exit_ns"].to_numpy(), c["year"].to_numpy()
        pred = run_folds(X, y, M.purged_year_folds(dec, ex, yrs), a.workers)
        pred_fwd = run_folds(X, y, M.forward_folds(ex, yrs), a.workers)
        pred_shuf = run_folds(X, y, M.purged_year_folds(dec, ex, yrs), a.workers, shuffle=True)
        r = read(c, pred, pred_fwd, pred_shuf, alpha, side_leg)
        r["top_gain"] = importances(X, y, cols)
        res["models"][model] = r
        print(model, {k: r[k] for k in ("n_candidates", "n_selected", "top_net_mean", "top_net_lb90", "p_control",
                                        "forward_top_net_mean", "shuffle_p_control", "verdict")}, flush=True)
    a.out.write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
