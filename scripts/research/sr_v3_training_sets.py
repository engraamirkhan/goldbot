"""Training-ready sr-v3 datasets: verify and manifest the dl-v1 + fs-v1 + weights stores the sr-v3 trainer reads
(goldbot/research/sr_v3_train.py `StoreData`; card docs/research/prereg-2026-11-26-sr-v3.md rev 8), per cell
(instrument x tf), learn 2010-17, phase 0. Outcome-blind: this script never reads a label value (it reads the
skeleton, the fs-v1 rows, the weights, the tiers and raw minutes), so it is legitimate on a sealed store.

    PYTHONPATH=<source the stores were built from> python scripts/research/sr_v3_training_sets.py \
        --instruments XAUUSD --out ~/tmp/sr-v3-trainsets

Per cell it records: the partition keys the trainer will pick (current label spec and code hash, exactly one per
year), rows (bars, T2 rows both sides, tier-usable under each column set, joined to fs-v1 and weights = the rows the
trainer's T2 frame holds before the T0 merge), the P30 columns present and their NaN share, the data_version
(`StoreData.version`'s formula for this cell), and quality checks:

- look_ahead: every fs-v1 row is available at or before its decision (`available_utc <= decision_utc`), its window
  starts before it, entry is at or after the decision, the label resolves after the horizon, no learn row's horizon
  passes the learn end, nothing touches the sealed holdout (holdout_guard);
- join: every T2 skeleton bar has exactly one fs-v1 row and one weight row (as-of on availability: the fs-v1 row for a
  decision is the one available at it, checked above), no duplicate (phase, decision_utc);
- missing_bars: in-session 1m minutes missing from the raw source over the learn years (gaps probed against the
  session table, as `quality.check_bars`), threshold 0.1 % (flagged, never dropped);
- cross_source: the r11 tiers grade each month against the reference source (A / A-consistent = agrees); the share of
  learn months at A/A-cons and of T2 rows usable under P30.

Normalisation inputs (past data only): per fold Y (test year; 2018 = v0) the P30 design columns' count, mean and sd
over the training rows the trainer's `train_mask` admits (horizon end before Y minus the label embargo), tier-usable,
and the 2010-11 medians the trainer imputes with. The trainer computes its own; these are the cross-check.

Writes <out>/<instrument>/<tf>.json, <out>/<instrument>/<tf>.norm.parquet and <out>/manifest.json (local only, never
committed: market-derived, ADR 0048).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data import holdout_guard
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.timeutil import epoch_ns
from goldbot.research import dense_labels as D
from goldbot.research import feature_store as F
from goldbot.research import windows as WN

ROOT = Path(__file__).resolve().parents[2]
LEARN_YEARS: tuple[int, ...] = tuple(range(2010, 2018))
TFS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")
MEMBERS: tuple[str, ...] = ("XAUUSD", "AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "NZDUSD", "USDCAD",
                            "USDCHF", "USDJPY", "XAGUSD")      # = sr_v3.POOLED_MEMBERS (gold, FX10, metals)
UNITS: dict[str, tuple[str, ...]] = {"1m": ("1m",), "5m": ("5m",), "15m": ("15m",), "30m": ("30m",), "1h": ("1h",),
                                     "2h": ("2h",), "4h": ("4h",), "1d+1w": ("1d", "1w")}   # = sr_v3.UNITS
MISSING_MAX = 0.001                                           # in-session minutes missing, share


def _ns(s: Any) -> np.ndarray:
    return np.asarray(epoch_ns(pd.DatetimeIndex(pd.to_datetime(s, utc=True))))


def file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------- partition keys
def label_keys(labels: Path, instrument: str, tf: str, year: int, phase: int = 0) -> tuple[str, str] | None:
    """(label key, fs key) of the one partition under the current spec and code hash, as `StoreData.keys`."""
    d = Path(labels) / D.SPEC.spec_id / instrument / tf / f"p{phase}" / str(year)
    want_s, want_c = D.spec_hash(), D.code_hash()
    cands = [m for m in (json.loads(p.read_text()) for p in sorted(d.glob("*.json")))
             if m.get("spec_hash") == want_s and m.get("code_hash") == want_c]
    if not cands:
        return None
    if len(cands) > 1:
        raise ValueError(f"dl-v1 {instrument} {tf} p{phase} {year}: {len(cands)} current partitions; prune one")
    return str(cands[0]["key"]), str(cands[0]["fs_key"])


def data_version(labels: Path, tiers_path: Path, members: Sequence[str], tfs: Sequence[str],
                 phases: Sequence[int] = (0,)) -> str:
    """`sr_v3_train.StoreData.version`: the identity a rebuild needs (label spec and code hash, tiers sha, keys)."""
    keys = {f"{i}:{t}:{y}:{p}": label_keys(labels, i, t, y, p) for i in members for t in tfs for y in LEARN_YEARS
            for p in phases}
    blob = {"spec": D.spec_hash(), "code": D.code_hash(), "tiers": file_sha256(tiers_path), "keys": keys}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- checks
def look_ahead(fs: pd.DataFrame, sk: pd.DataFrame, learn_end: pd.Timestamp) -> dict[str, int]:
    """Violations (counts; all must be 0). fs: fs-v1 rows; sk: skeleton rows (outcome-blind)."""
    dec = _ns(fs["decision_utc"])
    out = {"fs_available_after_decision": int((_ns(fs["available_utc"]) > dec).sum()),
           "fs_window_not_before_decision": int((_ns(fs["window_from_utc"]) >= dec).sum())}
    sd = _ns(sk["decision_utc"])
    ok = sk["label_ok"].to_numpy(bool)
    ent = sk["entry_utc"]
    out["label_ok_without_entry"] = int((ent.isna().to_numpy() & ok).sum())
    out["entry_not_after_decision"] = int(((_ns(ent.fillna(sk["decision_utc"])) <= sd) & ok).sum())   # C5: strictly after
    hz = _ns(sk["horizon_end_utc"])
    out["horizon_not_after_decision"] = int((hz <= sd)[ok].sum())
    # the label is knowable when the last minute it reads is visible: after the decision, never after the horizon's
    # data (it may precede horizon_end when the source has no minute at the horizon end: a gap, not a leak)
    out["label_available_not_after_decision"] = int((_ns(sk["label_available_utc"]) <= sd)[ok].sum())
    out["label_ok_horizon_past_learn_end"] = int((hz > learn_end.value)[ok].sum())
    for what, frame, col in (("fs", fs, "decision_utc"), ("skeleton", sk, "horizon_end_utc")):
        try:
            holdout_guard.refuse(frame, what=what, col=col)
            out[f"holdout_{what}"] = 0
        except holdout_guard.HoldoutSealed:
            out[f"holdout_{what}"] = int(holdout_guard.in_holdout(frame, col).sum())
    return out


def missing_minutes(minutes: pd.DataFrame, years: Sequence[int], *, max_probe_min: int = 6 * 60) -> dict[str, Any]:
    """In-session 1m minutes absent from the source over `years`: every gap between consecutive minutes up to
    `max_probe_min` is probed minute by minute against the session table (a market close is not a missing bar);
    longer gaps are closures (weekends, holidays) and counted apart. Share = missing / (present + missing)."""
    ts = pd.DatetimeIndex(pd.to_datetime(minutes["ts_utc"], utc=True))
    ts = ts[np.isin(ts.year, list(years))].sort_values()
    ns = epoch_ns(ts)
    gap = np.diff(ns) // 60_000_000_000
    idx = np.flatnonzero((gap > 1) & (gap <= max_probe_min))
    miss = 0
    by_year: dict[int, int] = {}
    for i in (int(j) for j in idx):
        probe = pd.date_range(ts[i], ts[i + 1], freq="60s", inclusive="neither")
        k = int(DEFAULT_SESSIONS.is_open(probe).sum())
        if k:
            miss += k
            by_year[ts[i].year] = by_year.get(ts[i].year, 0) + k
    n = len(ts)
    share = miss / (n + miss) if n + miss else 1.0
    return {"present": n, "missing_in_session": miss, "share": share, "by_year": by_year,
            "long_closures": int((gap > max_probe_min).sum()), "pass": share < MISSING_MAX,
            "sessions": "DEFAULT_SESSIONS (gold table; for FX it ignores the gold daily break)"}


def cross_source(tiers: F.Tiers, instrument: str) -> dict[str, Any]:
    months = [f"{y}-{m:02d}" for y in LEARN_YEARS for m in range(1, 13)]
    t = tiers.tier_of(instrument, months)
    vc = pd.Series(t).value_counts()
    return {"tiers_key": tiers.key(instrument), "learn_months": len(months),
            "by_tier": {str(k): int(v) for k, v in vc.items()},
            "share_A_or_A_cons": float(np.isin(t, list(F.INTRADAY_TIERS)).mean())}


# ---------------------------------------------------------------------------------------------- normalisation
def train_cut(tf: str, test_year: int) -> int:
    """`sr_v3_train.train_mask`'s cut (ns): horizon end < Y start - label embargo; v0 (2018): <= learn end."""
    if test_year > LEARN_YEARS[-1]:
        return int(WN.SPANS[WN.Window.LEARN][1].value) + 1
    return int((pd.Timestamp(f"{test_year}-01-01", tz="UTC")
                - WN.label_embargo(pd.Timedelta(seconds=D.SPEC.h_by_tf[tf]))).value)


class NormAcc:
    def __init__(self, cols: Sequence[str], folds: Sequence[int]):
        self.cols = list(cols)
        k = len(cols)
        self.acc = {y: [np.zeros(k), np.zeros(k), np.zeros(k)] for y in folds}

    def add(self, X: np.ndarray, hz: np.ndarray, tf: str) -> None:
        for y, (n, s, q) in self.acc.items():
            m = hz < train_cut(tf, y)
            if not m.any():
                continue
            x = X[m]
            ok = np.isfinite(x)
            n += ok.sum(0)
            s += np.where(ok, x, 0.0).sum(0)
            q += np.where(ok, x * x, 0.0).sum(0)

    def frame(self) -> pd.DataFrame:
        rows = []
        for y, (n, s, q) in self.acc.items():
            with np.errstate(invalid="ignore", divide="ignore"):
                mu = s / n
                sd = np.sqrt(np.maximum(q / n - mu * mu, 0.0) * n / np.maximum(n - 1, 1))
            for c, a, b, d in zip(self.cols, n, mu, sd, strict=True):
                rows.append({"fold": y, "column": c, "n": int(a), "mean": float(b), "sd": float(d)})
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------- one cell
def cell(instrument: str, tf: str, *, labels: Path, features: Path, weights: Path, tiers: F.Tiers,
         tiers_path: Path, minutes_check: Mapping[str, Any] | None, out: Path) -> dict[str, Any]:
    from goldbot.research.self_retrain import design
    p30 = list(D.COLUMN_SETS["P30"])
    rec: dict[str, Any] = {"instrument": instrument, "tf": tf, "window": "learn", "years": list(LEARN_YEARS),
                           "phase": 0, "label_spec_hash": D.spec_hash(), "label_code_hash": D.code_hash(),
                           "fs_code_hash": F.code_hash(), "tiers_sha256": tiers.sha256}
    ks = {y: k for y in LEARN_YEARS if (k := label_keys(labels, instrument, tf, y)) is not None}
    rec["years_built"] = sorted(ks)
    rec["years_missing"] = [y for y in LEARN_YEARS if y not in ks]
    rec["keys"] = {str(y): {"label": k[0], "fs": k[1]} for y, k in ks.items()}
    rec["data_version"] = data_version(labels, tiers_path, [instrument], [tf])
    wp = weights / instrument / f"{tf}.parquet"
    rec["weights"] = {"path": str(wp), "sha256": file_sha256(wp) if wp.exists() else None}
    if not ks or not wp.exists():
        rec["status"] = "missing"
        return rec
    w = D._typed(pd.read_parquet(wp))[["phase", "decision_utc", "u_tf", "w"]]
    folds = tuple(range(LEARN_YEARS[0] + 1, LEARN_YEARS[-1] + 2))       # test years 2011..2017 and v0 (2018)
    norm = NormAcc(p30, folds)
    learn_end = WN.SPANS[WN.Window.LEARN][1]
    tot: dict[str, Any] = {"bars": 0, "t2_rows": 0, "label_ok_bars": 0, "dup_decisions": 0, "fs_unmatched_bars": 0,
                           "weight_unmatched_bars": 0, "w_sum": 0.0, "u_sum": 0.0,
                           **{f"usable_{cs}": 0 for cs in D.COLUMN_SETS}, "trainer_t2_rows": 0}
    la: dict[str, int] = {}
    nan = np.zeros(len(p30))
    n_x = 0
    med_rows = []
    cols_present: set[str] = set()
    for y, (lk, fk) in sorted(ks.items()):
        sk = D.skeleton(labels, instrument=instrument, tf=tf, years=[y], keys={(y, 0): lk})
        t2 = sk[sk["template"] == D.SPEC.judged_head].reset_index(drop=True)
        bars = t2[t2["side"] == 1]
        fs, _ = F.load(features, instrument=instrument, tf=tf, years=[y], tiers=None, purpose="parity",
                       expect_keys={(y, 0): fk})
        cols_present |= set(fs.columns)
        for k2, v in look_ahead(fs, t2, learn_end).items():
            la[k2] = la.get(k2, 0) + v
        tot["bars"] += len(bars)
        tot["t2_rows"] += len(t2)
        tot["label_ok_bars"] += int(bars["label_ok"].sum())
        tot["dup_decisions"] += int(bars.duplicated(["phase", "decision_utc"]).sum())
        key = ["phase", "decision_utc"]
        m_fs = bars[key].merge(fs[key].drop_duplicates(), on=key, how="left", indicator=True)
        tot["fs_unmatched_bars"] += int((m_fs["_merge"] == "left_only").sum())
        m_w = bars[key].merge(w, on=key, how="left", indicator=True)
        tot["weight_unmatched_bars"] += int((m_w["_merge"] == "left_only").sum())
        tot["w_sum"] += float(m_w["w"].fillna(0).sum())
        tot["u_sum"] += float(m_w["u_tf"].fillna(0).sum())
        for cs, cols in D.COLUMN_SETS.items():
            u = D.attach_tiers(t2.drop(columns=["tier", "usable"]), tiers, columns=cols)
            tot[f"usable_{cs}"] += int((u["usable"] & (u["side"] == 1)).sum())
            if cs == "P30":
                ut = u[u["usable"].to_numpy(bool)]
        feats = fs.drop(columns=[c for c in ("instrument", "tf", "source", "bid_only", "group_id", "available_utc",
                                             "window_from_utc", "tier") if c in fs.columns])
        rows = ut[["phase", "decision_utc", "side", "horizon_end_utc"]].merge(feats, on=key, how="inner").merge(
            w[key], on=key, how="inner")
        tot["trainer_t2_rows"] += len(rows)
        if len(rows):
            X = design(rows).reindex(columns=p30).to_numpy(float)
            nan += (~np.isfinite(X)).sum(0)
            n_x += len(X)
            norm.add(X, _ns(rows["horizon_end_utc"]), tf)
            if y in (2010, 2011):
                med_rows.append(X)
    rec["rows"] = tot
    rec["columns"] = {"P30": p30, "core-12": list(D.COLUMN_SETS["core-12"]), "core-6": list(D.COLUMN_SETS["core-6"]),
                      "fs_missing_P30": sorted(c for c in p30 if c != "side" and c not in cols_present),
                      "nan_share_P30": {c: round(float(v / n_x), 5) if n_x else None
                                        for c, v in zip(p30, nan, strict=True)}}
    nf = norm.frame()
    if med_rows:
        med = np.nanmedian(np.vstack(med_rows), axis=0)
        nf = pd.concat([nf, pd.DataFrame({"fold": 0, "column": p30, "n": 0, "mean": med, "sd": np.nan})],
                       ignore_index=True)       # fold 0 = the 2010-11 medians (imputation)
    (out / instrument).mkdir(parents=True, exist_ok=True)
    norm_path = out / instrument / f"{tf}.norm.parquet"
    nf.to_parquet(norm_path, index=False)
    rec["normalisation"] = {"path": str(norm_path), "sha256": file_sha256(norm_path), "folds": list(folds),
                            "rule": "per fold Y: rows with horizon_end < Y-01-01 - label_embargo(H) (v0 2018: <= "
                                    "learn end), tier-usable under P30, unweighted; fold 0 = 2010-11 medians"}
    q: dict[str, Any] = {"look_ahead": {"violations": la, "pass": not any(la.values())},
                         "join": {"dup_decisions": tot["dup_decisions"], "fs_unmatched_bars": tot["fs_unmatched_bars"],
                                  "weight_unmatched_bars": tot["weight_unmatched_bars"],
                                  "pass": not (tot["dup_decisions"] or tot["fs_unmatched_bars"]
                                               or tot["weight_unmatched_bars"])},
                         "missing_bars": minutes_check,
                         "cross_source": {**cross_source(tiers, instrument),
                                          "t2_usable_P30_share": tot["usable_P30"] / max(1, tot["label_ok_bars"])},
                         "columns": {"pass": not rec["columns"]["fs_missing_P30"]},
                         "years": {"pass": not rec["years_missing"]}}
    rec["quality"] = q
    hard = ("look_ahead", "join", "columns")
    rec["status"] = "ready" if all(q[k]["pass"] for k in hard) else "failed"
    rec["warnings"] = [k for k in ("missing_bars", "years") if q[k] and not q[k]["pass"]]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instruments", default=",".join(MEMBERS))
    ap.add_argument("--tfs", default=",".join(TFS))
    ap.add_argument("--labels", type=Path, default=D.CACHE)
    ap.add_argument("--features", type=Path, default=F.CACHE)
    ap.add_argument("--weights", type=Path, default=None)
    ap.add_argument("--tiers", type=Path, default=ROOT / "raw" / "tiers" / "tiers_served_r11-v2.1.csv")
    ap.add_argument("--raw", type=Path, default=ROOT / "raw")
    ap.add_argument("--out", type=Path, default=Path.home() / "tmp" / "sr-v3-trainsets")
    ap.add_argument("--no-minutes", action="store_true", help="skip the raw-minute missing-bar check")
    a = ap.parse_args()
    weights = a.weights or a.labels / "_weights"
    a.out.mkdir(parents=True, exist_ok=True)
    mp = a.out / "manifest.json"
    man: dict[str, Any] = json.loads(mp.read_text()) if mp.exists() else {"cells": {}}
    for inst in (i for i in a.instruments.split(",") if i):
        tiers = F.Tiers.from_csv(a.tiers, source="dukascopy" if inst == F.GOLD else "histdata")
        mc = None
        if not a.no_minutes:
            m = F.history_minutes(inst, LEARN_YEARS[0], LEARN_YEARS[-1], a.raw)
            mc = missing_minutes(m, LEARN_YEARS)
            del m
        for tf in (t for t in a.tfs.split(",") if t):
            rec = cell(inst, tf, labels=a.labels, features=a.features, weights=weights, tiers=tiers,
                       tiers_path=a.tiers, minutes_check=mc, out=a.out)
            (a.out / inst).mkdir(parents=True, exist_ok=True)
            (a.out / inst / f"{tf}.json").write_text(json.dumps(rec, indent=1, default=str))
            man["cells"][f"{inst}:{tf}"] = {k: rec.get(k) for k in ("status", "data_version", "years_missing",
                                                                     "warnings")} | {
                "trainer_t2_rows": (rec.get("rows") or {}).get("trainer_t2_rows"), "path": str(a.out / inst / f"{tf}.json")}
            print(json.dumps({"cell": f"{inst}:{tf}", **man["cells"][f"{inst}:{tf}"]}), flush=True)
            mp.write_text(json.dumps(man, indent=1, default=str))
    built = sorted({k.split(":")[0] for k, v in man["cells"].items() if v["status"] == "ready"})
    man.update(label_spec_hash=D.spec_hash(), label_code_hash=D.code_hash(), fs_code_hash=F.code_hash(),
               tiers_sha256=file_sha256(a.tiers), labels=str(a.labels), features=str(a.features), weights=str(weights),
               written_utc=str(pd.Timestamp.now(tz="UTC")),
               unit_versions={body: {u: data_version(a.labels, a.tiers, mem, tfs) for u, tfs in UNITS.items()}
                              for body, mem in (("gold", ["XAUUSD"]), ("pooled", list(MEMBERS)))},
               ready_instruments=built)
    mp.write_text(json.dumps(man, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
