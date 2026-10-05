import datetime as dt
import importlib.util
from pathlib import Path

import pandas as pd

_spec = importlib.util.spec_from_file_location("dukascopy_year", Path(__file__).resolve().parents[1] / "scripts" / "dukascopy_year.py")
assert _spec is not None and _spec.loader is not None
dy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dy)


def test_month_range_end_is_exclusive_first_of_next_month():
    months = list(dy.month_range(2025, today=dt.date(2026, 1, 15)))
    assert len(months) == 12
    assert months[0] == (dt.date(2025, 1, 1), dt.date(2025, 2, 1))
    assert months[-1] == (dt.date(2025, 12, 1), dt.date(2026, 1, 1))


def test_month_range_current_month_stops_before_today():
    # today's file is not published until the day closes
    months = list(dy.month_range(2026, today=dt.date(2026, 3, 10)))
    assert len(months) == 3
    assert months[-1] == (dt.date(2026, 3, 1), dt.date(2026, 3, 10))
    # on the 1st there is no completed day in the new month, so it is not requested (was reported MISSING)
    assert list(dy.month_range(2026, today=dt.date(2026, 10, 1)))[-1] == (dt.date(2026, 9, 1), dt.date(2026, 10, 1))


def test_week_chunks_cover_range_and_skip_weekend_only_chunks():
    # May 2021: 1st is a Saturday, 31st a Monday
    start, end = dt.date(2021, 5, 1), dt.date(2021, 6, 1)
    chunks = list(dy.week_chunks(start, end))
    assert chunks[0][0] == start and chunks[-1][1] == end
    for (a, b), (c, _) in zip(chunks, chunks[1:]):
        assert b == c
    assert all((b - a).days <= 7 for a, b in chunks)
    # a chunk that is only Sat+Sun is dropped
    assert list(dy.week_chunks(dt.date(2021, 5, 1), dt.date(2021, 5, 3))) == []


def _bars(month: str, n: int, flag: str = "") -> pd.DataFrame:
    ts = pd.date_range(f"{month}-01 01:00", periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "dq_flag": flag})


def test_months_to_fetch_keeps_complete_months_only():
    months = list(dy.month_range(2022, today=dt.date(2023, 6, 1)))
    existing = pd.concat([
        _bars("2022-01", 20_000),                               # complete -> keep
        _bars("2022-02", 3_000),                                # short -> refetch
        _bars("2022-04", 20_000, "warning:partial_month"),      # flagged partial -> refetch
        _bars("2022-05", 20_000, "warning:ask_imputed"),        # imputed side -> refetch
    ])
    need = dy.months_to_fetch(existing, months, today=dt.date(2023, 6, 1))
    assert "2022-01" not in need
    assert {"2022-02", "2022-03", "2022-04", "2022-05", "2022-12"} <= need


def test_months_to_fetch_always_refreshes_open_month():
    today = dt.date(2026, 3, 10)
    months = list(dy.month_range(2026, today=today))
    existing = pd.concat([_bars(m, 20_000) for m in ("2026-01", "2026-02", "2026-03")])
    assert dy.months_to_fetch(existing, months, today=today) == {"2026-03"}
    assert dy.months_to_fetch(None, months, today=today) == {"2026-01", "2026-02", "2026-03"}
