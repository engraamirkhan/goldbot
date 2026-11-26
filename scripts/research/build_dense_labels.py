"""Build dl-v1 dense labels (goldbot/research/dense_labels.py) over fs-v1 rows, then the uniqueness weights and the
n_eff per (instrument, tf, window). Counts only: this script never reads a label value (it reads the outcome-blind
skeleton: decisions, horizons, tiers).

    python scripts/research/build_dense_labels.py --instruments XAUUSD --tfs all --years 2010-2017 --workers 4
    python scripts/research/build_dense_labels.py --instruments XAUUSD,EURUSD --tfs all --windows learn=2010-2017 \
        --windows test_old=2003-2009 --workers 4 --summary ~/tmp/dense-labels/_reports/run.json

Each window is built from minutes that END at the window's end (a label whose horizon passes it is `truncated`, so a
learn label never reads a confirm-window price). Reads raw/ (one source per instrument, the holdout cut at load);
writes ~/tmp/dense-labels and the fs-v1 cache (local only, never committed or published). Idempotent: a partition whose
key exists is skipped. Workers: at most 4 (spawned; each loads its instrument's minutes).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.config import TF_SECONDS
from goldbot.data import holdout_guard
from goldbot.data import htf_bars as H
from goldbot.research import dense_labels as D
from goldbot.research import feature_store as F
from goldbot.research.self_retrain import CELL_TFS

ROOT = Path(__file__).resolve().parents[2]
TIERS = "tiers/tiers_served_r11-v2.1.csv"     # D-1 served tiers (PR #467): dukascopy:XAUUSD, histdata:<others>
EVENT_COLS = ["instrument", "tf", "phase", "group_id", "decision_utc", "window_from_utc", "entry_utc",
              "horizon_end_utc", "label_ok", "source", "template"]


def _years(s: str) -> list[int]:
    a, _, b = s.partition("-")
    return list(range(int(a), int(b or a) + 1))


def _first_year(inst: str, raw: Path) -> int:
    if inst == F.GOLD:
        have = [int(p.stem.rsplit("_", 1)[1]) for p in raw.glob("xauusd_1m_dukascopy_*.parquet")]
    else:
        have = [int(p.stem.rsplit("_", 1)[1]) for p in (raw / F.HISTDATA_DIR / inst).glob("*_1m_bid_*.parquet")]
    return min(have)


def _fs_key(fs_cache: Path, m: pd.DataFrame, inst: str, tf: str, year: int, phase: int) -> str | None:
    """The fs-v1 partition key the current minutes give (`feature_store.cache_key`), if that partition is on disk."""
    key = F.cache_key(m, instrument=inst, tf=tf, year=year, phase=phase)
    d = Path(fs_cache) / F.SPEC.spec_id / inst / tf / f"p{phase}" / str(year)
    return key if (d / f"{key[:24]}.json").exists() else None


def job(inst: str, tf: str, years: list[int], until: int, phases: list[int], raw: str, fs_cache: str,
        cache: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    rawp, fsc, out = Path(raw), Path(fs_cache), Path(cache)
    m = F.history_minutes(inst, _first_year(inst, rawp), until, rawp)   # minutes end at the window's end
    load_s = time.perf_counter() - t0
    mm = D.Minutes(m)
    msha = H.minutes_sha(m)
    hi = min(pd.Timestamp(f"{until + 1}-01-01", tz="UTC"), F.holdout_guard.HOLDOUT_START)
    htf = F._htf_for(m, F.SPEC, hi)
    recs: list[dict[str, Any]] = []
    for ph in phases:
        if ph and (ph * 60 >= TF_SECONDS[tf] or TF_SECONDS[tf] >= TF_SECONDS["1d"]):
            continue
        bars = D.decision_bars(mm, tf, ph)
        for y in years:
            base = {"instrument": inst, "tf": tf, "phase": ph, "year": y}
            t = time.perf_counter()
            rows = F.build_year(m, instrument=inst, tf=tf, year=y, cache_dir=fsc, htf=htf, phase=ph)
            fs_s = time.perf_counter() - t
            fs_key = _fs_key(fsc, m, inst, tf, y, ph)
            if fs_key is None:              # skip this partition, report it; never abort the run
                recs.append({**base, "skipped": "no fs-v1 manifest under the current data key"})
                continue
            parts = {"fs_key": fs_key, "minutes_sha256": msha, "minutes_to_utc": str(m["ts_utc"].max())}
            key = D.label_key(parts, instrument=inst, tf=tf, year=y, phase=ph)
            ev_path = out / "_events" / inst / tf / f"p{ph}" / f"{y}_{key[:24]}.parquet"
            if D.partition_exists(out, inst, tf, y, ph, parts) and ev_path.exists():
                recs.append({**base, "bars": len(rows), "cached": True, "key": key, "events": str(ev_path)})
                continue
            t = time.perf_counter()
            lab = D.label_rows(mm, rows, tf, bars=bars)
            lab_s = time.perf_counter() - t
            D.write_partition(lab, cache=out, instrument=inst, tf=tf, year=y, phase=ph, parts=parts)
            # one event per bar (the horizon is outcome-blind and shared by the 16 heads)
            ev = lab.loc[(lab["template"] == D.SPEC.judged_head) & (lab["side"] == 1), EVENT_COLS[:-1]]
            ev_path.parent.mkdir(parents=True, exist_ok=True)
            ev.assign(template=D.SPEC.judged_head).to_parquet(ev_path, index=False)
            recs.append({**base, "bars": len(rows), "heads": len(lab), "label_ok_bars": int(ev["label_ok"].sum()),
                         "fs_s": round(fs_s, 1), "label_s": round(lab_s, 1), "key": key, "events": str(ev_path)})
    return {"instrument": inst, "tf": tf, "years": years, "load_s": round(load_s, 1),
            "wall_s": round(time.perf_counter() - t0, 1), "parts": recs}


def counts(inst: str, tfs: list[str], windows: dict[str, list[int]], event_paths: dict[str, list[Path]], raw: Path,
           cache: Path, tiers: F.Tiers | None) -> list[dict[str, Any]]:
    """Weights over every built tf of the instrument (on its market-minute grid), then n_eff per tf, window and
    column set (core-6, core-12, P30; ADR 0104 4): untiered, and tier-surviving for the judged head T2 (quant C2)."""
    years = sorted({y for ys in windows.values() for y in ys})
    m = F.history_minutes(inst, _first_year(inst, raw), years[-1], raw)
    grid = D._ns(H.effective_minutes(m)["ts_utc"])
    del m
    events, built = {}, {}
    for tf in tfs:
        fr = [pd.read_parquet(p) for p in sorted(event_paths.get(tf, []))]
        if fr:
            ev = D._typed(pd.concat(fr, ignore_index=True)).sort_values(["phase", "decision_utc"])
            if ev.duplicated(["phase", "decision_utc"]).any():
                raise ValueError(f"{inst} {tf}: an event appears in two partitions")
            events[tf] = ev.reset_index(drop=True)
            built[tf] = int(events[tf]["phase"].nunique())
            holdout_guard.refuse_labels(events[tf].assign(max_hold_s=(events[tf]["horizon_end_utc"]
                                                                      - events[tf]["decision_utc"]).dt.total_seconds()),
                                        what=f"dl-v1 events {inst} {tf}")
    w = D.weights(events, grid, built)
    out = []
    for tf, wt in w.items():
        ev = events[tf]
        wdir = cache / "_weights" / inst
        wdir.mkdir(parents=True, exist_ok=True)
        wt.to_parquet(wdir / f"{tf}.parquet", index=False)
        for name, ys in windows.items():
            yr = ev["decision_utc"].dt.year.isin(ys).to_numpy()
            a = D.n_eff(wt[yr], tf)
            rec = {"instrument": inst, "tf": tf, "window": name, "bars": int(yr.sum()),
                   "label_ok_bars": int((yr & ev["label_ok"].to_numpy(bool)).sum()), "heads": int(yr.sum()) * 16,
                   "days": a["days"], "n_eff_untiered": round(a["n_eff"]),
                   "tiers": None if tiers is None else tiers.key(inst)}
            for cs, cols in D.COLUMN_SETS.items():
                if tiers is None:
                    rec[f"n_eff_T2_{cs}"] = None
                    continue
                tiered = D.attach_tiers(ev, tiers, columns=cols)
                b = D.n_eff(wt[yr & tiered["usable"].to_numpy(bool)], tf)
                rec[f"n_eff_T2_{cs}"] = round(b["n_eff"])
                rec[f"bars_T2_{cs}"] = b["rows"]
            out.append(rec)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--instruments", default=F.GOLD)
    ap.add_argument("--tfs", default="all")
    ap.add_argument("--windows", action="append", default=None, help="name=YYYY-YYYY (repeatable)")
    ap.add_argument("--phases", default="0")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--raw", type=Path, default=ROOT / "raw")
    ap.add_argument("--fs-cache", type=Path, default=F.CACHE)
    ap.add_argument("--cache", type=Path, default=D.CACHE)
    ap.add_argument("--summary", type=Path, default=None)
    ap.add_argument("--chunk-1m", type=int, default=2, help="years per 1m job")
    a = ap.parse_args()
    if a.workers > 4:
        raise SystemExit("at most 4 workers")
    windows = {k: _years(v) for k, v in (w.split("=") for w in (a.windows or ["learn=2010-2017"]))}
    tfs = list(CELL_TFS) if a.tfs == "all" else a.tfs.split(",")
    skipped = [tf for tf in tfs if tf not in TF_SECONDS]
    tfs = [tf for tf in tfs if tf in TF_SECONDS]
    if skipped:
        print(f"skipped, no bar rule in config.TF_SECONDS: {skipped} (TF-2h)", flush=True)
    phases = [int(p) for p in a.phases.split(",")]
    insts = a.instruments.split(",")
    jobs = []
    for inst in insts:
        for tf in tfs:
            for ys in windows.values():
                step = a.chunk_1m if tf == "1m" else len(ys)
                for i in range(0, len(ys), step):
                    jobs.append((inst, tf, ys[i:i + step], ys[-1]))
    jobs.sort(key=lambda j: (TF_SECONDS[j[1]], -len(j[2])))
    t0 = time.perf_counter()
    res = []
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(job, i, tf, ys, u, phases, str(a.raw), str(a.fs_cache), str(a.cache)): (i, tf, ys)
                for i, tf, ys, u in jobs}
        for f in as_completed(futs):
            r = f.result()
            res.append(r)
            print(json.dumps({k: r[k] for k in ("instrument", "tf", "years", "load_s", "wall_s")}), flush=True)
    build_s = time.perf_counter() - t0
    tiers_path = a.raw / TIERS

    def tiers_for(inst: str) -> F.Tiers | None:
        if not tiers_path.exists():
            return None
        return F.Tiers.from_csv(tiers_path, source="dukascopy" if inst == F.GOLD else "histdata")
    t1 = time.perf_counter()
    paths: dict[str, dict[str, list[Path]]] = {}
    skipped_parts = []
    for r in res:
        for p in r["parts"]:
            if "events" in p:
                paths.setdefault(p["instrument"], {}).setdefault(p["tf"], []).append(Path(p["events"]))
            elif "skipped" in p:
                skipped_parts.append(p)
    cnt = [c for inst in insts for c in counts(inst, tfs, windows, paths.get(inst, {}), a.raw, a.cache,
                                               tiers_for(inst))]
    tiers = tiers_for(insts[0])
    count_s = time.perf_counter() - t1
    summary = {"spec_id": D.SPEC.spec_id, "spec_hash": D.spec_hash(), "code_hash": D.code_hash(),
               "tiers_sha256": None if tiers is None else tiers.sha256,
               "tiers_recorded": D.readable(tiers), "workers": a.workers,
               "skipped_parts": skipped_parts, "build_wall_s": round(build_s, 1), "count_wall_s": round(count_s, 1), "jobs": res, "counts": cnt,
               "built_utc": str(pd.Timestamp.now(tz="UTC"))}
    if a.summary:
        a.summary.parent.mkdir(parents=True, exist_ok=True)
        a.summary.write_text(json.dumps(summary, indent=1, default=str))
    df = pd.DataFrame(cnt)
    if len(df):
        cols = ["instrument", "window", "tf", "bars", "heads", "n_eff_untiered",
                *[f"n_eff_T2_{cs}" for cs in D.COLUMN_SETS]]
        print(df[cols].to_string(index=False))
    if skipped_parts:
        print(f"SKIPPED partitions: {skipped_parts}", flush=True)
    print(f"build {build_s:.0f}s, counts {count_s:.0f}s, {a.workers} workers", flush=True)
    return 0


if __name__ == "__main__":
    np.seterr(invalid="ignore")
    raise SystemExit(main())
