"""The micro-tick screen (scripts/research/micro_tick_screen.py, prereg-2026-11-01-micro-tick.md) on hand-built
paths: the walk's intrabar order and gaps, the spread-state cost, Holm, and the three signals' point-in-time inputs."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[2] / "scripts" / "research" / "micro_tick_screen.py"
spec = importlib.util.spec_from_file_location("micro_tick_screen", PATH)
assert spec and spec.loader
mt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mt)

CELL = {"rt": 0.2, "ov": 0.1, "spread": 0.08}


def arr(*x: float) -> np.ndarray:
    return np.array(x, dtype=float)


def test_walk_same_minute_stop_and_target_is_a_stop() -> None:
    gross, kind, j = mt.walk(arr(100, 100), arr(100.2, 102.5), arr(99.9, 98.0), arr(100, 99), 1, 1.0, 2.0)
    assert (kind, j) == ("stop", 1) and gross == pytest.approx(-1.0)


def test_walk_gap_through_stop_fills_at_open_and_short_mirrors() -> None:
    gross, kind, _ = mt.walk(arr(100, 98.5), arr(100.1, 98.6), arr(99.5, 98.0), arr(100, 98.2), 1, 1.0, None)
    assert kind == "stop" and gross == pytest.approx(-1.5)
    gross, kind, j = mt.walk(arr(100, 99.5, 98.0), arr(100.1, 99.6, 98.1), arr(99.9, 99.0, 97.9), arr(100, 99.2, 98),
                             -1, 1.0, 1.5)
    assert (kind, j) == ("target", 2) and gross == pytest.approx(1.5)   # gap through target: level only


def test_walk_time_exit_at_last_close() -> None:
    gross, kind, j = mt.walk(arr(100, 100.2), arr(100.3, 100.4), arr(99.8, 100.0), arr(100.2, 100.3), 1, 1.0, None)
    assert (kind, j) == ("time", 1) and gross == pytest.approx(0.3)


def test_cost_adds_overshoot_on_stops_and_spread_excess() -> None:
    assert mt.trade_cost(CELL, "time", 0.3, 0.3, 0.3, 0.3) == pytest.approx(1.5 * 0.2)
    assert mt.trade_cost(CELL, "stop", 0.3, 0.3, 0.3, 0.3) == pytest.approx(1.5 * 0.3)
    # entry spread 3x its median: + 1.5 x 0.5 x 0.08 x 2
    assert mt.trade_cost(CELL, "time", 0.9, 0.3, 0.2, 0.3) == pytest.approx(1.5 * (0.2 + 0.08))


def test_holm_step_down() -> None:
    adj = mt.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == pytest.approx({"a": 0.03, "c": 0.06, "b": 0.06})


def frame(n: int, start: str = "2012-03-06 05:00") -> pd.DataFrame:
    t = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    raw = pd.DataFrame({"ts_utc": t, "bid_open": 100.0, "bid_high": 100.1, "bid_low": 99.9, "bid_close": 100.0,
                        "tick_count": 1.0, "spread_mean": 0.2, "spread_max": 0.3})
    for k in ("open", "high", "low", "close"):
        raw[f"ask_{k}"] = raw[f"bid_{k}"] + 0.2
    return raw


def test_trailing_features_exclude_the_current_bar() -> None:
    raw = frame(200)
    raw.loc[150, "spread_mean"] = 5.0
    f = mt.frame_from_raw(raw)
    assert f.med_sp.iat[150] == pytest.approx(0.2) and f.atr1.iat[150] == pytest.approx(0.2)


def test_vac_fades_a_fast_move_with_a_blown_spread() -> None:
    raw = frame(200, "2012-03-06 07:00")
    k = 120
    for i, lvl in enumerate((100.5, 101.0, 101.5)):            # +1.5 over 3 minutes = 7.5 x ATR1m
        for c in ("open", "high", "low", "close"):
            raw.loc[k - 2 + i, f"bid_{c}"] = lvl
            raw.loc[k - 2 + i, f"ask_{c}"] = lvl + 0.2
    raw.loc[k, "spread_mean"] = 0.4                             # 2 x the median
    f = mt.frame_from_raw(raw)
    sig = mt.sig_vac(f, 0)
    assert [(s[0], s[1]) for s in sig] == [(k, -1)]
    raw.loc[k, "spread_mean"] = 0.25                            # no blowout: no signal
    assert mt.sig_vac(mt.frame_from_raw(raw), 0) == []


def test_tim_follows_a_tick_weighted_one_sided_burst() -> None:
    raw = frame(200, "2012-03-06 07:00")
    for i in range(5):
        k = 150 + i
        raw.loc[k, ["bid_open", "bid_close"]] = [100.0 + 0.1 * i, 100.1 + 0.1 * i]
        raw.loc[k, ["ask_open", "ask_close"]] = [100.2 + 0.1 * i, 100.3 + 0.1 * i]
        raw.loc[k, "tick_count"] = 3.0
    sig = mt.sig_tim(mt.frame_from_raw(raw), 0)
    assert sig and sig[0][:2] == (151, 1)            # bars 150-151 at 3 ticks: I5 = 6/9, activity 9 >= 7.5


def test_srr_fades_a_burst_pierce_of_the_prior_day_high_closed_back_inside() -> None:
    # prior trading day (22:00-22:00 UTC) high 100.1 mid; then a pierce at 08:00 with a tick burst and a close back
    raw = frame(60 * 36, "2012-03-05 22:00")
    k = int(np.flatnonzero(raw.ts_utc == pd.Timestamp("2012-03-07 08:00", tz="UTC"))[0])
    raw.loc[k, ["bid_high", "ask_high"]] = [100.6, 100.8]
    raw.loc[k, "tick_count"] = 5.0
    f = mt.frame_from_raw(raw)
    sig = mt.sig_srr(f, 0)
    assert [(s[0], s[1]) for s in sig] == [(k, -1)]
    raw.loc[k, "tick_count"] = 1.0                              # no stop-trigger burst: no trade
    assert mt.sig_srr(mt.frame_from_raw(raw), 0) == []


def test_simulate_flattens_before_21_utc() -> None:
    raw = frame(120, "2012-03-06 20:20")
    f = mt.frame_from_raw(raw)
    costs = {s: CELL for s in ("asia", "london", "newyork")}
    r = mt.simulate(f, costs, 0, 1, 1.0, None, 60)
    assert r is not None and pd.Timestamp(r["t_exit"], tz="UTC") == pd.Timestamp("2012-03-06 20:59", tz="UTC")
