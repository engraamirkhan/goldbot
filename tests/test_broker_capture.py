"""ADR 0105 (ii) capture guard: due day (CME full closures), same-day FAIL, month copy and recovery, freeze rule,
SHA256SUMS verification, the job, the two checks (SRE review of #471)."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data import broker_capture as BC


def _bars(day: str, minutes: int) -> pd.DataFrame:
    lo, _ = BC.session_bounds(dt.date.fromisoformat(day))
    return pd.DataFrame({"ts_utc": lo + pd.to_timedelta(range(minutes), unit="min"), "bid_close": 2000.0})


def _month(m: str, n: int = 30) -> pd.DataFrame:
    start = pd.Period(m, freq="M").to_timestamp().tz_localize("UTC") + pd.Timedelta(days=1)
    return pd.DataFrame({"ts_utc": pd.date_range(start, periods=n, freq="min"), "bid_close": 1.0})


def test_the_due_day_skips_weekends_and_cme_full_closures() -> None:
    assert BC.due_day(pd.Timestamp("2026-11-26 23:49", tz="UTC")) == dt.date(2026, 11, 25)
    assert BC.due_day(pd.Timestamp("2026-11-26 23:51", tz="UTC")) == dt.date(2026, 11, 26)   # Thanksgiving: early halt
    assert BC.due_day(pd.Timestamp("2026-11-29 12:00", tz="UTC")) == dt.date(2026, 11, 27)    # Sunday -> Friday
    assert BC.due_day(pd.Timestamp("2026-12-26 12:00", tz="UTC")) == dt.date(2026, 12, 24)    # Christmas
    assert BC.due_day(pd.Timestamp("2027-03-27 12:00", tz="UTC")) == dt.date(2027, 3, 25)     # Good Friday
    assert BC.due_day(pd.Timestamp("2027-12-27 12:00", tz="UTC")) == dt.date(2027, 12, 23)    # Christmas observed
    assert BC.due_day(pd.Timestamp("2026-09-30 23:59", tz="UTC")) is None


def test_a_missed_session_day_fails_the_same_day_with_the_recovery_command() -> None:
    now = pd.Timestamp("2026-11-25 23:52", tz="UTC")
    st, why = BC.judge_capture(_bars("2026-11-25", 300), now)
    assert st == "fail" and "300 of 1380" in why and "m1-backfill icm-demo --days 3" in why
    assert BC.judge_capture(_bars("2026-11-25", 1300), now)[0] == "ok"


def test_an_absent_closed_month_is_copied_and_recovers(tmp_path: Path) -> None:
    slot = pd.Timestamp("2026-12-02 23:55", tz="UTC")
    bars = pd.concat([_month("2026-10"), _month("2026-11"), _month("2026-12")])
    assert BC.months_to_copy(slot, raw_dir=tmp_path) == ["2026-10", "2026-11", "2026-12"]
    out = BC.copy_months(bars, slot, raw_dir=tmp_path)
    assert {"icm-demo_m1_2026-10.parquet", "icm-demo_m1_2026-11.parquet"} <= set(out["files"])
    assert BC.missing_copies(slot, raw_dir=tmp_path) == [] and out["sum_mismatch"] == []
    assert (tmp_path / "icm-demo" / "gaps_2026-10.json").exists()
    assert BC.months_to_copy(slot, raw_dir=tmp_path) == ["2026-11", "2026-12"]     # Oct copied, Nov not frozen yet


def test_the_copy_check_fails_on_a_late_month_and_on_a_sum_mismatch(tmp_path: Path) -> None:
    late = pd.Timestamp("2026-11-06 01:00", tz="UTC")
    st, why = BC.judge_copy(late, raw_dir=tmp_path)
    assert st == "fail" and "2026-10" in why
    BC.copy_months(_month("2026-10"), late, months=["2026-10"], raw_dir=tmp_path)
    assert BC.judge_copy(late, raw_dir=tmp_path)[0] == "ok"
    f = BC.month_file(tmp_path, BC.REFERENCE_ACCOUNT, "2026-10")
    f.write_bytes(f.read_bytes() + b"x")
    st, why = BC.judge_copy(late, raw_dir=tmp_path)
    assert st == "fail" and "SHA256SUMS mismatch" in why


def test_sums_are_rewritten_only_for_files_written_and_others_verified(tmp_path: Path) -> None:
    now = pd.Timestamp("2026-11-20 23:55", tz="UTC")
    BC.copy_months(pd.concat([_month("2026-10"), _month("2026-11")]), now, months=["2026-10", "2026-11"],
                   raw_dir=tmp_path)
    f10 = BC.month_file(tmp_path, BC.REFERENCE_ACCOUNT, "2026-10")
    f10.write_bytes(f10.read_bytes() + b"tamper")
    out = BC.copy_months(_month("2026-11", 40), now, months=["2026-11"], raw_dir=tmp_path)
    assert out["sum_mismatch"] == ["icm-demo_m1_2026-10.parquet"]      # not silently re-summed from disk


def test_a_frozen_month_rewrite_gets_a_new_sha_and_a_log_entry(tmp_path: Path) -> None:
    first = pd.Timestamp("2026-11-02 23:55", tz="UTC")
    BC.copy_months(_month("2026-10", 30), first, months=["2026-10"], raw_dir=tmp_path)
    later = pd.Timestamp("2026-11-20 23:55", tz="UTC")
    assert BC.frozen("2026-10", later) and not BC.frozen("2026-10", first)
    out = BC.copy_months(_month("2026-10", 31), later, months=["2026-10"], raw_dir=tmp_path)
    acc = tmp_path / "icm-demo"
    rw = [json.loads(x) for x in (acc / "rewrites.jsonl").read_text().splitlines()]
    assert rw[0]["month"] == "2026-10" and rw[0]["sha256"] == out["files"]["icm-demo_m1_2026-10.parquet"]["sha256"]
    assert any("rewrite_utc" in e for e in json.loads((acc / "gaps_2026-10.json").read_text()))


class _Store:
    def __init__(self, bars: pd.DataFrame) -> None:
        self.bars = bars
        self.calls: list[dict[str, Any]] = []

    def read(self, table: str, **kw: Any) -> pd.DataFrame:
        self.calls.append({"table": table, **kw})
        return self.bars


def test_the_job_reads_icm_demo_xauusd_only_and_does_not_raise_on_a_missed_day(tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from goldbot.ops import jobs
    monkeypatch.setattr(BC, "RAW_DIR", tmp_path)
    st = _Store(_bars("2026-11-25", 10))
    out = jobs.broker_m1_copy(type("Ctx", (), {"store": st})(), pd.Timestamp("2026-11-25 23:55", tz="UTC"))
    assert st.calls[0]["table"] == "bars_1m_broker" and st.calls[0]["source"] == "icm-demo"
    assert st.calls[0]["symbol"] == "XAUUSD" and st.calls[0]["start"] == BC.CAPTURE_FROM   # October missing: read it
    assert "capture missed" in out["capture"]


def test_both_checks_are_registered_in_the_learning_family() -> None:
    from goldbot.ops import health
    from goldbot.ops.checks import capture
    assert health.check_broker_m1_capture is capture.check_broker_m1_capture
    assert health.check_broker_m1_copy is capture.check_broker_m1_copy
    assert (capture.NAME, capture.COPY_NAME) == ("learning:broker_m1_capture", "learning:broker_m1_copy")


def test_writes_are_atomic(tmp_path: Path) -> None:
    BC.copy_months(_month("2026-11"), pd.Timestamp("2026-11-20", tz="UTC"), months=["2026-11"], raw_dir=tmp_path)
    assert not [p for p in (tmp_path / "icm-demo").iterdir() if p.name.endswith(".part")]
    assert os.path.exists(tmp_path / "icm-demo" / "SHA256SUMS")


def test_a_day7_backfill_is_in_the_frozen_day8_copy_and_a_day6_grade_is_refused(tmp_path: Path,
                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Quant ruling on the freeze window: freeze at day 8 = feed_reconcile's 7-day back-fill window + 1."""
    from goldbot.ops import jobs
    assert BC.FREEZE_DAYS == 8
    monkeypatch.setattr(BC, "RAW_DIR", tmp_path)
    october = _month("2026-10", 60)
    hole = october.iloc[30]["ts_utc"]
    without = october[october["ts_utc"] != hole]
    day5 = pd.Timestamp("2026-11-05 23:55", tz="UTC")
    jobs.broker_m1_copy(type("Ctx", (), {"store": _Store(without)})(), day5)
    sha5 = BC.read_sums(tmp_path / "icm-demo")["icm-demo_m1_2026-10.parquet"]
    with pytest.raises(BC.GradeRefused, match="not frozen"):
        BC.frozen_copy_for_grade("2026-10", pd.Timestamp("2026-11-06 12:00", tz="UTC"), raw_dir=tmp_path)
    day7 = pd.Timestamp("2026-11-07 23:55", tz="UTC")       # feed_reconcile back-filled the hole by now
    assert "2026-10" in BC.months_to_copy(day7, raw_dir=tmp_path)
    jobs.broker_m1_copy(type("Ctx", (), {"store": _Store(october)})(), day7)
    day8 = pd.Timestamp("2026-11-08 23:55", tz="UTC")
    assert "2026-10" not in BC.months_to_copy(day8, raw_dir=tmp_path)       # frozen: the day-8 run leaves it
    jobs.broker_m1_copy(type("Ctx", (), {"store": _Store(october)})(), day8)
    path, sha8 = BC.frozen_copy_for_grade("2026-10", day8, raw_dir=tmp_path)
    assert hole in set(pd.to_datetime(pd.read_parquet(path)["ts_utc"], utc=True)) and sha8 != sha5
    assert not (tmp_path / "icm-demo" / "rewrites.jsonl").exists()        # rewritten before the freeze: no entry


def test_read_capture_refuses_holdout_rows() -> None:
    from goldbot.data.holdout_guard import HoldoutSealed
    sealed = pd.DataFrame({"ts_utc": [pd.Timestamp("2026-09-30 12:00", tz="UTC")], "bid_close": [1.0]})
    with pytest.raises(HoldoutSealed):
        BC.read_capture(_Store(sealed))
    assert len(BC.read_capture(_Store(_bars("2026-11-25", 5)))) == 5
