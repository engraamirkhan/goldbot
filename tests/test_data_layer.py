import numpy as np
import pandas as pd
import pytest

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.quality import check_bars
from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.store import Store, asof_join
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import feature_day, feature_day_close, server_to_utc


def test_server_to_utc_handles_eu_dst():
    # 10:00 Athens: EET (+2) in January, EEST (+3) in July
    jan = server_to_utc(pd.Series(pd.to_datetime(["2025-01-15 10:00"])), "Europe/Athens")[0]
    jul = server_to_utc(pd.Series(pd.to_datetime(["2025-07-15 10:00"])), "Europe/Athens")[0]
    assert jan.hour == 8 and jul.hour == 7


def test_feature_day_boundary_at_1330_new_york():
    idx = pd.DatetimeIndex(["2025-01-15 18:29:00", "2025-01-15 18:30:00"], tz="UTC")  # 13:29 / 13:30 ET in winter
    fd = feature_day(idx)
    assert fd[0] == pd.Timestamp("2025-01-15") and fd[1] == pd.Timestamp("2025-01-16")
    # in summer settlement is 17:30 UTC
    idx_s = pd.DatetimeIndex(["2025-07-15 17:29:00", "2025-07-15 17:30:00"], tz="UTC")
    fd_s = feature_day(idx_s)
    assert fd_s[0] == pd.Timestamp("2025-07-15") and fd_s[1] == pd.Timestamp("2025-07-16")
    assert feature_day_close(pd.DatetimeIndex(["2025-07-15"]))[0] == pd.Timestamp("2025-07-15 17:30", tz="UTC")


def test_sessions_closed_on_weekend_and_daily_break():
    sat = pd.DatetimeIndex(["2025-03-08 12:00"], tz="UTC")
    assert not DEFAULT_SESSIONS.is_open(sat)[0]
    # 00:30 Athens (22:30 UTC in winter) is inside the daily break
    brk = pd.DatetimeIndex(["2025-03-05 22:30"], tz="UTC")
    assert not DEFAULT_SESSIONS.is_open(brk)[0]
    open_ = pd.DatetimeIndex(["2025-03-05 13:00"], tz="UTC")
    assert DEFAULT_SESSIONS.is_open(open_)[0]


@pytest.fixture(scope="module")
def bars1m():
    ticks = synthetic_ticks("2025-03-03", "2025-03-15", ticks_per_minute=3)
    return ticks_to_1m(ticks)


def test_ticks_to_1m_and_visibility(bars1m):
    assert not bars1m.empty
    assert (bars1m["visible_at"] - bars1m["ts_utc"] == pd.Timedelta(minutes=1)).all()
    assert (bars1m["ask_close"] >= bars1m["bid_close"]).all()
    # no bars on Saturday
    assert not (pd.DatetimeIndex(bars1m["ts_utc"]).dayofweek == 5).any()


def test_resample_15m_1h_1d(bars1m):
    b15 = resample_bars(bars1m, "15m")
    b1h = resample_bars(bars1m, "1h")
    b1d = resample_bars(bars1m, "1d")
    assert (pd.DatetimeIndex(b15["ts_utc"]).minute % 15 == 0).all()
    assert (b15["visible_at"] - b15["ts_utc"] == pd.Timedelta(minutes=15)).all()
    assert (pd.DatetimeIndex(b1h["ts_utc"]).minute == 0).all()
    # daily bars become visible at 13:30 New York (18:30 UTC in March before US DST, 17:30 after)
    vis = pd.DatetimeIndex(b1d["visible_at"]).tz_convert("America/New_York")
    assert set(vis.hour) == {13} and set(vis.minute) == {30}
    # highs contain closes
    m = mid(b1h)
    assert (m["high"] >= m["close"]).all() and (m["low"] <= m["close"]).all()


def test_store_roundtrip_and_dedup(tmp_path, bars1m):
    s = Store(tmp_path)
    n1 = s.append("bars_1m", bars1m, source="synthetic")
    n2 = s.append("bars_1m", bars1m.tail(100), source="synthetic")  # overlapping rows
    out = s.read("bars_1m", source="synthetic")
    assert n1 == len(bars1m) and n2 == 100
    assert len(out) == len(bars1m)
    assert out["ts_utc"].is_monotonic_increasing


def test_asof_join_never_leaks_future_release():
    bars = pd.DataFrame({"ts_utc": pd.date_range("2025-01-01", periods=48, freq="1h", tz="UTC")})
    macro = pd.DataFrame({
        "available_utc": pd.DatetimeIndex(["2025-01-01 21:30"], tz="UTC"),
        "real_yield_10y": [2.1],
    })
    out = asof_join(bars, macro)
    before = out[out["ts_utc"] < pd.Timestamp("2025-01-01 21:30", tz="UTC")]
    after = out[out["ts_utc"] >= pd.Timestamp("2025-01-01 21:30", tz="UTC")]
    assert before["real_yield_10y"].isna().all()
    assert (after["real_yield_10y"] == 2.1).all()
    with pytest.raises(ValueError):
        asof_join(bars, macro.rename(columns={"available_utc": "value_date"}))
    # different timestamp units on the two sides (pandas 3) join the same way
    macro_us = macro.assign(available_utc=pd.DatetimeIndex(macro["available_utc"]).as_unit("us"))
    assert asof_join(bars, macro_us)["real_yield_10y"].equals(out["real_yield_10y"])


def test_a_fake_macro_release_never_reaches_a_bar_before_its_available_utc(tmp_path):
    """Design D14: seed a fake release, run it through the release file, the store and the macro feature, and check
    that no bar sees a value (or a value derived from it) before that value's available_utc."""
    from goldbot.data.macro import US_BDAY, fred_frame
    from goldbot.data.release import load_macro_into_store, read_macro_files, store_macro
    from goldbot.features import build_features

    days = pd.date_range("2024-01-02", "2024-06-28", freq=US_BDAY)                      # FRED has no holiday rows
    gvz = pd.DataFrame({"value_date": days, "value": 10.0 + np.arange(len(days))})       # every value unique
    ry = pd.DataFrame({"value_date": days, "value": np.linspace(1.0, 2.0, len(days))})
    rel = pd.concat([fred_frame("GVZCLS", gvz, pd.Timestamp("2024-07-01")),
                     fred_frame("DFII10", ry, pd.Timestamp("2024-07-01"))], ignore_index=True)
    rel.to_parquet(tmp_path / "macro_fred.parquet", index=False)
    store = Store(tmp_path / "store")
    load_macro_into_store(store, read_macro_files(tmp_path))
    macro = store_macro(store)
    assert set(macro["series"]) == {"gold_vix", "real_yield_10y"} and len(macro) == len(rel)

    bars = pd.DataFrame({"ts_utc": pd.date_range("2024-02-01", "2024-06-30", freq="15min", tz="UTC")})
    X = build_features(bars, ["macro_drivers"], {"macro": macro})
    g = rel[rel["series"] == "gold_vix"].sort_values("available_utc")
    avail = pd.DatetimeIndex(g["available_utc"])
    k = avail.searchsorted(pd.DatetimeIndex(bars["ts_utc"]), side="right") - 1    # last value public at each bar
    assert (k >= 0).all()
    assert np.array_equal(X["macro_gvz"].to_numpy(), g["value"].to_numpy()[k])
    # a 20-observation change uses only observations public at the bar
    assert np.allclose(X["macro_gvz_chg20"].to_numpy(), np.where(k >= 20, 20.0, np.nan), equal_nan=True)
    # the bar before a value's availability still shows the previous value; the bar at it shows the new one
    for v_avail, v_prev, v_new in zip(avail[30:35], g["value"].to_numpy()[29:34], g["value"].to_numpy()[30:35]):
        assert (X.loc[bars["ts_utc"] == v_avail - pd.Timedelta(minutes=15), "macro_gvz"] == v_prev).all()
        assert (X.loc[bars["ts_utc"] == v_avail, "macro_gvz"] == v_new).all()

    def at(ts: str) -> float:
        return float(X.loc[bars["ts_utc"] == pd.Timestamp(ts, tz="UTC"), "macro_gvz"].iloc[0])

    def value_of(day: str) -> float:
        return float(g.loc[g["value_date"] == pd.Timestamp(day), "value"].iloc[0])

    # Thursday's value is public on Friday 23:00 UTC (next business day), so Friday 20:00 still sees Wednesday's;
    # Friday's value waits for Monday 23:00 UTC
    assert at("2024-03-08 20:00") == value_of("2024-03-06")
    assert at("2024-03-11 22:45") == value_of("2024-03-07")
    assert at("2024-03-11 23:00") == value_of("2024-03-08")


def test_quality_checks_flag_spike_and_bid_gt_ask(bars1m):
    b = bars1m.copy()
    i = len(b) // 2
    b.loc[i, "bid_close"] = b.loc[i, "bid_close"] * 1.20
    b.loc[i + 5, "ask_close"] = b.loc[i + 5, "bid_close"] - 1.0
    flagged, events = check_bars(b)
    kinds = {e.check for e in events}
    assert "spike" in kinds and "bid_gt_ask" in kinds
    assert str(flagged.at[i + 5, "dq_flag"]).startswith("error")


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_epoch_ns_is_unit_independent(unit):
    from goldbot.data.timeutil import epoch_ns
    idx = pd.DatetimeIndex(["2025-03-05 13:00", "2025-03-05 13:01"], tz="UTC").as_unit(unit)
    assert epoch_ns(idx).tolist() == [1741179600_000_000_000, 1741179660_000_000_000]
    assert epoch_ns(pd.Series(idx)).tolist() == epoch_ns(idx).tolist()


@pytest.mark.parametrize("unit", ["ms", "ns"])
def test_gap_check_fires_whatever_the_timestamp_unit(bars1m, unit):
    # pandas 3 keeps ms resolution from Dukascopy timestamps; the gap check must still see a 30-minute hole
    b = bars1m.copy()
    b["ts_utc"] = pd.DatetimeIndex(b["ts_utc"]).as_unit(unit)
    open_ = DEFAULT_SESSIONS.is_open(pd.DatetimeIndex(b["ts_utc"]))
    i = int(np.flatnonzero(open_)[500])
    hole = b.iloc[i:i + 30].index
    _, dq = check_bars(b.drop(index=hole))
    assert any(e.check == "gap_in_session" for e in dq), [e.check for e in dq]
