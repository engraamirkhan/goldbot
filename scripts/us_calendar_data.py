"""Build the point-in-time US calendar (tiers 1 and 2) the data-us-calendar workflow publishes on release
`calendar-v1` (goldbot/data/us_calendar.py has the sources, licence and availability rules).

FRED release dates (CPI, NFP, PCE, PPI, retail sales, GDP, JOLTS, jobless claims) need a free FRED API key in the
environment variable FRED_API_KEY (the repo secret of the same name); it is never printed. FOMC statements, press
conferences and minutes come from the Federal Reserve Board's meeting calendars; ISM dates are rule-derived; the
hand-checked cancellations (MANUAL_EVENTS) are added every run. With --existing (the last published file) history is
kept as published (merge_calendar). last_ok is kept per event type and moves only for types downloaded successfully,
so a type that failed before is backfilled with rule stamps when it comes back.

Guards, each reported in the output and the step summary:
* monthly releases less than 20 days apart are quarantined (not published) unless hand-checked (SPACING_EXEMPT);
* a type whose download would cancel more than MAX_CANCELLATIONS future events is refused for this run (its
  published rows are kept, its last_ok does not move; exit 1);
* a per-type, per-year count table against the expected counts (complete years only; a mismatch is a warning for the
  quant review, not a failure).

Never loses history: --existing must name a file that exists unless --first-run says there is no published file yet,
and a merged frame with fewer rows per source than the previous file, or without every previously published row, is
refused (check_keeps_history; exit 2, no output, so the publish step uploads nothing).

Exit status: 2 when history would be lost (or --existing is missing without --first-run); 1 when FRED_API_KEY is
missing (nothing written) or any source failed or was refused (the others are merged); 0 otherwise.

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
    ISM,
    RELEASES,
    FedCalendarError,
    FredError,
    HistoryLoss,
    calendar_frame,
    check_keeps_history,
    count_table,
    fetch_fomc,
    fetch_release,
    mass_cancellations,
    merge_calendar,
    quarantine_spacing,
    read_release,
    types_of,
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
    prev, prev_ok = (read_release(args.existing) if args.existing and Path(args.existing).exists() else (None, {}))
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
        notes.append(f"FOMC (federalreserve.gov): {len(fomc):,} scheduled meetings "
                     f"{fomc['day'].min():%Y-%m-%d} .. {fomc['day'].max():%Y-%m-%d}")
    except FedCalendarError as exc:
        notes.append(f"FOMC FAILED (published rows kept): {exc}")
    failed = len(dates) < len(RELEASES) or fomc is None
    fresh, quarantined = quarantine_spacing(calendar_frame(dates, now, fomc))
    for _, q in quarantined.iterrows():
        notes.append(f"QUARANTINED (monthly release < 20 days after the previous one; add to SPACING_EXEMPT once "
                     f"hand-checked): {q['event_id']}")
    fetched = [*types_of([*dates, *(["fomc"] if fomc is not None else [])]), *ISM]
    mass = mass_cancellations(prev, fresh, now, fetched)
    if mass:
        failed = True
        notes.append(f"REFUSED for this run (would cancel more future events than allowed; published rows kept): {mass}")
        fetched = [t for t in fetched if t not in mass]
        fresh = fresh[~fresh["event_type"].isin(mass) | (fresh["source"] == "manual")]
    for n in notes:
        print(n, flush=True)
    merged = merge_calendar(prev, fresh, now, prev_ok, fetched_types=fetched)
    try:
        check_keeps_history(prev, merged)
    except HistoryLoss as exc:
        print(f"REFUSED (nothing written, nothing published): {exc}")
        return 2
    last_ok = {**prev_ok, **{t: now for t in fetched}}
    write_release(merged, args.out, last_ok)
    n_prev = 0 if prev is None else len(prev)
    lines = ["", "| event_type | tier | rows | cancelled | first event | last event |", "|---|---:|---:|---:|---|---|"]
    for t, g in merged.groupby("event_type"):
        ev = pd.DatetimeIndex(g["event_utc"])
        lines.append(f"| {t} | {int(g['tier'].iloc[0])} | {len(g):,} | {int((g['status'] == 'cancelled').sum())} | "
                     f"{ev.min():%Y-%m-%d %H:%M} | {ev.max():%Y-%m-%d %H:%M} |")
    lines.append(f"\n{len(merged) - n_prev:,} rows added to {n_prev:,} published; wrote {args.out}")
    counts = count_table(merged, through_year=now.year - 1)
    bad = counts[counts["ok"] == False]  # noqa: E712  (None = partial year)
    table = ["", "### Events per type and year (quant review before any trial cites tier1_events/tier2_events)", "",
             "| event_type | year | n | expected | ok |", "|---|---:|---:|---|---|"]
    table += [f"| {r.event_type} | {r.year} | {r.n} | {r.expected} | "
              f"{'partial' if r.ok is None else ('yes' if r.ok else '**NO**')} |"
              for r in counts.sort_values(["event_type", "year"]).itertuples()]
    lines.append(f"count check: {len(bad)} type-years outside the expected range"
                 + (": " + ", ".join(f"{r.event_type} {r.year} ({r.n}, expected {r.expected})"
                                     for r in bad.itertuples()) if len(bad) else ""))
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(["## calendar-v1 (US tiers 1 and 2)", "", *[f"- {n}" for n in notes], *lines, *table])
                     + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
