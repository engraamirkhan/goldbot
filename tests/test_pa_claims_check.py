"""scripts/pa_claims_check.py: the three descriptive measurements on crafted bars (no outcome beyond frequencies)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

_SPEC = importlib.util.spec_from_file_location("pa_claims_check",
                                               Path(__file__).resolve().parents[1] / "scripts" / "pa_claims_check.py")
assert _SPEC and _SPEC.loader
M = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(M)


def _bars(close: np.ndarray, wick: float, freq: str) -> pd.DataFrame:
    o = np.r_[close[0], close[:-1]]
    ts = pd.date_range("2024-01-01", periods=len(close), freq=freq, tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": np.maximum(o, close) + wick,
                         "low": np.minimum(o, close) - wick, "close": close})


def test_ema20_touch_counts_trading_days_whose_bars_straddle_the_ema():
    flat = _bars(np.full(24 * 10, 100.0), 0.5, "1h")
    r = M.ema20_touch_days(flat)
    assert r["share"] == 1.0 and r["days"] == 9                       # the first day lacks 12 bars after warm-up
    trend = _bars(100 + 5.0 * np.arange(24 * 10), 0.1, "1h")          # far above its EMA20 all the time
    assert M.ema20_touch_days(trend)["share"] == 0.0


def _zig(pts: list[float]) -> np.ndarray:
    close = np.concatenate([np.linspace(a, b, int(abs(b - a)) + 1)[1:] for a, b in zip(pts[:-1], pts[1:])])
    return np.r_[np.full(10, float(pts[0])), close]


def test_a_reversal_attempt_is_a_lower_high_in_an_uptrend_failing_on_a_close_above_the_last_high():
    # up-trend HL 100 -> HH 110 -> HL 104 -> HH 116, then a lower high at 113 (the attempt), then above 116: failed
    r = M.reversal_attempts(_bars(_zig([95, 105, 100, 110, 104, 116, 108, 113, 109, 125]), 0.2, "1h"),
                            pivot_len=2, horizon=200)
    assert r["counter_trend"] == {"failed": 1, "held": 0, "open": 0, "failed_share": 1.0}
    # the same lower high followed by a close below the last higher low (108): the reversal held
    r2 = M.reversal_attempts(_bars(_zig([95, 105, 100, 110, 104, 116, 108, 113, 100]), 0.2, "1h"),
                             pivot_len=2, horizon=200)
    assert r2["counter_trend"]["held"] == 1 and r2["counter_trend"]["failed"] == 0


def test_a_higher_high_in_an_uptrend_is_not_a_reversal_attempt():
    r = M.reversal_attempts(_bars(_zig([95, 105, 100, 110, 104, 116, 108, 125]), 0.2, "1h"), pivot_len=2, horizon=200)
    assert r["counter_trend"]["failed"] + r["counter_trend"]["held"] == 0


def test_shuffled_bars_keep_every_bar_shape_and_return_but_not_their_order():
    rng = np.random.default_rng(1)
    b = _bars(2000 + np.cumsum(rng.normal(0, 1, 500)), 0.3, "1h")
    s = M.shuffled_bars(b, seed=4)
    rel = lambda x: np.sort(np.round((x[["open", "high", "low", "close"]].to_numpy()[1:]  # noqa: E731
                                      - x["close"].to_numpy()[:-1, None]).ravel(), 9))
    assert np.allclose(rel(s), rel(b)) and not np.allclose(s["close"].to_numpy(), b["close"].to_numpy())
    null = M.reversal_null(b, n=5, pivot_len=2)
    assert len(null["counter_trend"]["shares"]) == 5 and 0 <= null["counter_trend"]["p05"] <= null["counter_trend"]["p95"] <= 1


def test_ema_touch_uses_the_previous_bar_ema_not_the_one_the_bar_itself_sets():
    close = np.r_[np.full(40, 100.0), 110.0]
    b = _bars(close, 0.0, "1h")
    b.loc[40, "open"], b.loc[40, "low"] = 100.5, 100.5                   # opens above, never trades at 100
    assert not M.ema_touch(b, 20)[40] and M.ema_touch(b, 20)[39]


def test_matched_baseline_weights_the_every_bar_rate_by_the_events_distance_bins():
    dist_all = np.array([0.1, 0.1, 0.9, 0.9, 0.9, 0.9])
    hit_all = np.array([True, True, False, False, True, False])
    # events: one in the near bin (rate 1.0), three in the far bin (rate 0.25)
    assert M.matched_rate(np.array([0.1, 0.9, 0.9, 0.9]), dist_all, hit_all, width=0.5) == (1.0 + 3 * 0.25) / 4


def test_gap_fill_rates_are_shares_between_zero_and_one_with_event_counts():
    rng = np.random.default_rng(3)
    close = 2000 + np.cumsum(rng.normal(0, 1.0, 5000))
    r = M.gap_fill(_bars(close, 0.3, "15min"), ks=(1.0, 2.0), ns=(8, 16))
    assert r["events"][1.0]["count"] >= r["events"][2.0]["count"] > 0
    for v in (*r["baseline"].values(), r["events"][1.0][8], r["events"][1.0][16]):
        assert 0.0 <= v <= 1.0
    assert r["events"][1.0][16] >= r["events"][1.0][8]
    assert 0.0 <= r["events"][1.0]["matched_16"] <= 1.0
