"""W2 research windows (ADR research-windows-w2): frozen spans, entry-time rule, test_old flag, embargo, holdout sealed."""
from __future__ import annotations

import pandas as pd
import pytest

from goldbot.data.resample import BAR_COLUMNS
from goldbot.research import windows as w

U = w._utc


def _year_file(folder, year: int, end: str | None = None, flag_first: str = "") -> int:
    ts = pd.date_range(f"{year}-01-01", end or f"{year}-12-31 23:00", freq="6h", tz="UTC")
    df = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1)})
    for c in BAR_COLUMNS[2:]:
        df[c] = 1000.0
    df["dq_flag"] = ""
    df.loc[0, "dq_flag"] = flag_first
    df.to_parquet(folder / f"xauusd_1m_dukascopy_{year}.parquet", index=False)
    return len(df)


def test_frozen_spans_match_amendment_w2():
    assert w.bounds("learn_old") == (U("2003-01-01"), U("2006-01-01"))
    assert w.bounds("test_old", allow_test_old=True) == (U("2006-02-01"), U("2010-01-01"))
    assert w.bounds("learn") == (U("2010-01-01"), U("2018-01-01"))
    assert w.bounds("confirm") == (U("2018-01-01"), U("2025-10-01"))
    assert w.years("learn_old") == (2003, 2004, 2005)
    assert w.years("test_old", allow_test_old=True) == (2006, 2007, 2008, 2009)
    assert w.years("learn") == tuple(range(2010, 2018))
    assert w.years("confirm") == tuple(range(2018, 2026))


def test_spans_tile_2003_to_the_holdout_without_hole_or_overlap():
    spans = list(w.SPANS.values())
    assert spans[0][0] == w.FIRST_BAR_FROM and spans[-1][1] == w.HOLDOUT_FROM
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))


def test_holdout_constant_agrees_with_the_rest_of_research():
    from goldbot.research.arms import HOLDOUT_FROM
    from goldbot.research.exploration_data import HOLDOUT_START
    assert w.HOLDOUT_FROM == HOLDOUT_FROM == HOLDOUT_START


def test_test_old_needs_the_explicit_flag():
    for call in (lambda: w.years("test_old"), lambda: w.bounds("test_old"),
                 lambda: w.entry_mask([U("2008-03-01")], "test_old")):
        with pytest.raises(w.TestOldSealed):
            call()


@pytest.mark.parametrize("flag", [False, True])
def test_holdout_and_embargo_are_never_returned(flag):
    with pytest.raises(w.HoldoutSealed):
        w.years("holdout", allow_test_old=flag)
    with pytest.raises(w.EmbargoSealed):
        w.years("embargo", allow_test_old=flag)


def test_unknown_window_is_refused():
    with pytest.raises(ValueError, match="unknown window"):
        w.years("test")


@pytest.mark.parametrize("ts,expected", [
    ("2003-05-05 00:00", "learn_old"), ("2005-12-31 23:59", "learn_old"), ("2006-01-15", "embargo"),
    ("2006-02-01 00:00", "test_old"), ("2008-09-15", "test_old"), ("2009-12-31 23:59", "test_old"),
    ("2010-01-01", "learn"), ("2017-12-31 23:59", "learn"), ("2018-01-01", "confirm"),
    ("2025-09-30 23:59:59", "confirm"), ("2025-10-01", "holdout"), ("2026-03-01", "holdout"),
])
def test_entry_time_decides_the_window(ts, expected):
    assert w.window_of(ts) == expected


def test_window_of_converts_other_timezones_to_utc():
    # 2009-12-31 22:00 New York is 2010-01-01 03:00 UTC: the entry belongs to learn, not test_old
    assert w.window_of(pd.Timestamp("2009-12-31 22:00", tz="America/New_York")) == "learn"


def test_window_of_refuses_dates_before_the_first_window():
    with pytest.raises(ValueError):
        w.window_of("2002-06-01")


def test_entry_mask_keeps_only_entries_in_the_window():
    ts = pd.Series(pd.to_datetime(["2005-12-31 23:00", "2006-01-20 00:00", "2006-02-01 00:00", "2025-09-30 00:00", "2025-10-01 00:00"], utc=True))
    assert w.entry_mask(ts, "learn_old").tolist() == [True, False, False, False, False]
    assert w.entry_mask(ts, "test_old", allow_test_old=True).tolist() == [False, False, True, False, False]
    assert w.entry_mask(ts, "confirm").tolist() == [False, False, False, True, False]   # 2025-10-01 is holdout


def test_read_window_bars_cuts_confirm_at_the_holdout(tmp_path):
    _year_file(tmp_path, 2025)
    _year_file(tmp_path, 2026, end="2026-06-30")
    b = w.read_window_bars(tmp_path, "confirm", warmup_days=w.MAX_WARMUP_DAYS)
    assert b["ts_utc"].max() < w.HOLDOUT_FROM and len(b) > 200
    assert list(b.columns) == BAR_COLUMNS


def test_read_window_bars_keeps_the_span_and_the_seals(tmp_path):
    for y in range(2003, 2011):
        _year_file(tmp_path, y)
    lo = w.read_window_bars(tmp_path, "learn_old")
    assert lo["ts_utc"].min() >= U("2003-01-01") and lo["ts_utc"].max() < U("2006-01-01")
    with pytest.raises(w.TestOldSealed):
        w.read_window_bars(tmp_path, "test_old")
    t = w.read_window_bars(tmp_path, "test_old", allow_test_old=True)
    assert t["ts_utc"].min() == U("2006-02-01") and t["ts_utc"].max() < U("2010-01-01")
    for sealed, exc in (("holdout", w.HoldoutSealed), ("embargo", w.EmbargoSealed)):
        with pytest.raises(exc):
            w.read_window_bars(tmp_path, sealed, allow_test_old=True)


def test_warmup_reads_earlier_bars_but_entries_stay_in_window(tmp_path):
    for y in (2009, 2010):
        _year_file(tmp_path, y)
    b = w.read_window_bars(tmp_path, "learn", warmup_days=30)
    pre = b[b["ts_utc"] < U("2010-01-01")]
    assert len(pre) and pre["ts_utc"].min() >= U("2009-12-02")
    assert not w.entry_mask(pre["ts_utc"], "learn").any()
    with pytest.raises(ValueError):
        w.read_window_bars(tmp_path, "learn", warmup_days=w.MAX_WARMUP_DAYS + 1)


def test_available_years_is_as_on_disk(tmp_path):
    _year_file(tmp_path, 2004)
    _year_file(tmp_path, 2005)
    assert w.available_years(tmp_path, "learn_old") == (2004, 2005)
    with pytest.raises(FileNotFoundError):
        w.read_window_bars(tmp_path, "learn")


def test_test_old_opens_only_for_confirm_passed_ids():
    assert w.allow_test_old_for("H-80", {"H-80", "H-12"}) is True
    with pytest.raises(w.TestOldSealed):
        w.allow_test_old_for("H-99", {"H-80"})
    assert w.years("test_old", allow_test_old=w.allow_test_old_for("H-80", ["H-80"])) == (2006, 2007, 2008, 2009)


def test_error_rows_are_dropped_like_every_research_read(tmp_path):
    n = _year_file(tmp_path, 2011, flag_first="error:bid_gt_ask")
    b = w.read_window_bars(tmp_path, "learn")
    assert len(b) == n - 1
