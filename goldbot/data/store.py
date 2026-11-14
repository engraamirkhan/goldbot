"""Point-in-time Parquet store with DuckDB views.

Layout: data/{table}/source=X/symbol=XAUUSD/year=YYYY/month=MM/part-*.parquet
Every table carries `ts_utc` (tz-aware UTC). Tables whose rows become known later than the
event they describe (macro, positioning, news, tv_signals) also carry `available_utc`, and every join to
bars uses `available_utc`, never the nominal time. `asof_join` enforces this.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import IO, Iterable, Iterator, Mapping

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Partition locks, held across processes. POSIX: flock, shared for readers, exclusive for a writer. Windows (the VPS
# runs goldbot natively; there is no fcntl): msvcrt.locking on byte 0 of the lock file, exclusive only, so readers of
# one partition take turns. LK_LOCK gives up after ten one-second tries, so a non-blocking lock is retried here until
# it is free, and every handle is unlocked before it is closed (Windows does not promise to release it on close).
# `deadline` (time.monotonic): give up then with StoreLockTimeout naming the partition (readers and the compaction job:
# a stuck holder fails them loudly instead of freezing health, the trade chart or a report); None waits for ever.


class StoreLockTimeout(TimeoutError):
    """A partition lock was not free within the store's lock timeout (a stuck writer or compaction holds it)."""


def _timed_out(fh: IO[bytes]) -> StoreLockTimeout:
    return StoreLockTimeout(f"partition lock {fh.name} still held by a writer (append or compaction) at the deadline")


if sys.platform == "win32":
    import msvcrt

    def _lock(fh: IO[bytes], *, shared: bool, deadline: float | None = None) -> None:
        fh.seek(0)
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise _timed_out(fh) from None
                time.sleep(0.01)

    def _unlock(fh: IO[bytes]) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fh: IO[bytes], *, shared: bool, deadline: float | None = None) -> None:
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        if deadline is None:
            fcntl.flock(fh, mode)
            return
        while True:
            try:
                fcntl.flock(fh, mode | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise _timed_out(fh) from None
                time.sleep(0.01)

    def _unlock(fh: IO[bytes]) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)

TABLES = {
    "ticks", "bars_1m", "bars_15m", "bars_30m", "bars_1h", "bars_4h", "bars_1d", "bars_1w",
    "macro", "calendar_events", "news", "tv_signals", "tv_indicators", "tv_ideas", "features", "labels",
    "trades", "decisions", "dq_events", "cost_tables", "fills",
    "exit_fills",                              # every exit leg against the price it was asked at (finance GAP-04)
    "bars_quarantine",                         # 1m rows with an error-severity dq_flag, kept out of the bar tables
    "ticks_quarantine",                        # tick rows moved out of `ticks` (data/quarantine.py), kept for lineage
    "bars_1m_broker",                          # the broker's own M1 as its terminal reports it (D12/D23), never blended
    # the engine's minutes from the broker's ticks (engine/history.py), never blended. Gold: source = broker name,
    # symbol = its traded symbol; any other instrument (multi-instrument plan): source = account, symbol = its unique
    # store_symbol (goldbot/instruments.py), so it is never pooled with XAUUSD (engine/books.py `live_key`)
    "bars_1m_live",
    "bars_15m_broker",                         # the broker's own M15, the key zones' long history (engine/key_zones.py)
    "positioning",                             # COT and GLD holdings (local files only), point in time
    "observe_bars_1m",                         # ADR 0063: an observe-only account's terminal M1 (source = account), local only
    "observe_ticks",                           # ADR 0063: its sampled quotes and spreads (source = account), local only
}

KEY_COLUMNS = {
    "ticks": ["ts_utc", "bid", "ask"],
    "tv_signals": ["signal_hash"],
    # one row per plot of an indicator's bar alert (source=indicator@version): idempotent on the bar and the plot.
    # `origin` ("csv" for a chart export, absent for a live alert) keeps an import from ever replacing an alert row;
    # `meta_hash` (the direct pull's input set, absent elsewhere) keeps two input sets of one version apart.
    "tv_indicators": ["indicator", "version", "tf", "bar_time", "plot", "origin", "meta_hash"],
    "macro": ["series", "value_date", "vintage"],
    "positioning": ["series", "value_date", "vintage"],
    "fills": ["ts_utc", "client_order_id"],
    "observe_ticks": ["ts_utc", "bid", "ask"],    # ADR 0063: a quote sampled twice (no change between samples) is one row
    "exit_fills": ["ts_utc", "position_id", "kind"],
    "calendar_events": ["event_id"],          # a re-fetched event replaces the earlier copy (forecast revisions)
    "news": ["item_id"],
    # data/quarantine.py: a re-run after a crash between the quarantine copy and the table rewrite adds no second copy
    "ticks_quarantine": ["ts_utc", "bid", "ask", "from_table"],
    "bars_quarantine": ["ts_utc", "from_table", "quarantine_reason", "bid_close"],                       # headlines often share a published minute; ts_utc is not a key
}


def _utc(x: str | pd.Timestamp) -> pd.Timestamp:
    """Naive values are taken as UTC; aware ones are converted (pd.Timestamp(aware, tz=...) raises)."""
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


class Store:
    def __init__(self, root: str | Path, *, lock_timeout_s: float = 30.0):
        self.root = Path(root)
        self.lock_timeout_s = lock_timeout_s     # a reader's wait for its shared partition locks (StoreLockTimeout)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    def table_dir(self, table: str) -> Path:
        if table not in TABLES:
            raise ValueError(f"unknown table {table!r}")
        return self.root / table

    def partition_dir(self, table: str, source: str, symbol: str, ts: pd.Timestamp) -> Path:
        return self.table_dir(table) / f"source={source}" / f"symbol={symbol}" / f"year={ts.year}" / f"month={ts.month:02d}"

    # ------------------------------------------------------------------ write
    def append(self, table: str, df: pd.DataFrame, *, source: str, symbol: str = "XAUUSD", dedupe: bool = True) -> int:
        """Append rows, de-duplicating against the key columns within each partition.

        dedupe=False writes each call as a new part file without reading the partition back: for append-only
        logs (the engine's ticks and fills) where rewriting a month of rows on every flush would be the cost."""
        if df.empty:
            return 0
        df = df.copy()
        if "ts_utc" not in df.columns:
            raise ValueError("every table row needs ts_utc")
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
        df = df.sort_values("ts_utc")
        written = 0
        keys = KEY_COLUMNS.get(table, ["ts_utc"])
        for (y, m), part in df.groupby([df["ts_utc"].dt.year, df["ts_utc"].dt.month]):
            pdir = self.partition_dir(table, source, symbol, part["ts_utc"].iloc[0])
            pdir.mkdir(parents=True, exist_ok=True)
            with self._partition_lock(pdir):
                old_files = sorted(pdir.glob("*.parquet"))
                existing = self._read_partition(pdir) if dedupe else None
                if existing is not None and not existing.empty:
                    merged = pd.concat([existing, part], ignore_index=True)
                    part_out = merged.drop_duplicates(subset=[k for k in keys if k in merged.columns],
                                                      keep="last").sort_values("ts_utc")
                else:
                    part_out = part.drop_duplicates(subset=[k for k in keys if k in part.columns], keep="last") \
                        if dedupe else part
                # new file first (temp name, fsync, atomic rename), old files only after: readers never see an empty
                # partition; a crash leaves old data or old+new (the next append dedupes by key)
                self._write_atomic(pdir, part_out)
                if dedupe:
                    for f in old_files:
                        f.unlink(missing_ok=True)
            written += len(part)
        return written

    @staticmethod
    @contextmanager
    def _partition_lock(pdir: Path, timeout_s: float | None = None) -> Iterator[None]:
        """Exclusive lock held for the whole read-modify-write of one partition (across processes); `timeout_s` (the
        compaction job) bounds the wait, None (an engine append) waits."""
        with open(pdir / ".lock", "ab") as fh:
            _lock(fh, shared=False, deadline=None if timeout_s is None else time.monotonic() + timeout_s)
            try:
                yield
            finally:
                _unlock(fh)

    @staticmethod
    @contextmanager
    def _read_locks(tdir: Path, source: str | None, symbol: str | None, start: str | pd.Timestamp | None = None,
                    end: str | pd.Timestamp | None = None, timeout_s: float | None = None) -> Iterator[set[Path]]:
        """Shared lock on every partition the read can return rows from (the source/symbol folders, the months of
        [start, end]) while listing+reading: a writer holds its exclusive lock across rename+unlink (an append, or a
        compaction's swap), so a reader sees exactly the old or the new rows of a locked partition, never both.
        Partitions outside the window are pruned by their year/month before DuckDB opens a file. Fixed order; writers
        hold one lock: no deadlock. Yields the parts an interrupted compaction already replaced in those partitions
        (`compacted_away`; empty unless one died between its rename and its unlinks). The whole wait is bounded by
        `timeout_s` (StoreLockTimeout)."""
        pat = f"source={source or '*'}/symbol={symbol or '*'}/year=*/month=*/.lock"
        lo = _utc(start).year * 12 + _utc(start).month if start is not None else None
        hi = _utc(end).year * 12 + _utc(end).month if end is not None else None
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        locked: list[IO[bytes]] = []
        opened: list[IO[bytes]] = []
        manifests: list[Path] = []
        try:
            for lk in sorted(tdir.glob(pat)):
                if lo is not None or hi is not None:
                    try:
                        ym = int(lk.parent.parent.name.split("=", 1)[1]) * 12 + int(lk.parent.name.split("=", 1)[1])
                    except ValueError:
                        ym = None
                    if ym is not None and ((lo is not None and ym < lo) or (hi is not None and ym > hi)):
                        continue
                fh: IO[bytes] = open(lk, "ab")
                opened.append(fh)
                _lock(fh, shared=True, deadline=deadline)
                locked.append(fh)
                if (lk.parent / MANIFEST).exists():
                    manifests.append(lk.parent / MANIFEST)
            yield compacted_away(manifests)
        finally:
            for held in locked:
                try:
                    _unlock(held)
                except OSError:      # still released by the close below on POSIX; never mask the reader's own error
                    pass
            for f in opened:
                f.close()

    @staticmethod
    def _write_atomic(pdir: Path, df: pd.DataFrame, metadata: dict[bytes, bytes] | None = None) -> None:
        """Write `df` as one new part (temp name, fsync, rename). `metadata`: extra Parquet key-value pairs (the
        quarantine rewrite passes `goldbot.lineage`, so its rows keep their write times, backlog 240)."""
        table = pa.Table.from_pandas(df, preserve_index=False)
        if metadata:
            table = table.replace_schema_metadata({**(table.schema.metadata or {}), **metadata})
        tmp, name = _stage_part(pdir, table)
        os.replace(tmp, pdir / name)
        _fsync_dir(pdir)

    @staticmethod
    def _read_partition(pdir: Path) -> pd.DataFrame | None:
        gone = compacted_away([pdir / MANIFEST])
        files = [f for f in sorted(pdir.glob("*.parquet")) if f not in gone]
        if not files:
            return None
        return pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)

    # ------------------------------------------------------------------- read
    def read(
        self,
        table: str,
        *,
        source: str | None = None,
        symbol: str | None = "XAUUSD",
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
        columns: Iterable[str] | Mapping[str, str | None] | None = None,
        folder_only: bool = False,
    ) -> pd.DataFrame:
        """Rows of `table`, one `symbol` (default XAUUSD) or, with `symbol=None`, every symbol of the `source`: a broker
        whose gold carries a suffix (Vantage writes XAUUSD+) is read by its source alone (backlog 141).

        Rows come back ordered by ts_utc, then the table's key columns (KEY_COLUMNS), then every column (`_order_by`,
        backlog 162): rows sharing a ts_utc read in one order, run after run, on either duckdb major.

        `columns` as a mapping (name -> DuckDB type, None to keep the column's own) casts each column explicitly.
        `folder_only=True` scans only the source=/symbol= folder's month partitions from `start` to `end` (source,
        symbol and a `columns` mapping required): the read no longer lists and opens every part file of the table
        (the health pass's decisions-journal read, 2026-11-10 round 3: 9,447 parts of every account). union_by_name
        then sees that folder's files only, so a column all-null there would read as DuckDB's NULL/INTEGER rather
        than the type another source gives it: the explicit casts give the same values and dtypes as the whole-table
        read. A cast column no file of the folder has falls back to the whole-table read (the result as before)."""
        tdir = self.table_dir(table)
        if not tdir.exists() or not any(tdir.rglob("*.parquet")):
            return pd.DataFrame()
        if folder_only and (source is None or symbol is None or not isinstance(columns, Mapping)):
            raise ValueError("folder_only needs source, symbol and a columns mapping (name -> type)")
        con = _connect()
        try:
            # backlog 170: `gone` holds the parts a compaction already replaced (a crash before its unlinks), never read
            # twice; found under the shared locks, so empty unless a compaction died mid-way
            with self._read_locks(tdir, source, symbol, start, end, self.lock_timeout_s) as gone:
                files: list[str] | None = None
                if folder_only:     # listed under the partitions' shared locks, as DuckDB's own glob is
                    files = _folder_files(tdir / f"source={source}" / f"symbol={symbol}", start, end)
                    if not files:
                        return pd.DataFrame()
                sql, params = _read_query(self, table, source=source, symbol=symbol, start=start, end=end,
                                          columns=columns, files=_live(files, tdir, gone))
                try:
                    df = _fetch_listed(con, sql, params)
                except duckdb.BinderException:
                    if files is None:
                        raise
                    # a cast column no file of the folder has: the whole-table read, as before folder_only
                    sql, params = _read_query(self, table, source=source, symbol=symbol, start=start, end=end,
                                              columns=columns, files=_live(None, tdir, gone))
                    df = _fetch_listed(con, sql, params)
        finally:
            con.close()
        if "ts_utc" in df.columns:
            df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
        return df

    def sql(self, query: str) -> pd.DataFrame:
        """Run DuckDB SQL; tables are exposed as views named after the table. Zoned timestamps come back tz-aware UTC
        (`_fetch`), whatever the duckdb major or the host's zone. Views exist for the tables the query names, read
        under the same shared partition locks as `read` (every partition of those tables, bounded wait), so an append
        or a compaction swap is seen whole or not at all."""
        con = _connect()
        try:
            with ExitStack() as locks:
                for t in sorted(TABLES):
                    tdir = self.table_dir(t)
                    if not re.search(rf"\b{t}\b", query) or not tdir.exists() or not any(tdir.rglob("*.parquet")):
                        continue      # a view only for the tables the query names: only their partitions are locked
                    # shared locks as `read` takes them (bounded wait): a swap is never seen half done
                    gone = locks.enter_context(self._read_locks(tdir, None, None, timeout_s=self.lock_timeout_s))
                    target = _live(None, tdir, gone)
                    scan = _scan(target if target is not None else str(tdir / "**" / "*.parquet"), t)
                    con.execute(f"CREATE VIEW {t} AS SELECT * FROM {scan}")
                return _fetch(con, query, None)
        finally:
            con.close()



# ------------------------------------------------------------------ compaction support (backlog 170, data/compact.py)
MANIFEST = ".compact.json"      # one per partition folder (compactions run under its lock); not *.parquet


def compacted_away(manifests: Iterable[Path]) -> set[Path]:
    """The part files a compaction replaced but has not unlinked yet (a crash between its rename and its unlinks):
    every part a complete manifest names, when the manifest's new part is on disk. A manifest whose new part is not
    there (a crash before the rename) replaced nothing: the old parts are the partition. Empty in the normal case."""
    out: set[Path] = set()
    for m in manifests:
        try:
            doc = json.loads(m.read_text())
            new, replaces = str(doc["new"]), [str(r) for r in doc["replaces"]]
        except (OSError, ValueError, KeyError, TypeError):
            continue                          # written by rename: a torn manifest cannot exist; an odd one is ignored
        if (m.parent / new).exists():
            out.update(m.parent / r for r in replaces if r != new)
    return out


def _live(files: list[str] | None, tdir: Path, gone: set[Path]) -> list[str] | None:
    """`files` (globs; None: the whole table) unchanged when nothing is `gone`, else expanded to the part files
    minus the replaced ones."""
    if not gone:
        return files
    globs = files if files is not None else [str(tdir / "**" / "*.parquet")]
    return [f for g in globs for f in sorted(glob.glob(g, recursive=True)) if Path(f) not in gone]


def _fsync_dir(pdir: Path) -> None:
    dfd = os.open(pdir, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _stage_part(pdir: Path, table: pa.Table) -> tuple[Path, str]:
    """Write `table` to a temp name in `pdir` (not *.parquet: invisible to readers) and fsync it; returns the temp path
    and the part name it is to be renamed to."""
    name = f"part-{uuid.uuid4().hex[:8]}.parquet"
    tmp = pdir / f".{name}.tmp"
    pq.write_table(table, tmp, compression="zstd")
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return tmp, name

def _read_query(store: Store, table: str, *, source: str | None, symbol: str | None,
                start: str | pd.Timestamp | None, end: str | pd.Timestamp | None,
                columns: Iterable[str] | Mapping[str, str | None] | None,
                files: list[str] | None = None) -> tuple[str, list]:
    """The SQL and parameters of `Store.read` (`files`: the part-file globs to scan instead of the whole table).

    The ts bounds are bound as ISO strings, which duckdb casts to the column's own type: duckdb 2 reads a Parquet ns
    timestamp with a zone as TIMESTAMPTZ_NS, which does not compare with a bound TIMESTAMPTZ (BinderException), and
    `CAST(? AS TIMESTAMPTZ_NS)` does not exist in 1.x. Casting the column instead (`CAST(ts_utc AS TIMESTAMPTZ)`) binds
    on both but 2.x ROUNDS ns to us there (1.x truncates), so a tick a few hundred ns before a bound moved windows. A
    string bound is a constant: it stays a table filter in the Parquet scan on both majors, so row groups outside the
    window are skipped (tests/test_store_duckdb_compat.py), and the row set is the same on both (us bounds; 1.x compares
    its truncated values, 2.x the exact ns, which select the same rows)."""
    if not columns:
        cols = "*"
    elif isinstance(columns, Mapping):
        cols = ", ".join(_quote(c) if t is None else f"CAST({_quote(c)} AS {t}) AS {_quote(c)}"
                         for c, t in columns.items())
    else:
        cols = ", ".join(_quote(c) for c in columns)
    scan = _scan(files if files is not None else str(store.table_dir(table) / "**" / "*.parquet"), table)
    where: list[str] = []
    params: list = []
    if symbol is not None:
        where.append("symbol = ?")
        params.append(symbol)
    if source:
        where.append("source = ?")
        params.append(source)
    if start is not None:
        where.append("ts_utc >= ?")
        params.append(_bound(start))
        # rows sit in the UTC year/month partition of their ts_utc: a filter on the partition values lets DuckDB
        # skip every other month's files without opening them (same rows; the column types still come from the
        # whole table, as before)
        where.append("year * 12 + CAST(month AS BIGINT) >= ?")
        params.append(_utc(start).year * 12 + _utc(start).month)
    if end is not None:
        where.append("ts_utc < ?")
        params.append(_bound(end))
        where.append("year * 12 + CAST(month AS BIGINT) <= ?")
        params.append(_utc(end).year * 12 + _utc(end).month)
    sql = f"SELECT {cols} FROM {scan} {'WHERE ' + ' AND '.join(where) if where else ''} {_order_by(table)}"
    return sql, params


def _quote(name: str) -> str:
    """A column name as a DuckDB identifier (`check` is a keyword)."""
    return '"' + name.replace('"', '""') + '"'


def _order_by(table: str) -> str:
    """Backlog 162: ts_utc, then the table's key columns, then every column of the scan, left to right (`COLUMNS(*)`
    expands over the scanned columns, hive partition columns included, whatever the select list; year and month are
    left out, they follow from ts_utc and cost sort width). A content order, not a file order (part names are random),
    so two stores holding the same rows read them alike. A total order, so
    rows sharing a ts_utc come back in one order on every read; DuckDB's parallel scan otherwise returns ties in the
    order its threads finish. The key columns are matched by a regex that always matches ts_utc, so a key column
    absent from every file (tv_indicators' `origin` before chart exports) is skipped instead of failing to bind.
    Known effect (quant Q-C2): ticks sharing a ts_utc read sorted by bid then ask, not in arrival order, so a minute
    built from them (the D12 engine minute, crossfeed.reconcile_account) opens at the lowest and closes at the
    highest of the tied bids: at most one tick, data-quality grading only (the engine's live bars are built in
    memory from arrival order, never from this read)."""
    keys = [k for k in KEY_COLUMNS.get(table, []) if k != "ts_utc"]
    tie = f"COLUMNS('^({'|'.join(['ts_utc', *(re.escape(k) for k in keys)])})$'), " if keys else ""
    return f"ORDER BY ts_utc, {tie}COLUMNS(* EXCLUDE (year, month))"     # both follow from ts_utc


def _folder_files(folder: Path, start: str | pd.Timestamp | None, end: str | pd.Timestamp | None) -> list[str]:
    """The part-file globs of `folder`'s year=/month= partitions that can hold rows in [start, end), in order."""
    lo = _utc(start).year * 12 + _utc(start).month if start is not None else None
    hi = _utc(end).year * 12 + _utc(end).month if end is not None else None
    out = []
    for mdir in sorted(folder.glob("year=*/month=*")):
        try:
            ym = int(mdir.parent.name.split("=", 1)[1]) * 12 + int(mdir.name.split("=", 1)[1])
        except ValueError:
            continue
        if (lo is not None and ym < lo) or (hi is not None and ym > hi) or not any(mdir.glob("*.parquet")):
            continue
        out.append(str(mdir / "*.parquet"))
    return out


def _bound(x: str | pd.Timestamp) -> str:
    """A ts bound as an ISO string at microsecond resolution (as the python datetime bound was before backlog 156)."""
    return _utc(x).floor("us").to_pydatetime().isoformat(sep=" ")    # floor first: no pandas nanosecond warning


_TZ_TYPES = {"TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ", "TIMESTAMPTZ_NS"}


def _connect() -> duckdb.DuckDBPyConnection:
    """A fresh in-memory connection with the session zone set to UTC: duckdb hands TIMESTAMPTZ back in the session
    zone, which otherwise is the host's (Europe/London on the Mac, whatever Windows is set to on the VPS)."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    return con


_VANISHED_RETRIES = 3


def _fetch_listed(con: duckdb.DuckDBPyConnection, sql: str, params: list | None) -> pd.DataFrame:
    """`_fetch` of a `Store.read` scan, run again when a listed part vanished before DuckDB opened it (IOException).
    A read locks only the months of its window, but its scan lists the whole table (the schema union, so the frame is
    the same as before backlog 170): a compaction swap or a dedupe rewrite of another, unlocked month can unlink a
    part between DuckDB's listing and its open. Parts appear only by atomic rename and are never rewritten in place, so
    a vanished file is the only race; a re-run lists again (a swap holds its lock for milliseconds). Other errors (a
    corrupt part: InvalidInputException) and the last attempt's IOException are raised."""
    for attempt in range(_VANISHED_RETRIES):
        try:
            return _fetch(con, sql, params)
        except duckdb.IOException:
            if attempt == _VANISHED_RETRIES - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def _fetch(con: duckdb.DuckDBPyConnection, sql: str, params: list | None) -> pd.DataFrame:
    """Run `sql`; every zoned timestamp column comes back tz-aware UTC at microsecond resolution on duckdb 1.x and 2.x
    alike (backlog 156). 1.x reads a Parquet ns timestamp with a zone as TIMESTAMPTZ (us, truncating ns); 2.x keeps
    TIMESTAMPTZ_NS and `.df()` returns it naive in UTC wall time. Naive TIMESTAMP columns are left as they are.
    Columns are converted by position, so a join returning two `ts_utc` columns is covered too."""
    cur = con.execute(sql, params) if params else con.execute(sql)
    kinds = [str(d[1]).upper() for d in cur.description] if cur.description else []
    df = cur.df()
    for i, kind in enumerate(kinds):
        if kind in _TZ_TYPES and i < df.shape[1]:
            col = pd.to_datetime(df.iloc[:, i], utc=True)      # naive (2.x) is UTC wall time; aware is converted
            if col.dt.unit != "us":
                col = col.dt.floor("us").dt.as_unit("us")        # 1.x's resolution: it truncates ns on read
            df.isetitem(i, col.array)
    return df


def _scan(glob: str | list[str], table: str) -> str:
    """The DuckDB scan of a table's files, always by column name (union_by_name). Without it DuckDB takes the schema
    from the first file: an early part with an all-null column (typed NULL) made later string parts fail to cast, and
    `tv_indicators` mixes live-alert and chart-export files with extra columns (origin, repaint_validated, ...) where
    an older file must read as NULL. A list of globs scans only those (`Store.read(folder_only=True)`)."""
    def lit(g: str) -> str:
        return "'" + g.replace("'", "''") + "'"     # a path with a quote stays one SQL string literal
    src = lit(glob) if isinstance(glob, str) else "[" + ", ".join(lit(g) for g in glob) + "]"
    return f"read_parquet({src}, hive_partitioning=true, union_by_name=true)"


def asof_join(bars: pd.DataFrame, other: pd.DataFrame, *, on: str = "ts_utc", available_col: str = "available_utc",
              prefix: str = "", tolerance: pd.Timedelta | None = None) -> pd.DataFrame:
    """Join `other` onto `bars` using only rows whose `available_utc` <= bar time.

    This is the single mechanism through which macro, news, calendar and TradingView data reach
    features. It is deliberately impossible to join on the nominal/value date here.
    """
    if available_col not in other.columns:
        raise ValueError(f"asof_join requires {available_col!r}; never join on nominal dates")
    left = bars.sort_values(on).copy()
    right = other.sort_values(available_col).copy()
    # one timestamp unit on both keys (pandas 3 keeps s/ms/us/ns per source and merge_asof rejects a mismatch)
    left[on] = pd.DatetimeIndex(pd.to_datetime(left[on], utc=True)).as_unit("ns")
    right[available_col] = pd.DatetimeIndex(pd.to_datetime(right[available_col], utc=True)).as_unit("ns")
    if prefix:
        right = right.rename(columns={c: f"{prefix}{c}" for c in right.columns if c not in (available_col,)})
    out = pd.merge_asof(
        left, right, left_on=on, right_on=available_col, direction="backward",
        allow_exact_matches=True, tolerance=tolerance,
        suffixes=("", "_other"),     # the bars keep their own columns (every store table also carries ts_utc)
    )
    return out.drop(columns=[available_col])
