"""The descriptive diagnostic battery (lifecycle plan N10, N6, A26): never a selector, never a gate."""
from __future__ import annotations

import numpy as np
import pytest

from goldbot.research import diagnostics as D
from goldbot.research import objective as O


def test_weighted_calibration_slope_is_one_for_a_calibrated_prediction_and_ignores_weight_scale():
    rng = np.random.default_rng(1)
    pred = rng.normal(0, 0.2, 20_000)
    y = pred + rng.normal(0, 1.0, len(pred))
    w = rng.uniform(0.1, 1.0, len(pred))
    c = D.weighted_calibration(pred, y, w)
    assert c.slope == pytest.approx(1.0, abs=0.08) and c.intercept == pytest.approx(0.0, abs=0.02)
    c10 = D.weighted_calibration(pred, y, w * 10)
    assert (c10.slope, c10.intercept) == pytest.approx((c.slope, c.intercept))


def test_brier_skill_is_zero_for_the_base_rate_and_positive_for_skill():
    y = np.array([0, 1, 1, 0] * 50, dtype=float)
    w = np.ones(len(y))
    assert D.brier_skill(np.full(len(y), 0.5), y, w) == pytest.approx(0.0)
    assert D.brier_skill(np.where(y > 0, 0.8, 0.2), y, w) > 0


def test_within_arm_auc_ignores_between_arm_shifts_and_is_diagnostic_only():
    # inside each arm the score is pure noise; between arms it lines up with the base rate -> pooled AUC > 0.5
    rng = np.random.default_rng(2)
    arm = np.repeat(["A", "B"], 5_000)
    y = np.where(arm == "A", rng.random(10_000) < 0.7, rng.random(10_000) < 0.3).astype(float)
    score = np.where(arm == "A", 1.0, 0.0) + rng.normal(0, 0.01, 10_000)
    assert D.within_arm_auc(score, y, arm) == pytest.approx(0.5, abs=0.03)
    assert "goldbot.research.diagnostics" not in O.GUARDED_MODULES


def test_seed_spread_reads_inconclusive_when_the_seed_sd_reaches_half_the_minimum_edge():
    assert not D.seed_spread([0.01, 0.011, 0.012, 0.009, 0.010], min_edge=0.05).inconclusive
    assert D.seed_spread([0.0, 0.05, -0.04, 0.03, -0.02], min_edge=0.05).inconclusive


def test_learning_curve_evaluates_each_size_up_to_the_rows_available():
    seen: list[int] = []

    def s_at(n: int) -> float:
        seen.append(n)
        return 1.0 / n

    curve = D.learning_curve(s_at, sizes=(1_000, 10_000, 100_000), n_available=20_000)
    assert [p.n for p in curve] == [1_000, 10_000, 20_000] and seen == [1_000, 10_000, 20_000]


def _cells(n: int, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 6))
    gross = rng.normal(0.0, 1.0, n)
    cost = rng.uniform(0.05, 0.12, n)
    days = np.array([f"d{i % 600:04d}" for i in range(n)])
    return X, gross, cost, days, np.ones(n)


def test_shuffled_labels_keep_at_most_five_percent_beyond_the_intercept_rule_with_a_sound_penalty():
    X, gross, cost, days, w = _cells(30_000, 3)
    cells = np.where(X[:, 0] > 0, "hi", "lo")
    chk = D.shuffled_keep_check(X, gross, cost, w, days, cells, lam0=50.0, design_effect=1.0, seed=4)
    assert chk.passed, chk.false_keep_share


def test_the_shuffled_check_flags_a_penalty_that_is_too_loose_on_noise_features():
    X, gross, cost, days, w = _cells(400, 5)
    X = np.random.default_rng(6).normal(size=(400, 150))
    gross = gross * 4 - 1.0                              # the intercept rule keeps nothing
    chk = D.shuffled_keep_check(X, gross, cost, w, days, np.full(400, "all"), lam0=1e-6, design_effect=1.0,
                                 seed=7, min_kept=0)
    assert not chk.passed


def test_the_oracle_canary_recovers_a_planted_ten_basis_point_signal():
    X, gross, cost, days, w = _cells(40_000, 8)
    can = D.oracle_canary(X, gross, cost, w, days, delta=0.10, lam0=10.0, design_effect=1.0, seed=9)
    assert can.s_oracle > can.s_null and can.recovered


def test_score_deciles_tabulate_win_rate_and_mean_net_r_per_decile():
    rng = np.random.default_rng(10)
    s = rng.random(1_000)
    net = s - 0.5
    t = D.score_deciles(s, net)
    assert len(t) == 10 and list(t.columns) == ["n", "win_rate", "mean_net_r"]
    assert t["mean_net_r"].is_monotonic_increasing


def test_the_false_keep_metric_fails_noise_keeps_that_a_net_excess_would_cancel():
    """Quant ruling 3 on PR #461: lam0 = 1e-6, 150 noise columns, an intercept rule keeping 30-70 %: the fit keeps
    about as many rows as the intercept rule, but different ones; the net excess is ~0, the false-keep share is not."""
    rng = np.random.default_rng(11)
    n = 400
    X = rng.normal(size=(n, 150))
    gross = rng.normal(0.08, 1.0, n)
    cost = rng.uniform(0.02, 0.14, n)                    # the intercept (~0.08) beats about half the costs
    days = np.array([f"d{i % 120:04d}" for i in range(n)])
    assert 0.3 < (gross.mean() - cost > 0).mean() < 0.7
    chk = D.shuffled_keep_check(X, gross, cost, np.ones(n), days, np.full(n, "all"), lam0=1e-6, design_effect=1.0,
                                seed=12, min_kept=0)
    assert not chk.passed and chk.false_keep_share["all"] > 0.05
    assert chk.xor_share["all"] >= chk.false_keep_share["all"]
