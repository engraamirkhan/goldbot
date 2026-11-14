"""BACKLOG 260: the open month's tick parts (321,590 on the brain, 39 folders, against 17,360 in closed months; a
Store.read of one folder took 139-174 s and 4.9 GB RSS) are bounded by compacting the parts of CLOSED UTC days inside
the open month. The engines only append today's ticks (dedupe=False: a part is never rewritten), so a part whose rows
all fall before `before`'s UTC day is settled; today's parts (and any part reaching into today or later, a clock
error for example) are never touched. Each night folds the previous day's parts into the month's compacted part. The
same guarantees as the closed-month compaction: every row kept, the same Store.read frame (content hash), the write
time of every row kept as lineage."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from goldbot.config import CompactSettings
from goldbot.data.compact import compact_table, written_utc
from goldbot.data.store import Store
from goldbot.ops.jobs import JOBS

ACC = "icm-demo"
NIGHT = pd.Timestamp("2026-11-14 03:40", tz="UTC")


def _ticks(t0: str, n: int, *, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.Timestamp(t0, tz="UTC") + pd.to_timedelta(np.arange(n) * 250, unit="ms")
    bid = 2650 + rng.normal(0, 0.2, n).round(2)
    return pd.DataFrame({"ts_utc": ts, "bid": bid, "ask": bid + 0.12})


def _pdir(store: Store) -> Path:
    return store.table_dir("ticks") / f"source={ACC}" / "symbol=XAUUSD" / "year=2026" / "month=11"


def _seed(store: Store) -> None:
    for k, t in enumerate(["2026-11-12 08:00", "2026-11-12 09:00", "2026-11-13 08:00", "2026-11-13 23:59:57"]):
        store.append("ticks", _ticks(t, 8, seed=k), source=ACC, dedupe=False)          # closed days
    store.append("ticks", _ticks("2026-11-14 00:00", 8, seed=7), source=ACC, dedupe=False)   # today
    store.append("ticks", _ticks("2026-11-14 03:00", 8, seed=8), source=ACC, dedupe=False)   # today


def test_closed_days_of_the_open_month_become_one_part_and_today_is_untouched(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    _seed(store)
    today = sorted(f.name for f in _pdir(store).glob("*.parquet")
                   if pq.read_table(f, columns=["ts_utc"]).column(0).to_pandas().min() >= pd.Timestamp("2026-11-14",
                                                                                                      tz="UTC"))
    before = store.read("ticks", source=ACC)
    rep = compact_table(store, "ticks", before=NIGHT, closed_days=True)
    assert [(p.parts_before, p.parts_after) for p in rep.partitions] == [(4, 1)]
    files = sorted(f.name for f in _pdir(store).glob("*.parquet"))
    assert len(files) == 3 and set(today) <= set(files)                 # today's two parts, as they were
    pd.testing.assert_frame_equal(store.read("ticks", source=ACC), before)
    new = next(f for f in _pdir(store).glob("*.parquet") if f.name not in today)
    assert written_utc(new).notna().all() and len(written_utc(new)) == 32     # lineage of every closed-day row


def test_off_by_default_and_the_next_night_folds_the_previous_day_in(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    _seed(store)
    assert compact_table(store, "ticks", before=NIGHT).partitions == []        # default: the open month untouched
    compact_table(store, "ticks", before=NIGHT, closed_days=True)
    store.append("ticks", _ticks("2026-11-14 05:00", 8, seed=9), source=ACC, dedupe=False)
    before = store.read("ticks", source=ACC)
    rep = compact_table(store, "ticks", before=NIGHT + pd.Timedelta(days=1), closed_days=True)
    assert [(p.parts_before, p.parts_after) for p in rep.partitions] == [(4, 1)]   # month part + 3 parts of the 14th
    assert len(list(_pdir(store).glob("*.parquet"))) == 1
    pd.testing.assert_frame_equal(store.read("ticks", source=ACC), before)


def test_a_part_reaching_into_today_or_later_is_never_taken(tmp_path: Path) -> None:
    """A part with one tick at or after midnight (a flush across midnight, or a server-clock error putting ticks in
    the future) stays: only parts settled entirely before the day are compacted."""
    store = Store(tmp_path / "data")
    store.append("ticks", _ticks("2026-11-12 08:00", 8), source=ACC, dedupe=False)
    store.append("ticks", _ticks("2026-11-12 09:00", 8), source=ACC, dedupe=False)
    store.append("ticks", _ticks("2026-11-13 23:59:58", 16), source=ACC, dedupe=False)     # ends 00:00:01.75
    rep = compact_table(store, "ticks", before=NIGHT, closed_days=True)
    assert [(p.parts_before, p.parts_after) for p in rep.partitions] == [(2, 1)]
    assert len(list(_pdir(store).glob("*.parquet"))) == 2


def test_the_job_compacts_closed_days_of_the_configured_tables(tmp_path: Path) -> None:
    assert CompactSettings().closed_days_tables == ["ticks", "observe_ticks"]
    store = Store(tmp_path / "data")
    _seed(store)
    cfg = CompactSettings(tables=["ticks"])
    out = JOBS["journal_compact"](SimpleNamespace(store=store, settings=SimpleNamespace(compact=cfg)), NIGHT)  # type: ignore[arg-type]
    assert out["tables"]["ticks"]["parts_before"] == 4 and out["tables"]["ticks"]["parts_after"] == 1
    none = CompactSettings(tables=["ticks"], closed_days_tables=[])
    store2 = Store(tmp_path / "data2")
    _seed(store2)
    out2 = JOBS["journal_compact"](SimpleNamespace(store=store2, settings=SimpleNamespace(compact=none)), NIGHT)  # type: ignore[arg-type]
    assert out2["partitions"] == 0
