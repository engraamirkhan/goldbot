"""Download COMEX gold Commitments of Traders (CFTC) and SPDR Gold Shares holdings and write the point-in-time Parquet
the data-positioning workflow publishes on release `positioning-v1` (goldbot/data/positioning.py has the sources and
the availability rules).

With --existing (the last published file) history is kept as published: only new dates and revised values are added
(merge_releases), and a row that should have been in the previous download but was not is stamped no earlier than this
run. COT: the first run downloads 2006 onwards; later runs this year and last. GLD: the whole archive each run.

Never loses history: --existing must name a file that exists unless --first-run says there is no published file yet
(the workflow passes --first-run only when the release or its asset is confirmed missing), and a merged frame with
fewer rows for any source than the previous file, or without every previously published row, is refused
(check_keeps_history; exit 2, no output, so the publish step uploads nothing).

Exit status: 2 when history would be lost (or --existing is missing without --first-run); 1 when COT fails (its published rows are kept) or nothing could be written; a GLD source that is
unavailable (blocked or reformatted; it is not scraped around) keeps its published rows, is reported, and exits 0, so a
known gap does not open an issue every week.

  python scripts/positioning_data.py --out positioning.parquet --existing prev/positioning.parquet [--first-run]
                                     [--from-year 2006]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.positioning import (  # noqa: E402
    COT_FIRST_YEAR,
    GldSourceUnavailable,
    HistoryLoss,
    check_keeps_history,
    cot_frame,
    fetch_cot,
    fetch_gld,
    gld_frame,
    merge_releases,
    read_release,
    write_release,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="positioning.parquet")
    ap.add_argument("--existing", default="", help="previously published positioning Parquet (kept as is)")
    ap.add_argument("--from-year", type=int, default=0, help="first COT year (default: 2006, or last year with --existing)")
    ap.add_argument("--first-run", action="store_true",
                    help="there is no published file yet (confirmed); allows --existing to be missing")
    ap.add_argument("--skip-gld", action="store_true")
    args = ap.parse_args(argv)
    if args.existing and not Path(args.existing).exists() and not args.first_run:
        print(f"REFUSED: {args.existing} does not exist and --first-run was not given; a missing previous file must "
              "never be mistaken for a first run (it would rebuild from scratch and drop published history)")
        return 2
    prev, last_ok = (read_release(args.existing) if args.existing and Path(args.existing).exists() else (None, {}))
    now = pd.Timestamp.now(tz="UTC")
    has_cot = prev is not None and (prev["source"] == "cftc").any()
    first = args.from_year or (now.year - 1 if has_cot else COT_FIRST_YEAR)
    fresh, notes, cot_failed = [], [], False
    try:
        cot = fetch_cot(list(range(first, now.year + 1)))
        fresh.append(cot_frame(cot, now))
        last_ok["cftc"] = now
        notes.append(f"COT: {len(cot):,} reports {cot['report_date'].min():%Y-%m-%d} .. {cot['report_date'].max():%Y-%m-%d}")
    except (RuntimeError, ValueError, OSError) as exc:
        cot_failed = True
        notes.append(f"COT FAILED: {exc}")
    if args.skip_gld:
        notes.append("GLD: skipped (--skip-gld)")
    else:
        try:
            gld = fetch_gld()
            fresh.append(gld_frame(gld, now))
            last_ok["spdr"] = now
            notes.append(f"GLD: {len(gld):,} days {gld['value_date'].min():%Y-%m-%d} .. {gld['value_date'].max():%Y-%m-%d}")
        except GldSourceUnavailable as exc:
            notes.append(f"GLD SOURCE UNAVAILABLE (published rows kept, not scraped): {exc}")
    for n in notes:
        print(n, flush=True)
    if not fresh and prev is None:
        print("nothing downloaded and no previous file: no output")
        return 1
    merged = merge_releases(prev, pd.concat(fresh, ignore_index=True), now, last_ok) if fresh else prev
    assert merged is not None
    try:
        check_keeps_history(prev, merged)
    except HistoryLoss as exc:
        print(f"REFUSED (nothing written, nothing published): {exc}")
        return 2
    write_release(merged, args.out, last_ok)
    n_prev = 0 if prev is None else len(prev)
    lines = ["", "| series | rows | first value_date | last value_date | last available_utc |", "|---|---:|---|---|---|"]
    for s, g in merged.groupby("series"):
        lines.append(f"| {s} | {len(g):,} | {g['value_date'].min():%Y-%m-%d} | {g['value_date'].max():%Y-%m-%d} | "
                     f"{pd.Timestamp(g['available_utc'].max()):%Y-%m-%d %H:%M} |")
    lines.append(f"\n{len(merged) - n_prev:,} rows added to {n_prev:,} published; wrote {args.out}")
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(["## positioning-v1", "", *[f"- {n}" for n in notes], *lines]) + "\n")
    return 1 if cot_failed else 0


if __name__ == "__main__":
    sys.exit(main())
