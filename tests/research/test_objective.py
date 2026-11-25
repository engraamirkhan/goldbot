"""obj-v1, the one objective of record (ADR 0098 and its 2026-11-27 amendment R-H / R-I; lifecycle plan N1, N5)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.research import objective as O


def _days(n: int, n_days: int) -> np.ndarray:
    return np.array([f"d{i % n_days:04d}" for i in range(n)])


# ------------------------------------------------------------------------------------------------ clip and weights
def test_every_statistic_clips_net_r_at_plus_minus_ten_r_and_counts_the_quarantine_without_dropping_rows():
    r = np.array([-25.0, -10.0, 0.5, 12.0])
    assert O.clip_r(r).tolist() == [-10.0, -10.0, 0.5, 10.0]
    assert O.quarantine_count(r) == 2
    assert O.CLIP_R == 10.0


def test_uniqueness_weight_is_one_for_a_lone_label_and_one_over_p_for_p_identical_overlapping_labels():
    t0 = np.array([0, 0, 0, 100], dtype=np.int64)
    t1 = np.array([10, 10, 10, 110], dtype=np.int64)
    u = O.uniqueness_weights(t0, t1)
    assert np.allclose(u, [1 / 3, 1 / 3, 1 / 3, 1.0])


def test_uniqueness_weight_averages_one_over_concurrency_across_a_partly_overlapping_label():
    # a on [0, 10), b on [5, 15): a is alone half its life, shared half -> (0.5 * 1 + 0.5 * 0.5) = 0.75
    u = O.uniqueness_weights(np.array([0, 5], dtype=np.int64), np.array([10, 15], dtype=np.int64))
    assert np.allclose(u, [0.75, 0.75])


def test_a_zero_length_label_shares_its_instant_with_the_labels_open_then():
    u = O.uniqueness_weights(np.array([0, 5], dtype=np.int64), np.array([10, 5], dtype=np.int64))
    assert np.allclose(u, [1.0, 0.5])


def test_kish_n_eff_is_sum_squared_over_sum_of_squares():
    assert O.kish_n_eff(np.ones(50)) == pytest.approx(50.0)
    assert O.kish_n_eff(np.array([1.0, 0.0, 0.0])) == pytest.approx(1.0)
    assert O.kish_n_eff(np.array([3.0, 1.0])) == pytest.approx(16 / 10)


def test_weights_are_normalised_to_sum_to_kish_n_eff_over_the_overlap_design_effect():
    w = np.array([1.0, 2.0, 3.0, 4.0])
    n = O.normalise_weights(w)
    assert n.sum() == pytest.approx(O.kish_n_eff(w))
    assert O.normalise_weights(w, design_effect=2.0).sum() == pytest.approx(O.kish_n_eff(w) / 2)
    assert np.allclose(O.normalise_weights(w * 10), n)


# ------------------------------------------------------------------------------------------------ offset-form fit
def _synthetic(n: int = 40_000, seed: int = 7) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 2))
    mu = 0.12 * X[:, 0] - 0.01                      # E[gross R | x]; the second column is noise
    gross = mu + rng.normal(scale=1.0, size=n)
    cost = rng.uniform(0.02, 0.10, size=n)          # the ub95 cost of record, known at decision
    return X, gross, cost, mu


def test_offset_form_keeps_iff_predicted_gross_minus_known_cost_is_positive_on_a_known_dgp():
    X, gross, cost, mu = _synthetic()
    m = O.fit_offset(X, gross, np.ones(len(gross)), lam0=1.0)
    keep = m.keep(X, cost)
    truth = mu - cost > 0
    assert (keep == truth).mean() > 0.95
    assert m.coef[0] == pytest.approx(0.12, abs=0.02)
    assert m.objective_id == O.OBJECTIVE_ID


def test_the_fit_target_is_gross_r_clipped_at_ten_r():
    X = np.zeros((4, 1))
    m = O.fit_offset(X, np.array([40.0, 0.0, 0.0, 0.0]), np.ones(4), lam0=1.0)
    assert m.intercept == pytest.approx(10.0 / 4)


def test_multiplying_the_weights_by_ten_gives_an_identical_fit():
    X, gross, _, _ = _synthetic(5_000)
    w = np.random.default_rng(1).uniform(0.2, 1.0, len(gross))
    a = O.fit_offset(X, gross, w, lam0=50.0)
    b = O.fit_offset(X, gross, w * 10, lam0=50.0)
    assert np.allclose(a.coef, b.coef) and a.intercept == pytest.approx(b.intercept)


def test_p_phase_copies_at_uniqueness_one_over_p_with_design_effect_p_reproduce_the_real_only_fit():
    X, gross, _, _ = _synthetic(3_000)
    real = O.fit_offset(X, gross, np.ones(len(gross)), lam0=200.0)
    P = 4
    copies = O.fit_offset(np.repeat(X, P, axis=0), np.repeat(gross, P), np.full(len(gross) * P, 1.0 / P),
                          lam0=200.0, design_effect=P)
    assert np.allclose(real.coef, copies.coef) and real.intercept == pytest.approx(copies.intercept)


def test_an_overlap_design_effect_of_h_scales_the_penalty_by_h():
    X, gross, _, _ = _synthetic(3_000)
    w = np.ones(len(gross))
    assert np.allclose(O.fit_offset(X, gross, w, lam0=100.0, design_effect=3.0).coef,
                       O.fit_offset(X, gross, w, lam0=300.0).coef)


def test_a_model_artifact_without_objective_id_or_with_another_id_does_not_load():
    X, gross, _, _ = _synthetic(500)
    m = O.fit_offset(X, gross, np.ones(len(gross)), lam0=1.0)
    doc = m.model_dump()
    assert O.OffsetModel.model_validate(doc) == m
    doc.pop("objective_id")
    with pytest.raises(ValidationError):
        O.OffsetModel.model_validate(doc)
    with pytest.raises(ValidationError):
        O.OffsetModel.model_validate({**m.model_dump(), "objective_id": "auc-v0"})


# ------------------------------------------------------------------------------------------------ the read
def test_abstain_is_decided_from_kept_counts_first_and_an_abstained_unit_counts_zero():
    n = 1_000
    net = np.full(n, 1.0)
    keep = np.zeros(n, dtype=bool)
    keep[:99] = True                                     # 99 kept < MIN_KEPT
    rd = O.read(net, net + 0.1, keep, _days(n, 200), boot=0)
    assert rd.abstain and rd.n_kept == 99
    assert rd.kept_mean is None
    assert rd.s == pytest.approx(0.0 - 1.0)              # keep counts 0; S = 0 - max(0, mean net)


def test_an_abstained_arm_keeps_nothing_so_it_counts_zero_while_other_arms_keep_their_rows():
    n = 2_000
    arm = np.where(np.arange(n) < 1_000, "A", "B")
    keep = np.zeros(n, dtype=bool)
    keep[:500] = True                                    # A keeps 500 rows over 200 days
    keep[1_000:1_050] = True                             # B keeps 50 < MIN_KEPT
    out = O.abstain_groups(keep, _days(n, 200), arm)
    assert out[:1_000].tolist() == keep[:1_000].tolist() and not out[1_000:].any()


def test_too_few_kept_days_abstains_even_with_enough_kept_rows():
    n = 400
    keep = np.zeros(n, dtype=bool)
    keep[:200] = True
    days = np.where(np.arange(n) < 200, np.array([f"k{i % 29}" for i in range(n)]), "z")
    assert O.read(np.ones(n), np.ones(n), keep, days, boot=0).abstain


def test_s_is_mean_net_times_keep_minus_max_zero_and_mean_net():
    rng = np.random.default_rng(3)
    n = 5_000
    net = rng.normal(-0.04, 1.0, n)
    keep = rng.random(n) < 0.7
    rd = O.read(net, net + 0.07, keep, _days(n, 400), boot=0)
    assert rd.s == pytest.approx(float((net * keep).mean() - max(0.0, net.mean())))
    assert rd.keep_share == pytest.approx(keep.mean())
    assert rd.kept_mean == pytest.approx(net[keep].mean())


def test_s_splits_exactly_into_gross_skill_cost_avoidance_and_the_base_term():
    rng = np.random.default_rng(4)
    n = 4_000
    gross = rng.normal(0.03, 1.0, n)
    cost = rng.uniform(0.02, 0.15, n)
    net = gross - cost
    keep = cost < 0.08                                   # a pure cost-avoidance rule
    rd = O.read(net, gross, keep, _days(n, 300), boot=0)
    d = rd.decomposition
    assert d.gross_skill + d.cost_avoidance + d.base == pytest.approx(rd.s)
    q = keep.mean()
    assert d.gross_skill == pytest.approx(q * (gross[keep].mean() - gross.mean()))
    assert d.cost_avoidance == pytest.approx(q * (cost.mean() - cost[keep].mean()))
    assert d.cost_avoidance > 0 and abs(d.gross_skill) < d.cost_avoidance


def test_the_two_x_cost_stress_doubles_the_cost_leg_with_the_same_keep():
    n = 2_000
    gross = np.full(n, 0.10)
    net = gross - 0.04
    rd = O.read(net, gross, np.ones(n, dtype=bool), _days(n, 100), boot=0)
    assert rd.s_stress == pytest.approx((0.10 - 0.08) - 0.02)


def test_the_day_block_lower_bound_sits_below_s_and_is_reproducible():
    rng = np.random.default_rng(5)
    n = 6_000
    net = rng.normal(0.08, 1.0, n)
    keep = np.ones(n, dtype=bool)
    keep[::3] = False
    a = O.read(net, net, keep, _days(n, 500), boot=500, seed=11)
    b = O.read(net, net, keep, _days(n, 500), boot=500, seed=11)
    assert a.s_lo is not None and a.s_lo < a.s and a.s_lo == b.s_lo


def test_gbp_per_day_uses_unclipped_net_r_on_kept_rows():
    n = 300
    net = np.zeros(n)
    net[0] = 25.0
    keep = np.ones(n, dtype=bool)
    rd = O.read(net, net, keep, _days(n, 50), boot=0, gbp_per_r=2.0)
    assert rd.gbp_per_day == pytest.approx(25.0 * 2.0 / 50)
    assert rd.mean_net == pytest.approx(10.0 / n)       # statistics stay clipped


def test_the_read_reports_kish_n_eff_of_the_uniqueness_weights():
    n = 400
    w = np.full(n, 0.5)
    w[:100] = 1.0
    rd = O.read(np.zeros(n), np.zeros(n), np.ones(n, dtype=bool), _days(n, 40), boot=0, weights=w)
    assert rd.n_eff == pytest.approx(O.kish_n_eff(w))


# ------------------------------------------------------------------------------------------------ the gate
def _read(net_mean: float, gross_add: float = 0.05, n: int = 20_000, keep_share: float = 0.5,
          seed: int = 9) -> O.ObjectiveRead:
    rng = np.random.default_rng(seed)
    net = rng.normal(-0.20, 0.3, n)
    keep = rng.random(n) < keep_share
    net[keep] += net_mean + 0.20
    gross = net + gross_add
    gross[keep] += 0.02
    return O.read(net, gross, keep, _days(n, 1000), boot=400, seed=seed)


def test_the_gate_passes_only_when_every_leg_holds():
    rd = _read(0.20)
    v = O.gate(rd, control_kept_mean=-0.05)
    assert v.passed, v.reasons
    assert set(v.legs) == {"not_abstained", "s_min", "s_lower_bound", "kept_mean", "gross", "control", "cost_stress"}


def test_the_gate_fails_closed_without_a_control_leg():
    v = O.gate(_read(0.20), control_kept_mean=None)
    assert not v.passed and not v.legs["control"]


def test_the_gate_fails_a_negative_kept_mean_and_an_abstained_unit():
    assert not O.gate(_read(-0.01), control_kept_mean=-0.5).passed
    n = 500
    ab = O.read(np.ones(n), np.ones(n), np.zeros(n, dtype=bool), _days(n, 100), boot=0)
    v = O.gate(ab, control_kept_mean=-1.0)
    assert not v.passed and not v.legs["not_abstained"]


def test_paired_delta_of_identical_keeps_is_zero():
    rng = np.random.default_rng(2)
    n = 3_000
    net = rng.normal(0, 1, n)
    keep = rng.random(n) < 0.5
    d, lo = O.paired_delta(net, keep, keep, _days(n, 300), boot=200)
    assert d == 0.0 and lo == 0.0


# ------------------------------------------------------------------------------------------------ import guard
@pytest.mark.parametrize("module", O.GUARDED_MODULES)
def test_no_auc_accuracy_win_rate_or_unweighted_log_loss_reaches_a_fit_threshold_select_or_gate_path(module: str):
    assert O.guard_violations(module) == []


def test_the_import_guard_catches_a_planted_auc_import(tmp_path: Path):
    bad = tmp_path / "bad_gate.py"
    bad.write_text("from sklearn.metrics import roc_auc_score\nfrom goldbot.research import diagnostics\n"
                   "def g(y, p):\n    return roc_auc_score(y, p)\n")
    found = O.guard_violations_in_source(bad.read_text(), str(bad))
    assert any("roc_auc_score" in f for f in found) and any("diagnostics" in f for f in found)


def test_the_guard_also_bans_library_defaults_that_select_on_their_own_metric():
    src = "from sklearn.linear_model import LogisticRegressionCV\nimport lightgbm\nlightgbm.early_stopping(5)\n"
    found = O.guard_violations_in_source(src, "x.py")
    assert any("LogisticRegressionCV" in f for f in found) and any("early_stopping" in f for f in found)


# ------------------------------------------------------------------------------------------------ H-101 acceptance
ZOO = Path(os.environ.get("GOLDBOT_MODEL_ZOO_DIR", str(Path.home() / "tmp/history-replay/model-zoo")))


@pytest.mark.integration
@pytest.mark.skipif(not (ZOO / "learn_oof.parquet").exists(), reason="H-101 Stage L cache is local-only")
def test_h101_stage_l_rows_recompute_to_s_about_minus_0_021_r():
    from goldbot.research import model_zoo as Z
    j = Z.judged(pd.read_parquet(ZOO / "learn_oof.parquet"))
    best = j["keep_B5"].to_numpy(dtype=bool)
    rd = O.read(j["r"].to_numpy(float), j["r_mid"].to_numpy(float), best, j["session_day"].to_numpy(), boot=0)
    assert rd.s == pytest.approx(-0.021, abs=0.003)
    assert not O.gate(rd, control_kept_mean=None).passed
