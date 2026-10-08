"""The fast paths in the engine's bar-close loop return exactly what the code they replaced returned.

Each optimised function is compared with a reference copy of the previous implementation (bit for bit:
assert_*_equal with check_exact, NaNs in the same places, same dtypes)."""
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import tf_seconds
from goldbot.data.resample import IncrementalResampler, mid, resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import feature_day, floor_tf
from goldbot.features.columns import Columns
from goldbot.features.session import _dst_flag, f_session
from goldbot.features.structure import confirmed_levels, f_levels
from goldbot.features.technical import _nan_percentiles_sorted, _vol_tercile, atr, wma


# ------------------------------------------------------------------------------------------- references (old code)
def ref_atr(df, n=14):
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def ref_wma(s, n):
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


def ref_vol_tercile(rv):
    return rv.rolling(400, min_periods=100).apply(
        lambda x: 0 if x[-1] <= np.nanpercentile(x[:-1], 33) else (2 if x[-1] > np.nanpercentile(x[:-1], 67) else 1), raw=True)


def ref_levels(df, lag=5):
    levels = confirmed_levels(df, lag)
    a = ref_atr(df, 14).bfill().to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    up, dn, up_t, dn_t = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n), np.zeros(n)
    up_age, dn_age, n_near = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n)
    for i, lvls in enumerate(levels):
        above = [lv for lv in lvls if lv[0] > close[i]]
        below = [lv for lv in lvls if lv[0] <= close[i]]
        if above:
            lv = min(above, key=lambda x: x[0])
            up[i], up_t[i], up_age[i] = (lv[0] - close[i]) / a[i], lv[1], lv[2]
        if below:
            lv = max(below, key=lambda x: x[0])
            dn[i], dn_t[i], dn_age[i] = (close[i] - lv[0]) / a[i], lv[1], lv[2]
        n_near[i] = sum(1 for lv in lvls if abs(lv[0] - close[i]) <= 1.0 * a[i])
    return pd.DataFrame({"dist_res_atr": up, "res_touches": up_t, "res_age": up_age, "dist_sup_atr": dn,
                         "sup_touches": dn_t, "sup_age": dn_age, "levels_within_1atr": n_near}, index=df.index)


def ref_confirmed_levels(df, lag=5, max_levels=60):
    from goldbot.features.structure import swing_points
    is_high, is_low = swing_points(df, lag)
    a = ref_atr(df, 14).bfill().to_numpy()
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    known: list[list] = []
    per_bar: list = []
    for i in range(len(df)):
        j = i - lag
        if j >= 0:
            for flag, px in ((is_high.iat[j], highs[j]), (is_low.iat[j], lows[j])):
                if not flag:
                    continue
                merged = False
                for lv in known:
                    if abs(lv[0] - px) <= 0.3 * a[i]:
                        lv[0] = (lv[0] * lv[1] + px) / (lv[1] + 1)
                        lv[1] += 1
                        merged = True
                        break
                if not merged:
                    known.append([px, 1, i])
                if len(known) > max_levels:
                    known.sort(key=lambda x: (-x[1], -x[2]))
                    del known[max_levels:]
        per_bar.append([(float(lv[0]), int(lv[1]), i - int(lv[2])) for lv in known])
    return per_bar


def ref_agg_key(key):
    return np.asarray(key)        # the old _agg: tz-aware keys boxed to Timestamp objects


# ------------------------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def bars_1m() -> pd.DataFrame:
    # spans the US (Mar 9) and EU (Mar 30) DST switches
    return ticks_to_1m(synthetic_ticks("2025-03-03", "2025-04-04", ticks_per_minute=1, seed=21))


def _frames(bars_1m):
    for tf in ("15m", "1h", "4h", "1d"):
        m = mid(resample_bars(bars_1m, tf)).reset_index(drop=True)
        yield tf, m
        yield tf + "-tail", mid(resample_bars(bars_1m, tf).tail(800).reset_index(drop=True))
    yield "1m", mid(bars_1m.tail(5000).reset_index(drop=True))


def _noisy(rng, n):
    v = np.exp(rng.normal(size=n)).cumsum() * rng.choice([1e-6, 1.0, 1e3])
    return v


# --------------------------------------------------------------------------------------------------- tests
def test_atr_matches_frame_max(bars_1m):
    for _, m in _frames(bars_1m):
        pd.testing.assert_series_equal(atr(m, 14), ref_atr(m, 14), check_exact=True, check_names=False)
        pd.testing.assert_series_equal(atr(m, 100), ref_atr(m, 100), check_exact=True, check_names=False)
    m = mid(resample_bars(bars_1m, "1h")).reset_index(drop=True)
    m.loc[[3, 50, 51], "high"] = np.nan                     # NaN rows: the row max skips NaN like np.fmax
    m.loc[[50, 70], "close"] = np.nan
    pd.testing.assert_series_equal(atr(m, 14), ref_atr(m, 14), check_exact=True, check_names=False)
    pd.testing.assert_series_equal(atr(m.iloc[:0], 14), ref_atr(m.iloc[:0], 14), check_names=False, check_dtype=False)


def test_wma_and_vol_tercile_bit_identical(bars_1m):
    rng = np.random.default_rng(3)
    for trial in range(25):
        n = int(rng.integers(30, 1500))
        v = _noisy(rng, n)
        if trial % 3 == 0:
            v[: rng.integers(0, 60)] = np.nan
        if trial % 4 == 1:
            v[rng.random(n) < 0.05] = np.nan
        if trial % 5 == 2:
            v = np.round(v, 1)                                # ties
        s = pd.Series(v, index=pd.RangeIndex(7, 7 + n))
        for k in (3, 7, 27, 55):
            pd.testing.assert_series_equal(wma(s, k), ref_wma(s, k), check_exact=True)
        assert np.array_equal(_vol_tercile(v, 400, 100), ref_vol_tercile(s).to_numpy(), equal_nan=True)
    for _, m in _frames(bars_1m):
        rv = np.log(m["close"]).diff().rolling(20, min_periods=20).std()
        assert np.array_equal(_vol_tercile(rv.to_numpy(), 400, 100), ref_vol_tercile(rv).to_numpy(), equal_nan=True)


def test_row_percentiles_equal_numpy_nanpercentile():
    rng = np.random.default_rng(5)
    for trial in range(400):
        k = int(rng.integers(1, 450))
        row = rng.normal(size=k) * 10.0 ** rng.integers(-5, 5)
        if trial % 2:
            row = np.round(row, 2)
        padded = np.concatenate((row, np.full(rng.integers(0, 40), np.nan)))
        srt = np.sort(padded)[None, :]
        for q in (0, 1, 33, 50, 67, 99, 100):
            got, exp = _nan_percentiles_sorted(srt, np.array([k]), q)[0], np.nanpercentile(padded, q)
            assert got == exp, (k, q, got, exp)


def test_levels_single_pass_matches_snapshot_scan(bars_1m):
    for _, m in _frames(bars_1m):
        pd.testing.assert_frame_equal(f_levels(m, {}), ref_levels(m), check_exact=True)
        if len(m) < 3000:
            assert confirmed_levels(m) == ref_confirmed_levels(m)


def test_dst_flags_per_hour_match_per_bar(bars_1m):
    idx = pd.DatetimeIndex(pd.date_range("2024-03-01", "2025-11-10", freq="7min", tz="UTC"))
    for tz in ("America/New_York", "Europe/London"):
        ref = idx.tz_convert(tz).map(lambda t: t.dst().total_seconds() > 0).astype(int)
        np.testing.assert_array_equal(_dst_flag(idx, tz), np.asarray(ref))
    m = mid(bars_1m.tail(3000).reset_index(drop=True))
    out = f_session(m, {})
    ref_us = pd.DatetimeIndex(m["ts_utc"]).tz_convert("America/New_York").map(lambda t: t.dst().total_seconds() > 0).astype(int)
    assert out["us_dst"].dtype == np.asarray(ref_us).dtype and (out["us_dst"].to_numpy() == np.asarray(ref_us)).all()


def test_columns_builds_the_frame_column_inserts_built():
    idx = pd.RangeIndex(3, 9)
    s = pd.Series(np.arange(6, dtype=float), index=idx)
    values: dict[str, Any] = {"series": s, "array": np.arange(6) * 2, "scalar": 0, "codes": pd.Categorical(list("abcabc")).codes,
              "dow": pd.DatetimeIndex(pd.date_range("2025-01-01", periods=6, tz="UTC")).dayofweek, "bool_int": (s > 2).astype(int)}
    ref = pd.DataFrame(index=idx)
    out = Columns(idx)
    for k, v in values.items():
        ref[k] = v
        out[k] = v
    ref["derived"] = ref["array"] + ref["series"]
    out["derived"] = out["array"] + out["series"]
    pd.testing.assert_frame_equal(out.frame(), ref, check_exact=True)


def test_resample_tz_aware_keys_unchanged(bars_1m):
    from goldbot.data import resample as rs
    for unit in ("ns", "us"):
        b = bars_1m.copy()
        b["ts_utc"] = b["ts_utc"].dt.as_unit(unit)
        key = floor_tf(pd.DatetimeIndex(b["ts_utc"]), 3600)
        new = rs._agg(b, key)
        old = b.copy()
        old["_k"] = ref_agg_key(key)
        g = old.groupby("_k", sort=True)
        assert list(new["ts_utc"]) == list(g["bid_open"].first().index)
        assert new["ts_utc"].dtype == pd.DatetimeIndex(g["bid_open"].first().index).dtype


@pytest.mark.parametrize("tf", ["15m", "1h", "4h", "1d", "1w"])
def test_incremental_resampler_equals_full_resample(bars_1m, tf):
    """Grow, trim (also mid-group), no-op, replace a row, unit change: always exactly resample_bars."""
    rng = np.random.default_rng(hash(tf) % 2**32)
    rs = IncrementalResampler(tf)
    lo, hi = 0, 3000
    steps = 0
    while hi < len(bars_1m):
        frame = bars_1m.iloc[lo:hi]
        pd.testing.assert_frame_equal(rs(frame), resample_bars(frame, tf), check_exact=True)
        steps += 1
        hi += int(rng.choice([0, 1, 15, 60, 241]))
        if rng.random() < 0.5:
            lo = min(lo + int(rng.choice([1, 15, 37, 600])), hi - 1500)
        if steps > 120:
            break
    if tf != "1w":                                          # (a 2-day window mostly sits in one week: full builds)
        assert rs.full_builds < steps / 4                   # the cache was actually used
    frame = bars_1m.iloc[lo:hi].copy()
    frame.loc[frame.index[-5], "bid_high"] += 1.0           # a replaced minute: must not reuse its group
    pd.testing.assert_frame_equal(rs(frame), resample_bars(frame, tf), check_exact=True)
    frame["ts_utc"] = frame["ts_utc"].dt.as_unit("us")
    pd.testing.assert_frame_equal(rs(frame), resample_bars(frame, tf), check_exact=True)
    assert rs(frame.iloc[0:0]).empty


def test_group_keys_are_the_resample_groups(bars_1m):
    idx = pd.DatetimeIndex(bars_1m["ts_utc"])
    from goldbot.data.resample import _group_keys
    assert len(np.unique(_group_keys(idx, "1d", "America/New_York", "13:30"))) == len(resample_bars(bars_1m, "1d"))
    assert len(np.unique(_group_keys(idx, "4h", "America/New_York", "13:30"))) == len(resample_bars(bars_1m, "4h"))
    assert len(feature_day(idx)) == len(idx) and tf_seconds("4h") == 14400


def test_engine_frames_with_incremental_resampling_equal_fresh_builds(tmp_path, bars_1m):
    """Every bar close of a rolling (trimmed) 1m buffer: the engine's frames equal ones built from scratch."""
    from goldbot.engine import Engine, EngineConfig
    from goldbot.execution.paper import PaperBroker
    from goldbot.specialists import SPECIALISTS

    spec = SPECIALISTS["session_open"]()

    def engine() -> Engine:
        return Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path)), PaperBroker(equity=10_000),
                      [spec], {})

    cached = engine()
    b1 = bars_1m[bars_1m["ts_utc"] < pd.Timestamp("2025-03-13", tz="UTC")]
    for i, close in enumerate(pd.date_range("2025-03-12 11:00", "2025-03-12 15:00", freq="15min", tz="UTC")):
        complete = b1[b1["visible_at"] <= close].iloc[i * 7:]          # the buffer also loses its oldest bars
        cached._ctx_cache.clear()      # (context features are cached per context bar by design; rebuild them here)
        for tf in ("15m", "1h"):
            a, b = cached._frame(complete, tf, close), engine()._frame(complete, tf, close)
            assert a is not None and b is not None
            for x, y in ((a.dec, b.dec), (a.m, b.m), (a.X, b.X)):
                pd.testing.assert_frame_equal(x, y, check_exact=True)
            pd.testing.assert_series_equal(a.atr, b.atr, check_exact=True)
    assert cached._resamplers["15m"].full_builds == 1


def test_utc_index_matches_to_datetime():
    from goldbot.data.timeutil import utc_index
    for s in (pd.Series(pd.date_range("2025-01-01", periods=100, freq="1min", tz="UTC")),
              pd.Series(pd.date_range("2025-01-01", periods=100, freq="1min", tz="UTC")).dt.as_unit("us"),
              pd.Series(pd.date_range("2025-01-01", periods=100, freq="1min", tz="Europe/London")),
              pd.Series(pd.date_range("2025-01-01", periods=100, freq="1min")),
              pd.Series(["2025-01-01 00:00", "2025-01-01 00:01"])):
        pd.testing.assert_index_equal(utc_index(s), pd.DatetimeIndex(pd.to_datetime(s, utc=True)), exact=True)


def test_store_reads_only_partitions_in_range_with_the_same_rows(tmp_path):
    import duckdb

    from goldbot.data.store import Store, _utc
    st = Store(tmp_path)
    rng = np.random.default_rng(1)
    frames = []
    for m in pd.date_range("2023-11-01", "2024-04-01", freq="MS", tz="UTC"):
        ts = pd.date_range(m, periods=600, freq="7min")
        frames.append(pd.DataFrame({"ts_utc": ts, "bid": rng.normal(size=len(ts)), "n": rng.integers(0, 9, len(ts))}))
    b = pd.concat(frames)
    st.append("bars_1m", b, source="a", dedupe=False)
    st.append("bars_1m", b.iloc[::5].assign(ts_utc=lambda d: d["ts_utc"] + pd.Timedelta(seconds=30)), source="b", dedupe=False)

    def ref(**kw):
        where: list[str] = ["symbol = ?"]
        params: list[Any] = ["XAUUSD"]
        if kw.get("source"):
            where.append("source = ?")
            params.append(kw["source"])
        if kw.get("start") is not None:
            where.append("ts_utc >= ?")
            params.append(_utc(kw["start"]).to_pydatetime())
        if kw.get("end") is not None:
            where.append("ts_utc < ?")
            params.append(_utc(kw["end"]).to_pydatetime())
        con = duckdb.connect()
        df = con.execute(f"SELECT * FROM read_parquet('{tmp_path}/bars_1m/**/*.parquet', hive_partitioning=true) "
                         f"WHERE {' AND '.join(where)} ORDER BY ts_utc", params).df()
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
        return df

    for kw in ({"source": "a", "start": "2024-01-15", "end": "2024-03-01"}, {"start": "2023-12-31 23:00", "end": "2024-01-01 01:00"},
               {"start": "2024-04-01"}, {"end": "2023-11-02"}, {"source": "b", "start": "2024-02-29"}, {"start": "2030-01-01"},
               {"source": "zzz", "start": "2024-01-01"}):
        got, exp = st.read("bars_1m", **kw), ref(**kw)            # timestamps are unique: ORDER BY fixes the row order
        pd.testing.assert_frame_equal(got, exp, check_exact=True)


def _ref_ticks_to_1m(ticks):
    """ticks_to_1m before the numpy aggregation (ten groupby aggregates)."""
    from goldbot.data.calendar import DEFAULT_SESSIONS
    from goldbot.data.resample import BAR_COLUMNS
    t = ticks.sort_values("ts_utc").copy()
    t["ts_utc"] = pd.to_datetime(t["ts_utc"], utc=True)
    t = t[(t["bid"] > 0) & (t["ask"] >= t["bid"])]
    t["spread"] = t["ask"] - t["bid"]
    t["minute"] = floor_tf(pd.DatetimeIndex(t["ts_utc"]), 60)
    g = t.groupby("minute", sort=True)
    bars = pd.DataFrame({
        "bid_open": g["bid"].first(), "bid_high": g["bid"].max(), "bid_low": g["bid"].min(), "bid_close": g["bid"].last(),
        "ask_open": g["ask"].first(), "ask_high": g["ask"].max(), "ask_low": g["ask"].min(), "ask_close": g["ask"].last(),
        "tick_count": g["bid"].size(), "spread_mean": g["spread"].mean(), "spread_max": g["spread"].max(),
    })
    bars.index.name = "ts_utc"
    bars = bars.reset_index()
    bars = bars[DEFAULT_SESSIONS.is_open(pd.DatetimeIndex(bars["ts_utc"]))]
    bars.insert(1, "visible_at", bars["ts_utc"] + pd.Timedelta(seconds=60))
    return bars.reset_index(drop=True)[BAR_COLUMNS]


def test_ticks_to_1m_numpy_aggregates_match_groupby():
    rng = np.random.default_rng(9)
    for k in range(120):
        n = int(rng.integers(1, 300))
        start = pd.Timestamp("2025-03-07 21:50", tz="UTC") + pd.Timedelta(minutes=int(rng.integers(0, 4000)))
        step = 1 if k % 3 else 5000                                   # coarse stamps: many equal timestamps
        ts = start + pd.to_timedelta(np.sort(rng.integers(0, 600_000, n)) // step * step, unit="ms")
        bid = 2400 + rng.normal(size=n).cumsum() * 0.1
        ask = bid + rng.random(n) * 0.5 - (0.6 if k % 7 == 0 else 0)  # some crossed quotes (dropped)
        if k % 11 == 0:
            bid[rng.random(n) < 0.1] = -1
        df = pd.DataFrame({"ts_utc": ts, "bid": bid, "ask": ask})
        if k % 5 == 0:
            df = df.sample(frac=1, random_state=k)                    # unsorted input
        pd.testing.assert_frame_equal(ticks_to_1m(df), _ref_ticks_to_1m(df), check_exact=True)
    big = synthetic_ticks("2025-03-03", "2025-03-06", ticks_per_minute=4, seed=1)
    pd.testing.assert_frame_equal(ticks_to_1m(big), _ref_ticks_to_1m(big), check_exact=True)


def test_bar_errors_are_check_bars_errors(bars_1m):
    from goldbot.data.quality import bar_errors, check_bars
    rng = np.random.default_rng(4)
    for k in range(150):
        i = int(rng.integers(0, len(bars_1m) - 20))
        b = bars_1m.iloc[i:i + int(rng.integers(1, 12))].copy()
        if k % 3 == 0 and len(b) > 1:
            b = pd.concat([b, b.iloc[[0]]])                          # duplicate stamp, out of order
        if k % 4 == 1:
            b.loc[b.index[0], "ask_close"] = b["bid_close"].iloc[0] - 1
        if k % 5 == 2:
            b.loc[b.index[-1], "spread_mean"] = -0.1
        if k % 6 == 3:
            b = b.iloc[::-1]
        if k % 7 == 4:
            b["ts_utc"] = b["ts_utc"].dt.as_unit("us")
        _, ev = check_bars(b)
        assert bar_errors(b) == [e for e in ev if e.severity == "error"]
    assert bar_errors(bars_1m.iloc[0:0]) == []


def test_rebuild_bars_fast_paths_equal_dedup_append(tmp_path, bars_1m):
    """Bars built tick by tick (skipping minutes still open, appending without re-de-duplicating) equal the
    de-duplicating rebuild of the previous code, on a trimmed buffer and after an external reassignment."""
    from goldbot.engine import Engine, EngineConfig
    from goldbot.execution.broker import Tick
    from goldbot.execution.paper import PaperBroker
    ticks = synthetic_ticks("2025-03-04", "2025-03-04 06:00", ticks_per_minute=3, seed=2)
    eng = Engine(EngineConfig(account_id="x", broker_name="icm", state_dir=str(tmp_path), max_bars_in_memory=200),
                 PaperBroker(equity=10_000), [], {})
    ref = pd.DataFrame()
    pending: list = []
    for j, (ts, bid, ask) in enumerate(zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float))):
        t = Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask))
        eng.ticks.append(t)
        pending.append(t)
        eng._rebuild_bars()
        # reference: the previous rebuild on every call
        new = _ref_ticks_to_1m(pd.DataFrame({"ts_utc": [x.ts_utc for x in pending], "bid": [x.bid for x in pending],
                                             "ask": [x.ask for x in pending]}))
        if not new.empty:
            last = new["ts_utc"].iloc[-1]
            done = new[new["ts_utc"] < last]
            if not done.empty:
                ref = pd.concat([ref, done]).drop_duplicates("ts_utc", keep="last").tail(200).reset_index(drop=True) \
                    if not ref.empty else done.reset_index(drop=True)
            pending = [x for x in pending if x.ts_utc >= last]
        if j == 400:                                   # an external reassignment (warm start, tests) is re-deduplicated
            eng.bars_1m = pd.concat([eng.bars_1m, eng.bars_1m.tail(3)]).reset_index(drop=True)
            ref = pd.concat([ref, ref.tail(3)]).reset_index(drop=True)
        assert [x.ts_utc for x in eng.ticks] == [x.ts_utc for x in pending]
    pd.testing.assert_frame_equal(eng.bars_1m, ref, check_exact=True)
    assert len(ref) == 200


def test_single_thread_lightgbm_fits_the_same_model():
    """FIT_THREADS only changes the thread count: the fitted model predicts exactly what an all-cores fit does."""
    pytest.importorskip("lightgbm")
    from goldbot.research.model import DEFAULT_PARAMS, MetaLabelModel
    rng = np.random.default_rng(2)
    n, cols = 800, [f"f{i}" for i in range(12)]
    X = pd.DataFrame(rng.normal(size=(n, len(cols))), columns=cols)
    X[X > 2.2] = np.nan
    y = pd.Series((X["f0"].fillna(0) + rng.normal(size=n) > 0).astype(int))
    w = pd.Series(rng.random(n))
    short = {**DEFAULT_PARAMS, "n_estimators": 40}       # (all-core fits crawl on a shared CPU; keep the test short)
    one = MetaLabelModel(feature_names=cols, params=short).fit(X, y, w)
    every = MetaLabelModel(feature_names=cols, params={**short, "n_jobs": -1}).fit(X, y, w)
    assert one.model.get_params()["n_jobs"] == 1 and "n_jobs" not in one.params
    np.testing.assert_array_equal(one.predict_raw(X), every.predict_raw(X))
    assert (one.importance() == every.importance()).all()
