"""Store.append is an atomic, locked read-modify-write (code review MEDIUM): no lost rows, no empty partition."""
from __future__ import annotations

import multiprocessing as mp
import threading
from pathlib import Path

import pandas as pd
import pytest

from goldbot.data.store import Store


def _mk(i: int) -> pd.DataFrame:
    return pd.DataFrame({"ts_utc": [pd.Timestamp("2024-03-01", tz="UTC") + pd.Timedelta(minutes=i)],
                         "bid": [float(i)], "ask": [float(i) + 1]})


def _worker(root: str, base: int) -> None:
    s = Store(root)
    for j in range(15):
        s.append("bars_1m", _mk(base + j), source="t")


def test_concurrent_appends_from_two_processes_keep_every_row(tmp_path: Path):
    ctx = mp.get_context("spawn")
    ps = [ctx.Process(target=_worker, args=(str(tmp_path), b)) for b in (0, 100)]
    for p in ps:
        p.start()
    for p in ps:
        p.join()
    assert all(p.exitcode == 0 for p in ps)
    assert len(Store(tmp_path).read("bars_1m", source="t")) == 30


def test_crash_after_write_before_unlink_loses_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    s = Store(tmp_path)
    s.append("bars_1m", _mk(0), source="t")
    real = Path.unlink

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("crash")

    monkeypatch.setattr(Path, "unlink", boom)
    with pytest.raises(RuntimeError):
        s.append("bars_1m", _mk(5), source="t")
    monkeypatch.setattr(Path, "unlink", real)
    assert len(s.read("bars_1m", source="t").drop_duplicates("ts_utc")) == 2
    s.append("bars_1m", _mk(9), source="t")          # the next append dedupes the leftovers
    assert len(s.read("bars_1m", source="t")) == 3


def test_reader_never_sees_empty_partition_during_append(tmp_path: Path):
    s = Store(tmp_path)
    s.append("bars_1m", _mk(0), source="t")
    stop = threading.Event()
    empty: list[int] = []

    def rd() -> None:
        while not stop.is_set():
            if len(s.read("bars_1m", source="t")) == 0:
                empty.append(1)

    t = threading.Thread(target=rd)
    t.start()
    for i in range(1, 25):
        s.append("bars_1m", _mk(i), source="t")
    stop.set()
    t.join()
    assert not empty


def test_reader_during_append_sees_exactly_old_or_new_rows(tmp_path: Path):
    """Rows are only ever added, so every read must be a prefix 1..n with no duplicates (never old+new together)."""
    s = Store(tmp_path)
    s.append("bars_1m", _mk(0), source="t")
    stop = threading.Event()
    bad: list[int] = []

    def rd() -> None:
        while not stop.is_set():
            ts = s.read("bars_1m", source="t")["ts_utc"]
            if ts.duplicated().any() or len(ts) == 0:
                bad.append(len(ts))

    t = threading.Thread(target=rd)
    t.start()
    for i in range(1, 40):
        s.append("bars_1m", _mk(i), source="t")
    stop.set()
    t.join()
    assert not bad


def _dec(ts: str, agent: object) -> pd.DataFrame:
    return pd.DataFrame({"ts_utc": [pd.Timestamp(ts, tz="UTC")], "agent_id": [agent], "x": [1.0]})


def test_read_unions_parts_when_first_part_has_all_null_column(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    st = Store(tmp_path)
    pdir = st.partition_dir("decisions", "icm-probe", "XAUUSD", pd.Timestamp("2026-10-01", tz="UTC"))
    pdir.mkdir(parents=True)
    first = pa.Table.from_pandas(_dec("2026-10-01", None), preserve_index=False)   # legacy part: agent_id typed null
    assert pa.types.is_null(first.schema.field("agent_id").type)
    pq.write_table(first, pdir / "part-00000001.parquet")
    st._write_atomic(pdir, _dec("2026-10-02", "a1"))
    out = st.read("decisions", source="icm-probe")
    assert len(out) == 2
    assert pd.api.types.is_string_dtype(out["agent_id"])
    assert pd.isna(out["agent_id"].iloc[0]) and out["agent_id"].iloc[1] == "a1"


@pytest.mark.parametrize("later", [[1.5], ["s"]])
def test_all_none_part_unions_with_later_typed_part(tmp_path: Path, later: list) -> None:
    st = Store(tmp_path)
    for ts, vals in (("2026-10-01", [None]), ("2026-10-02", later)):
        pdir = st.partition_dir("decisions", "icm-probe", "XAUUSD", pd.Timestamp(ts, tz="UTC"))
        pdir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({"ts_utc": [pd.Timestamp(ts, tz="UTC")], "x": vals})
        st._write_atomic(pdir, df)
    out = st.read("decisions", source="icm-probe")
    assert pd.isna(out["x"].iloc[0])
    if isinstance(later[0], float):
        assert out["x"].dtype == "float64" and out["x"].iloc[1] == 1.5
    else:
        assert pd.api.types.is_string_dtype(out["x"]) and out["x"].iloc[1] == "s"
