"""Build the point-in-time US calendar (tiers 1 and 2) the data-us-calendar workflow publishes on release
`calendar-v1` (goldbot/data/us_calendar.py has the sources, licence and availability rules).

FRED release dates (CPI, NFP, PCE, PPI, retail sales, GDP, JOLTS, jobless claims) come from the FRED API when the
environment variable FRED_API_KEY (the repo secret of the same name) is set; it is never printed. Without it (the
default `--source auto`) the same dates come keyless from ALFRED's release-date files and FRED's release calendar
(goldbot/data/us_calendar_keyless.py, ADR 0044); `--source api` or `--source keyless` forces one. FOMC statements, press
conferences and minutes come from the Federal Reserve Board's meeting calendars; ISM dates are rule-derived; the
hand-checked cancellations (MANUAL_EVENTS) are added every run. With --existing (the last published file) history is
kept as published (merge_calendar). last_ok is kept per event type and moves only for types downloaded successfully,
so a type that failed before is backfilled with rule stamps when it comes back.

Guards, each reported in the output and the step summary:
* hand-checked revision-only ALFRED dates (REVISION_ONLY) are dropped; other monthly releases less than 20 days apart
  are quarantined (not published) unless hand-checked (SPACING_EXEMPT);
* a type whose download would cancel more than MAX_CANCELLATIONS future events is refused for this run (its
  published rows are kept, its last_ok does not move; exit 1). After verifying the change is real (RUNBOOK "US
  calendar: a type refused for mass cancellations"), a one-shot `--allow-cancellations TYPE` applies it; every use is
  written to the step summary as an AUDIT line;
* a GDP advance more than 45 days after its quarter end that is not hand-checked is flagged;
* gaps of more than 45 days between monthly releases (a lost release or a shutdown) are listed;
* a per-type, per-year count table against the expected counts (complete years only; a mismatch is a warning for the
  quant review, not a failure).

Never loses history: --existing must name a file that exists unless --first-run says there is no published file yet,
and a merged frame with fewer rows per source than the previous file, or without every previously published row, is
refused (check_keeps_history; exit 2, no output, so the publish step uploads nothing).

Exit status: 2 when history would be lost (or --existing is missing without --first-run); 1 when `--source api` is
asked for without FRED_API_KEY (nothing written) or any source failed or was refused (the others are merged); 0
otherwise.

  [FRED_API_KEY=...] python scripts/us_calendar_data.py --out us_calendar.parquet \\
                   --existing prev/us_calendar.parquet [--first-run] [--source auto|api|keyless]
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
    EVENT_TYPES,
    ISM,
    RELEASES,
    FedCalendarError,
    FredError,
    HistoryLoss,
    calendar_frame,
    check_keeps_history,
    count_table,
    drop_revision_only,
    fetch_fomc,
    fetch_release,
    late_gdp_advances,
    mass_cancellations,
    merge_calendar,
    monthly_gaps,
    quarantine_spacing,
    read_release,
    types_of,
    write_release,
)
from goldbot.data.us_calendar_keyless import fetch_release_keyless  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=ASSET)
    ap.add_argument("--existing", default="", help="previously published calendar Parquet (kept as is)")
    ap.add_argument("--first-run", action="store_true",
                    help="there is no published file yet (confirmed); allows --existing to be missing")
    ap.add_argument("--allow-cancellations", action="append", default=[], choices=list(EVENT_TYPES), metavar="TYPE",
                    help="one-shot, after verification: let this type cancel more than the limit of future events")
    ap.add_argument("--source", choices=["auto", "api", "keyless"], default="auto",
                    help="release dates from the FRED API (needs FRED_API_KEY) or keyless from ALFRED and the FRED "
                         "release calendar; auto: the API when the key is set, else keyless")
    args = ap.parse_args(argv)
    if args.existing and not Path(args.existing).exists() and not args.first_run:
        print(f"REFUSED: {args.existing} does not exist and --first-run was not given; a missing previous file must "
              "never be mistaken for a first run (it would rebuild from scratch and drop published history)")
        return 2
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if args.source == "api" and not api_key:
        print("--source api: FRED_API_KEY is not set (add the repo secret FRED_API_KEY, a free key from "
              "fred.stlouisfed.org, or use --source keyless); nothing written")
        return 1
    keyless = args.source == "keyless" or (args.source == "auto" and not api_key)
    prev, prev_ok = (read_release(args.existing) if args.existing and Path(args.existing).exists() else (None, {}))
    now = pd.Timestamp.now(tz="UTC")
    dates: dict[str, pd.DatetimeIndex] = {}
    notes: list[str] = [("release dates: keyless (ALFRED release-date files + FRED release calendar, ADR 0044)"
                         if keyless else "release dates: FRED API")]
    for rel in RELEASES:
        try:
            if keyless:
                k = fetch_release_keyless(rel, now)
                d, origin = k.dates, f"ALFRED/FRED calendar {rel.release_id}"
                notes += k.notes
            else:
                d, origin = fetch_release(rel, api_key), f"FRED {rel.release_id}"
            dates[rel.event_type] = d
            notes.append(f"{rel.event_type} ({origin}): {len(d):,} dates "
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
    if "gdp" in dates:
        notes += [f"GDP ADVANCE FLAG (check, then add to GDP_ADVANCE_OVERRIDES): {m}" for m in late_gdp_advances(drop_revision_only("gdp", dates["gdp"]))]
    fetched = [*types_of([*dates, *(["fomc"] if fomc is not None else [])]), *ISM]
    allow = list(args.allow_cancellations)
    if allow:
        would = mass_cancellations(prev, fresh, now, fetched, limit=0)
        notes.append(f"AUDIT: --allow-cancellations {', '.join(allow)} by the operator; cancellations applied: "
                     f"{ {t: would.get(t, 0) for t in allow} }")
    mass = mass_cancellations(prev, fresh, now, fetched, allow=allow)
    if mass:
        failed = True
        notes.append(f"REFUSED for this run (would cancel more future events than allowed; published rows kept): {mass}")
        fetched = [t for t in fetched if t not in mass]
        fresh = fresh[~fresh["event_type"].isin(mass) | (fresh["source"] == "manual")]
    for n in notes:
        print(n, flush=True)
    merged = merge_calendar(prev, fresh, now, prev_ok, fetched_types=fetched, allow_cancellations=allow)
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
    gaps = monthly_gaps(merged, through=now)
    lines.append(f"monthly gap check (> 45 days; a lost release or a shutdown): {len(gaps)}"
                 + (": " + "; ".join(gaps) if gaps else ""))
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
