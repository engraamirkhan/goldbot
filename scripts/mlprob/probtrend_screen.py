"""ML+prob lane PROBTREND: a point-in-time trend-day probability gating the 11 gross-positive sprint-200 trend rules
(docs/research/mlprob/probtrend-prereg.md, frozen before any look; amendment 1 adds W2 test-old, ex-2025, drift
control and bps costs as reported-only items).

    uv run python scripts/mlprob/probtrend_screen.py --stage calib --window explore
    uv run python scripts/mlprob/probtrend_screen.py --stage pnl --window explore
    uv run python scripts/mlprob/probtrend_screen.py --stage pnl --window confirm      (explore passers only, once)
    uv run python scripts/mlprob/probtrend_screen.py --stage pnl --window test_old     (confirm passers only, once)

The engine (signals, walk, costs, leak-free twins) is `probtrend_engine.py`, a verbatim copy of
`scripts/sprint200/trend_screen.py` at ccaf6b26. Writes aggregate statistics only (JSON under ~/tmp, not committed)
plus a forecast table (per-day probabilities, no prices). Nothing at or after 2025-10-01 is read (engine assertion).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probtrend_engine as E  # noqa: E402

from goldbot.data.store import asof_join  # noqa: E402
from goldbot.labels import SwapSpec  # noqa: E402
from goldbot.research import arms as A  # noqa: E402
from goldbot.research.metrics import MIN_TRADES_FOR_DSR, DsrInputs  # noqa: E402

K_DSR = 3_300
K_EFF = 12
REF_PRICE = 4_000.0
N_DRIFT = 4
MIN, DAYNS = E.MIN, E.DAYNS
RULES = ["S200-TREND-01", "S200-TREND-03", "S200-TREND-04", "S200-TREND-10", "S200-TREND-15", "S200-TREND-19",
         "S200-TREND-20", "S200-TREND-24", "S200-TREND-28", "S200-TREND-31", "S200-TREND-32"]
PT = {f"PT-{i + 1:02d}": r for i, r in enumerate(RULES)}
POOLED = "PT-12"
FOUR_H = {"S200-TREND-01", "S200-TREND-15", "S200-TREND-28", "S200-TREND-31"}
TIER1 = ("fomc_statement", "nfp", "cpi")
FEAT_A = ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11"]
FEAT_B = FEAT_A + ["f12", "f13", "f14", "f15"]
# lane windows (data from, first decision, end exclusive); the engine's own explore/confirm are kept
E.WINDOWS["explore_read"] = (pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2011-01-01", tz="UTC"),
                             pd.Timestamp("2018-01-01", tz="UTC"))
E.WINDOWS["test_old"] = (pd.Timestamp("2005-01-01", tz="UTC"), pd.Timestamp("2006-02-01", tz="UTC"),
                         pd.Timestamp("2010-01-01", tz="UTC"))
CTX_WINDOW = {"explore": "explore_read", "confirm": "confirm", "test_old": "test_old"}
OUT = Path.home() / "tmp" / "mlprob_probtrend"


# ---------------------------------------------------------------- daily table, labels, features
def daily_table(ctx: E.Ctx, cal: pd.DataFrame) -> pd.DataFrame:
    t, o, h, lo = ctx.t, ctx.o, ctx.h, ctx.lo
    ud = np.unique(ctx.tday)
    d1 = ctx.bars["1d"]
    assert len(d1.c) == len(ud)
    fi = np.searchsorted(ctx.tday, ud, side="left")
    li = np.searchsorted(ctx.tday, ud, side="right") - 1
    D = pd.DataFrame(index=pd.Index(ud, name="td"))
    D["year"] = pd.to_datetime(ud, unit="D").year
    D["nmin"] = li - fi + 1
    Op, H, L, C = (pd.Series(x, index=D.index) for x in (d1.o, d1.h, d1.lo, d1.c))
    rng = H - L
    atr = {n: pd.Series(E.atr(d1, n).to_numpy(), index=D.index).shift(1) for n in (5, 20, 100)}
    a20 = atr[20]
    # snapshot A: trading-day open
    pa = o[fi]
    D["avail_A"] = t[fi] + MIN
    dispA = np.abs(C.to_numpy() - pa)
    erA = dispA / np.where(rng > 0, rng, np.nan)
    D["lab_A"] = np.where((D.nmin >= 600) & a20.notna(), ((erA >= 0.5) & (dispA >= 0.5 * a20)).astype(float), np.nan)
    # snapshot B: London open of the day
    lon = ctx.day.lon_start.reindex(ud).to_numpy(float)
    labB = np.full(len(ud), np.nan)
    pb = np.full(len(ud), np.nan)
    availB = np.full(len(ud), np.nan)
    for i in range(len(ud)):
        if not np.isfinite(lon[i]):
            continue
        k = int(np.searchsorted(t, int(lon[i]), side="left"))
        if k > li[i] or k < fi[i]:
            continue
        pb[i] = o[k]
        availB[i] = int(lon[i]) + MIN
        if li[i] - k + 1 < 300 or not np.isfinite(a20.iloc[i]):
            continue
        hr, lr, cr = h[k:li[i] + 1].max(), lo[k:li[i] + 1].min(), ctx.c[li[i]]
        disp = abs(cr - pb[i])
        er = disp / (hr - lr) if hr > lr else 0.0
        labB[i] = float(er >= 0.5 and disp >= 0.5 * a20.iloc[i])
    D["lab_B"], D["avail_B"] = labB, availB
    # features (A and B share f1-f11; all from completed days before d, the calendar, or the weekday)
    D["f1"] = np.log(atr[5] / a20)
    D["f2"] = np.log(rng.shift(1) / a20)
    D["f3"] = ((C - Op).abs() / rng.where(rng > 0)).shift(1)
    D["f4"] = np.log(a20 / atr[100])
    D["f5"] = (C.shift(1) - C.shift(6)).abs() / (a20 * np.sqrt(5))
    D["f6"] = (C.shift(1) - C.shift(21)).abs() / (a20 * np.sqrt(20))
    D["f7"] = D.lab_A.shift(1).rolling(10, min_periods=5).mean()
    nr4 = rng <= rng.rolling(4).min()
    ins = (H < H.shift(1)) & (L > L.shift(1))
    D["f8"] = (nr4 | ins).astype(float).shift(1)
    start = E._ny_time(ud - 1, 17, 0)
    end = E._ny_time(ud, 17, 0)
    ev = cal[cal.event_type.isin(TIER1) & (cal.status == "scheduled")]
    ev_t = E._ns(pd.DatetimeIndex(pd.to_datetime(ev.event_utc, utc=True)))
    ev_av = E._ns(pd.DatetimeIndex(pd.to_datetime(ev.available_utc, utc=True)))
    f9 = np.zeros(len(ud))
    for i in range(len(ud)):
        inday = (ev_t >= start[i]) & (ev_t < end[i]) & (ev_av <= t[fi[i]])   # known at snapshot A (hence at B)
        f9[i] = float(inday.any())
    D["f9"] = f9
    wd = pd.to_datetime(ud, unit="D").dayofweek
    D["f10"], D["f11"] = (wd == 0).astype(float), (wd == 4).astype(float)
    day = ctx.day.reindex(ud)
    ok = day.asia_n >= 300
    ar = (day.asia_h - day.asia_l).where(ok & (day.asia_h > day.asia_l))
    D["f12"] = np.log(ar / a20)
    D["f13"] = ((day.asia_c - day.asia_o).abs() / ar)
    D["f14"] = np.log(ar / ar.shift(1).rolling(20, min_periods=10).median())
    D["f15"] = np.abs(pb - pa) / a20
    # Asia must be complete before B: the session closes at the London open (calendar.py)
    assert ((day.asia_last.dropna() < day.lon_start.reindex(day.asia_last.dropna().index).fillna(np.inf))).all()
    return D


# ---------------------------------------------------------------- model
def fit(D: pd.DataFrame, snap: str) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression
    feats = FEAT_A if snap == "A" else FEAT_B
    tr = D[D[f"lab_{snap}"].notna()].dropna(subset=feats)
    X = tr[feats].to_numpy(float)
    mu, sd = X.mean(0), X.std(0)
    sd = np.where(sd > 0, sd, 1.0)
    m = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
    y = tr[f"lab_{snap}"].to_numpy(int)
    m.fit((X - mu) / sd, y)
    p_in = m.predict_proba((X - mu) / sd)[:, 1]
    return {"m": m, "mu": mu, "sd": sd, "feats": feats, "tau": float(np.quantile(p_in, 0.60)),
            "base": float(y.mean()), "n_train": int(len(y)),
            "coef": dict(zip(feats, np.round(m.coef_[0], 4).tolist(), strict=True)),
            "intercept": float(m.intercept_[0])}


def predict(model: dict[str, Any], D: pd.DataFrame) -> np.ndarray:
    X = D[model["feats"]].to_numpy(float)
    ok = np.isfinite(X).all(1)
    p = np.full(len(D), np.nan)
    if ok.any():
        p[ok] = model["m"].predict_proba((X[ok] - model["mu"]) / model["sd"])[:, 1]
    return p


def forecasts(D: pd.DataFrame, models: dict[tuple[str, int], dict[str, Any]], years: list[int] | None) -> pd.DataFrame:
    """One row per day x snapshot: available_utc, p, tau, base (training base rate), label (for calibration only)."""
    rows = []
    for snap in ("A", "B"):
        for key, mdl in models.items():
            if key[0] != snap:
                continue
            sub = D if years is None else D[D.year == key[1]]
            if sub.empty:
                continue
            p = predict(mdl, sub)
            rows.append(pd.DataFrame({"td": sub.index, "snap": snap, "available_utc": sub[f"avail_{snap}"].to_numpy(),
                                      "p": p, "tau": mdl["tau"], "base": mdl["base"],
                                      "label": sub[f"lab_{snap}"].to_numpy()}))
    fc = pd.concat(rows, ignore_index=True)
    fc = fc[np.isfinite(fc.available_utc.to_numpy(float))]
    fc["available_utc"] = pd.to_datetime(fc.available_utc.astype(np.int64), utc=True)
    return fc.sort_values("available_utc").reset_index(drop=True)


def walk_forward(D: pd.DataFrame, years: range) -> tuple[pd.DataFrame, dict[str, Any]]:
    models: dict[tuple[str, int], dict[str, Any]] = {}
    for y in years:
        tr = D[D.year < y]
        for snap in ("A", "B"):
            models[(snap, y)] = fit(tr, snap)
    fc = forecasts(D[D.year.isin(list(years))], models, list(years))
    info = {f"{s}{y}": {k: v for k, v in m.items() if k in ("tau", "base", "n_train", "coef", "intercept")}
            for (s, y), m in models.items()}
    return fc, info


def calibration(fc: pd.DataFrame) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    out: dict[str, Any] = {}
    for snap in ("A", "B"):
        f = fc[(fc.snap == snap) & fc.label.notna() & fc.p.notna()]
        y, p = f.label.to_numpy(float), f.p.to_numpy(float)
        brier = float(np.mean((p - y) ** 2))
        clim = float(np.mean((f.base.to_numpy(float) - y) ** 2))
        bins = pd.qcut(p, 10, labels=False, duplicates="drop")
        rel = pd.DataFrame({"b": bins, "p": p, "y": y}).groupby("b").agg(mean_p=("p", "mean"), obs=("y", "mean"),
                                                                         n=("y", "size"))
        lg = np.log(p / (1 - p)).reshape(-1, 1)
        cal = LogisticRegression(penalty=None).fit(lg, y.astype(int))
        top = p >= f.tau.to_numpy(float)
        out[snap] = {"n_days": int(len(y)), "base_rate": float(y.mean()), "brier": brier, "brier_clim": clim,
                     "bss": 1 - brier / clim, "auc": float(roc_auc_score(y, p)),
                     "cal_slope": float(cal.coef_[0][0]), "cal_intercept": float(cal.intercept_[0]),
                     "gate_share": float(top.mean()), "hit_gate": float(y[top].mean()), "hit_rest": float(y[~top].mean()),
                     "reliability": [{k: round(float(v), 4) for k, v in r.items()} for _, r in rel.iterrows()]}
    out["gate_pass"] = bool(all(out[s]["bss"] > 0 and out[s]["auc"] >= 0.55 for s in ("A", "B")))
    return out


# ---------------------------------------------------------------- simulation (the engine's, plus drift twins)
def simulate(ctx: E.Ctx, sid: str, spec: E.Spec) -> dict[str, list[dict[str, Any]]]:
    """`E.simulate` line for line (same real trades and leak-free twins), plus the entry price on each row and the
    amendment-1 drift twins (same side, clock time and exits on a random other trading day within +/-20)."""
    sig = spec.sig.sort_values("dec", kind="stable").reset_index(drop=True)
    sig = sig[(sig.dec >= ctx.dec_from) & (sig.dec < ctx.end)]
    flags: dict[int, np.ndarray] | None = None
    if spec.exit_flags is not None:
        fd, f_long, f_short = spec.exit_flags
        flags = {1: fd[np.asarray(f_long, dtype=bool)], -1: fd[np.asarray(f_short, dtype=bool)]}
    t = ctx.t
    ud = np.unique(ctx.tday)
    real: list[dict[str, Any]] = []
    twins: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    busy_until = -1
    big = np.iinfo(np.int64).max
    r: Any
    for r in sig.itertuples(index=False):
        dec = int(r.dec)
        if dec < busy_until:
            continue
        k0 = int(np.searchsorted(t, dec, side="left"))
        if k0 >= len(t) - 1:
            continue
        dl = E.deadline(ctx, str(r.dl), k0)
        if dl < big and t[k0] > dl - 15 * MIN:
            continue
        side = int(r.side)
        e = float(ctx.o[k0])
        risk = float(r.risk) if np.isfinite(r.risk) else side * (e - float(r.stop_px))
        if not (np.isfinite(risk) and risk > 0):
            continue
        cap = int(r.cap)
        t_end = min(cap, dl, ctx.end)
        fl = flags[side] if flags is not None else None
        g, kind, kx, tx = E.walk(ctx, k0, side, risk, float(r.tgt), float(r.trail), t_end, fl)
        n_id = len(real)
        real.append({"t_fill": int(t[k0]), "t_exit": tx, "side": side, "risk": risk, "gross": g, "kind": kind,
                     "id": n_id, "e": e})
        busy_until = tx
        hi_k = int(ctx.run_end[k0])
        if hi_k - k0 + 1 < 30:
            hi_k = int(np.searchsorted(t, t[k0] + 60 * MIN, side="left")) - 1
        hold = (cap - int(t[k0])) if cap < big else None
        for j in range(E.N_TWIN):
            kk = k0 + int(E.h_u(sid, n_id, j) * (hi_k - k0 + 1))
            tw = E._twin(ctx, kk, r, side, risk, hold, fl, n_id)
            if tw is not None:
                tw["e"] = float(ctx.o[kk])
                twins.append(tw)
        td0 = int(ctx.tday[k0])
        p0 = int(np.searchsorted(ud, td0))
        for j in range(N_DRIFT):
            u = E.h_u(sid, n_id, j, "drift")
            off = int(u * 40) - 20
            off = off + 1 if off >= 0 else off                         # -20..-1, 1..20
            p1 = p0 + off
            if p1 < 0 or p1 >= len(ud):
                continue
            td1 = int(ud[p1])
            tt = int(t[k0]) + (td1 - td0) * DAYNS
            kk = int(np.searchsorted(t, tt, side="left"))
            if kk >= len(t) - 1 or int(ctx.tday[kk]) != td1 or int(t[kk]) - tt > 30 * MIN:
                continue
            tw = E._twin(ctx, kk, r, side, risk, hold, fl, n_id)
            if tw is not None:
                tw["e"] = float(ctx.o[kk])
                drift.append(tw)
    return {"real": real, "twins": twins, "drift": drift}


def price(rows: list[dict[str, Any]], costs: A.ArmCosts, swap: SwapSpec) -> pd.DataFrame:
    df = E.price(rows, costs, swap)
    if df.empty:
        return df
    k = df.e.to_numpy(float) / REF_PRICE                          # W2 price-relative (bps) costs
    df["R_bps"] = (df.gross + k * (df.swap - df.cost)) / df.risk     # cost = round trip + overshoot on stop exits
    ent = pd.DatetimeIndex(pd.to_datetime(df.t_fill.to_numpy(), utc=True)).tz_convert(E.NYZ).tz_localize(None)
    df["td"] = (ent + pd.Timedelta(hours=7)).normalize()
    return df


# ---------------------------------------------------------------- statistics
def _t(x: np.ndarray) -> float:
    if len(x) < 2:
        return float("nan")
    sd = float(np.std(x, ddof=1))
    return float(np.mean(x) / (sd / np.sqrt(len(x)))) if sd > 0 else float("nan")


def _excess(real: pd.DataFrame, tw: pd.DataFrame, col: str = "R") -> float:
    if tw.empty or real.empty:
        return float("nan")
    m = tw.groupby("key")[col].mean()
    has = real.key.isin(m.index)
    return float((real[col][has] - real.key[has].map(m)).mean()) if has.any() else float("nan")


def block_p(real: pd.DataFrame, block: int = 5, n_boot: int = 2000, seed: int = 0) -> float:
    """One-sided day-block-bootstrap p of mean net R > 0 (W2): resample blocks of 5 consecutive trade days."""
    g = real.groupby("td").R.agg(["sum", "size"]).sort_index()
    s, c = g["sum"].to_numpy(), g["size"].to_numpy()
    nb = int(np.ceil(len(s) / block))
    starts = np.arange(0, max(len(s) - block + 1, 1))
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for b in range(n_boot):
        st = rng.choice(starts, nb)
        idx = (st[:, None] + np.arange(block)[None, :]).ravel()
        idx = idx[idx < len(s)]
        means[b] = s[idx].sum() / c[idx].sum()
    return float(np.mean(means <= 0))


def stats(real: pd.DataFrame, tw: pd.DataFrame, dr: pd.DataFrame, years: range) -> dict[str, Any]:
    if real.empty:
        return {"n": 0}
    r = real.R.to_numpy()
    day = real.groupby("td").R.sum().to_numpy()
    yr = real.groupby("year").R.mean()
    di = DsrInputs.of(r)
    dd = DsrInputs.of(day)
    out = {"n": int(len(r)), "n_days": int(len(day)), "net_R": float(r.mean()), "t": _t(r), "t_day": _t(day),
           "gross_R": float(real.gross_R.mean()), "gross_t": _t(real.gross_R.to_numpy()),
           "cost_R": float(real.cost_R.mean()), "net_R_bps": float(real.R_bps.mean()), "t_bps": _t(real.R_bps.to_numpy()),
           "ctrl_excess": _excess(real, tw), "drift_excess": _excess(real, dr),
           "drift_adj_gross": float(real.gross_R.mean() - dr.gross_R.mean()) if not dr.empty else float("nan"),
           "long_share": float((real.side > 0).mean()), "median_risk_usd": float(real.risk.median()),
           "years_pos": int(sum(1 for y in years if y in yr.index and yr[y] > 0)),
           "year_net_R": {str(k): round(float(v), 4) for k, v in yr.items()},
           "dsr_K": K_DSR, "dsr_trade": di.dsr(K_DSR) if len(r) >= MIN_TRADES_FOR_DSR else None,
           "dsr_day": dd.dsr(K_DSR) if len(day) >= MIN_TRADES_FOR_DSR else None,
           "dsr_trade_keff": di.dsr(K_EFF) if len(r) >= MIN_TRADES_FOR_DSR else None,
           "dsr_inputs": di.model_dump(), "boot_p": block_p(real)}
    return out


# ---------------------------------------------------------------- workers
_CTX: E.Ctx | None = None
_FC: pd.DataFrame | None = None
_WIN: str = ""


def _init(raw: str, window: str, fc_path: str) -> None:
    global _CTX, _FC, _WIN
    _CTX = E.build_ctx(Path(raw), CTX_WINDOW[window])
    _FC = pd.read_parquet(fc_path)
    _WIN = window


def gate_sig(sig: pd.DataFrame, fc: pd.DataFrame) -> pd.DataFrame:
    """Keep signals whose forecast in force (asof_join on available_utc, tolerance 24 h) has p >= tau."""
    if sig.empty:
        return sig
    left = sig.assign(ts_utc=pd.to_datetime(sig.dec.to_numpy(), utc=True), _row=np.arange(len(sig)))
    j = asof_join(left, fc[["available_utc", "p", "tau", "snap"]], on="ts_utc", tolerance=pd.Timedelta(hours=24))
    keep = j[(j.p >= j.tau).fillna(False).astype(bool)]._row.to_numpy()
    return sig.iloc[np.sort(keep)].reset_index(drop=True)


def _run(rule: str) -> dict[str, Any]:
    assert _CTX is not None and _FC is not None
    costs = A.load_costs(E.ROOT / "config" / "arm_costs.yaml")
    swap = SwapSpec.shipped()
    spec = E.STRATS[rule](_CTX)
    sig = spec.sig[(spec.sig.dec >= _CTX.dec_from) & (spec.sig.dec < _CTX.end)].reset_index(drop=True)
    out: dict[str, Any] = {"rule": rule, "n_signals": int(len(sig))}
    for arm, s in (("gated", gate_sig(sig, _FC)), ("ungated", sig)):
        res = simulate(_CTX, rule, E.Spec(s, spec.exit_flags))
        frames = {}
        for k in ("real", "twins", "drift"):
            df = price(res[k], costs, swap)
            if not df.empty:
                df["key"] = rule + ":" + df.id.astype(str)
                df["rule"] = rule
            frames[k] = df
        out[arm] = frames
        out[f"n_signals_{arm}"] = int(len(s))
    return out


YEARS = {"explore": range(2011, 2018), "confirm": range(2018, 2026), "test_old": range(2006, 2010)}


def evaluate(window: str, rows: list[dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
    yrs = YEARS[window]
    by = {r["rule"]: r for r in rows}
    res = []
    for pid in ids:
        rules = RULES if pid == POOLED else [PT[pid]]
        if any(x not in by for x in rules):
            continue
        cat = {arm: {k: pd.concat([by[x][arm][k] for x in rules if not by[x][arm][k].empty], ignore_index=True)
                     if any(not by[x][arm][k].empty for x in rules) else pd.DataFrame()
                     for k in ("real", "twins", "drift")} for arm in ("gated", "ungated")}
        g, u = cat["gated"], cat["ungated"]
        sg = stats(g["real"], g["twins"], g["drift"], yrs)
        su = stats(u["real"], u["twins"], u["drift"], yrs)
        rec: dict[str, Any] = {"id": pid, "rules": rules, "gated": sg, "ungated": su,
                               "gate_uplift": (sg.get("net_R", np.nan) - su.get("net_R", np.nan)) if sg["n"] else None}
        if window == "confirm" and sg["n"]:
            gr = g["real"]
            m = gr.year < 2025
            sub = {k: (v[v.year < 2025] if not v.empty else v) for k, v in g.items()}
            rec["gated_ex2025"] = stats(sub["real"], sub["twins"], sub["drift"], range(2018, 2025)) if m.any() else {}
            ur = {k: (v[v.year < 2025] if not v.empty else v) for k, v in u.items()}
            rec["ungated_ex2025"] = stats(ur["real"], ur["twins"], ur["drift"], range(2018, 2025))
        tcol = "t_day" if pid == POOLED else "t"
        n, net, tt = sg["n"], sg.get("net_R", np.nan), sg.get(tcol, np.nan)
        if window == "explore":
            ok = (n >= 100 and net > 0 and tt >= 2.0 and sg["ctrl_excess"] >= 0.05
                  and rec["gate_uplift"] is not None and rec["gate_uplift"] >= 0.03)
            rec["w2_explore_pass"] = bool(n >= 100 and net > 0 and sg.get("t_day", np.nan) >= 2.0
                                          and sg["ctrl_excess"] >= 0)
        elif window == "confirm":
            dsr = sg["dsr_trade"]
            if pid == POOLED:
                dsr = None if sg["dsr_trade"] is None or sg["dsr_day"] is None else min(sg["dsr_trade"], sg["dsr_day"])
            ok = n >= 100 and net > 0 and tt >= 2.0 and dsr is not None and dsr >= 0.5 and sg["years_pos"] >= 6
        else:
            nmin = 60 if (pid != POOLED and PT[pid] in FOUR_H) else 100
            ok = n >= nmin and net > 0 and sg.get("t", np.nan) >= 1.28 and sg["ctrl_excess"] >= 0
        rec["pass"] = bool(ok)
        rec["verdict"] = "PASS" if ok else ("FAIL-COUNT" if n < 100 else "FAIL")
        res.append(rec)
    return res


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def build_forecasts(raw: Path, window: str, cal: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if window == "explore":
        ctx = E.build_ctx(raw, "explore_read")
        D = daily_table(ctx, cal)
        fc, info = walk_forward(D, range(2011, 2018))
        return fc, {"models": info}
    ctx = E.build_ctx(raw, "explore")
    Dx = daily_table(ctx, cal)
    del ctx
    models = {(s, 0): fit(Dx, s) for s in ("A", "B")}
    ctx = E.build_ctx(raw, CTX_WINDOW[window])
    D = daily_table(ctx, cal)
    lo = pd.Timestamp(ctx.dec_from, tz="UTC").tz_localize(None).normalize()
    D = D[pd.to_datetime(D.index, unit="D") >= lo - pd.Timedelta(days=1)]
    fc = forecasts(D, models, None)
    info = {s: {k: v for k, v in m.items() if k in ("tau", "base", "n_train", "coef", "intercept")}
            for (s, _), m in models.items()}
    return fc, {"models": info}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=E.ROOT.parents[2] / "raw" if (E.ROOT.parents[2] / "raw").exists()
                    else E.ROOT / "raw")
    ap.add_argument("--calendar", type=Path, default=Path.home() / "tmp" / "kcal_out" / "us_calendar.parquet")
    ap.add_argument("--stage", choices=["calib", "pnl"], required=True)
    ap.add_argument("--window", choices=list(YEARS), required=True)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    assert a.workers <= 2, "the shared Mac: at most 2 workers per lane"
    a.out.mkdir(parents=True, exist_ok=True)
    cal = pd.read_parquet(a.calendar)
    fc_path = a.out / f"forecast_{a.window}.parquet"
    if a.window == "explore":
        ids = list(PT) + [POOLED]
    else:
        prev = "explore" if a.window == "confirm" else "confirm"
        pj = a.out / f"pnl_{prev}.json"
        if not pj.exists():
            raise SystemExit(f"{a.window} needs the {prev} result ({pj})")
        ids = [r["id"] for r in json.loads(pj.read_text())["results"] if r.get("pass")]
        if not ids:
            print(f"no {prev} passers: {a.window} not run")
            return
        if a.window == "test_old":
            need = [a.raw / f"xauusd_1m_dukascopy_{y}.parquet" for y in range(2005, 2010)]
            if not all(p.exists() for p in need):
                print("TEST-OLD data not on disk: confirm passers are 'old-test pending'", ids)
                return
    done = a.out / f"pnl_{a.window}.json"
    if a.stage == "pnl" and a.window != "explore" and done.exists():
        raise SystemExit(f"{a.window} already read once ({done})")
    if a.stage == "calib" or not fc_path.exists():
        fc, info = build_forecasts(a.raw, a.window, cal)
        fc.drop(columns=["label"]).to_parquet(fc_path)
        cb = calibration(fc[fc.td >= int(pd.Timestamp(E.WINDOWS[CTX_WINDOW[a.window]][1]).tz_localize(None).value
                                         // DAYNS)])
        (a.out / f"calib_{a.window}.json").write_text(json.dumps({"window": a.window, **cb, **info,
                                                                  "calendar_sha": _sha(a.calendar)},
                                                                 indent=1, default=float))
        print(json.dumps({s: {k: round(v, 4) for k, v in cb[s].items() if isinstance(v, float)} for s in ("A", "B")}
                         | {"gate_pass": cb["gate_pass"]}, indent=1))
        if a.stage == "calib":
            return
    cbj = json.loads((a.out / "calib_explore.json").read_text())
    if not cbj["gate_pass"]:
        raise SystemExit("calibration gate failed on explore: no P&L read (prereg section 3)")
    rules = sorted({r for i in ids for r in (RULES if i == POOLED else [PT[i]])})
    with ProcessPoolExecutor(a.workers, initializer=_init, initargs=(str(a.raw), a.window, str(fc_path))) as ex:
        rows = list(ex.map(_run, rules))
    res = evaluate(a.window, rows, ids)
    done.write_text(json.dumps({"window": a.window, "cost_version": E.COST_VERSION, "K_dsr": K_DSR, "ids": ids,
                                "n_signals": {r["rule"]: [r["n_signals"], r["n_signals_gated"]] for r in rows},
                                "results": res}, indent=1, default=float))
    for r in res:
        g, u = r["gated"], r["ungated"]
        print(r["id"], r["verdict"], {k: (round(v, 4) if isinstance(v, float) else v) for k, v in g.items()
                                      if k in ("n", "gross_R", "net_R", "t", "t_day", "ctrl_excess", "drift_excess",
                                               "net_R_bps", "dsr_trade", "years_pos")},
              "ungated", round(u.get("net_R", float("nan")), 4), "uplift", r["gate_uplift"])


if __name__ == "__main__":
    main()
