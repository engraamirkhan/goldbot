"""Positioning screen logic (docs/research/prereg-positioning-2026-11-01.md): PIT signals, exits, control, Holm."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.research import positioning_screen as ps


def test_trailing_share_below_uses_only_previous_values() -> None:
    x = np.array([1.0, 2.0, 3.0, 0.5, np.nan, 10.0])
    out = ps.trailing_share_below(x, window=3, min_prior=2)
    assert np.isnan(out[0]) and np.isnan(out[1])          # fewer than 2 previous values
    assert out[2] == 1.0                                   # 1, 2 both below 3
    assert out[3] == 0.0                                   # 1, 2, 3 none below 0.5
    assert np.isnan(out[4])
    assert out[5] == 1.0                                   # previous window [3, 0.5, nan] -> 2 finite, both below
    y = x.copy()
    y[5] = -1.0                                            # changing a later value never changes an earlier output
    assert np.array_equal(ps.trailing_share_below(y, 3, 2)[:5], out[:5], equal_nan=True)


def _pos(net: np.ndarray, oi: float = 100.0) -> pd.DataFrame:
    days = pd.date_range("2006-06-13", periods=len(net), freq="7D")
    avail = (days + pd.Timedelta(days=3, hours=20)).tz_localize("UTC")
    rows = [pd.DataFrame({"series": "cot_mm_net", "value_date": days, "value": net, "available_utc": avail}),
            pd.DataFrame({"series": "cot_open_interest", "value_date": days, "value": oi, "available_utc": avail})]
    return pd.concat(rows, ignore_index=True)


def test_cot_signals_extremes_and_flow_sign() -> None:
    rng = np.random.default_rng(0)
    net = rng.normal(0, 5, 200)
    net[180] = -50.0                                       # a crowded short: bottom decile -> long
    net[190] = 50.0                                        # crowded long: top decile -> short
    s = ps.cot_signals(_pos(net))
    assert s.loc[180, "P1_EXT"] == 1 and s.loc[190, "P1_EXT"] == -1
    assert (s.loc[:ps.P1_MIN - 1, "P1_EXT"] == 0).all()    # not enough history
    assert s.loc[180, "P2_FLOW"] == 1                      # a big fall in MM net -> contrarian long
    assert s.loc[190, "P2_FLOW"] == -1
    assert s["available_utc"].is_monotonic_increasing


def test_gld_signal_momentum_sign() -> None:
    days = pd.bdate_range("2008-01-01", periods=400)
    t = 1000.0 + np.random.default_rng(1).normal(0, 0.5, 400).cumsum()
    t[390:] = t[389] * 1.05                                 # a 5% inflow at 390
    pos = pd.DataFrame({"series": "gld_tonnes", "value_date": days, "value": t,
                        "available_utc": (days + pd.Timedelta(days=1, hours=14)).tz_localize("UTC")})
    s = ps.gld_signals(pos)
    assert s.loc[390, "P3_ETF"] == 1
    assert (s.loc[:ps.P3_MIN, "P3_ETF"] == 0).all()


def test_new_report_mask() -> None:
    r = pd.to_datetime(pd.Series([None, "2010-01-05", "2010-01-05", "2010-01-12", "2010-01-12"]))
    assert ps.new_report_mask(r).tolist() == [False, True, False, True, False]


def test_trade_path_stop_gap_and_time_exit() -> None:
    o = np.array([100, 100, 95, 100.0])
    h = np.array([101, 101, 96, 101.0])
    lo = np.array([99, 99, 94, 99.0])
    c = np.array([100, 100, 95, 100.0])
    g, x, hit = ps.trade_path(o, h, lo, c, 0, 3, 1, 2.0)    # stop 98, bar 2 opens at 95 -> filled at 95
    assert (hit, x) == ("stop", 2) and g == pytest.approx(-2.5)
    g, x, hit = ps.trade_path(o, h, lo, c, 0, 1, 1, 2.0)
    assert (hit, x, g) == ("time", 1, 0.0)
    g, x, hit = ps.trade_path(o, h, lo, c, 2, 3, -1, 10.0)  # short from 95, no stop at 105, exit at 100
    assert hit == "time" and g == pytest.approx(-0.5)


def test_exit_bars_five_decisions_later() -> None:
    idx = np.arange(0, 60, 6)
    out = ps.exit_bars(idx, 100)
    assert out[0] == idx[5] and out[-1] == 99


def test_matched_control_same_year_and_side() -> None:
    pool_years = np.array([2010] * 4 + [2011] * 4)
    pool_long = np.array([1, 1, 1, 1, -1, -1, -1, -1.0])
    pool_short = -pool_long
    rng = np.random.default_rng(0)
    m = ps.matched_control(np.array([2010, 2011]), np.array([1, -1]), pool_years, pool_long, pool_short, 50, rng)
    assert np.allclose(m, 1.0)                             # long in 2010 (+1) and short in 2011 (+1)
    with pytest.raises(ValueError):
        ps.matched_control(np.array([2012]), np.array([1]), pool_years, pool_long, pool_short, 5, rng)


def test_holm_monotone() -> None:
    adj = ps.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)
