"""Pre-commit guard: refuse market data in this public repository (ADR 0048, Dukascopy terms: no redistribution).

Refused, whatever the file is called:
* columnar data files (Parquet, Feather/Arrow IPC, Dukascopy bi5), recognised by extension or by their magic bytes
  (`PAR1`, `ARROW1`), outside the fixture folders;
* delimited text (CSV/TSV, also gzipped) whose header row looks like price bars or ticks: a time column plus open/high/
  low/close, or bid/ask, as whole cells; outside the fixture folders;
* inside the fixture folders (FIXTURE_DIRS: small synthetic test data, never market data) any such file larger than
  MAX_FIXTURE_BYTES: a real year of 1m bars is about 13 MB, a synthetic golden fixture a few hundred KB.

The bars themselves live in the gitignored raw/ (yearly files) and data/ (the store); .gitignore keeps them out of
`git add .`, this hook stops a forced add (`git add -f`). tests/test_no_market_data.py also runs it over every tracked
file, so CI fails even when the hook was skipped.

Usage: python scripts/check_no_market_data.py FILE... (exit 1 and the offending names when any match).
"""
from __future__ import annotations

import csv
import gzip
import io
import re
import sys
from pathlib import Path

DATA_EXT = (".parquet", ".pq", ".feather", ".arrow", ".ipc", ".bi5")
TEXT_EXT = (".csv", ".tsv", ".csv.gz", ".tsv.gz", ".txt")
MAGIC = (b"PAR1", b"ARROW1")
FIXTURE_DIRS = ("tests/data/", "tests/fixtures/")
MAX_FIXTURE_BYTES = 1_000_000
HEAD_BYTES = 64 * 1024
TIME_COLS = frozenset({"ts", "ts_utc", "time", "timestamp", "datetime", "date", "gmt time", "local time", "visible_at"})
OHLC = frozenset({"open", "high", "low", "close"})
QUOTE = frozenset({"bid", "ask"})


def _cells(line: str) -> set[str]:
    try:
        dialect = csv.Sniffer().sniff(line, delimiters=",;\t|")
        row = next(csv.reader([line], dialect))
    except (csv.Error, StopIteration):
        row = re.split(r"[,;\t|]", line)
    return {re.sub(r"[\s\"']+", " ", c).strip().lower() for c in row}


def looks_like_price_header(line: str) -> bool:
    cells = _cells(line.lstrip("\ufeff"))
    prices = {part for c in cells for part in re.split(r"[_\s]+", c)}   # bid_close, ask_open, close_bid ... by part
    has_time = bool(cells & TIME_COLS)
    return has_time and (OHLC <= cells or OHLC <= prices or QUOTE <= cells or QUOTE <= prices)


def _head(path: Path) -> bytes:
    with open(path, "rb") as fh:
        raw = fh.read(HEAD_BYTES)
    if raw[:2] == b"\x1f\x8b":
        try:
            with gzip.open(path, "rb") as gz:
                raw = gz.read(HEAD_BYTES)
        except (OSError, EOFError):
            pass
    return raw


def reason(path: Path, rel: str | None = None) -> str | None:
    """Why `path` (repo-relative name `rel`) must not be committed; None when it may."""
    name = (rel or path.as_posix()).replace("\\", "/")
    low = name.lower()
    if not path.is_file():
        return None
    head = _head(path)
    is_data = low.endswith(DATA_EXT) or head.startswith(MAGIC)
    is_price_text = False
    if not is_data and (low.endswith(TEXT_EXT) or b"," in head[:4096]):
        first = io.StringIO(head.decode("utf-8", "replace")).readline()
        is_price_text = looks_like_price_header(first)
    if not (is_data or is_price_text):
        return None
    kind = "columnar data file" if is_data else "price-bar/tick table"
    if any(low.startswith(d) for d in FIXTURE_DIRS):
        size = path.stat().st_size
        if size > MAX_FIXTURE_BYTES:
            return f"{kind} of {size:,} bytes in a fixture folder (max {MAX_FIXTURE_BYTES:,}: fixtures are synthetic)"
        return None
    return f"{kind} outside {', '.join(FIXTURE_DIRS)} (market data stays in the gitignored raw/ and data/)"


def main(argv: list[str]) -> int:
    bad = [(f, r) for f in argv if (r := reason(Path(f), f))]
    for f, r in bad:
        print(f"{f}: {r}; ADR 0048, Dukascopy terms forbid redistribution and this repository is public")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
