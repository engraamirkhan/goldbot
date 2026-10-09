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
        "breakout": "1h", "mean_reversion": "15m", "session_open": "15m", "trend": "1h",
        "tsmom": "1h", "intraday_momentum": "15m"}


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


@pytest.mark.parametrize("family", ["trend", "mean_reversion", "breakout", "tsmom", "tsmom_4h", "intraday_momentum",
                                    "intraday_momentum_london"])
def test_candidates_use_no_future_bars(bars_1m, family):
    configs: dict[str, dict[str, Any]] = {"trend": {"adx_min": 0.0, "pullback_atr": 5.0}, "mean_reversion": {"band_z": 1.0, "rsi_low": 45.0,
             "rsi_high": 55.0, "max_vol_tercile": 2}, "breakout": {"max_range_atr": 5.0, "min_tick_ratio": 0.0},
             "tsmom": {}, "tsmom_4h": {"timeframe": "4h", "max_bars": 12}, "intraday_momentum": {},
             "intraday_momentum_london": {"session": "london"}}
    loose = configs[family]
    cls = SPECIALISTS[family.removesuffix("_4h").removesuffix("_london")]
    spec = cls(**loose)
    m, X = _frame(bars_1m, spec.timeframe)
    full = spec.candidates(m, X)
    assert len(full) > 10 and set(full["side"]) <= {-1, 1}
    # recomputing on a prefix of the history (features included) gives the same signals up to the cut
    cut = len(m) * 2 // 3
    bars_cut = bars_1m[bars_1m["visible_at"] <= pd.Timestamp(m["ts_utc"].iloc[cut]) + pd.Timedelta(spec.timeframe)]
    m2, X2 = _frame(bars_cut, spec.timeframe)
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


def test_lookahead_check_passes_the_real_features_and_catches_a_leaky_one(bars_1m):
    from goldbot.features.registry import FEATURES, feature
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, lookahead_check
    dec = resample_bars(bars_1m, "15m").reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(bars_1m, x) for x in context_tfs("15m")}
    clean = lookahead_check(dec, context)
    assert clean["lookahead_columns"] == [] and clean["columns_checked"] > 100

    @feature("test_leaky_zscore", "test")
    def leaky(df, ctx):            # normalised by the whole sample's mean: uses the future
        return pd.DataFrame({"close_over_sample_mean": df["close"] / df["close"].mean()}, index=df.index)
    try:
        out = lookahead_check(dec, context, feature_names=[*DEFAULT_FEATURE_NAMES, "test_leaky_zscore"])
        assert out["lookahead_columns"] == ["close_over_sample_mean"]
    finally:
        del FEATURES["test_leaky_zscore"]


def _bars_15m(days: list[str]) -> pd.DataFrame:
    ts = pd.DatetimeIndex([t for d in days for t in pd.date_range(d, periods=96, freq="15min", tz="UTC")])
    close = 2000.0 + np.arange(len(ts)) * 0.1
    return pd.DataFrame({"ts_utc": ts, "open": close - 0.1, "high": close + 0.2, "low": close - 0.3, "close": close})


def test_intraday_momentum_decides_at_mid_session_on_the_local_clock_and_exits_at_the_close():
    m = _bars_15m(["2024-01-10", "2024-07-10", "2024-07-11"])
    m = m[m["ts_utc"] != pd.Timestamp("2024-07-11 12:30", tz="UTC")].reset_index(drop=True)   # 08:30 EDT bar missing
    X = pd.DataFrame({"atr14": np.ones(len(m))})
    spec = SPECIALISTS["intraday_momentum"]()
    assert spec.config["session"] == "newyork" and spec.label_spec.max_bars == 14
    c = spec.candidates(m, X)
    ts = pd.DatetimeIndex(m["ts_utc"].iloc[c["idx"]])
    # the decision bar opens at 12:00 New York time (closes 12:15): 17:00 UTC in winter, 16:00 UTC in summer; the
    # day whose opening bar is missing is skipped
    assert list(ts) == [pd.Timestamp("2024-01-10 17:00", tz="UTC"), pd.Timestamp("2024-07-10 16:00", tz="UTC")]
    assert c["side"].tolist() == [1, 1]                                       # up since the open: long
    last = pd.DatetimeIndex(m["ts_utc"].iloc[c["idx"] + 1 + spec.label_spec.max_bars]).tz_convert("America/New_York")
    assert [t.strftime("%H:%M") for t in last] == ["15:45", "15:45"]          # time barrier: the bar closing at 16:00
    falling = m.assign(close=m["close"].iloc[::-1].to_numpy(), open=m["open"].iloc[::-1].to_numpy())
    assert spec.candidates(falling, X)["side"].tolist() == [-1, -1]
    small = SPECIALISTS["intraday_momentum"](min_move_atr=1.0).candidates(m, X.assign(atr14=1000.0))
    assert small.empty                                                        # move below min_move_atr x ATR
    ldn = SPECIALISTS["intraday_momentum"](session="london")
    lt = pd.DatetimeIndex(m["ts_utc"].iloc[ldn.candidates(m, X)["idx"]]).tz_convert("Europe/London")
    assert [t.strftime("%H:%M") for t in lt] == ["12:00"] * 3 and ldn.label_spec.max_bars == 16
    with pytest.raises(ValueError, match="unknown session"):
        SPECIALISTS["intraday_momentum"](session="tokyo")


def test_tsmom_trades_the_trailing_trend_on_a_four_hour_schedule():
    n = 900
    ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    rng = np.random.default_rng(3)
    m = pd.DataFrame({"ts_utc": ts, "close": 2000.0 * np.exp(np.cumsum(0.002 + rng.normal(0, 0.001, n)))})
    none = pd.DataFrame(index=m.index)
    spec = SPECIALISTS["tsmom"]()
    c = spec.candidates(m, none)
    assert len(c) > 50 and set(c["side"]) == {1}
    assert c["idx"].min() >= 480                                              # the 20-day horizon needs its history
    close_hours = (pd.DatetimeIndex(m["ts_utc"].iloc[c["idx"]]) + pd.Timedelta("1h")).hour
    assert set(close_hours % 4) == {0}                                        # at most once per 4 hours
    down = m.assign(close=2000.0 * np.exp(np.cumsum(-0.002 + rng.normal(0, 0.001, n))))
    assert set(spec.candidates(down, none)["side"]) == {-1}
    assert SPECIALISTS["tsmom"](min_score=50.0).candidates(m, none).empty
    from goldbot.specialists.time_series_momentum import TimeSeriesMomentumSpecialist
    four = TimeSeriesMomentumSpecialist(timeframe="4h", max_bars=12)
    assert four._bars(four.config["lb_slow_h"]) == 120 and four.label_spec.max_bars == 12


@pytest.mark.parametrize("family", ["tsmom", "intraday_momentum"])
def test_new_families_fire_often_enough_for_the_screen(bars_1m, family):
    """The screen needs 1,000 events in 2010-2025 (15.75 years): about 64 a year after one position at a time."""
    from goldbot.features.technical import atr
    from goldbot.labels import one_at_a_time, triple_barrier
    spec = SPECIALISTS[family]()
    dec = resample_bars(bars_1m, spec.timeframe).reset_index(drop=True)
    m, X = _frame(bars_1m, spec.timeframe)
    labels = one_at_a_time(triple_barrier(dec, spec.candidates(m, X), spec.label_spec, atr(m, 14)))
    ts = pd.DatetimeIndex(m["ts_utc"])
    warmup = 480 / (24 * 5 / 7) / 365.25 if family == "tsmom" else 0.0       # 480 trading hours of history first
    years = (ts[-1] - ts[0]).days / 365.25 - warmup
    assert len(labels) / years > 1.5 * 1000 / 15.75


def test_tsmom_daily_option_sizes_lookbacks_and_barriers_for_days():
    from goldbot.research.walkforward import window_for
    from goldbot.specialists.time_series_momentum import TimeSeriesMomentumSpecialist
    d = TimeSeriesMomentumSpecialist(timeframe="1d")
    assert d.timeframe == "1d"
    # 20 / 60 / 120 trading days of vol-scaled return (60-day volatility), 3 ATR target, 1.5 ATR stop, 10 days
    assert [d._bars(d.config[k]) for k in ("lb_fast_h", "lb_mid_h", "lb_slow_h", "vol_window_h")] == [20, 60, 120, 60]
    ls = d.label_spec
    assert (ls.target_atr, ls.stop_atr, ls.max_bars) == (3.0, 1.5, 10)
    assert TimeSeriesMomentumSpecialist(timeframe="1d", max_bars=7).label_spec.max_bars == 7   # overrides still win
    assert TimeSeriesMomentumSpecialist().config["max_bars"] == 48                            # 1h defaults unchanged
    assert TimeSeriesMomentumSpecialist(timeframe="4h", max_bars=12).config["lb_slow_h"] == 480
    # a clone rebuilt from its identity keeps exactly its configuration (and agent id)
    again = TimeSeriesMomentumSpecialist(identity=d.identity)
    assert again.config == d.config and again.agent_id == d.agent_id
    # walk-forward on daily bars: train 60 / test 12 / step 12 months on an expanding window
    w = window_for("1d")
    assert (w["train_months"], w["test_months"], w["step_months"], w["expanding"]) == (60, 12, 12, True)
    assert w["purge_days"] >= 14                                   # a 10-day hold spans two calendar weeks


def test_tsmom_daily_fires_on_every_settled_bar_without_lookahead():
    # daily bars open and close at COMEX settlement (13:30 New York), never on a whole UTC hour
    n = 400
    days = pd.bdate_range("2023-01-02", periods=n)
    ts = pd.DatetimeIndex([pd.Timestamp(f"{d:%Y-%m-%d} 13:30", tz="America/New_York").tz_convert("UTC") for d in days])
    rng = np.random.default_rng(5)
    m = pd.DataFrame({"ts_utc": ts, "close": 2000.0 * np.exp(np.cumsum(0.004 + rng.normal(0, 0.01, n)))})
    none = pd.DataFrame(index=m.index)
    spec = SPECIALISTS["tsmom"](timeframe="1d")
    c = spec.candidates(m, none)
    assert c["idx"].min() >= 120                                   # the 120-day horizon needs its history
    assert len(c) > 0.8 * (n - 120) and set(c["side"]) <= {-1, 1}  # a steady uptrend signals on nearly every bar
    assert (c["side"] == 1).mean() > 0.9
    cut = 300
    part = spec.candidates(m.iloc[:cut].reset_index(drop=True), none.iloc[:cut])
    pd.testing.assert_frame_equal(part, c[c["idx"] < cut].reset_index(drop=True), check_dtype=False)
