"""Quarantine of rows already in the store, and the spans they leave (design: Data quality, point in time).

Rows found wrong after they were written (a feed stamped with the wrong server clock, for example) are never edited in
place and never deleted: `move_rows` moves them out of their table into its quarantine table (`ticks` ->
`ticks_quarantine`, a bar table -> `bars_quarantine`) with `dq_flag`, `quarantine_reason` and `quarantined_utc`, so
every reader that reads the table no longer sees them and the original rows stay reproducible. The quarantine copy is
written first and the table partition is rewritten after it (atomic rename, under the partition's writer lock), so a
crash leaves the rows in both places, never in neither; a re-run moves nothing more (idempotent).

`record_span` writes the time span the quarantine covers to `dq_events` (check `quarantine_span`, columns `span_from`,
`span_to`, source = the table's source), and `spans` reads them back: the D12 reconciliation leaves those minutes out
of its comparison and reports them, instead of grading a known, quarantined window as a feed fault.

A corrected version of quarantined rows, when there is one, is a new row with `available_utc` = the time it was
written (never backdated): it was not knowable before.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd
import pyarrow.parquet as pq

from goldbot.data.store import Store

SPAN_CHECK = "quarantine_span"
QUARANTINE_TABLE = {"ticks": "ticks_quarantine", "bars_1m": "bars_quarantine", "bars_1m_live": "bars_quarantine"}

Select = Callable[[Path, pd.DataFrame], "pd.Series[bool]"]


def move_rows(store: Store, table: str, *, source: str, symbol: str, select: Select, reason: str,
              now: pd.Timestamp, apply: bool = False) -> int:
    """Move the rows `select(part_file, rows)` marks out of `table` (source, symbol) into its quarantine table.
    `select` sees each part file with its own rows (so it can use the file's write time). Returns the rows selected;
    with apply=False (the default) nothing is written."""
    qtable = QUARANTINE_TABLE[table]
    root = store.table_dir(table) / f"source={source}" / f"symbol={symbol}"
    moved = 0
    for pdir in sorted(p for p in root.glob("year=*/month=*") if p.is_dir()):
        with store._partition_lock(pdir):
            files = sorted(pdir.glob("*.parquet"))
            keep, out = [], []
            for f in files:
                rows = pq.read_table(f).to_pandas()
                mask = select(f, rows).to_numpy(dtype=bool)
                keep.append(rows[~mask])
                out.append(rows[mask])
            gone = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
            if gone.empty:
                continue
            moved += len(gone)
            if not apply:
                continue
            gone = gone.assign(dq_flag=f"error:{reason}", quarantine_reason=reason,
                               quarantined_utc=pd.Timestamp(now).tz_convert("UTC"), from_table=table)
            store.append(qtable, gone.drop(columns=[c for c in ("source", "symbol", "year", "month") if c in gone]),
                         source=source, symbol=symbol, dedupe=False)
            rest = pd.concat(keep, ignore_index=True).sort_values("ts_utc", kind="stable")
            if not rest.empty:
                store._write_atomic(pdir, rest.reset_index(drop=True))
            for f in files:
                f.unlink(missing_ok=True)
    return moved


def record_span(store: Store, *, source: str, symbol: str, start: pd.Timestamp, end: pd.Timestamp, reason: str,
                detail: str, now: pd.Timestamp) -> bool:
    """Record [start, end) as quarantined for `source` (idempotent: an identical span is not written twice)."""
    start, end = pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC")
    if any(a == start and b == end for a, b in spans(store, source=source, symbol=symbol, start=start, end=end)):
        return False
    row = pd.DataFrame([{"ts_utc": start, "check": SPAN_CHECK, "severity": "error", "detail": f"{reason}: {detail}",
                         "span_from": start, "span_to": end, "recorded_utc": pd.Timestamp(now).tz_convert("UTC")}])
    store.append("dq_events", row, source=source, symbol=symbol, dedupe=False)
    return True


def spans(store: Store, *, source: str, symbol: str, start: pd.Timestamp, end: pd.Timestamp,
          lookback: pd.Timedelta = pd.Timedelta(days=31)) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Quarantined spans of `source` overlapping [start, end) (a span is stamped at its start: read back `lookback`)."""
    try:
        ev = store.read("dq_events", source=source, symbol=symbol, start=pd.Timestamp(start) - lookback, end=end)
    except Exception:            # noqa: BLE001 - no events table or a schema without the span columns: no spans
        return []
    if ev.empty or "check" not in ev.columns or "span_to" not in ev.columns:
        return []
    ev = ev[ev["check"] == SPAN_CHECK]
    out = []
    for a, b in zip(pd.to_datetime(ev["span_from"], utc=True), pd.to_datetime(ev["span_to"], utc=True)):
        if a < end and b > start:
            out.append((pd.Timestamp(a), pd.Timestamp(b)))
    return sorted(set(out))


__all__ = ["QUARANTINE_TABLE", "SPAN_CHECK", "move_rows", "record_span", "spans"]
