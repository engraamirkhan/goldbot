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


def test_reversal_attempt_fails_when_price_closes_beyond_the_turn_before_breaking_the_last_swing():
    # up-trend: HL 100 -> HH 110 -> HL 104 -> HH 116 (top attempt) -> dip to 108 -> close above 116 = failed
    pts = [95, 105, 100, 110, 104, 116, 108, 125]
    close = np.concatenate([np.linspace(a, b, int(abs(b - a)) + 1)[1:] for a, b in zip(pts[:-1], pts[1:])])
    close = np.r_[np.full(10, 95.0), close]
    r = M.reversal_attempts(_bars(close, 0.2, "1h"), pivot_len=2, horizon=200)
    ct = r["counter_trend"]
    assert ct["failed"] >= 1 and ct["held"] == 0
    # the same top followed by a drop through the last higher low (104) holds
    pts2 = [95, 105, 100, 110, 104, 116, 108, 112, 95]
    close2 = np.concatenate([np.linspace(a, b, int(abs(b - a)) + 1)[1:] for a, b in zip(pts2[:-1], pts2[1:])])
    r2 = M.reversal_attempts(_bars(np.r_[np.full(10, 95.0), close2], 0.2, "1h"), pivot_len=2, horizon=200)
    assert r2["counter_trend"]["held"] >= 1


def test_gap_fill_rates_are_shares_between_zero_and_one_with_event_counts():
    rng = np.random.default_rng(3)
    close = 2000 + np.cumsum(rng.normal(0, 1.0, 5000))
    r = M.gap_fill(_bars(close, 0.3, "15min"), ks=(1.0, 2.0), ns=(8, 16))
    assert r["events"][1.0]["count"] >= r["events"][2.0]["count"] > 0
    for v in (*r["baseline"].values(), r["events"][1.0][8], r["events"][1.0][16]):
        assert 0.0 <= v <= 1.0
    assert r["events"][1.0][16] >= r["events"][1.0][8]
