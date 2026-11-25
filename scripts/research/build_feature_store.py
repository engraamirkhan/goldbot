"""Build fs-v1 feature partitions (goldbot/research/feature_store.py) for instruments x timeframes x years, longest
job first, and print rows and seconds per partition (lifecycle N9 throughput).

    python scripts/research/build_feature_store.py --instruments XAUUSD --tfs 15m --years 2010-2017
    python scripts/research/build_feature_store.py --instruments all --tfs all --years 2003-2025 --summary out.json

Reads raw/ (Dukascopy gold, raw/histdata_m1 for the rest; one source per instrument), writes the cache under
~/tmp/feature-store (local only; market data is never committed or published). The holdout is cut at load and refused
by the store. Idempotent: a partition whose key exists is read, not recomputed.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from goldbot.config import TF_SECONDS
from goldbot.research import feature_store as F
from goldbot.research.self_retrain import CELL_TFS

ROOT = Path(__file__).resolve().parents[2]


def _years(s: str) -> list[int]:
    a, _, b = s.partition("-")
    return list(range(int(a), int(b or a) + 1))


def _available_years(instrument: str, raw: Path, want: list[int]) -> list[int]:
    if instrument == F.GOLD:
        have = {int(p.stem.rsplit("_", 1)[1]) for p in raw.glob("xauusd_1m_dukascopy_*.parquet")}
    else:
        have = {int(p.stem.rsplit("_", 1)[1]) for p in (raw / F.HISTDATA_DIR / instrument).glob("*_1m_bid_*.parquet")}
    return [y for y in want if y in have]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--instruments", default=F.GOLD, help="comma list or 'all' (gold + every HistData key)")
    ap.add_argument("--tfs", default="15m", help="comma list or 'all' (the nine cell timeframes)")
    ap.add_argument("--years", default="2010-2017")
    ap.add_argument("--phases", default="0", help="comma list of offset minutes (training only), e.g. 0,5,10")
    ap.add_argument("--raw", type=Path, default=ROOT / "raw")
    ap.add_argument("--cache", type=Path, default=F.CACHE)
    ap.add_argument("--summary", type=Path, default=None)
    a = ap.parse_args()
    insts = [F.GOLD, *F.histdata_instruments(a.raw)] if a.instruments == "all" else a.instruments.split(",")
    tfs = list(CELL_TFS) if a.tfs == "all" else a.tfs.split(",")
    known = [tf for tf in tfs if tf in TF_SECONDS]
    if len(known) < len(tfs):        # 2h is a cell timeframe with no bar rule (config.TF_SECONDS, resample_bars)
        print(f"skipped, no bar rule in config.TF_SECONDS: {sorted(set(tfs) - set(known))}", flush=True)
    tfs = known
    phases = [int(p) for p in a.phases.split(",")]
    want = _years(a.years)
    out = []
    for inst in insts:
        years = _available_years(inst, a.raw, want)
        if not years:
            print(f"{inst}: no files for {a.years}", flush=True)
            continue
        t0 = time.perf_counter()
        first = _available_years(inst, a.raw, list(range(1990, years[0] + 1)))[0]     # daily context: whole history
        m = F.history_minutes(inst, first, years[-1], a.raw)
        load_s = time.perf_counter() - t0
        print(f"{inst}: {len(m):,} minutes {first}-{years[-1]} loaded in {load_s:.1f}s", flush=True)
        jobs = F.job_order([(inst, tf, y) for tf in tfs for y in years])
        hi = pd.Timestamp(f"{years[-1] + 1}-01-01", tz="UTC")
        t1 = time.perf_counter()
        htf = F._htf_for(m, F.SPEC, min(hi, F.holdout_guard.HOLDOUT_START))
        htf_s = time.perf_counter() - t1
        for _, tf, y in jobs:
            for ph in phases:
                if ph and F.tf_seconds(tf) >= F.tf_seconds("1d"):
                    continue
                t = time.perf_counter()
                rows = F.build_year(m, instrument=inst, tf=tf, year=y, cache_dir=a.cache, htf=htf, phase=ph)
                s = time.perf_counter() - t
                finite = int(rows[list(F.SPEC.columns)].notna().all(axis=1).sum()) if len(rows) else 0
                rec = {"instrument": inst, "tf": tf, "phase": ph, "year": y, "rows": len(rows),
                       "rows_all_finite": finite, "seconds": round(s, 2)}
                out.append(rec)
                print(json.dumps(rec), flush=True)
        print(f"{inst}: daily/weekly context bars {htf_s:.1f}s", flush=True)
    if a.summary:
        a.summary.write_text(json.dumps(out, indent=1))
    tot = pd.DataFrame(out)
    if len(tot):
        print(tot.groupby(["instrument", "tf"])[["rows", "seconds"]].sum().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
