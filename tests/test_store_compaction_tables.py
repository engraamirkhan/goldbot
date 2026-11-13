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

    def spy(batch: Any, schema: Any) -> Any:
        seen.append(batch.num_rows)
        return real(batch, schema)
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
    assert "ticks" not in s.tables          # enabled only after the brain measurement (backlog follow-up 239)
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
