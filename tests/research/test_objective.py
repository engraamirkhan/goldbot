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
    m = O.fit_offset(X, gross, np.ones(len(gross)), lam0=1.0, design_effect=1.0)
    keep = m.keep(X, cost)
    truth = mu - cost > 0
    assert (keep == truth).mean() > 0.95
    assert m.coef[0] == pytest.approx(0.12, abs=0.02)
    assert m.objective_id == O.OBJECTIVE_ID


def test_the_fit_target_is_gross_r_clipped_at_ten_r():
    X = np.zeros((4, 1))
    m = O.fit_offset(X, np.array([40.0, 0.0, 0.0, 0.0]), np.ones(4), lam0=1.0, design_effect=1.0)
    assert m.intercept == pytest.approx(10.0 / 4)


def test_multiplying_the_weights_by_ten_gives_an_identical_fit():
    X, gross, _, _ = _synthetic(5_000)
    w = np.random.default_rng(1).uniform(0.2, 1.0, len(gross))
    a = O.fit_offset(X, gross, w, lam0=50.0, design_effect=1.0)
    b = O.fit_offset(X, gross, w * 10, lam0=50.0, design_effect=1.0)
    assert np.allclose(a.coef, b.coef) and a.intercept == pytest.approx(b.intercept)


def test_p_phase_copies_at_uniqueness_one_over_p_with_design_effect_p_reproduce_the_real_only_fit():
    X, gross, _, _ = _synthetic(3_000)
    real = O.fit_offset(X, gross, np.ones(len(gross)), lam0=200.0, design_effect=1.0)
    P = 4
    copies = O.fit_offset(np.repeat(X, P, axis=0), np.repeat(gross, P), np.full(len(gross) * P, 1.0 / P),
                          lam0=200.0, design_effect=P)
    assert np.allclose(real.coef, copies.coef) and real.intercept == pytest.approx(copies.intercept)


def test_an_overlap_design_effect_of_h_scales_the_penalty_by_h():
    X, gross, _, _ = _synthetic(3_000)
    w = np.ones(len(gross))
    assert np.allclose(O.fit_offset(X, gross, w, lam0=100.0, design_effect=3.0).coef,
                       O.fit_offset(X, gross, w, lam0=300.0, design_effect=1.0).coef)


def test_a_model_artifact_without_objective_id_or_with_another_id_does_not_load():
    X, gross, _, _ = _synthetic(500)
    m = O.fit_offset(X, gross, np.ones(len(gross)), lam0=1.0, design_effect=1.0)
    doc = m.model_dump()
    assert O.OffsetModel.model_validate(doc) == m
    doc.pop("objective_id")
    with pytest.raises(ValidationError):
        O.OffsetModel.model_validate(doc)
    with pytest.raises(ValidationError):
        O.OffsetModel.model_validate({**m.model_dump(), "objective_id": "auc-v0"})


def test_fit_offset_needs_an_explicit_design_effect():
    X, gross, _, _ = _synthetic(200)
    with pytest.raises(TypeError):
        O.fit_offset(X, gross, np.ones(len(gross)), lam0=1.0)  # type: ignore[call-arg]


def test_fit_offset_refuses_a_non_finite_label_or_weight_and_a_zero_penalty():
    X, gross, _, _ = _synthetic(200)
    w = np.ones(len(gross))
    bad = gross.copy()
    bad[3] = np.nan
    with pytest.raises(ValueError, match="finite"):
        O.fit_offset(X, bad, w, lam0=1.0, design_effect=1.0)
    w2 = w.copy()
    w2[0] = np.inf
    with pytest.raises(ValueError, match="finite"):
        O.fit_offset(X, gross, w2, lam0=1.0, design_effect=1.0)
    with pytest.raises(ValueError, match="lam0"):
        O.fit_offset(X, gross, w, lam0=0.0, design_effect=1.0)


def test_an_infinite_input_is_imputed_as_missing_and_the_artifact_round_trips():
    X, gross, _, _ = _synthetic(500)
    X[7, 0] = np.inf
    m = O.fit_offset(X, gross, np.ones(len(gross)), lam0=1.0, design_effect=1.0)
    assert np.isfinite(m.coef).all() and np.isfinite(m.predict_gross(X)).all()
    assert O.OffsetModel.model_validate_json(m.model_dump_json()) == m


def test_a_constant_or_duplicated_column_still_fits_under_the_ridge():
    X, gross, _, _ = _synthetic(500)
    X = np.column_stack([X, X[:, 0], np.ones(len(X))])
    m = O.fit_offset(X, gross, np.ones(len(gross)), lam0=1.0, design_effect=1.0)
    assert np.isfinite(m.coef).all()


def test_the_implied_shrinkage_is_n_eff_over_h_against_lambda():
    assert O.shrinkage(10_000.0, 2.0, 100.0) == pytest.approx(5_000 / 5_100)


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


def test_s_splits_exactly_into_gross_skill_cost_avoidance_path_cost_and_the_base_term():
    rng = np.random.default_rng(4)
    n = 4_000
    gross = rng.normal(0.03, 1.0, n)
    cost_x = rng.uniform(0.02, 0.15, n)                 # known at decision
    stop = gross < -0.9
    realised = cost_x + np.where(stop, 0.05, -0.01)       # the exit path charges more on a stop
    net = gross - realised
    keep = cost_x < 0.08                                 # a pure cost-avoidance rule
    rd = O.read(net, gross, keep, _days(n, 300), cost_x=cost_x, boot=0)
    d = rd.decomposition
    assert d.cost_avoidance is not None and d.path_cost is not None
    assert d.gross_skill + d.cost_avoidance + d.path_cost + d.base == pytest.approx(rd.s)
    assert d.cost_avoidance + d.path_cost == pytest.approx(d.realised_cost_avoidance)
    q = keep.mean()
    assert d.gross_skill == pytest.approx(q * (gross[keep].mean() - gross.mean()))
    assert d.cost_avoidance == pytest.approx(q * (cost_x.mean() - cost_x[keep].mean()))
    assert d.cost_avoidance > 0 and abs(d.gross_skill) < d.cost_avoidance


def test_without_a_decision_time_cost_the_split_is_missing_and_the_gate_fails_closed():
    n = 2_000
    rd = O.read(np.full(n, 0.2), np.full(n, 0.3), np.ones(n, dtype=bool), _days(n, 100), boot=0)
    assert rd.decomposition.cost_avoidance is None and rd.decomposition.path_cost is None
    assert not O.gate(rd).legs["decision_cost"]


def test_the_two_x_cost_stress_doubles_the_cost_leg_with_the_same_keep():
    n = 2_000
    gross = np.full(n, 0.10)
    net = gross - 0.04
    rd = O.read(net, gross, np.ones(n, dtype=bool), _days(n, 100), boot=0)
    assert rd.s_stress == pytest.approx((0.10 - 0.08) - 0.02)


def test_the_stress_takes_the_cost_from_raw_gross_minus_raw_net_and_clips_after():
    n = 200
    gross, net = np.full(n, 12.0), np.full(n, 9.0)        # raw cost 3; the clipped legs would say 10 - 9 = 1
    keep = np.arange(n) < 100
    rd = O.read(net, gross, keep, _days(n, 50), boot=0)
    # stressed net clip(12 - 2 x 3) = 6 on every row: S = 0.5 x 6 - max(0, 6) = -3 (from clipped legs: 4 - 8 = -4)
    assert rd.s_stress == pytest.approx(-3.0)


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


def test_the_bootstrap_orders_days_by_value_not_as_strings():
    rng = np.random.default_rng(6)
    n = 3_000
    net = rng.normal(0.05, 1.0, n)
    keep = rng.random(n) < 0.6
    d_int = np.arange(n) % 300                          # 0..299: as strings "10" < "9"
    d_str = np.array([f"{v:04d}" for v in d_int])
    a = O.read(net, net, keep, d_int, boot=300, seed=3)
    b = O.read(net, net, keep, d_str, boot=300, seed=3)
    assert a.s_lo == b.s_lo


# ------------------------------------------------------------------------------------------------ the gate
def _read(net_mean: float, gross_add: float = 0.05, n: int = 20_000, keep_share: float = 0.5,
          seed: int = 9, control: float | None = -0.10, boot: int = O.BOOT) -> O.ObjectiveRead:
    rng = np.random.default_rng(seed)
    net = rng.normal(-0.20, 0.3, n)
    keep = rng.random(n) < keep_share
    net[keep] += net_mean + 0.20
    gross = net + gross_add
    gross[keep] += 0.02
    days = _days(n, 1000)
    ctrl = None if control is None else rng.normal(control, 0.3, 4_000)
    return O.read(net, gross, keep, days, cost_x=np.full(n, gross_add), boot=boot, seed=seed,
                  control_net=ctrl, control_day=None if ctrl is None else _days(4_000, 1000))


def test_the_gate_passes_its_legs_only_when_every_leg_holds():
    v = O.gate(_read(0.20))
    assert v.legs_passed, v.reasons
    assert set(v.legs) == {"not_abstained", "full_bootstrap", "s_min", "s_lower_bound", "kept_mean", "gross",
                           "control", "cost_stress", "decision_cost"}


def test_the_gate_fails_closed_without_a_control_leg():
    v = O.gate(_read(0.20, control=None))
    assert not v.legs_passed and not v.legs["control"]


def test_the_control_leg_needs_kept_minus_control_of_min_s_with_a_positive_lower_bound():
    rd = _read(0.20, control=0.18)                        # kept beats control by only ~0.02 R
    assert rd.control_diff is not None and rd.control_diff < O.MIN_S_R
    assert not O.gate(rd).legs["control"]
    rd2 = _read(0.20, control=-0.10)
    assert rd2.control_lo is not None and rd2.control_lo > 0 and O.gate(rd2).legs["control"]


def test_a_read_with_fewer_than_boot_draws_fails_the_gate():
    v = O.gate(_read(0.20, boot=400))
    assert not v.legs_passed and v.reasons == ("full_bootstrap",)


def test_the_gate_fails_a_negative_kept_mean_and_an_abstained_unit():
    assert not O.gate(_read(-0.01)).legs_passed
    n = 500
    ab = O.read(np.ones(n), np.ones(n), np.zeros(n, dtype=bool), _days(n, 100), boot=0)
    v = O.gate(ab)
    assert not v.legs_passed and not v.legs["not_abstained"]


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
    src = ("from sklearn.linear_model import LogisticRegressionCV, RidgeCV\nimport lightgbm\nlightgbm.early_stopping(5)\n"
           "from sklearn.model_selection import GridSearchCV, cross_val_score\n"
           "m = lightgbm.train({}, d, early_stopping_rounds=5)\nf(x, scoring='roc_auc')\n")
    found = " ".join(O.guard_violations_in_source(src, "x.py"))
    for name in ("LogisticRegressionCV", "RidgeCV", "early_stopping", "GridSearchCV", "cross_val_score",
                 "early_stopping_rounds=", "scoring="):
        assert name in found, name


def test_the_guard_flags_hist_gradient_boosting_unless_early_stopping_is_off():
    bad = "from sklearn.ensemble import HistGradientBoostingRegressor as H\nm = HistGradientBoostingRegressor()\n"
    ok = "m = HistGradientBoostingRegressor(early_stopping=False)\n"
    assert any("early_stopping=False" in f for f in O.guard_violations_in_source(bad, "x.py"))
    assert O.guard_violations_in_source(ok, "x.py") == []


def test_the_guard_resolves_relative_and_dynamic_imports():
    rel = "from .diagnostics import within_arm_auc\nfrom . import diagnostics\n"
    found = O.guard_violations_in_source(rel, "goldbot/research/x.py", "goldbot.research")
    assert any("goldbot.research.diagnostics" in f for f in found)
    dyn = "import importlib\nm = importlib.import_module('sklearn.metrics')\n"
    assert any("dynamically" in f for f in O.guard_violations_in_source(dyn, "x.py"))


def _py_files() -> list[Path]:
    root = Path(O.__file__).resolve().parents[2]
    return [p for d in ("goldbot", "scripts") for p in sorted((root / d).rglob("*.py"))]


def test_every_user_of_fit_offset_keep_rule_or_gate_is_guarded_or_listed_with_its_reason():
    root = Path(O.__file__).resolve().parents[2]
    guarded = {m.replace(".", "/") + ".py" for m in O.GUARDED_MODULES}
    users = {p.relative_to(root).as_posix() for p in _py_files() if O.objective_users(p.read_text())}
    users.discard("goldbot/research/objective.py")
    assert users, "the scan found no user at all: it is broken"
    assert users - guarded - set(O.UNGUARDED_USERS) == set()
    assert all(len(why) > 30 for why in O.UNGUARDED_USERS.values())
    assert set(O.UNGUARDED_USERS) <= users, "a listed user no longer uses obj-v1: drop it"


def test_the_reverse_dependency_scan_catches_a_planted_user():
    assert O.objective_users("from goldbot.research import objective as Q\nQ.gate(r)\n") == {"gate"}
    assert O.objective_users("from goldbot.research.objective import fit_offset\n") == {"fit_offset"}
    assert O.objective_users("from goldbot.research import objective\nobjective.read(1)\n") == set()


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
    assert not O.gate(rd).legs_passed
