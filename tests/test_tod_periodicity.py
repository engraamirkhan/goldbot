"""BACKLOG 89: the opt-in `tod_periodicity` feature group (N1, docs/research/candidates/next-2026-10-18.md; signed
amendment under O-4 in docs/research/preregistration-2027Q1.md).

Design rule (amendment): "Slots are 30-minute UTC bins; a 15m bar maps to the slot containing its open (both 15m bars
of a slot map to it). Each column uses only the same slot on earlier days, so `available_utc` = the prior day's slot
close; joined by `asof_join`; `lookahead_check` must report 0 columns ... `tod_periodicity` is an opt-in family, never
in `DEFAULT_FEATURE_NAMES` ... A build that adds it to the default set is refused at review."
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features import FEATURES, build_features, registry
from goldbot.features import tod_periodicity as tod
from goldbot.features.technical import atr
from goldbot.research.discovery import DiscoveryConfig, check_group_sizes, column_groups
from goldbot.research.pipeline import (
    DEFAULT_FEATURE_NAMES,
    MACRO_FEATURE_NAMES,
    OPT_IN_FEATURES,
    build_decision_frame,
    lookahead_check,
)

FROZEN_DEFAULT_VERSION = "f-079deacc61"      # the Q1 2027 H-02/H-01/H-04/C4H feature set (preregistration-2027Q1.yaml)
FROZEN_MACRO_VERSION = "f-51ac5ebabb"        # default + macro drivers
COLUMNS = ["slot_mean_ret_5d", "slot_mean_ret_10d", "slot_mean_ret_20d", "slot_hit_rate_20d", "slot_t_20d",
           "slot_ret_over_atr"]


@pytest.fixture
def enabled():
    """Register the opt-in feature for one test and restore the registry afterwards (other tests see the default)."""
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    yield tod.enable()
    registry.FEATURES.clear()
    registry.FEATURES.update(feats)
    registry.SIGNED.clear()
    registry.SIGNED.update(signed)


def _bars(start: str, end: str, freq: str = "15min", seed: int = 3) -> pd.DataFrame:
    """Mid bars of a random walk, Monday to Friday only (no Saturday, no Sunday)."""
    ts = pd.date_range(start, end, freq=freq, tz="UTC", inclusive="left")
    ts = ts[ts.dayofweek < 5]
    rng = np.random.default_rng(seed)
    close = 1900 * np.exp(np.cumsum(0.0006 * rng.standard_normal(len(ts))))
    op = np.concatenate(([1900.0], close[:-1]))
    wig = np.abs(0.0003 * rng.standard_normal(len(ts)))
    return pd.DataFrame({"ts_utc": ts, "open": op, "high": np.maximum(op, close) * (1 + wig),
                         "low": np.minimum(op, close) * (1 - wig), "close": close})


def _slot_returns(bars: pd.DataFrame) -> dict[pd.Timestamp, float]:
    """Reference, by plain loops: slot start -> log(last close / first open) of the complete 15m slots (2 bars)."""
    groups: dict[pd.Timestamp, list[int]] = {}
    for i, t in enumerate(bars["ts_utc"]):
        groups.setdefault(t.floor("30min"), []).append(i)
    return {s: float(np.log(bars["close"].iloc[ix[-1]] / bars["open"].iloc[ix[0]]))
            for s, ix in groups.items() if len(ix) == 2}


def _earlier(rets: dict[pd.Timestamp, float], t: pd.Timestamp) -> list[float]:
    """The returns of the slot containing `t` on strictly earlier days, oldest first."""
    start = t.floor("30min")
    return [rets[s] for s in sorted(rets) if s < start and (s.hour, s.minute) == (start.hour, start.minute)]


def _row(bars: pd.DataFrame, when: str) -> int:
    return int(np.flatnonzero(pd.DatetimeIndex(bars["ts_utc"]) == pd.Timestamp(when, tz="UTC"))[0])


def test_a_15m_bar_maps_to_the_30_minute_utc_slot_containing_its_open_and_dst_does_not_move_it():
    ts = pd.DatetimeIndex(["2025-03-28 13:00", "2025-03-28 13:15", "2025-03-28 13:30", "2025-03-28 13:45",
                           "2025-10-24 13:00", "2025-10-24 13:15",      # either side of both DST changes of 2025
                           "2025-11-07 13:00", "2025-11-07 13:15"], tz="UTC")
    slot = tod.slot_start(ts)
    assert [t.strftime("%H:%M") for t in slot] == ["13:00", "13:00", "13:30", "13:30", "13:00", "13:00", "13:00",
                                                   "13:00"]
    assert list(tod.slot_of_day(ts)) == [26, 26, 27, 27, 26, 26, 26, 26]


def test_the_group_has_six_columns_in_its_own_family(enabled):
    spec = FEATURES[enabled]
    assert spec.family == "tod_periodicity" and spec.name == "tod_periodicity"
    X = build_features(_bars("2025-03-03", "2025-03-10"), [enabled])
    assert [c for c in X.columns if c != "ts_utc"] == COLUMNS


def test_both_15m_bars_of_a_slot_carry_the_same_value_and_it_is_the_mean_of_earlier_days_only(enabled):
    bars = _bars("2025-03-03", "2025-04-18")
    X = build_features(bars, [enabled])
    rets = _slot_returns(bars)
    for day in ("2025-04-09", "2025-04-16"):
        i0 = _row(bars, f"{day} 08:00")
        past = _earlier(rets, bars["ts_utc"].iloc[i0])
        for n in (5, 10, 20):
            col = X[f"slot_mean_ret_{n}d"]
            assert col.iloc[i0] == col.iloc[i0 + 1]                           # 08:00 and 08:15 share the slot
            assert col.iloc[i0] == pytest.approx(np.mean(past[-n:]), rel=1e-9)   # the same slot on earlier days
        assert X["slot_hit_rate_20d"].iloc[i0] == X["slot_hit_rate_20d"].iloc[i0 + 1]


def test_a_slots_value_never_uses_its_own_days_return(enabled):
    """Move one day's 08:00-08:30 prices: the slot's own two bars are unchanged, the next day's same slot is the
    earliest bar that sees it, and no other time of day changes."""
    bars = _bars("2025-03-03", "2025-04-18")
    ts = pd.DatetimeIndex(bars["ts_utc"])
    day = pd.Timestamp("2025-04-02 08:00", tz="UTC")
    hit = np.asarray((ts >= day) & (ts < day + pd.Timedelta("30min")))
    shocked = bars.copy()
    for k in ("close", "high"):
        shocked.loc[hit, k] = shocked.loc[hit, k] * 1.05
    base, alt = build_features(bars, [enabled]), build_features(shocked, [enabled])
    same = np.isclose(base[COLUMNS].to_numpy(float), alt[COLUMNS].to_numpy(float), equal_nan=True).all(axis=1)
    changed = ~same
    assert not changed[hit].any()                                        # not on its own day's slot bars
    assert ts[changed].min() == pd.Timestamp("2025-04-03 08:00", tz="UTC")     # the next day's same slot
    # the slot's return statistics change in that slot only (ret_over_atr also moves later through the ATR, which is
    # past-only too: Wilder smoothing of the shocked bars)
    stats = [c for c in COLUMNS if c != "slot_ret_over_atr"]
    moved = ~np.isclose(base[stats].to_numpy(float), alt[stats].to_numpy(float), equal_nan=True).all(axis=1)
    assert set(ts[moved].strftime("%H:%M")) <= {"08:00", "08:15"}


def test_a_monday_slot_uses_fridays_close_as_its_latest_observation(enabled):
    bars = _bars("2025-03-03", "2025-04-18")
    X = build_features(bars, [enabled])
    rets = _slot_returns(bars)
    assert pd.Timestamp("2025-04-14").dayofweek == 0
    week = [rets[pd.Timestamp(f"2025-04-{d} 08:00", tz="UTC")] for d in (7, 8, 9, 10, 11)]      # Monday .. Friday
    assert X["slot_mean_ret_5d"].iloc[_row(bars, "2025-04-14 08:00")] == pytest.approx(np.mean(week), rel=1e-9)
    # nothing is stamped on a weekend: there are no bars, and the Friday-evening bars see Thursday's and Friday's slots
    assert not pd.DatetimeIndex(bars["ts_utc"]).dayofweek.isin([5, 6]).any()


def test_the_columns_are_nan_before_enough_days(enabled):
    bars = _bars("2025-03-03", "2025-04-18")
    X = build_features(bars, [enabled])
    d4, d5 = _row(bars, "2025-03-07 08:00"), _row(bars, "2025-03-10 08:00")       # 4 and 5 earlier days
    assert np.isnan(X["slot_mean_ret_5d"].iloc[d4]) and np.isfinite(X["slot_mean_ret_5d"].iloc[d5])
    assert np.isnan(X["slot_mean_ret_20d"].iloc[d5]) and np.isnan(X["slot_t_20d"].iloc[d5])
    assert np.isfinite(X[COLUMNS].iloc[-1].to_numpy(float)).all()


def test_a_slot_missing_a_bar_is_left_out_of_the_history_not_averaged_on_half_its_bars(enabled):
    bars = _bars("2025-03-03", "2025-04-18")
    holes = bars[bars["ts_utc"] != pd.Timestamp("2025-04-08 08:15", tz="UTC")].reset_index(drop=True)
    X = build_features(holes, [enabled])
    monday = _row(holes, "2025-04-14 08:00")
    past = _earlier(_slot_returns(holes), holes["ts_utc"].iloc[monday])
    assert X["slot_mean_ret_5d"].iloc[monday] == pytest.approx(np.mean(past[-5:]), rel=1e-9)
    full_past = _earlier(_slot_returns(bars), bars["ts_utc"].iloc[_row(bars, "2025-04-14 08:00")])
    assert np.mean(full_past[-5:]) != pytest.approx(np.mean(past[-5:]))           # the hole day was dropped
    half = _row(holes, "2025-04-08 08:00")                                      # the half-observed slot's own bar
    assert X["slot_mean_ret_5d"].iloc[half] == pytest.approx(
        np.mean(_earlier(_slot_returns(holes), holes["ts_utc"].iloc[half])[-5:]), rel=1e-9)


def test_bars_longer_than_a_slot_give_all_nan_columns(enabled):
    X = build_features(_bars("2025-03-03", "2025-04-18", freq="1h"), [enabled])
    assert X[COLUMNS].isna().all().all()


def test_hit_rate_t_and_return_over_atr_follow_their_definitions(enabled):
    bars = _bars("2025-03-03", "2025-05-02")
    X = build_features(bars, [enabled])
    rets = _slot_returns(bars)
    i = _row(bars, "2025-05-01 09:30")
    past = _earlier(rets, bars["ts_utc"].iloc[i])
    r = np.array(past[-20:])
    assert X["slot_hit_rate_20d"].iloc[i] == pytest.approx((r > 0).mean())
    assert X["slot_t_20d"].iloc[i] == pytest.approx(r.mean() / (r.std(ddof=1) / np.sqrt(20)))
    prev_slot = pd.Timestamp("2025-04-30 09:30", tz="UTC")
    last_bar = _row(bars, "2025-04-30 09:45")                                   # the previous day's slot-closing bar
    pct = atr(bars).iloc[last_bar] / bars["close"].iloc[last_bar]
    assert X["slot_ret_over_atr"].iloc[i] == pytest.approx(rets[prev_slot] / pct)


def test_no_column_changes_when_future_bars_are_appended(enabled):
    bars = _bars("2025-03-03", "2025-05-02")
    full = build_features(bars, [enabled]).drop(columns=["ts_utc"])
    for cut in (97, 700, len(bars) // 2, len(bars) - 3, len(bars) - 1):
        part = build_features(bars.iloc[:cut], [enabled]).drop(columns=["ts_utc"])
        pd.testing.assert_frame_equal(part, full.iloc[:cut], check_dtype=False)


def test_the_pipeline_lookahead_check_reports_zero_columns(enabled):
    ticks = synthetic_ticks("2025-03-03", "2025-04-18", ticks_per_minute=2, seed=5)
    bars_15m = resample_bars(ticks_to_1m(ticks), "15m").reset_index(drop=True)
    out = lookahead_check(bars_15m, None, feature_names=[*DEFAULT_FEATURE_NAMES, enabled])
    assert out["lookahead_columns"] == [] and out["columns_checked"] >= len(COLUMNS)
    _, X = build_decision_frame(bars_15m, None, [enabled])
    assert np.isfinite(X[COLUMNS].iloc[-1].to_numpy(float)).all()


def test_it_is_opt_in_so_the_default_feature_list_and_every_pinned_version_are_unchanged(enabled):
    assert "tod_periodicity" in OPT_IN_FEATURES and "tod_periodicity" in FEATURES
    assert "tod_periodicity" not in DEFAULT_FEATURE_NAMES
    live_default = [n for n in FEATURES if n not in OPT_IN_FEATURES]       # the default set, recomputed with it enabled
    assert live_default == DEFAULT_FEATURE_NAMES
    assert registry.feature_version(live_default) == FROZEN_DEFAULT_VERSION
    assert registry.feature_version([*live_default, *MACRO_FEATURE_NAMES]) == FROZEN_MACRO_VERSION
    assert registry.feature_version([*live_default, enabled]) != FROZEN_DEFAULT_VERSION     # only O-4's own set moves


def test_importing_the_package_does_not_register_it():
    assert "tod_periodicity" not in FEATURES
    assert registry.feature_version(DEFAULT_FEATURE_NAMES) == FROZEN_DEFAULT_VERSION


def test_it_is_its_own_group_in_o4s_family_map_and_the_group_size_test_passes(enabled):
    ticks = synthetic_ticks("2025-03-03", "2025-03-21", ticks_per_minute=2, seed=5)
    bars_15m = resample_bars(ticks_to_1m(ticks), "15m").reset_index(drop=True)
    names = [*DEFAULT_FEATURE_NAMES, enabled]
    m, X = build_decision_frame(bars_15m, None, names)
    cols = [c for c in X.columns if c != "ts_utc"]
    cfg = DiscoveryConfig(top_k=39, max_selected=39, survivor_unit="group", group_top_k=3, stability_threshold=0.7,
                          threshold_rule="tercile_inner", tercile_q=2 / 3, tercile_method="linear",
                          context_groups="per_timeframe")
    groups = column_groups(cols, "family", m, names, context_groups=cfg.context_groups)
    assert sorted(c for c, g in groups.items() if g == "tod_periodicity") == sorted(COLUMNS)
    others = column_groups([c for c in cols if c not in COLUMNS], "family", m, list(DEFAULT_FEATURE_NAMES),
                           context_groups=cfg.context_groups)
    assert {c: g for c, g in groups.items() if c not in COLUMNS} == others      # no other column moved group
    check_group_sizes(groups, cfg, "family")
    assert Counter(groups.values())["tod_periodicity"] == 6


def test_signed_columns_are_side_aligned_around_their_neutral_values(enabled):
    X = pd.DataFrame({"side": [1, -1], "slot_mean_ret_5d": [0.001, 0.001], "slot_t_20d": [1.0, 1.0],
                      "slot_hit_rate_20d": [0.6, 0.6], "slot_ret_over_atr": [0.5, 0.5]})
    out = registry.side_align(X)
    assert out["slot_mean_ret_5d"].tolist() == [0.001, -0.001]
    assert out["slot_hit_rate_20d"].tolist() == pytest.approx([0.1, -0.1])
    assert out["slot_ret_over_atr"].tolist() == [0.5, -0.5] and out["slot_t_20d"].tolist() == [1.0, -1.0]
