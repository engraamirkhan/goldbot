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
