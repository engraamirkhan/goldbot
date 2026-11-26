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
from tests.test_run_manifest import git_repo, manifest_fields

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------------------------- seed stability
def test_fewer_than_five_seeds_is_not_deployable() -> None:
    v = mq.seed_stability({1: 0.1, 2: 0.12, 3: 0.09, 4: 0.11})
    assert not v.passed and v.check == "seed_stability"
    assert any("5 seeds" in r for r in v.reasons)


def test_one_seed_with_the_opposite_sign_of_the_pooled_edge_is_not_deployable() -> None:
    v = mq.seed_stability({1: 0.1, 2: 0.12, 3: 0.09, 4: 0.11, 5: -0.01})
    assert not v.passed
    assert any("seed 5" in r for r in v.reasons)


def test_five_seeds_all_on_the_side_of_a_positive_pooled_edge_are_stable() -> None:
    v = mq.seed_stability({1: 0.1, 2: 0.12, 3: 0.09, 4: 0.11, 5: 0.02})
    assert v.passed and v.reasons == []
    assert v.detail["seeds"] == [1, 2, 3, 4, 5] and v.detail["pooled"] == pytest.approx(0.088)


def test_a_stable_but_negative_or_zero_pooled_edge_is_not_deployable() -> None:
    assert not mq.seed_stability({s: -0.05 for s in range(5)}).passed
    assert not mq.seed_stability({s: 0.0 for s in range(5)}).passed


def test_an_explicit_pooled_edge_is_the_sign_every_seed_must_share() -> None:
    edges = {1: 0.1, 2: 0.12, 3: 0.09, 4: 0.11, 5: 0.02}
    v = mq.seed_stability(edges, pooled=-0.01)
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

    v = mq.param_shift({"stop_k": 2.0, "horizon_bars": 8}, evaluate)
    assert v.passed
    assert seen[0] == {"stop_k": 2.0, "horizon_bars": 8}        # the declared point first
    assert [p["point"] for p in v.detail["grid"]] == seen[1:]    # then exactly the pre-built grid, in its order
    assert len(seen) == 1 + 8


def test_a_strategy_losing_money_at_any_shifted_point_fails_the_shift_check() -> None:
    def evaluate(point: dict[str, Any]) -> float:
        return -0.01 if point["stop_k"] > 2.3 else 0.04

    v = mq.param_shift({"stop_k": 2.0}, evaluate)
    assert not v.passed
    assert any("stop_k" in r and "+20%" in r for r in v.reasons)


def test_a_strategy_with_no_declared_parameter_cannot_pass_the_shift_check() -> None:
    with pytest.raises(ValueError, match="no declared parameter"):
        mq.param_shift({}, lambda p: 1.0)


# ---------------------------------------------------------------------------------------------- beats baseline
def _oos(n_days: int = 60, per_day: int = 4, lift: float = 0.0, seed: int = 0) -> tuple[pd.Series, pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    idx = pd.Index([f"c{i}" for i in range(n_days * per_day)], name="candidate_id")
    days = pd.Series(np.repeat([f"2024-01-{d:03d}" for d in range(n_days)], per_day), index=idx)
    base = pd.Series(rng.normal(0.0, 0.5, len(idx)), index=idx)
    model = base + lift + pd.Series(rng.normal(0.0, 0.05, len(idx)), index=idx)
    return model, base, days


def test_a_model_clearly_ahead_of_the_baseline_on_the_same_rows_after_costs_passes() -> None:
    model, base, days = _oos(lift=0.1)
    v = mq.beats_baseline(model, base, days, baseline_manifest_id="c" * 64, boot=500)
    assert v.passed and v.check == "beats_baseline"
    assert v.detail["baseline_manifest_id"] == "c" * 64 and v.detail["d"] > 0 and v.detail["p"] <= 0.05


def test_a_model_no_better_than_the_baseline_fails() -> None:
    model, base, days = _oos(lift=0.0)
    assert not mq.beats_baseline(model, base, days, baseline_manifest_id="c" * 64, boot=500).passed


def test_the_comparison_refuses_rows_that_are_not_identical() -> None:
    model, base, days = _oos(lift=0.1)
    with pytest.raises(ValueError, match="same out-of-sample rows"):
        mq.beats_baseline(model.iloc[1:], base, days, baseline_manifest_id="c" * 64)


def test_a_missing_net_value_is_refused_not_read_as_zero() -> None:
    model, base, days = _oos(lift=0.1)
    model.iloc[3] = np.nan
    with pytest.raises(ValueError, match="after-cost"):
        mq.beats_baseline(model, base, days, baseline_manifest_id="c" * 64)


# ---------------------------------------------------------------------------------------------- serving rule
def _verdicts(family: str, root: Path, repo: Path, *, baseline_family: str = "C", beat: bool = True) -> list[Any]:
    out: list[Any] = [mq.seed_stability({s: 0.1 for s in range(1, 6)})]
    if family in mq.BASELINE_REQUIRED:
        base = rm.record(**manifest_fields(family=baseline_family, artifact=b"gbt"), root=root, repo=repo)
        model, b, days = _oos(lift=0.1 if beat else 0.0)
        out.append(mq.beats_baseline(model, b, days, baseline_manifest_id=base.manifest_id, boot=500))
    return out


@pytest.mark.parametrize("family", ["A", "B", "D"])
def test_a_deep_or_rl_model_without_a_baseline_win_is_refused_for_serving(tmp_path: Path, family: str) -> None:
    repo = git_repo(tmp_path / "repo")
    m = rm.record(**manifest_fields(family=family, quality=[mq.seed_stability({s: 0.1 for s in range(1, 6)})]),
                  root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="baseline"):
        mq.require_servable(m, lookup=lambda i: rm.load(i, root=tmp_path / "m"))


@pytest.mark.parametrize("family", ["A", "B", "D"])
def test_a_deep_or_rl_model_with_a_passing_paired_win_over_a_c_model_serves(tmp_path: Path, family: str) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts(family, tmp_path / "m", repo)
    m = rm.record(**manifest_fields(family=family, quality=q), root=tmp_path / "m", repo=repo)
    mq.require_servable(m, lookup=lambda i: rm.load(i, root=tmp_path / "m"))


def test_a_failed_baseline_comparison_is_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts("A", tmp_path / "m", repo, beat=False)
    m = rm.record(**manifest_fields(family="A", quality=q), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="beats_baseline failed"):
        mq.require_servable(m, lookup=lambda i: rm.load(i, root=tmp_path / "m"))


def test_a_baseline_that_is_not_a_c_family_model_does_not_count(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts("A", tmp_path / "m", repo, baseline_family="G")
    m = rm.record(**manifest_fields(family="A", quality=q), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="not a C-family"):
        mq.require_servable(m, lookup=lambda i: rm.load(i, root=tmp_path / "m"))


def test_a_baseline_manifest_that_cannot_be_found_does_not_count(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    q = _verdicts("A", tmp_path / "m", repo)
    m = rm.record(**manifest_fields(family="A", quality=q), root=tmp_path / "other", repo=repo)
    with pytest.raises(mq.ModelRefused, match="baseline manifest"):
        mq.require_servable(m, lookup=lambda i: rm.load(i, root=tmp_path / "other"))


def test_a_c_family_model_needs_no_baseline_but_still_needs_five_stable_seeds(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    ok = rm.record(**manifest_fields(family="C", quality=_verdicts("C", tmp_path / "m", repo)), root=tmp_path / "m",
                   repo=repo)
    mq.require_servable(ok, lookup=lambda i: rm.load(i, root=tmp_path / "m"))
    bare = rm.record(**manifest_fields(family="C", artifact=b"other"), root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="seed_stability"):
        mq.require_servable(bare, lookup=lambda i: rm.load(i, root=tmp_path / "m"))


def test_seed_verdict_must_cover_the_manifests_own_seeds(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    m = rm.record(**manifest_fields(family="C", seeds=[11, 12, 13, 14, 15],
                                    quality=[mq.seed_stability({s: 0.1 for s in range(1, 6)})]),
                  root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="seeds"):
        mq.require_servable(m, lookup=lambda i: rm.load(i, root=tmp_path / "m"))


def test_any_recorded_check_that_failed_refuses_the_model(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    shift = mq.param_shift({"stop_k": 2.0}, lambda p: -0.01)
    m = rm.record(**manifest_fields(family="C", quality=[*_verdicts("C", tmp_path / "m", repo), shift]),
                  root=tmp_path / "m", repo=repo)
    with pytest.raises(mq.ModelRefused, match="param_shift failed"):
        mq.require_servable(m, lookup=lambda i: rm.load(i, root=tmp_path / "m"))


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
    ModelQualitySettings(param_shifts=[0.1, 0.2, 0.3], baseline_alpha=0.01, baseline_boot=10_000)
    for bad in ({"param_shifts": [0.1]}, {"baseline_alpha": 0.1}, {"baseline_boot": 100}):
        with pytest.raises(ValidationError):
            ModelQualitySettings(**bad)
