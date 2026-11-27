"""Run manifests of ADR 0106 (goldbot/research/run_manifest.py): every training run records its data version, settings
hash, seeds, code sha, uv.lock hash and scaler fit end; the model registry stores the manifest id with every artefact
and refuses to serve a fitted model without one; `rebuild` reproduces an artefact byte-identical or fails loudly; the
monthly walk-forward job writes manifests and never promotes; `rollback-model` restores the previous champion."""
from __future__ import annotations

import ast
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research import model_quality as mq
from goldbot.research import monthly_walkforward as mwf
from goldbot.research import run_manifest as rm
from goldbot.research.model import MetaLabelModel
from goldbot.research.model_registry import ModelRegistry, rollback_main
from goldbot.research.rule_model import RuleModel

ROOT = Path(__file__).resolve().parents[1]
T_END = pd.Timestamp("2024-12-31 21:00", tz="UTC")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def git_repo(path: Path) -> Path:
    """A committed checkout with a uv.lock and one tracked source file."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    (path / "uv.lock").write_text("version = 1\n")
    (path / "trainer.py").write_text("X = 1\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def manifest_fields(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"model": "test:gbt", "family": "C", "artifact": b"artifact-bytes",
                            "data_version": "dv-" + "a" * 61, "settings": {"num_leaves": 4, "rounds": 5},
                            "manifest_key": "test:k", "served": "seed0", "train_end": T_END,
                            "scaler_fit_end": T_END, "label_end_max": T_END + pd.Timedelta(hours=4),
                            "results": {"edge": 0.1}}
    base.update(over)
    return base


def _rows(path: Path, n: int = 400, seed: int = 0) -> Path:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 3))
    y = (X[:, 0] + 0.5 * X[:, 1] + rng.normal(0, 0.5, n) > 0).astype(int)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"x1": X[:, 0], "x2": X[:, 1], "x3": X[:, 2], "y": y}).to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------------------------- the manifest
def test_a_recorded_manifest_carries_data_version_settings_hash_seeds_code_sha_lock_hash_and_scaler_fit_end(
        tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    m = rm.record(**manifest_fields(), root=tmp_path / "m", repo=repo)
    assert m.data_version == "dv-" + "a" * 61
    assert m.settings_hash == rm.settings_hash({"rounds": 5, "num_leaves": 4})       # key order does not matter
    assert m.seeds == mq.seed_list("test:k") and m.manifest_key == "test:k" and m.served == "seed0"
    assert m.label_end_max == T_END + pd.Timedelta(hours=4)
    assert m.code_sha == _git(repo, "rev-parse", "HEAD") and m.code_dirty is False
    assert m.uv_lock_hash == rm.file_sha256(repo / "uv.lock")
    assert m.scaler_fit_end == T_END and m.train_end == T_END
    assert m.artifact_sha256 == rm.sha256_bytes(b"artifact-bytes")
    assert (tmp_path / "m" / f"{m.manifest_id}.json").exists()
    assert rm.load(m.manifest_id, root=tmp_path / "m") == m


def test_a_manifest_whose_scaler_saw_rows_after_the_train_end_is_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    with pytest.raises(ValueError, match="scaler_fit_end"):
        rm.record(**manifest_fields(scaler_fit_end=T_END + pd.Timedelta(minutes=1)), root=tmp_path / "m", repo=repo)


def test_a_manifest_without_a_seed_is_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    with pytest.raises(ValueError, match="seed"):
        rm.record(**manifest_fields(seeds=[]), root=tmp_path / "m", repo=repo)


def test_settings_that_are_not_plain_json_are_refused_rather_than_stringified(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    with pytest.raises(rm.ManifestError, match="settings"):
        rm.record(**manifest_fields(settings={"t": pd.Timestamp("2024-01-01")}), root=tmp_path / "m", repo=repo)
    with pytest.raises(rm.ManifestError, match="settings"):
        rm.record(**manifest_fields(settings={"x": float("nan")}), root=tmp_path / "m", repo=repo)


def test_a_non_finite_result_is_stored_as_null(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    m = rm.record(**manifest_fields(results={"auc": float("nan"), "n": 3}), root=tmp_path / "m", repo=repo)
    assert m.results == {"auc": None, "n": 3}


def test_a_manifest_file_edited_after_recording_is_refused_on_load(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    m = rm.record(**manifest_fields(), root=tmp_path / "m", repo=repo)
    f = tmp_path / "m" / f"{m.manifest_id}.json"
    doc = json.loads(f.read_text())
    doc["served"] = "ensemble"
    f.write_text(json.dumps(doc))
    with pytest.raises(rm.ManifestError, match="does not match"):
        rm.load(m.manifest_id, root=tmp_path / "m")


def test_recording_outside_a_git_checkout_or_without_a_lock_file_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    with pytest.raises(rm.ManifestError, match="git"):
        rm.record(**manifest_fields(), root=tmp_path / "m", repo=tmp_path / "plain")
    repo = git_repo(tmp_path / "repo")
    (repo / "uv.lock").unlink()
    with pytest.raises(rm.ManifestError, match="uv.lock"):
        rm.record(**manifest_fields(), root=tmp_path / "m", repo=repo)


def test_a_dirty_tree_is_recorded_as_dirty_and_never_served(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    (repo / "trainer.py").write_text("X = 2\n")
    m = rm.record(**manifest_fields(quality=[mq.seed_stability({s: 0.1 for s in mq.seed_list("test:k")}, rows="r" * 64)]),
                  root=tmp_path / "m", repo=repo)
    assert m.code_dirty is True
    with pytest.raises(mq.ModelRefused, match="dirty"):
        mq.require_servable(m, lookup=lambda i: rm.load(i, root=tmp_path / "m"))


def test_an_untracked_file_does_not_make_the_tree_dirty(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    (repo / "state.json").write_text("{}")
    assert rm.record(**manifest_fields(), root=tmp_path / "m", repo=repo).code_dirty is False


def test_the_hook_api_is_one_call_from_a_trainer() -> None:
    """The trainers of IF-1/IF-2 call `run_manifest.record(...)` (or `fit_and_record`) once per fit; the names they
    rely on are pinned here."""
    assert {"record", "fit_and_record", "load", "rebuild", "BuildSpec", "RunManifest"} <= set(rm.__all__)
    assert {"seed_stability", "param_shift", "beats_baseline", "require_servable"} <= set(mq.__all__)


# ---------------------------------------------------------------------------------------------- rebuild
@pytest.mark.parametrize(("builder", "settings"), [
    ("tests.rebuild_builders:tiny_lightgbm", {"num_leaves": 7, "rounds": 12}),
    ("tests.rebuild_builders:tiny_mlp", {"hidden": 6, "max_iter": 60, "weight_decay": 1e-3}),
])
def test_rebuild_reproduces_the_artifact_byte_identical(tmp_path: Path, builder: str, settings: dict[str, Any]) -> None:
    repo = git_repo(tmp_path / "repo")
    _rows(tmp_path / "data" / "rows.csv")
    art, m = rm.fit_and_record(builder, model="t", family="C", data_version="dv1", data_root=tmp_path / "data",
                               data_files=["rows.csv"], settings=settings, manifest_key="t:mlp", served="seed0",
                               label_end_max=T_END,
                               train_end=T_END, scaler_fit_end=T_END, root=tmp_path / "m", repo=repo)
    assert m.builder == builder and m.data_files == {"rows.csv": rm.file_sha256(tmp_path / "data" / "rows.csv")}
    assert rm.sha256_bytes(art) == m.artifact_sha256
    assert rm.rebuild(m, repo=repo, data_root=tmp_path / "data") == m.artifact_sha256


def _recorded(tmp_path: Path, builder: str = "tests.rebuild_builders:tiny_lightgbm") -> tuple[Path, rm.RunManifest]:
    repo = git_repo(tmp_path / "repo")
    _rows(tmp_path / "data" / "rows.csv")
    _, m = rm.fit_and_record(builder, model="t", family="C", data_version="dv1", data_root=tmp_path / "data",
                             data_files=["rows.csv"], settings={"num_leaves": 7, "rounds": 3}, manifest_key="t:gbt",
                             served="ensemble", label_end_max=T_END,
                             train_end=T_END, scaler_fit_end=T_END, root=tmp_path / "m", repo=repo)
    return repo, m


def test_rebuild_refuses_a_changed_lock(tmp_path: Path) -> None:
    repo, m = _recorded(tmp_path)
    (repo / "uv.lock").write_text("version = 2\n")
    _git(repo, "commit", "-qam", "bump")
    with pytest.raises(rm.RebuildRefused, match="uv.lock"):
        rm.rebuild(m, repo=repo, data_root=tmp_path / "data")


def test_rebuild_refuses_a_dirty_tree(tmp_path: Path) -> None:
    repo, m = _recorded(tmp_path)
    (repo / "trainer.py").write_text("X = 3\n")
    with pytest.raises(rm.RebuildRefused, match="dirty"):
        rm.rebuild(m, repo=repo, data_root=tmp_path / "data")


def test_rebuild_refuses_another_commit_and_names_the_one_to_check_out(tmp_path: Path) -> None:
    repo, m = _recorded(tmp_path)
    (repo / "trainer.py").write_text("X = 4\n")
    _git(repo, "commit", "-qam", "change")
    with pytest.raises(rm.RebuildRefused, match=m.code_sha[:12]):
        rm.rebuild(m, repo=repo, data_root=tmp_path / "data")


def test_rebuild_refuses_a_changed_data_file(tmp_path: Path) -> None:
    repo, m = _recorded(tmp_path)
    _rows(tmp_path / "data" / "rows.csv", seed=1)
    with pytest.raises(rm.RebuildRefused, match="rows.csv"):
        rm.rebuild(m, repo=repo, data_root=tmp_path / "data")


def test_rebuild_refuses_a_manifest_recorded_without_a_builder(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    m = rm.record(**manifest_fields(), root=tmp_path / "m", repo=repo)
    with pytest.raises(rm.RebuildRefused, match="builder"):
        rm.rebuild(m, repo=repo, data_root=tmp_path)


def test_rebuild_reports_a_mismatch_when_the_output_differs(tmp_path: Path) -> None:
    repo, m = _recorded(tmp_path, builder="tests.rebuild_builders:drifting")
    with pytest.raises(rm.RebuildMismatch, match=m.artifact_sha256[:12]):
        rm.rebuild(m, repo=repo, data_root=tmp_path / "data")


def test_the_rebuild_script_exits_0_on_identical_1_on_mismatch_and_2_when_refused(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "research" / "rebuild_model.py"
    repo, m = _recorded(tmp_path)

    def run(manifest_id: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(script), manifest_id, "--manifests", str(tmp_path / "m"),
                               "--repo", str(repo), "--data-root", str(tmp_path / "data")],
                              capture_output=True, text=True, cwd=ROOT,
                              env={**os.environ, "PYTHONPATH": str(ROOT)})    # the test builders live in tests/
    ok = run(m.manifest_id)
    assert ok.returncode == 0, ok.stderr
    assert m.artifact_sha256 in ok.stdout
    _, drift = rm.fit_and_record("tests.rebuild_builders:drifting", model="t", family="C", data_version="dv1",
                                 data_root=tmp_path / "data", data_files=["rows.csv"], settings={}, manifest_key="t:d",
                                 served="seed0", label_end_max=T_END,
                                 train_end=T_END, scaler_fit_end=T_END, root=tmp_path / "m", repo=repo)
    mismatch = run(drift.manifest_id)
    assert mismatch.returncode == 1 and "does not match" in mismatch.stderr
    (repo / "trainer.py").write_text("dirty\n")
    refused = run(m.manifest_id)
    assert refused.returncode == 2 and "dirty" in refused.stderr


def test_the_rebuild_script_accepts_a_registry_version(tmp_path: Path) -> None:
    repo, m = _recorded(tmp_path)
    reg = ModelRegistry(tmp_path / "models")
    model = RuleModel(rule="box_break")
    data = pickle.dumps(model)
    m2 = rm.record(**manifest_fields(artifact=data, builder=m.builder, data_files=m.data_files,
                                     settings=m.settings, manifest_key=m.manifest_key),
                   root=tmp_path / "models" / "manifests", repo=repo)
    e = reg.add_challenger(model, family="box_break", agent_id="bb", backtest={}, manifest=m2)
    assert rm.resolve(e.version, models_dir=tmp_path / "models") == m2
    assert rm.resolve(m2.manifest_id, models_dir=tmp_path / "models") == m2


# ---------------------------------------------------------------------------------------------- the registry
FIT_AT = pd.Timestamp("2026-12-01 10:00", tz="UTC")


def _servable_manifest(tmp_path: Path, repo: Path, data: bytes, **over: Any) -> rm.RunManifest:
    q = [mq.seed_stability({s: 0.1 for s in mq.seed_list("test:k")}, rows="r" * 64)]
    return rm.record(**manifest_fields(artifact=data, quality=q, **over), root=tmp_path / "models" / "manifests",
                     repo=repo)


def test_every_registry_entry_stores_the_manifest_id_and_model_family(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    reg = ModelRegistry(tmp_path / "models")
    model = MetaLabelModel(feature_names=["x"])
    m = _servable_manifest(tmp_path, repo, pickle.dumps(model))
    e = reg.add_challenger(model, family="box_break", agent_id="bb", backtest={}, manifest=m, now=FIT_AT)
    assert e.manifest_id == m.manifest_id and e.model_family == "C"
    again = ModelRegistry(tmp_path / "models").get(e.version)
    assert again.manifest_id == m.manifest_id
    assert set(ModelRegistry(tmp_path / "models").shadow_models()) == {e.version}


def test_add_challenger_refuses_a_manifest_of_another_artefact(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    reg = ModelRegistry(tmp_path / "models")
    m = _servable_manifest(tmp_path, repo, b"some other bytes")
    with pytest.raises(ValueError, match="artefact"):
        reg.add_challenger(MetaLabelModel(feature_names=["x"]), family="box_break", agent_id="bb", backtest={}, manifest=m)
    assert reg.entries == []


def test_add_challenger_copies_a_manifest_recorded_elsewhere_into_the_registry(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    model = MetaLabelModel(feature_names=["x"])
    m = rm.record(**manifest_fields(artifact=pickle.dumps(model)), root=tmp_path / "elsewhere", repo=repo)
    reg = ModelRegistry(tmp_path / "models")
    reg.add_challenger(model, family="box_break", agent_id="bb", backtest={}, manifest=m)
    assert rm.load(m.manifest_id, root=tmp_path / "models" / "manifests") == m


def test_a_fitted_model_trained_after_the_cutoff_without_a_manifest_is_refused_for_serving(tmp_path: Path) -> None:
    reg = ModelRegistry(tmp_path / "models")
    e = reg.add_challenger(MetaLabelModel(feature_names=["x"]), family="box_break", agent_id="bb", backtest={}, now=FIT_AT)
    assert "no run manifest" in (reg.serving_refusal(e) or "")
    with pytest.raises(mq.ModelRefused, match="no run manifest"):
        reg.load_for_serving(e)
    assert reg.shadow_models() == {}
    assert isinstance(reg.load(e), MetaLabelModel)            # the plain checksummed load (backups, recalibration) still works


def test_promote_refuses_a_challenger_the_serving_rule_refuses_and_keeps_the_champion(tmp_path: Path) -> None:
    """PR #494 safety MEDIUM-3(a): promoting a refused challenger would demote a serving champion for a model the
    engine never loads, leaving the agent's open trades with no model to re-score them."""
    reg = ModelRegistry(tmp_path / "models")
    before = mq.MANIFEST_REQUIRED_FROM - pd.Timedelta(days=1)
    old = reg.add_challenger(MetaLabelModel(feature_names=["x"]), family="box_break", agent_id="bb", backtest={},
                             now=before)
    reg.promote(old.version, now=before)
    e = reg.add_challenger(MetaLabelModel(feature_names=["x"]), family="box_break", agent_id="bb", backtest={}, now=FIT_AT)
    with pytest.raises(mq.ModelRefused, match="no run manifest"):
        reg.promote(e.version, now=FIT_AT)
    assert reg.get(e.version).status == "challenger" and reg.champion("bb").version == old.version  # type: ignore[union-attr]
    assert ModelRegistry(tmp_path / "models").champion("bb").version == old.version             # type: ignore[union-attr]
    assert set(reg.champion_models()) == {"bb"}


def test_the_saturday_job_does_not_promote_a_refused_challenger_and_reports_it(tmp_path: Path,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from goldbot.ops import jobs
    from goldbot.research.promotion import PromotionDecision
    reg = ModelRegistry(tmp_path / "models")
    e = reg.add_challenger(MetaLabelModel(feature_names=["x"]), family="box_break", agent_id="bb", backtest={"n": 1},
                           now=FIT_AT)
    monkeypatch.setattr(jobs, "_backtest_stats", lambda entry: object())
    monkeypatch.setattr(jobs, "shadow_stats", lambda ctx, entry: SimpleNamespace(n_trades=60, n_clusters=60))
    monkeypatch.setattr(jobs, "evaluate_promotion",
                        lambda bt, sh, champ: PromotionDecision(promote=True, ready=True, checks=[]))
    out = jobs._decide_challengers(SimpleNamespace(models=reg, state_dir=tmp_path), "bb", FIT_AT)
    assert out[0]["action"] == "promotion_refused" and "no run manifest" in out[0]["refused"]
    assert reg.get(e.version).status == "challenger" and reg.champion("bb") is None


def test_a_legacy_fitted_entry_from_before_the_cutoff_still_serves(tmp_path: Path) -> None:
    reg = ModelRegistry(tmp_path / "models")
    before = mq.MANIFEST_REQUIRED_FROM - pd.Timedelta(days=1)
    e = reg.add_challenger(MetaLabelModel(feature_names=["x"]), family="box_break", agent_id="bb", backtest={}, now=before)
    assert reg.serving_refusal(e) is None and set(reg.shadow_models()) == {e.version}


def test_a_rule_model_is_not_a_fitted_model_and_serves_without_a_manifest(tmp_path: Path) -> None:
    reg = ModelRegistry(tmp_path / "models")
    e = reg.ensure_rule_challenger(family="box_break", agent_id="bb", now=FIT_AT)
    assert e is not None and reg.serving_refusal(e) is None


def test_a_deep_model_without_a_baseline_win_is_refused_by_the_registry(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    reg = ModelRegistry(tmp_path / "models")
    model = MetaLabelModel(feature_names=["x"])
    m = _servable_manifest(tmp_path, repo, pickle.dumps(model), family="A")
    e = reg.add_challenger(model, family="box_break", agent_id="bb", backtest={}, manifest=m, now=FIT_AT)
    assert "baseline" in (reg.serving_refusal(e) or "")
    assert reg.shadow_models() == {}


def test_a_missing_manifest_file_refuses_the_entry(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    reg = ModelRegistry(tmp_path / "models")
    model = MetaLabelModel(feature_names=["x"])
    m = _servable_manifest(tmp_path, repo, pickle.dumps(model))
    e = reg.add_challenger(model, family="box_break", agent_id="bb", backtest={}, manifest=m, now=FIT_AT)
    (tmp_path / "models" / "manifests" / f"{m.manifest_id}.json").unlink()
    assert "manifest" in (reg.serving_refusal(e) or "")


# ---------------------------------------------------------------------------------------------- rollback-model
def _two_champions(tmp_path: Path) -> tuple[ModelRegistry, str, str]:
    reg = ModelRegistry(tmp_path / "models")
    a = reg.ensure_rule_challenger(family="box_break", agent_id="bb", now=FIT_AT)
    assert a is not None
    reg.promote(a.version, now=FIT_AT)
    b = reg.add_challenger(RuleModel(rule="box_break"), family="box_break", agent_id="bb", backtest={},
                           now=FIT_AT + pd.Timedelta(days=7))
    reg.promote(b.version, now=FIT_AT + pd.Timedelta(days=7))
    return reg, a.version, b.version


def test_rollback_model_restores_the_previous_champion_in_one_command(tmp_path: Path) -> None:
    _, first, second = _two_champions(tmp_path)
    code = rollback_main(["bb", "--models", str(tmp_path / "models"), "--reason", "drill"])
    assert code == 0
    reg = ModelRegistry(tmp_path / "models")
    champ = reg.champion("bb")
    assert champ is not None and champ.version == first
    assert reg.get(second).status == "previous" and any("rollback-model: drill" in n for n in reg.get(second).notes)


def test_rollback_model_refuses_an_agent_without_a_previous_champion(tmp_path: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    reg = ModelRegistry(tmp_path / "models")
    a = reg.ensure_rule_challenger(family="box_break", agent_id="bb", now=FIT_AT)
    assert a is not None
    reg.promote(a.version, now=FIT_AT)
    assert rollback_main(["bb", "--models", str(tmp_path / "models")]) == 2
    assert "no previous champion" in capsys.readouterr().err
    assert ModelRegistry(tmp_path / "models").get(a.version).status == "champion"


def test_rollback_model_refuses_a_previous_champion_that_would_not_serve(tmp_path: Path,
                                                                         capsys: pytest.CaptureFixture[str],
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    reg = ModelRegistry(tmp_path / "models")
    old = reg.add_challenger(MetaLabelModel(feature_names=["x"]), family="box_break", agent_id="bb", backtest={}, now=FIT_AT)
    with monkeypatch.context() as mp:     # promoted while it served (its refusal arises later): promote checks it
        mp.setattr(reg, "serving_refusal", lambda e: None)
        reg.promote(old.version, now=FIT_AT)
    new = reg.add_challenger(RuleModel(rule="box_break"), family="box_break", agent_id="bb", backtest={},
                             now=FIT_AT + pd.Timedelta(days=1))
    reg.promote(new.version, now=FIT_AT + pd.Timedelta(days=1))
    assert rollback_main(["bb", "--models", str(tmp_path / "models")]) == 2
    assert "would not serve" in capsys.readouterr().err
    assert ModelRegistry(tmp_path / "models").get(new.version).status == "champion"


def test_rollback_model_is_a_registered_ops_command() -> None:
    from goldbot.ops.commands.registry import find
    assert find("rollback-model") is not None


# ---------------------------------------------------------------------------------------------- monthly walk-forward
def test_the_monthly_walkforward_row_runs_on_the_first_of_the_month_at_a_fixed_utc_time() -> None:
    from goldbot.config import load_settings
    from goldbot.ops.jobs import JOBS
    s = load_settings().scheduler.monthly_walkforward
    assert s.kind == "monthly" and s.day == 1 and s.weekday is None and s.at == "04:30"
    assert "monthly_walkforward" in JOBS


def _trainer_ok(*, window_end: pd.Timestamp, manifests_root: Path, repo: Path) -> list[rm.RunManifest]:
    return [rm.record(**manifest_fields(train_end=window_end - pd.Timedelta(hours=1),
                                        scaler_fit_end=window_end - pd.Timedelta(hours=1),
                                        schedule="monthly_walkforward"), root=manifests_root, repo=repo)]


def test_the_monthly_run_trains_on_data_before_the_month_writes_a_manifest_and_never_promotes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = git_repo(tmp_path / "repo")
    monkeypatch.setattr(ModelRegistry, "promote", lambda *a, **k: pytest.fail("the monthly job promoted"))
    monkeypatch.setattr(ModelRegistry, "restore_previous", lambda *a, **k: pytest.fail("the monthly job restored"))
    seen: dict[str, Any] = {}

    def trainer(*, window_end: pd.Timestamp, manifests_root: Path) -> list[rm.RunManifest]:
        seen["window_end"] = window_end
        return _trainer_ok(window_end=window_end, manifests_root=manifests_root, repo=repo)
    monkeypatch.setitem(sys.modules, "fake_trainers", type(sys)("fake_trainers"))
    sys.modules["fake_trainers"].monthly = trainer      # type: ignore[attr-defined]
    out = mwf.run(pd.Timestamp("2027-01-01 04:30", tz="UTC"), trainers=["fake_trainers:monthly"],
                  manifests_root=tmp_path / "m")
    assert seen["window_end"] == pd.Timestamp("2027-01-01", tz="UTC")
    assert out["trained"] == 1 and out["errors"] == {}
    mid = out["manifests"]["fake_trainers:monthly"][0]
    assert rm.load(mid, root=tmp_path / "m").schedule == "monthly_walkforward"


def test_a_manifest_past_the_window_or_off_schedule_fails_the_monthly_job(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    repo = git_repo(tmp_path / "repo")

    def peeks(*, window_end: pd.Timestamp, manifests_root: Path) -> list[rm.RunManifest]:
        return [rm.record(**manifest_fields(train_end=window_end, scaler_fit_end=window_end,
                                            schedule="monthly_walkforward"), root=manifests_root, repo=repo)]

    def adhoc(*, window_end: pd.Timestamp, manifests_root: Path) -> list[rm.RunManifest]:
        return [rm.record(**manifest_fields(artifact=b"x", train_end=window_end - pd.Timedelta(days=1),
                                            scaler_fit_end=window_end - pd.Timedelta(days=1)),
                          root=manifests_root, repo=repo)]
    mod = type(sys)("fake_trainers2")
    mod.peeks, mod.adhoc = peeks, adhoc              # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_trainers2", mod)
    with pytest.raises(mwf.MonthlyRunFailed) as ei:
        mwf.run(pd.Timestamp("2027-01-01 04:30", tz="UTC"), trainers=["fake_trainers2:peeks", "fake_trainers2:adhoc"],
                manifests_root=tmp_path / "m")
    msg = str(ei.value)
    assert "train_end" in msg and "schedule" in msg


def test_one_failing_trainer_never_skips_another(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = git_repo(tmp_path / "repo")
    calls: list[str] = []

    def boom(*, window_end: pd.Timestamp, manifests_root: Path) -> list[rm.RunManifest]:
        calls.append("boom")
        raise RuntimeError("no data")

    def ok(*, window_end: pd.Timestamp, manifests_root: Path) -> list[rm.RunManifest]:
        calls.append("ok")
        return _trainer_ok(window_end=window_end, manifests_root=manifests_root, repo=repo)
    mod = type(sys)("fake_trainers3")
    mod.boom, mod.ok = boom, ok                      # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_trainers3", mod)
    with pytest.raises(mwf.MonthlyRunFailed, match="no data"):
        mwf.run(pd.Timestamp("2027-02-01 04:30", tz="UTC"), trainers=["fake_trainers3:boom", "fake_trainers3:ok"],
                manifests_root=tmp_path / "m")
    assert calls == ["boom", "ok"]
    assert len(list((tmp_path / "m").glob("*.json"))) == 1


def test_with_no_trainer_configured_the_run_says_so_instead_of_reporting_a_refit() -> None:
    out = mwf.run(pd.Timestamp("2026-12-01 04:30", tz="UTC"), trainers=[], manifests_root=Path("unused"))
    assert out["trained"] == 0 and "no trainer" in out["note"]


def test_the_monthly_module_holds_no_promotion_path() -> None:
    tree = ast.parse((ROOT / "goldbot" / "research" / "monthly_walkforward.py").read_text())
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
            {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"promote", "restore_previous", "ModelRegistry", "set_status", "_set_status"}


def test_a_rollback_can_be_rolled_forward_with_the_same_command(tmp_path: Path) -> None:
    _, first, second = _two_champions(tmp_path)
    assert rollback_main(["bb", "--models", str(tmp_path / "models")]) == 0
    reg = ModelRegistry(tmp_path / "models")
    assert reg.get(second).status == "previous"
    assert rollback_main(["bb", "--models", str(tmp_path / "models")]) == 0
    champ = ModelRegistry(tmp_path / "models").champion("bb")
    assert champ is not None and champ.version == second


def test_the_cusum_restore_still_retires_the_alarmed_champion(tmp_path: Path) -> None:
    reg, first, second = _two_champions(tmp_path)
    reg.restore_previous("bb", "CUSUM alarm")
    assert reg.get(second).status == "retired" and reg.get(first).status == "champion"


def test_the_registry_lock_serialises_load_modify_save(tmp_path: Path) -> None:
    _two_champions(tmp_path)
    with ModelRegistry.locked(tmp_path / "models") as reg:
        assert reg.champion("bb") is not None
        assert (tmp_path / "models" / "registry.lock").exists()


def test_a_recalibrated_entry_still_matches_its_manifest_by_the_fitted_artefact_sha(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    reg = ModelRegistry(tmp_path / "models")
    model = MetaLabelModel(feature_names=["x"])
    m = _servable_manifest(tmp_path, repo, pickle.dumps(model))
    e = reg.add_challenger(model, family="box_break", agent_id="bb", backtest={}, manifest=m, now=FIT_AT)
    fitted = e.sha256
    reg.recalibrate(e.version, MetaLabelModel(feature_names=["x"], params={"recal": 1}), {"n": 50})
    assert e.recalibrations[0]["base_sha256"] == fitted and e.sha256 != fitted
    assert reg.serving_refusal(e) is None
    e.recalibrations[0]["base_sha256"] = "0" * 64
    assert "another artefact" in (reg.serving_refusal(e) or "")


def test_a_manifest_whose_labels_end_past_the_window_fails_the_monthly_job(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    repo = git_repo(tmp_path / "repo")

    def leaks(*, window_end: pd.Timestamp, manifests_root: Path) -> list[rm.RunManifest]:
        before = window_end - pd.Timedelta(hours=1)
        return [rm.record(**manifest_fields(train_end=before, scaler_fit_end=before,
                                            label_end_max=window_end + pd.Timedelta(hours=3),
                                            schedule="monthly_walkforward"), root=manifests_root, repo=repo)]
    mod = type(sys)("fake_trainers4")
    mod.leaks = leaks                                # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_trainers4", mod)
    with pytest.raises(mwf.MonthlyRunFailed, match="label_end_max"):
        mwf.run(pd.Timestamp("2027-01-01 04:30", tz="UTC"), trainers=["fake_trainers4:leaks"],
                manifests_root=tmp_path / "m")


# ---------------------------------------------------------------------------------------------- trading-safety #494
@pytest.mark.parametrize("garbage", ["[1, 2, 3]", "not json", "", '"a string"'])
def test_a_garbage_manifest_refuses_only_its_model_and_every_other_champion_still_loads(tmp_path: Path,
                                                                                       garbage: str) -> None:
    repo = git_repo(tmp_path / "repo")
    reg = ModelRegistry(tmp_path / "models")
    model = MetaLabelModel(feature_names=["x"])
    m = _servable_manifest(tmp_path, repo, pickle.dumps(model))
    bad = reg.add_challenger(model, family="box_break", agent_id="bad", backtest={}, manifest=m, now=FIT_AT)
    reg.promote(bad.version, now=FIT_AT)
    good = reg.ensure_rule_challenger(family="box_break", agent_id="good", now=FIT_AT)
    assert good is not None
    reg.promote(good.version, now=FIT_AT)
    (tmp_path / "models" / "manifests" / f"{m.manifest_id}.json").write_text(garbage)
    assert "bad" in (reg.serving_refusal(reg.get(bad.version)) or "")
    assert set(reg.champion_models()) == {"good"}
    assert set(reg.shadow_models()) == {good.version}
    with pytest.raises(rm.ManifestError):
        rm.load(m.manifest_id, root=tmp_path / "models" / "manifests")


def test_an_artefact_failing_its_checksum_refuses_only_that_champion(tmp_path: Path) -> None:
    reg = ModelRegistry(tmp_path / "models")
    a = reg.ensure_rule_challenger(family="box_break", agent_id="a", now=FIT_AT)
    b = reg.ensure_rule_challenger(family="box_break", agent_id="b", now=FIT_AT)
    assert a is not None and b is not None
    reg.promote(a.version, now=FIT_AT)
    reg.promote(b.version, now=FIT_AT)
    (tmp_path / "models" / a.artefact).write_bytes(b"swapped")
    assert set(reg.champion_models()) == {"b"}


def test_the_engines_model_reload_keeps_running_on_any_error() -> None:
    """engine.py's reload loop: any exception keeps the current models (a narrower catch let one bad file kill the
    engine and its exits)."""
    src = (ROOT / "goldbot" / "ops" / "services" / "engine.py").read_text()
    tree = ast.parse(src)
    handlers = [h for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler)
                and "model reload refused" in ast.get_source_segment(src, h)]   # type: ignore[operator]
    assert handlers and all(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in handlers)


def test_monthly_trainers_are_refused_until_they_run_in_a_bounded_child() -> None:
    from pydantic import ValidationError

    from goldbot.config import ModelQualitySettings
    with pytest.raises(ValidationError, match="niced child"):
        ModelQualitySettings(monthly_trainers=["goldbot.research.sr_v3_train:monthly"])


def test_a_cusum_alarm_never_restores_a_previous_model_that_would_not_serve(tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from goldbot.ops.checks.feeds import check_model_watch
    from goldbot.ops.health import HealthContext
    from goldbot.ops.jobs import model_watch
    from tests.test_shadow import _promote_pair, _shadow_rets, _watch_ctx
    ctx = _watch_ctx(tmp_path)
    t = FIT_AT                                        # both fitted after the cutoff, without a manifest
    with monkeypatch.context() as mp:     # promoted while they served (the refusal arises later): promote checks it
        mp.setattr(ctx.models, "serving_refusal", lambda e: None)
        a, b = _promote_pair(ctx, t)
    promoted = t + pd.Timedelta(days=30)
    _shadow_rets(tmp_path, b, promoted, [-0.006] * 12)
    out = model_watch(ctx, promoted + pd.Timedelta(days=6))
    assert out["agent-x"]["action"] == "restore_refused"
    assert ctx.models.champion("agent-x") is None
    assert ctx.models.get(b).status == "retired" and ctx.models.get(a).status == "previous"
    hc = HealthContext(state_dir=tmp_path, now=promoted + pd.Timedelta(days=6), get_secret=lambda k: None)
    assert check_model_watch(hc).status == "fail"
