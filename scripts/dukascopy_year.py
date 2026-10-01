"""Download one year of XAUUSD ticks from Dukascopy month by month (via dukascopy-node), resample each
month to 1m bars with goldbot's resampler, and write one Parquet for the year. Used by the data-dukascopy
workflow on GitHub runners; also runnable on any machine that can reach datafeed.dukascopy.com.

  python scripts/dukascopy_year.py 2025 [--out xauusd_1m_dukascopy_2025.parquet]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.loaders import load_dukascopy_ticks_csv  # noqa: E402
from goldbot.data.quality import check_bars  # noqa: E402
from goldbot.data.resample import ticks_to_1m  # noqa: E402


def month_range(year: int):
    today = dt.date.today()
    for m in range(1, 13):
        start = dt.date(year, m, 1)
        if start > today:
            break
        end = (dt.date(year + 1, 1, 1) if m == 12 else dt.date(year, m + 1, 1)) - dt.timedelta(days=1)
        yield start, min(end, today)


def download(start: dt.date, end: dt.date, outdir: Path, attempts: int = 3) -> Path | None:
    outdir.mkdir(parents=True, exist_ok=True)
    for a in range(attempts):
        cmd = ["npx", "-y", "dukascopy-node", "-i", "xauusd", "-from", start.isoformat(), "-to", end.isoformat(),
               "-t", "tick", "-f", "csv", "-dir", str(outdir), "-bs", "5", "-bp", "2000", "-r", "5", "-ch", "0"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        files = sorted(outdir.glob(f"xauusd-tick-{start.isoformat()}-*.csv"), key=os.path.getmtime)
        if files and files[-1].stat().st_size > 1000:
            return files[-1]
        print(f"attempt {a + 1} for {start:%Y-%m} produced no data; rc={r.returncode}\n{r.stdout[-800:]}\n{r.stderr[-800:]}", flush=True)
        time.sleep(10 * (a + 1))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("year", type=int)
    ap.add_argument("--out", default=None)
    ap.add_argument("--tmp", default="raw/dukascopy")
    args = ap.parse_args()
    bars = []
    missing = []
    for start, end in month_range(args.year):
        t0 = time.time()
        f = download(start, end, Path(args.tmp))
        if f is None:
            missing.append(f"{start:%Y-%m}")
            continue
        ticks = load_dukascopy_ticks_csv(f)
        b = ticks_to_1m(ticks)
        print(f"{start:%Y-%m}: {len(ticks):,} ticks -> {len(b):,} 1m bars [{time.time() - t0:.0f}s]", flush=True)
        bars.append(b)
        f.unlink(missing_ok=True)
    if not bars:
        print("no data downloaded for", args.year, "missing:", missing)
        return 1
    out = pd.concat(bars).drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
    out, dq = check_bars(out)
    out["source"] = "dukascopy"
    path = args.out or f"xauusd_1m_dukascopy_{args.year}.parquet"
    out.to_parquet(path, compression="zstd", index=False)
    print(f"wrote {path}: {len(out):,} bars, {len(dq)} dq events, missing months: {missing or 'none'}")
    shutil.rmtree(args.tmp, ignore_errors=True)
    return 0 if not missing else 2


if __name__ == "__main__":
    sys.exit(main())
