"""Backlog 170: the decisions journal's small part files are compacted off the hot path (goldbot/data/compact.py).

Every closed month partition of every source/symbol folder is rewritten into one part under the partition's writer
lock: every row kept (no dedupe: identical journal rows are legitimate), the same `Store.read` frame before and after,
the open month never touched, and a crash at any step leaves every row readable exactly once (manifest)."""
from __future__ import annotations

import json
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


def test_closed_months_become_one_part_with_every_row_in_store_order(tmp_path: Path) -> None:
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
    # the part on disk is in store order: ts_utc, then every column (decisions has no key columns)
    raw = pq.read_table(_parts(store, ACC, 2026, 9)[0]).to_pandas()
    assert raw["ts_utc"].is_monotonic_increasing
    assert str(raw["ts_utc"].dt.tz) == "UTC"
    assert not list((store.table_dir("decisions")).rglob(".compact-*"))


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
    assert len(list(pdir.glob(".compact-*.json"))) == 1
    pd.testing.assert_frame_equal(_read(store), before)        # the manifest hides the replaced parts
    pd.testing.assert_frame_equal(_read(store, None), before_all)
    part = Store._read_partition(pdir)                         # the dedupe-append read path too
    assert part is not None and len(part) == 24
    sql = store.sql("SELECT count(*) AS n FROM decisions WHERE source = 'icm-demo'")
    assert int(sql["n"].iloc[0]) == len(before)
    monkeypatch.undo()
    compact_table(store, "decisions", before=NOW)               # the next run finishes the unlinks
    assert len(_parts(store, ACC, 2026, 9)) == 1 and not list(pdir.glob(".compact-*"))
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
    manifest = json.loads(next(pdir.glob(".compact-*.json")).read_text())
    assert manifest["rows"] == 24 and len(manifest["replaces"]) == 6
    pd.testing.assert_frame_equal(_read(store), before)        # new part not renamed: the manifest is ignored
    monkeypatch.undo()
    compact_table(store, "decisions", before=NOW)
    assert len(_parts(store, ACC, 2026, 9)) == 1 and not list(pdir.glob(".compact-*")) \
        and not list(pdir.glob(".*.tmp"))
    pd.testing.assert_frame_equal(_read(store), before)


def test_a_mismatch_keeps_the_old_parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(tmp_path)
    _seed(store)
    before = _read(store)
    real = compact_mod._merged

    def drop_one(files: list[Path], table: str) -> Any:
        t = real(files, table)
        return t.slice(0, t.num_rows - 1)
    monkeypatch.setattr(compact_mod, "_merged", drop_one)
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
