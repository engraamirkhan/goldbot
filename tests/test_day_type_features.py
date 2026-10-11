"""Opt-in `day_type` opportunity features (BACKLOG 30a; docs/research/opportunity-days.md section 3.1; H-16, H-17,
H-19, H-20, H-21 in docs/research/preregistration-2027Q2-draft.md): opportunity days on the 22:00 UTC rollover,
H-16's HAR model of the session-window range (08:00 London to 16:30 New York) with its monthly expanding refit and
band cuts, GVZ over realised volatility with the 5-business-day staleness rule, prior-day range, NR4/NR7, inside day,
the reopen gap, the DST-aware noise ratio and the tier-1 calendar columns. Every column is checked point in time
(truncation at several kinds of cut, and perturbation after the cut)."""
import argparse
import importlib.util
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data import us_calendar as uc
from goldbot.data.macro import US_BDAY, driver_frames, fred_frame
from goldbot.features import build_features, registry
from goldbot.features import day_type as dt
from goldbot.features.survey import yang_zhang

ROOT = Path(__file__).resolve().parents[1]
FROZEN_DEFAULT_VERSION = "f-079deacc61"      # sessions v2: the Q1 2027 H-02 universe (preregistration-2027Q1.md)
FROZEN_MACRO_VERSION = "f-51ac5ebabb"        # default + macro drivers
# day_type moves the version: the combined hashes are pinned here and in the Q2 draft (review M5)
DEFAULT_DAY_TYPE_VERSION = "f-1eada57050"
MACRO_DAY_TYPE_VERSION = "f-48c4c0c57e"


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


@pytest.fixture
def enabled():
    """Register the opt-in feature for one test and restore the registry afterwards (other tests see the default)."""
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    yield dt.enable()
    registry.FEATURES.clear()
    registry.FEATURES.update(feats)
    registry.SIGNED.clear()
    registry.SIGNED.update(signed)


def _bars(start: str, end: str, freq: str = "1h", seed: int = 3) -> pd.DataFrame:
    """Mid bars of a random walk whose daily volatility is persistent (log-vol AR(1) per day), with the weekend closure
    (Friday 22:00 to Sunday 22:00 UTC) removed."""
    ts = pd.date_range(start, end, freq=freq, tz="UTC", inclusive="left")
    wd, hr = ts.dayofweek, ts.hour
    closed = (wd == 5) | ((wd == 4) & (hr >= 22)) | ((wd == 6) & (hr < 22))
    ts = ts[~closed]
    rng = np.random.default_rng(seed)
    day = np.asarray(dt.opportunity_day(ts).factorize()[0])
    lv = np.zeros(day.max() + 1)
    for i in range(1, len(lv)):
        lv[i] = 0.9 * lv[i - 1] + 0.3 * rng.standard_normal()
    sig = 0.0015 * np.exp(lv[day])
    ret = sig * rng.standard_normal(len(ts))
    close = 2000 * np.exp(np.cumsum(ret))
    open_ = np.concatenate(([2000.0], close[:-1])) * np.exp(sig * 0.2 * rng.standard_normal(len(ts)))
    high = np.maximum(open_, close) + np.abs(sig * rng.standard_normal(len(ts))) * close
    low = np.minimum(open_, close) - np.abs(sig * rng.standard_normal(len(ts))) * close
    return pd.DataFrame({"ts_utc": ts, "open": open_, "high": high, "low": low, "close": close,
                         "spread": 0.2, "tick_count": 10})


def _days_bars(days: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """Hourly bars realising given daily (label, open, high, low, close) on opportunity days: 24 bars from the previous
    calendar day's 22:00 UTC; the high is printed at 12:00 UTC and the low at 14:00 UTC (inside the session window)."""
    rows = []
    for label, o, h, lo, c in days:
        t0 = pd.Timestamp(label, tz="UTC") - pd.Timedelta(hours=2)
        path = np.linspace(o, c, 25)
        for i in range(24):
            a, b = path[i], path[i + 1]
            rows.append({"ts_utc": t0 + pd.Timedelta(hours=i), "open": a, "close": b,
                         "high": h if i == 14 else max(a, b), "low": lo if i == 16 else min(a, b)})
    df = pd.DataFrame(rows)
    df["spread"], df["tick_count"] = 0.2, 10
    return df


def _script():
    spec = importlib.util.spec_from_file_location("research_pass_day_type", ROOT / "scripts" / "research_pass.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- opt-in registration and versions
def test_importing_the_module_registers_nothing_and_the_default_column_list_is_identical():
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, MACRO_FEATURE_NAMES, research_feature_names
    assert dt.NAME not in registry.FEATURES
    bars = _bars("2025-01-06", "2025-01-20")
    before_cols = list(build_features(bars, DEFAULT_FEATURE_NAMES).columns)
    before_names = list(DEFAULT_FEATURE_NAMES)
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    try:
        name = dt.enable()
        assert dt.enable() == name                                      # idempotent
        assert list(DEFAULT_FEATURE_NAMES) == before_names and name not in DEFAULT_FEATURE_NAMES
        assert list(build_features(bars, DEFAULT_FEATURE_NAMES).columns) == before_cols
        assert registry.feature_version(DEFAULT_FEATURE_NAMES) == FROZEN_DEFAULT_VERSION
        assert registry.feature_version([*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES]) == FROZEN_MACRO_VERSION
        assert research_feature_names({"macro": pd.DataFrame({"x": [1]})}) == [*DEFAULT_FEATURE_NAMES,
                                                                              *MACRO_FEATURE_NAMES]
        assert registry.FEATURES[name].family == "day_type" and registry.FEATURES[name].signed == {}
    finally:
        registry.FEATURES.clear()
        registry.FEATURES.update(feats)
        registry.SIGNED.clear()
        registry.SIGNED.update(signed)


def test_day_type_moves_the_feature_version_to_the_pinned_combined_hashes(enabled):
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, MACRO_FEATURE_NAMES
    assert registry.feature_version([*DEFAULT_FEATURE_NAMES, enabled]) == DEFAULT_DAY_TYPE_VERSION
    assert registry.feature_version([*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES, enabled]) == MACRO_DAY_TYPE_VERSION
    draft = (ROOT / "docs" / "research" / "preregistration-2027Q2-draft.md").read_text()
    assert DEFAULT_DAY_TYPE_VERSION in draft          # the macro combination is worked out at the freeze


def test_the_column_set_is_fixed_and_nan_without_history_or_inputs(enabled):
    bars = _bars("2025-01-06", "2025-01-07")                          # one day: no completed earlier day
    X = build_features(bars, [enabled], {})
    assert list(X.columns.drop("ts_utc")) == list(dt.COLUMNS)
    assert X.drop(columns=["ts_utc"]).isna().all().all()


# --------------------------------------------------------------------------- research_pass wiring
def test_research_pass_puts_the_calendar_in_ctx_and_its_families_only_with_their_flag(enabled):
    from goldbot.research.pipeline import research_feature_names
    mod = _script()
    cal = _calendar()
    assert mod.feature_ctx(None, None) == (None, None)
    ctx, names = mod.feature_ctx(None, None, cal)
    assert ctx is not None and ctx["us_calendar"] is cal and names is None      # in ctx, no calendar family
    ctx, names = mod.feature_ctx(None, None, cal, day_type=True)
    assert names == [*research_feature_names(ctx), "day_type"] and ctx is not None and "us_calendar" in ctx
    ctx, names = mod.feature_ctx(None, None, cal, calendar_features=True, day_type=True)
    assert names == [*research_feature_names(ctx), "tier1_events", "tier2_events", "day_type"]


def test_research_pass_records_day_type_inputs_on_the_trial_row():
    mod = _script()
    vd = pd.bdate_range("2025-01-02", periods=5)
    macro = fred_frame("GVZCLS", pd.DataFrame({"value_date": vd, "value": 15.0}), pd.Timestamp("2025-02-01"))
    info = mod.day_type_info(True, macro, _calendar())
    assert info["used"] is True and info["version"] == dt.VERSION and info["family"] == "day_type"
    assert info["gvz"] is True and info["us_calendar"] is True
    assert info["refit_schedule"] == dt.REFIT_SCHEDULE and info["min_fit_rows"] == dt.MIN_FIT_ROWS
    assert mod.day_type_info(True, None, _calendar())["gvz"] is False
    assert mod.day_type_info(False)["used"] is False


@pytest.mark.parametrize("over,why", [({"us_calendar": False}, "--us-calendar"), ({"cpcv": [3]}, "--cpcv"),
                                      ({"discover": True}, "--discover"), ({"from_year": 2011}, "--from-year 2010")])
def test_research_pass_refuses_day_type_without_the_calendar_with_cpcv_discover_or_a_late_start(over, why):
    mod = _script()
    args = argparse.Namespace(day_type=True, discover=False, cpcv=None, from_year=2010)
    cal = None if over.pop("us_calendar", True) is False else _calendar()
    for k, v in over.items():
        setattr(args, k, v)
    msg = mod.day_type_refusal(args, cal)
    assert msg is not None and why in msg
    assert mod.day_type_refusal(argparse.Namespace(day_type=True, discover=False, cpcv=None, from_year=2010),
                                _calendar()) is None
    assert mod.day_type_refusal(argparse.Namespace(day_type=False, discover=True, cpcv=[1], from_year=2015),
                                None) is None


# --------------------------------------------------------------------------- opportunity day (22:00 UTC rollover)
def test_the_opportunity_day_rolls_at_22_utc_and_the_sunday_reopen_belongs_to_monday():
    ts = pd.DatetimeIndex([_utc("2025-03-04 21:59"), _utc("2025-03-04 22:00"), _utc("2025-03-09 22:00"),
                           _utc("2025-07-06 21:00"), _utc("2025-03-07 21:45")])
    got = dt.opportunity_day(ts)
    assert list(got) == [pd.Timestamp("2025-03-04"), pd.Timestamp("2025-03-05"), pd.Timestamp("2025-03-10"),
                         pd.Timestamp("2025-07-06"), pd.Timestamp("2025-03-07")]
    assert dt.day_close_utc(got[:1])[0] == _utc("2025-03-04 22:00")


def test_a_short_day_is_a_stub_and_left_out_of_the_daily_inputs():
    bars = _days_bars([("2025-03-03", 100, 101, 99, 100.5), ("2025-03-04", 100.5, 102, 100, 101)])
    bars = pd.concat([bars, bars.iloc[24:30].assign(ts_utc=lambda d: d["ts_utc"] + pd.Timedelta(days=1))])
    daily = dt.daily_bars(bars)
    assert list(daily["stub"]) == [False, False, True]                  # six hours on the third day
    assert len(dt.daily_inputs(daily)) == 2


def test_daily_bars_refuse_bars_out_of_time_order():
    bars = _days_bars([("2025-03-03", 100, 101, 99, 100.5)])
    with pytest.raises(ValueError, match="time order"):
        dt.daily_bars(bars.iloc[::-1])


# --------------------------------------------------------------------------- H-16's target: the session window
@pytest.mark.parametrize("label,inside,outside", [
    ("2025-01-15", ["08:00", "21:15"], ["07:45", "21:30"]),     # winter: 08:00 UTC .. 16:30 NY = 21:30 UTC
    ("2025-07-16", ["07:00", "20:15"], ["06:45", "20:30"]),     # summer: 07:00 UTC .. 16:30 NY = 20:30 UTC
])
def test_the_session_window_runs_from_0800_london_to_1630_new_york_on_local_clocks(label, inside, outside):
    ts = pd.date_range(pd.Timestamp(label, tz="UTC") - pd.Timedelta(hours=2), periods=96, freq="15min")
    bars = pd.DataFrame({"ts_utc": ts, "open": 100.0, "close": 100.0, "high": 100.5, "low": 99.5})
    base = dt.daily_bars(bars)["window_range"].iloc[0]
    assert base == pytest.approx(1.0)
    for hhmm, expect in [*[(h, True) for h in inside], *[(h, False) for h in outside]]:
        spiked = bars.copy()
        spiked.loc[ts.strftime("%H:%M") == hhmm, "high"] = 110.0
        got = dt.daily_bars(spiked)["window_range"].iloc[0]
        assert bool(got == pytest.approx(10.5)) is expect, hhmm


def test_the_har_target_is_the_window_range_over_the_atr_visible_at_the_open():
    inp = dt.daily_inputs(dt.daily_bars(_bars("2024-01-01", "2024-04-30")))
    k = 60
    assert inp["w"].iloc[k] == pytest.approx(inp["window_range"].iloc[k] / inp["atr"].iloc[k - 1])
    assert inp["y"].iloc[k] == pytest.approx(np.log(inp["w"].iloc[k]))
    assert inp["atr"].iloc[k] == pytest.approx(inp["tr"].iloc[k - 19:k + 1].mean())
    assert inp["x5"].iloc[k] == pytest.approx(np.log(inp["w"].iloc[k - 4:k + 1].mean()))
    assert inp["x22_t"].iloc[k] == pytest.approx(np.log(inp["w"].iloc[k - 22:k].mean()))
    assert inp["window_range"].iloc[k] < inp["range"].iloc[k] + 1e-9      # a sub-window of the day


def test_on_4h_bars_the_window_and_the_noise_ratio_are_nan(enabled):
    bars = _bars("2025-01-06", "2025-03-01", freq="4h")
    X = build_features(bars, [enabled], {})
    assert dt.daily_bars(bars)["window_range"].isna().all()
    assert X["opp_har_range_hat"].isna().all() and X["opp_noise_ratio"].isna().all()


# --------------------------------------------------------------------------- prior-day columns
def test_prior_day_range_nr4_nr7_inside_day_on_completed_stub_free_days(enabled):
    labels = pd.bdate_range("2025-01-06", periods=30)
    ranges = [4.0] * 22 + [5.0, 3.0, 6.0, 2.5, 2.0, 7.0, 1.0, 3.0]       # day 28 is NR7 and inside day 27
    days = [(str(lab.date()), 100.0, 100.0 + r / 2, 100.0 - r / 2, 100.0) for lab, r in zip(labels, ranges, strict=True)]
    bars = _days_bars(days)
    X = build_features(bars, [enabled], {})
    inp = dt.daily_inputs(dt.daily_bars(bars))
    tr = np.array(ranges)                                               # flat closes: true range = range
    k = 28
    on_next = (bars["ts_utc"] >= dt.day_close_utc(pd.DatetimeIndex([labels[k]]))[0]).to_numpy()
    first_next = int(np.flatnonzero(on_next)[0])
    assert X["opp_prev_range_atr"].iloc[first_next] == pytest.approx(ranges[k] / tr[k - 19:k + 1].mean())
    assert X["opp_nr4"].iloc[first_next] == 1.0 and X["opp_nr7"].iloc[first_next] == 1.0
    assert X["opp_inside_day"].iloc[first_next] == 1.0
    last_k = first_next - 1                                             # on day k: the columns describe day k-1
    assert X["opp_nr4"].iloc[last_k] == 0.0 and X["opp_inside_day"].iloc[last_k] == 0.0
    assert X["opp_prev_range_atr"].iloc[last_k] == pytest.approx(ranges[k - 1] / tr[k - 20:k].mean())
    assert inp["nr4"].iloc[k - 4] == 0.0 and inp["nr4"].iloc[k - 2] == 1.0
    assert inp["nr7"].iloc[k - 4] == 0.0 and inp["nr7"].iloc[k - 2] == 1.0


def test_the_reopen_gap_anchors_on_the_first_bar_at_or_after_the_1800_new_york_reopen(enabled):
    labels = pd.bdate_range("2025-01-06", periods=30)
    bars = _days_bars([(str(lab.date()), 100.0, 101.0, 99.0, 100.0) for lab in labels])
    monday = labels[-5]
    assert monday.dayofweek == 0
    t = pd.DatetimeIndex(bars["ts_utc"])
    sunday_22 = int(np.flatnonzero(t == pd.Timestamp(monday, tz="UTC") - pd.Timedelta(hours=2))[0])
    reopen = sunday_22 + 1                                              # 23:00 UTC = 18:00 New York in winter
    bars.loc[sunday_22, ["open", "high", "low", "close"]] = [104.0, 104.0, 104.0, 100.0]   # a stray pre-reopen quote
    bars.loc[reopen, ["open", "high"]] = [103.0, 103.0]
    X = build_features(bars, [enabled], {})
    assert np.isnan(X["opp_gap_atr"].iloc[sunday_22])                  # before the reopen: no gap for the day yet
    assert X["opp_gap_atr"].iloc[reopen] == pytest.approx(3.0 / 2.0)   # |103 - 100| / ATR20 of 2.0-wide days
    assert X["opp_gap_atr"].iloc[reopen + 5] == pytest.approx(1.5)
    assert X["opp_gap_atr"].iloc[sunday_22 - 1] == pytest.approx(0.0)  # Friday's own reopen had no gap


# --------------------------------------------------------------------------- HAR fit, refit schedule, bands
def test_har_fit_uses_only_rows_whose_target_closed_before_the_refit_instant():
    inp = dt.daily_inputs(dt.daily_bars(_bars("2024-01-01", "2024-12-31")))
    at = _utc("2024-09-30 22:00")
    fit = dt.fit_har(inp, at, min_rows=50)
    assert fit.coef is not None and len(fit.coef) == 4 and fit.columns == dt.TARGET_REGRESSORS
    assert fit.last_target_close is not None and fit.last_target_close <= at - dt.FIT_PURGE
    used = inp[(inp["close_utc"] <= at - dt.FIT_PURGE) & inp["y"].notna()
               & inp[list(dt.TARGET_REGRESSORS)].notna().all(axis=1)]
    assert fit.n_rows == len(used)
    later = inp["close_utc"] > at - dt.FIT_PURGE
    assert dt.fit_har(inp[~later], at, min_rows=50).coef == pytest.approx(fit.coef)
    junk = inp.copy()
    junk.loc[later, ["y", *dt.TARGET_REGRESSORS]] = 7.0
    assert dt.fit_har(junk, at, min_rows=50).coef == pytest.approx(fit.coef)
    purged = inp.index[(inp["close_utc"] > at - dt.FIT_PURGE) & (inp["close_utc"] <= at)]
    j2 = inp.copy()
    j2.loc[purged, "y"] = 9.0
    assert dt.fit_har(j2, at, min_rows=50).coef == pytest.approx(fit.coef)
    j3 = inp.copy()
    j3.loc[used.index[-1], "y"] = 9.0
    assert dt.fit_har(j3, at, min_rows=50).coef != pytest.approx(fit.coef)
    assert dt.fit_har(inp, _utc("2024-02-01"), min_rows=50).coef is None


def test_har_fit_takes_any_column_list_and_stores_its_in_sample_fitted_quantiles():
    inp = dt.daily_inputs(dt.daily_bars(_bars("2024-01-01", "2024-12-31")))
    inp["extra_t"] = np.log(inp["prev_range_atr"].shift(1))            # E-4's full model adds columns like this
    at = _utc("2024-09-30 22:00")
    cols = (*dt.TARGET_REGRESSORS, "extra_t")
    fit = dt.fit_har(inp, at, regressors=cols, min_rows=50)
    assert fit.coef is not None and len(fit.coef) == 5 and fit.columns == cols
    used = inp[(inp["close_utc"] <= at - dt.FIT_PURGE)][["y", *cols]].dropna()
    X = np.column_stack([np.ones(len(used)), used[list(cols)].to_numpy()])
    fitted = X @ np.array(fit.coef)
    assert fit.n_rows == len(used)
    assert fit.fitted_quantiles == pytest.approx(tuple(np.quantile(fitted, dt.BAND_QUANTILES)))
    base = dt.fit_har(inp, at, min_rows=50)
    assert base.fitted_quantiles is not None and base.fitted_quantiles[0] < base.fitted_quantiles[2]


def test_the_refit_instant_is_the_close_of_the_last_weekday_before_the_month():
    inp = dt.daily_inputs(dt.daily_bars(_bars("2024-01-01", "2024-12-31")))
    refits = set(dt.monthly_refits(inp))
    assert _utc("2024-08-30 22:00") in refits                          # 31 Aug is a Saturday: Friday's close
    assert _utc("2024-09-30 22:00") in refits                          # Monday 30 Sep
    assert _utc("2024-05-31 22:00") in refits and _utc("2024-06-30 22:00") not in refits   # 30 Jun is a Sunday
    assert _utc("2024-06-28 22:00") in refits


def test_each_forecast_uses_the_fit_in_force_and_monday_after_a_weekend_month_end_gets_the_new_fit():
    inp = dt.daily_inputs(dt.daily_bars(_bars("2024-01-01", "2024-12-31")))
    fc = dt.har_forecast(inp, min_rows=50)
    assert list(fc.columns) == ["hat", "cut33", "cut50", "cut80", "band"]
    fri = int(np.flatnonzero((inp["close_utc"] == _utc("2024-08-30 22:00")).to_numpy())[0])   # targets Mon 2 Sep
    sept = dt.fit_har(inp, _utc("2024-08-30 22:00"), min_rows=50)
    aug = dt.fit_har(inp, _utc("2024-07-31 22:00"), min_rows=50)
    x = np.array([1.0, *inp[list(dt.EMIT_REGRESSORS)].iloc[fri]])
    assert fc["hat"].iloc[fri] == pytest.approx(float(np.exp(x @ np.array(sept.coef))))
    x_thu = np.array([1.0, *inp[list(dt.EMIT_REGRESSORS)].iloc[fri - 1]])          # Thursday: still August's fit
    assert fc["hat"].iloc[fri - 1] == pytest.approx(float(np.exp(x_thu @ np.array(aug.coef))))
    assert sept.fitted_quantiles is not None
    q33, q50, q80 = np.exp(sept.fitted_quantiles)
    assert (fc["cut33"].iloc[fri], fc["cut50"].iloc[fri], fc["cut80"].iloc[fri]) == pytest.approx((q33, q50, q80))
    h = fc["hat"].to_numpy()
    expect = np.where(np.isnan(h), np.nan, np.where(h < fc["cut33"], 0.0, np.where(h < fc["cut80"], 1.0, 2.0)))
    assert np.allclose(fc["band"], expect, equal_nan=True)
    assert np.isnan(fc["hat"].to_numpy()[:50]).all()
    one = dt.har_forecast(inp, refit_at=pd.DatetimeIndex([_utc("2024-08-30 22:00")]), min_rows=50)
    before = (inp["close_utc"] < _utc("2024-08-30 22:00")).to_numpy()
    assert one["hat"][before].isna().all() and one["hat"][~before].notna().all()


def test_the_feature_uses_the_ctx_refit_hook_and_emits_the_cuts(enabled):
    bars = _bars("2024-01-01", "2024-12-31")
    ctx = {"day_type_refit_at": [_utc("2024-06-28 22:00")], "day_type_min_fit_rows": 50}
    X = build_features(bars, [enabled], ctx)
    t = pd.DatetimeIndex(bars["ts_utc"])
    assert X.loc[t < _utc("2024-06-28 22:00"), "opp_har_range_hat"].isna().all()
    late = t > _utc("2024-08-01")
    assert X.loc[late, ["opp_har_range_hat", "opp_har_cut33", "opp_har_cut50", "opp_har_cut80", "opp_har_band"]
                 ].notna().all().all()
    assert X.loc[late, "opp_har_cut33"].nunique() == 1                  # one fit in force: one set of cuts
    assert set(X.loc[late, "opp_har_band"].unique()) <= {0.0, 1.0, 2.0}


# --------------------------------------------------------------------------- GVZ over realised volatility
def test_driver_frames_keep_the_value_date():
    vd = pd.date_range("2025-01-02", periods=30, freq=US_BDAY)          # FRED has no holiday rows
    fr = driver_frames(fred_frame("GVZCLS", pd.DataFrame({"value_date": vd, "value": 15.0}), pd.Timestamp("2025-03-01")))
    assert list(fr["macro_gvz"]["value_date"]) == list(vd)


def test_gvz_at_a_monday_0700_bar_is_the_previous_thursday_close(enabled):
    bars = _bars("2025-01-06", "2025-03-12")
    vd = pd.bdate_range("2024-12-02", "2025-03-07")
    obs = pd.DataFrame({"value_date": vd, "value": 10.0 + np.arange(len(vd))})
    X = build_features(bars, [enabled], {"macro": fred_frame("GVZCLS", obs, pd.Timestamp("2025-03-08"))})
    mon = (bars["ts_utc"] == _utc("2025-03-10 07:00")).to_numpy()
    thursday = 10.0 + float(np.flatnonzero(vd == pd.Timestamp("2025-03-06"))[0])
    daily = dt.daily_bars(bars)
    s = daily[~daily["stub"]].reset_index(drop=True)

    def yz_at(t: str) -> float:
        done = s[s["close_utc"] <= _utc(t)].reset_index(drop=True)      # completed stub-free days only
        return float(yang_zhang(done, 20)[-1] * np.sqrt(252) * 100)
    assert X.loc[mon, "opp_gvz_rv"].item() == pytest.approx(thursday / yz_at("2025-03-10 07:00"))
    assert X.loc[mon, "opp_gvz_stale"].item() == 0.0
    tue = (bars["ts_utc"] == _utc("2025-03-11 00:00")).to_numpy()      # Friday's value: public Monday 23:00 UTC
    assert X.loc[tue, "opp_gvz_rv"].item() == pytest.approx((thursday + 1) / yz_at("2025-03-11 00:00"))


def test_gvz_goes_stale_after_five_us_business_days_counted_on_value_date(enabled):
    bars = _bars("2025-01-06", "2025-02-22")
    vd = pd.bdate_range("2024-12-02", "2025-02-10")                     # last value: Monday 10 Feb
    X = build_features(bars, [enabled], {"macro": fred_frame("GVZCLS", pd.DataFrame({"value_date": vd, "value": 15.0}),
                                                             pd.Timestamp("2025-03-01"))})
    t = pd.DatetimeIndex(bars["ts_utc"])

    def at(s: str) -> pd.Series:
        return X.loc[np.flatnonzero(t == _utc(s))[0], ["opp_gvz_rv", "opp_gvz_stale"]]
    # 10, 11, 12, 13, 14 Feb (17 Feb is Presidents Day): still five business days on Tuesday 18 Feb
    assert at("2025-02-18 10:00")["opp_gvz_stale"] == 0.0 and np.isfinite(at("2025-02-18 10:00")["opp_gvz_rv"])
    assert at("2025-02-19 10:00")["opp_gvz_stale"] == 1.0 and np.isnan(at("2025-02-19 10:00")["opp_gvz_rv"])
    assert np.isnan(build_features(bars, [enabled], {})["opp_gvz_stale"]).all()      # no macro: unknown, not stale


# --------------------------------------------------------------------------- noise ratio (DST-aware)
def _session_bars(start: str, n_days: int, freq: str = "15min") -> pd.DataFrame:
    """Bars on n weekdays; each day's price path is the same function of London local time."""
    rows = []
    per_day = int(pd.Timedelta("1D") / pd.Timedelta(freq))
    for lab in pd.bdate_range(start, periods=n_days):
        ts = pd.date_range(pd.Timestamp(lab, tz="UTC") - pd.Timedelta(hours=2), periods=per_day, freq=freq)
        local = ts.tz_convert("Europe/London")
        m = (local.hour * 60 + local.minute).to_numpy(dtype=float)
        p = 100 + np.sin(m / 90.0) + m / 300.0
        rows.append(pd.DataFrame({"ts_utc": ts, "open": p, "close": p + 0.05, "high": p + 0.1, "low": p - 0.1}))
    df = pd.concat(rows, ignore_index=True)
    df["spread"], df["tick_count"] = 0.2, 10
    return df


@pytest.mark.parametrize("start,london_open_utc,ny_open_utc", [("2025-01-06", "08:00", "13:30"),
                                                              ("2025-06-02", "07:00", "12:30")])
def test_noise_ratio_anchors_on_the_local_london_then_new_york_open(enabled, start, london_open_utc, ny_open_utc):
    bars = _session_bars(start, 20)
    rng, session, since = dt.noise_components(bars)
    t = pd.DatetimeIndex(bars["ts_utc"])
    hhmm = t.strftime("%H:%M")
    day0 = t.normalize() == t[96 + 8].normalize()
    lo = np.flatnonzero(day0 & (hhmm == london_open_utc))[0]
    ny = np.flatnonzero(day0 & (hhmm == ny_open_utc))[0]
    assert np.isnan(rng[lo - 1]) and session[lo] == dt.LONDON and since[lo] == 0
    assert rng[lo] == pytest.approx(bars["high"].iloc[lo] - bars["low"].iloc[lo])
    assert session[ny] == dt.NEWYORK and since[ny] == 0
    assert rng[ny] == pytest.approx(bars["high"].iloc[ny] - bars["low"].iloc[ny])   # the range restarts at NY
    assert session[ny - 1] == dt.LONDON
    X = build_features(bars, [enabled], {})
    ratio = X["opp_noise_ratio"].to_numpy()
    first14 = t < pd.Timestamp(pd.bdate_range(start, periods=15)[-1], tz="UTC") - pd.Timedelta(hours=2)
    assert np.isnan(ratio[first14]).all()
    in_session = ~np.isnan(rng) & ~first14
    assert in_session.any() and np.allclose(ratio[in_session], 1.0)


def test_on_hourly_bars_the_new_york_session_starts_on_the_bar_containing_0830(enabled):
    bars = _session_bars("2025-01-06", 20, freq="1h")
    rng, session, since = dt.noise_components(bars)
    t = pd.DatetimeIndex(bars["ts_utc"])
    day0 = t.normalize() == t[24 + 4].normalize()
    ny = np.flatnonzero(day0 & (t.strftime("%H:%M") == "13:00"))[0]   # 08:00-09:00 New York holds 08:30
    assert session[ny] == dt.NEWYORK and since[ny] == -30
    assert rng[ny] == pytest.approx(bars["high"].iloc[ny] - bars["low"].iloc[ny])
    assert session[ny - 1] == dt.LONDON


# --------------------------------------------------------------------------- tier-1 calendar
def _calendar() -> pd.DataFrame:
    def row(eid: str, tier: int, event: str, avail: str, status: str = "scheduled") -> dict:
        return {"event_id": eid, "event_type": eid.split(":")[0], "tier": tier, "release_id": -1,
                "event_utc": _utc(event), "status": status, "available_utc": _utc(avail), "ts_utc": _utc(avail),
                "vintage": pd.Timestamp("2025-03-01"), "source": "fred"}
    return pd.DataFrame([
        row("cpi:2025-03-12", 1, "2025-03-12 12:30", "2025-03-05 12:30"),
        row("fomc_statement:2025-03-12", 1, "2025-03-12 18:00", "2025-03-05 18:00"),
        row("fomc_press_conference:2025-03-12", 1, "2025-03-12 18:30", "2025-03-05 18:30"),   # same release
        row("ppi:2025-03-12", 2, "2025-03-12 12:30", "2025-03-05 12:30"),
        row("nfp:2025-03-14", 1, "2025-03-14 12:30", "2025-03-14 10:00"),                     # added late
        row("pce:2025-03-13", 1, "2025-03-13 12:30", "2025-03-06 12:30"),
        row("pce:2025-03-13", 1, "2025-03-13 12:30", "2025-03-12 21:00", "cancelled"),
    ])


def test_tier1_columns_are_nan_without_the_calendar():
    out = dt.tier1_columns(_bars("2025-03-10", "2025-03-12"), None)
    assert list(out.columns) == ["opp_tier1_today", "opp_min_to_first_tier1"] and out.isna().all().all()


def test_tier1_columns_count_distinct_known_releases_of_the_day(enabled):
    bars = _bars("2025-03-03", "2025-03-15")
    out = dt.tier1_columns(bars, _calendar())
    t = pd.DatetimeIndex(bars["ts_utc"])

    def at(s: str) -> list:
        return out.loc[np.flatnonzero(t == _utc(s))[0]].tolist()
    assert np.isnan(at("2025-03-04 10:00")).all()
    assert at("2025-03-11 23:00") == [2.0, pytest.approx(13 * 60 + 30)]       # CPI + FOMC (press = same release)
    assert at("2025-03-12 15:00") == [2.0, -150.0]
    assert at("2025-03-12 22:00") == [0.0, dt.NO_EVENT_MIN]                   # Thursday: PCE cancelled
    assert at("2025-03-14 09:00") == [0.0, dt.NO_EVENT_MIN]                   # NFP not known yet
    assert at("2025-03-14 10:00") == [1.0, 150.0]
    X = build_features(bars, [enabled], {"us_calendar": _calendar()})
    assert np.allclose(X["opp_tier1_today"], out["opp_tier1_today"], equal_nan=True)


@pytest.mark.integration
def test_tier1_columns_read_the_calendar_release_file(tmp_path):
    from goldbot.data.release import read_us_calendar_files
    cpi = next(r for r in uc.RELEASES if r.event_type == "cpi")
    v = pd.Timestamp("2025-01-01")
    day = pd.Timestamp("2025-03-19")                                           # FOMC with press conference
    rows = pd.concat([
        uc.event_rows("cpi", cpi.release_id, pd.DatetimeIndex(["2025-03-12"]), cpi.at_et, v, "fred"),
        uc.event_rows("fomc_statement", -1, pd.DatetimeIndex([day]), uc.fomc_statement_et(day, True), v, "fed"),
        uc.event_rows("fomc_press_conference", -1, pd.DatetimeIndex([day]), uc.fomc_press_et(day), v, "fed"),
        uc.event_rows("ppi", 46, pd.DatetimeIndex(["2025-03-13"]), time(8, 30), v, "fred"),
    ], ignore_index=True)
    uc.write_release(rows, str(tmp_path / uc.ASSET), {})
    cal = read_us_calendar_files(tmp_path)
    bars = _bars("2025-03-10", "2025-03-21")
    out = dt.tier1_columns(bars, cal)
    t = pd.DatetimeIndex(bars["ts_utc"])

    def at(s: str) -> list:
        return out.loc[np.flatnonzero(t == _utc(s))[0]].tolist()
    assert at("2025-03-12 06:00") == [1.0, pytest.approx(6.5 * 60)]           # CPI 08:30 New York = 12:30 UTC (EDT)
    assert at("2025-03-19 06:00") == [1.0, pytest.approx(12 * 60)]            # statement 14:00 NY; press not counted
    assert at("2025-03-13 06:00") == [0.0, dt.NO_EVENT_MIN]                   # PPI is tier 2


# --------------------------------------------------------------------------- point in time, every column
def _full_ctx(start: str = "2021-06-01", end: str = "2024-12-31") -> dict:
    vd = pd.bdate_range(start, end)
    obs = pd.DataFrame({"value_date": vd, "value": 15 + 3 * np.sin(np.arange(len(vd)) / 20)})
    cal = []
    for d in pd.date_range(pd.Timestamp(start) + pd.Timedelta(days=9), end, freq="7D"):
        ev = pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=13, minutes=30)
        cal.append({"event_id": f"cpi:{d.date()}", "event_type": "cpi", "tier": 1, "release_id": -1, "event_utc": ev,
                    "status": "scheduled", "available_utc": ev - pd.Timedelta(days=7),
                    "ts_utc": ev - pd.Timedelta(days=7), "vintage": pd.Timestamp("2025-01-01"), "source": "fred"})
    return {"macro": fred_frame("GVZCLS", obs, pd.Timestamp("2025-01-01")), "us_calendar": pd.DataFrame(cal)}


@pytest.fixture(scope="module")
def long_bars() -> pd.DataFrame:
    """30-minute bars 2021-06 .. 2024-08 with a stub day (Memorial Day 2024: six hours of bars)."""
    b = _bars("2021-06-01", "2024-08-31", freq="30min", seed=5)
    t = pd.DatetimeIndex(b["ts_utc"])
    stub = (dt.opportunity_day(t) == pd.Timestamp("2024-05-27")) & (t >= _utc("2024-05-27 04:00"))
    return b[~np.asarray(stub)].reset_index(drop=True)


@pytest.fixture(scope="module")
def long_full(long_bars) -> pd.DataFrame:
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    try:
        return build_features(long_bars, [dt.enable()], _full_ctx())
    finally:
        registry.FEATURES.clear()
        registry.FEATURES.update(feats)
        registry.SIGNED.clear()
        registry.SIGNED.update(signed)


def _cut_ctx(ctx: dict, cut: pd.Timestamp) -> dict:
    return {k: v[pd.to_datetime(v["available_utc"], utc=True) <= cut] for k, v in ctx.items()}


@pytest.mark.parametrize("cut", [
    "2024-04-30 21:30",          # weekday month-end, last bar before the refit instant
    "2024-04-30 22:00",          # weekday month-end, first bar of the new month's fit
    "2024-06-28 22:00",          # weekend month-end: Friday's close is the July refit instant
    "2024-06-30 22:00",          # the Sunday reopen after it
    "2024-05-27 02:00",          # inside the stub day
    "2024-05-28 01:00",          # the day after the stub
    "2024-03-13 13:00",          # DST gap week (New York on summer time, London not yet)
])
def test_every_column_on_truncated_history_matches_the_full_history_before_the_cut(enabled, long_bars, long_full, cut):
    ctx = _full_ctx()
    n = int(pd.DatetimeIndex(long_bars["ts_utc"]).searchsorted(_utc(cut), side="right"))
    part = build_features(long_bars.iloc[:n], [enabled], _cut_ctx(ctx, _utc(cut)))
    for c in dt.COLUMNS:
        a, b = long_full[c].to_numpy()[:n], part[c].to_numpy()
        assert np.isfinite(a[-3000:]).any(), c                          # the column carries values near the cut
        assert np.allclose(a, b, equal_nan=True), (c, cut)


def test_no_column_moves_when_later_prices_change_or_a_side_column_is_present(enabled, long_bars, long_full):
    ctx = _full_ctx()
    n = int(len(long_bars) * 0.93)
    shocked = long_bars.copy()
    f = np.exp(np.random.default_rng(1).normal(0, 0.02, len(shocked) - n))
    for c in ("open", "high", "low", "close"):
        shocked.loc[n:, c] = shocked.loc[n:, c].to_numpy() * f
    after = build_features(shocked, [enabled], ctx)
    with_side = build_features(long_bars.assign(side=-1), [enabled], ctx)
    for c in dt.COLUMNS:
        assert np.allclose(long_full[c].to_numpy()[:n], after[c].to_numpy()[:n], equal_nan=True), c
        assert np.allclose(long_full[c], with_side[c], equal_nan=True), c


@pytest.mark.integration
def test_the_pipeline_lookahead_check_reports_no_day_type_column(enabled):
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.research.pipeline import lookahead_check
    bars = resample_bars(ticks_to_1m(synthetic_ticks("2025-01-06", "2025-04-26", ticks_per_minute=1, seed=7)),
                         "15m").reset_index(drop=True)
    ctx: dict = {**_full_ctx("2024-11-01", "2025-06-30"), "day_type_min_fit_rows": 10}
    out = lookahead_check(bars, None, feature_names=[enabled], ctx=ctx)
    assert out["lookahead_columns"] == [] and out["columns_checked"] == len(dt.COLUMNS)
