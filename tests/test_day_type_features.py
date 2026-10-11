"""Opt-in `day_type` opportunity features (BACKLOG 30a; docs/research/opportunity-days.md section 3.1, H-16, H-20,
H-21): opportunity days on the 22:00 UTC rollover, the HAR range forecast and its monthly expanding refit, GVZ over
realised volatility, prior-day range, NR4/NR7, inside day, reopen gap, the DST-aware noise ratio and the tier-1
calendar columns. Every column is checked point in time (truncation and perturbation after the cut)."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data.macro import fred_frame
from goldbot.features import build_features, registry
from goldbot.features import day_type as dt
from goldbot.features.survey import yang_zhang

FROZEN_DEFAULT_VERSION = "f-079deacc61"      # sessions v2: the Q1 2027 H-02 universe (preregistration-2027Q1.md)
FROZEN_MACRO_VERSION = "f-f999b359e2"        # default + macro drivers (H-16's base version)


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
    wig = np.abs(sig * rng.standard_normal(len(ts))) * close
    high = np.maximum(open_, close) + wig
    low = np.minimum(open_, close) - np.abs(sig * rng.standard_normal(len(ts))) * close
    return pd.DataFrame({"ts_utc": ts, "open": open_, "high": high, "low": low, "close": close,
                         "spread": 0.2, "tick_count": 10})


def _days_bars(days: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """Hourly bars realising given daily (label, open, high, low, close) on opportunity days: 24 bars from the previous
    calendar day's 22:00 UTC; the high is printed at the 6th bar and the low at the 12th."""
    rows = []
    for label, o, h, lo, c in days:
        t0 = pd.Timestamp(label, tz="UTC") - pd.Timedelta(hours=2)
        path = np.linspace(o, c, 25)
        for i in range(24):
            a, b = path[i], path[i + 1]
            rows.append({"ts_utc": t0 + pd.Timedelta(hours=i), "open": a, "close": b,
                         "high": h if i == 5 else max(a, b), "low": lo if i == 11 else min(a, b)})
    df = pd.DataFrame(rows)
    df["spread"], df["tick_count"] = 0.2, 10
    return df


# --------------------------------------------------------------------------- opt-in registration
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
        assert feature_version_of(DEFAULT_FEATURE_NAMES) == FROZEN_DEFAULT_VERSION
        assert feature_version_of([*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES]) == FROZEN_MACRO_VERSION
        assert research_feature_names({"macro": pd.DataFrame({"x": [1]})}) == [*DEFAULT_FEATURE_NAMES,
                                                                              *MACRO_FEATURE_NAMES]
        assert feature_version_of([*DEFAULT_FEATURE_NAMES, name]) != FROZEN_DEFAULT_VERSION
        assert registry.FEATURES[name].family == "day_type" and registry.FEATURES[name].signed == {}
    finally:
        registry.FEATURES.clear()
        registry.FEATURES.update(feats)
        registry.SIGNED.clear()
        registry.SIGNED.update(signed)


def feature_version_of(names: list[str]) -> str:
    return registry.feature_version(names)


def test_the_column_set_is_fixed_and_nan_without_history_or_inputs(enabled):
    bars = _bars("2025-01-06", "2025-01-07")                          # one day: no completed earlier day
    X = build_features(bars, [enabled], {})
    assert list(X.columns.drop("ts_utc")) == list(dt.COLUMNS)
    assert X.drop(columns=["ts_utc"]).isna().all().all()


def test_research_pass_adds_day_type_only_with_the_flag(enabled):
    from goldbot.research.pipeline import research_feature_names
    spec = importlib.util.spec_from_file_location(
        "research_pass_day_type", Path(__file__).resolve().parents[1] / "scripts" / "research_pass.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.feature_ctx(None, None) == (None, None)
    ctx, names = mod.feature_ctx(None, None, day_type=True)
    assert names == [*research_feature_names(ctx), "day_type"]
    info = mod.day_type_info(True)
    assert info["used"] is True and info["version"] == dt.VERSION and info["family"] == "day_type"
    assert mod.day_type_info(False)["used"] is False


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


# --------------------------------------------------------------------------- prior-day columns
def test_prior_day_range_nr4_nr7_inside_day_on_completed_stub_free_days(enabled):
    labels = pd.bdate_range("2025-01-06", periods=30)
    ranges = [4.0] * 22 + [5.0, 3.0, 6.0, 2.5, 2.0, 7.0, 1.0, 3.0]       # day 28 (index 28) is NR7 and inside day 27
    days = []
    for lab, r in zip(labels, ranges, strict=True):
        days.append((str(lab.date()), 100.0, 100.0 + r / 2, 100.0 - r / 2, 100.0))
    bars = _days_bars(days)
    X = build_features(bars, [enabled], {})
    inp = dt.daily_inputs(dt.daily_bars(bars))
    tr = np.array(ranges)                                               # flat closes: true range = range
    k = 28
    atr_k = tr[k - 19:k + 1].mean()
    on_next = (bars["ts_utc"] >= dt.day_close_utc(pd.DatetimeIndex([labels[k]]))[0]).to_numpy()
    first_next = int(np.flatnonzero(on_next)[0])
    assert X["opp_prev_range_atr"].iloc[first_next] == pytest.approx(ranges[k] / atr_k)
    assert X["opp_nr4"].iloc[first_next] == 1.0 and X["opp_nr7"].iloc[first_next] == 1.0
    assert X["opp_inside_day"].iloc[first_next] == 1.0
    # on day k itself (before its close) the columns describe day k-1 (range 7, not narrow, not inside)
    last_k = first_next - 1
    assert X["opp_nr4"].iloc[last_k] == 0.0 and X["opp_inside_day"].iloc[last_k] == 0.0
    assert X["opp_prev_range_atr"].iloc[last_k] == pytest.approx(ranges[k - 1] / tr[k - 20:k].mean())
    assert inp["nr4"].iloc[k - 4] == 0.0 and inp["nr4"].iloc[k - 2] == 1.0    # 6.0 is not narrow; 2.0 is (of 4)
    assert inp["nr7"].iloc[k - 4] == 0.0 and inp["nr7"].iloc[k - 2] == 1.0    # 6.0 is not; 2.0 is (of 7)


def test_the_reopen_gap_is_the_first_open_against_the_last_close_over_atr(enabled):
    labels = pd.bdate_range("2025-01-06", periods=30)
    days = [(str(lab.date()), 100.0, 101.0, 99.0, 100.0) for lab in labels]
    bars = _days_bars(days)
    monday = labels[-5]                                                 # a Monday: Sunday reopen after Friday
    assert monday.dayofweek == 0
    first = int(np.flatnonzero((bars["ts_utc"] >= pd.Timestamp(monday, tz="UTC") - pd.Timedelta(hours=2)).to_numpy())[0])
    for c in ("open", "high"):
        bars.loc[first, c] = 103.0                                      # gap up 3.0 at the reopen
    bars.loc[first, "low"] = 100.0
    X = build_features(bars, [enabled], {})
    assert X["opp_gap_atr"].iloc[first] == pytest.approx(3.0 / 2.0)     # ATR20 of 2.0-wide flat days
    assert X["opp_gap_atr"].iloc[first + 5] == pytest.approx(1.5)       # constant through the day
    assert X["opp_gap_atr"].iloc[first - 1] == pytest.approx(0.0)       # Friday's own reopen had no gap


# --------------------------------------------------------------------------- HAR range forecast
def test_har_fit_uses_only_rows_whose_target_closed_before_the_refit_instant():
    inp = dt.daily_inputs(dt.daily_bars(_bars("2024-01-01", "2024-12-31")))
    at = _utc("2024-09-30 22:00")
    fit = dt.fit_har(inp, at, min_rows=50)
    assert fit.coef is not None and len(fit.coef) == 4
    assert fit.last_target_close is not None and fit.last_target_close <= at - dt.FIT_PURGE
    used = inp[(inp["close_utc"] <= at - dt.FIT_PURGE) & inp["y"].notna()
               & inp[list(dt.TARGET_REGRESSORS)].notna().all(axis=1)]
    assert fit.n_rows == len(used)
    # truncation: removing or rewriting every later row leaves the coefficients unchanged
    later = inp["close_utc"] > at - dt.FIT_PURGE
    assert dt.fit_har(inp[~later], at, min_rows=50).coef == pytest.approx(fit.coef)
    junk = inp.copy()
    junk.loc[later, ["y", *dt.TARGET_REGRESSORS]] = 7.0
    assert dt.fit_har(junk, at, min_rows=50).coef == pytest.approx(fit.coef)
    # the row inside the 1-day purge is not used either; an earlier row is
    purged = inp.index[(inp["close_utc"] > at - dt.FIT_PURGE) & (inp["close_utc"] <= at)]
    j2 = inp.copy()
    j2.loc[purged, "y"] = 9.0
    assert dt.fit_har(j2, at, min_rows=50).coef == pytest.approx(fit.coef)
    j3 = inp.copy()
    j3.loc[used.index[-1], "y"] = 9.0
    assert dt.fit_har(j3, at, min_rows=50).coef != pytest.approx(fit.coef)
    # fewer than min_rows usable rows: no fit
    assert dt.fit_har(inp, _utc("2024-02-01"), min_rows=50).coef is None


def test_har_refits_monthly_on_an_expanding_window_and_each_forecast_uses_the_fit_in_force():
    inp = dt.daily_inputs(dt.daily_bars(_bars("2024-01-01", "2024-12-31")))
    refits = dt.monthly_refits(inp)
    assert all(t.hour == 22 and (t + pd.Timedelta(hours=2)).day == 1 for t in refits)   # the month's first rollover
    hat = dt.har_forecast(inp, min_rows=50)
    k = int(np.flatnonzero((inp["close_utc"] >= _utc("2024-10-15")).to_numpy())[0])
    close_k = inp["close_utc"].iloc[k]
    at = refits[refits <= close_k].max()
    coef = np.array(dt.fit_har(inp, at, min_rows=50).coef)
    x = np.array([1.0, *inp[list(dt.EMIT_REGRESSORS)].iloc[k]])
    assert hat[k] == pytest.approx(float(np.exp(x @ coef)))
    # the fold-fit hook: any refit schedule, e.g. one fold end
    one = dt.har_forecast(inp, refit_at=pd.DatetimeIndex([_utc("2024-08-31 22:00")]), min_rows=50)
    before = (inp["close_utc"] < _utc("2024-08-31 22:00")).to_numpy()
    assert np.isnan(one[before]).all() and np.isfinite(one[~before][25:]).all()
    # history before the first refit with min_rows usable rows has no forecast
    assert np.isnan(hat[:50]).all()


def test_the_feature_uses_the_ctx_refit_hook(enabled):
    bars = _bars("2024-01-01", "2024-12-31")
    ctx = {"day_type_refit_at": [_utc("2024-06-30 22:00")], "day_type_min_fit_rows": 50}
    X = build_features(bars, [enabled], ctx)
    t = pd.DatetimeIndex(bars["ts_utc"])
    assert X.loc[t < _utc("2024-06-30 22:00"), "opp_har_range_hat"].isna().all()
    assert X.loc[t > _utc("2024-08-01"), "opp_har_range_hat"].notna().all()


# --------------------------------------------------------------------------- GVZ over realised volatility
def test_gvz_at_a_monday_0700_bar_is_the_previous_thursday_close(enabled):
    bars = _bars("2025-01-06", "2025-03-12")
    vd = pd.bdate_range("2024-12-02", "2025-03-07")
    obs = pd.DataFrame({"value_date": vd, "value": 10.0 + np.arange(len(vd))})
    macro = fred_frame("GVZCLS", obs, pd.Timestamp("2025-03-08"))
    X = build_features(bars, [enabled], {"macro": macro})
    mon = (bars["ts_utc"] == _utc("2025-03-10 07:00")).to_numpy()
    thursday = 10.0 + float(np.flatnonzero(vd == pd.Timestamp("2025-03-06"))[0])
    daily = dt.daily_bars(bars)
    s = daily[~daily["stub"]].reset_index(drop=True)

    def yz_at(t: str) -> float:
        done = s[s["close_utc"] <= _utc(t)].reset_index(drop=True)      # completed stub-free days only
        return float(yang_zhang(done, 20)[-1] * np.sqrt(252) * 100)
    assert X.loc[mon, "opp_gvz_rv"].item() == pytest.approx(thursday / yz_at("2025-03-10 07:00"))
    # Friday's value is public only on Monday 23:00 UTC
    tue = (bars["ts_utc"] == _utc("2025-03-11 00:00")).to_numpy()
    assert X.loc[tue, "opp_gvz_rv"].item() == pytest.approx((thursday + 1) / yz_at("2025-03-11 00:00"))


def test_a_stale_gvz_series_stops_feeding_the_ratio(enabled):
    bars = _bars("2025-01-06", "2025-03-11")
    vd = pd.bdate_range("2024-12-02", "2025-01-31")
    macro = fred_frame("GVZCLS", pd.DataFrame({"value_date": vd, "value": 15.0}), pd.Timestamp("2025-03-08"))
    X = build_features(bars, [enabled], {"macro": macro})
    t = pd.DatetimeIndex(bars["ts_utc"])
    assert X.loc[t > _utc("2025-02-15"), "opp_gvz_rv"].isna().all()


# --------------------------------------------------------------------------- noise ratio (DST-aware)
def _session_bars(start: str, n_days: int) -> pd.DataFrame:
    """15m bars on n weekdays; each day's price path is the same function of London local time."""
    rows = []
    for lab in pd.bdate_range(start, periods=n_days):
        ts = pd.date_range(pd.Timestamp(lab, tz="UTC") - pd.Timedelta(hours=2), periods=96, freq="15min")
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
    day0 = t.normalize() == t[96 + 8].normalize()                      # the second day's UTC date
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
    assert np.isnan(ratio[first14]).all()                              # fewer than 14 earlier days
    in_session = ~np.isnan(rng) & ~first14
    assert in_session.any() and np.allclose(ratio[in_session], 1.0)    # every day is the same path


# --------------------------------------------------------------------------- tier-1 calendar (optional input)
def _calendar() -> pd.DataFrame:
    def row(eid: str, tier: int, event: str, avail: str, status: str = "scheduled") -> dict:
        return {"event_id": eid, "event_type": eid.split(":")[0], "tier": tier, "release_id": -1,
                "event_utc": _utc(event), "status": status, "available_utc": _utc(avail), "ts_utc": _utc(avail),
                "vintage": pd.Timestamp("2025-03-01"), "source": "fred"}
    return pd.DataFrame([
        row("cpi:2025-03-12", 1, "2025-03-12 12:30", "2025-03-05 12:30"),
        row("fomc_statement:2025-03-12", 1, "2025-03-12 18:00", "2025-03-05 18:00"),
        row("ppi:2025-03-12", 2, "2025-03-12 12:30", "2025-03-05 12:30"),
        row("nfp:2025-03-14", 1, "2025-03-14 12:30", "2025-03-14 10:00"),                 # added late
        row("pce:2025-03-13", 1, "2025-03-13 12:30", "2025-03-06 12:30"),
        row("pce:2025-03-13", 1, "2025-03-13 12:30", "2025-03-12 21:00", "cancelled"),    # cancelled the day before
    ])


def test_tier1_columns_are_nan_without_the_calendar():
    bars = _bars("2025-03-10", "2025-03-12")
    out = dt.tier1_columns(bars, None)
    assert list(out.columns) == ["opp_tier1_today", "opp_min_to_first_tier1"] and out.isna().all().all()


def test_tier1_columns_count_the_day_s_known_scheduled_events(enabled):
    bars = _bars("2025-03-03", "2025-03-15")
    out = dt.tier1_columns(bars, _calendar())
    t = pd.DatetimeIndex(bars["ts_utc"])

    def at(s: str) -> pd.Series:
        return out.loc[np.flatnonzero(t == _utc(s))[0]]
    assert at("2025-03-04 10:00").isna().all()                                       # before the calendar's first row
    assert at("2025-03-11 23:00").tolist() == [2.0, pytest.approx(13 * 60 + 30)]     # Wed's day opens Tue 22:00
    assert at("2025-03-12 15:00").tolist() == [2.0, -150.0]                          # first event passed
    assert at("2025-03-12 22:00").tolist() == [0.0, dt.NO_EVENT_MIN]                 # Thursday: PCE cancelled
    assert at("2025-03-14 09:00").tolist() == [0.0, dt.NO_EVENT_MIN]                 # NFP not known yet
    assert at("2025-03-14 10:00").tolist() == [1.0, 150.0]
    X = build_features(bars, [enabled], {"us_calendar": _calendar()})
    assert np.allclose(X["opp_tier1_today"], out["opp_tier1_today"], equal_nan=True)


# --------------------------------------------------------------------------- point in time, every column
def _full_ctx(start: str = "2022-01-03", end: str = "2024-12-31") -> dict:
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
    return _bars("2022-01-03", "2024-08-31", freq="30min", seed=5)


def _cut_ctx(ctx: dict, cut: pd.Timestamp) -> dict:
    return {k: v[pd.to_datetime(v["available_utc"], utc=True) <= cut] for k, v in ctx.items()}


def test_every_column_on_truncated_history_matches_the_full_history_before_the_cut(enabled, long_bars):
    ctx = _full_ctx()
    n = int(len(long_bars) * 0.93)
    cut = long_bars["ts_utc"].iloc[n - 1]
    full = build_features(long_bars, [enabled], ctx)
    part = build_features(long_bars.iloc[:n], [enabled], _cut_ctx(ctx, cut))
    for c in dt.COLUMNS:
        a, b = full[c].to_numpy()[:n], part[c].to_numpy()
        assert np.isfinite(a[-3000:]).any(), c                    # the column carries values near the cut
        assert np.allclose(a, b, equal_nan=True), c


def test_no_column_moves_when_later_prices_change_or_a_side_column_is_present(enabled, long_bars):
    ctx = _full_ctx()
    n = int(len(long_bars) * 0.93)
    base = build_features(long_bars, [enabled], ctx)
    shocked = long_bars.copy()
    f = np.exp(np.random.default_rng(1).normal(0, 0.02, len(shocked) - n))
    for c in ("open", "high", "low", "close"):
        shocked.loc[n:, c] = shocked.loc[n:, c].to_numpy() * f
    after = build_features(shocked, [enabled], ctx)
    with_side = build_features(long_bars.assign(side=-1), [enabled], ctx)
    for c in dt.COLUMNS:
        assert np.allclose(base[c].to_numpy()[:n], after[c].to_numpy()[:n], equal_nan=True), c
        assert np.allclose(base[c], with_side[c], equal_nan=True), c


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
