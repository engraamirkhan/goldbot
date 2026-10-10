"""Survey feature families (goldbot/features/survey.py): each value checked against its definition on bars where the
answer is known, and a truncated-history check per family that no value changes when later bars are appended."""
import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features import FEATURES, build_features, families
from goldbot.features.survey import ROUND_TRIP_EXTRA_USD, rolling_hurst
from goldbot.features.technical import atr
from goldbot.specialists import SPECIALISTS

SURVEY = ["vol_estimators", "round_numbers", "regime_stats", "jumps", "expected_move"]


def _bars(o, h, lo, c, spread: float = 0.3, start: str = "2024-01-10 00:00") -> pd.DataFrame:
    o, h, lo, c = (np.asarray(x, dtype=float) for x in (o, h, lo, c))
    ts = pd.date_range(start, periods=len(o), freq="15min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": o, "high": h, "low": lo, "close": c, "spread": spread, "tick_count": 10.0})


def _first(s: pd.Series) -> int:
    """Position of the first non-NaN value."""
    return int(np.flatnonzero(s.notna().to_numpy())[0])


def _walk(n: int, seed: int = 1, sd: float = 0.001, start: float = 2000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = start * np.exp(np.cumsum(rng.normal(0, sd, n)))
    o = np.concatenate(([start], c[:-1])) * np.exp(rng.normal(0, sd / 4, n))
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, sd / 2, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, sd / 2, n)))
    return _bars(o, h, lo, c)


def test_survey_families_are_registered_default_and_no_specialist_declares_their_columns():
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES
    fam = families()
    assert {"vol_estimators", "jumps", "expected_move"} <= set(fam["volatility"])
    assert "round_numbers" in fam["structure"] and fam["regime"] == ["regime_stats"]
    assert set(SURVEY) <= set(DEFAULT_FEATURE_NAMES)
    cols = set(build_features(_walk(300), SURVEY).columns) - {"ts_utc"}
    assert len(cols) == 9 + 8 + 6 + 4 + 4
    for name, cls in SPECIALISTS.items():
        assert not cols & set(cls.model_features), name


# --------------------------------------------------------------------------- volatility estimators
def test_volatility_estimators_match_their_formulas_on_the_last_window():
    df = _walk(200, seed=3)
    X = build_features(df, ["vol_estimators"])
    o, h, lo, c = (df[k].to_numpy(dtype=float)[-60:] for k in ("open", "high", "low", "close"))
    pc = df["close"].to_numpy(dtype=float)[-61:-1]
    gk = 0.5 * np.log(h / lo) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
    rs = np.log(h / c) * np.log(h / o) + np.log(lo / c) * np.log(lo / o)
    k = 0.34 / (1.34 + 61 / 59)
    yz = np.sqrt(np.var(np.log(o / pc), ddof=1) + k * np.var(np.log(c / o), ddof=1) + (1 - k) * rs.mean())
    last = X.iloc[-1]
    assert np.isclose(last["vol_gk_60"], np.sqrt(gk.mean()))
    assert np.isclose(last["vol_gk_20"], np.sqrt(gk[-20:].mean()))
    assert np.isclose(last["vol_rs_60"], np.sqrt(rs.mean()))
    assert np.isclose(last["vol_yz_60"], yz)
    assert np.isclose(last["vol_pk_60"], np.sqrt((np.log(h / lo) ** 2).mean() / (4 * np.log(2))))
    assert np.isclose(last["vol_ts_yz_20_60"], last["vol_yz_20"] / last["vol_yz_60"])
    yz20 = X["vol_yz_20"].to_numpy()[-60:]
    assert np.isclose(last["vol_of_vol_60"], yz20.std(ddof=1) / yz20.mean())
    # all estimators agree on the order of magnitude of the per-bar volatility (sd 0.001)
    assert all(0.0003 < last[f"vol_{e}_60"] < 0.003 for e in ("gk", "rs", "yz", "pk"))


def test_volatility_estimators_warm_up_as_documented():
    X = build_features(_walk(120), ["vol_estimators"])
    first = {c: _first(X[c]) for c in X.columns if c != "ts_utc"}
    assert first["vol_gk_20"] == first["vol_rs_20"] == 19 and first["vol_pk_60"] == 59
    assert first["vol_yz_20"] == 20 and first["vol_yz_60"] == 60 and first["vol_ts_yz_20_60"] == 60
    assert first["vol_of_vol_60"] == 79


# --------------------------------------------------------------------------- round numbers
def test_round_number_distance_is_in_atr_and_touches_count_bars_trading_through_the_level():
    n = 60
    c = np.full(n, 2003.0)                     # nearest $5 level 2005 (above), $10 2000, $25 2000, $50 2000
    h, lo = c + 0.5, c - 0.5                   # range 1.0: ATR14 = 1.0, no bar reaches 2005 or 2000...
    lo[[5, 45]] = 1999.5                      # ...except two bars that trade through 2000
    X = build_features(_bars(c, h, lo, c), ["round_numbers"])
    a = atr(_bars(c, h, lo, c), 14).to_numpy()
    last = X.iloc[-1]
    assert np.isclose(last["round_5_dist_atr"], -2.0 / a[-1]) and np.isclose(last["round_10_dist_atr"], 3.0 / a[-1])
    assert last["round_5_touches"] == 0 and last["round_10_touches"] == 1 and last["round_50_touches"] == 1   # bar 5 left
    assert X["round_10_touches"].iloc[49] == 2 and X["round_10_touches"].iloc[55] == 1   # first full window at bar 49
    assert X["round_10_touches"].iloc[:49].isna().all() and X["round_5_dist_atr"].iloc[:13].isna().all()


# --------------------------------------------------------------------------- regime statistics
def test_variance_ratio_is_near_one_for_a_random_walk_and_below_one_for_a_reverting_series():
    rw = build_features(_walk(3000, seed=7), ["regime_stats"])
    assert 0.8 < rw["vr_2"].iloc[200:].median() < 1.2 and 0.7 < rw["vr_8"].iloc[200:].median() < 1.3
    k = np.arange(400)
    c = 2000 + 0.5 * (-1.0) ** k + 0.01 * np.random.default_rng(0).normal(size=400)    # a bar-to-bar zigzag
    zz = build_features(_bars(c, c + 0.1, c - 0.1, c), ["regime_stats"])
    assert zz["vr_2"].iloc[-1] < 0.1 and zz["er_10"].iloc[-1] < 0.05
    assert zz["vr_2"].iloc[:121].isna().all() and zz["vr_2"].iloc[121:].notna().all()


def test_hurst_separates_persistent_from_anti_persistent_returns_and_needs_128_returns():
    rng = np.random.default_rng(11)
    e = rng.normal(size=3000)
    trend = np.convolve(e, np.ones(20), mode="full")[:3000]        # moving-average returns: persistent at small scales
    anti = np.diff(e, prepend=0.0)                                    # differenced noise: anti-persistent
    h_rw, h_trend, h_anti = (np.nanmedian(rolling_hurst(r)) for r in (e, trend, anti))
    assert h_anti < h_rw < h_trend and 0.4 < h_rw < 0.7
    X = build_features(_walk(300), ["regime_stats"])
    assert _first(X["hurst_128"]) == 128


def test_efficiency_ratio_is_one_on_a_straight_line():
    c = 2000 + 0.25 * np.arange(60)
    X = build_features(_bars(c, c + 0.1, c - 0.1, c), ["regime_stats"])
    assert np.allclose(X["er_10"].iloc[10:], 1.0) and np.allclose(X["er_30"].iloc[30:], 1.0)
    assert X["er_10"].iloc[:10].isna().all()


# --------------------------------------------------------------------------- jumps
def test_a_jump_is_flagged_with_its_sign_and_counted_from():
    df = _walk(200, seed=5)
    c = df["close"].to_numpy(dtype=float).copy()
    c[150:] *= 0.98                                      # a -2% bar against ~0.1% noise
    df = df.assign(close=c, open=np.r_[df["open"].iloc[:150], c[150:]], low=np.minimum(df["low"], c),
                   high=np.r_[df["high"].iloc[:150], c[150:] * 1.0005])
    X = build_features(df, ["jumps"])
    assert X["jump_flag"].iloc[150] == 1 and X["jump_sign"].iloc[150] == -1 and X["jump_z"].iloc[150] < -4
    assert X["bars_since_jump"].iloc[150] == 0 and X["bars_since_jump"].iloc[160] == 10
    assert X["jump_flag"].iloc[:61].sum() == 0 and X["jump_z"].iloc[:61].isna().all()
    assert X["jump_flag"].iloc[61:150].sum() == 0 and (X["bars_since_jump"].iloc[:150] == 500).all()


# --------------------------------------------------------------------------- expected move vs cost
def test_expected_move_is_the_scaled_yang_zhang_move_over_the_round_trip_cost():
    df = _walk(200, seed=9)
    X = build_features(df, ["expected_move", "vol_estimators"])
    cost = 0.3 + ROUND_TRIP_EXTRA_USD
    last = X.iloc[-1]
    close = df["close"].iloc[-1]
    for hz in (4, 16, 48):
        assert np.isclose(last[f"em_yz_{hz}_cost"], close * last["vol_yz_20"] * np.sqrt(hz) / cost)
    assert np.isclose(last["em_atr_cost"], atr(df, 14).iloc[-1] / cost)
    assert X["em_yz_4_cost"].iloc[:95].isna().all() and X["em_yz_4_cost"].iloc[95:].notna().all()
    custom = build_features(df, ["expected_move"], ctx={"em_horizons": (8,), "round_trip_extra": 0.0})
    assert np.isclose(custom["em_yz_8_cost"].iloc[-1], close * last["vol_yz_20"] * np.sqrt(8) / 0.3)


# --------------------------------------------------------------------------- no look-ahead
@pytest.fixture(scope="module")
def bars_15m():
    ticks = synthetic_ticks("2025-03-03", "2025-04-12", ticks_per_minute=2, seed=5)
    return resample_bars(ticks_to_1m(ticks), "15m").reset_index(drop=True)


@pytest.mark.parametrize("family", SURVEY)
def test_no_survey_feature_changes_when_future_bars_are_appended(bars_15m, family):
    m = mid(bars_15m).reset_index(drop=True)
    full = build_features(m, [family]).drop(columns=["ts_utc"])
    for cut in (150, len(m) // 3, len(m) // 2, len(m) - 3):
        part = build_features(m.iloc[:cut], [family]).drop(columns=["ts_utc"])
        pd.testing.assert_frame_equal(part, full.iloc[:cut], check_dtype=False)
    warm = full.iloc[200:]
    assert not warm.isna().any().any()                    # warmed up, every column is defined


def test_survey_features_pass_the_pipeline_lookahead_check(bars_15m):
    assert all(n in FEATURES for n in SURVEY)
    from goldbot.research.pipeline import lookahead_check
    out = lookahead_check(bars_15m, None, feature_names=SURVEY)
    assert out["lookahead_columns"] == [] and out["columns_checked"] >= 31
