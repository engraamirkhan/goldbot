"""W2 windows and the sealed holdout: nothing at or after 2025-10-01 reaches position research."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research.position import data, engine
from goldbot.research.position.signals import PositionSpec
from goldbot.research.position.windows import (
    WINDOWS,
    HoldoutViolation,
    Window,
    assert_before_holdout,
    get_window,
)
from tests.research.position.helpers import (
    NO_FINANCING,
    UNIT,
    ZERO_COSTS,
    flat_daily,
    minutes_1m,
    series_signal,
)


def test_the_window_table_is_the_w2_table() -> None:
    got = {k: (w.start, w.end) for k, w in WINDOWS.items()}
    assert got == {"LEARN": (date(2010, 1, 1), date(2017, 12, 31)),
                   "LEARN-OLD": (date(2003, 1, 1), date(2005, 12, 31)),
                   "TEST-OLD": (date(2006, 2, 1), date(2009, 12, 31)),
                   "CONFIRM": (date(2018, 1, 1), date(2025, 9, 30))}
    with pytest.raises(KeyError):
        get_window("HOLDOUT")


def test_a_window_reaching_the_holdout_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="reaches the holdout"):         # pydantic wraps the HoldoutViolation
        Window(name="x", start=date(2025, 1, 1), end=date(2025, 10, 1), use="x")


def test_a_date_on_the_holdout_day_is_refused_and_the_day_before_passes() -> None:
    assert_before_holdout(pd.DatetimeIndex(["2025-09-30"]))
    assert_before_holdout(pd.DatetimeIndex(["2025-09-30 23:59:59"], tz="UTC"))
    with pytest.raises(HoldoutViolation):
        assert_before_holdout(pd.DatetimeIndex(["2025-09-30", "2025-10-01"]))
    with pytest.raises(HoldoutViolation):
        assert_before_holdout(pd.DatetimeIndex(["2025-10-01 00:00"], tz="UTC"))


def _write_1m(path: Path, start: str, n_days: int) -> None:
    b = minutes_1m(n_days, start=start, step_min=30)
    b["dq_flag"], b["source"] = "", "dukascopy"
    b.to_parquet(path)


def test_the_gold_loader_never_opens_a_post_holdout_file_and_cuts_2025_at_the_holdout(tmp_path: Path) -> None:
    _write_1m(tmp_path / "xauusd_1m_dukascopy_2025.parquet", "2025-09-24", 8)    # runs to 2025-10-03
    (tmp_path / "xauusd_1m_dukascopy_2026.parquet").write_bytes(b"not a parquet file: opening it would raise")
    raw = data.read_1m_files(tmp_path, "xauusd")
    assert raw["ts_utc"].max() < pd.Timestamp("2025-10-01", tz="UTC")
    daily = data.load_xau_daily(tmp_path)
    assert daily.index.max() == pd.Timestamp("2025-09-30")
    assert daily["close_utc"].max() == pd.Timestamp("2025-09-30 21:00", tz="UTC")
    assert set(daily.columns) >= {"open", "high", "low", "close", "close_utc"}


def test_the_winter_friday_hour_after_the_21_utc_close_folds_into_mondays_bar() -> None:
    b = minutes_1m(10, start="2012-01-02", step_min=30)                       # Mon 2 .. Fri 13 Jan
    fri = pd.Timestamp("2012-01-06 21:00", tz="UTC")
    extra = b.iloc[:2].copy()                                                 # two bars, moved to Fri 21:00
    extra["ts_utc"] = [fri, fri + pd.Timedelta(minutes=30)]
    extra["visible_at"] = extra["ts_utc"] + pd.Timedelta(minutes=1)
    for c in ("bid", "ask"):
        extra[f"{c}_open"], extra[f"{c}_high"], extra[f"{c}_low"], extra[f"{c}_close"] = 5000.0, 5010.0, 4990.0, 5005.0
    daily = data.daily_from_1m(pd.concat([b, extra]).sort_values("ts_utc").reset_index(drop=True))
    assert (pd.DatetimeIndex(daily.index).dayofweek < 5).all() and len(daily) == 10            # no Saturday stub
    mon = daily.loc["2012-01-09"]
    assert mon["open"] == 5000.0 and mon["high"] >= 5010.0 and mon["low"] <= 4990.0   # the fill after Friday's decision
    assert mon["close_utc"] == pd.Timestamp("2012-01-09 21:00", tz="UTC")


def test_a_daily_file_loader_cuts_at_the_holdout(tmp_path: Path) -> None:
    dates = pd.bdate_range("2025-09-25", "2025-10-06")
    pd.DataFrame({"date": dates, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0}).to_parquet(tmp_path / "US500.parquet")
    daily = data.load_daily_file(tmp_path / "US500.parquet")
    assert daily.index.max() == pd.Timestamp("2025-09-30")
    assert (daily["close_utc"] == (pd.DatetimeIndex(daily.index) + pd.Timedelta(hours=21)).tz_localize("UTC")).all()


def test_the_engine_refuses_a_panel_holding_a_holdout_date() -> None:
    bars = flat_daily(20, start="2025-09-15")
    spec = PositionSpec(id="T", symbols=("XAUUSD",), signal=series_signal(pd.Series(1.0, index=bars.index)))
    with pytest.raises(HoldoutViolation):
        engine.run(spec, {"XAUUSD": bars}, get_window("CONFIRM"), cfg=UNIT, costs={"XAUUSD": ZERO_COSTS},
                   venues={"A": NO_FINANCING})


def test_the_engine_refuses_a_signal_dated_in_the_holdout() -> None:
    bars = flat_daily(30, start="2025-08-01")
    leaky = pd.Series(1.0, index=pd.bdate_range("2025-08-01", periods=60, name="date"))

    def fn(symbol: str, panel) -> pd.Series:      # ignores the panel and returns dates it was never given
        return leaky
    spec = PositionSpec(id="T", symbols=("XAUUSD",), signal=fn)
    with pytest.raises(HoldoutViolation):
        engine.run(spec, {"XAUUSD": bars}, get_window("CONFIRM"), cfg=UNIT, costs={"XAUUSD": ZERO_COSTS},
                   venues={"A": NO_FINANCING})


def test_a_learn_run_never_hands_the_signal_a_bar_after_the_window() -> None:
    bars = flat_daily(2300, start="2009-06-01")                     # runs into 2018
    seen: list[pd.Timestamp] = []

    def fn(symbol: str, panel) -> pd.Series:
        seen.append(panel[symbol].index.max())
        return pd.Series(np.ones(len(panel[symbol])), index=panel[symbol].index)
    spec = PositionSpec(id="T", symbols=("XAUUSD",), signal=fn)
    engine.run(spec, {"XAUUSD": bars}, get_window("LEARN"), cfg=UNIT, costs={"XAUUSD": ZERO_COSTS},
               venues={"A": NO_FINANCING})
    assert max(seen) <= pd.Timestamp("2017-12-31")
