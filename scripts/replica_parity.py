"""Replica parity over a window: goldbot's own `smc_replica` indicators vs chart values pulled locally.

Local only. It reads a pull directory (bars_<tag>.parquet and plots_<tag>.parquet per timeframe, default ~/tmp/tv,
never in the repo) and prints per-plot aggregates and an OK / WARN / SKIP status per timeframe
(goldbot.research.replica_parity). It is an optional health check: the exit code is 0 unless --strict is given, and
a missing pull is SKIP, not an error.

    python scripts/replica_parity.py [--dir ~/tmp/tv] [--from 2026-01-01] [--to 2026-10-01] [--tf 60] [--json out.json] [--strict]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from goldbot.research.replica_parity import ParityReport, parity

PULLS = {"15": "auth01_OANDA_XAUUSD_15", "60": "auth02_OANDA_XAUUSD_60", "240": "auth03_OANDA_XAUUSD_240",
         "1D": "auth04_OANDA_XAUUSD_1D"}
DAILY_FROM = "1921-01-01"          # earlier daily rows of the pull are yearly/monthly bars


def run(root: Path, tf: str, start: str | None, end: str | None) -> ParityReport | None:
    bars_f, plots_f = root / f"bars_{PULLS[tf]}.parquet", root / f"plots_{PULLS[tf]}.parquet"
    if not bars_f.exists() or not plots_f.exists():
        return None
    bars = pd.read_parquet(bars_f)
    plots = pd.read_parquet(plots_f)
    if tf == "1D" and start is None:
        start = DAILY_FROM
    return parity(bars, plots, start, end, daily=(tf == "1D"))


def main() -> int:
    ap = argparse.ArgumentParser(description="replica parity vs a local chart pull")
    ap.add_argument("--dir", default=str(Path.home() / "tmp" / "tv"))
    ap.add_argument("--from", dest="start", default=None)
    ap.add_argument("--to", dest="end", default=None)
    ap.add_argument("--tf", choices=sorted(PULLS), action="append")
    ap.add_argument("--json", default="")
    ap.add_argument("--strict", action="store_true", help="exit 1 on WARN")
    args = ap.parse_args()
    root = Path(args.dir).expanduser()
    out: dict[str, dict] = {}
    worst = "OK"
    for tf in args.tf or list(PULLS):
        rep = run(root, tf, args.start, args.end)
        if rep is None:
            print(f"{tf}: SKIP (no pull in {root})")
            continue
        out[tf] = rep.model_dump()
        bad = rep.inexact()
        n = len(rep.rows)
        print(f"{tf}: {rep.status} {n - len(rep.failing())}/{n} plots pass ({n - len(bad)} exact), "
              f"{rep.bars_compared} bars {rep.start} .. {rep.end}")
        for r in bad:
            print(f"   {r.plot}: precision {r.precision:.4f} recall {r.recall:.4f} max_rel {r.max_rel:.2e} "
                  f"share {r.share_equal:.4f} (chart {r.n}, replica {r.n_replica})")
        if rep.status == "WARN":
            worst = "WARN"
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1, default=str))
    return 1 if (args.strict and worst == "WARN") else 0


if __name__ == "__main__":
    sys.exit(main())
