"""Download gold's macro drivers from FRED's public CSV endpoint (no API key) and write the point-in-time macro
Parquet the data-macro workflow publishes on release `macro-v1`.

Series: DFII10 (10y real yield), T10YIE (10y breakeven), DTWEXBGS (broad dollar), GVZCLS (gold VIX), DGS2 (2y), and the
equity-stress pair VIXCLS (CBOE VIX, 16:15 ET close) and SP500 (S&P 500 close; FRED serves only about the last ten
years). Both are daily market closes posted the next business day, so they take the same availability rule as GVZCLS
(fred_available_utc: 23:00 UTC on the next US business day). The macro feature family reads them as opt-in columns.
Each row: series, series_id, value_date, value, vintage (retrieval date), available_utc (goldbot.data.macro:
fred_available_utc, a conservative publication stamp) and ts_utc (= available_utc).

With --existing (the last published file) history is kept as published: only new dates and revised values are
added (goldbot.data.macro.merge_vintages), so a revision never back-fills what an earlier bar could see. A series
that fails to download keeps its published rows and the script exits 1 after writing the file.

  python scripts/fred_macro.py --out macro_fred.parquet [--existing prev/macro_fred.parquet] [--start 2003-01-01]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.macro import MACRO_RELEASE_SERIES, fetch_fred_csv, fred_frame, merge_vintages  # noqa: E402

# equity stress (indicator survey rank 6); same daily-close availability rule as GVZCLS, stored under their series ids
EQUITY_STRESS_SERIES = ("VIXCLS", "SP500")
RELEASE_SERIES = (*MACRO_RELEASE_SERIES, *EQUITY_STRESS_SERIES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="macro_fred.parquet")
    ap.add_argument("--existing", default="", help="previously published macro Parquet (kept as is)")
    ap.add_argument("--start", default="2003-01-01")
    ap.add_argument("--series", nargs="*", default=list(RELEASE_SERIES))
    args = ap.parse_args()
    prev = pd.read_parquet(args.existing) if args.existing and Path(args.existing).exists() else None
    today = pd.Timestamp.now(tz="UTC").normalize()
    fresh, failed = [], []
    for sid in args.series:
        try:
            obs = fetch_fred_csv(sid, args.start)
        except RuntimeError as exc:
            print(f"FAILED {exc}", flush=True)
            failed.append(sid)
            continue
        fresh.append(fred_frame(sid, obs, today))
        print(f"{sid}: {len(obs):,} observations {obs['value_date'].min():%Y-%m-%d} .. {obs['value_date'].max():%Y-%m-%d}",
              flush=True)
    if not fresh and prev is None:
        print("nothing downloaded and no previous file: no output")
        return 1
    merged = merge_vintages(prev, pd.concat(fresh, ignore_index=True)) if fresh else prev
    assert merged is not None
    merged.to_parquet(args.out, index=False)
    n_prev = 0 if prev is None else len(prev)
    print("\n| series | rows | first value_date | last value_date | last available_utc |\n|---|---:|---|---|---|")
    for s, g in merged.groupby("series"):
        print(f"| {s} | {len(g):,} | {g['value_date'].min():%Y-%m-%d} | {g['value_date'].max():%Y-%m-%d} | "
              f"{pd.Timestamp(g['available_utc'].max()):%Y-%m-%d %H:%M} |")
    print(f"\n{len(merged) - n_prev:,} rows added to {n_prev:,} published; wrote {args.out}")
    if failed:
        print(f"download failed for {failed}; their published rows were kept")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
