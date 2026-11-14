"""Backlog 179 (R146): the other append-only store tables (ticks, observe_ticks, fills, dq_events) are compacted like
the decisions journal (backlog 170): closed months only, every row kept (no dedupe: fills keep every row, duplicate
ticks of two flushes stay), the same `Store.read` frame before and after, memory bounded (streamed by row group), the
write time of every row kept as lineage (a tick's part mtime was the only evidence of the 2026-10-26 tz quarantine),
and the nightly job time-boxed and paced."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from goldbot.data import compact as compact_mod
from goldbot.data.compact import compact_table, written_utc
from goldbot.data.store import Store

NOW = pd.Timestamp("2026-11-13 03:40", tz="UTC")
ACC = "icm-demo"


def _ticks(t0: str, n: int, *, step_ms: int = 250, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.Timestamp(t0, tz="UTC") + pd.to_timedelta(np.arange(n) * step_ms, unit="ms")
    bid = 2650 + rng.normal(0, 0.2, n).round(2)
    return pd.DataFrame({"ts_utc": ts, "bid": bid, "ask": bid + 0.12})


def _fill(t: str, coid: str, **kw: Any) -> pd.DataFrame:
    row = {"ts_utc": pd.Timestamp(t, tz="UTC"), "client_order_id": coid, "agent_id": "box", "side": "buy",
           "lots": 0.01, "requested": 2650.0, "filled": 2650.1, "order_type": "market", "retcode": 10009,
           "commission": np.nan}
    row.update(kw)
    return pd.DataFrame([row])


def _dq(t: str, check: str, n: int = 2) -> pd.DataFrame:
    t0 = pd.Timestamp(t, tz="UTC")
    return pd.DataFrame({"ts_utc": [t0 + pd.Timedelta(minutes=i) for i in range(n)], "check": check,
                         "severity": "warning", "detail": [f"{check} {i}" for i in range(n)]})


def _seed(store: Store) -> None:
    for d in range(1, 5):                                     # ticks: four flushes in September, one in the open month
        store.append("ticks", _ticks(f"2026-09-0{d} 08:00", 40, seed=d), source=ACC, dedupe=False)
    store.append("ticks", _ticks("2026-09-04 08:00", 40, seed=4), source=ACC, dedupe=False)   # a re-flushed batch
    store.append("ticks", _ticks("2026-10-01 08:00", 10), source=ACC, dedupe=False)
    store.append("ticks", _ticks("2026-10-02 08:00", 10), source=ACC, dedupe=False)
    store.append("ticks", _ticks("2026-11-02 08:00", 10), source=ACC, dedupe=False)
    store.append("ticks", _ticks("2026-11-03 08:00", 10), source=ACC, dedupe=False)
    store.append("observe_ticks", _ticks("2026-09-05 21:00", 20), source="icm-observe", symbol="XAUUSD247",
                 dedupe=False)
    store.append("observe_ticks", _ticks("2026-09-06 21:00", 20), source="icm-observe", symbol="XAUUSD247",
                 dedupe=False)
    # fills: two rows with the same key (ts_utc, client_order_id) in two parts: both are kept (no dedupe)
    store.append("fills", _fill("2026-09-10 10:00", "p1"), source=ACC, dedupe=False)
    store.append("fills", _fill("2026-09-10 10:00", "p1", filled=2650.2), source=ACC, dedupe=False)
    store.append("fills", _fill("2026-09-11 10:00", "p2", commission=-0.07), source=ACC, dedupe=False)
    store.append("fills", _fill("2026-11-01 10:00", "p3"), source=ACC, dedupe=False)
    store.append("dq_events", _dq("2026-09-01 00:00", "gap"), source=ACC, dedupe=False)
    store.append("dq_events", _dq("2026-09-02 00:00", "spike"), source=ACC, dedupe=False)
    store.append("dq_events", _dq("2026-09-02 00:00", "spike").assign(table="positioning"), source="positioning",
                 dedupe=False)
    store.append("dq_events", _dq("2026-09-03 00:00", "spike").assign(table="positioning"), source="positioning",
                 dedupe=False)


def _parts(store: Store, table: str, source: str, symbol: str, year: int, month: int) -> list[Path]:
    return sorted((store.table_dir(table) / f"source={source}" / f"symbol={symbol}" / f"year={year}"
                   / f"month={month:02d}").glob("*.parquet"))


CASES = [("ticks", ACC, "XAUUSD", 9, 5, 200), ("observe_ticks", "icm-observe", "XAUUSD247", 9, 2, 40),
         ("fills", ACC, "XAUUSD", 9, 3, 3), ("dq_events", ACC, "XAUUSD", 9, 2, 4),
         ("dq_events", "positioning", "XAUUSD", 9, 2, 4)]


@pytest.mark.parametrize(("table", "source", "symbol", "month", "parts", "rows"), CASES)
def test_each_table_reads_the_same_frame_after_compaction(tmp_path: Path, table: str, source: str, symbol: str,
                                                          month: int, parts: int, rows: int) -> None:
    store = Store(tmp_path)
    _seed(store)
    before = store.read(table, source=source, symbol=symbol)
    before_all = store.read(table, symbol=None)
    open_parts = _parts(store, table, source, symbol, 2026, 11)
    assert len(_parts(store, table, source, symbol, 2026, month)) == parts
    rep = compact_table(store, table, before=NOW)
    assert len(_parts(store, table, source, symbol, 2026, month)) == 1
    assert _parts(store, table, source, symbol, 2026, 11) == open_parts       # the open month is never touched
    pd.testing.assert_frame_equal(store.read(table, source=source, symbol=symbol), before)
    pd.testing.assert_frame_equal(store.read(table, symbol=None), before_all)
    res = next(p for p in rep.partitions if (p.source, p.symbol, p.month) == (source, symbol, month))
    assert (res.parts_before, res.parts_after, res.rows, res.error) == (parts, 1, rows, None)
    assert res.hash_before == res.hash_after
    assert all(p.month != 11 for p in rep.partitions)


def test_fills_keep_every_row_even_on_a_repeated_key(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    compact_table(store, "fills", before=NOW)
    f = store.read("fills", source=ACC)
    sep = f[f["ts_utc"].dt.month == 9]
    assert len(sep) == 3 and sorted(sep.loc[sep["client_order_id"] == "p1", "filled"]) == [2650.1, 2650.2]


def test_duplicate_ticks_of_two_flushes_are_both_kept(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    compact_table(store, "ticks", before=NOW)
    t = store.read("ticks", source=ACC)
    sep4 = t[t["ts_utc"].dt.day.eq(4) & t["ts_utc"].dt.month.eq(9)]
    assert len(sep4) == 80 and sep4.duplicated(["ts_utc", "bid", "ask"]).sum() == 40


def test_every_row_keeps_the_write_time_of_its_part(tmp_path: Path) -> None:
    """A tick carries no write time of its own: its part's mtime is the evidence (the 2026-10-26 quarantine picked
    rows stamped ahead of it). The compacted part keeps it per row (Parquet metadata, rows in part-write order), and
    a second compaction (late rows folded in) keeps the first one's lineage."""
    store = Store(tmp_path)
    _seed(store)
    parts = _parts(store, "ticks", ACC, "XAUUSD", 2026, 9)
    want: list[pd.Timestamp] = []
    for i, p in enumerate(sorted(parts, key=lambda p: p.stat().st_mtime_ns)):
        os.utime(p, ns=(p.stat().st_atime_ns, 1_788_000_000_000_000_000 + i * 1_000_000_000))
    for p in sorted(parts, key=lambda p: p.stat().st_mtime_ns):
        want += [pd.Timestamp(p.stat().st_mtime_ns, unit="ns", tz="UTC")] * pq.read_metadata(p).num_rows
    compact_table(store, "ticks", before=NOW)
    (new,) = _parts(store, "ticks", ACC, "XAUUSD", 2026, 9)
    assert list(written_utc(new)) == want
    store.append("ticks", _ticks("2026-09-30 23:59", 5), source=ACC, dedupe=False)     # a late flush
    late = next(p for p in _parts(store, "ticks", ACC, "XAUUSD", 2026, 9) if p != new)
    late_at = pd.Timestamp(late.stat().st_mtime_ns, unit="ns", tz="UTC")
    compact_table(store, "ticks", before=NOW)
    (again,) = _parts(store, "ticks", ACC, "XAUUSD", 2026, 9)
    assert list(written_utc(again)) == want + [late_at] * 5
    raw = pq.read_table(again).to_pandas()                   # rows on disk in part-write order, every row
    assert len(raw) == 205


def test_written_utc_of_a_plain_part_is_its_mtime(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    p = _parts(store, "fills", ACC, "XAUUSD", 2026, 11)[0]
    assert list(written_utc(p)) == [pd.Timestamp(p.stat().st_mtime_ns, unit="ns", tz="UTC")]


def test_the_build_is_streamed_in_bounded_row_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Memory bound: no read or write holds more than `row_group_rows` rows (a 10^7-row tick month is never one
    frame): parts are read batch by batch and written as row groups of at most that size."""
    store = Store(tmp_path)
    for d in range(1, 4):
        store.append("ticks", _ticks(f"2026-09-0{d} 08:00", 70, seed=d), source=ACC, dedupe=False)
    before = store.read("ticks", source=ACC)
    seen: list[int] = []
    real = compact_mod._conform

    def spy(batch: Any, schema: Any, *rest: Any) -> Any:
        seen.append(batch.num_rows)
        return real(batch, schema, *rest)
    monkeypatch.setattr(compact_mod, "_conform", spy)
    rep = compact_table(store, "ticks", before=NOW, row_group_rows=32)
    assert rep.partitions[0].error is None and seen and max(seen) <= 32
    (new,) = _parts(store, "ticks", ACC, "XAUUSD", 2026, 9)
    md = pq.read_metadata(new)
    assert md.num_rows == 210 and md.num_row_groups >= 7
    assert all(md.row_group(i).num_rows <= 32 for i in range(md.num_row_groups))
    monkeypatch.undo()
    store.append("ticks", _ticks("2026-09-29 08:00", 3), source=ACC, dedupe=False)
    before = store.read("ticks", source=ACC)
    seen.clear()
    monkeypatch.setattr(compact_mod, "_conform", spy)
    compact_table(store, "ticks", before=NOW, row_group_rows=32)    # the big compacted part is read in batches too
    assert seen and max(seen) <= 32
    pd.testing.assert_frame_equal(store.read("ticks", source=ACC), before)


def test_the_job_is_time_boxed_and_defers_the_rest(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    rep = compact_table(store, "ticks", before=NOW, time_box_s=0.0)
    assert rep.partitions == [] and rep.deferred == 2                 # September and October: next night
    assert rep.summary()["deferred"] == 2
    assert len(_parts(store, "ticks", ACC, "XAUUSD", 2026, 9)) == 5


def test_min_parts_leaves_a_lightly_fragmented_month(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    rep = compact_table(store, "ticks", before=NOW, min_parts=3)
    assert {(p.month, p.parts_before) for p in rep.partitions} == {(9, 5)}
    assert len(_parts(store, "ticks", ACC, "XAUUSD", 2026, 10)) == 2


def test_partitions_are_paced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(tmp_path)
    _seed(store)
    naps: list[float] = []
    monkeypatch.setattr(compact_mod.time, "sleep", naps.append)
    compact_table(store, "ticks", before=NOW, pause_s=0.5)
    assert naps == [0.5, 0.5]


def test_the_spill_directory_is_bounded_and_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The verifying sorted read runs in DuckDB with a memory limit and a spill directory under the store root (not
    in a table folder, never *.parquet), which is removed after the run."""
    store = Store(tmp_path)
    _seed(store)
    settings: list[str] = []
    real = compact_mod._connect

    def spy() -> Any:
        con = real()
        orig = con.execute

        class Con:
            def execute(self, sql: str, *a: Any) -> Any:
                if sql.startswith("SET"):
                    settings.append(sql)
                return orig(sql, *a)

            def __getattr__(self, name: str) -> Any:
                return getattr(con, name)
        return Con()
    monkeypatch.setattr(compact_mod, "_connect", spy)
    compact_table(store, "ticks", before=NOW, memory_limit="256MB", threads=1)
    assert any("memory_limit" in s and "256MB" in s for s in settings)
    assert any("threads" in s for s in settings)
    assert any("temp_directory" in s and ".compact_tmp" in s for s in settings)
    assert not (tmp_path / ".compact_tmp").exists()


def test_job_compacts_every_configured_table_and_reports_per_table(tmp_path: Path) -> None:
    from goldbot.config import CompactSettings, load_settings
    from goldbot.ops.jobs import journal_compact
    s = load_settings().compact
    assert {"decisions", "fills", "dq_events", "observe_ticks"} <= set(s.tables)
    assert s.tables[-1] == "ticks"          # enabled after the brain measurement (BACKLOG 239), compacted last
    store = Store(tmp_path)
    _seed(store)
    cfg = CompactSettings(tables=["decisions", "ticks", "observe_ticks", "fills", "dq_events"])
    out = journal_compact(SimpleNamespace(store=store, settings=SimpleNamespace(compact=cfg)), NOW)  # type: ignore[arg-type]
    assert set(out["tables"]) == {"decisions", "ticks", "observe_ticks", "fills", "dq_events"}
    assert out["tables"]["ticks"]["partitions"] == 2 and out["tables"]["fills"]["rows"] == 3
    assert out["partitions"] == sum(t["partitions"] for t in out["tables"].values())
    assert out["errors"] == [] and out["deferred"] == 0


def test_job_defaults_without_settings_still_compact_decisions(tmp_path: Path) -> None:
    from goldbot.ops.jobs import journal_compact
    store = Store(tmp_path)
    _seed(store)
    out = journal_compact(SimpleNamespace(store=store), NOW)  # type: ignore[arg-type]
    assert out["tables"]["fills"]["partitions"] == 1 and "ticks" not in out["tables"]


def test_the_parts_report_measures_every_folder_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import importlib.util
    import json
    path = Path(__file__).resolve().parents[1] / "scripts" / "store_parts_report.py"
    spec = importlib.util.spec_from_file_location("store_parts_report_179", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = Store(tmp_path)
    _seed(store)
    files = sorted(tmp_path.rglob("*.parquet"))
    assert mod.main(["--data-root", str(tmp_path), "--tables", "ticks", "fills"]) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    tick = next(x for x in lines if x["table"] == "ticks")
    assert tick["closed_parts"] + tick["open_parts"] == 9 and tick["rows"] == tick["read_rows"] == 240
    assert tick["biggest_closed_month"]["rows"] == 200 and "read_s" in tick
    assert sorted(tmp_path.rglob("*.parquet")) == files


def test_the_tz_quarantine_selector_still_finds_ahead_rows_after_compaction(tmp_path: Path) -> None:
    """The 2026-10-26 selector (rows stamped > 30 min after their part was written) reads each row's original write
    time, so a compacted month selects the same rows as its parts did."""
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts" / "quarantine_tz_ticks_2026_10_26.py"
    spec = importlib.util.spec_from_file_location("quarantine_tz_script_179", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = Store(tmp_path)
    for t0, seed in (("2026-10-26 01:00", 0), ("2026-10-26 02:00", 1), ("2026-10-26 03:00", 2)):
        store.append("ticks", _ticks(t0, 10, seed=seed), source=ACC, dedupe=False)
    parts = sorted(_parts(store, "ticks", ACC, "XAUUSD", 2026, 10),
                   key=lambda p: pq.read_table(p).column("ts_utc")[0].as_py())
    for p, wrote in zip(parts, ["2026-10-26 00:00", "2026-10-26 02:05", "2026-10-26 03:05"]):
        ns = pd.Timestamp(wrote, tz="UTC").value      # the first part was written an hour before its stamps
        os.utime(p, ns=(ns, ns))
    picked = sum(int(mod.ahead(p, pq.read_table(p).to_pandas()).sum()) for p in parts)
    compact_table(store, "ticks", before=NOW)
    (new,) = _parts(store, "ticks", ACC, "XAUUSD", 2026, 10)
    rows = pq.read_table(new).to_pandas()
    sel = mod.ahead(new, rows)
    assert picked == 10 and int(sel.sum()) == 10
    assert rows.loc[sel.to_numpy(), "ts_utc"].min() == pd.Timestamp("2026-10-26 01:00", tz="UTC")


# ---- code review of 80339ff7 (LOWs)
def test_a_part_compacted_before_lineage_has_unknown_write_times(tmp_path: Path) -> None:
    """Review 2: a part the R131 compaction wrote (no pandas metadata, no lineage) holds many flushes re-sorted; its
    own mtime is the compaction time, not a write time: its rows read as unknown (NaT), also after a later compaction."""
    import pyarrow as pa
    store = Store(tmp_path)
    pdir = store.partition_dir("decisions", ACC, "XAUUSD", pd.Timestamp("2026-10-01", tz="UTC"))
    pdir.mkdir(parents=True)
    old = pd.DataFrame({"ts_utc": pd.date_range("2026-10-01", periods=4, freq="h", tz="UTC"), "action": "score"})
    pq.write_table(pa.Table.from_pandas(old, preserve_index=False).replace_schema_metadata(None),
                   pdir / "part-r131aaaa.parquet")
    assert written_utc(pdir / "part-r131aaaa.parquet").isna().all()
    store.append("decisions", old.assign(ts_utc=old["ts_utc"] + pd.Timedelta(days=5)), source=ACC, dedupe=False)
    late = next(p for p in pdir.glob("*.parquet") if p.name != "part-r131aaaa.parquet")
    late_at = pd.Timestamp(late.stat().st_mtime_ns, unit="ns", tz="UTC")
    before = store.read("decisions", source=ACC)
    compact_table(store, "decisions", before=NOW)
    (new,) = sorted(pdir.glob("*.parquet"))
    w = written_utc(new)
    assert w.iloc[:4].isna().all() and list(w.iloc[4:]) == [late_at] * 4
    pd.testing.assert_frame_equal(store.read("decisions", source=ACC), before)


def test_quarantine_move_after_a_crashed_compaction_does_not_duplicate(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 3: a compaction died between its rename and its unlinks (old parts + new part on disk, manifest names
    the old ones). move_rows finishes that swap first, so its rewrite never carries the replaced rows twice."""
    from goldbot.data import quarantine as qr
    store = Store(tmp_path)
    _seed(store)
    before = store.read("ticks", source=ACC)

    def boom(*_a: Any, **_k: Any) -> None:
        raise SystemExit("killed between the rename and the unlinks")
    monkeypatch.setattr(compact_mod, "_unlink_replaced", boom)
    with pytest.raises(SystemExit):
        compact_table(store, "ticks", before=NOW)
    monkeypatch.undo()
    lo = pd.Timestamp("2026-09-01 08:00", tz="UTC")

    def first_secs(_p: Path, rows: pd.DataFrame) -> pd.Series:
        ts = pd.to_datetime(rows["ts_utc"], utc=True)
        return (ts >= lo) & (ts < lo + pd.Timedelta(seconds=2))
    n = qr.move_rows(store, "ticks", source=ACC, symbol="XAUUSD", select=first_secs, reason=qr.TZ_MISLABEL,
                     now=NOW, apply=True)
    after = store.read("ticks", source=ACC)
    moved = store.read("ticks_quarantine", source=ACC)
    assert n == 8 and len(moved) == 8 and len(after) == len(before) - 8
    assert not list(store.table_dir("ticks").rglob(".compact.json"))


def test_a_column_typed_differently_across_parts_is_unified_as_duckdb_reads_it(tmp_path: Path) -> None:
    """Review 4: one writer stored `detail` as an integer, another as text. Arrow cannot unify them; DuckDB's
    union_by_name reads VARCHAR. The compacted part holds the column as DuckDB reads it, so the frame is the same."""
    store = Store(tmp_path)
    store.append("dq_events", _dq("2026-09-01 00:00", "gap").assign(detail=[5, 6], score=[1, 2]), source=ACC,
                 dedupe=False)
    store.append("dq_events", _dq("2026-09-02 00:00", "spike").assign(score=[1.5, 2.5]), source=ACC, dedupe=False)
    before = store.read("dq_events", source=ACC)
    rep = compact_table(store, "dq_events", before=NOW)
    assert [p.error for p in rep.partitions] == [None]
    assert len(_parts(store, "dq_events", ACC, "XAUUSD", 2026, 9)) == 1
    pd.testing.assert_frame_equal(store.read("dq_events", source=ACC), before)
