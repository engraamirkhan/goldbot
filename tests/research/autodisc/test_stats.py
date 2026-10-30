"""The search-level honesty set (ADR 0058 phase 2, design section 3.2 steps 6-10): known cases for each statistic."""
import numpy as np
import pytest
from scipy import stats as sps

from goldbot.research.autodisc import stats as S
from goldbot.research.autodisc.nulls import make_noise_tensor


def noise(n=500, k=40, seed=0):
    return np.random.default_rng(seed).normal(0.0, 1.0, (n, k))


# ---------------------------------------------------------------- stationary bootstrap

def test_stationary_bootstrap_is_deterministic_and_has_the_stated_mean_block_length():
    idx = S.stationary_bootstrap_indices(2000, 50, 5.0, seed=3)
    assert idx.shape == (50, 2000) and idx.min() >= 0 and idx.max() < 2000
    assert np.array_equal(idx, S.stationary_bootstrap_indices(2000, 50, 5.0, seed=3))
    cont = np.mean((idx[:, 1:] - idx[:, :-1]) % 2000 == 1)       # a block continues with probability 1 - 1/b
    assert cont == pytest.approx(1 - 1 / 5.0, abs=0.02)
    w = S.stationary_bootstrap_weights(2000, 50, 5.0, seed=3)
    assert np.allclose(w.sum(axis=1), 1.0)


def test_mean_block_one_is_the_iid_bootstrap():
    idx = S.stationary_bootstrap_indices(1000, 20, 1.0, seed=0)
    assert np.mean((idx[:, 1:] - idx[:, :-1]) % 1000 == 1) < 0.01


# ---------------------------------------------------------------- White RC / Hansen SPA

def test_reality_check_does_not_reject_a_batch_of_pure_noise():
    rc = S.reality_check(noise(seed=1), draws=500, seed=1)
    assert rc.rc_p > S.RC_ALPHA and rc.spa_p > S.RC_ALPHA and rc.n_specs == 40 and rc.n_days == 500


def test_reality_check_size_on_noise_stays_near_the_nominal_level():
    p = np.array([S.reality_check(noise(300, 20, seed=1000 + s), draws=300, seed=s).spa_p for s in range(100)])
    # 10% nominal; the consistent SPA is known to run slightly liberal in small samples (measured 12% at 150 seeds)
    assert (p <= S.RC_ALPHA).mean() <= 0.18 and (p <= 0.05).mean() <= 0.10


def test_reality_check_rejects_when_one_spec_has_a_real_edge():
    d = noise(seed=2)
    d[:, 7] += 0.3                                  # +0.3 sd a day over 500 days: t about 6.7
    rc = S.reality_check(d, draws=500, seed=2)
    assert rc.rc_p < 0.01 and rc.spa_p < 0.01


def test_spa_is_not_diluted_by_clearly_poor_specs_while_the_rc_is():
    rng = np.random.default_rng(4)
    good = rng.normal(0.0, 1.0, (500, 1)) + 0.12
    poor = rng.normal(0.0, 1.0, (500, 300)) - 1.0       # clearly worse than the zero benchmark
    rc = S.reality_check(np.hstack([good, poor]), draws=1000, seed=4)
    assert rc.spa_p < rc.rc_p and rc.spa_p <= S.RC_ALPHA


def test_reality_check_refuses_short_or_non_finite_input():
    with pytest.raises(ValueError):
        S.reality_check(noise(10, 3))
    d = noise(50, 3)
    d[3, 1] = np.nan
    with pytest.raises(ValueError):
        S.reality_check(d)


# ---------------------------------------------------------------- PBO (CSCV)

def test_pbo_is_zero_when_one_spec_dominates_in_every_period():
    d = noise(480, 20, seed=5) * 0.1
    d[:, 3] += 1.0
    out = S.batch_pbo(d, fitness=d.mean(axis=0))
    assert out is not None and out.pbo == 0.0 and out.n_configs == 20


def test_pbo_of_pure_noise_is_near_one_half():
    out = S.batch_pbo(noise(480, 50, seed=6), fitness=np.zeros(50))
    assert out is not None and 0.2 <= out.pbo <= 0.8


def test_pbo_reads_only_the_top_specs_by_fitness_and_needs_two():
    d = noise(240, 80, seed=7)
    assert S.batch_pbo(d, fitness=np.arange(80), top=10).n_configs == 10       # type: ignore[union-attr]
    assert S.batch_pbo(d[:, :1], fitness=[1.0]) is None


# ---------------------------------------------------------------- deflated Sharpe

def test_deflated_sharpe_at_one_trial_is_the_probabilistic_sharpe_ratio():
    x = np.random.default_rng(8).standard_t(5, 400) * 0.5 + 0.1
    sr, g3, g4 = x.mean() / x.std(), sps.skew(x), sps.kurtosis(x, fisher=False)
    want = sps.norm.cdf(sr * np.sqrt(len(x) - 1) / np.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2))
    got = S.batch_dsr(x[:, None], [0], k_search=1)[0]
    assert got == pytest.approx(want, rel=1e-9)


def test_deflated_sharpe_falls_as_the_number_of_trials_grows():
    d = noise(400, 30, seed=9)
    d[:, 0] += 0.15
    vals = [S.batch_dsr(d, [0], k_search=k)[0] for k in (1, 10, 100, 10_000)]
    assert all(a > b for a, b in zip(vals, vals[1:], strict=False))


def test_deflated_sharpe_penalises_negative_skew_and_fat_tails():
    from goldbot.research.metrics import deflated_sharpe
    base = deflated_sharpe(0.1, 50, 500, 0.0, 3.0, 1e-4)             # SR above the expected best of 50 trials
    assert 0.5 < base and deflated_sharpe(0.1, 50, 500, -2.0, 3.0, 1e-4) < base
    assert deflated_sharpe(0.1, 50, 500, 0.0, 12.0, 1e-4) < base


# ---------------------------------------------------------------- circular-shift null

def test_circular_shift_null_rejects_a_signal_aligned_with_the_returns():
    rng = np.random.default_rng(10)
    n = 3000
    r_long = rng.normal(0, 1, n)
    side = np.where(rng.random(n) < 0.2, np.sign(r_long + rng.normal(0, 1.0, n)), 0).astype(np.int8)
    out = S.circular_shift_null(side, r_long, -r_long, n_shifts=199, seed=1)
    assert out.true_mean_r > 0.3 and out.p_value == pytest.approx(1 / 200)


def test_circular_shift_null_does_not_reject_a_signal_independent_of_the_returns():
    ps = []
    for s in range(20):
        rng = np.random.default_rng(100 + s)
        r_long = rng.normal(0, 1, 2000)
        side = np.where(rng.random(2000) < 0.2, rng.choice([-1, 1], 2000), 0).astype(np.int8)
        ps.append(S.circular_shift_null(side, r_long, -r_long, n_shifts=99, seed=s).p_value)
    assert sum(p <= 0.05 for p in ps) <= 3 and np.median(ps) > 0.2


def test_circular_shift_null_never_uses_a_near_identity_shift_and_is_deterministic():
    side = np.zeros(500, dtype=np.int8)
    side[::7] = 1
    r = np.random.default_rng(0).normal(0, 1, 500)
    a = S.circular_shift_null(side, r, -r, n_shifts=50, min_shift=40, seed=2)
    assert a == S.circular_shift_null(side, r, -r, n_shifts=50, min_shift=40, seed=2)
    assert all(40 <= k <= 460 for k in a.shifts)


def test_circular_shift_null_ignores_bars_without_an_outcome():
    side = np.ones(400, dtype=np.int8)
    r = np.random.default_rng(1).normal(0, 1, 400)
    r[::2] = np.nan
    out = S.circular_shift_null(side, r, -r, n_shifts=20, seed=0)
    assert out.n_events == 200 and np.isfinite(out.true_mean_r)


# ---------------------------------------------------------------- plateau

def test_plateau_passes_a_broad_optimum_and_fails_a_knife_edge():
    broad = S.plateau_check(1.0, [0.8, 0.7, 0.9, 0.6])
    knife = S.plateau_check(1.0, [0.1, -0.2, 0.05, -0.1])
    assert broad.passed and broad.ratio == pytest.approx(0.75)
    assert not knife.passed and knife.share_positive == 0.5


def test_plateau_needs_neighbours_a_positive_peak_and_counts_unreadable_ones_as_failures():
    assert not S.plateau_check(1.0, []).passed
    assert not S.plateau_check(-0.5, [0.4, 0.4]).passed
    out = S.plateau_check(1.0, [0.9, None, None])
    assert out.share_positive == pytest.approx(1 / 3) and not out.passed


# ---------------------------------------------------------------- batch gate and excess series

def test_batch_passes_only_when_spa_rejects_and_pbo_is_low():
    good = S.RealityCheck(rc_p=0.02, spa_p=0.03, n_days=100, n_specs=5, draws=100)
    bad = S.RealityCheck(rc_p=0.5, spa_p=0.4, n_days=100, n_specs=5, draws=100)
    low = S.PBOResult(pbo=0.1, n_configs=5, n_combinations=10, logit_median=1.0)
    high = S.PBOResult(pbo=0.6, n_configs=5, n_combinations=10, logit_median=-1.0)
    assert S.batch_passes(good, low) == (True, [])
    assert not S.batch_passes(bad, low)[0] and not S.batch_passes(good, high)[0]
    ok, why = S.batch_passes(None, None)
    assert not ok and len(why) == 2


def test_excess_daily_subtracts_the_drift_baseline_of_the_same_hour_and_side():
    t = make_noise_tensor(2000, seed=3)
    mask = np.zeros(2000, dtype=bool)
    mask[100:1900:9] = True
    ev = t.select(mask, 1, 0)
    day_id = np.unique(t.ts_utc.normalize(), return_inverse=True)[1]
    x = S.excess_daily(t, ev.r(), ev.idx, ev.side_i, 0, day_id, int(day_id.max()) + 1)
    base = t.drift_table(0)[0, t.ts_utc.hour.to_numpy()[ev.idx]]
    assert x.sum() == pytest.approx(float((ev.r() - base).sum()))
