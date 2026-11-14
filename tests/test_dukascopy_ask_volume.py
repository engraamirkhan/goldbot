"""BACKLOG 213: Dukascopy's ask-side tick volume is kept in the 1m bars as a nullable `tick_count_ask` (absent = NaN,
never 0), resample sums it like `tick_count` (a bar with any minute lacking it reads NaN), and the order-flow proxies
gain the quoted-volume imbalance `of_quote_imb_15m/60m` only where both sides exist."""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data import orderflow as of
from goldbot.data.resample import BAR_COLUMNS, OPTIONAL_BAR_COLUMNS, resample_bars
from goldbot.features import orderflow as of_feat
from tests.test_orderflow_features import _m1

_spec = importlib.util.spec_from_file_location("dukascopy_year_ask",
                                               Path(__file__).resolve().parents[1] / "scripts" / "dukascopy_year.py")
assert _spec is not None and _spec.loader is not None
dy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dy)

TS = pd.date_range("2025-03-04 09:00", periods=5, freq="1min", tz="UTC")


def _side(vol: list[float], close: float) -> pd.DataFrame:
    return pd.DataFrame({"ts_utc": TS, "open": close, "high": close + 1, "low": close - 1, "close": close,
                         "volume": vol})


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dy, "MIN_BARS_PER_FULL_MONTH", 1)
    monkeypatch.setattr(dy.time, "sleep", lambda s: None)


def test_the_ask_side_volume_is_kept_next_to_the_bid_side(monkeypatch, tmp_path, quiet):
    sides = {"bid": _side([0.12, 0.5, 1.7, 0.0, 0.03], 2900.5), "ask": _side([0.2, 0.0, 2.5, 0.4, 0.01], 2900.7)}
    monkeypatch.setattr(dy, "download_side", lambda s, e, which, out, **kw: sides[which])
    bars = dy.month_bars(dt.date(2025, 3, 1), dt.date(2025, 4, 1), tmp_path)
    assert bars is not None
    assert bars["tick_count"].tolist() == [0.12, 0.5, 1.7, 0.0, 0.03]
    assert bars["tick_count_ask"].tolist() == [0.2, 0.0, 2.5, 0.4, 0.01]


def test_an_imputed_ask_side_has_no_ask_volume(monkeypatch, tmp_path, quiet):
    """One side patchy: the other side's prices are imputed; its volume is unknown (NaN), never a copy or 0."""
    bid = pd.concat([_side([1.0] * 5, 2900.5)] * 3, ignore_index=True)
    bid["ts_utc"] = pd.date_range("2025-03-04 09:00", periods=15, freq="1min", tz="UTC")
    ask = _side([2.0] * 5, 2900.7).iloc[:2]
    monkeypatch.setattr(dy, "download_side", lambda s, e, which, out, **kw: {"bid": bid, "ask": ask}[which])
    bars = dy.month_bars(dt.date(2025, 3, 1), dt.date(2025, 4, 1), tmp_path)
    assert bars is not None and (bars["dq_flag"] == "warning:ask_imputed").all()
    assert bars["tick_count_ask"].isna().all() and (bars["tick_count"] == 1.0).all()


def test_the_year_file_keeps_the_column_and_months_built_before_it_read_nan(monkeypatch, tmp_path):
    """--months refetch: the refetched month carries tick_count_ask; a month kept from the published file has NaN."""
    def full(ts: pd.DatetimeIndex, ask: float | None) -> pd.DataFrame:
        df = pd.DataFrame({"ts_utc": ts})
        for c in ("bid_open", "bid_high", "bid_low", "bid_close"):
            df[c] = 2000.0
        for c in ("ask_open", "ask_high", "ask_low", "ask_close"):
            df[c] = 2000.3
        df["tick_count"] = 1.0
        df["spread_mean"], df["spread_max"] = 0.3, 0.3
        df["visible_at"] = df["ts_utc"] + pd.Timedelta(minutes=1)
        df["dq_flag"] = ""
        if ask is not None:
            df["tick_count_ask"] = ask
        return df

    sep = pd.date_range("2026-09-08 10:00", periods=300, freq="1min", tz="UTC")
    octo = pd.date_range("2026-10-06 10:00", periods=300, freq="1min", tz="UTC")
    prev = tmp_path / "prev.parquet"
    full(pd.DatetimeIndex(sep.append(octo)), None).assign(source="dukascopy").to_parquet(prev, index=False)
    monkeypatch.setattr(dy, "month_range", lambda year, today=None: iter(
        [(dt.date(2026, 9, 1), dt.date(2026, 10, 1)), (dt.date(2026, 10, 1), dt.date(2026, 11, 1))]))
    monkeypatch.setattr(dy, "month_bars", lambda start, end, tmp, **kw: full(octo, 0.7)[BAR_COLUMNS
                                                                                         + OPTIONAL_BAR_COLUMNS
                                                                                         + ["dq_flag"]])
    monkeypatch.setattr(dy, "MIN_BARS_PER_FULL_MONTH", 1)
    out = tmp_path / "out.parquet"
    monkeypatch.setattr(dy.sys, "argv", ["dukascopy_year.py", "2026", "--out", str(out), "--existing", str(prev),
                                         "--months", "2026-10", "--tmp", str(tmp_path / "raw")])
    dy.main()
    got = pd.read_parquet(out)
    in_oct = got["ts_utc"] >= pd.Timestamp("2026-10-01", tz="UTC")
    assert (got.loc[in_oct, "tick_count_ask"] == 0.7).all()
    assert got.loc[~in_oct, "tick_count_ask"].isna().all() and (~in_oct).sum() == 300


def _m1_ask(n: int, ask: float | np.ndarray | None, **kw) -> pd.DataFrame:
    df = _m1(n, ask=False, **kw)
    if ask is not None:
        df["tick_count_ask"] = ask
    return df


def test_resample_sums_the_ask_volume_and_a_bar_with_any_unknown_minute_reads_nan():
    m = _m1_ask(30, 2.0)
    m.loc[20, "tick_count_ask"] = np.nan
    out = resample_bars(m, "15m")
    assert out["tick_count_ask"].iloc[0] == pytest.approx(30.0)
    assert np.isnan(out["tick_count_ask"].iloc[1])
    assert "tick_count_ask" not in resample_bars(_m1(30, ask=False), "15m").columns     # absent stays absent


def test_quoted_volume_imbalance_where_both_sides_exist_only():
    m = _m1_ask(120, None)
    m["tick_count"] = 30.0
    m["tick_count_ask"] = 10.0
    f = of.orderflow_frame(m)
    assert f["of_quote_imb_15m"].iloc[-1] == pytest.approx((30 - 10) / 40)
    assert f["of_quote_imb_60m"].iloc[-1] == pytest.approx(0.5)
    m.loc[100:, "tick_count_ask"] = np.nan              # the last 20 minutes lack the ask side
    f = of.orderflow_frame(m)
    assert np.isnan(f["of_quote_imb_15m"].iloc[-1])     # a window with a one-sided minute is unknown, not skewed
    assert np.isnan(f["of_quote_imb_60m"].iloc[-1])
    assert f[["of_quote_imb_15m", "of_quote_imb_60m"]].iloc[60:99].notna().all().all()
    assert of.orderflow_frame(_m1(120, ask=False))[["of_quote_imb_15m", "of_quote_imb_60m"]].isna().all().all()


def test_a_kept_published_day_gains_the_ask_volume_without_any_published_value_changing():
    ts = pd.date_range("2026-10-06 01:00", periods=10, freq="1min", tz="UTC")
    published = pd.DataFrame({"ts_utc": ts, "bid_close": 2000.0, "tick_count": 1.0, "dq_flag": ""})
    fresh = published.assign(bid_close=1999.0, tick_count=9.0, tick_count_ask=0.4)   # same size: published day kept
    out = dy.merge_never_shrink(published, fresh)
    assert (out["bid_close"] == 2000.0).all() and (out["tick_count"] == 1.0).all()
    assert (out["tick_count_ask"] == 0.4).all()


def test_the_feature_carries_the_new_columns_under_a_new_version():
    assert {"of_quote_imb_15m", "of_quote_imb_60m"} <= set(of_feat.COLUMNS)
    assert of_feat.VERSION == "3"
