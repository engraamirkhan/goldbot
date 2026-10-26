"""Quarantine of stored rows after the fact (goldbot/data/quarantine.py) and its effect on D12.

2026-10-26: the icm MT5 bridges converted with Europe/Athens (UTC+2) while the broker ran UTC+3 (NY+7, the US-EU gap
week), so the engines stored ticks stamped one hour ahead of the wall clock that wrote them. The rows are moved out of
`ticks` into `ticks_quarantine` (never edited, never deleted), the span is recorded, and the reconciliation reports the
span instead of failing on it."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from goldbot.data import crossfeed as cf
from goldbot.data import quarantine as qr
from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.ops.watchdog import loop_heartbeat_path
from tests.test_crossfeed import NOW, FakeBroker, _health_ctx, as_broker_m1, day_ticks

T0 = pd.Timestamp("2026-10-07 06:00", tz="UTC")
T1 = pd.Timestamp("2026-10-07 10:00", tz="UTC")


def _ahead(path: Path, rows: pd.DataFrame) -> pd.Series:
    """Rows stamped more than 30 minutes after their file was written: impossible for a live tick."""
    written = pd.Timestamp(os.path.getmtime(path), unit="s", tz="UTC")
    return pd.to_datetime(rows["ts_utc"], utc=True) > written + pd.Timedelta(minutes=30)


def _seed(tmp_path: Path) -> tuple[Store, pd.DataFrame, int]:
    """The day's ticks in two part files; the [T0, T1) ones written an hour before their stamps."""
    store = Store(tmp_path / "data")
    ticks = day_ticks()
    bad = (ticks["ts_utc"] >= T0) & (ticks["ts_utc"] < T1)
    store.append("ticks", ticks[~bad], source="icm-demo", dedupe=False)
    pdir = store.partition_dir("ticks", "icm-demo", "XAUUSD", T0)
    for f in pdir.glob("*.parquet"):
        os.utime(f, (NOW.timestamp(), NOW.timestamp()))               # written after every stamp: never ahead
    before = set(pdir.glob("*.parquet"))
    store.append("ticks", ticks[bad], source="icm-demo", dedupe=False)
    (f,) = set(pdir.glob("*.parquet")) - before
    early = (T0 - pd.Timedelta(hours=1)).timestamp()
    os.utime(f, (early, early))
    return store, ticks, int(bad.sum())


def test_dry_run_selects_and_writes_nothing(tmp_path):
    store, ticks, n_bad = _seed(tmp_path)
    n = qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason="tz", now=NOW)
    assert n == n_bad and len(store.read("ticks", source="icm-demo")) == len(ticks)
    assert store.read("ticks_quarantine", source="icm-demo").empty


def test_rows_move_to_the_quarantine_table_unchanged_and_a_rerun_moves_nothing(tmp_path):
    store, ticks, n_bad = _seed(tmp_path)
    n = qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason="tz_mislabel",
                     now=NOW, apply=True)
    kept, gone = store.read("ticks", source="icm-demo"), store.read("ticks_quarantine", source="icm-demo")
    assert n == n_bad == len(gone) and len(kept) == len(ticks) - n_bad
    assert kept["ts_utc"].between(T0, T1, inclusive="left").sum() == 0
    assert set(gone["dq_flag"]) == {"error:tz_mislabel"} and set(gone["from_table"]) == {"ticks"}
    orig = ticks[(ticks["ts_utc"] >= T0) & (ticks["ts_utc"] < T1)].reset_index(drop=True)
    got = gone.sort_values("ts_utc").reset_index(drop=True)
    pd.testing.assert_frame_equal(got[["bid", "ask"]], orig[["bid", "ask"]])    # first-release values reproducible
    assert qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason="tz_mislabel",
                        now=NOW, apply=True) == 0


def test_spans_are_recorded_once_and_read_back_by_overlap(tmp_path):
    store = Store(tmp_path / "data")
    assert qr.spans(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1) == []
    for _ in range(2):
        qr.record_span(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1, reason="tz_mislabel",
                       detail="test", now=NOW)
    assert len(store.read("dq_events", source="icm-demo")) == 1
    assert qr.spans(store, source="icm-demo", symbol="XAUUSD", start=T1 - pd.Timedelta(minutes=1),
                    end=NOW) == [(T0, T1)]
    assert qr.spans(store, source="icm-demo", symbol="XAUUSD", start=T1, end=NOW) == []
    assert qr.spans(store, source="icm-probe", symbol="XAUUSD", start=T0, end=T1) == []


def test_a_quarantined_window_is_reported_by_d12_not_failed(tmp_path):
    store, ticks, _ = _seed(tmp_path)
    loop_heartbeat_path(tmp_path, "icm-demo").write_text(json.dumps(
        {"phase": "loop", "ts": NOW.timestamp() - 5, "started": (NOW - pd.Timedelta(days=3)).timestamp()}))
    qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason="tz", now=NOW, apply=True)
    broker = FakeBroker(as_broker_m1(ticks_to_1m(ticks)))
    plain = cf.reconcile_account(store, broker, "icm-demo", "XAUUSD", NOW, state_dir=tmp_path)
    assert plain.missing_engine >= 200 and plain.quarantined_minutes == 0     # without the span: an engine gap
    qr.record_span(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1, reason="tz_mislabel", detail="test",
                   now=NOW)
    rep = cf.reconcile_account(store, broker, "icm-demo", "XAUUSD", NOW, state_dir=tmp_path)
    assert rep.quarantined_minutes == 240 and rep.missing_engine == 0 and rep.severity == "ok"
    c = cf.check_reconciliation(_health_ctx(tmp_path), "icm-demo")
    assert c.status == "ok" and "240 broker minutes in a quarantined span" in c.reason
