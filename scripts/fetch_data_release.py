"""Fetch the 1m bar Parquet files published by the data-dukascopy workflow (release tag data-v1) into the
store. Works anywhere github.com is reachable. Usage: python scripts/fetch_data_release.py [--years 2015 2026]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.release import sync_release_bars  # noqa: E402
from goldbot.data.store import Store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs=2, type=int, default=None)
    ap.add_argument("--token", default=None, help="GitHub token for a private repo (or GH_TOKEN env)")
    args = ap.parse_args()
    counts = sync_release_bars(Store(load_settings().data_root), years=tuple(args.years) if args.years else None, token=args.token)
    if not counts:
        print("no assets yet; run the data-dukascopy workflow first")
        return
    for tf, n in counts.items():
        print(tf, n)


if __name__ == "__main__":
    main()
