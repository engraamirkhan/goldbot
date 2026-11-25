"""Minute-revision audit for sr-v3 snapshot admission (pre-registered in
docs/research/prereg-amendment-2026-11-25-sr-v3-snapshot-admission.md section 2; run it only as written there).

Read-only: aux.db is opened `mode=ro`, the store is only read, nothing is written. No label, R or outcome column is
read. Run on the brain from /opt/goldbot as the goldbot user, e.g. `bash q.sh scripts/research/minute_revision_audit.py`
(the script is piped to the venv's python on stdin). Prints one JSON document.

R: per broker, the share of broker-feed minutes whose nominal-view value at the close differs from the PIT view at
close + LIVE_WRITE_GRACE. S: per broker, the share of sampled decision bars whose 39 sr-v2 features differ materially
between a snapshot from the PIT view and one from the nominal view (write stamps removed).
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

W0, W1 = pd.Timestamp("2026-11-14", tz="UTC"), pd.Timestamp("2026-11-24", tz="UTC")     # R: minutes in [W0, W1)
S0, S1 = pd.Timestamp("2026-11-17", tz="UTC"), pd.Timestamp("2026-11-24", tz="UTC")     # S: decisions in [S0, S1)
PER_CELL, SEED = 40, 20261125
PRICE_TOL, FEATURE_TOL_SD = 0.001, 0.05
R_MAX, S_MAX = 0.005, 0.05
MIN_MINUTES, MIN_SNAPSHOTS = 2000, 30
AGES = {"close": pd.Timedelta(minutes=3), "1h": pd.Timedelta(hours=1, minutes=1),
        "24h": pd.Timedelta(hours=24, minutes=1)}          # from the minute's open: close + grace, + 1 h, + 24 h


def wilson_upper(k: int, n: int, z: float = 1.959964) -> float | None:
    if n == 0:
        return None
    p = k / n
    return float((p + z * z / (2 * n) + z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n))


def strip(versions: pd.DataFrame) -> pd.DataFrame:
    """The nominal view: every stored version visible from its minute's close, no write stamp."""
    out = versions.copy()
    out["visible_at"] = pd.to_datetime(out["ts_utc"], utc=True) + pd.Timedelta(minutes=1)
    out["written"] = False
    return out


def pick(versions: pd.DataFrame) -> pd.DataFrame:
    """as_of_minutes' choice per minute among the given visible versions: lowest rank, latest visible."""
    v = versions.sort_values(["ts_utc", "rank", "visible_at"], ascending=[True, True, False], kind="stable")
    return v.drop_duplicates("ts_utc", keep="first").set_index("ts_utc")


def minute_r(versions: pd.DataFrame, prices: list[str]) -> dict[str, Any]:
    ts = pd.to_datetime(versions["ts_utc"], utc=True)
    v = versions[((ts >= W0) & (ts < W1)).to_numpy()]
    if v.empty:
        return {"minutes": 0}
    nom = pick(strip(v))
    out: dict[str, Any] = {"minutes": int(len(nom)), "nominal_rank": nom["rank"].value_counts().sort_index()
                           .to_dict()}
    vis = pd.to_datetime(v["visible_at"], utc=True)
    vts = pd.to_datetime(v["ts_utc"], utc=True)
    for name, age in AGES.items():
        pit = pick(v[(vis <= vts + age).to_numpy()]).reindex(nom.index)
        absent = pit[prices[0]].isna().to_numpy()
        diff = (np.abs(pit[prices].to_numpy(dtype=float) - nom[prices].to_numpy(dtype=float)) > PRICE_TOL).any(axis=1)
        bad = absent | diff
        out[name] = {"differ": int(bad.sum()), "absent": int(absent.sum()), "value": int((diff & ~absent).sum())}
    return out


def main() -> None:
    os.nice(19)
    sys.path.insert(0, os.getcwd())
    import yaml

    from goldbot.config import load_settings
    from goldbot.data import holdout_guard
    from goldbot.data.account_bars import PRICE_COLUMNS, minute_versions
    from goldbot.data.store import Store
    from goldbot.research import arms as arm_spec
    from goldbot.research.feature_snapshots import READ_DAYS, decision_features
    from goldbot.research.self_retrain import FEATURES

    settings = load_settings()
    store = Store(settings.data_root)
    accounts = yaml.safe_load(Path("config/accounts.yaml").read_text())["accounts"]
    now = pd.Timestamp.now(tz="UTC")
    inst = arm_spec.arm_instruments()
    conn = sqlite3.connect("file:state/aux.db?mode=ro", uri=True)
    obs = pd.read_sql_query("SELECT account_id, broker, arm_id, tf, decision_ns FROM exploration_obs "
                            "WHERE decision_ns >= ? AND decision_ns < ?", conn, params=(int(S0.value), int(S1.value)))
    conn.close()
    obs = obs[obs["arm_id"].map(lambda a: inst.get(str(a))) == "XAUUSD"]
    keys = obs[["account_id", "broker", "tf", "decision_ns"]].drop_duplicates()
    rng = np.random.default_rng(SEED)
    sample = []
    for _, g in keys.sort_values(["account_id", "tf", "decision_ns"]).groupby(["account_id", "tf"], sort=True):
        idx = np.sort(rng.choice(len(g), size=min(PER_CELL, len(g)), replace=False))
        sample.append(g.iloc[idx])
    samp = pd.concat(sample, ignore_index=True) if sample else keys.iloc[0:0]
    report: dict[str, Any] = {"run_utc": now.isoformat(), "R_window": [str(W0), str(W1)], "S_window": [str(S0), str(S1)],
                              "accounts": {}, "brokers": {}}
    snaps: list[dict[str, Any]] = []
    timing: list[float] = []
    for account in ("icm-demo", "icm-probe", "vantage-demo"):
        acc = accounts[account]
        broker, symbol = str(acc["broker"]), str(acc["symbol"])
        # R: broker feeds only (no reference minutes)
        ver = minute_versions(store, account_id=account, broker=broker, symbol=symbol, start=W0, end=W1, as_of=now,
                              reference=None)
        report["accounts"][account] = {"broker": broker, "symbol": symbol, "R": minute_r(ver, PRICE_COLUMNS)}
        del ver
        # S: the snapshot job's own read (reference included), both views
        mine = samp[samp["account_id"] == account]
        if mine.empty:
            continue
        first = pd.Timestamp(int(mine["decision_ns"].min()), tz="UTC")
        last = pd.Timestamp(int(mine["decision_ns"].max()), tz="UTC")
        pit = minute_versions(store, account_id=account, broker=broker, symbol=symbol,
                              start=first - pd.Timedelta(days=READ_DAYS), end=last, as_of=now)
        # quant R10: never read sealed-holdout minutes (added after the 2026-11-25 run, whose 100-day snapshot seed
        # reached into Aug-Sep 2026; disclosed in the prereg amendment section 6)
        pit = holdout_guard.cut(pit, what=f"revision audit snapshot seed of {account}")
        nom = strip(pit)
        for tf, dec_ns in zip(mine["tf"].astype(str), mine["decision_ns"].astype("int64"), strict=True):
            at = pd.Timestamp(int(dec_ns), tz="UTC")
            t = time.perf_counter()
            a = decision_features(pit, tf, at)
            timing.append(time.perf_counter() - t)
            b = decision_features(nom, tf, at)
            snaps.append({"broker": broker, "account": account, "tf": tf,
                          "pit": None if a is None else {f: a.features.get(f) for f in FEATURES},
                          "nom": None if b is None else {f: b.features.get(f) for f in FEATURES}})
        del pit, nom
    # S per broker: material difference vs the sample's PIT sd per feature
    both = [s for s in snaps if s["pit"] is not None and s["nom"] is not None]
    P = pd.DataFrame([s["pit"] for s in both], dtype=float)
    N = pd.DataFrame([s["nom"] for s in both], dtype=float)
    sd = P.std(ddof=1).replace(0.0, np.nan)
    nan_mismatch = (P.isna() != N.isna()).any(axis=1).to_numpy() if len(both) else np.zeros(0, dtype=bool)
    z = ((P - N).abs() / sd).fillna(0.0) if len(both) else P
    material = ((z > FEATURE_TOL_SD).any(axis=1).to_numpy() | nan_mismatch) if len(both) else np.zeros(0, dtype=bool)
    any_diff = ((P - N).abs().fillna(0.0) > 1e-12).any(axis=1).to_numpy() | nan_mismatch if len(both) else material
    brokers = sorted({str(a["broker"]) for a in report["accounts"].values()})
    for broker in brokers:
        r_n = sum(a["R"].get("minutes", 0) for a in report["accounts"].values() if a["broker"] == broker)
        r_k = {age: sum(a["R"][age]["differ"] for a in report["accounts"].values()
                        if a["broker"] == broker and age in a["R"]) for age in AGES}
        sel = np.array([s["broker"] == broker for s in both], dtype=bool)
        s_n, s_k = int(sel.sum()), int(material[sel].sum()) if len(both) else 0
        r_up, s_up = wilson_upper(r_k["close"], r_n), wilson_upper(s_k, s_n)
        enough = r_n >= MIN_MINUTES and s_n >= MIN_SNAPSHOTS
        verdict = "insufficient" if not enough else (
            "pass" if (r_up is not None and r_up <= R_MAX and s_up is not None and s_up <= S_MAX) else "fail")
        report["brokers"][broker] = {
            "R_minutes": r_n, "R_differ": r_k, "R_share": {k: (v / r_n if r_n else None) for k, v in r_k.items()},
            "R_upper95_close": r_up, "S_snapshots": s_n, "S_material": s_k, "S_share": s_k / s_n if s_n else None,
            "S_upper95": s_up, "S_any_difference": int(any_diff[sel].sum()) if len(both) else 0,
            "S_unsnapshotted": sum(1 for s in snaps if s["broker"] == broker and (s["pit"] is None or s["nom"] is None)),
            "verdict": verdict}
    if len(both):
        report["S_worst_features"] = z.gt(FEATURE_TOL_SD).sum().sort_values(ascending=False).head(8).to_dict()
    report["snapshot_seconds"] = {"n": len(timing), "median": float(np.median(timing)) if timing else None,
                                  "p95": float(np.quantile(timing, 0.95)) if timing else None}
    try:
        report["mem_available_mb"] = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                                              if line.startswith("MemAvailable"))) // 1024
    except (OSError, StopIteration):
        report["mem_available_mb"] = None
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
