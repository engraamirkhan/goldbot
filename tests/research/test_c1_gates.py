"""Amendment C1 controls and gates (ADR 0069): the random-SIDE twin (same minute, same stop and exit, a coin for the side)
and the drift-adjusted gross replace the same-side twin as the gate; day-summed t; the equal-weight family composite;
BH-FDR at confirm."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.data.timeutil import epoch_ns
from goldbot.research import c1_gates as G

MIN = 60_000_000_000
DAY = 1440 * MIN


def _regime_days(seed: int, n_days: int = 2000, edge_r: float = 0.2, same_sign: bool = False) -> dict[str, np.ndarray]:
    """A synthetic minute path whose drift sign flips day by day (a persistent side, TSMOM-like) and one trade a day at
    a random minute on the day's side: a planted side edge of `edge_r` with random timing. Also a same-side twin at
    another random minute of the same day (the old leak-free control)."""
    rng = np.random.default_rng(seed)
    per_day, hold, sigma = 390, 60, 0.30
    risk = sigma * np.sqrt(hold)
    mu = edge_r * risk / hold
    sign = np.ones(n_days) if same_sign else rng.choice([-1.0, 1.0], n_days)
    steps = rng.normal(0.0, sigma, (n_days, per_day)) + (sign * mu)[:, None]
    px = 2000.0 + np.cumsum(steps.ravel())
    start = pd.Timestamp("2010-01-04", tz="UTC").value
    k = np.arange(n_days) * per_day + rng.integers(0, per_day - hold, n_days)
    k_twin = np.arange(n_days) * per_day + rng.integers(0, per_day - hold, n_days)
    side = sign.astype(int)
    gross = side * (px[k + hold] - px[k])
    twin = side * (px[k_twin + hold] - px[k_twin])
    entry_ns = start + np.arange(n_days) * (DAY * 8 * 365 // n_days)
    return {"gross_r": gross / risk, "twin_r": twin / risk, "side": side, "px": px[k], "risk": np.full(n_days, risk),
            "hold_ns": np.full(n_days, hold * MIN), "entry_ns": entry_ns.astype(np.int64),
            # the path's own clock (one step a minute): the in-window drift a side-less trader would collect
            "drift": np.array([G.window_drift_per_ns(float(px[0]), float(px[-1]), 0, (len(px) - 1) * MIN)])}


def test_a_planted_side_edge_with_random_timing_passes_the_random_side_control() -> None:
    d = _regime_days(7)
    cost = 0.03
    net, net_opp = d["gross_r"] - cost, -d["gross_r"] - cost        # the coin's other side: same minute, stop, exit
    excess = G.random_side_excess(net, net_opp)
    assert excess.mean() == pytest.approx(0.2, abs=0.05)
    v = G.explore_verdict(net_r=net, net_r_opposite=net_opp, gross_r=d["gross_r"], side=d["side"], entry_px=d["px"],
                          risk_usd=d["risk"], hold_ns=d["hold_ns"], entry_ns=d["entry_ns"],
                          drift_per_ns=float(d["drift"][0]), n_min=100)
    assert v.passed, v.failed
    # the same-side twin (the pre-C1 gate) sees the side's edge in its twin too and rejects the same rule
    old_excess = float(np.mean(net - (d["twin_r"] - cost)))
    assert old_excess < 0.05


def test_no_edge_fails_the_random_side_control() -> None:
    d = _regime_days(11, edge_r=0.0)
    net, net_opp = d["gross_r"] - 0.03, -d["gross_r"] - 0.03
    v = G.explore_verdict(net_r=net, net_r_opposite=net_opp, gross_r=d["gross_r"], side=d["side"], entry_px=d["px"],
                          risk_usd=d["risk"], hold_ns=d["hold_ns"], entry_ns=d["entry_ns"], drift_per_ns=0.0,
                          n_min=100)
    assert not v.passed and "day_t" in v.failed


def test_a_long_only_rule_that_collects_the_drift_fails_the_drift_adjusted_leg() -> None:
    """Every day drifts up and the rule is always long: the random-side twin calls that side skill, the drift-adjusted
    gross (side x in-window drift x hold subtracted) does not."""
    d = _regime_days(3, same_sign=True)
    mu = float(d["drift"][0])
    adj = G.drift_adjusted_gross_r(d["gross_r"], d["side"], d["px"], d["hold_ns"], d["risk"], mu)
    assert mu > 0 and d["gross_r"].mean() > 0.15 and adj.mean() < 0.03
    v = G.explore_verdict(net_r=d["gross_r"], net_r_opposite=-d["gross_r"], gross_r=d["gross_r"], side=d["side"],
                          entry_px=d["px"], risk_usd=d["risk"], hold_ns=d["hold_ns"], entry_ns=d["entry_ns"],
                          drift_per_ns=mu, n_min=100)
    assert G.random_side_excess(d["gross_r"], -d["gross_r"]).mean() > 0.15      # the coin alone would let it through
    assert "drift_adjusted_gross" in v.failed and not v.passed


def test_day_summed_t_counts_days_not_trades() -> None:
    entry = np.array([0, 1, 2, DAY, DAY + 1, 2 * DAY], dtype=np.int64) + pd.Timestamp("2012-01-02", tz="UTC").value
    r = np.array([1.0, 1.0, 1.0, -1.0, -1.0, 2.0])
    days = G.day_sums(r, entry)
    assert days.tolist() == [3.0, -2.0, 2.0]
    assert G.day_t(r, entry) == pytest.approx(np.mean(days) / (np.std(days, ddof=1) / np.sqrt(3)))


def test_years_positive_reads_the_mean_by_entry_year() -> None:
    entry = epoch_ns(pd.DatetimeIndex(["2010-03-01", "2010-04-01", "2011-03-01", "2012-03-01"], tz="UTC"))
    assert G.years_positive(np.array([1.0, -0.5, -1.0, 0.1]), entry) == 2


def test_bh_fdr_matches_the_textbook_example() -> None:
    p = np.array([0.01, 0.04, 0.03, 0.005, 0.20])
    # sorted 0.005, 0.01, 0.03, 0.04, 0.20 against q k / m = 0.02, 0.04, 0.06, 0.08, 0.10: the largest k is 4
    assert G.bh_fdr(p, 0.10).tolist() == [True, True, True, True, False]
    assert G.bh_fdr(np.array([0.5, 0.6]), 0.10).tolist() == [False, False]


def test_the_one_sided_day_bootstrap_p_is_small_for_a_clear_edge_and_large_for_none() -> None:
    rng = np.random.default_rng(1)
    assert G.day_bootstrap_p(rng.normal(0.3, 1.0, 500), np.random.default_rng(2)) < 0.01
    assert G.day_bootstrap_p(rng.normal(-0.1, 1.0, 500), np.random.default_rng(2)) > 0.5


def test_the_composite_weights_members_equally_by_day() -> None:
    t0 = pd.Timestamp("2012-01-02", tz="UTC").value
    a = (np.array([t0, t0 + DAY], dtype=np.int64), np.array([1.0, 1.0]))
    b = (np.array([t0], dtype=np.int64), np.array([-3.0]))      # a member with one trade on day 1 only
    days, series = G.composite_days([a, b])
    assert series.tolist() == [(1.0 - 3.0) / 2, (1.0 + 0.0) / 2]
    assert len(days) == 2
