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
    need = dy.months_to_fetch(existing, months, today=dt.date(2023, 6, 1), retry_flagged=True)
    assert "2022-01" not in need
    assert {"2022-02", "2022-03", "2022-04", "2022-05", "2022-12"} <= need


def test_months_to_fetch_keeps_flagged_closed_months_by_default():
    # G5: the Saturday run re-downloaded the three bid-imputed 2026 months for ~4 h a week (run 38042847605);
    # closed months that are flagged imputed/partial are kept unless a dispatch asks for the retry
    months = list(dy.month_range(2026, today=dt.date(2026, 10, 17)))
    existing = pd.concat([
        _bars("2026-01", 20_000),
        _bars("2026-02", 20_000, "warning:bid_imputed"),
        _bars("2026-03", 20_000, "warning:partial_month"),
        _bars("2026-04", 3_000),                                # short and unflagged -> still refetched
        *[_bars(f"2026-{m:02d}", 20_000) for m in range(5, 11)],
    ])
    need = dy.months_to_fetch(existing, months, today=dt.date(2026, 10, 17))
    assert need == {"2026-04", "2026-10"}


def test_months_to_fetch_always_refreshes_open_month():
    today = dt.date(2026, 3, 10)
    months = list(dy.month_range(2026, today=today))
    existing = pd.concat([_bars(m, 20_000) for m in ("2026-01", "2026-02", "2026-03")])
    assert dy.months_to_fetch(existing, months, today=today) == {"2026-03"}
    assert dy.months_to_fetch(None, months, today=today) == {"2026-01", "2026-02", "2026-03"}


def test_fractional_dukascopy_volumes_are_kept(monkeypatch, tmp_path):
    # XAUUSD volumes from Dukascopy are fractional; an int cast once turned most minutes into 0 volume
    ts = pd.date_range("2025-03-04 09:00", periods=5, freq="1min", tz="UTC")
    side = pd.DataFrame({"ts_utc": ts, "open": 2900.0, "high": 2901.0, "low": 2899.0, "close": 2900.5,
                         "volume": [0.12, 0.5, 1.7, 0.0, 0.03]})
    monkeypatch.setattr(dy, "download_side", lambda s, e, which, out: side.assign(close=2900.5 + (which == "ask") * 0.2))
    monkeypatch.setattr(dy, "MIN_BARS_PER_FULL_MONTH", 1)
    monkeypatch.setattr(dy.time, "sleep", lambda s: None)
    bars = dy.month_bars(dt.date(2025, 3, 1), dt.date(2025, 4, 1), tmp_path)
    assert bars is not None and bars["tick_count"].tolist() == [0.12, 0.5, 1.7, 0.0, 0.03]


def _day_bars(day: str, n: int, flag: str = "", price: float = 2000.0) -> pd.DataFrame:
    ts = pd.date_range(f"{day} 01:00", periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "bid_close": price, "dq_flag": flag})


def test_merge_never_shrinks_a_published_day():
    published = pd.concat([_day_bars("2026-10-05", 1000), _day_bars("2026-10-06", 1000), _day_bars("2026-10-07", 900)])
    fresh = pd.concat([
        _day_bars("2026-10-05", 400, price=1.0),     # throttled re-download: fewer bars -> published day kept
        _day_bars("2026-10-06", 1000, price=1.0),    # same count, published unflagged -> published kept (no rewrite)
        _day_bars("2026-10-07", 1100, price=1.0),    # more bars -> fresh day replaces it
        _day_bars("2026-10-08", 700, price=1.0),     # new day -> added
    ])
    out = dy.merge_never_shrink(published, fresh)
    day = out["ts_utc"].dt.strftime("%Y-%m-%d")
    counts = day.value_counts().to_dict()
    assert counts == {"2026-10-05": 1000, "2026-10-06": 1000, "2026-10-07": 1100, "2026-10-08": 700}
    assert (out.loc[day == "2026-10-05", "bid_close"] == 2000.0).all()
    assert (out.loc[day == "2026-10-06", "bid_close"] == 2000.0).all()
    assert (out.loc[day == "2026-10-07", "bid_close"] == 1.0).all()
    assert out["ts_utc"].is_monotonic_increasing and not out["ts_utc"].duplicated().any()
    pub_counts = published["ts_utc"].dt.strftime("%Y-%m-%d").value_counts()
    assert all(counts[d] >= n for d, n in pub_counts.items())


def test_merge_prefers_an_unflagged_fresh_day_of_equal_size_over_an_imputed_one():
    published = _day_bars("2026-02-03", 1000, "warning:bid_imputed")
    fresh = _day_bars("2026-02-03", 1000, price=1.0)
    out = dy.merge_never_shrink(published, fresh)
    assert (out["dq_flag"] == "").all() and (out["bid_close"] == 1.0).all()


def test_merge_with_nothing_published_returns_fresh():
    fresh = _day_bars("2026-10-08", 10)
    assert len(dy.merge_never_shrink(fresh.iloc[0:0], fresh)) == 10


def test_main_never_publishes_fewer_bars_for_a_day_than_before(monkeypatch, tmp_path):
    # end to end through main(): the open month comes back short from a throttled download; the published
    # October days must survive (before the never-shrink merge they were replaced wholesale)
    from goldbot.data.resample import BAR_COLUMNS

    def full(ts: pd.DatetimeIndex, price: float) -> pd.DataFrame:
        df = pd.DataFrame({"ts_utc": ts})
        for c in ("bid_open", "bid_high", "bid_low", "bid_close"):
            df[c] = price
        for c in ("ask_open", "ask_high", "ask_low", "ask_close"):
            df[c] = price + 0.3
        df["tick_count"] = 1.0
        df["spread_mean"], df["spread_max"] = 0.3, 0.3
        df["visible_at"] = df["ts_utc"] + pd.Timedelta(minutes=1)
        df["dq_flag"] = ""
        return df[BAR_COLUMNS + ["dq_flag"]]

    # Tue 6 and Wed 7 Oct 2026, 10:00-14:59 UTC (inside the gold session)
    days = [pd.date_range(f"2026-10-0{d} 10:00", periods=300, freq="1min", tz="UTC") for d in (6, 7)]
    published = full(pd.DatetimeIndex(days[0].append(days[1])), 2000.0).assign(source="dukascopy")
    prev = tmp_path / "prev.parquet"
    published.to_parquet(prev, index=False)
    short = full(days[0][:50], 1.0)                    # only 50 of Tuesday's 300 bars came back, no Wednesday

    monkeypatch.setattr(dy, "month_range", lambda year, today=None: iter([(dt.date(2026, 10, 1), dt.date(2026, 10, 17))]))
    monkeypatch.setattr(dy, "month_bars", lambda start, end, tmp: short)
    out = tmp_path / "out.parquet"
    monkeypatch.setattr(dy.sys, "argv", ["dukascopy_year.py", "2026", "--out", str(out), "--existing", str(prev),
                                         "--tmp", str(tmp_path / "raw"), "--report", str(tmp_path / "r.md")])
    assert dy.main() == 0
    got = pd.read_parquet(out)
    assert len(got) == 600
    assert (got["bid_close"] == 2000.0).all()
