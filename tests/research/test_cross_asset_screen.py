"""Cross-asset lead-lag screen (docs/research/candidates/cross_asset/cross_asset_screen.py,
prereg-2026-11-01-cross-asset.md): point-in-time beta, exact-minute returns, the zero-tolerance lead join, the
catch-up signal rule and the stop/time walk."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data.store import asof_join

PATH = Path(__file__).resolve().parents[2] / "docs" / "research" / "candidates" / "cross_asset" / "cross_asset_screen.py"
spec = importlib.util.spec_from_file_location("cross_asset_screen", PATH)
assert spec and spec.loader
xs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xs)
MIN = xs.MIN


def test_ret5_needs_the_exact_bar_five_minutes_back() -> None:
    ts = np.array([0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11], dtype=np.int64) * MIN
    c = np.exp(np.arange(len(ts), dtype=float))
    r = xs.ret5(ts, c)
    assert np.isnan(r[:5]).all()
    assert r[5] == pytest.approx(5.0)          # minute 5 vs minute 0
    assert r[7] == pytest.approx(7 - 3)        # minute 8 vs minute 3
    assert np.isnan(r[10])                     # minute 11 vs minute 6: no bar at 6


def test_ret5_minute_seven_uses_minute_two() -> None:
    ts = np.array([0, 1, 2, 3, 4, 5, 7], dtype=np.int64) * MIN
    c = np.exp(np.arange(len(ts), dtype=float))
    assert xs.ret5(ts, c)[6] == pytest.approx(6 - 2)


def test_lead_join_has_zero_tolerance() -> None:
    grid = pd.DataFrame({"ts_utc": pd.to_datetime(np.array([1, 2, 3]) * MIN, utc=True)})
    lead = pd.DataFrame({"available_utc": pd.to_datetime(np.array([1, 3]) * MIN, utc=True), "r_x": [0.1, 0.3]})
    out = asof_join(grid, lead, tolerance=pd.Timedelta(0))
    assert out["r_x"].iloc[0] == 0.1 and np.isnan(out["r_x"].iloc[1]) and out["r_x"].iloc[2] == 0.3


def test_rolling_beta_uses_only_prior_days() -> None:
    rng = np.random.default_rng(0)
    days = np.repeat(np.arange(25), 50)
    x = rng.normal(size=(len(days), 1))
    y = 2.0 * x[:, 0] + 0.01 * rng.normal(size=len(days))
    y2 = y.copy()
    y2[days == 22] = -50 * x[days == 22, 0]          # day 22's own data must not move day 22's beta
    b1, _ = xs.rolling_beta(days, y, x, np.ones(len(days), bool), lookback=20)
    b2, s2 = xs.rolling_beta(days, y2, x, np.ones(len(days), bool), lookback=20)
    assert 19 not in b1 and 20 in b1
    assert b1[22][0] == pytest.approx(2.0, abs=0.01)
    assert b2[22][0] == pytest.approx(b1[22][0])
    assert b2[23][0] != pytest.approx(b1[23][0])      # visible the day after
    assert s2[20] == pytest.approx(0.01, rel=0.2)


def _frame(r_gold: list[float], r_x: list[float], day0: int = 0) -> pd.DataFrame:
    n = len(r_gold)
    base = (day0 * 1440 + 8 * 60) * MIN              # bars open 08:00 UTC
    return pd.DataFrame({"ts_utc": pd.to_datetime(base + (np.arange(n) + 1) * MIN, utc=True), "k": np.arange(n),
                         "r_gold": r_gold, "r_x": r_x})


def test_signal_needs_lead_move_and_gold_lagging(monkeypatch: pytest.MonkeyPatch) -> None:
    # 2010-01-04 is a Monday; beta 1, sd 0.001 forced
    day0 = int(pd.Timestamp("2010-01-04").value // (1440 * MIN))
    f = _frame([0.0, 0.0025, 0.001, -0.001, 0.0], [0.003, 0.003, 0.003, -0.0015, 0.0], day0)
    monkeypatch.setattr(xs, "rolling_beta", lambda day, y, x, s: ({day0: np.array([1.0])}, {day0: 0.001}))
    sig = xs.signals(f, ["r_x"])
    # row 0: pred 3 sd, gold 0 -> long; row 1: gold did 83% -> no; row 2: 33% -> long; row 3: 1.5 sd -> no
    assert list(sig["k"]) == [0, 2]
    assert list(sig["side"]) == [1, 1]


def test_walk_time_stop_long_short_and_gap() -> None:
    o, h, lo = np.array([100.0, 100.5]), np.array([100.6, 101.0]), np.array([99.9, 100.2])
    assert xs.walk_time_stop(o, h, lo, 1, 100.0, 1.0, 100.8) == (pytest.approx(0.8), False)
    assert xs.walk_time_stop(o, h, lo, -1, 100.0, 1.0, 100.8) == (pytest.approx(-1.0), True)   # 101.0 touches stop
    # long gapped through the stop: fills at the open, worse than the level
    o2, h2, lo2 = np.array([100.0, 98.5]), np.array([100.1, 98.7]), np.array([99.5, 98.2])
    assert xs.walk_time_stop(o2, h2, lo2, 1, 100.0, 1.0, 99.0) == (pytest.approx(-1.5), True)
