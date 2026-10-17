"""Strict parser for the `months` input of the data-dukascopy workflow (targeted refetch, G1/G2).

Standard library only: the workflow's plan job runs it before any dependency is installed, and
scripts/dukascopy_year.py imports the same function, so the workflow and the script accept exactly the same lists.

  python3 scripts/dukascopy_months.py "2024-01,2024-06,2026-06"
  -> years=[2024, 2026]
     months=2024-01,2024-06,2026-06

Exit status 2 and nothing on stdout for any list that is not comma-separated YYYY-MM months (ASCII digits only,
spaces allowed around commas), from 2010-01 up to the current month, at most MAX_MONTHS of them.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys

FIRST_YEAR = 2010   # the first year data-v1 holds
MAX_MONTHS = 24     # a targeted refetch costs up to ~80 runner-minutes a month when Dukascopy throttles
_MONTH = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")


def parse_months(text: str, today: dt.date | None = None, max_months: int = MAX_MONTHS) -> list[str]:
    """Sorted, de-duplicated YYYY-MM keys, or ValueError. A month is accepted only if it has a completed day by
    `today` (the same rule as dukascopy_year.month_range: today's file is published after the close)."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    if max_months < 1:
        raise ValueError("max_months must be at least 1")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("months is empty")
    out: set[str] = set()
    for raw in text.split(","):
        item = raw.strip(" ")
        if not _MONTH.fullmatch(item):
            raise ValueError(f"not a YYYY-MM month: {raw!r}")
        start = dt.date(int(item[:4]), int(item[5:]), 1)
        if start.year < FIRST_YEAR:
            raise ValueError(f"{item} is before {FIRST_YEAR}-01")
        if start >= today:
            raise ValueError(f"{item} has no completed day yet (today is {today:%Y-%m-%d} UTC)")
        out.add(item)
    if len(out) > max_months:
        raise ValueError(f"at most {max_months} months per dispatch (got {len(out)}); split the list")
    return sorted(out)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: dukascopy_months.py YYYY-MM[,YYYY-MM...]", file=sys.stderr)
        return 2
    try:
        months = parse_months(argv[0])
    except ValueError as e:
        print(f"invalid months: {e}", file=sys.stderr)
        return 2
    print("years=" + json.dumps(sorted({int(m[:4]) for m in months})))
    print("months=" + ",".join(months))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
