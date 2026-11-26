"""fs-v1, the vectorised point-in-time feature store (lifecycle N9; goldbot/research/feature_store.py) and the closed
higher-timeframe bar store (goldbot/data/htf_bars.py).

Acceptance (N9): on sampled decisions fs-v1 equals the engine's frame row (`engine.frames.frame` through
`Engine._context`, on the same minutes) and `feature_snapshots.decision_features` with max abs diff <= 1e-9 and an
identical NaN mask; a truncation (future perturbation) test proves no look-ahead; cache keys never depend on the
decisions; tiers are applied at load and their file hash recorded. Synthetic minutes only (no market data in tests).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from goldbot.data import holdout_guard
from goldbot.data import htf_bars as H
from goldbot.data.resample import resample_bars
from goldbot.research import feature_snapshots as fsnap
from goldbot.research import feature_store as F
from goldbot.research.self_retrain import FEATURES as SR_FEATURES
from goldbot.research.self_retrain import INPUTS as SR_INPUTS

START = pd.Timestamp("2023-06-01", tz="UTC")
DAYS = 190           # > 100 daily bars + warm-up: the d1 inputs that were always NaN get values
TOL = 1e-9


def _open(ts: pd.DatetimeIndex) -> np.ndarray:
    """Gold's week: shut from Friday 21:00 to Sunday 22:00 UTC and for the 21:00-22:00 daily break."""
    wd, h = ts.day_of_week, ts.hour
    weekend = (wd == 5) | ((wd == 4) & (h >= 21)) | ((wd == 6) & (h < 22))
    return np.asarray(~weekend & (h != 21))


def make_minutes(days: int = DAYS, seed: int = 11, start: pd.Timestamp = START) -> pd.DataFrame:
    """1m bid/ask bars of a random walk with volatility clusters, flat stretches and session closures."""
    ts = start + pd.to_timedelta(np.arange(days * 1440), unit="min")
    ts = ts[_open(ts)]
    n = len(ts)
    rng = np.random.default_rng(seed)
    vol = 0.15 * np.exp(np.cumsum(rng.normal(0, 0.01, n)).clip(-1.5, 1.5))
    step = rng.normal(0, 1, n) * vol
    step[rng.random(n) < 0.03] = 0.0                       # flat minutes: the rolling-std edge case
    close = 1900.0 + np.cumsum(step)
    opn = np.r_[close[0], close[:-1]]
    hi = np.maximum(opn, close) + rng.uniform(0, 0.15, n)
    lo = np.minimum(opn, close) - rng.uniform(0, 0.15, n)
    out = pd.DataFrame({"ts_utc": ts})
    for side, off in (("bid", -0.1), ("ask", 0.1)):
        out[f"{side}_open"], out[f"{side}_high"] = opn + off, hi + off
        out[f"{side}_low"], out[f"{side}_close"] = lo + off, close + off
    out["tick_count"] = rng.integers(3, 60, n).astype(float)
    out["spread_mean"] = 0.2 + rng.uniform(0, 0.05, n)
    out["spread_max"] = out["spread_mean"] + 0.1
    out["visible_at"] = out["ts_utc"] + pd.Timedelta(minutes=1)
    out["source"] = "synthetic"
    return out


@pytest.fixture(scope="module")
def minutes() -> pd.DataFrame:
    return make_minutes()


def _engine_row(minutes: pd.DataFrame, tf: str, at: pd.Timestamp) -> dict[str, float] | None:
    """engine.frames.frame on every minute visible at `at` (a memory long enough for the closed daily bars, which
    fs-v1 serves from the HTF store), through Engine._context; no Engine is started."""
    from goldbot.engine import frames
    from goldbot.engine.runner import Engine
    primary = object()
    eng: Any = SimpleNamespace(book=primary, primary=primary,
                               cfg=SimpleNamespace(feature_window=fsnap.FEATURE_WINDOW),
                               _ctx_cache={}, _resample=resample_bars)
    eng._context = lambda complete, ctf, ts: Engine._context(eng, complete, ctf, ts)
    complete = minutes[minutes["visible_at"] <= at].reset_index(drop=True)
    fr = frames.frame(eng, complete, tf, at)
    if fr is None:
        return None
    row = fr.X.iloc[-1]
    return {c: float(row[c]) if c in row.index else np.nan for c in SR_FEATURES}


def _snapshot_row(minutes: pd.DataFrame, tf: str, at: pd.Timestamp, memory: int | None) -> dict[str, float] | None:
    with mock.patch.object(fsnap, "ENGINE_MINUTES", memory if memory is not None else 10**12):
        snap = fsnap.decision_features(minutes, tf, at)
    if snap is None:
        return None
    vals = snap.features
    return {c: np.nan if (v := vals.get(c)) is None else float(v) for c in SR_FEATURES}


def _diff(a: dict[str, float], b: dict[str, float]) -> tuple[float, list[str]]:
    """(max abs diff over columns finite in both, columns whose NaN mask differs)."""
    worst, mask = 0.0, []
    for c in SR_FEATURES:
        x, y = a[c], b[c]
        if np.isnan(x) != np.isnan(y):
            mask.append(c)
        elif not np.isnan(x):
            worst = max(worst, 0.0 if x == y else abs(x - y))
    return worst, mask


def _row_at(rows: pd.DataFrame, at: pd.Timestamp) -> dict[str, float]:
    got = F.features_for(rows, pd.DatetimeIndex([at]))
    return {c: float(got[c].iloc[0]) for c in SR_FEATURES}


# ---------------------------------------------------------------------------------------------- the spec
def test_the_spec_is_the_sr_v2_set_39_market_columns_plus_side_is_40_inputs():
    spec = F.SPEC
    assert spec.spec_id == "fs-v1"
    assert tuple(spec.columns) == SR_FEATURES
    assert len(spec.columns) + 1 == len(SR_INPUTS) == 40
    assert set(spec.context_tfs) == {"1d"}                  # the sr-v2 context columns are all d1_
    assert set(F.WINDOW_EXACT) <= set(spec.columns)


def test_the_spec_hash_and_the_code_hash_are_stable_and_change_with_the_spec():
    assert F.spec_hash(F.SPEC) == F.spec_hash(F.SPEC)
    other = F.SPEC.model_copy(update={"feature_window": 500})
    assert F.spec_hash(other) != F.spec_hash(F.SPEC)
    assert len(F.code_hash()) == 64


# ---------------------------------------------------------------------------------------------- closed HTF bars
def test_the_htf_store_holds_only_closed_bars_equal_to_the_resampler_with_availability_and_source(minutes):
    cut = minutes[minutes["ts_utc"] < START + pd.Timedelta(days=60, hours=10)]     # ends mid-day: last day open
    for tf in ("1d", "1w"):
        bars = H.closed_bars(cut, tf)
        ref = resample_bars(cut, tf)
        last_seen = cut["visible_at"].max()
        ref = ref[pd.to_datetime(ref["visible_at"], utc=True) <= last_seen].reset_index(drop=True)
        assert len(bars) == len(ref) and len(bars) > 0
        assert (pd.to_datetime(bars["visible_at"], utc=True) <= last_seen).all()
        pd.testing.assert_frame_equal(bars[ref.columns].reset_index(drop=True), ref, check_dtype=False)
        assert (bars["available_utc"] >= bars["visible_at"]).all()
        assert set(bars["source"]) == {"synthetic"}


def test_the_htf_store_writes_atomically_and_rebuilding_from_the_same_minutes_is_idempotent(minutes, tmp_path):
    a = H.write(tmp_path, "XAUUSD", "1d", minutes)
    b = H.write(tmp_path, "XAUUSD", "1d", minutes)
    assert a == b and a.exists()
    m = H.read_manifest(a)
    assert m["minutes_sha256"] == H.minutes_sha(minutes) and m["rows"] == len(H.read(a))
    assert not list(tmp_path.rglob("*.tmp*"))


def test_d1_inputs_are_finite_from_the_htf_store_where_the_engines_90k_minute_memory_leaves_them_nan(minutes):
    rows = F.compute(minutes, "1h")
    at = pd.Timestamp(rows["decision_utc"].iloc[-1])
    got = _row_at(rows, at)
    assert all(np.isfinite(got[c]) for c in ("d1_atr_ratio_14_100", "d1_rv_ratio", "d1_slope_ema20"))
    today = _snapshot_row(minutes, "1h", at, fsnap.ENGINE_MINUTES)        # what the nightly snapshot stores today
    assert today is not None and np.isnan(today["d1_atr_ratio_14_100"]) and np.isnan(today["d1_rv_ratio"])


# ---------------------------------------------------------------------------------------------- parity oracle
def _sample(rows: pd.DataFrame, k: int, seed: int = 3) -> list[pd.Timestamp]:
    dec = pd.DatetimeIndex(rows["decision_utc"])
    pick = np.sort(np.random.default_rng(seed).choice(len(dec), size=min(k, len(dec)), replace=False))
    # plus two off-close decisions (inside a bar): the engine reads the last closed bar, as fs-v1's as-of join
    return [*dec[pick], dec[len(dec) // 2] + pd.Timedelta(minutes=7), dec[-1] + pd.Timedelta(minutes=3)]


@pytest.mark.parametrize("tf", ["15m", "1h", "4h"])
def test_fs_v1_equals_the_engine_frame_and_the_snapshot_on_sampled_decisions(minutes, tf):
    rows = F.compute(minutes, tf)
    assert len(rows) > 0
    worst, bad_mask, n = 0.0, [], 0
    for at in _sample(rows, 10):
        mine = _row_at(rows, at)
        eng = _engine_row(minutes, tf, at)
        snap = _snapshot_row(minutes, tf, at, None)
        assert eng is not None and snap is not None
        for other in (eng, snap):
            d, mask = _diff(mine, other)
            worst = max(worst, d)
            bad_mask += mask
        n += 1
    assert n >= 10
    assert bad_mask == [] and worst <= TOL, (worst, sorted(set(bad_mask)))


def test_the_first_rows_follow_the_engines_minimum_of_120_decision_bars(minutes):
    rows = F.compute(minutes, "1h")
    first = pd.Timestamp(rows["decision_utc"].iloc[0])
    assert _engine_row(minutes, "1h", first - pd.Timedelta(hours=1)) is None      # one bar fewer: no frame
    eng = _engine_row(minutes, "1h", first)
    assert eng is not None
    d, mask = _diff(_row_at(rows, first), eng)
    assert mask == [] and d <= TOL


def test_window_exact_columns_match_a_per_window_build_bit_for_bit(minutes):
    """atr_ratio_14_100 (Wilder 100: about 1e-3 apart between a full-history pass and an 800-bar window) and bb_z_20
    (pandas' rolling std carries the window's start in its rounding) are rebuilt per 800-bar window in a batch."""
    from goldbot.data.resample import mid
    from goldbot.features import build_features
    m = mid(resample_bars(minutes, "15m"))
    idx = np.array([900, 1500, 2600, len(m) - 1])
    got = F.window_exact(m, idx, F.SPEC.feature_window)
    for j, i in enumerate(idx):
        w = m.iloc[i - F.SPEC.feature_window + 1:i + 1].reset_index(drop=True)
        ref = build_features(w, ["atr", "mean_reversion"]).iloc[-1]
        for c in F.WINDOW_EXACT:
            assert got[c][j] == ref[c] or (np.isnan(got[c][j]) and np.isnan(ref[c])), c


# ---------------------------------------------------------------------------------------------- point in time
@pytest.mark.parametrize(("tf", "phase"), [("15m", 0), ("4h", 0), ("1d", 0), ("15m", 5)])
def test_truncation_rows_known_by_t_never_change_when_everything_after_t_is_perturbed(minutes, tf, phase):
    """No look-ahead: every row available by T is identical (values and NaN mask) whether the minutes after T are
    absent or wildly different (quant L2: intraday, 4h, daily and an offset phase)."""
    T = START + pd.Timedelta(days=183, hours=13, minutes=7)
    before = minutes[minutes["visible_at"] <= T]
    after = minutes.copy()
    late = (after["visible_at"] > T).to_numpy()
    for c in [c for c in after.columns if c.startswith(("bid_", "ask_"))]:
        after.loc[late, c] = after.loc[late, c] * 1.7 + 50.0
    after.loc[late, "tick_count"] = 999.0
    a, b = F.compute(before, tf, phase=phase), F.compute(after, tf, phase=phase)
    a = a[a["available_utc"] <= T].reset_index(drop=True)
    b = b[b["available_utc"] <= T].reset_index(drop=True)
    assert len(a) == len(b) > 5
    pd.testing.assert_frame_equal(a, b, check_exact=True)
    # the check has power: rows after T do move
    full_a, full_b = F.compute(minutes, tf, phase=phase), F.compute(after, tf, phase=phase)
    assert not full_a["rsi14"].tail(5).equals(full_b["rsi14"].tail(5))


def test_a_revised_minute_never_reaches_any_row_and_a_stale_row_is_not_served(minutes):
    """Quant L1: a minute revised years later (available_utc 2028) puts its decision bar and its daily bar out of
    every frame (drop_late), so no row depends on the revised value; a decision whose last available row is more
    than two bars old reads NaN, counted."""
    k = int(pd.DatetimeIndex(minutes["ts_utc"]).searchsorted(START + pd.Timedelta(days=120, hours=10, minutes=3)))
    out = []
    for bump in (25.0, 400.0):
        rev = minutes.copy()
        rev["available_utc"] = rev["visible_at"]
        rev.loc[k, "available_utc"] = pd.Timestamp("2028-01-03", tz="UTC")
        rev.loc[k, ["bid_close", "ask_close", "bid_high", "ask_high"]] += bump
        out.append(F.compute(rev, "1h"))
    pd.testing.assert_frame_equal(out[0], out[1], check_exact=True)
    bar_close = pd.Timestamp(minutes.loc[k, "ts_utc"]).floor("1h") + pd.Timedelta(hours=1)
    assert bar_close not in set(pd.DatetimeIndex(out[0]["decision_utc"]))
    rows = out[0]
    last = pd.Timestamp(rows["decision_utc"].iloc[-1])
    held = rows.copy()                                    # the last three rows held back by a later revision
    held.loc[held.index[-3:], "available_utc"] = pd.Timestamp("2028-01-03", tz="UTC")
    got = F.features_for(held, [last - pd.Timedelta(hours=2), last])
    assert got["rsi14"].notna().tolist() == [True, False] and got.attrs["stale"] == 1
    assert F.features_for(rows, [last, last + pd.Timedelta(minutes=30)])["rsi14"].notna().all()
    with pytest.raises(ValueError, match="phase"):
        F.features_for(pd.concat([rows, rows.assign(phase=5)]), [last])


def test_a_year_built_with_its_warm_up_equals_the_single_pass_over_the_whole_history(tmp_path):
    m = make_minutes(days=400, start=pd.Timestamp("2023-05-01", tz="UTC"), seed=5)
    whole = F.compute(m, "1h")
    year = F.build_year(m, instrument="XAUUSD", tf="1h", year=2024, cache_dir=tmp_path)
    assert len(year) > 0
    assert (pd.DatetimeIndex(year["decision_utc"]).year == 2024).all()
    ref = whole[pd.DatetimeIndex(whole["decision_utc"]).year == 2024].reset_index(drop=True)
    pd.testing.assert_frame_equal(year.reset_index(drop=True), ref)


def test_the_cache_key_never_depends_on_decisions_and_changes_with_data_code_or_spec(tmp_path, minutes):
    k1 = F.cache_key(minutes, instrument="XAUUSD", tf="1h", year=2023)
    assert k1 == F.cache_key(minutes, instrument="XAUUSD", tf="1h", year=2023)
    revised = minutes.copy()
    revised.loc[len(revised) // 2, "bid_close"] += 0.01
    assert F.cache_key(revised, instrument="XAUUSD", tf="1h", year=2023) != k1
    assert F.cache_key(minutes, instrument="XAUUSD", tf="15m", year=2023) != k1
    other = F.SPEC.model_copy(update={"feature_window": 500})
    assert F.cache_key(minutes, instrument="XAUUSD", tf="1h", year=2023, spec=other) != k1
    with mock.patch.object(F, "code_hash", return_value="0" * 64):
        assert F.cache_key(minutes, instrument="XAUUSD", tf="1h", year=2023) != k1


def test_a_second_build_reads_the_cache_and_computes_nothing(tmp_path, minutes):
    first = F.build_year(minutes, instrument="XAUUSD", tf="1h", year=2023, cache_dir=tmp_path)
    with mock.patch.object(F, "compute", side_effect=AssertionError("recomputed")):
        again = F.build_year(minutes, instrument="XAUUSD", tf="1h", year=2023, cache_dir=tmp_path)
    pd.testing.assert_frame_equal(first, again)
    man = F.read_manifests(tmp_path)
    assert len(man) == 1 and man[0]["spec_id"] == "fs-v1" and man[0]["rows"] == len(first)
    assert {"code_hash", "spec_hash", "minutes_sha256", "htf_sha256", "seconds"} <= set(man[0])


def test_the_job_order_is_longest_first():
    jobs = F.job_order([("XAUUSD", tf, y) for tf in ("1h", "1m", "4h", "15m") for y in (2010, 2011)])
    assert [j[1] for j in jobs[:2]] == ["1m", "1m"] and [j[1] for j in jobs[-2:]] == ["4h", "4h"]


def test_minutes_or_years_touching_the_holdout_are_refused(tmp_path):
    m = make_minutes(days=30, start=pd.Timestamp("2025-09-20", tz="UTC"))
    with pytest.raises(holdout_guard.HoldoutSealed):
        F.compute(m, "1h")
    ok = make_minutes(days=30, start=pd.Timestamp("2024-01-02", tz="UTC"))
    with pytest.raises(holdout_guard.HoldoutSealed):
        F.build_year(ok, instrument="XAUUSD", tf="1h", year=2026, cache_dir=tmp_path)


# ---------------------------------------------------------------------------------------------- tiers
def _tiers(tmp_path, tiers: dict[str, str]) -> F.Tiers:
    p = tmp_path / "tiers_r11-v2.1.csv"
    pd.DataFrame({"key": "XAUUSD", "month": list(tiers), "tier": list(tiers.values()),
                  "r11_id": "r11-v2.1-2026-11-25"}).to_csv(p, index=False)
    return F.Tiers.from_csv(p, source="synthetic")       # the fixture's minutes are graded as their own source


def test_tier_aware_loading_keeps_a_for_intraday_b_only_daily_and_excludes_c_f_p_and_ungraded(tmp_path, minutes):
    tiers = _tiers(tmp_path, {"2023-06": "C", "2023-07": "A", "2023-08": "A-consistent", "2023-09": "B",
                              "2023-10": "F", "2023-11": "P"})
    assert len(tiers.sha256) == 64 and tiers.r11_id == "r11-v2.1-2026-11-25"
    F.build_year(minutes, instrument="XAUUSD", tf="1h", year=2023, cache_dir=tmp_path / "c")
    rows, man = F.load(tmp_path / "c", instrument="XAUUSD", tf="1h", years=[2023], tiers=tiers, minutes=minutes)
    assert set(rows["tier"]) <= {"A", "A-consistent"} and len(rows)
    assert man["tiers_sha256"] == tiers.sha256 and man["r11_id"] == tiers.r11_id
    assert man["dropped"]["B"] > 0 and man["dropped"]["C"] + man["dropped"]["F"] + man["dropped"]["P"] > 0
    assert man["dropped"]["ungraded"] > 0                         # December: no tier row, never loaded
    assert {"tier_window"} <= set(rows.columns)
    assert (rows["tier_window"].map(F.TIER_RANK) >= rows["tier"].map(F.TIER_RANK)).all()
    # the daily timeframe keeps B (its first rows come after 120 daily bars: November)
    b_all = _tiers(tmp_path, {f"2023-{mo:02d}": "B" for mo in range(6, 13)})
    F.build_year(minutes, instrument="XAUUSD", tf="1d", year=2023, cache_dir=tmp_path / "d")
    daily, dm = F.load(tmp_path / "d", instrument="XAUUSD", tf="1d", years=[2023], tiers=b_all, minutes=minutes)
    assert len(daily) and set(daily["tier"]) == {"B"} and dm["dropped"]["B"] == 0


def test_a_judged_or_training_read_always_needs_tiers_and_the_data_key_only_parity_may_skip_them(tmp_path, minutes):
    F.build_year(minutes, instrument="XAUUSD", tf="1h", year=2023, cache_dir=tmp_path)
    tiers = _tiers(tmp_path, {f"2023-{mo:02d}": "A" for mo in range(6, 13)})
    for purpose in ("judge", "train"):
        with pytest.raises(ValueError, match="tiers"):
            F.load(tmp_path, instrument="XAUUSD", tf="1h", years=[2023], tiers=None, minutes=minutes, purpose=purpose)
        with pytest.raises(ValueError, match="minutes"):
            F.load(tmp_path, instrument="XAUUSD", tf="1h", years=[2023], tiers=tiers, purpose=purpose)
    rows, man = F.load(tmp_path, instrument="XAUUSD", tf="1h", years=[2023], tiers=None, purpose="parity")
    assert len(rows) and man["tiers_sha256"] is None and man["purpose"] == "parity"


def test_a_revised_minute_makes_the_cached_partition_unreadable_until_rebuilt(tmp_path, minutes):
    """Code review M2: load takes the key the data gives, never the newest file."""
    tiers = _tiers(tmp_path, {f"2023-{mo:02d}": "A" for mo in range(6, 13)})
    F.build_year(minutes, instrument="XAUUSD", tf="1h", year=2023, cache_dir=tmp_path / "c")
    revised = minutes.copy()
    revised.loc[len(revised) // 3, ["bid_high", "ask_high"]] += 5.0        # moves its hour's high
    with pytest.raises(FileNotFoundError, match="key"):
        F.load(tmp_path / "c", instrument="XAUUSD", tf="1h", years=[2023], tiers=tiers, minutes=revised)
    F.build_year(revised, instrument="XAUUSD", tf="1h", year=2023, cache_dir=tmp_path / "c")
    old, _ = F.load(tmp_path / "c", instrument="XAUUSD", tf="1h", years=[2023], tiers=tiers, minutes=minutes)
    new, _ = F.load(tmp_path / "c", instrument="XAUUSD", tf="1h", years=[2023], tiers=tiers, minutes=revised)
    assert not old["atr_ratio_14_100"].equals(new["atr_ratio_14_100"])


def test_load_refuses_rows_that_reach_a_moved_holdout(tmp_path, minutes, monkeypatch):
    """Code review M1: the guard runs at load too, on decisions and on window starts."""
    tiers = _tiers(tmp_path, {f"2023-{mo:02d}": "A" for mo in range(6, 13)})
    F.build_year(minutes, instrument="XAUUSD", tf="1h", year=2023, cache_dir=tmp_path)
    keys = {(2023, 0): F.cache_key(minutes, instrument="XAUUSD", tf="1h", year=2023)}
    monkeypatch.setattr(holdout_guard, "HOLDOUT_START", pd.Timestamp("2023-11-01", tz="UTC"))
    with pytest.raises(holdout_guard.HoldoutSealed):
        F.load(tmp_path, instrument="XAUUSD", tf="1h", years=[2023], tiers=tiers, expect_keys=keys)
    assert "goldbot/data/holdout_guard.py" in {p.relative_to(F.ROOT).as_posix() for p in F.code_files()}


def test_tiers_of_another_source_are_refused_unless_named_as_a_recorded_proxy(tmp_path, minutes):
    """Quant C3: Dukascopy gold read through the HistData grade only as an explicit, recorded proxy."""
    p = tmp_path / "tiers.csv"
    pd.DataFrame({"key": "XAUUSD", "month": [f"2023-{mo:02d}" for mo in range(6, 13)], "tier": "A",
                  "r11_id": "r11-v2.1-2026-11-25"}).to_csv(p, index=False)
    hist = F.Tiers.from_csv(p)                                    # grades histdata:XAUUSD
    duka = minutes.assign(source="dukascopy")
    F.build_year(duka, instrument="XAUUSD", tf="1h", year=2023, cache_dir=tmp_path / "c")
    with pytest.raises(ValueError, match="proxy_source"):
        F.load(tmp_path / "c", instrument="XAUUSD", tf="1h", years=[2023], tiers=hist, minutes=duka)
    rows, man = F.load(tmp_path / "c", instrument="XAUUSD", tf="1h", years=[2023], tiers=hist, minutes=duka,
                       proxy_source="histdata:XAUUSD")
    assert len(rows) and man["tier_proxy"] == "histdata:XAUUSD"


def test_the_purge_drops_a_phase_5_copy_of_a_test_row_from_training(minutes):
    """Quant C6: group_id counts n_eff, it does not replace the purge."""
    p0, p5 = F.compute(minutes, "15m"), F.compute(minutes, "15m", phase=5)
    hold = pd.Timedelta(hours=4)
    test = p0.iloc[3000:3200].assign(label_end_utc=lambda d: d["decision_utc"] + hold)
    copy = p5[p5["group_id"].isin(test["group_id"])].assign(label_end_utc=lambda d: d["decision_utc"] + hold)
    far = p0.iloc[:500].assign(label_end_utc=lambda d: d["decision_utc"] + hold)
    assert len(copy) > 100
    assert not F.purge(copy, test).any()
    assert F.purge(far, test).all()


def test_a_model_at_4h_or_reading_d1_columns_serves_only_when_the_serving_path_matches_fs_v1(tmp_path):
    """Quant C4: the promotion gate's check reads the parity oracle's deployed-path block."""
    rep = {"tf": "4h", "snapshot_as_deployed_90k": {"n": 300, "columns_over_1e-9": {"atr_ratio_14_100": 0.049},
                                                    "columns_mask_differs": {"d1_rv_ratio": 300}}}
    p = tmp_path / "XAUUSD_4h.json"
    p.write_text(json.dumps(rep))
    assert F.serve_ready("15m", ["rsi14"], None)[0]
    assert not F.serve_ready("15m", ["rsi14", "d1_rv_ratio"], None)[0]
    assert not F.serve_ready("4h", ["atr_ratio_14_100"], p)[0]
    rep["snapshot_as_deployed_90k"] = {"n": 300, "columns_over_1e-9": {}, "columns_mask_differs": {}}
    p.write_text(json.dumps(rep))
    assert F.serve_ready("4h", ["atr_ratio_14_100"], p)[0]


def test_pooled_bid_only_and_bid_ask_rows_drop_the_spread_and_tick_columns(minutes):
    """Quant L3."""
    a = F.compute(minutes, "1h")
    mixed = pd.concat([a, a.assign(bid_only=True)])
    assert set(F.columns_for(a)) == set(F.SPEC.columns)
    assert set(F.SPEC.columns) - set(F.columns_for(mixed)) == set(F.BID_ONLY_MISSING)


def test_every_row_carries_its_availability_source_and_window_start(minutes):
    rows = F.compute(minutes, "1h")
    for c in ("decision_utc", "available_utc", "window_from_utc", "source", "instrument", "tf"):
        assert c in rows.columns
    assert (rows["available_utc"] >= rows["decision_utc"]).all()
    assert str(rows["available_utc"].dtype) == "datetime64[ns, UTC]"


# ---------------------------------------------------------------------------------------------- phases, sources
def test_offset_phase_candles_share_the_group_id_of_their_phase_0_bar_and_judged_reads_refuse_them(tmp_path, minutes):
    p0, p5 = F.compute(minutes, "15m"), F.compute(minutes, "15m", phase=5)
    assert set(p0["phase"]) == {0} and set(p5["phase"]) == {5}
    assert (pd.DatetimeIndex(p5["decision_utc"]).minute % 15 == 5).all()
    assert (p0["group_id"] == F._ns(p0["decision_utc"])).all()                    # phase 0: its own close
    g = p5["group_id"].to_numpy() - F._ns(p5["decision_utc"])
    assert ((g > 0) & (g < 15 * 60 * 10**9)).all()                                # the next phase-0 close
    assert not np.allclose(p0["ret_4"].to_numpy()[-50:], p5["ret_4"].to_numpy()[-50:], equal_nan=True)
    with pytest.raises(ValueError, match="phase"):
        F.compute(minutes, "1d", phase=5)
    with pytest.raises(ValueError, match="phase"):
        F.compute(minutes, "15m", phase=15)
    F.build_year(minutes, instrument="XAUUSD", tf="15m", year=2023, cache_dir=tmp_path, phase=5)
    F.build_year(minutes, instrument="XAUUSD", tf="15m", year=2023, cache_dir=tmp_path)
    tiers = _tiers(tmp_path, {f"2023-{mo:02d}": "A" for mo in range(6, 13)})
    with pytest.raises(ValueError, match="phase 0"):
        F.load(tmp_path, instrument="XAUUSD", tf="15m", years=[2023], tiers=tiers, minutes=minutes, phases=(0, 5))
    rows, _ = F.load(tmp_path, instrument="XAUUSD", tf="15m", years=[2023], tiers=tiers, minutes=minutes,
                     phases=(0, 5), purpose="train")
    assert set(rows["phase"]) == {0, 5}


def _histdata(tmp_path, minutes: pd.DataFrame, key: str = "EURUSD") -> None:
    d = tmp_path / "histdata_m1" / key
    d.mkdir(parents=True)
    b = minutes[["ts_utc", "visible_at", "bid_open", "bid_high", "bid_low", "bid_close"]].copy()
    b[["bid_open", "bid_high", "bid_low", "bid_close"]] /= 1500.0
    b["source"], b["dq_flag"] = f"histdata:{key}", ""
    b.loc[5, "dq_flag"] = "error:clock"
    for y, part in b.groupby(pd.DatetimeIndex(b["ts_utc"]).year):
        part.to_parquet(d / f"{key.lower()}_1m_bid_{y}.parquet", index=False)


def test_a_bid_only_histdata_instrument_loads_with_its_spread_and_tick_columns_nan_and_flagged(tmp_path, minutes):
    _histdata(tmp_path, minutes)
    assert F.histdata_instruments(tmp_path) == ["EURUSD"]
    m = F.history_minutes("EURUSD", 2023, 2024, tmp_path)
    assert len(m) == len(minutes) - 1 and m["bid_only"].all()                      # the error minute is dropped
    assert (m["ask_close"] == m["bid_close"]).all() and m["tick_count"].isna().all()
    rows = F.compute(m, "1h", instrument="EURUSD")
    assert rows["bid_only"].all() and set(rows["source"]) == {"histdata:EURUSD"}
    for c in F.BID_ONLY_MISSING:
        assert rows[c].isna().all(), c
    late = rows.tail(200)
    for c in ("atr_ratio_14_100", "rsi14", "adx14", "bb_z_20", "d1_rv_ratio", "hour_utc"):
        assert late[c].notna().all(), c


def test_the_spec_columns_are_exactly_what_its_registered_features_produce():
    from goldbot.features import build_features
    m = pd.DataFrame({"ts_utc": pd.date_range("2024-01-02", periods=300, freq="1h", tz="UTC"),
                      "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "spread": 0.1, "tick_count": 5.0})
    dec = set(build_features(m, list(F.SPEC.decision_features)).columns)
    ctx = set(build_features(m, list(F.SPEC.context_features)).columns)
    for c in F.SPEC.columns:
        assert (c[3:] in ctx) if c.startswith("d1_") else (c in dec), c


def test_staleness_counts_rows_so_a_market_gap_never_blanks_a_decision(minutes):
    """Quant re-verify R1: after the daily break or the weekend the engine reads the last bar before the gap; so
    does fs-v1 (staleness in rows, not wall-clock time)."""
    rows = F.compute(minutes, "15m")
    sunday = pd.Timestamp("2023-10-01 22:07", tz="UTC")             # weekly reopen + 7 min
    tuesday = pd.Timestamp("2023-10-03 22:07", tz="UTC")            # after the 21:00-22:00 break
    got = F.features_for(rows, [sunday, tuesday])
    assert got.attrs["stale"] == 0 and got["rsi14"].notna().all()
    assert list(pd.DatetimeIndex(got["decision_utc"])) == [pd.Timestamp("2023-09-29 21:00", tz="UTC"),
                                                           pd.Timestamp("2023-10-03 21:00", tz="UTC")]


def test_the_code_hash_covers_the_import_closure_but_not_the_whole_repo():
    """Quant L4: store, calendars, daily, config, instruments and the guard are in; jobs, engine and UI are not."""
    fs = {p.relative_to(F.ROOT).as_posix() for p in F.code_files()}
    for need in ("goldbot/data/store.py", "goldbot/data/us_calendar.py", "goldbot/data/daily.py", "goldbot/config.py",
                 "goldbot/instruments.py", "goldbot/data/holdout_guard.py", "goldbot/features/technical.py"):
        assert need in fs, need
    assert not any(f.startswith(("goldbot/engine/", "goldbot/ops/", "goldbot/telegram/", "goldbot/api/")) for f in fs)
    assert "goldbot/research/self_retrain.py" not in fs            # its FEATURES are in the spec hash
