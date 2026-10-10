"""Fetch the 1m bar Parquet files published by the data-dukascopy workflow (release tag data-v1) into the
store, and with --macro the FRED macro series the data-macro workflow publishes (release macro-v1) into the store's
`macro` table, and with --positioning the COT/GLD rows the data-positioning workflow publishes (release
positioning-v1) into the `positioning` table. Works anywhere github.com is reachable.
Usage: python scripts/fetch_data_release.py [--years 2015 2026] [--macro | --macro-only] [--positioning]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.release import sync_release_bars, sync_release_macro, sync_release_positioning  # noqa: E402
from goldbot.data.store import Store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs=2, type=int, default=None)
    ap.add_argument("--token", default=None, help="GitHub token for a private repo (or GH_TOKEN env)")
    ap.add_argument("--macro", action="store_true", help="also load the macro series (release macro-v1)")
    ap.add_argument("--macro-only", action="store_true", help="load only the macro series")
    ap.add_argument("--positioning", action="store_true", help="also load COT and GLD holdings (release positioning-v1)")
    args = ap.parse_args()
    store = Store(load_settings().data_root)
    if not args.macro_only:
        counts = sync_release_bars(store, years=tuple(args.years) if args.years else None, token=args.token)
        if not counts:
            print("no assets yet; run the data-dukascopy workflow first")
        for tf, n in counts.items():
            print(tf, n)
    if args.macro or args.macro_only:
        n = sync_release_macro(store, token=args.token)
        print("macro", n if n else "0 (no macro-v1 release yet, or nothing new; run the data-macro workflow)")
    if args.positioning:
        n = sync_release_positioning(store, token=args.token)
        print("positioning", n if n else "0 (no positioning-v1 release yet; run the data-positioning workflow)")


if __name__ == "__main__":
    main()
