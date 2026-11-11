"""The Parquet store reads the same on duckdb 1.x and 2.x (backlog 156).

duckdb 2.0 reads a Parquet timestamp written as pandas datetime64[ns, UTC] as TIMESTAMPTZ_NS: comparing it with a bound
parameter raised a BinderException, and `.df()` returned it naive. 1.x read it as TIMESTAMPTZ (microseconds) and returned
it in the session time zone (the host's, so Europe/London on the Mac). The contract on both: every tz-aware column comes
back tz-aware UTC at microsecond resolution, naive columns stay naive, ts filters still prune row groups.
"""
from __future__ import annotations

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from goldbot.data.store import Store, _read_query


def _utc_us(s: pd.Series) -> bool:
    return isinstance(s.dtype, pd.DatetimeTZDtype) and str(s.dtype.tz) == "UTC" and s.dtype.unit == "us"


def _ns_rows(start: str, periods: int, freq: str = "1h") -> pd.DataFrame:
    ts = pd.date_range(start, periods=periods, freq=freq, tz="UTC").as_unit("ns")
    return pd.DataFrame({
        "ts_utc": ts,
        "available_utc": (ts + pd.Timedelta(minutes=5)).as_unit("ns"),
        "value_date": ts.tz_localize(None).as_unit("ns"),                # a naive column must stay naive
        "close": range(periods),
    })


@pytest.fixture
def store(tmp_path) -> Store:
    s = Store(tmp_path / "data")
    # two months in ns, one month in us: the mix every real table holds (pandas 3 keeps the writer's unit)
    s.append("bars_1h", _ns_rows("2024-01-31 20:00", 8), source="t")
    us = _ns_rows("2024-03-01", 3)
    us["ts_utc"] = us["ts_utc"].dt.as_unit("us")
    s.append("bars_1h", us, source="t")
    return s


def test_ts_filters_bind_on_ns_files_and_return_utc(store: Store):
    df = store.read("bars_1h", source="t", start="2024-01-31 22:00", end="2024-02-01 02:00")
    assert df["close"].tolist() == [2, 3, 4, 5]
    assert _utc_us(df["ts_utc"]) and _utc_us(df["available_utc"])
    assert df["ts_utc"].iloc[0] == pd.Timestamp("2024-01-31 22:00", tz="UTC")
    assert not isinstance(df["value_date"].dtype, pd.DatetimeTZDtype)
    assert df["value_date"].iloc[0] == pd.Timestamp("2024-01-31 22:00")     # UTC wall time, not shifted


def test_a_read_across_ns_and_us_months_keeps_order_and_bounds(store: Store):
    df = store.read("bars_1h", source="t", start="2024-02-01 03:00")
    assert df["close"].tolist() == [7, 0, 1, 2]
    assert df["ts_utc"].is_monotonic_increasing
    assert _utc_us(df["ts_utc"])


def test_sql_returns_utc_whatever_the_host_zone(store: Store):
    df = store.sql("SELECT ts_utc, available_utc, value_date FROM bars_1h ORDER BY ts_utc")
    assert _utc_us(df["ts_utc"]) and _utc_us(df["available_utc"])
    assert not isinstance(df["value_date"].dtype, pd.DatetimeTZDtype)
    last = store.sql("SELECT max(ts_utc) AS t FROM bars_1h")["t"].iloc[0]
    assert last == pd.Timestamp("2024-03-01 02:00", tz="UTC") and str(last.tz) == "UTC"


def test_sql_keeps_duplicate_column_names_of_a_join(store: Store):
    df = store.sql("SELECT a.ts_utc, b.ts_utc FROM bars_1h a JOIN bars_1h b USING (close) ORDER BY 1")
    assert df.shape == (17, 2)
    assert all(_utc_us(df.iloc[:, i]) for i in range(2))


def test_sub_microsecond_ticks_truncate_like_1x(tmp_path):
    s = Store(tmp_path / "data")
    s.append("ticks", pd.DataFrame({"ts_utc": pd.to_datetime(["2024-05-01 10:00:00.123456789"], utc=True),
                                    "bid": [1.0], "ask": [1.1]}), source="t")
    t = s.read("ticks", source="t", start="2024-05-01 10:00:00.123456", end="2024-05-01 10:00:00.123457")
    assert t["ts_utc"].tolist() == [pd.Timestamp("2024-05-01 10:00:00.123456", tz="UTC")]


def test_ts_filter_is_pushed_into_the_parquet_scan(tmp_path):
    """Row-group pruning: the ts bound sits in the scan's filters, so a narrow window skips the other row groups."""
    tdir = tmp_path / "data" / "bars_1m" / "source=t" / "symbol=XAUUSD" / "year=2024" / "month=01"
    tdir.mkdir(parents=True)
    ts = pd.date_range("2024-01-01", periods=200_000, freq="10s", tz="UTC").as_unit("ns")
    pq.write_table(pa.Table.from_pandas(pd.DataFrame({"ts_utc": ts, "close": 1.0}), preserve_index=False),
                   tdir / "part-a.parquet", row_group_size=10_000)
    s = Store(tmp_path / "data")
    sql, params = _read_query(s, "bars_1m", source="t", symbol="XAUUSD", start="2024-01-10", end="2024-01-10 06:00",
                              columns=None)
    con = duckdb.connect()
    plan = con.execute("EXPLAIN ANALYZE " + sql, params).fetchall()[0][1]
    scan = plan[plan.index("_SCAN") if "_SCAN" in plan else plan.index("Table Scan"):]
    assert "Filters" in scan and "ts_utc" in scan
    if duckdb.__version__.startswith("2"):            # 2.x prints the pruned count; 1.x elides the no-op cast
        assert "Row Groups Scanned: 1 / 20" in plan or "Row Groups Scanned: 2 / 20" in plan
    assert len(s.read("bars_1m", source="t", start="2024-01-10", end="2024-01-10 06:00")) == 2160
