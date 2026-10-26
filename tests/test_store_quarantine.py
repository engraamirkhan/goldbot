"""Quarantine of stored rows after the fact (goldbot/data/quarantine.py) and its effect on D12.

2026-10-26: the icm MT5 bridges converted with Europe/Athens (UTC+2) while the broker ran UTC+3 (NY+7, the US-EU gap
week), so the engines stored ticks stamped one hour ahead of the wall clock that wrote them. The rows are moved out of
`ticks` into `ticks_quarantine` (never edited, never deleted), the span is recorded, and the reconciliation reports the
span instead of failing on it."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pandas as pd
import pytest

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
    n = qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason=qr.TZ_MISLABEL, now=NOW)
    assert n == n_bad and len(store.read("ticks", source="icm-demo")) == len(ticks)
    assert store.read("ticks_quarantine", source="icm-demo").empty


def test_rows_move_to_the_quarantine_table_unchanged_and_a_rerun_moves_nothing(tmp_path):
    store, ticks, n_bad = _seed(tmp_path)
    n = qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason=qr.TZ_MISLABEL,
                     now=NOW, apply=True)
    kept, gone = store.read("ticks", source="icm-demo"), store.read("ticks_quarantine", source="icm-demo")
    assert n == n_bad == len(gone) and len(kept) == len(ticks) - n_bad
    assert kept["ts_utc"].between(T0, T1, inclusive="left").sum() == 0
    assert set(gone["dq_flag"]) == {f"error:{qr.TZ_MISLABEL}"} and set(gone["from_table"]) == {"ticks"}
    orig = ticks[(ticks["ts_utc"] >= T0) & (ticks["ts_utc"] < T1)].reset_index(drop=True)
    got = gone.sort_values("ts_utc").reset_index(drop=True)
    pd.testing.assert_frame_equal(got[["bid", "ask"]], orig[["bid", "ask"]])    # first-release values reproducible
    assert qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason=qr.TZ_MISLABEL,
                        now=NOW, apply=True) == 0


def test_a_crash_between_the_quarantine_copy_and_the_rewrite_does_not_duplicate_on_rerun(tmp_path, monkeypatch):
    store, _, n_bad = _seed(tmp_path)

    real, calls = store._write_atomic, []

    def crash(*a, **k):                   # the quarantine copy is written, the table rewrite fails
        calls.append(1)
        if len(calls) > 1:
            raise OSError("disk full")
        return real(*a, **k)
    with monkeypatch.context() as m:
        m.setattr(store, "_write_atomic", crash)
        with pytest.raises(OSError):
            qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason=qr.TZ_MISLABEL,
                         now=NOW, apply=True)
    assert len(store.read("ticks_quarantine", source="icm-demo")) == n_bad      # the copy came first
    qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason=qr.TZ_MISLABEL, now=NOW,
                 apply=True)
    assert len(store.read("ticks_quarantine", source="icm-demo")) == n_bad


def test_only_known_reasons_backed_by_quarantined_rows_make_a_span(tmp_path):
    store, _, _ = _seed(tmp_path)
    with pytest.raises(ValueError, match="reason"):
        qr.record_span(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1, reason="feed looked odd",
                       detail="x", now=NOW)
    # a span with no rows in a quarantine table behind it would hide a real fault: it is ignored
    qr.record_span(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1, reason=qr.TZ_MISLABEL, detail="x",
                   now=NOW)
    assert qr.spans(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1) == []
    qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason=qr.TZ_MISLABEL, now=NOW,
                 apply=True)
    assert qr.spans(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1) == [(T0, T1)]


def test_spans_are_recorded_once_and_read_back_by_overlap(tmp_path):
    store, _, _ = _seed(tmp_path)
    qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason=qr.TZ_MISLABEL, now=NOW,
                 apply=True)
    for _ in range(2):
        qr.record_span(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1, reason=qr.TZ_MISLABEL,
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
    qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason=qr.TZ_MISLABEL, now=NOW, apply=True)
    broker = FakeBroker(as_broker_m1(ticks_to_1m(ticks)))
    plain = cf.reconcile_account(store, broker, "icm-demo", "XAUUSD", NOW, state_dir=tmp_path)
    assert plain.missing_engine >= 200 and plain.quarantined_minutes == 0     # without the span: an engine gap
    qr.record_span(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1, reason=qr.TZ_MISLABEL, detail="test",
                   now=NOW)
    rep = cf.reconcile_account(store, broker, "icm-demo", "XAUUSD", NOW, state_dir=tmp_path)
    # reported, not failed; a quarantined share above 5% of the window still warns, so a span cannot hide a day
    assert rep.quarantined_minutes == 240 and rep.missing_engine == 0 and rep.severity == "warning"
    c = cf.check_reconciliation(_health_ctx(tmp_path), "icm-demo")
    assert c.status == "warn" and "240 broker minutes in a quarantined span" in c.reason


@pytest.mark.parametrize("q,severity", [(0, "ok"), (60, "ok"), (80, "warning")])
def test_a_quarantined_share_above_five_percent_warns(q, severity):
    _, g = cf.grade(broker_minutes=1300 - q, engine_minutes=1300 - q, matched=1300 - q, edge_excluded=0,
                    close_mismatch=0, missing_engine=0, missing_broker=0, quarantined_minutes=q)
    assert g == severity


# ------------------------------------------------------------------------------------------- the 2026-10-26 script
def _script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "quarantine_tz_ticks_2026_10_26.py"
    spec = importlib.util.spec_from_file_location("quarantine_tz_script", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_corrected_minutes_become_visible_when_written_not_at_their_minute_close():
    sc = _script()
    m1 = cf.broker_m1_bars(as_broker_m1(ticks_to_1m(day_ticks())))
    fixed = sc.stamp_corrected(m1, NOW - pd.Timedelta(hours=2))
    applied = NOW - pd.Timedelta(hours=2)
    assert (pd.to_datetime(fixed["available_utc"], utc=True) == applied).all()
    vis = pd.to_datetime(fixed["visible_at"], utc=True)
    assert (vis >= applied).all() and (vis >= fixed["ts_utc"] + pd.Timedelta(minutes=1)).all()
    late = fixed["ts_utc"] + pd.Timedelta(minutes=1) > applied          # minutes closing after the repair keep theirs
    assert (vis[late] == (fixed["ts_utc"] + pd.Timedelta(minutes=1))[late]).all()


def test_backdated_corrected_minutes_are_repaired_once(tmp_path):
    sc = _script()
    store = Store(tmp_path / "data")
    m1 = cf.broker_m1_bars(as_broker_m1(ticks_to_1m(day_ticks()))).head(50)
    applied = NOW - pd.Timedelta(hours=1)
    old = m1.assign(dq_flag="warning:corrected_broker_m1", available_utc=applied, correction_of=sc.REASON)
    store.append("bars_1m_live", old, source="icm", symbol="XAUUSD")
    assert sc.repair_visible_at(store, now=NOW, apply=False) == 50
    assert sc.repair_visible_at(store, now=NOW, apply=True) == 50
    live = store.read("bars_1m_live", source="icm")
    assert len(live) == 50 and (pd.to_datetime(live["visible_at"], utc=True) >= applied).all()
    assert (pd.to_datetime(live["available_utc"], utc=True) == applied).all()          # when it was written: kept
    assert len(store.read("bars_quarantine", source="icm")) == 50                       # the backdated copies, kept
    assert sc.repair_visible_at(store, now=NOW, apply=True) == 0
