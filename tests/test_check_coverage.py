"""scripts/check_coverage.py (W1-6, G1/G2): day-by-day coverage of a published 1m bar file against the session
calendar. A month that clears 15,000 bars can still have lost whole weekdays (coverage triage 2026-10-16)."""
import datetime as dt
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS

_spec = importlib.util.spec_from_file_location("check_coverage", Path(__file__).resolve().parents[1] / "scripts" / "check_coverage.py")
assert _spec is not None and _spec.loader is not None
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def _session_bars(start: str, end: str, drop: tuple[str, ...] = ()) -> pd.DataFrame:
    ts = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    ts = ts[DEFAULT_SESSIONS.is_open(ts)]
    keep = ~pd.Index(ts.strftime("%Y-%m-%d")).isin(drop)
    return pd.DataFrame({"ts_utc": ts[np.asarray(keep)]})


def test_good_friday_dates():
    assert cc.good_friday(2024) == dt.date(2024, 3, 29)
    assert cc.good_friday(2025) == dt.date(2025, 4, 18)
    assert cc.good_friday(2026) == dt.date(2026, 4, 3)


def test_closures_are_expected_not_gaps():
    closures = cc.closure_days(2024)
    assert {dt.date(2024, 1, 1), dt.date(2024, 3, 29), dt.date(2024, 12, 25), dt.date(2024, 12, 26)} <= closures
    # a weekend holiday moves to the neighbouring weekdays (2021-12-25 was a Saturday)
    assert {dt.date(2021, 12, 24), dt.date(2021, 12, 27)} <= cc.closure_days(2021)


def test_a_lost_weekday_inside_a_passing_month_is_a_gap():
    # 2024-01: three lost weekdays (10..12 Jan, as in data-v1) still leave >15,000 bars, so the month "passed"
    bars = _session_bars("2024-01-01", "2024-02-01", drop=("2024-01-01", "2024-01-10", "2024-01-11", "2024-01-12"))
    assert len(bars) > 15_000
    rep = cc.coverage(bars, dt.date(2024, 1, 1), dt.date(2024, 1, 31))
    gaps = rep[rep["status"] == "gap"]
    assert [d.isoformat() for d in gaps["day"]] == ["2024-01-10", "2024-01-11", "2024-01-12"]
    assert rep.loc[rep["day"] == dt.date(2024, 1, 1), "status"].item() == "closure"
    assert cc.gap_months(rep) == ["2024-01"]


def test_a_short_day_below_the_floor_is_a_gap_and_a_full_month_is_clean():
    bars = _session_bars("2025-06-01", "2025-07-01")
    rep = cc.coverage(bars, dt.date(2025, 6, 1), dt.date(2025, 6, 30))
    assert (rep["status"] != "gap").all() and cc.gap_months(rep) == []
    # keep only 2 h of 2025-06-11: below the 700-bar floor -> gap
    day = bars["ts_utc"].dt.strftime("%Y-%m-%d") == "2025-06-11"
    thin = pd.concat([bars[~day], bars[day].iloc[:120]])
    rep = cc.coverage(thin, dt.date(2025, 6, 1), dt.date(2025, 6, 30))
    assert rep.loc[rep["status"] == "gap", "day"].tolist() == [dt.date(2025, 6, 11)]
    assert rep.loc[rep["day"] == dt.date(2025, 6, 11), "bars"].item() == 120


def test_a_stale_file_shows_its_missing_tail():
    # the 2026 file stopped at 2026-10-05; every later weekday up to the check date is a gap
    bars = _session_bars("2026-10-01", "2026-10-06")
    rep = cc.coverage(bars, dt.date(2026, 10, 1), dt.date(2026, 10, 9))
    assert [d.isoformat() for d in rep.loc[rep["status"] == "gap", "day"]] == [
        "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]


def test_epoch_units_do_not_change_the_count():
    # pandas 3 keeps s/ms/us units; the day bucketing must not depend on them
    bars = _session_bars("2025-06-02", "2025-06-03")
    rep_ns = cc.coverage(bars, dt.date(2025, 6, 2), dt.date(2025, 6, 2))
    rep_s = cc.coverage(bars.assign(ts_utc=bars["ts_utc"].dt.as_unit("s")), dt.date(2025, 6, 2), dt.date(2025, 6, 2))
    assert rep_ns["bars"].tolist() == rep_s["bars"].tolist() and rep_ns["bars"].item() > 1_000


def test_main_exit_status_and_report(tmp_path, capsys):
    bars = _session_bars("2024-01-01", "2024-02-01", drop=("2024-01-10",)).assign(source="dukascopy")
    f = tmp_path / "xauusd_1m_dukascopy_2024.parquet"
    bars.to_parquet(f, index=False)
    rep = tmp_path / "cov.md"
    assert cc.main([str(f), "--to", "2024-01-31", "--report", str(rep)]) == 1
    text = rep.read_text()
    assert "2024-01-10" in text and "refetch months: 2024-01" in text
    clean = tmp_path / "clean.parquet"
    _session_bars("2025-06-01", "2025-07-01").to_parquet(clean, index=False)
    assert cc.main([str(clean), "--from", "2025-06-01", "--to", "2025-06-30"]) == 0
