"""BACKLOG 241: tick rows carry their own write time. `Engine.flush_ticks` and the observe collector add `written_utc`
(the flush's wall clock, UTC) to every row; rows written before the column read it as null (never backfilled); the
2026-10-26 tz selector prefers the column over the part file's lineage when it is present."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pandas as pd

from goldbot.data.compact import stamp_written
from goldbot.data.store import Store
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_observe_collector import OBS, SAT, _collector, _step, _Terminal

T = pd.Timestamp("2025-03-03 10:00", tz="UTC")


def _engine(tmp_path: Path) -> Engine:
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                               owner_user_id=111, data_root=str(tmp_path / "data")),
                  PaperBroker(equity=10_000), [spec], {"session_open": ConstantModel(p=0.5)}, ApprovalCenter({111}))


def test_stamp_written_adds_one_utc_wall_clock_per_flush() -> None:
    df = pd.DataFrame({"ts_utc": [T, T + pd.Timedelta(seconds=1)], "bid": [1.0, 2.0], "ask": [1.1, 2.1]})
    now = pd.Timestamp("2026-11-14 08:00:00.123456789", tz="UTC")
    out = stamp_written(df, now)
    assert "written_utc" not in df.columns                         # the caller's frame is not modified
    assert str(out["written_utc"].dtype) == "datetime64[ns, UTC]" and (out["written_utc"] == now).all()


def test_engine_flush_stamps_every_tick_row_with_the_flush_wall_clock(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    for i in range(5):
        eng._tick_log.append(Tick(ts_utc=T + pd.Timedelta(seconds=i), bid=2900.0 + i, ask=2900.3 + i))
    before = pd.Timestamp.now(tz="UTC")
    eng.flush_ticks()
    after = pd.Timestamp.now(tz="UTC")
    got = Store(tmp_path / "data").read("ticks", source="icm-demo")
    assert len(got) == 5 and got["written_utc"].notna().all()
    assert got["written_utc"].between(before, after).all() and got["written_utc"].nunique() == 1


def test_rows_written_before_the_column_read_null_and_are_never_backfilled(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    old = pd.DataFrame({"ts_utc": [T], "bid": [1.0], "ask": [1.1]})
    store.append("ticks", old, source="icm-demo", dedupe=False)
    new = stamp_written(pd.DataFrame({"ts_utc": [T + pd.Timedelta(minutes=1)], "bid": [2.0], "ask": [2.1]}),
                        pd.Timestamp("2026-11-14 08:00", tz="UTC"))
    store.append("ticks", new, source="icm-demo", dedupe=False)
    got = store.read("ticks", source="icm-demo").set_index("bid")["written_utc"]
    assert pd.isna(got.loc[1.0]) and got.loc[2.0] == pd.Timestamp("2026-11-14 08:00", tz="UTC")


def test_the_observe_collector_stamps_its_tick_samples(tmp_path: Path) -> None:
    term = _Terminal()
    c = _collector(tmp_path, term, backfill_hours=1)
    before = pd.Timestamp.now(tz="UTC")
    _step(c, term, SAT)
    c.flush()
    ticks = Store(tmp_path / "data").read("observe_ticks", source=OBS, symbol="XAUUSD247")
    assert len(ticks) >= 1 and (ticks["written_utc"] >= before).all()


def _selector():
    path = Path(__file__).resolve().parents[1] / "scripts" / "quarantine_tz_ticks_2026_10_26.py"
    spec = importlib.util.spec_from_file_location("qtz", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_tz_selector_prefers_the_row_column_over_the_part_file(tmp_path: Path) -> None:
    mod = _selector()
    store = Store(tmp_path / "data")
    ts = mod.LO + pd.Timedelta(hours=2)
    rows = pd.DataFrame({"ts_utc": [ts, ts], "bid": [1.0, 2.0], "ask": [1.1, 2.1],
                         # row 0 written an hour before its stamp (ahead), row 1 after it
                         "written_utc": [ts - pd.Timedelta(hours=1), pd.NaT]}).astype({"written_utc": "datetime64[ns, UTC]"})
    store.append("ticks", rows, source="icm-demo", dedupe=False)
    (part,) = list(store.table_dir("ticks").rglob("*.parquet"))
    late = (ts + pd.Timedelta(hours=1)).timestamp()
    os.utime(part, (late, late))                     # the file says "written after": only the column says ahead
    sel = mod.ahead(part, pd.read_parquet(part))
    assert sel.tolist() == [True, False]             # null column -> the file's lineage (written after: not ahead)
