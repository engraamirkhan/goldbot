"""ADR 0105 (ii) capture guard: due day, missed-day FAIL the same day, month copy within 5 days, job and check."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data import broker_capture as BC


def _bars(day: str, minutes: int) -> pd.DataFrame:
    lo, _ = BC.session_bounds(dt.date.fromisoformat(day))
    return pd.DataFrame({"ts_utc": lo + pd.to_timedelta(range(minutes), unit="min"), "bid_close": 2000.0})


def test_the_due_day_is_the_last_weekday_session_closed_before_2350_utc() -> None:
    assert BC.due_day(pd.Timestamp("2026-11-26 23:49", tz="UTC")) == dt.date(2026, 11, 25)
    assert BC.due_day(pd.Timestamp("2026-11-26 23:51", tz="UTC")) == dt.date(2026, 11, 26)
    assert BC.due_day(pd.Timestamp("2026-11-29 12:00", tz="UTC")) == dt.date(2026, 11, 27)    # Sunday -> Friday
    assert BC.due_day(pd.Timestamp("2026-12-26 12:00", tz="UTC")) == dt.date(2026, 12, 24)    # 25 Dec skipped
    assert BC.due_day(pd.Timestamp("2026-09-30 23:59", tz="UTC")) is None                      # before the capture


def _october_copied(raw: Path) -> None:
    p = BC.month_file(raw, BC.REFERENCE_ACCOUNT, "2026-10")
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ts_utc": [pd.Timestamp("2026-10-01", tz="UTC")]}).to_parquet(p)


def test_a_missed_session_day_fails_the_same_day(tmp_path: Path) -> None:
    now = pd.Timestamp("2026-11-26 23:52", tz="UTC")
    _october_copied(tmp_path)
    st, why = BC.judge(_bars("2026-11-26", 300), now, raw_dir=tmp_path)
    assert st == "fail" and "capture missed" in why and "300 of 1380" in why
    st, why = BC.judge(_bars("2026-11-26", 1300), now, raw_dir=tmp_path)
    assert st == "ok" and "1300 of 1380" in why


def test_a_closed_month_copy_is_due_within_five_days(tmp_path: Path) -> None:
    full = _bars("2026-11-05", 1380)
    assert BC.missing_copies(pd.Timestamp("2026-11-05 12:00", tz="UTC"), raw_dir=tmp_path) == []   # Oct not due
    assert BC.missing_copies(pd.Timestamp("2026-11-06 01:00", tz="UTC"), raw_dir=tmp_path) == ["2026-10"]
    october = pd.DataFrame({"ts_utc": pd.date_range("2026-10-01", periods=10, freq="min", tz="UTC"), "bid_close": 1.0})
    out = BC.copy_months(pd.concat([october, full]), pd.Timestamp("2026-11-05 23:55", tz="UTC"), raw_dir=tmp_path)
    assert set(out["files"]) == {"icm-demo_m1_2026-10.parquet", "icm-demo_m1_2026-11.parquet"}
    sums = (tmp_path / "icm-demo" / "SHA256SUMS").read_text()
    assert "icm-demo_m1_2026-10.parquet" in sums and (tmp_path / "icm-demo" / "gaps.json").exists()
    assert BC.missing_copies(pd.Timestamp("2026-11-06 01:00", tz="UTC"), raw_dir=tmp_path) == []


class _Store:
    def __init__(self, bars: pd.DataFrame) -> None:
        self.bars = bars
        self.calls: list[dict[str, Any]] = []

    def read(self, table: str, **kw: Any) -> pd.DataFrame:
        self.calls.append({"table": table, **kw})
        return self.bars


def test_the_copy_job_reads_only_the_reference_account_and_fails_on_a_missed_day(tmp_path: Path,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from goldbot.ops import jobs
    monkeypatch.setattr(BC, "RAW_DIR", tmp_path)
    _october_copied(tmp_path)
    ok = _Store(_bars("2026-11-26", 1380))
    out = jobs.broker_m1_copy(type("Ctx", (), {"store": ok})(), pd.Timestamp("2026-11-26 23:55", tz="UTC"))
    assert ok.calls[0]["table"] == "bars_1m_broker" and ok.calls[0]["source"] == "icm-demo"
    assert out["rows"] == 1380 and "1380 of 1380" in out["capture"]
    with pytest.raises(RuntimeError, match="capture missed"):
        jobs.broker_m1_copy(type("Ctx", (), {"store": _Store(_bars("2026-11-26", 10))})(),
                            pd.Timestamp("2026-11-26 23:55", tz="UTC"))


def test_the_check_is_registered_in_the_learning_family() -> None:
    from goldbot.ops import health
    from goldbot.ops.checks import capture
    assert health.check_broker_m1_capture is capture.check_broker_m1_capture
    assert capture.NAME == "learning:broker_m1_capture"
