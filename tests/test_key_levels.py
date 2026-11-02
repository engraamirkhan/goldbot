"""Key levels (goldbot/features/key_levels.py): point-in-time multi-timeframe support/resistance zones.

Design rule (sprint-3000 brief, ADDENDUM A, owner 2026-11-02): "a zone exists only from the close of the bar that
confirms it"; appending bars never changes a past zone; every join to other bars is asof on `available_utc`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.data.resample import mid, resample_bars, ticks_to_1m
from goldbot.data.store import asof_join
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features import key_levels as kl
from goldbot.features.structure import swing_points


@pytest.fixture(scope="module")
def bars_1m() -> pd.DataFrame:
    # ~11 weeks: enough for ATR(14, 1d), the strength percentile warm-up and a few weekly bars
    return ticks_to_1m(synthetic_ticks("2025-01-06", "2025-03-22", ticks_per_minute=1, seed=5))


@pytest.fixture(scope="module")
def frame(bars_1m: pd.DataFrame) -> pd.DataFrame:
    return kl.key_level_frame(bars_1m)


def _vis(bars: pd.DataFrame) -> pd.Series:
    return pd.Series(pd.to_datetime(bars["visible_at"], utc=True))


# ------------------------------------------------------------------------------------------- clustering and strength
def test_levels_within_the_cluster_width_merge_into_one_zone_listing_every_timeframe():
    z = kl.cluster_levels(prices=np.array([100.0, 100.2, 100.24, 101.0]), weights=np.array([1.0, 4.0, 2.0, 3.0]),
                          bits=np.array([kl.TF_BIT["15m"], kl.TF_BIT["1d"], kl.TF_BIT["1h"], kl.TF_BIT["4h"]]),
                          origin=np.array([1, 1, -1, 1]), width=0.25)
    assert len(z.price) == 2
    assert (z.lo[0], z.hi[0]) == (100.0, 100.24)
    assert z.strength[0] == pytest.approx(7.0)
    assert z.price[0] == pytest.approx((100 * 1 + 100.2 * 4 + 100.24 * 2) / 7)
    assert kl.tf_names(int(z.bits[0])) == ("15m", "1h", "1d")
    assert z.touches.tolist() == [3, 1]
    assert z.strength[1] == pytest.approx(3.0)


def test_a_zone_is_never_wider_than_the_cluster_width_even_when_levels_chain():
    prices = np.arange(0, 2.0, 0.1) + 100.0           # each level 0.1 from the next: a chain 1.9 wide
    z = kl.cluster_levels(prices=prices, weights=np.ones(len(prices)), bits=np.full(len(prices), kl.TF_BIT["15m"]),
                          origin=np.zeros(len(prices), dtype=int), width=0.25)
    assert ((z.hi - z.lo) <= 0.25 + 1e-12).all()
    assert z.touches.sum() == len(prices)


def test_strength_is_timeframe_weight_halving_every_half_life_of_its_timeframe():
    assert kl.TF_WEIGHT == {"15m": 1, "1h": 2, "4h": 3, "1d": 4, "1w": 5, "1M": 5}
    day = 86_400 * 10**9
    for tf, hl in kl.HALF_LIFE_DAYS.items():
        w = kl.decayed_weight(tf, age_ns=np.array([0, int(hl * day), int(2 * hl * day)]))
        assert w.tolist() == pytest.approx([kl.TF_WEIGHT[tf], kl.TF_WEIGHT[tf] / 2, kl.TF_WEIGHT[tf] / 4])


def test_round_numbers_are_fifty_and_hundred_dollar_levels_around_the_price():
    p, w = kl.round_levels(close=2061.0, atr_1d=40.0)
    assert p.tolist() == [1950.0, 2000.0, 2050.0, 2100.0, 2150.0]       # within 3 ATR(1d) of the close
    assert w.tolist() == [1.0, 2.0, 1.0, 2.0, 1.0]                       # $100 counts double


def test_a_zone_born_as_resistance_flips_to_support_after_a_close_above_it():
    z = kl.cluster_levels(prices=np.array([100.0]), weights=np.array([4.0]), bits=np.array([kl.TF_BIT["1d"]]),
                          origin=np.array([1]), width=0.25)
    assert kl.role_of(z, 0, close=99.0) == ("resistance", False)
    assert kl.role_of(z, 0, close=101.0) == ("support", True)
    assert kl.role_of(z, 0, close=100.0)[0] == "inside"


# ------------------------------------------------------------------------------------------- point in time
def test_a_swing_zone_appears_exactly_at_the_close_of_the_bar_that_confirms_it(bars_1m):
    b15 = mid(resample_bars(bars_1m, "15m"))
    hi, _ = swing_points(b15, kl.SWING_LAG)
    vis = _vis(b15)
    cands = np.flatnonzero(hi.to_numpy())
    cands = cands[cands + kl.SWING_LAG < len(b15)]
    j = int(cands[-40])                                                  # a late 15m swing high (ATR(1d) warm)
    px = float(b15["high"].iloc[j])
    confirm = vis.iloc[j + kl.SWING_LAG]

    def members_at(t: pd.Timestamp) -> int:
        z = kl.zones_asof(bars_1m, t)
        return sum(x.touches for x in z.zones if x.lo <= px <= x.hi and "15m" in x.tfs)

    assert members_at(confirm) >= 1
    assert members_at(confirm) == members_at(confirm - pd.Timedelta(minutes=1)) + 1 or \
        members_at(confirm - pd.Timedelta(minutes=1)) == 0


@pytest.mark.parametrize("cut_frac", [0.55, 0.7713, 0.9])
def test_appending_bars_never_changes_a_past_zone_or_a_past_row(bars_1m, frame, cut_frac):
    k = int(len(bars_1m) * cut_frac)                                    # cuts mid-15m, mid-day
    head = bars_1m.iloc[:k].reset_index(drop=True)
    part = kl.key_level_frame(head)
    pd.testing.assert_frame_equal(part, frame.iloc[:k].reset_index(drop=True), check_exact=True)
    for t in _vis(head).iloc[np.array([k // 2, k - 400, k - 1])]:
        assert kl.zones_asof(head, t) == kl.zones_asof(bars_1m, t)


def test_zones_asof_only_reads_bars_visible_by_then(bars_1m):
    t = _vis(bars_1m).iloc[60_000]
    later = bars_1m.copy()
    late = later["visible_at"] > t
    later.loc[late, ["bid_high", "ask_high"]] += 500.0                  # the future is wildly different
    assert kl.zones_asof(later, t) == kl.zones_asof(bars_1m, t)


def test_a_corrected_minute_is_not_a_level_before_it_is_visible(bars_1m):
    i = 50_000
    fixed = bars_1m.copy()
    spike = float(fixed["bid_close"].iloc[i]) + 300.0
    fixed.loc[i, ["bid_high", "ask_high"]] = spike                       # a corrected minute with a new high ...
    fixed.loc[i, "visible_at"] = fixed.loc[i, "ts_utc"] + pd.Timedelta(days=2)   # ... written two days later
    for t in _vis(bars_1m).iloc[np.array([i + 30, i + 1500])]:                     # after its natural close, before it was written
        z = kl.zones_asof(fixed, t)
        assert not any(x.lo - 1 <= spike <= x.hi + 1 for x in z.zones if "round" not in x.tfs or x.touches)
        assert z == kl.zones_asof(bars_1m.drop(index=i), t)
    frame = kl.key_level_frame(fixed)
    pd.testing.assert_frame_equal(frame.drop(index=i), kl.key_level_frame(bars_1m.drop(index=i)), check_exact=True)
    assert np.isnan(float(frame["res_price"].iloc[i])) and not bool(frame["pct_ready"].iloc[i])


def test_bars_whose_visibility_goes_backwards_are_refused(bars_1m):
    shuffled = bars_1m.iloc[[*range(100, 200), *range(0, 100)]]
    with pytest.raises(ValueError, match="time order"):
        kl.key_level_frame(shuffled)


def test_the_per_bar_frame_is_the_nearest_zone_of_the_book_at_each_bar(bars_1m, frame):
    vis = _vis(bars_1m)
    close = mid(bars_1m)["close"].to_numpy()
    for i in (40_000, 55_555, len(bars_1m) - 1):
        z = kl.zones_asof(bars_1m, vis.iloc[i])
        assert z.atr_1d is not None
        above = [x for x in z.zones if x.price > close[i]]
        below = [x for x in z.zones if x.price <= close[i]]
        row = frame.iloc[i]
        assert row["available_utc"] == vis.iloc[i]
        r = min(above, key=lambda x: x.price)
        s = max(below, key=lambda x: x.price)
        assert row["res_price"] == pytest.approx(r.price)
        assert row["sup_price"] == pytest.approx(s.price)
        assert row["res_tfs"] == kl.tf_bits(r.tfs)
        assert row["res_strength_pct"] == pytest.approx(r.strength_pct)
        assert row["res_dist_atr"] == pytest.approx((r.price - close[i]) / z.atr_1d)
        assert row["sup_dist_atr"] == pytest.approx((close[i] - s.price) / z.atr_1d)


def test_previous_day_highs_and_lows_are_levels_once_the_day_has_rolled(bars_1m):
    m = mid(bars_1m)
    t = pd.Timestamp("2025-03-12 23:00", tz="UTC")              # Wednesday, after the 17:00 New York roll
    day = m[(m["ts_utc"] >= pd.Timestamp("2025-03-10 21:00", tz="UTC"))
            & (m["ts_utc"] < pd.Timestamp("2025-03-11 21:00", tz="UTC"))]   # Tuesday's session (EDT: 21:00 UTC)
    nxt = m[(m["ts_utc"] >= pd.Timestamp("2025-03-11 21:00", tz="UTC"))
            & (m["ts_utc"] < pd.Timestamp("2025-03-12 21:00", tz="UTC"))]   # Wednesday's session: the new PDH/PDL
    z = kl.zones_asof(bars_1m, t)
    for px in (nxt["high"].max(), nxt["low"].min()):
        assert any(x.lo <= px <= x.hi and "1d" in x.tfs for x in z.zones)
    z_before = kl.zones_asof(bars_1m, pd.Timestamp("2025-03-12 12:00", tz="UTC"))
    for px in (day["high"].max(), day["low"].min()):
        assert any(x.lo <= px <= x.hi and "1d" in x.tfs for x in z_before.zones)


def test_strength_pct_is_the_percentile_among_zones_of_the_trailing_days(bars_1m):
    z = kl.zones_asof(bars_1m, _vis(bars_1m).iloc[-1])
    pct = [x.strength_pct for x in z.zones]
    assert all(p is not None and 0.0 <= p <= 100.0 for p in pct)
    by_strength = sorted(z.zones, key=lambda x: x.strength)
    got = [x.strength_pct or 0.0 for x in by_strength]
    assert got == sorted(got)


def test_strength_pct_is_unknown_until_twenty_days_of_zones_exist(bars_1m):
    z = kl.zones_asof(bars_1m, pd.Timestamp("2025-02-04 12:00", tz="UTC"))
    assert z.zones and all(x.strength_pct is None for x in z.zones)


def test_nearest_lists_three_zones_above_and_below_nearest_first(bars_1m):
    z = kl.zones_asof(bars_1m, _vis(bars_1m).iloc[-1])
    assert z.price is not None
    above, below = z.nearest(3)
    assert len(above) == 3 and len(below) == 3
    assert [x.price for x in above] == sorted(x.price for x in above)
    assert [x.price for x in below] == sorted((x.price for x in below), reverse=True)
    assert all(x.price > z.price for x in above) and all(x.price <= z.price for x in below)


def test_the_frame_joins_to_decision_bars_by_available_utc(bars_1m, frame):
    h1 = mid(resample_bars(bars_1m, "1h"))
    left = pd.DataFrame({"ts_utc": _vis(h1)})                    # a decision is taken at its bar's close
    joined = asof_join(left, frame[["available_utc", "res_price"]])
    i = len(h1) - 3
    row = frame[frame["available_utc"] <= left["ts_utc"].iloc[i]].iloc[-1]
    assert joined["res_price"].iloc[i] == row["res_price"]


def test_filtering_by_strength_and_timeframe_picks_the_nearest_qualifying_zone(bars_1m):
    strong = kl.key_level_frame(bars_1m, min_strength_pct=60.0, min_tf="4h")
    vis = _vis(bars_1m)
    close = mid(bars_1m)["close"].to_numpy()
    for i in (50_000, len(bars_1m) - 1):
        z = kl.zones_asof(bars_1m, vis.iloc[i])
        ok = [x for x in z.zones if (x.strength_pct or 0) >= 60.0 and kl.TF_RANK[x.top_tf] >= kl.TF_RANK["4h"]]
        above = [x.price for x in ok if x.price > close[i]]
        below = [x.price for x in ok if x.price <= close[i]]
        got_r, got_s = strong["res_price"].iloc[i], strong["sup_price"].iloc[i]
        assert (np.isnan(got_r) and not above) or got_r == pytest.approx(min(above))
        assert (np.isnan(got_s) and not below) or got_s == pytest.approx(max(below))


# ------------------------------------------------------------------------------------------- registered feature
def test_the_key_levels_feature_is_opt_in_and_needs_the_whole_series(bars_1m):
    from goldbot.features import registry
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, OPT_IN_FEATURES
    feats = dict(registry.FEATURES)
    try:
        name = kl.enable()
        assert name == "key_levels" and name in OPT_IN_FEATURES and name not in DEFAULT_FEATURE_NAMES
        spec = registry.FEATURES[name]
        assert registry.NEEDS_STATE in spec.tags
        m = mid(resample_bars(bars_1m, "1h"))
        with pytest.raises(ValueError):
            registry.build_features(m, [name])                  # never on a plain tail
        out = registry.build_features(m, [name], ctx={registry.FULL_HISTORY: True})
        assert [c for c in out.columns if c.startswith("kl_")] == list(kl.FEATURE_COLUMNS)
        assert len(kl.FEATURE_COLUMNS) <= 4
        assert out[list(kl.FEATURE_COLUMNS)].iloc[-200:].notna().all().all()
    finally:
        registry.FEATURES.clear()
        registry.FEATURES.update(feats)
