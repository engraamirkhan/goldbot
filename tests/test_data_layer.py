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


def test_quality_checks_flag_spike_and_bid_gt_ask(bars1m):
    b = bars1m.copy()
    i = len(b) // 2
    b.loc[i, "bid_close"] = b.loc[i, "bid_close"] * 1.20
    b.loc[i + 5, "ask_close"] = b.loc[i + 5, "bid_close"] - 1.0
    flagged, events = check_bars(b)
    kinds = {e.check for e in events}
    assert "spike" in kinds and "bid_gt_ask" in kinds
    assert flagged.loc[i + 5, "dq_flag"].startswith("error")
