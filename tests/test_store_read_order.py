"""Backlog 162: rows that share a ts_utc come back from `Store.read` in one order, run after run.

DuckDB scans part files in parallel, so `ORDER BY ts_utc` alone left ties (several COT series on one date, several dq
events in one minute, two sources' bars in one read) in whatever order the threads finished: measured 2026-11-11,
25-29 % of positioning rows and 37 % of dq_events rows moved between two reads on 1.5.6. A consumer taking
`.iloc[-1]`, `drop_duplicates(keep="last")` or a first match per ts could then differ between two replays of the same
data. The contract: ts_utc, then the table's key columns (store.KEY_COLUMNS), then every column; and the first/last
pick per key after a sort on `available_utc` uses a stable sort (macro.first_release, positioning.merge_vintages).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.data import positioning as pos
from goldbot.data.macro import first_release, merge_vintages
from goldbot.data.store import Store

DAY = pd.Timestamp("2024-03-01", tz="UTC")


def _many_parts(store: Store, table: str, frame: pd.DataFrame, *, parts: int, source: str, symbol: str = "XAUUSD",
                dedupe: bool = False) -> None:
    """`frame` written as `parts` part files (shuffled): a parallel scan finishes them in any order."""
    rng = np.random.default_rng(7)
    idx = rng.permutation(len(frame))
    for chunk in np.array_split(idx, parts):
        store.append(table, frame.iloc[chunk], source=source, symbol=symbol, dedupe=dedupe)


def _signature(df: pd.DataFrame) -> list[tuple]:
    return list(df.astype(str).itertuples(index=False, name=None))


def _ten(read) -> list[list[tuple]]:
    return [_signature(read()) for _ in range(10)]


def test_ten_reads_of_dq_events_sharing_minutes_give_one_order(tmp_path):
    store = Store(tmp_path / "data")
    n = 4000
    ev = pd.DataFrame({"ts_utc": [DAY + pd.Timedelta(minutes=i // 20) for i in range(n)],     # 20 events per minute
                       "check": [f"c{i % 7}" for i in range(n)], "severity": "warning",
                       "detail": [f"d{(i * 7919) % n}" for i in range(n)]})
    _many_parts(store, "dq_events", ev, parts=40, source="icm-demo")
    orders = _ten(lambda: store.read("dq_events", source="icm-demo"))
    assert all(o == orders[0] for o in orders)
    got = store.read("dq_events", source="icm-demo")
    assert got["ts_utc"].is_monotonic_increasing and len(got) == n
    # the order inside a minute is every column's, left to right (dq_events has no key columns)
    want = got.sort_values(["ts_utc", "check", "severity", "detail"], kind="stable").reset_index(drop=True)
    pd.testing.assert_frame_equal(got, want)


def test_ten_reads_of_positioning_with_series_on_one_date_give_the_key_order(tmp_path):
    store = Store(tmp_path / "data")
    rows = []
    for d in range(60):
        vd = pd.Timestamp("2024-01-02") + pd.Timedelta(weeks=d)
        avail = pd.Timestamp(vd, tz="UTC") + pd.Timedelta(days=3, hours=20, minutes=30)
        for s in range(30):
            rows.append({"series": f"cot_s{(s * 17) % 30:02d}", "value_date": vd, "vintage": "first",
                         "value": float(s), "available_utc": avail, "ts_utc": avail})
    frame = pd.DataFrame(rows)
    for k in range(20):                          # keyed table: one part per source call, 20 sources in one read
        store.append("positioning", frame.iloc[k::20], source=f"src{k:02d}", symbol="XAUUSD")
    orders = _ten(lambda: store.read("positioning", source=None))
    assert all(o == orders[0] for o in orders)
    got = store.read("positioning", source=None)
    keyed = got.sort_values(["ts_utc", "series", "value_date", "vintage"], kind="stable").reset_index(drop=True)
    pd.testing.assert_frame_equal(got, keyed)


def test_ten_reads_of_two_sources_bars_in_one_read_give_one_order(tmp_path):
    store = Store(tmp_path / "data")
    ts = pd.date_range("2024-03-01", periods=3000, freq="1min", tz="UTC")
    for src, off in (("a", 0.0), ("b", 0.5), ("c", 0.25)):
        bars = pd.DataFrame({"ts_utc": ts, "bid_close": np.arange(len(ts)) + off})
        _many_parts(store, "bars_1m", bars, parts=12, source=src)
    orders = _ten(lambda: store.read("bars_1m", source=None, symbol=None))
    assert all(o == orders[0] for o in orders)
    got = store.read("bars_1m", source=None, symbol=None)
    assert got.groupby("ts_utc")["source"].apply(list).map(lambda s: s == ["a", "c", "b"]).all() or \
        got.groupby("ts_utc")["bid_close"].apply(lambda s: s.is_monotonic_increasing).all()


def test_a_tied_read_with_a_column_subset_and_a_window_is_ordered_too(tmp_path):
    store = Store(tmp_path / "data")
    ev = pd.DataFrame({"ts_utc": [DAY] * 500, "check": [f"c{(i * 37) % 500:03d}" for i in range(500)],
                       "severity": "warning", "detail": "x"})
    _many_parts(store, "dq_events", ev, parts=25, source="s")
    orders = _ten(lambda: store.read("dq_events", source="s", columns=["ts_utc", "check"], start=DAY,
                                     end=DAY + pd.Timedelta(minutes=1)))
    assert all(o == orders[0] for o in orders)
    assert store.read("dq_events", source="s", columns=["check"])["check"].is_monotonic_increasing


@pytest.mark.parametrize("seed", range(5))
def test_first_release_takes_the_earliest_row_in_input_order_on_tied_availability(seed):
    """Two rows of one value_date available at the same instant: the first in the input (the store's order) wins,
    whatever pandas' default sort would do with ties."""
    rng = np.random.default_rng(seed)
    n = 400
    avail = pd.Timestamp("2024-01-05 15:00", tz="UTC")
    macro = pd.DataFrame({"series": "s", "value_date": [pd.Timestamp("2024-01-01") + pd.Timedelta(days=i // 2)
                                                        for i in range(n)],
                          "value": np.arange(n, dtype=float),
                          "available_utc": [avail + pd.Timedelta(hours=int(h)) for h in rng.integers(0, 3, n)]})
    out = first_release(macro, "s")
    want = macro.sort_values("available_utc", kind="stable").drop_duplicates("value_date", keep="first")
    assert out["value"].tolist() == want.sort_values("value_date")["value"].tolist()


@pytest.mark.parametrize("seed", range(5))
def test_merge_releases_compares_with_the_last_row_in_input_order_on_tied_availability(seed):
    """The last published value per (series, value_date) among rows available at the same instant is the last one
    in the store's order (stable sort): a revision check never flips between replays."""
    rng = np.random.default_rng(seed)
    base = pd.Timestamp("2024-01-05 20:30", tz="UTC")
    n = 300
    avail = [base + pd.Timedelta(hours=int(h)) for h in rng.integers(0, 3, n)]
    prev = pd.DataFrame({"series": "cot_mm_long", "series_id": "x", "value_date": [pd.Timestamp("2024-01-02")] * n,
                         "value": np.arange(n, dtype=float), "vintage": pd.Timestamp("2024-01-05"),
                         "available_utc": avail, "ts_utc": avail, "source": "cftc_disagg"})
    last = prev.sort_values("available_utc", kind="stable").iloc[[-1]].reset_index(drop=True)
    out = pos.merge_releases(prev[list(pos.COLUMNS)], last[list(pos.COLUMNS)],   # the same value: no revision
                             retrieved_utc=base + pd.Timedelta(days=1))
    assert len(out) == n


@pytest.mark.parametrize("seed", range(5))
def test_macro_merge_vintages_compares_with_the_last_row_in_input_order_on_tied_availability(seed):
    """Quant Q-C1, the macro twin of the merge_releases test: the last value per (series, value_date) among rows
    available at the same instant is the last in the store's order, so an unchanged download adds no revision."""
    rng = np.random.default_rng(seed)
    base = pd.Timestamp("2024-01-05 23:00", tz="UTC")
    n = 300
    avail = [base + pd.Timedelta(hours=int(h)) for h in rng.integers(0, 3, n)]
    prev = pd.DataFrame({"series": "dgs10", "value_date": [pd.Timestamp("2024-01-02")] * n,
                         "value": np.arange(n, dtype=float), "vintage": pd.Timestamp("2024-01-05"),
                         "available_utc": avail, "ts_utc": avail})
    last = prev.sort_values("available_utc", kind="stable").iloc[[-1]].reset_index(drop=True)
    assert len(merge_vintages(prev, last)) == n


def test_a_store_root_with_a_quote_in_its_path_reads(tmp_path):
    """Code review LOW: the scan's paths are SQL string literals, a quote in them is escaped."""
    store = Store(tmp_path / "o'brien" / "data")
    store.append("dq_events", pd.DataFrame({"ts_utc": [DAY], "check": ["c"], "severity": ["warning"],
                                            "detail": ["x"]}), source="s", dedupe=False)
    assert len(store.read("dq_events", source="s")) == 1
    assert len(store.read("dq_events", source="s", columns={"check": "VARCHAR"}, folder_only=True)) == 1
