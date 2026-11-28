"""D-12-xt: re-measure the D-12 residual ICCs and tau_a at the xt-v1 exit freeze (quant ruling on PR #514; sr-v3 card
work item D-12-xt; amendment docs/research/prereg-amendment-2026-11-27-sr-v3-d4-exit-freeze.md).

What changes against D-12 (`measure_rho.py`, PR #466, h_ref = 1 bar, holds of 2-30 bars): the GEOMETRY only. Every
candidate is labelled by the dense-label factory itself (`dense_labels.label_rows` under `dense_labels.SPEC`), so the
geometry is xt-v1 exactly: d0 = ATR14_tf (Wilder) x sqrt(H / h_ref) with h_ref = H (factor 1), floored at 1.5 x the
median 1m true range of the 1,440 minutes up to the decision; T2 (stop 1, target 2) and T6 (stop 2, time exit at H) at
the card's k; hold = H of the tf (1m 60 min, 5m 4 h, 15m 8 h, 30m 16 h, 1h 1 d, 2h 2 d, 4h 5 d, 1d 10 d, 1w 20 d);
xt-v1 fill rules (entry at the first minute opening after the decision, side-aware quotes, a gapped stop fills at the
open, a gapped target at the target, a minute touching both is a stop, the 1d/1w time exit on the decision grid).
Every label row carries the factory's `exit_freeze_sha` stamp and the run refuses one that is not
`dense_labels.EXIT_FREEZE_SHA256`. A row is used only when the factory's tier rule makes it usable (`attach_tiers`,
label window decision month .. horizon end A / A-consistent on the D-1 served tiers, the file the dense labels use).

What does NOT change (the ruling's "same"): learn 2010-17 only (minutes 2010..2017, a horizon past 2017 is truncated,
never read); the hold buckets (`data_sufficiency.hold_bucket`, the label horizon sets the bucket); the control grid
(every hold_bars-th tf-bar close of each NY-17:00 session day, the same times on every instrument; for H >= 1 day, the
first bar of every ceil(H / 1 d)-th business session day, and for weekly bars every ceil(H / 1 w)-th epoch week, so a
candidate never overlaps the next); the three replayed
families (box_break_close, sweep_absorb, session_range_break: decisions from `measure_rho.family_decisions` on the D-12
loader, so they are the D-12 decisions; greedy non-overlap at hold H); the outcomes (long, short, seeded random, both;
gross and net of the D-12 constant bps cost in the template's own R) and the estimators and the week-block bootstrap
upper 95 % (`measure_rho.icc_block`, B = 2,000, the D-12 seeds rule).

Binding (quant ruling on #514): rho_u,day and rho_u,inst (class day-mean and vs-gold day-mean) bind at max(D-12
recorded, re-run) per class x bucket; tau_a / lambda_a take the re-run value only (R units must match); both are
recorded side by side (`side_by_side`). `by_tf_rerun` reports the re-run's worst cell per tf (never binding): hold = H
widens gt4h to 8 h .. 20 d, and the quant may rule a per-tf binding there. The file records `exit_freeze_sha` (and the xt-v1 record) so
`data_sufficiency.require_power_read_rho` and `sr_v3.rho_inputs(freeze_sha=...)` accept it, and `binds_with` (the
D-12 file, path and sha256) so `config.check_rho_measurement` can recompute the le-60m bound as max(cells, D-12).
rho_x (label-blind feature ICCs, D-12a) does not depend on the exit geometry: it is read from the D-12 file
(`rho_x_from`, sha256), not copied. `binding_by_tf` is quant ruling 1 on #525 (`PER_TF_RULE`); `--rebind` recomputes
every summary and binding of a committed file from its cells file without re-measuring.

Label-blind with respect to sr-v3 / H-102: control-grid and replayed-family residual ICCs only; no sr-v3 learner
outcome, no dense-label store value is read (the labels are computed here, never loaded from ~/tmp/dense-labels).

Usage: python scripts/research/measure_rho_xt.py [--raw RAW] [--workers 3] [--icc-workers 8] [--boot 2000]
       [--recorded docs/research/rho-measurement-2026-11.json] [--out docs/research] [--stem rho-measurement-2026-11-28-xt-v1]
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")          # one BLAS thread per process: the worker count bounds the CPU threads

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from goldbot.config import tf_seconds  # noqa: E402
from goldbot.data.timeutil import epoch_ns  # noqa: E402
from goldbot.research import dense_labels as D  # noqa: E402
from goldbot.research import feature_store as F  # noqa: E402
from goldbot.research.data_sufficiency import hold_bucket  # noqa: E402


def _load_d12() -> Any:
    """measure_rho.py (D-12), imported for its loader, families, estimator and constants; never edited (the committed
    D-12 file records its sha256, `test_d12_the_committed_measurement_matches_the_committed_script_and_its_cells`)."""
    spec = importlib.util.spec_from_file_location("measure_rho", ROOT / "scripts" / "research" / "measure_rho.py")
    if spec is None or spec.loader is None:
        raise ImportError("scripts/research/measure_rho.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("measure_rho", mod)
    spec.loader.exec_module(mod)
    return mod


M = _load_d12()

ITEM = "D-12-xt (quant ruling on PR #514; sr-v3 card work item D-12-xt)"
SCHEMA = "rho_day_measurement/v3"
TFS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")
HEADS: tuple[str, ...] = ("T2", "T6")
BANDS: tuple[str, ...] = ("le60m", "1h-4h", "gt4h")
DAY_S = 86_400
TIERS_FILE = "tiers/tiers_served_r11-v2.1.csv"      # D-1 served tiers: the file the dense labels use (dukascopy gold)
RECORDED = "docs/research/rho-measurement-2026-11.json"
STEM = "rho-measurement-2026-11-28-xt-v1"
BIND_RULE = ("rho_u_day, rho_u_inst_day_mean and rho_u_inst_vs_gold_day_mean bind at max(D-12 recorded, re-run) per "
             "class x bucket; tau_a / lambda_a are the re-run value only (quant ruling on #514)")


def fs_name(inst: str) -> str:
    """measure_rho's instrument id -> the feature store / dense-label id (gold is "XAUUSD")."""
    return F.GOLD if inst == M.GOLD else inst


def geometry_name(tf: str, head: str) -> str:
    h = D.SPEC.h_by_tf[tf]
    span = f"{h // 60}m" if h < 3_600 else (f"{h // 3_600}h" if h < DAY_S else f"{h // DAY_S}d")
    return f"{tf} H={span} {head}"


GEOMETRIES: tuple[tuple[str, str, str], ...] = tuple((tf, head, geometry_name(tf, head)) for tf in TFS for head in HEADS)
BAND_OF: dict[str, str] = {name: hold_bucket(tf) for tf, _, name in GEOMETRIES}


def geometry_record() -> dict[str, Any]:
    """The geometry this file was measured under, from the factory's own freeze record (`dense_labels.exit_freeze`)."""
    fr = D.exit_freeze()
    tpl = {t.name: t.model_dump(mode="json") for t in fr.templates if t.name in HEADS}
    return {"freeze_id": fr.freeze_id, "exit_freeze_sha": D.exit_freeze_hash(D.SPEC), "heads": tpl,
            "h_by_tf_s": {tf: fr.h_by_tf[tf] for tf in TFS}, "h_ref_s": {tf: fr.h_ref[tf] for tf in TFS},
            "h_ref_factor": {tf: math.sqrt(fr.h_by_tf[tf] / fr.h_ref[tf]) for tf in TFS},
            "atr_n": fr.atr_n, "floor": f"{fr.floor_tr_mult} x median 1m TR of the {fr.floor_window_min} minutes up to "
                                        "the decision", "clip_r": fr.clip_r,
            "grid_time_exit_tfs": list(fr.grid_time_exit_tfs), "behaviour_sha": fr.behaviour_sha,
            "labeller": "goldbot/research/dense_labels.py label_rows(SPEC) + attach_tiers(columns=())",
            "hold_bucket": {tf: hold_bucket(tf) for tf in TFS}}


# ------------------------------------------------------------------------------------------------------------ candidates

def grid_decisions(close_ns: np.ndarray, tf: str) -> np.ndarray:
    """The control grid on one instrument's tf decision-bar closes (ns): H < 1 day, every (H / tf)-th close of each
    session day counted from its first (the day's candidates never overlap); H >= 1 day, the first close of every
    ceil(H / 1 d)-th business session day (business-day index from a fixed epoch: the same days on every instrument,
    and calendar spacing >= H, so no candidate overlaps the next); weekly bars (closing on a weekend), every
    ceil(H / 1 w)-th epoch week. Returns the kept closes."""
    close_ns = np.asarray(close_ns, dtype=np.int64)
    if not len(close_ns):
        return close_ns
    day = M.session_day(pd.DatetimeIndex(pd.to_datetime(close_ns, unit="ns", utc=True)))
    rank = pd.Series(np.arange(len(close_ns))).groupby(day).cumcount().to_numpy()
    h = D.SPEC.h_by_tf[tf]
    if h < DAY_S:
        step = h // tf_seconds(tf)
        if step < 1 or h % tf_seconds(tf):
            raise ValueError(f"{tf}: H {h} s is not a whole number of bars")
        keep = rank % step == 0
    elif tf_seconds(tf) >= 7 * DAY_S:
        wk = 7 * DAY_S * 10**9                      # weekly bars close on a weekend: every ceil(H / 1 w)-th epoch week
        keep = (close_ns // wk) % math.ceil(h / (7 * DAY_S)) == 0
    else:
        dd = day.astype("datetime64[D]")
        bd = np.busday_count(np.datetime64("2000-01-03"), dd)
        keep = (rank == 0) & np.is_busday(dd) & (bd % math.ceil(h / DAY_S) == 0)
    return close_ns[keep]


def thin(dec_ns: np.ndarray, hold_s: int) -> np.ndarray:
    """Greedy non-overlap in time (D-12 family rule): keep a decision only if it is >= hold after the last kept one.
    Indices into the sorted input."""
    keep: list[int] = []
    last: int | None = None
    hold = int(hold_s) * 10**9
    for i, t in enumerate(np.asarray(dec_ns, dtype=np.int64)):
        if last is None or t >= last + hold:
            keep.append(i)
            last = int(t)
    return np.asarray(keep, dtype=int)


def label_xt(mm: D.Minutes, inst: str, tf: str, dec_ns: np.ndarray, source: str, bid_only: bool,
             bars: pd.DataFrame, tiers: F.Tiers | None) -> pd.DataFrame:
    """The xt-v1 T2 / T6 labels (both sides) of decisions `dec_ns` through the factory, usable rows only (tier rule
    and label_ok). Refuses any row not stamped with the xt-v1 freeze."""
    if not len(dec_ns):
        return pd.DataFrame()
    dec = pd.DatetimeIndex(pd.to_datetime(np.asarray(dec_ns, dtype=np.int64), unit="ns", utc=True))
    rows = pd.DataFrame({"instrument": inst, "tf": tf, "phase": 0, "group_id": np.arange(len(dec)),
                         "decision_utc": dec, "available_utc": dec, "window_from_utc": dec, "source": source,
                         "bid_only": bid_only})
    lab = D.label_rows(mm, rows, tf, bars=bars)
    stamps = set(lab["exit_freeze_sha"].astype(str))
    if stamps != {D.EXIT_FREEZE_SHA256}:
        raise RuntimeError(f"labels stamped {sorted(stamps)}, not xt-v1 {D.EXIT_FREEZE_SHA256[:12]}: refused")
    lab = lab[lab["template"].isin(HEADS)]
    lab = D.attach_tiers(lab, tiers, columns=())
    return lab[lab["usable"].to_numpy(bool)].reset_index(drop=True)


def outcomes(lab: pd.DataFrame, inst: str, cost_bp: tuple[float, float], head: str, seed: Sequence[int],
             fam_side: pd.Series | None = None) -> pd.DataFrame:
    """One row per decision with both sides usable: long / short gross (the label's clipped gross R) and net of the
    D-12 constant bps cost (rt of the entry, plus ov on a stop exit) in the template's own R; both_gross; a seeded
    random side; with `fam_side` (decision -> +1/-1), the family's own side."""
    if lab.empty or "template" not in lab.columns:
        return pd.DataFrame()
    t = lab[lab["template"] == head]
    if t.empty:
        return pd.DataFrame()
    rt, ov = cost_bp
    stop = (t["barrier_hit"].astype(str) == "stop").to_numpy(float)
    entry, risk = t["entry"].to_numpy(float), t["risk_usd"].to_numpy(float)
    t = t.assign(net=t["gross_r"].to_numpy(float) - (rt * 1e-4 * entry + ov * 1e-4 * entry * stop) / risk)
    w = t.pivot_table(index="decision_utc", columns="side", values=["gross_r", "net"], aggfunc="first")
    need = [("gross_r", 1), ("gross_r", -1), ("net", 1), ("net", -1)]
    if not all(c in w.columns for c in need):
        return pd.DataFrame()
    w = w[need].dropna()
    dec = pd.DatetimeIndex(w.index)
    out = pd.DataFrame({"decision": dec, "day": M.session_day(dec),
                        "long_gross": w[("gross_r", 1)].to_numpy(float), "long_net": w[("net", 1)].to_numpy(float),
                        "short_gross": w[("gross_r", -1)].to_numpy(float),
                        "short_net": w[("net", -1)].to_numpy(float)})
    out["both_gross"] = (out["long_gross"] + out["short_gross"]) / 2
    rs = np.random.default_rng(list(seed)).choice([1, -1], len(out))
    out["rand_gross"] = np.where(rs == 1, out["long_gross"], out["short_gross"])
    out["rand_net"] = np.where(rs == 1, out["long_net"], out["short_net"])
    if fam_side is not None:
        sd = fam_side.reindex(dec).to_numpy(float)
        out["fam_gross"] = np.where(sd > 0, out["long_gross"], out["short_gross"])
        out["fam_net"] = np.where(sd > 0, out["long_net"], out["short_net"])
    return out


# ------------------------------------------------------------------------------------------------------------ worker

def work(args: tuple[str, str, str]) -> tuple[str, dict[tuple[str, ...], pd.DataFrame], pd.Series, dict[str, Any]]:
    """One instrument: the D-12 family decisions (D-12 loader), then the learn minutes through the factory's Minutes,
    then every xt-v1 geometry on the control grid and on each family."""
    raw, inst, tiers_path = Path(args[0]), args[1], Path(args[2])
    t0 = time.perf_counter()
    from goldbot.research import history_replay as HR
    b = M.load_bars(raw, inst)
    fams = M.family_decisions(b, HR.load_blackout(raw / "calendar"))
    del b
    fams["all"] = (pd.concat(list(fams.values()), ignore_index=True).sort_values("decision", kind="stable")
                   .drop_duplicates("decision").reset_index(drop=True))
    name = fs_name(inst)
    m = F.history_minutes(name, M.LEARN_LO.year, M.LEARN_HI.year - 1, raw)
    ts = pd.DatetimeIndex(m["ts_utc"])
    m = m[np.asarray((ts >= M.LEARN_LO) & (ts < M.LEARN_HI))].reset_index(drop=True)
    source, bid_only = str(m["source"].iloc[0]), bool(m["bid_only"].iloc[0])
    mm = D.Minutes(m)
    tiers = F.Tiers.from_csv(tiers_path, source="dukascopy" if name == F.GOLD else "histdata")
    cost = M.COST_BP.get(inst, M.FX_COST_BP)
    series: dict[tuple[str, ...], pd.DataFrame] = {}
    ncand: dict[str, int] = {}
    for tf in TFS:
        bars = D.decision_bars(mm, tf)
        hold = D.SPEC.h_by_tf[tf]
        g_dec = grid_decisions(bars["close_ns"].to_numpy(), tf)
        g_lab = label_xt(mm, name, tf, g_dec, source, bid_only, bars, tiers)
        for fam, fd in fams.items():
            dec = pd.DatetimeIndex(fd["decision"])
            d_ns = epoch_ns(dec)
            order = np.argsort(d_ns, kind="stable")
            kept = order[thin(d_ns[order], hold)]
            f_lab = label_xt(mm, name, tf, d_ns[kept], source, bid_only, bars, tiers)
            side = pd.Series(fd["side"].to_numpy(float)[kept], index=dec[kept])
            ncand[f"family {fam} decisions"] = int(len(fd))
            for head in HEADS:
                gname = geometry_name(tf, head)
                c = outcomes(f_lab, inst, cost, head, [M.SEED, sum(map(ord, inst)), tf_seconds(tf), len(head), 7],
                             fam_side=side)
                ncand[f"family {fam} {gname}"] = int(len(c))
                if c.empty:
                    continue
                day = c["day"].to_numpy()
                for col in (*M.FAMILY_OUTCOMES, "both_gross"):
                    series[(f"fam-{fam}", gname, col)] = M.cells(day, c[col].to_numpy(float))
        for head in HEADS:
            gname = geometry_name(tf, head)
            c = outcomes(g_lab, inst, cost, head, [M.SEED, sum(map(ord, inst)), tf_seconds(tf), len(head)])
            ncand[gname] = int(len(c))
            if c.empty:
                continue
            day = c["day"].to_numpy()
            for col in (*M.OUTCOMES, "both_gross"):
                series[("out", gname, col)] = M.cells(day, c[col].to_numpy(float))
        print(json.dumps({"inst": inst, "tf": tf, "grid": int(len(g_dec)), "s": round(time.perf_counter() - t0)}),
              flush=True)
    closes = pd.Series(mm.mid["close"], index=pd.DatetimeIndex(pd.to_datetime(mm.ts, unit="ns", utc=True)))
    closes = closes[np.isfinite(closes.to_numpy())]
    daily = pd.Series(closes.to_numpy(), index=M.session_day(pd.DatetimeIndex(closes.index))).groupby(level=0).last()
    rets = pd.Series(np.log(daily.to_numpy(float)), index=daily.index).diff()
    info = {"minutes": int(len(m)), "source": source, "bid_only": bid_only,
            "first": str(ts.min()), "last": str(ts.max()), "candidates": ncand,
            "wall_s": round(time.perf_counter() - t0, 1)}
    return inst, series, rets, info


# ------------------------------------------------------------------------------------------------------------ summary

def orientations(rets: pd.DataFrame) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """PC1 and gold-sign orientations per class from learn daily returns (measure_rho.main, unchanged)."""
    pc1: dict[str, dict[str, int]] = {}
    for cls, all_members in M.CLASSES.items():
        members = [m_ for m_ in all_members if m_ in rets.columns]       # a --instruments subset (smoke runs)
        if len(members) < 2:
            continue
        x = rets[members].dropna()
        x = (x - x.mean()) / x.std()
        _, vec = np.linalg.eigh(np.cov(x.to_numpy().T))
        load = vec[:, -1] if vec[:, -1].sum() >= 0 else -vec[:, -1]
        sign = {m_: int(np.sign(float(val)) or 1) for m_, val in zip(members, load, strict=True)}
        if M.GOLD in sign and sign[M.GOLD] < 0:
            sign = {k: -s for k, s in sign.items()}
        pc1[cls] = sign
    gsign: dict[str, dict[str, int]] = {}
    for cls, all_members in M.CLASSES.items():
        members = [m_ for m_ in all_members if m_ in rets.columns]
        if M.GOLD in members and len(members) > 1:
            x = rets[members].dropna()
            gsign[cls] = {m_: (1 if m_ == M.GOLD else int(np.sign(float(x[m_].corr(x[M.GOLD]))) or 1)) for m_ in members}
    return pc1, gsign


def icc_jobs(per: Mapping[tuple[str, ...], list[pd.DataFrame]], pc1: Mapping[str, Mapping[str, int]],
             gsign: Mapping[str, Mapping[str, int]], boot: int) -> list[tuple[str, str, pd.DataFrame, str | None, int, int]]:
    jobs = []
    seed = M.SEED
    for key, parts in per.items():
        full = pd.concat(parts, ignore_index=True)
        directional = key[2] != "both_gross"
        for cls, members in M.CLASSES.items():
            tab = full[full["inst"].isin(members)]
            if tab.empty or tab["day"].nunique() < 20:
                continue
            gold = M.GOLD if (cls in M.VS_GOLD and M.GOLD in members) else None
            orients: list[tuple[str, Mapping[str, int] | None]] = [("raw", None)]
            if directional and cls in pc1:
                orients.append(("pc1", pc1[cls]))
            if directional and cls in gsign and gsign[cls] != pc1.get(cls):
                orients.append(("goldsign", gsign[cls]))
            for oname, osign in orients:
                t = tab
                if osign is not None:
                    s = t["inst"].map(osign).to_numpy(float)
                    t = t.assign(S=t["S"].to_numpy() * s)
                seed += 1
                jobs.append(("|".join(key) + f"|{oname}", cls, t[["inst", "day", "n", "S", "Q"]], gold, boot, seed))
    return jobs


def cells_table(out_rows: Sequence[tuple[str, str, Mapping[str, Any]]]) -> pd.DataFrame:
    rows = []
    for jkey, cls, r in out_rows:
        kind, geom, col, orient = jkey.split("|")
        row: dict[str, Any] = {"kind": kind, "class": cls, "key": jkey,
                               **{rk: rv for rk, rv in r.items() if not isinstance(rv, dict)},
                               "source": "grid" if kind == "out" else kind.removeprefix("fam-"), "geometry": geom,
                               "outcome": col, "orientation": orient, "band": BAND_OF[geom], "head": geom.split()[-1],
                               "tf": geom.split()[0]}
        for rk, rv in r.items():
            if isinstance(rv, dict):
                for kk, vv in rv.items():
                    row[f"{rk}.{kk}"] = vv
        rows.append(row)
    return pd.DataFrame(rows)


def worst(t: pd.DataFrame, stat: str) -> dict[str, Any]:
    """The largest upper 95 % bound of `stat` over the variants in `t` (measure_rho.main's rule)."""
    col = f"{stat}.hi95"
    if col not in t.columns:
        return {"upper95": None}
    t = t[t[col].notna()]
    if t.empty:
        return {"upper95": None}
    top = t.iloc[int(np.argmax(t[col].to_numpy(float)))]
    return {"upper95": float(top[col]), "point_at_max": float(top[f"{stat}.point"]), "argmax": str(top["key"]),
            "point_range": [float(t[f"{stat}.point"].min()), float(t[f"{stat}.point"].max())],
            "days": [int(t["days"].min()), int(t["days"].max())], "variants": int(len(t))}


def summarise(src: pd.DataFrame, bands: Sequence[str] = BANDS) -> dict[str, dict[str, Any]]:
    """measure_rho.main's `summarise`, unchanged: per (class, bucket) the worst upper 95 % of rho_u,day, rho_u,inst
    (day-mean and pair), vs gold, and the between-instrument SD of mean gross T2 R (both-sides and long, raw)."""
    o = src[src["outcome"] != "both_gross"]
    res: dict[str, dict[str, Any]] = {"rho_u_day": {}, "rho_u_inst": {}, "rho_u_inst_vs_gold": {}, "tau_sd": {}}
    for band in bands:
        for cls in M.CLASSES:
            t = o[(o["band"] == band) & (o["class"] == cls)]
            k = f"{cls}|{band}"
            res["rho_u_day"][k] = worst(t, "within_day")
            if cls != "gold":
                res["rho_u_inst"][k] = {"agg": worst(t, "cross_agg"), "pair": worst(t, "cross_pair")}
            if cls in M.VS_GOLD:
                res["rho_u_inst_vs_gold"][k] = {"agg": worst(t, "vs_gold_agg"), "pair": worst(t, "vs_gold_pair")}
            tt = src[(src["band"] == band) & (src["class"] == cls) & (src["head"] == "T2")
                     & src["outcome"].isin(["both_gross", "long_gross"]) & (src["orientation"] == "raw")]
            if cls != "gold" and len(tt):
                res["tau_sd"][k] = worst(tt, "between_inst_sd")
    return res


def _up(w: Mapping[str, Any] | None) -> float | None:
    return None if not w else w.get("upper95")


def _mx(*vals: float | None) -> float | None:
    v = [float(x) for x in vals if x is not None and math.isfinite(float(x))]
    return max(v) if v else None


def _cap(v: float | None) -> float | None:          # a correlation bound above 1 is sampling noise: capped
    return None if v is None else min(1.0, v)


def rerun_binding(by_source: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """The re-run's own binding (measure_rho.main's rule): max(grid, families) per class x bucket; tau_a =
    max(0.01, that) and lambda_a = 1.35 / tau_a^2."""
    grid_s, fam_s, pool_s = by_source["grid"], by_source["families"], by_source["family:all"]
    out: dict[str, dict[str, Any]] = {"rho_u_day": {}, "rho_u_inst_day_mean": {}, "rho_u_inst_vs_gold_day_mean": {},
                                      "tau_a": {}}
    for bk in grid_s["rho_u_day"]:
        g_, f_ = _up(grid_s["rho_u_day"][bk]), _up(fam_s["rho_u_day"].get(bk))
        out["rho_u_day"][bk] = {"grid": g_, "families": f_, "families_pooled": _up(pool_s["rho_u_day"].get(bk)),
                                "max": _cap(_mx(g_, f_))}
    for name, sk in (("rho_u_inst_day_mean", "rho_u_inst"), ("rho_u_inst_vs_gold_day_mean", "rho_u_inst_vs_gold")):
        for bk, w in grid_s[sk].items():
            g_, f_ = _up(w["agg"]), _up(fam_s[sk].get(bk, {}).get("agg"))
            out[name][bk] = {"grid": g_, "families": f_, "families_pooled": _up(pool_s[sk].get(bk, {}).get("agg")),
                             "max": _cap(_mx(g_, f_))}
    for bk, w in grid_s["tau_sd"].items():
        g_, f_ = _up(w), _up(fam_s["tau_sd"].get(bk))
        m_ = _mx(g_, f_)
        tau = max(0.01, m_) if m_ is not None else None
        out["tau_a"][bk] = {"grid": g_, "families": f_, "families_pooled": _up(pool_s["tau_sd"].get(bk)),
                            "tau_a": tau, "lambda_a": 1.35 / tau ** 2 if tau else None}
    return out


def bind(rerun: Mapping[str, Mapping[str, Mapping[str, Any]]],
         recorded: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, dict[str, Any]]:
    """The D-12-xt binding (quant ruling on #514): rho_u_day and both rho_u_inst levels (class day-mean, vs-gold
    day-mean) bind at max(D-12 recorded `max`, re-run `max`) per cell; le-60m rho_u_day also carries rho_value =
    max(0.05, that); tau_a / lambda_a are the re-run's only (the recorded tau_a is reported, never used)."""
    out: dict[str, dict[str, Any]] = {}
    for name in ("rho_u_day", "rho_u_inst_day_mean", "rho_u_inst_vs_gold_day_mean"):
        out[name] = {}
        for bk in sorted(set(rerun.get(name, {})) | set(recorded.get(name, {}))):
            r = dict(rerun.get(name, {}).get(bk) or {})
            rec = (recorded.get(name, {}).get(bk) or {}).get("max")
            e = {**r, "rerun": r.get("max"), "d12_recorded": rec, "max": _cap(_mx(r.get("max"), rec))}
            if name == "rho_u_day" and bk.endswith("|le60m"):
                e["rho_value"] = max(0.05, e["max"]) if e["max"] is not None else None
            out[name][bk] = e
    out["tau_a"] = {}
    for bk in sorted(set(rerun.get("tau_a", {})) | set(recorded.get("tau_a", {}))):
        r = dict(rerun.get("tau_a", {}).get(bk) or {})
        rec = (recorded.get("tau_a", {}).get(bk) or {}).get("tau_a")
        tau = r.get("tau_a")
        out["tau_a"][bk] = {**r, "tau_a": tau, "lambda_a": 1.35 / tau ** 2 if tau else None, "d12_recorded_tau_a": rec,
                            "rule": "re-run only"}
    return out


def side_by_side(binding: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """One row per class x bucket: D-12 recorded, re-run and binding of each quantity (the ruling's 'both reported')."""
    rows = []
    for cls in M.CLASSES:
        for band in BANDS:
            bk = f"{cls}|{band}"
            r: dict[str, Any] = {"class": cls, "bucket": band}
            for name, short in (("rho_u_day", "rho_u_day"), ("rho_u_inst_day_mean", "rho_u_inst"),
                                ("rho_u_inst_vs_gold_day_mean", "rho_u_inst_vs_gold")):
                e = binding.get(name, {}).get(bk)
                if e:
                    r[short] = {"d12": e.get("d12_recorded"), "rerun": e.get("rerun"), "binding": e.get("max")}
            t = binding.get("tau_a", {}).get(bk)
            if t:
                r["tau_a"] = {"d12": t.get("d12_recorded_tau_a"), "rerun": t.get("tau_a"), "binding": t.get("tau_a")}
            rows.append(r)
    return rows


PER_TF_RULE = ("quant ruling 1 on #525 (bind per tf): rho_u,inst(tf) = max(D-12 binding of the tf's bucket, the "
               "cumulative max of the re-run per-tf value over holds <= this tf), the same for the vs-gold level; "
               "rho_u,day(tf) = max(D-12 bucket value, re-run at the tf), undefined for H >= 1 d (design effect 1); "
               "tau_a(tf) = the re-run at the tf only")


def binding_by_tf(tab: pd.DataFrame, recorded: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, Any]:
    """Quant ruling 1 on #525: the binding per class x tf (`PER_TF_RULE`) from the cells table (grid and families
    together = max(grid, families)) and the D-12 recorded binding per bucket. Keys `<class>|<tf>`."""
    per = summarise(tab.assign(band=tab["tf"]), bands=TFS)
    out: dict[str, Any] = {"rule": PER_TF_RULE, "rho_u_day": {}, "rho_u_inst_day_mean": {},
                           "rho_u_inst_vs_gold_day_mean": {}, "tau_a": {}}
    for cls in M.CLASSES:
        cum: dict[str, float | None] = {"rho_u_inst_day_mean": None, "rho_u_inst_vs_gold_day_mean": None}
        for tf in TFS:
            k, bk = f"{cls}|{tf}", f"{cls}|{hold_bucket(tf)}"
            d12 = (recorded.get("rho_u_day", {}).get(bk) or {}).get("max")
            if D.SPEC.h_by_tf[tf] >= DAY_S:
                out["rho_u_day"][k] = {"d12": d12, "rerun": None, "max": None, "design_effect_1": True}
            else:
                r = _cap(_up(per["rho_u_day"].get(k)))
                out["rho_u_day"][k] = {"d12": d12, "rerun": r, "max": _cap(_mx(d12, r))}
            for name, sk in (("rho_u_inst_day_mean", "rho_u_inst"), ("rho_u_inst_vs_gold_day_mean",
                                                                     "rho_u_inst_vs_gold")):
                if k not in per[sk]:
                    continue
                r = _cap(_up(per[sk][k]["agg"]))
                cum[name] = _mx(cum[name], r)
                d12i = (recorded.get(name, {}).get(bk) or {}).get("max")
                out[name][k] = {"d12": d12i, "rerun": r, "rerun_cummax": cum[name], "max": _cap(_mx(d12i, cum[name]))}
            if k in per["tau_sd"]:
                r = _up(per["tau_sd"][k])
                tau = max(0.01, r) if r is not None else None
                out["tau_a"][k] = {"rerun": r, "tau_a": tau, "lambda_a": 1.35 / tau ** 2 if tau else None}
    return out


def assemble(*, binding: Mapping[str, Any], by_source: Mapping[str, Any], recorded_path: str, recorded_sha: str,
             recorded: Mapping[str, Any], meta: Mapping[str, Any]) -> dict[str, Any]:
    """The D-12-xt JSON (without `cells_file`, set by `write`). rho_x is not copied: `rho_x_from` names the D-12 file
    (sha256), which `sr_v3.rho_inputs` follows (quant F7 on #525: the JSON stays under the 800 kB cap)."""
    rd = binding["rho_u_day"]
    return {
        "schema": SCHEMA, "item": ITEM,
        "exit_freeze_sha": D.EXIT_FREEZE_SHA256,
        "geometry": geometry_record(),
        "window": {"name": "learn", "from": str(M.LEARN_LO), "to_exclusive": str(M.LEARN_HI)},
        "never_read": ["test_old 2006-02..2009-12", "confirm 2018-01..2025-09", "holdout 2025-10-01 onward",
                       "any dense-label store value (labels computed here) or sr-v3 / H-102 outcome"],
        "candidates": [
            {"kind": "control", "name": "control_grid",
             "how": "H < 1 d: every (H/tf)-th tf-bar close of each NY-17:00 session day; H >= 1 d: the first close "
                    "of every ceil(H/1 d)-th business session day (1w: every ceil(H/1 w)-th epoch week); same "
                    "times on every instrument"},
            *({"kind": "family", "name": fam, "arms": list(arms), "replay": "measure_rho.family_decisions (D-12)",
               "labelled_with": "every xt-v1 geometry; greedy non-overlap at hold H per instrument"}
              for fam, arms in M.FAMILIES.items())],
        "excluded_arms": list(M.EXCLUDED_ARMS),
        "binds_with": {"path": recorded_path, "sha256": recorded_sha, "rule": BIND_RULE},
        "stated_deviations": [
            "rows are the factory's usable rows: label window (decision month .. horizon end) A / A-consistent on the "
            "D-1 served tiers (gold graded too); D-12 used gold unfiltered and the r11-v2.1 HistData tiers per minute",
            "HistData has no ask: ask = bid (as the dense labels); XAG and FX bps costs are D-12's assumptions",
            "rho_x is the D-12 file's (`rho_x_from`; label-blind features; the exit geometry does not enter)",
            "hold = H puts 1m alone in le60m and 5m alone in 1h-4h; gt4h holds 8 h .. 20 d",
        ],
        "geometries": [g for _, _, g in GEOMETRIES],
        "bootstrap": {"block": "ISO week of the NY-17:00 session day, all instruments jointly", "B": meta["boot"],
                      "upper": "97.5th percentile", "seed": M.SEED},
        "script": meta["script"], "data": meta["data"],
        "classes": {k: list(v) for k, v in M.CLASSES.items()},
        "not_measurable": recorded.get("not_measurable"),
        "pc1_orientation": meta["pc1"], "gold_sign_orientation": meta["gsign"],
        "rho_x_from": {"path": recorded_path, "sha256": recorded_sha, "note": "D-12a rho_x, read from this file"},
        "binding": binding,
        "side_by_side": side_by_side(binding),
        "by_source": by_source,
        "rho_day_le_60m": {cls: rd[f"{cls}|le60m"].get("rho_value") for cls in M.CLASSES},
    }


def finish(tab: pd.DataFrame, *, recorded: Mapping[str, Any], recorded_path: str, recorded_sha: str,
           meta: Mapping[str, Any]) -> dict[str, Any]:
    """Every summary and binding from the cells table: by source (grid / families in the JSON; the per-family detail
    is the cells file's `source` column), the bucket binding, the per-tf binding (ruling 1) and the per-tf re-run."""
    full = {"grid": summarise(tab[tab["source"] == "grid"]), "families": summarise(tab[tab["source"] != "grid"]),
            "family:all": summarise(tab[tab["source"] == "all"])}
    binding = bind(rerun_binding(full), recorded["binding"])
    out = assemble(binding=binding, by_source={k: full[k] for k in ("grid", "families")}, recorded_path=recorded_path,
                   recorded_sha=recorded_sha, recorded=recorded, meta=meta)
    out["binding_by_tf"] = binding_by_tf(tab, recorded["binding"])
    out["by_tf_rerun"] = summarise(tab.assign(band=tab["tf"]), bands=TFS)
    return out


def read_cells(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, compression="gzip")


def script_record(head: str) -> dict[str, Any]:
    script = Path(__file__).resolve()
    return {"path": str(script.relative_to(ROOT)), "sha256": M.sha256_file(script), "git_blob": M.git_blob(script),
            "uses": {"scripts/research/measure_rho.py": M.sha256_file(ROOT / "scripts/research/measure_rho.py"),
                     "goldbot/research/dense_labels.py": M.sha256_file(ROOT / "goldbot/research/dense_labels.py")},
            "base_commit": head}


def write(out_dir: Path, stem: str, out: dict[str, Any], tab: pd.DataFrame) -> tuple[Path, Path]:
    """`<stem>.json` and `<stem>-cells.csv.gz` (aggregate statistics only) under `out_dir`; the JSON records the
    cells file's repo-relative path and sha256."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cells_path = out_dir / f"{stem}-cells.csv.gz"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(tab.sort_values("key").to_csv(index=False, float_format="%.6g").encode())
    cells_path.write_bytes(buf.getvalue())
    return write_json(out_dir / f"{stem}.json", out, cells_path, len(tab)), cells_path


def write_json(js: Path, out: dict[str, Any], cells_path: Path, rows: int) -> Path:
    rel = cells_path.relative_to(ROOT) if cells_path.is_relative_to(ROOT) else cells_path
    out = {**out, "cells_file": {"path": str(rel), "sha256": M.sha256_file(cells_path), "rows": int(rows)}}
    js.write_text(json.dumps(M.rounded(out), indent=1, default=str) + "\n")
    return js


def rebind(js: Path, recorded_path: str) -> Path:
    """Recompute every summary and binding of a committed D-12-xt file from its own cells file (no market data read,
    no measurement repeated); the measurement's provenance (`measurement_run`: the measuring script's sha256 / blob /
    commit, the data shas, the bootstrap) is kept, `script` becomes this script."""
    old = json.loads(js.read_text())
    cells_path = ROOT / old["cells_file"]["path"]
    if M.sha256_file(cells_path) != old["cells_file"]["sha256"]:
        raise SystemExit(f"{cells_path} differs from the sha256 its JSON records: refused")
    rec_path = ROOT / recorded_path
    recorded = json.loads(rec_path.read_text())
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    run = old.get("measurement_run") or old["script"]
    meta = {"boot": old["bootstrap"]["B"], "pc1": old["pc1_orientation"], "gsign": old["gold_sign_orientation"],
            "script": script_record(head), "data": old["data"]}
    tab = read_cells(cells_path)
    out = finish(tab, recorded=recorded, recorded_path=recorded_path, recorded_sha=M.sha256_file(rec_path), meta=meta)
    out["measurement_run"] = run
    return write_json(js, out, cells_path, len(tab))


# ------------------------------------------------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=ROOT / "raw")
    ap.add_argument("--workers", type=int, default=3, help="instrument workers (each ~1.5 GB)")
    ap.add_argument("--icc-workers", type=int, default=8)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--recorded", default=RECORDED)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "research")
    ap.add_argument("--stem", default=STEM)
    ap.add_argument("--instruments", default="", help="comma list (default: every class member)")
    ap.add_argument("--rebind", type=Path, default=None,
                    help="recompute the summaries and bindings of this committed JSON from its cells file and exit")
    a = ap.parse_args()
    if a.rebind is not None:
        print(rebind(a.rebind, a.recorded))
        return
    if max(a.workers, a.icc_workers) > 8:
        raise SystemExit("at most 8 workers (8 CPU threads)")
    if D.exit_freeze_hash(D.SPEC) != D.EXIT_FREEZE_SHA256:
        raise SystemExit("this host's label spec does not hash to xt-v1: refused (sr-v3 D-4)")
    rec_path = ROOT / a.recorded
    recorded = json.loads(rec_path.read_text())
    insts = sorted({i for v in M.CLASSES.values() for i in v})
    if a.instruments:
        insts = [i for i in insts if i in set(a.instruments.split(","))]
    tiers_path = a.raw / TIERS_FILE
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(work, [(str(a.raw), i, str(tiers_path)) for i in insts]))
    per: dict[tuple[str, ...], list[pd.DataFrame]] = {}
    for inst, series, _, _ in res:
        for k, v in series.items():
            if len(v):
                per.setdefault(k, []).append(v.assign(inst=inst))
    rets = pd.DataFrame({r[0]: r[2] for r in res})
    info = {r[0]: r[3] for r in res}
    pc1, gsign = orientations(rets)
    jobs = icc_jobs(per, pc1, gsign, a.boot)
    print(json.dumps({"icc_jobs": len(jobs)}), flush=True)
    with ProcessPoolExecutor(a.icc_workers) as ex:
        out_rows = list(ex.map(M.job, jobs, chunksize=4))
    tab = cells_table(out_rows)
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    data_files = sorted({p for i in insts for p in M.files_for(a.raw, i) if p.exists()})
    meta = {"boot": a.boot, "pc1": pc1, "gsign": gsign, "script": script_record(head),
            "data": {"tiers_file": {"path": f"raw/{TIERS_FILE}", "sha256": M.sha256_file(tiers_path),
                                    "tiers": ["A", "A-consistent"]},
                     "d12_family_tiers_file": {"path": f"raw/{M.TIERS_FILE}",
                                               "sha256": M.sha256_file(a.raw / M.TIERS_FILE)},
                     "bars": [{"path": f"raw/{p.relative_to(a.raw)}", "sha256": M.sha256_file(p)} for p in data_files],
                     "instruments": info}}
    out = finish(tab, recorded=recorded, recorded_path=a.recorded, recorded_sha=M.sha256_file(rec_path), meta=meta)
    js, cells = write(a.out, a.stem, out, tab)
    print(json.dumps({"json": str(js), "cells": str(cells), "rho_day_le_60m": out["rho_day_le_60m"]}, indent=1))


if __name__ == "__main__":
    main()
