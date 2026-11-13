"""Compaction of a store table's small part files, off the hot path (backlog 170; other tables: backlog 179, R146).

The engines append the `decisions` journal with dedupe=False: one part file per flush, 2-16 per 15 min per account
(9,447 parts / 75 MB on the brain on 2026-11-10). Every read lists and opens the schema of each part (union_by_name).
The nightly `journal_compact` job (ops/jobs.py) rewrites each CLOSED month partition (before the current UTC month,
so never the one an engine is writing) of every source=/symbol= folder into one part:

- the new part is read, merged, written to a temp name and verified WITHOUT the partition lock (an engine flush is
  not held up; parts are never rewritten in place, so the listed files are a stable snapshot). Only the swap runs
  under the exclusive writer lock (`Store._partition_lock`, bounded wait): it checks every listed part is still
  there (a dedupe rewrite in between: the partition is skipped until the next night), writes the manifest, renames
  the new part and unlinks the listed ones, milliseconds. A part appended meanwhile is not in the list and stays.
  A reader holds the shared locks of the partitions it reads, so it sees the old parts or the new one, never both;
- every row and column kept exactly: no dedupe (identical journal rows are legitimate). The new part is checked
  before it is published: the same row count and the same content hash (sha256 of the `Store.read`-ordered frame
  DuckDB returns for the old parts and for the new file); a mismatch drops the new file and keeps the old parts;
- rows on disk in part-write order (the parts by mtime), streamed batch by batch into row groups of at most
  `row_group_rows` (backlog 179: a tick month is ~10^7 rows, never one frame), so the write time of every row survives
  as lineage: the new part's Parquet metadata (`goldbot.lineage`) lists each replaced part's name, mtime and rows in
  file order (`written_utc`). A tick carries no write time of its own; its part's mtime was the only evidence of the
  2026-10-26 tz quarantine. `Store.read` orders by content (backlog 162), so the on-disk order never shows in a read;
- the verifying read is DuckDB's sorted read with a memory limit, a thread cap and a spill directory under the store
  root (`.compact_tmp`, removed after the run), hashed batch by batch: memory bounded on the 4-core brain;
- the tables (settings `compact.tables`): decisions, fills, dq_events, observe_ticks; ticks once measured on the brain.
  A run is time-boxed (`time_box_s`: partitions not started are `deferred` to the next night) and paced (`pause_s`);
  a closed month is compacted from `min_parts` parts. Fills keep every row (no dedupe anywhere here);
- crash-safe: a manifest (`.compact.json`, fsynced) naming the new part and the parts it replaces is written
  before the rename. A crash between the rename and the unlinks leaves rows twice on disk: `Store.read`, `Store.sql`
  and `Store._read_partition` skip every part a manifest replaced while its new part exists (`compacted_away`), and
  the next run, under the lock, finishes the unlinks first. A crash before the rename leaves the old parts as the
  partition; the next run drops the manifest and the temp file. Each run also sweeps stale `.part-*.tmp` files (an
  append that died before its rename) under the lock, where no append can be mid-write.

Late rows of a closed month (a flush just after the month turned) land as a new part and are folded in the next night.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from goldbot.base import Record
from goldbot.data.store import (
    MANIFEST,
    Store,
    _connect,
    _fsync_dir,
    _order_by,
    _scan,
)

log = logging.getLogger(__name__)

LINEAGE = b"goldbot.lineage"      # Parquet key-value metadata: [{part, written_ns, rows}, ...] in file row order
SPILL_DIR = ".compact_tmp"        # under the store root: DuckDB's spill files of the verifying sorted read
ROW_GROUP_ROWS = 262_144


class Budget(Record):
    """Resource bounds of one run (settings `compact`)."""
    memory_limit: str = "1GB"             # DuckDB's limit for the verifying sorted read (it spills beyond)
    threads: int = 2                      # of the brain's 4 cores
    row_group_rows: int = ROW_GROUP_ROWS  # rows held at once by the streamed build


class PartitionResult(Record):
    source: str
    symbol: str
    year: int
    month: int
    parts_before: int
    parts_after: int
    rows: int
    hash_before: str | None = None
    hash_after: str | None = None
    recovered: bool = False                  # a manifest of an earlier, interrupted run was finished or dropped
    skipped: bool = False                    # a listed part changed before the swap: left for the next night
    error: str | None = None


class CompactReport(Record):
    table: str
    partitions: list[PartitionResult] = []
    deferred: int = 0                        # closed partitions due but not started: the time box ran out

    def summary(self) -> dict[str, object]:
        ok = [p for p in self.partitions if p.error is None and not p.skipped]
        folders: dict[str, dict[str, int]] = {}
        for p in ok:
            f = folders.setdefault(f"{p.source}/{p.symbol}", {"partitions": 0, "parts_before": 0, "parts_after": 0})
            f["partitions"] += 1
            f["parts_before"] += p.parts_before
            f["parts_after"] += p.parts_after
        return {"table": self.table, "partitions": len(ok), "parts_before": sum(p.parts_before for p in ok),
                "parts_after": sum(p.parts_after for p in ok), "rows": sum(p.rows for p in ok),
                "recovered": sum(p.recovered for p in self.partitions),
                "skipped": sum(p.skipped for p in self.partitions), "deferred": self.deferred,
                "folders": folders,
                "errors": [f"{p.source}/{p.symbol}/{p.year}-{p.month:02d}: {p.error}"
                           for p in self.partitions if p.error is not None]}


class CompactMismatch(RuntimeError):
    """The new part does not read back as the old parts did: it is dropped, the old parts stay."""


def compact_table(store: Store, table: str, *, before: pd.Timestamp, lock_timeout_s: float | None = None,
                  min_parts: int = 2, time_box_s: float | None = None, pause_s: float = 0.0,
                  memory_limit: str = "1GB", threads: int = 2, row_group_rows: int = ROW_GROUP_ROWS) -> CompactReport:
    """Compact every partition of `table` whose month is before `before`'s UTC month (closed months only) and that
    holds at least `min_parts` parts, or an interrupted run's manifest or temp file. One partition failing never stops
    the others: its error is in the report and its old parts stay as they were. The lock wait is bounded
    (`lock_timeout_s`, default the store's): a stuck holder fails that partition loudly instead of hanging the night.
    `time_box_s`: no partition is started after that many seconds (the rest are `deferred`; a started one finishes);
    `pause_s`: a sleep after each compacted partition, so the engines' flushes and reads keep the disk."""
    timeout = store.lock_timeout_s if lock_timeout_s is None else lock_timeout_s
    budget = Budget(memory_limit=memory_limit, threads=threads, row_group_rows=row_group_rows)
    t = pd.Timestamp(before)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    cutoff = t.year * 12 + t.month
    report = CompactReport(table=table)
    tdir = store.table_dir(table)
    if not tdir.exists():
        return report
    t0 = time.monotonic()
    spill = store.root / SPILL_DIR
    try:
        for pdir in sorted(tdir.glob("source=*/symbol=*/year=*/month=*")):
            try:
                source, symbol, year, month = (p.name.split("=", 1)[1] for p in
                                               (pdir.parents[2], pdir.parents[1], pdir.parent, pdir))
                y, m = int(year), int(month)
            except (ValueError, IndexError):
                continue
            if y * 12 + m >= cutoff:
                continue                          # the open month (or a future one): an engine may be writing it
            if len(list(pdir.glob("*.parquet"))) < max(min_parts, 2) and not any(pdir.glob(".compact*")) \
                    and not any(pdir.glob(".part-*.tmp")):
                continue
            if time_box_s is not None and time.monotonic() - t0 >= time_box_s:
                report.deferred += 1              # the next night's run starts with it
                continue
            try:
                res = _compact_partition(pdir, table, timeout, budget, spill)
            except Exception as exc:              # one bad partition does not stop the night's run
                log.exception("compaction of %s failed; its parts are untouched", pdir)
                res = PartitionResult(source=source, symbol=symbol, year=y, month=m, parts_before=0, parts_after=0,
                                      rows=0, error=f"{type(exc).__name__}: {exc}")
            else:
                if res is None:
                    continue
                res = res.model_copy(update={"source": source, "symbol": symbol, "year": y, "month": m})
            report.partitions.append(res)
            if pause_s > 0:
                time.sleep(pause_s)
    finally:
        shutil.rmtree(spill, ignore_errors=True)
    return report


def _compact_partition(pdir: Path, table: str, timeout_s: float, budget: Budget | None = None,
                       spill: Path | None = None) -> PartitionResult | None:
    budget = budget or Budget()
    with Store._partition_lock(pdir, timeout_s):          # recovery first: a half-done swap is finished or dropped
        recovered = _recover(pdir)
        files = sorted(pdir.glob("*.parquet"))
    if len(files) <= 1:
        if not recovered:
            return None
        n = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
        return PartitionResult(source="", symbol="", year=0, month=0, parts_before=len(files),
                               parts_after=len(files), rows=n, recovered=True)
    # the build, outside the lock: parts are never rewritten in place, so `files` is a stable snapshot
    rows = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
    hash_before, n_before = _store_hash(files, table, budget=budget, spill=spill)
    tmp, name = _build(pdir, files, budget.row_group_rows)
    try:
        hash_after, n_after = _store_hash([tmp], table, budget=budget, spill=spill)
        if not (n_before == n_after == rows == pq.ParquetFile(tmp).metadata.num_rows) or hash_after != hash_before:
            raise CompactMismatch(f"{pdir}: {rows} rows / {hash_before[:12]} before, {n_after} rows / "
                                  f"{hash_after[:12]} in the new part")
        with Store._partition_lock(pdir, timeout_s):      # the swap: milliseconds
            if not all(f.exists() for f in files):        # a dedupe rewrite replaced a listed part meanwhile
                tmp.unlink(missing_ok=True)
                log.warning("compaction of %s skipped: its parts changed during the build", pdir)
                return PartitionResult(source="", symbol="", year=0, month=0, parts_before=len(files),
                                       parts_after=len(files), rows=rows, recovered=recovered, skipped=True)
            _write_manifest(pdir / MANIFEST, {"new": name, "tmp": tmp.name, "replaces": [f.name for f in files],
                                              "rows": rows, "hash": hash_before,
                                              "created_utc": pd.Timestamp.now("UTC").isoformat()})
            _rename_new_part(tmp, pdir / name)
            _unlink_replaced(pdir, files)
            (pdir / MANIFEST).unlink()
            _fsync_dir(pdir)
    except BaseException:
        tmp.unlink(missing_ok=True)           # never published (after the rename the temp name is gone already)
        raise
    log.info("compacted %s: %d parts -> 1, %d rows, sha256 %s before and after", pdir, len(files), rows,
             hash_before[:16])
    return PartitionResult(source="", symbol="", year=0, month=0, parts_before=len(files), parts_after=1,
                           rows=rows, hash_before=hash_before, hash_after=hash_after, recovered=recovered)


def _recover(pdir: Path) -> bool:
    """Finish or drop an interrupted run's manifest (caller holds the partition lock). New part on disk: unlink the
    parts it replaced. Not on disk (a crash before the rename): the old parts are the partition; drop the temp file.
    Stale `.part-*.tmp` files (an append or a build that died before its rename) go too: under the exclusive lock no
    append is mid-write."""
    changed = False
    m = pdir / MANIFEST
    if m.exists():
        try:
            doc = json.loads(m.read_text())
            new, replaces = str(doc["new"]), [str(r) for r in doc["replaces"]]
        except (OSError, ValueError, KeyError, TypeError):
            new, replaces, doc = "", [], {}
        if new and (pdir / new).exists():
            for r in replaces:
                if r != new:
                    (pdir / r).unlink(missing_ok=True)
            log.warning("compaction of %s: finished an interrupted run (%d replaced parts unlinked)", pdir,
                        len(replaces))
        elif doc.get("tmp"):
            (pdir / str(doc["tmp"])).unlink(missing_ok=True)
        m.unlink(missing_ok=True)
        changed = True
    for leftover in [*pdir.glob(MANIFEST + ".tmp"), *pdir.glob(".part-*.tmp")]:
        leftover.unlink(missing_ok=True)
        changed = True
    if changed:
        _fsync_dir(pdir)
    return changed


def _lineage(f: Path) -> list[dict[str, int | str]]:
    """The write time of `f`'s rows as segments in file row order: a compacted part's own record, else the part's
    mtime for all of its rows."""
    md = pq.read_metadata(f)
    kv = md.metadata or {}
    if LINEAGE in kv:
        try:
            segs = json.loads(kv[LINEAGE])
            out: list[dict[str, int | str]] = [{"part": str(g["part"]), "written_ns": int(g["written_ns"]),
                                                "rows": int(g["rows"])} for g in segs]
            if sum(int(g["rows"]) for g in out) == md.num_rows:
                return out
        except (ValueError, KeyError, TypeError):
            pass                              # an odd record: the part's own mtime, as for a plain part
    return [{"part": f.name, "written_ns": f.stat().st_mtime_ns, "rows": md.num_rows}]


def written_utc(path: str | Path) -> pd.Series:
    """When each row of the part file `path` was written (UTC, in file row order): the mtime of the part it was first
    appended in, kept through compaction (`goldbot.lineage`). A part never compacted: its own mtime for every row."""
    segs = _lineage(Path(path))
    ns = np.repeat(np.array([int(g["written_ns"]) for g in segs], dtype="int64"),
                   [int(g["rows"]) for g in segs])
    return pd.Series(pd.to_datetime(ns, unit="ns", utc=True), name="written_utc")


def _conform(batch: pa.RecordBatch, schema: pa.Schema) -> pa.RecordBatch:
    """`batch` with `schema`'s columns, in its order and types (a column the part lacks reads as null, as
    union_by_name gives it)."""
    names = batch.schema.names
    cols = [batch.column(names.index(f.name)).cast(f.type) if f.name in names else pa.nulls(batch.num_rows, f.type)
            for f in schema]
    return pa.RecordBatch.from_arrays(cols, schema=schema)


def _build(pdir: Path, files: list[Path], row_group_rows: int) -> tuple[Path, str]:
    """Every row of `files`, parts in write order (mtime, name), columns unified by name, streamed to a temp part in
    `pdir` (not *.parquet: invisible to readers) in row groups of at most `row_group_rows`, fsynced. Memory: one
    buffer of `row_group_rows` rows. Returns the temp path and the part name it is to be renamed to."""
    lin = {f: _lineage(f) for f in files}
    order = sorted(files, key=lambda f: (int(lin[f][0]["written_ns"]), f.name))
    schema = pa.unify_schemas([pq.read_schema(f).remove_metadata() for f in order], promote_options="permissive")
    schema = schema.with_metadata({LINEAGE: json.dumps([g for f in order for g in lin[f]]).encode()})
    name = f"part-{uuid.uuid4().hex[:8]}.parquet"
    tmp = pdir / f".{name}.tmp"
    buf: list[pa.RecordBatch] = []
    held = 0
    try:
        with pq.ParquetWriter(tmp, schema, compression="zstd") as w:
            for f in order:
                for raw in pq.ParquetFile(f).iter_batches(batch_size=row_group_rows):
                    batch = _conform(raw, schema)
                    if buf and held + batch.num_rows > row_group_rows:
                        w.write_table(pa.Table.from_batches(buf, schema=schema), row_group_size=row_group_rows)
                        buf, held = [], 0
                    buf.append(batch)
                    held += batch.num_rows
            if buf:
                w.write_table(pa.Table.from_batches(buf, schema=schema), row_group_size=row_group_rows)
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, name


_FIXED = (pa.types.is_integer, pa.types.is_floating, pa.types.is_boolean)


def _store_hash(files: list[Path], table: str, *, budget: Budget | None = None,
                spill: Path | None = None) -> tuple[str, int]:
    """sha256 and row count of the read `Store.read` gives for `files` (DuckDB, union_by_name, the store order),
    streamed: DuckDB sorts within `budget.memory_limit` (spilling to `spill`) and hands back Arrow batches, each
    hashed column by column (one digest per column for its values and one for its validity, so where a batch starts
    never changes the result)."""
    budget = budget or Budget()
    con = _connect()
    try:
        con.execute(f"SET memory_limit = '{budget.memory_limit}'")
        con.execute(f"SET threads = {int(budget.threads)}")
        if spill is not None:
            spill.mkdir(parents=True, exist_ok=True)
            con.execute("SET temp_directory = '" + str(spill).replace("'", "''") + "'")
        reader = _arrow_reader(con, f"SELECT * FROM {_scan([str(f) for f in files], table)} {_order_by(table)}",
                               budget.row_group_rows)
        schema = reader.schema
        values = [hashlib.sha256() for _ in schema]
        valid = [hashlib.sha256() for _ in schema]
        n = 0
        for batch in reader:
            n += batch.num_rows
            for i, col in enumerate(batch.columns):
                valid[i].update(col.is_valid().to_numpy(zero_copy_only=False).tobytes())
                values[i].update(_column_bytes(col))
    finally:
        con.close()
    h = hashlib.sha256()
    h.update("\x1f".join(f"{f.name}:{f.type}" for f in schema).encode())
    for v, ok in zip(values, valid):
        h.update(v.digest())
        h.update(ok.digest())
    h.update(str(n).encode())
    return h.hexdigest(), n


def _arrow_reader(con: object, sql: str, batch_rows: int) -> pa.RecordBatchReader:
    """A streaming Arrow reader of `sql`: the relation API (a DB-API result copies every row first on duckdb 2.x),
    `to_arrow_reader` on 2.x, `fetch_arrow_reader` on 1.x."""
    res = con.sql(sql)  # type: ignore[attr-defined]
    for name in ("to_arrow_reader", "fetch_arrow_reader", "record_batch"):
        fn = getattr(res, name, None)
        if fn is not None:
            reader: pa.RecordBatchReader = fn(batch_rows)
            return reader
    raise RuntimeError("this duckdb has no streaming Arrow reader")


def _column_bytes(col: pa.Array) -> bytes:
    """A column's values as bytes whose layout does not depend on where the batch starts or on its nulls: numbers and
    booleans as their fixed-width values (nulls as 0: the validity digest tells them apart), every other type as one
    64-bit pandas hash per row (its text when a cell is unhashable, a list for example)."""
    if any(ok(col.type) for ok in _FIXED):
        fill = pa.scalar(False if pa.types.is_boolean(col.type) else 0, col.type)
        return bytes(pc.fill_null(col, fill).to_numpy(zero_copy_only=False).tobytes())
    s = col.to_pandas()
    try:
        rows = pd.util.hash_pandas_object(s, index=False)
    except TypeError:
        rows = pd.util.hash_pandas_object(s.astype(str), index=False)
    return bytes(rows.to_numpy().tobytes())


def _write_manifest(path: Path, doc: dict[str, object]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(doc, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _rename_new_part(tmp: Path, final: Path) -> None:
    os.replace(tmp, final)
    _fsync_dir(final.parent)


def _unlink_replaced(pdir: Path, files: list[Path]) -> None:
    for f in files:
        f.unlink(missing_ok=True)
    _fsync_dir(pdir)
