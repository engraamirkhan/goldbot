"""Model-quality rules of ADR 0106 (goldbot/research/model_quality.py): results stable across at least 5 seeds,
profitable under +-10 % and +-20 % parameter shifts, and every A, B or D model beating the C-family gradient-boosted
baseline out of sample after costs before it can serve."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.config import load_settings
from goldbot.research import model_quality as mq
from goldbot.research import run_manifest as rm
from tests.test_run_manifest import T_END, git_repo, manifest_fields

ROOT = Path(__file__).resolve().parents[1]
RK = "r" * 64                       # a rows_key: the learn-window OOF rows every verdict of a model reads


# ---------------------------------------------------------------------------------------------- seed stability
def test_fewer_than_five_seeds_is_not_deployable() -> None:
    v = mq.seed_stability({1: 0.1, 2: 0.12, 3: 0.09, 4: 0.11}, rows=RK)
    assert not v.passed and v.check == "seed_stability"
    assert any("5 seeds" in r for r in v.reasons)


def test_one_seed_with_the_opposite_sign_of_the_pooled_edge_is_not_deployable() -> None:
    v = mq.seed_stability({1: 0.1, 2: 0.12, 3: 0.09, 4: 0.11, 5: -0.01}, rows=RK)
    assert not v.passed
    assert any("seed 5" in r for r in v.reasons)


def test_five_seeds_all_on_the_side_of_a_positive_pooled_edge_are_stable() -> None:
    v = mq.seed_stability({1: 0.1, 2: 0.12, 3: 0.09, 4: 0.11, 5: 0.02}, rows=RK)
    assert v.passed and v.reasons == []
    assert v.detail["seeds"] == [1, 2, 3, 4, 5] and v.detail["pooled"] == pytest.approx(0.088)


def test_a_stable_but_negative_or_zero_pooled_edge_is_not_deployable() -> None:
    assert not mq.seed_stability({s: -0.05 for s in range(5)}, rows=RK).passed
    assert not mq.seed_stability({s: 0.0 for s in range(5)}, rows=RK).passed


def test_an_explicit_pooled_edge_is_the_sign_every_seed_must_share() -> None:
    edges = {1: 0.1, 2: 0.12, 3: 0.09, 4: 0.11, 5: 0.02}
    v = mq.seed_stability(edges, rows=RK, pooled=-0.01)
    assert not v.passed and v.detail["pooled"] == -0.01


# ---------------------------------------------------------------------------------------------- parameter shift
def test_the_shift_grid_moves_each_parameter_alone_by_ten_and_twenty_percent_both_ways() -> None:
    grid = mq.shift_grid({"stop_k": 2.0, "threshold": 0.5})
    assert len(grid) == 8
    stop = sorted(p.value for p in grid if p.param == "stop_k")
    assert stop == pytest.approx([1.6, 1.8, 2.2, 2.4])
    assert all(p.point["threshold"] == 0.5 for p in grid if p.param == "stop_k")


def test_an_integer_parameter_moves_by_at_least_one_step_and_stays_an_integer() -> None:
    grid = mq.shift_grid({"horizon_bars": 4})
    values = sorted(p.value for p in grid)
    assert values == [3, 3, 5, 5] and all(isinstance(v, int) for v in values)


def test_the_grid_is_fixed_before_any_point_is_read() -> None:
    seen: list[dict[str, Any]] = []

    def evaluate(point: dict[str, Any]) -> float:
        seen.append(dict(point))
        return 0.05

    v = mq.param_shift({"stop_k": 2.0, "horizon_bars": 8}, evaluate, rows=RK)
    assert v.passed
    assert seen[0] == {"stop_k": 2.0, "horizon_bars": 8}        # the declared point first
    assert [p["point"] for p in v.detail["grid"]] == seen[1:]    # then exactly the pre-built grid, in its order
    assert len(seen) == 1 + 8


def test_a_strategy_losing_money_at_any_shifted_point_fails_the_shift_check() -> None:
    def evaluate(point: dict[str, Any]) -> float:
        return -0.01 if point["stop_k"] > 2.3 else 0.04

    v = mq.param_shift({"stop_k": 2.0}, evaluate, rows=RK)
    assert not v.passed
    assert any("stop_k" in r and "+20%" in r for r in v.reasons)


def test_a_strategy_with_no_declared_parameter_cannot_pass_the_shift_check() -> None:
    with pytest.raises(ValueError, match="no declared parameter"):
        mq.param_shift({}, lambda p: 1.0, rows=RK)


# ---------------------------------------------------------------------------------------------- beats baseline
FOLDS = ["f1", "f2", "f3"]


def _oos(n_days: int = 60, per_day: int = 4, lift: float = 0.0, seed: int = 0, horizon: float = 1.0
         ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    idx = pd.Index([f"c{i}" for i in range(n_days * per_day)], name="candidate_id")
    days = pd.Series(np.repeat([f"2024-01-{d:03d}" for d in range(n_days)], per_day), index=idx)
    base = pd.Series(rng.normal(0.0, 0.5, len(idx)), index=idx)
    model = base + lift + pd.Series(rng.normal(0.0, 0.05, len(idx)), index=idx)
    return model, base, days, pd.Series(horizon, index=idx)


def _bb(lift: float = 0.1, *, baseline: str = "c" * 64, train_end: pd.Timestamp = T_END,
        **kw: Any) -> mq.QualityVerdict:
    model, base, days, hz = _oos(lift=lift)
    return mq.beats_baseline(model, base, days, hz, baseline_manifest_id=baseline, train_end=train_end,
                             fold_keys=FOLDS, trial=1, **kw)


def test_a_model_clearly_ahead_of_the_baseline_on_the_same_rows_after_costs_passes() -> None:
    v = _bb(0.1)
    assert v.passed and v.check == "beats_baseline"
    assert v.detail["baseline_manifest_id"] == "c" * 64 and v.detail["d"] > 0 and v.detail["p"] <= 0.05
    assert v.detail["boot"] == 10_000 and v.detail["trial"] == 1 and v.detail["window"] == mq.OOF_WINDOW


def test_a_model_no_better_than_the_baseline_fails() -> None:
    assert not _bb(0.0).passed


def test_the_comparison_refuses_rows_that_are_not_identical() -> None:
    model, base, days, hz = _oos(lift=0.1)
    with pytest.raises(ValueError, match="same out-of-sample rows"):
        mq.beats_baseline(model.iloc[1:], base, days, hz, baseline_manifest_id="c" * 64, train_end=T_END,
                          fold_keys=FOLDS, trial=1)


def test_a_missing_net_value_is_refused_not_read_as_zero() -> None:
    model, base, days, hz = _oos(lift=0.1)
    model.iloc[3] = np.nan
    with pytest.raises(ValueError, match="after-cost"):
        mq.beats_baseline(model, base, days, hz, baseline_manifest_id="c" * 64, train_end=T_END, fold_keys=FOLDS,
                          trial=1)


def test_fewer_than_ten_thousand_bootstrap_draws_are_refused() -> None:
    with pytest.raises(ValueError, match="10000"):
        _bb(0.1, boot=2_000)


def test_the_mean_block_covers_the_longest_label_horizon() -> None:
    assert mq.mean_block_days(pd.Series([0.2, 1.0, 2.0])) == 5             # floor of 5 session days
    assert mq.mean_block_days(pd.Series([1.0, 20.0])) == 30                 # 1d/1w: ceil(1.5 x 20)
    model, base, days, hz = _oos(lift=0.1, horizon=10.0)
    v = mq.beats_baseline(model, base, days, hz, baseline_manifest_id="c" * 64, train_end=T_END, fold_keys=FOLDS,
                          trial=1)
    assert v.detail["mean_block_days"] == 15


def test_a_read_must_be_booked_as_a_registry_trial() -> None:
    with pytest.raises(ValueError, match="trial"):
        mq.beats_baseline(*_oos(lift=0.1), baseline_manifest_id="c" * 64, train_end=T_END, fold_keys=FOLDS,
                          trial=0)


def test_confirm_or_test_old_rows_enter_only_under_a_booked_read_id() -> None:
    with pytest.raises(ValueError, match="read id"):
        _bb(0.1, window="confirm")
    assert _bb(0.1, window="confirm", read_id="R-2027-01-x").detail["read_id"] == "R-2027-01-x"


def test_holm_adjusts_the_a_b_d_reads_of_one_unit_and_cycle() -> None:
    strong, weak = _bb(0.1), _bb(0.02)
    out = mq.holm_adjust({"seq1": strong, "rl": weak}, group="XAUUSD:15m|2027-01")
    assert out["seq1"].detail["holm"]["m"] == len(mq.BASELINE_REQUIRED)
    assert out["seq1"].detail["holm"]["p_holm"] == pytest.approx(min(1.0, 3 * strong.detail["p"]))
    assert out["rl"].detail["holm"]["p_holm"] >= out["seq1"].detail["holm"]["p_holm"]
    assert out["seq1"].passed


def _at_p(p: float) -> mq.QualityVerdict:
    """A positive-d beats_baseline read with its bootstrap p set to `p`."""
    v = _bb(0.1)
    return mq.QualityVerdict(check=v.check, passed=p <= mq.ALPHA, reasons=[], detail={**v.detail, "p": p})


def test_holm_m_is_fixed_at_the_three_baseline_families_and_missing_reads_count_as_p_one() -> None:
    out = mq.holm_adjust({"A": _at_p(0.01), "B": _at_p(0.03)}, group="XAUUSD:15m|2027-01")
    assert {v.detail["holm"]["m"] for v in out.values()} == {3}
    assert out["A"].detail["holm"]["missing"] == 1
    assert out["A"].detail["holm"]["p_holm"] == pytest.approx(0.03) and out["A"].passed        # 3 x 0.01
    assert out["B"].detail["holm"]["p_holm"] == pytest.approx(0.06) and not out["B"].passed    # 2 x 0.03


def test_a_single_read_at_p_003_adjusted_alone_is_refused_for_serving(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts("A", tmp_path / "m", repo, p=0.03)
    assert q[-1].detail["holm"] == {"group": "XAUUSD:15m|2027-01", "m": 3, "missing": 2,
                                    "p_holm": pytest.approx(0.09)}
    m = rm.record(**_fields("A", quality=q), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="beats_baseline failed"):
        mq.require_servable(m, lookup=_lookup(tmp_path / "m"))


def test_a_baseline_win_adjusted_over_fewer_than_three_reads_is_refused_for_serving(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts("A", tmp_path / "m", repo)
    v = q[-1]
    q[-1] = mq.QualityVerdict(check=v.check, passed=True, reasons=[],
                              detail={**v.detail, "holm": {**v.detail["holm"], "m": 1, "missing": 0}})
    m = rm.record(**_fields("A", quality=q), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="Holm m 1 < 3"):
        mq.require_servable(m, lookup=_lookup(tmp_path / "m"))


# ---------------------------------------------------------------------------------------------- serving rule
KEY = "test:unit"


def _fields(family: str, **over: Any) -> dict[str, Any]:
    return manifest_fields(family=family, manifest_key=KEY, unit="XAUUSD:15m", fold_keys=FOLDS, **over)


def _seeds_ok(rows: str = RK) -> mq.QualityVerdict:
    return mq.seed_stability({s: 0.1 for s in mq.seed_list(KEY)}, rows=rows)


def _verdicts(family: str, root: Path, repo: Path, *, baseline_family: str = "C", beat: bool = True,
              base_end: pd.Timestamp = T_END, holm: bool = True, p: float | None = None) -> list[Any]:
    model, b, days, hz = _oos(lift=0.1 if beat else 0.0)
    rows = mq.rows_key(model.index)
    out: list[Any] = [_seeds_ok(rows)]
    if family in mq.BASELINE_REQUIRED:
        base = rm.record(**_fields(baseline_family, artifact=b"gbt", train_end=base_end, scaler_fit_end=base_end),
                         root=root, repo=repo)
        v = mq.beats_baseline(model, b, days, hz, baseline_manifest_id=base.manifest_id, train_end=T_END,
                              fold_keys=FOLDS, trial=7)
        if p is not None:
            v = mq.QualityVerdict(check=v.check, passed=True, reasons=[], detail={**v.detail, "p": p})
        out.append(mq.holm_adjust({"m": v}, group="XAUUSD:15m|2027-01")["m"] if holm else v)
    return out


def _lookup(root: Path) -> Any:
    return lambda i: rm.load(i, root=root)


@pytest.mark.parametrize("family", ["A", "B", "D"])
def test_a_deep_or_rl_model_without_a_baseline_win_is_refused_for_serving(tmp_path: Path, family: str) -> None:
    repo = git_repo(tmp_path / "repo")
    m = rm.record(**_fields(family, quality=[_seeds_ok()]), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="baseline"):
        mq.require_servable(m, lookup=_lookup(tmp_path / "m"))


@pytest.mark.parametrize("family", ["A", "B", "D"])
def test_a_deep_or_rl_model_with_a_holm_passing_win_over_the_units_c_model_serves(tmp_path: Path,
                                                                                    family: str) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts(family, tmp_path / "m", repo)
    m = rm.record(**_fields(family, quality=q), root=tmp_path / "m", repo=repo)
    mq.require_servable(m, lookup=_lookup(tmp_path / "m"))


def test_an_unadjusted_baseline_win_does_not_serve(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts("A", tmp_path / "m", repo, holm=False)
    m = rm.record(**_fields("A", quality=q), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="Holm"):
        mq.require_servable(m, lookup=_lookup(tmp_path / "m"))


def test_a_failed_baseline_comparison_is_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts("A", tmp_path / "m", repo, beat=False)
    m = rm.record(**_fields("A", quality=q), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="beats_baseline failed"):
        mq.require_servable(m, lookup=_lookup(tmp_path / "m"))


def test_a_baseline_that_is_not_a_c_family_model_does_not_count(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts("A", tmp_path / "m", repo, baseline_family="G")
    m = rm.record(**_fields("A", quality=q), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="not a C-family"):
        mq.require_servable(m, lookup=_lookup(tmp_path / "m"))


def test_a_baseline_with_another_train_end_is_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts("A", tmp_path / "m", repo, base_end=T_END - pd.Timedelta(days=30))
    m = rm.record(**_fields("A", quality=q), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="another train_end"):
        mq.require_servable(m, lookup=_lookup(tmp_path / "m"))


def test_a_baseline_of_another_unit_or_other_folds_is_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts("A", tmp_path / "m", repo)
    m = rm.record(**{**_fields("A", quality=q), "unit": "EURUSD:15m", "fold_keys": ["g1"]}, root=tmp_path / "m",
                  repo=repo)
    reasons = mq.servable_reasons(m, lookup=_lookup(tmp_path / "m"))
    assert any("unit" in r for r in reasons) and any("other folds" in r for r in reasons)


def test_a_baseline_manifest_that_cannot_be_found_does_not_count(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts("A", tmp_path / "m", repo)
    m = rm.record(**_fields("A", quality=q), root=tmp_path / "other", repo=repo)
    with pytest.raises(mq.ModelRefused, match="baseline manifest"):
        mq.require_servable(m, lookup=_lookup(tmp_path / "other"))


def test_a_c_family_model_needs_no_baseline_but_still_needs_five_stable_seeds(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    ok = rm.record(**_fields("C", quality=_verdicts("C", tmp_path / "m", repo)), root=tmp_path / "m", repo=repo)
    mq.require_servable(ok, lookup=_lookup(tmp_path / "m"))
    bare = rm.record(**_fields("C", artifact=b"other"), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="seed_stability"):
        mq.require_servable(bare, lookup=_lookup(tmp_path / "m"))


def test_seeds_are_the_deterministic_list_of_the_manifest_key() -> None:
    a = mq.seed_list("XAUUSD:15m|seq1|2027-01")
    assert a == mq.seed_list("XAUUSD:15m|seq1|2027-01") and len(a) == 5 and len(set(a)) == 5
    assert all(0 <= x < 2**31 for x in a) and a != mq.seed_list("XAUUSD:15m|seq1|2027-02")
    assert mq.seed_list("k", 7)[:5] == mq.seed_list("k")


def test_a_manifest_whose_seeds_differ_from_seed_list_is_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    with pytest.raises(ValueError, match="seed_list"):
        rm.record(**_fields("C", seeds=[11, 12, 13, 14, 15]), root=tmp_path / "m", repo=repo)


def test_seed_verdict_must_cover_every_seed_of_the_manifest(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    partial = mq.seed_stability({s: 0.1 for s in mq.seed_list(KEY, 7)[:5]}, rows=RK)
    m = rm.record(**_fields("C", seeds=mq.seed_list(KEY, 7), quality=[partial]), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="seeds"):
        mq.require_servable(m, lookup=_lookup(tmp_path / "m"))


def test_verdicts_read_on_different_rows_are_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    shift = mq.param_shift({"stop_k": 2.0}, lambda p: 0.05, rows="x" * 64)
    m = rm.record(**_fields("C", quality=[_seeds_ok(), shift]), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="different rows"):
        mq.require_servable(m, lookup=_lookup(tmp_path / "m"))


def test_any_recorded_check_that_failed_refuses_the_model(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    shift = mq.param_shift({"stop_k": 2.0}, lambda p: -0.01, rows=RK)
    m = rm.record(**_fields("C", quality=[_seeds_ok(), shift]), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="param_shift failed"):
        mq.require_servable(m, lookup=_lookup(tmp_path / "m"))


# ---------------------------------------------------------------------------------------------- settings
def test_the_model_quality_block_in_settings_yaml_holds_the_studies_parameters() -> None:
    raw = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())["model_quality"]
    s = load_settings().model_quality
    assert tuple(raw["param_shifts"]) == tuple(s.param_shifts) == mq.SHIFTS
    assert raw["baseline_alpha"] == s.baseline_alpha == mq.ALPHA
    assert raw["baseline_boot"] == s.baseline_boot == mq.BOOT
    assert raw["monthly_trainers"] == s.monthly_trainers == []


def test_settings_can_tighten_the_studies_but_never_loosen_them() -> None:
    from pydantic import ValidationError

    from goldbot.config import ModelQualitySettings
    ModelQualitySettings(param_shifts=[0.1, 0.2, 0.3], baseline_alpha=0.01, baseline_boot=20_000)
    for bad in ({"param_shifts": [0.1]}, {"baseline_alpha": 0.1}, {"baseline_boot": 2_000}):
        with pytest.raises(ValidationError):
            ModelQualitySettings(**bad)
