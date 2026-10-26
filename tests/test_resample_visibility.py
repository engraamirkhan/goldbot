"""A resampled bar is visible no earlier than its latest constituent minute (point in time).

2026-10-26: corrected icm minutes were stored with visible_at = the time they were written (data/quarantine.py), but
resampling gave a 15m/1h/4h/1d bar its natural close as visible_at, so replay and research saw the corrected minutes
inside higher-timeframe bars before they existed. A resampled bar's visible_at is now the later of its natural close
and its minutes' visible_at; for ordinary minutes (visible_at = ts_utc + 60 s) nothing changes."""
from __future__ import annotations

import pandas as pd
import pytest

from goldbot.data.resample import IncrementalResampler, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks

LATE = pd.Timestamp("2026-10-09 15:00", tz="UTC")


@pytest.fixture(scope="module")
def m1() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2026-10-05 22:00", "2026-10-08 21:00", ticks_per_minute=3, seed=3))


@pytest.mark.parametrize("tf", ["15m", "1h", "4h", "1d", "1w"])
def test_ordinary_minutes_resample_exactly_as_before(m1, tf):
    out = resample_bars(m1, tf)
    assert (pd.to_datetime(m1["visible_at"], utc=True) == m1["ts_utc"] + pd.Timedelta(minutes=1)).all()
    natural = {"15m": 900, "1h": 3600, "4h": 14400}
    if tf in natural:
        assert (out["visible_at"] == out["ts_utc"] + pd.Timedelta(seconds=natural[tf])).all()
    assert out["visible_at"].dtype == resample_bars(m1.drop(columns=["visible_at"]).assign(
        visible_at=m1["visible_at"]), tf)["visible_at"].dtype


@pytest.mark.parametrize("tf", ["15m", "1h", "4h", "1d"])
def test_a_late_visible_minute_makes_its_higher_timeframe_bar_late_and_only_that_bar(m1, tf):
    i = 700
    late = m1.copy()
    late.loc[i, "visible_at"] = LATE
    before, after = resample_bars(m1, tf), resample_bars(late, tf)
    pd.testing.assert_frame_equal(before.drop(columns=["visible_at"]), after.drop(columns=["visible_at"]))
    changed = before["visible_at"] != after["visible_at"]
    assert changed.sum() == 1
    row = after[changed].iloc[0]
    assert row["visible_at"] == LATE and row["ts_utc"] <= m1["ts_utc"].iloc[i] < row["ts_utc"] + pd.Timedelta(days=2)


def test_the_incremental_resampler_carries_late_visibility_too(m1):
    late = m1.copy()
    late.loc[len(late) - 5, "visible_at"] = LATE
    inc = IncrementalResampler("1h")
    inc(late.iloc[:-30])
    pd.testing.assert_frame_equal(inc(late), resample_bars(late, "1h"))
    # a minute whose visibility is revised in place is not served from the cache
    again = late.copy()
    again.loc[100, "visible_at"] = LATE
    pd.testing.assert_frame_equal(inc(again), resample_bars(again, "1h"))
