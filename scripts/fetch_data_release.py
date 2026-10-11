"""Fetch the 1m bar Parquet files published by the data-dukascopy workflow (release tag data-v1) into the
store, and with --macro the FRED macro series the data-macro workflow publishes (release macro-v1) into the store's
`macro` table, and with --positioning the COT/GLD rows the data-positioning workflow publishes (release
positioning-v1) into the `positioning` table, and with --us-calendar the US calendar, tiers 1 and 2, (release calendar-v1) into
raw-calendar/ (research reads it as a file; no store table yet). Works anywhere github.com is reachable.
With --download-only the release files are only downloaded (bars into raw/, macro into raw-macro/, the calendar into
raw-calendar/) and the store is not touched: what a workflow that reads the files directly needs
(scripts/count_owed_events.py, .github/workflows/count-owed-events.yml).
Usage: python scripts/fetch_data_release.py [--years 2015 2026] [--macro | --macro-only] [--positioning] [--us-calendar]
       [--download-only]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.release import (  # noqa: E402
    CALENDAR_TAG,
    MACRO_TAG,
    download_release,
    sync_release_bars,
    sync_release_macro,
    sync_release_positioning,
    sync_release_us_calendar,
)
from goldbot.data.store import Store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs=2, type=int, default=None)
    ap.add_argument("--token", default=None, help="GitHub token for a private repo (or GH_TOKEN env)")
    ap.add_argument("--macro", action="store_true", help="also load the macro series (release macro-v1)")
    ap.add_argument("--macro-only", action="store_true", help="load only the macro series")
    ap.add_argument("--positioning", action="store_true", help="also load COT and GLD holdings (release positioning-v1)")
    ap.add_argument("--us-calendar", action="store_true",
                    help="also download the US calendar, tiers 1 and 2 (release calendar-v1), into raw-calendar/")
    ap.add_argument("--download-only", action="store_true",
                    help="download the release files (raw/, raw-macro/, raw-calendar/) without loading the store")
    args = ap.parse_args()
    if args.download_only:
        download_only(args)
        return
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
    if args.us_calendar:
        cal = sync_release_us_calendar(token=args.token)
        print("us_calendar", len(cal) if len(cal) else "0 (no calendar-v1 release yet; run the data-us-calendar workflow)")


def download_only(args: argparse.Namespace) -> None:
    """Download the requested releases' files and load nothing (idempotent: unchanged files are skipped)."""
    years = (int(args.years[0]), int(args.years[1])) if args.years else None
    if not args.macro_only:
        bars = download_release("data-v1", Path("raw"), token=args.token, years=years)
        print("bars", len(bars), "files" if bars else "files (no data-v1 assets yet; run the data-dukascopy workflow)")
    if args.macro or args.macro_only:
        macro = download_release(MACRO_TAG, Path("raw-macro"), token=args.token)
        print("macro", len(macro), "files" if macro else "files (no macro-v1 release yet; run the data-macro workflow)")
    if args.us_calendar:
        cal = download_release(CALENDAR_TAG, Path("raw-calendar"), token=args.token)
        print("us_calendar", len(cal), "files" if cal else "files (no calendar-v1 release yet; run data-us-calendar)")


if __name__ == "__main__":
    main()
