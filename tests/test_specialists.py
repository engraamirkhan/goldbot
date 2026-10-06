"""The strategy families (design: specialist table): registration, context timeframes, each trigger's rule on
hand-made features, and no lookahead on real feature frames."""
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.research.pipeline import build_decision_frame
from goldbot.specialists import SPECIALISTS


@pytest.fixture(scope="module")
def bars_1m() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2024-01-01", "2024-04-01", ticks_per_minute=1, seed=21))


def _frame(bars_1m: pd.DataFrame, tf: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    dec = resample_bars(bars_1m, tf).reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(bars_1m, x) for x in context_tfs(tf)}
    return build_decision_frame(dec, context)


def test_every_design_family_is_registered_on_its_timeframe():
    assert {k: v.timeframe for k, v in SPECIALISTS.items()} == {
        "breakout": "1h", "mean_reversion": "15m", "session_open": "15m", "trend": "1h"}


def test_context_is_every_longer_timeframe():
    assert context_tfs("15m") == ["1h", "4h", "1d"]
    assert context_tfs("1h") == ["4h", "1d"]
    assert context_tfs("1d") == []


def test_trend_takes_a_pullback_in_the_4h_direction():
    n = 6
    X = pd.DataFrame({"h4_slope_ema50": [1.0] * n, "adx14": [30.0] * n,
                      "dist_ema50_atr": [1.5, 0.3, 0.2, 0.8, -0.4, 0.9],
                      "ret_1": [0.1, -0.1, -0.1, 0.2, -0.3, 0.2]})
    m = pd.DataFrame({"close": np.arange(n, dtype=float)})
    c = SPECIALISTS["trend"]().candidates(m, X)
    # bar 3: back above the EMA after touching it, closing up; bar 5 likewise; bar 4 is below the EMA
    assert c["idx"].tolist() == [3, 5] and c["side"].tolist() == [1, 1]
    weak = SPECIALISTS["trend"](adx_min=40.0).candidates(m, X)
    assert weak.empty                                                      # ADX filter


def test_mean_reversion_fades_band_extremes_in_calm_markets():
    X = pd.DataFrame({"bb_z_20": [2.5, -2.4, 2.5, 0.0], "rsi14": [80.0, 20.0, 80.0, 50.0],
                      "h1_vol_tercile": [0.0, 1.0, 2.0, 0.0]})
    c = SPECIALISTS["mean_reversion"]().candidates(pd.DataFrame({"close": [0.0] * 4}), X)
    assert c["idx"].tolist() == [0, 1] and c["side"].tolist() == [-1, 1]  # bar 2 is in the high-vol tercile


def test_breakout_needs_a_tight_range_and_volume():
    n = 12
    high = np.full(n, 101.0)
    low = np.full(n, 100.0)
    close = np.full(n, 100.5)
    high[9], close[9] = 103.0, 102.5          # closes above the 8-bar range
    low[11], close[11] = 97.0, 97.5           # closes below it, but on low volume
    m = pd.DataFrame({"high": high, "low": low, "close": close})
    X = pd.DataFrame({"atr14": [2.0] * n, "tick_vol_ratio_20": [2.0] * 10 + [2.0, 1.0]})
    c = SPECIALISTS["breakout"]().candidates(m, X)
    assert c["idx"].tolist() == [9] and c["side"].tolist() == [1]


@pytest.mark.parametrize("family", ["trend", "mean_reversion", "breakout"])
def test_candidates_use_no_future_bars(bars_1m, family):
    cls = SPECIALISTS[family]
    loose: dict[str, Any] = {"trend": {"adx_min": 0.0, "pullback_atr": 5.0}, "mean_reversion": {"band_z": 1.0, "rsi_low": 45.0,
             "rsi_high": 55.0, "max_vol_tercile": 2}, "breakout": {"max_range_atr": 5.0, "min_tick_ratio": 0.0}}[family]
    spec = cls(**loose)
    m, X = _frame(bars_1m, cls.timeframe)
    full = spec.candidates(m, X)
    assert len(full) > 10 and set(full["side"]) <= {-1, 1}
    # recomputing on a prefix of the history (features included) gives the same signals up to the cut
    cut = len(m) * 2 // 3
    bars_cut = bars_1m[bars_1m["visible_at"] <= pd.Timestamp(m["ts_utc"].iloc[cut]) + pd.Timedelta(cls.timeframe)]
    m2, X2 = _frame(bars_cut, cls.timeframe)
    part = spec.candidates(m2, X2)
    upto = full[full["idx"] < len(m2)].reset_index(drop=True)
    pd.testing.assert_frame_equal(part.reset_index(drop=True), upto, check_dtype=False)


def test_feature_subset_is_seeded_and_capped():
    from goldbot.research.pipeline import MAX_FEATURES, select_features
    cols = [f"f{i}" for i in range(90)]
    assert select_features(cols, {}) == cols[:MAX_FEATURES]
    a, b = select_features(cols, {"feature_seed": 11}), select_features(cols, {"feature_seed": 11})
    assert a == b and len(a) == MAX_FEATURES and a != cols[:MAX_FEATURES]
    assert a == sorted(a, key=cols.index)                                   # keeps the frame's column order
    assert select_features(cols, {"feature_seed": 12}) != a
    assert select_features(cols[:30], {"feature_seed": 11}) == cols[:30]


def test_mean_reversion_on_1h_uses_its_own_volatility_tercile(bars_1m):
    spec = SPECIALISTS["mean_reversion"](timeframe="1h", band_z=1.0, rsi_low=45.0, rsi_high=55.0, max_vol_tercile=2)
    m, X = _frame(bars_1m, "1h")
    assert "h1_vol_tercile" not in X.columns and len(spec.candidates(m, X)) > 10
