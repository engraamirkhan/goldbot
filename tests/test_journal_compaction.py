"""Backlog 170: the decisions journal's small part files are compacted off the hot path (goldbot/data/compact.py).

Every closed month partition of every source/symbol folder is rewritten into one part under the partition's writer
lock: every row kept (no dedupe: identical journal rows are legitimate), the same `Store.read` frame before and after,
the open month never touched, and a crash at any step leaves every row readable exactly once (manifest)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
import pytest

from goldbot.data import compact as compact_mod
from goldbot.data.compact import compact_table
from goldbot.data.store import Store

NOW = pd.Timestamp("2026-11-12 03:40", tz="UTC")
ACC = "icm-demo"


def _flush(store: Store, ts: str, n: int, *, source: str = ACC, extra: dict[str, Any] | None = None) -> None:
    """One engine flush (runner.flush_journal): n rows, one new part file."""
    t0 = pd.Timestamp(ts, tz="UTC")
    df = pd.DataFrame({"ts_utc": [t0 + pd.Timedelta(seconds=i) for i in range(n)], "account_id": source,
                       "agent_id": [f"a{i % 3}" for i in range(n)], "action": "score", "p": [0.5] * n,
                       "mult": None, "proposal_id": None, "detail": "{}"})
    for k, v in (extra or {}).items():
        df[k] = v
    store.append("decisions", df, source=source, symbol="XAUUSD", dedupe=False)


def _seed(store: Store) -> None:
    for d in range(1, 6):                                   # September: 5 flushes, one repeated verbatim
        _flush(store, f"2026-09-0{d} 10:00", 4)
    _flush(store, "2026-09-05 10:00", 4)                    # identical rows to the flush before: kept, both copies
    _flush(store, "2026-10-01 00:00", 3, extra={"note": "x"})   # a column only October's later parts carry
    _flush(store, "2026-10-02 00:00", 3)
    _flush(store, "2026-11-01 00:00", 2)                    # the open month: never touched
    _flush(store, "2026-11-02 00:00", 2)
    _flush(store, "2026-09-03 00:00", 2, source="vantage-demo")
    _flush(store, "2026-09-04 00:00", 2, source="vantage-demo")


def _parts(store: Store, source: str, year: int, month: int) -> list[Path]:
    return sorted((store.table_dir("decisions") / f"source={source}" / "symbol=XAUUSD" / f"year={year}"
                   / f"month={month:02d}").glob("*.parquet"))


def _read(store: Store, source: str | None = ACC) -> pd.DataFrame:
    return store.read("decisions", source=source, symbol="XAUUSD")


def test_closed_months_become_one_part_with_every_row_in_write_order(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    before, before_all = _read(store), _read(store, None)
    open_month = _parts(store, ACC, 2026, 11)
    rep = compact_table(store, "decisions", before=NOW)
    assert [len(_parts(store, ACC, 2026, m)) for m in (9, 10)] == [1, 1]
    assert len(_parts(store, "vantage-demo", 2026, 9)) == 1
    assert _parts(store, ACC, 2026, 11) == open_month          # the partition being written is left alone
    pd.testing.assert_frame_equal(_read(store), before)
    pd.testing.assert_frame_equal(_read(store, None), before_all)
    assert len(before[before["ts_utc"].dt.month == 9]) == 24   # the verbatim repeat is kept (no dedupe)
    done = {(p.source, p.year, p.month): p for p in rep.partitions}
    sep = done[(ACC, 2026, 9)]
    assert (sep.parts_before, sep.rows, sep.error) == (6, 24, None)
    assert sep.hash_before == sep.hash_after
    assert (ACC, 2026, 11) not in done
    # backlog 179: the part on disk holds the parts in write order (lineage); reads still come in store order
    raw = pq.read_table(_parts(store, ACC, 2026, 9)[0]).to_pandas()
    assert len(raw) == 24
    assert str(raw["ts_utc"].dt.tz) == "UTC"
    assert not list((store.table_dir("decisions")).rglob(".compact*"))


def test_rerun_is_a_no_op_and_late_rows_are_folded_in_next_night(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    compact_table(store, "decisions", before=NOW)
    first = _parts(store, ACC, 2026, 9)
    rep = compact_table(store, "decisions", before=NOW)
    assert rep.partitions == [] and _parts(store, ACC, 2026, 9) == first
    _flush(store, "2026-09-30 23:59", 1)                    # a late flush of last month's rows
    n = len(_read(store))
    compact_table(store, "decisions", before=NOW)
    assert len(_parts(store, ACC, 2026, 9)) == 1 and len(_read(store)) == n


def test_nanoseconds_are_kept_on_disk(tmp_path: Path) -> None:
    store = Store(tmp_path)
    for i in range(2):
        df = pd.DataFrame({"ts_utc": [pd.Timestamp("2026-09-01 00:00:00.000000123", tz="UTC") + pd.Timedelta(i)],
                           "action": "x"})
        store.append("decisions", df, source=ACC, dedupe=False)
    compact_table(store, "decisions", before=NOW)
    raw = pq.read_table(_parts(store, ACC, 2026, 9)[0]).column("ts_utc").to_pandas()
    assert [t.nanosecond for t in raw] == [123, 124]


def test_crash_after_rename_before_unlinks_reads_every_row_once(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(tmp_path)
    _seed(store)
    before, before_all = _read(store), _read(store, None)

    def boom(*_a: Any, **_k: Any) -> None:
        raise SystemExit("killed between the rename and the unlinks")
    monkeypatch.setattr(compact_mod, "_unlink_replaced", boom)
    with pytest.raises(SystemExit):
        compact_table(store, "decisions", before=NOW)
    pdir = _parts(store, ACC, 2026, 9)[0].parent
    assert len(_parts(store, ACC, 2026, 9)) == 7                # 6 old parts + the new one on disk, both
    assert len(list(pdir.glob(".compact.json"))) == 1
    pd.testing.assert_frame_equal(_read(store), before)        # the manifest hides the replaced parts
    pd.testing.assert_frame_equal(_read(store, None), before_all)
    part = Store._read_partition(pdir)                         # the dedupe-append read path too
    assert part is not None and len(part) == 24
    sql = store.sql("SELECT count(*) AS n FROM decisions WHERE source = 'icm-demo'")
    assert int(sql["n"].iloc[0]) == len(before)
    monkeypatch.undo()
    compact_table(store, "decisions", before=NOW)               # the next run finishes the unlinks
    assert len(_parts(store, ACC, 2026, 9)) == 1 and not list(pdir.glob(".compact*"))
    pd.testing.assert_frame_equal(_read(store), before)


def test_crash_before_rename_leaves_old_parts_and_rerun_compacts(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(tmp_path)
    _seed(store)
    before = _read(store)

    def boom(*_a: Any, **_k: Any) -> None:
        raise SystemExit("killed after the manifest, before the rename")
    monkeypatch.setattr(compact_mod, "_rename_new_part", boom)
    with pytest.raises(SystemExit):
        compact_table(store, "decisions", before=NOW)
    pdir = _parts(store, ACC, 2026, 9)[0].parent
    assert len(_parts(store, ACC, 2026, 9)) == 6
    manifest = json.loads(next(pdir.glob(".compact.json")).read_text())
    assert manifest["rows"] == 24 and len(manifest["replaces"]) == 6
    pd.testing.assert_frame_equal(_read(store), before)        # new part not renamed: the manifest is ignored
    monkeypatch.undo()
    compact_table(store, "decisions", before=NOW)
    assert len(_parts(store, ACC, 2026, 9)) == 1 and not list(pdir.glob(".compact*")) \
        and not list(pdir.glob(".*.tmp"))
    pd.testing.assert_frame_equal(_read(store), before)


def test_a_mismatch_keeps_the_old_parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(tmp_path)
    _seed(store)
    before = _read(store)
    real = compact_mod._build

    def drop_one(pdir: Path, files: list[Path], row_group_rows: int) -> Any:
        tmp, name = real(pdir, files, row_group_rows)
        t = pq.read_table(tmp)
        pq.write_table(t.slice(0, t.num_rows - 1), tmp)
        return tmp, name
    monkeypatch.setattr(compact_mod, "_build", drop_one)
    rep = compact_table(store, "decisions", before=NOW)
    assert all(p.error for p in rep.partitions)
    assert len(_parts(store, ACC, 2026, 9)) == 6 and not list(store.table_dir("decisions").rglob(".*.tmp"))
    pd.testing.assert_frame_equal(_read(store), before)


def test_health_journal_cache_sees_the_new_part_set(tmp_path: Path) -> None:
    from goldbot.ops.checks.engine import _journal_parts
    store = Store(tmp_path)
    _seed(store)
    acc = SimpleNamespace(account_id=ACC, symbol="XAUUSD")
    since = pd.Timestamp("2026-09-01", tz="UTC")
    old = _journal_parts(tmp_path, acc, since)  # type: ignore[arg-type]
    compact_table(store, "decisions", before=NOW)
    new = _journal_parts(tmp_path, acc, since)  # type: ignore[arg-type]
    assert old is not None and new is not None and old != new and len(new) == 4   # Sep 1 + Oct 1 + Nov 2


def test_journal_compact_job_is_scheduled_and_reports(tmp_path: Path) -> None:
    from goldbot.config import load_settings
    from goldbot.ops.jobs import JOBS, journal_compact
    assert JOBS["journal_compact"] is journal_compact
    s = load_settings().scheduler.journal_compact
    assert s.kind == "daily"
    store = Store(tmp_path)
    _seed(store)
    out = journal_compact(SimpleNamespace(store=store), NOW)  # type: ignore[arg-type]
    assert out["partitions"] == 3 and out["parts_before"] == 10 and out["parts_after"] == 3
    assert out["rows"] == 24 + 6 + 4


# ---- review of 9997629f (code + SRE)
posix = pytest.mark.skipif(sys.platform == "win32", reason="flock semantics")


def _locked_dirs(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    import goldbot.data.store as store_mod
    seen: list[Path] = []
    real = store_mod._lock

    def spy(fh: Any, *, shared: bool, deadline: float | None = None) -> None:
        seen.append(Path(fh.name).parent)
        real(fh, shared=shared, deadline=deadline)
    monkeypatch.setattr(store_mod, "_lock", spy)
    return seen


def test_a_read_locks_only_its_own_folder_and_months(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 1/3: a one-symbol read locks (and checks for a manifest) only its own source/symbol folder and the
    months its window can hold, not every month folder of the table."""
    store = Store(tmp_path)
    _seed(store)
    seen = _locked_dirs(monkeypatch)
    store.read("decisions", source=ACC, symbol="XAUUSD", start="2026-10-15")
    rel = sorted({p.relative_to(store.table_dir("decisions")).as_posix() for p in seen})
    assert rel == [f"source={ACC}/symbol=XAUUSD/year=2026/month=10", f"source={ACC}/symbol=XAUUSD/year=2026/month=11"]


@posix
def test_a_reader_behind_a_stuck_writer_fails_loudly(tmp_path: Path) -> None:
    """Review 3: a shared lock is waited for a bounded time, then StoreLockTimeout names the partition."""
    from goldbot.data.store import StoreLockTimeout
    store = Store(tmp_path, lock_timeout_s=0.2)
    _seed(store)
    pdir = _parts(store, ACC, 2026, 11)[0].parent
    with Store._partition_lock(pdir):
        t0 = time.monotonic()
        with pytest.raises(StoreLockTimeout, match="month=11"):
            store.read("decisions", source=ACC, symbol="XAUUSD")
        with pytest.raises(StoreLockTimeout):
            store.sql("SELECT count(*) FROM decisions")            # review 2: sql takes the read locks too
        assert time.monotonic() - t0 < 5
        store.read("decisions", source=ACC, symbol="XAUUSD", start="2026-09-01", end="2026-10-31")   # other months


@posix
def test_the_part_is_built_outside_the_writer_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 3: the read, merge and verify run without the exclusive lock (an engine flush is not held up)."""
    import fcntl
    store = Store(tmp_path)
    _seed(store)
    real = compact_mod._store_hash
    free: list[bool] = []

    def during_build(files: list[Path], table: str, **kw: Any) -> tuple[str, int]:
        with open(files[0].parent / ".lock", "ab") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                free.append(True)
                fcntl.flock(fh, fcntl.LOCK_UN)
            except BlockingIOError:
                free.append(False)
        return real(files, table, **kw)
    monkeypatch.setattr(compact_mod, "_store_hash", during_build)
    compact_table(store, "decisions", before=NOW)
    assert free and all(free)


def test_a_partition_changed_during_the_build_is_left_for_the_next_night(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """A part listed for the build vanished before the swap (a dedupe rewrite): nothing is replaced; a part appended
    meanwhile is never in the replaced set either."""
    store = Store(tmp_path)
    _seed(store)
    real = compact_mod._build

    def rewritten(pdir: Path, files: list[Path], row_group_rows: int) -> Any:
        t = real(pdir, files, row_group_rows)
        if files[0].parent.name == "month=09" and f"source={ACC}" in str(files[0]):
            gone = pq.read_table(files[0]).to_pandas()
            files[0].unlink()                                        # the rows move to a new part meanwhile
            store.append("decisions", gone, source=ACC, dedupe=False)
        return t
    monkeypatch.setattr(compact_mod, "_build", rewritten)
    before = _read(store)
    rep = compact_table(store, "decisions", before=NOW)
    sep = next(p for p in rep.partitions if (p.source, p.month) == (ACC, 9))
    assert sep.skipped and sep.error is None
    assert len(_parts(store, ACC, 2026, 9)) == 6 and not list(store.table_dir("decisions").rglob(".*.tmp"))
    pd.testing.assert_frame_equal(_read(store), before)


def test_stale_part_temp_files_are_swept_and_the_job_reports_its_duration(tmp_path: Path) -> None:
    from goldbot.ops.jobs import journal_compact
    store = Store(tmp_path)
    _seed(store)
    pdir = _parts(store, ACC, 2026, 9)[0].parent
    (pdir / ".part-deadbeef.parquet.tmp").write_bytes(b"torn")       # an append that died before its rename
    out = journal_compact(SimpleNamespace(store=store), NOW)  # type: ignore[arg-type]
    assert not (pdir / ".part-deadbeef.parquet.tmp").exists()
    assert isinstance(out["duration_s"], float) and out["duration_s"] >= 0


def test_backup_never_stages_parts_a_manifest_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 4: a crash between the rename and the unlinks; the backup copies the new part and not the old ones,
    so a restore (by any code) reads every row once."""
    from goldbot.ops.backup import _stage_partition
    store = Store(tmp_path / "data")
    _seed(store)

    def boom(*_a: Any, **_k: Any) -> None:
        raise SystemExit("killed between the rename and the unlinks")
    monkeypatch.setattr(compact_mod, "_unlink_replaced", boom)
    with pytest.raises(SystemExit):
        compact_table(store, "decisions", before=NOW)
    pdir = _parts(store, ACC, 2026, 9)[0].parent
    dest = tmp_path / "stage"
    _torn, why = _stage_partition(pdir, dest, tmp_path / "data")
    assert why is None
    staged = list((dest / "data" / pdir.relative_to(tmp_path / "data")).glob("*.parquet"))
    assert len(staged) == 1 and pq.read_metadata(staged[0]).num_rows == 24


def test_a_part_swapped_away_in_an_unlocked_month_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of c767e94a (LOW): a windowed read still scans the whole table's glob (its schema union, so the frame is
    the same as before: tests/test_health_journal_folder_read.py), but locks only the window's months. A compaction
    swap of an older month can unlink a part DuckDB listed before it opens it: DuckDB raises IOException, and the read
    re-lists and runs again (a swap is milliseconds) instead of failing. A corrupt part is still a loud error."""
    import duckdb

    import goldbot.data.store as store_mod
    store = Store(tmp_path)
    _seed(store)
    want = store.read("decisions", source=ACC, symbol="XAUUSD", start="2026-10-15")
    real = store_mod._fetch
    calls: list[int] = []

    def vanished_once(con: Any, sql: str, params: Any) -> pd.DataFrame:
        calls.append(1)
        if len(calls) == 1:
            raise duckdb.IOException("IO Error: No files found that match the pattern / cannot open file part-x")
        return real(con, sql, params)
    monkeypatch.setattr(store_mod, "_fetch", vanished_once)
    pd.testing.assert_frame_equal(store.read("decisions", source=ACC, symbol="XAUUSD", start="2026-10-15"), want)
    assert len(calls) == 2
    monkeypatch.undo()
    (_parts(store, ACC, 2026, 9)[0].parent / "part-garbage0.parquet").write_bytes(b"not parquet")
    with pytest.raises(duckdb.Error):
        store.read("decisions", source=ACC, symbol="XAUUSD", start="2026-10-15")
