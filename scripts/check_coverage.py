"""Day-by-day coverage of published 1m bar files (data-v1) against the session calendar (W1-6; freeze gaps G1/G2).

A month that clears dukascopy_year.py's 15,000-bar floor can still have lost whole weekdays (coverage triage
2026-10-16: 21 weekdays in the 2010-01..2025-09 research window, 42 in the 2026 holdout). This check counts bars per
UTC day and reports every weekday the static session table expects open (at least --min-bars open minutes) that holds
fewer than --min-bars bars, unless it is an expected closure (1 Jan, Good Friday, 25 and 26 Dec, and the weekdays
next to one that falls on a weekend). Shortened US-holiday sessions (5 h holes) stay above the floor by design.

Output: a markdown report (gap days, per-month counts, and the months to refetch for a targeted data-dukascopy
dispatch). Exit 1 when any unexpected gap is found, 0 when clean, 2 on unreadable input. Read-only: never writes data.

  python scripts/check_coverage.py xauusd_1m_dukascopy_2024.parquet [more files ...]
                                   [--from 2024-01-01] [--to 2024-12-31] [--min-bars 700] [--report cov.md]
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data.calendar import DEFAULT_SESSIONS  # noqa: E402
from goldbot.data.timeutil import epoch_ns  # noqa: E402

MIN_BARS_PER_DAY = 700        # triage rule: a weekday under 700 1m bars that is not a closure is a gap
DAY_NS = 86_400 * 1_000_000_000


def good_friday(year: int) -> dt.date:
    """Good Friday (Western), from the anonymous Gregorian Easter algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return dt.date(year, month, day) - dt.timedelta(days=2)


def closure_days(year: int) -> set[dt.date]:
    """Expected full-day closures seen in every data-v1 year: 1 Jan, Good Friday, 25 and 26 Dec. A fixed holiday on a
    weekend also closes the neighbouring Friday and Monday (observed days differ between US and UK venues)."""
    out = {good_friday(year)}
    for d in (dt.date(year, 1, 1), dt.date(year, 12, 25), dt.date(year, 12, 26)):
        out.add(d)
        if d.weekday() == 5:
            out |= {d - dt.timedelta(days=1), d + dt.timedelta(days=2)}
        elif d.weekday() == 6:
            out |= {d - dt.timedelta(days=2), d + dt.timedelta(days=1)}
    return out


def _expected_open_minutes(first: dt.date, last: dt.date) -> pd.Series:
    """Open minutes per UTC day under the static session table (server time zone resolved per minute)."""
    grid = pd.date_range(pd.Timestamp(first, tz="UTC"), pd.Timestamp(last + dt.timedelta(days=1), tz="UTC"),
                         freq="1min", inclusive="left")
    open_ = DEFAULT_SESSIONS.is_open(grid)
    days = epoch_ns(grid) // DAY_NS
    return pd.Series(open_.astype(np.int64)).groupby(days).sum()


def coverage(bars: pd.DataFrame, first: dt.date, last: dt.date, min_bars: int = MIN_BARS_PER_DAY) -> pd.DataFrame:
    """One row per UTC day in [first, last] that the session table expects open for at least `min_bars` minutes:
    day, expected open minutes, bars, status (ok | closure | gap)."""
    expected = _expected_open_minutes(first, last)
    ns = epoch_ns(pd.to_datetime(bars["ts_utc"], utc=True))
    counts = pd.Series(ns // DAY_NS).value_counts()
    closures: set[dt.date] = set()
    for y in range(first.year - 1, last.year + 2):
        closures |= closure_days(y)
    rows = []
    epoch = dt.date(1970, 1, 1)
    for dnum, exp in zip(expected.index.to_numpy(dtype=np.int64), expected.to_numpy(dtype=np.int64)):
        if exp < min_bars:
            continue                                     # weekend or Sunday-reopen day: not a full trading day
        day = epoch + dt.timedelta(days=int(dnum))
        n = int(counts.get(dnum, 0))
        status = "ok" if n >= min_bars else ("closure" if day in closures else "gap")
        rows.append((day, int(exp), n, status))
    return pd.DataFrame(rows, columns=["day", "expected", "bars", "status"])


def gap_months(rep: pd.DataFrame) -> list[str]:
    """YYYY-MM of every month holding at least one gap: the input to a targeted refetch."""
    return sorted({f"{d:%Y-%m}" for d in rep.loc[rep["status"] == "gap", "day"]})


def render(rep: pd.DataFrame, sources: Sequence[str], first: dt.date, last: dt.date, min_bars: int) -> str:
    gaps = rep[rep["status"] == "gap"]
    lines = [f"Coverage {first} .. {last} of {', '.join(sources)} (gap: a weekday under {min_bars} 1m bars, "
             "not a listed closure)", ""]
    lines.append(f"{len(gaps)} gap day(s); {int((rep['status'] == 'closure').sum())} closure day(s); "
                 f"{len(rep)} trading weekdays checked")
    lines.append(f"refetch months: {', '.join(gap_months(rep)) or 'none'}")
    if len(gaps):
        lines += ["", "| day | weekday | bars | expected |", "|---|---|---:|---:|"]
        lines += [f"| {d} | {d:%a} | {n:,} | {e:,} |" for d, e, n, _ in gaps.itertuples(index=False)]
    month = pd.Series([f"{d:%Y-%m}" for d in rep["day"]], index=rep.index)
    by = rep.assign(month=month).groupby("month").agg(
        bars=("bars", "sum"), gaps=("status", lambda s: int((s == "gap").sum())))
    lines += ["", "| month | 1m bars on checked weekdays | gap days |", "|---|---:|---:|"]
    lines += [f"| {m} | {int(r.bars):,} | {int(r.gaps)} |" for m, r in by.iterrows()]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--from", dest="first", type=dt.date.fromisoformat, default=None,
                    help="first day (default: 1 Jan of the first bar's year)")
    ap.add_argument("--to", dest="last", type=dt.date.fromisoformat, default=None,
                    help="last day (default: 31 Dec of the last bar's year or yesterday, whichever is earlier)")
    ap.add_argument("--min-bars", type=int, default=MIN_BARS_PER_DAY)
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)
    try:
        bars = pd.concat([pd.read_parquet(f, columns=["ts_utc"]) for f in args.files], ignore_index=True)
    except (OSError, ValueError) as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return 2
    if bars.empty:
        print("no bars in input", file=sys.stderr)
        return 2
    ts = pd.to_datetime(bars["ts_utc"], utc=True)
    first = args.first or dt.date(ts.min().year, 1, 1)
    yesterday = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    last = args.last or min(dt.date(ts.max().year, 12, 31), yesterday)
    rep = coverage(bars, first, last, args.min_bars)
    text = render(rep, [Path(f).name for f in args.files], first, last, args.min_bars)
    if args.report:
        Path(args.report).write_text(text)
    print(text)
    return 1 if (rep["status"] == "gap").any() else 0


if __name__ == "__main__":
    sys.exit(main())
