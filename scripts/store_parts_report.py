"""Part counts, sizes and read times of the append-only store tables (backlog 179, R146): run on the brain before
`ticks` joins settings `compact.tables`, and after, to show the effect. Read only: it lists files, reads Parquet
footers and times one `Store.read` per source/symbol folder; nothing is written.

    python scripts/store_parts_report.py [--data-root data] [--tables ticks fills ...] [--no-read]

Prints one JSON line per folder: table, source, symbol, closed and open month part counts, rows, bytes, the largest
closed month (rows, parts), the timed read (s) and the process's peak RSS (MB) after it.
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from goldbot.data.store import Store

TABLES = ["ticks", "observe_ticks", "fills", "dq_events", "decisions"]


def folder_report(store: Store, table: str, folder: Path, now: pd.Timestamp, read: bool) -> dict[str, object]:
    source, symbol = folder.parent.name.split("=", 1)[1], folder.name.split("=", 1)[1]
    cutoff = now.year * 12 + now.month
    out: dict[str, object] = {"table": table, "source": source, "symbol": symbol}
    closed_parts = open_parts = rows = size = 0
    biggest = (0, 0, "")
    for mdir in sorted(folder.glob("year=*/month=*")):
        files = list(mdir.glob("*.parquet"))
        ym = int(mdir.parent.name.split("=", 1)[1]) * 12 + int(mdir.name.split("=", 1)[1])
        n = sum(pq.read_metadata(f).num_rows for f in files)
        rows += n
        size += sum(f.stat().st_size for f in files)
        if ym < cutoff:
            closed_parts += len(files)
            biggest = max(biggest, (n, len(files), f"{mdir.parent.name}/{mdir.name}"))
        else:
            open_parts += len(files)
    out.update(closed_parts=closed_parts, open_parts=open_parts, rows=rows, mb=round(size / 1e6, 1),
               biggest_closed_month={"rows": biggest[0], "parts": biggest[1], "month": biggest[2]})
    if read:
        t0 = time.monotonic()
        df = store.read(table, source=source, symbol=symbol)
        out["read_s"] = round(time.monotonic() - t0, 3)
        out["read_rows"] = len(df)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        out["peak_rss_mb"] = round(rss / (1e6 if sys.platform == "darwin" else 1e3), 1)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-root", default="data")
    ap.add_argument("--tables", nargs="*", default=TABLES)
    ap.add_argument("--no-read", action="store_true", help="footers only: no timed Store.read")
    a = ap.parse_args(argv)
    store = Store(a.data_root)
    now = pd.Timestamp.now("UTC")
    for table in a.tables:
        tdir = store.table_dir(table)
        for folder in sorted(tdir.glob("source=*/symbol=*")):
            print(json.dumps(folder_report(store, table, folder, now, not a.no_read)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
