"""Build the point-in-time US calendar (tiers 1 and 2) the data-us-calendar workflow publishes on release
`calendar-v1` (goldbot/data/us_calendar.py has the sources, licence and availability rules).

FRED release dates (CPI, NFP, PCE, PPI, retail sales, GDP, JOLTS, jobless claims) need a free FRED API key in the
environment variable FRED_API_KEY (the repo secret of the same name); it is never printed. FOMC statements, press
conferences and minutes come from the Federal Reserve Board's meeting calendars; ISM dates are rule-derived. With --existing (the last published file) history is kept as published (merge_calendar): new events are
added, late ones stamped at this run, disappeared future events get a `cancelled` row.

Never loses history: --existing must name a file that exists unless --first-run says there is no published file yet,
and a merged frame with fewer rows per source than the previous file, or without every previously published row, is
refused (check_keeps_history; exit 2, no output, so the publish step uploads nothing).

Exit status: 2 when history would be lost (or --existing is missing without --first-run); 1 when FRED_API_KEY is
missing (nothing written) or any source failed (the others are merged, a failed source's published rows are kept and
never cancelled); 0 otherwise.

  FRED_API_KEY=... python scripts/us_calendar_data.py --out us_calendar.parquet \\
                   --existing prev/us_calendar.parquet [--first-run]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.us_calendar import (  # noqa: E402
    ASSET,
    FOMC_TYPES,
    ISM,
    RELEASES,
    FedCalendarError,
    FredError,
    HistoryLoss,
    calendar_frame,
    check_keeps_history,
    fetch_fomc,
    fetch_release,
    merge_calendar,
    read_release,
    write_release,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=ASSET)
    ap.add_argument("--existing", default="", help="previously published calendar Parquet (kept as is)")
    ap.add_argument("--first-run", action="store_true",
                    help="there is no published file yet (confirmed); allows --existing to be missing")
    args = ap.parse_args(argv)
    if args.existing and not Path(args.existing).exists() and not args.first_run:
        print(f"REFUSED: {args.existing} does not exist and --first-run was not given; a missing previous file must "
              "never be mistaken for a first run (it would rebuild from scratch and drop published history)")
        return 2
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        print("FRED_API_KEY is not set (add the repo secret FRED_API_KEY, a free key from fred.stlouisfed.org); "
              "nothing written")
        return 1
    prev, last_ok = (read_release(args.existing) if args.existing and Path(args.existing).exists() else (None, {}))
    now = pd.Timestamp.now(tz="UTC")
    dates: dict[str, pd.DatetimeIndex] = {}
    notes: list[str] = []
    for rel in RELEASES:
        try:
            d = fetch_release(rel, api_key)
            dates[rel.event_type] = d
            notes.append(f"{rel.event_type} (FRED {rel.release_id}): {len(d):,} dates "
                         f"{d.min():%Y-%m-%d} .. {d.max():%Y-%m-%d}" if len(d) else f"{rel.event_type}: no dates")
        except FredError as exc:
            notes.append(f"{rel.event_type} FAILED (published rows kept): {exc}")
    fomc = None
    try:
        fomc = fetch_fomc()
        last_ok["fed"] = now
        notes.append(f"FOMC (federalreserve.gov): {len(fomc):,} scheduled meetings "
                     f"{fomc['day'].min():%Y-%m-%d} .. {fomc['day'].max():%Y-%m-%d}")
    except FedCalendarError as exc:
        notes.append(f"FOMC FAILED (published rows kept): {exc}")
    failed = len(dates) < len(RELEASES) or fomc is None
    if dates:
        last_ok["fred"] = now
    last_ok["rule"] = now
    for n in notes:
        print(n, flush=True)
    fresh = calendar_frame(dates, now, fomc)
    fetched = [*dates, *ISM, *(FOMC_TYPES if fomc is not None else [])]
    merged = merge_calendar(prev, fresh, now, last_ok, fetched_types=fetched)
    try:
        check_keeps_history(prev, merged)
    except HistoryLoss as exc:
        print(f"REFUSED (nothing written, nothing published): {exc}")
        return 2
    write_release(merged, args.out, last_ok)
    n_prev = 0 if prev is None else len(prev)
    lines = ["", "| event_type | tier | rows | cancelled | first event | last event |", "|---|---:|---:|---:|---|---|"]
    for t, g in merged.groupby("event_type"):
        ev = pd.DatetimeIndex(g["event_utc"])
        lines.append(f"| {t} | {int(g['tier'].iloc[0])} | {len(g):,} | {int((g['status'] == 'cancelled').sum())} | "
                     f"{ev.min():%Y-%m-%d %H:%M} | "
                     f"{ev.max():%Y-%m-%d %H:%M} |")
    lines.append(f"\n{len(merged) - n_prev:,} rows added to {n_prev:,} published; wrote {args.out}")
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(["## calendar-v1 (US tiers 1 and 2)", "", *[f"- {n}" for n in notes], *lines]) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
