"""scripts/registry_repair.py: a `failed` row the record step wrote for a cell whose artifact did hold valid rows (the
matrix turned 3.0 into 3, so the planned agent id no longer matched) is replaced by the cell's real row, keeping its
trial number and the deflated-Sharpe count K. Dry run by default; never publishes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from goldbot.research import cells
from goldbot.research.preregistration import resolve_variant
from goldbot.research.registry import TrialRegistry, n_trials_effective
from goldbot.research.registry_sync import merge_rows, read_rows, write_rows
from goldbot.specialists import SPECIALISTS

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def repair() -> ModuleType:
    spec = importlib.util.spec_from_file_location("registry_repair", ROOT / "scripts/registry_repair.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # pydantic resolves the records' annotations through the module
    spec.loader.exec_module(mod)
    return mod


def _cell(idx: int, variant: dict[str, Any]) -> dict[str, Any]:
    return {"idx": idx, "specialist": "session_open", "pooled": "", "variant": variant, "rationale": "why"}


def _cell_row(tmp: Path, variant: dict[str, Any]) -> dict[str, Any]:
    """The row the cell wrote: research_pass.py ran the variant the matrix handed it (whole floats as ints)."""
    spec = SPECIALISTS["session_open"](**resolve_variant("session_open", variant))
    return TrialRegistry(tmp / "cellrun.jsonl").record(
        agent_id=spec.agent_id, family=spec.family, config=spec.config, feature_version="f-1", rationale="why",
        results={"screen": {"n": 10}}, status="screened", budget_quarter="2026Q4")


def _earlier(ts: str, us: int) -> str:
    return (datetime.fromisoformat(ts) - timedelta(microseconds=us)).isoformat()


def _artifact(root: Path, idx: int, rows: list[dict[str, Any]]) -> None:
    d = root / f"cell-{idx}"
    d.mkdir(parents=True)
    (d / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "status.json").write_text('{"exit": 0}')


HEAD = "239d8660c159be3c582bdb1e94c23388b6a49b50"


def _research_run(run_id: str) -> dict[str, Any]:
    """`gh run view <id> --json headSha,conclusion,workflowName` of a successful research.yml run."""
    return {"headSha": HEAD, "conclusion": "success", "workflowName": "research"}


def _published(tmp: Path) -> tuple[Path, Path, dict[str, Any]]:
    """A registry with one real trial, then a batch whose cell 0 ran (its row rejected, so a failed row stands in) and
    whose cell 1 left nothing (a genuinely failed trial). Returns (registry, artifacts of the run, the cell's row)."""
    real = _cell_row(tmp, {"confirm_bars": 1})
    ran = _cell_row(tmp, {"stop_atr": 3})
    failed = [cells._failed_row(_cell(0, {"stop_atr": 3.0}), "row 0 is ..., the cell was planned as ..."),
              cells._failed_row(_cell(1, {"stop_atr": 2.0}), "no log and no rows")]
    registry = tmp / "registry.jsonl"
    write_rows(registry, merge_rows([real], failed))
    arts = tmp / "39101594107"
    _artifact(arts, 0, [ran])
    return registry, arts, ran


def test_a_dry_run_reports_the_replacement_and_changes_nothing(
        tmp_path: Path, repair: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    registry, arts, ran = _published(tmp_path)
    before = registry.read_text()
    monkeypatch.setattr(repair, "run_info", _research_run)
    monkeypatch.setattr(repair, "artifact_digests", lambda run_id: {})
    monkeypatch.setattr(sys, "argv", ["registry_repair", str(registry), str(arts)])
    repair.main()
    out = capsys.readouterr().out
    assert registry.read_text() == before
    assert "#2 failed" in out and ran["agent_id"] in out and "dry run" in out
    assert "K 3 -> 3" in out


def test_apply_replaces_the_failed_row_with_the_cells_row_at_the_same_trial_number_and_k(
        tmp_path: Path, repair: ModuleType) -> None:
    registry, arts, ran = _published(tmp_path)
    published = read_rows(registry)
    result = repair.repair(published, [arts], verify=_research_run)
    rows = result.rows
    assert [(r["trial"], r["status"]) for r in rows] == [(1, "screened"), (2, "screened"), (3, "failed")]
    fixed = rows[1]
    assert (fixed["agent_id"], fixed["config_hash"], fixed["results"]) == \
        (ran["agent_id"], ran["config_hash"], ran["results"])
    assert fixed["ts"] == published[1]["ts"]                       # the slot it fills, so the numbering holds
    assert fixed["repair"]["replaces"]["agent_id"] == published[1]["agent_id"]
    assert fixed["repair"]["cell_ts"] == ran["ts"] and fixed["repair"]["source"] == "39101594107/cell-0"
    assert n_trials_effective(rows) == n_trials_effective(published) == 3
    assert [s.trial for s in result.replaced] == [2] and [s.trial for s in result.left] == [3]


def test_the_repair_survives_the_union_with_a_stale_copy_that_still_holds_the_failed_row(
        tmp_path: Path, repair: ModuleType) -> None:
    registry, arts, _ = _published(tmp_path)
    published = read_rows(registry)
    rows = repair.repair(published, [arts], verify=_research_run).rows
    for merged in (merge_rows(rows, published), merge_rows(published, rows)):
        assert [(r["trial"], r["status"]) for r in merged] == [(1, "screened"), (2, "screened"), (3, "failed")]


def test_a_cell_matched_by_two_runs_is_left_failed_with_the_reason(tmp_path: Path, repair: ModuleType) -> None:
    registry, arts, ran = _published(tmp_path)
    other = tmp_path / "39102627317"
    _artifact(other, 0, [{**ran, "ts": _earlier(ran["ts"], 1)}])
    result = repair.repair(read_rows(registry), [arts, other], verify=_research_run)
    assert not result.replaced and "2 runs" in result.left[0].why
    assert result.rows == read_rows(registry)


def test_a_cell_row_written_after_the_failed_row_belongs_to_a_later_run_and_is_not_taken(
        tmp_path: Path, repair: ModuleType) -> None:
    registry, _, _ = _published(tmp_path)
    later = tmp_path / "39999999999"
    _artifact(later, 0, [_cell_row(tmp_path, {"stop_atr": 3})])      # written now, after the failed row
    assert not repair.repair(read_rows(registry), [later], verify=_research_run).replaced


def test_a_cell_that_left_more_rows_than_its_failed_row_is_not_replaced_because_k_would_change(
        tmp_path: Path, repair: ModuleType) -> None:
    registry, arts, ran = _published(tmp_path)
    (arts / "cell-0" / "rows.jsonl").write_text(json.dumps(ran) + "\n" + json.dumps({**ran, "ts": _earlier(ran["ts"], 2)})
                                                + "\n")
    result = repair.repair(read_rows(registry), [arts], verify=_research_run)
    assert not result.replaced and "2 rows" in result.left[0].why


def test_every_repair_row_carries_its_run_the_runs_commit_and_the_digest_of_the_rows_file(
        tmp_path: Path, repair: ModuleType) -> None:
    registry, arts, _ = _published(tmp_path)
    fixed = repair.repair(read_rows(registry), [arts], verify=_research_run).rows[1]["repair"]
    digest = hashlib.sha256((arts / "cell-0" / "rows.jsonl").read_bytes()).hexdigest()
    assert (fixed["run_id"], fixed["head_sha"], fixed["rows_sha256"]) == ("39101594107", HEAD, digest)


@pytest.mark.parametrize("info", [{"conclusion": "failure"}, {"workflowName": "ci"}, {"headSha": ""}],
                         ids=["unsuccessful", "another-workflow", "no-commit"])
def test_a_run_that_is_not_a_successful_research_run_is_refused(
        tmp_path: Path, repair: ModuleType, info: dict[str, Any]) -> None:
    registry, arts, _ = _published(tmp_path)
    with pytest.raises(ValueError, match="39101594107"):
        repair.repair(read_rows(registry), [arts], verify=lambda run_id: {**_research_run(run_id), **info})


def _failed_registry(tmp: Path, real: dict[str, Any], cell: dict[str, Any],
                     cell_rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    """A registry holding one real trial then the failed row the record step wrote for `cell`, and the run's artifact."""
    registry = tmp / "registry.jsonl"
    write_rows(registry, merge_rows([real], [cells._failed_row(cell, "refused")]))
    arts = tmp / "39129559408"
    _artifact(arts, 0, cell_rows)
    return registry, arts


def test_a_failed_discovery_row_is_replaced_by_the_discovery_row_with_its_preregistration_and_k_rises_by_k_eff(
        tmp_path: Path, repair: ModuleType) -> None:
    """Trial #69 (H-40): the cell wrote a pre-registration and a discovery row; the failed row hid the discovery's K_eff."""
    cell = {"idx": 0, "specialist": "box_course", "pooled": "", "variant": {}, "rationale": "H-40"}
    real = _cell_row(tmp_path, {"confirm_bars": 1})
    agent, _, config, _ = cells._planned(cell)
    reg =TrialRegistry(tmp_path / "disc.jsonl")
    pre = reg.preregister(agent_id=agent, family="discovery_box_course", config=config, feature_version="f-1",
                          rationale="H-40", reading_rule="continue if AUC >= 0.53", plan={"n": 4})
    disc = reg.record(agent_id=agent, family="discovery_box_course", config=config, feature_version="f-1",
                      rationale="H-40", results={"discovery": {"survivor_unit": "group", "n_groups_screened": 4}},
                      status="discovery", preregistration=pre)
    registry, arts = _failed_registry(tmp_path, real, cell, [pre, disc])
    published = read_rows(registry)
    result = repair.repair(published, [arts], verify=_research_run)
    assert [(r["trial"], r["status"]) for r in result.rows] == [(1, "screened"), (2, "preregistered"), (2, "discovery")]
    assert result.rows[2]["preregistration"]["trial"] == 2 and result.rows[2]["repair"]["trial"] == 2
    assert [s.trial for s in result.replaced] == [2] and not result.left
    assert (result.k_before, result.k_after) == (2, 2 + 4)
    for merged in (merge_rows(result.rows, published), merge_rows(published, result.rows)):
        assert [r["status"] for r in merged] == ["screened", "preregistered", "discovery"]


def _discovery_case(tmp_path: Path, disc_results: dict[str, Any]) -> tuple[Path, Path]:
    cell = {"idx": 0, "specialist": "box_course", "pooled": "", "variant": {}, "rationale": "H-40"}
    real = _cell_row(tmp_path, {"confirm_bars": 1})
    agent, _, config, _ = cells._planned(cell)
    disc = TrialRegistry(tmp_path / "disc.jsonl").record(
        agent_id=agent, family="discovery_box_course", config=config, feature_version="f-1", rationale="H-40",
        results=disc_results, status="discovery")
    return _failed_registry(tmp_path, real, cell, [disc])


@pytest.mark.parametrize("k_eff", [0, 400], ids=["k_eff-zeroed", "k_eff-inflated"])
def test_a_tampered_k_eff_in_the_artifact_is_refused_because_k_must_match_the_operators_expectation(
        tmp_path: Path, repair: ModuleType, k_eff: int) -> None:
    registry, arts = _discovery_case(tmp_path, {"discovery": {"k_eff": k_eff}})
    published = read_rows(registry)
    with pytest.raises(ValueError, match="not the expected 6"):
        repair.repair(published, [arts], verify=_research_run, expect_k=2 + 4)
    assert repair.repair(published, [arts], verify=_research_run, expect_k=2 + k_eff).k_after == 2 + k_eff


def test_a_repair_that_would_lower_k_is_refused(
        tmp_path: Path, repair: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    registry, arts, _ = _published(tmp_path)
    counts = iter([10, 5])
    monkeypatch.setattr(repair, "n_trials_effective", lambda rows: next(counts))
    with pytest.raises(ValueError, match="lower K"):
        repair.repair(read_rows(registry), [arts], verify=_research_run)


def test_apply_without_expect_k_is_refused_and_writes_nothing(
        tmp_path: Path, repair: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    registry, arts, _ = _published(tmp_path)
    before = registry.read_text()
    monkeypatch.setattr(repair, "run_info", _research_run)
    monkeypatch.setattr(repair, "artifact_digests", lambda run_id: {"cell-0": "sha256:abc"})
    monkeypatch.setattr(sys, "argv", ["registry_repair", str(registry), str(arts), "--apply"])
    with pytest.raises(SystemExit, match="--expect-k"):
        repair.main()
    assert registry.read_text() == before
    monkeypatch.setattr(sys, "argv", ["registry_repair", str(registry), str(arts), "--apply", "--expect-k", "3"])
    repair.main()
    assert read_rows(registry)[1]["repair"]["artifact_digest"] == "sha256:abc"


def test_a_failed_pooled_row_is_replaced_by_the_pooled_cells_row(tmp_path: Path, repair: ModuleType) -> None:
    """Trial #52 (B12): the pooled row's agent id is `pooled_15m-g0-<hash>`, not the `pooled-15m` the plan once named."""
    cell = {"idx": 0, "specialist": "session_open", "pooled": "15m", "variant": {}, "rationale": "B12"}
    real = _cell_row(tmp_path, {"confirm_bars": 1})
    agent, family, config, _ = cells._planned(cell)
    row =TrialRegistry(tmp_path / "pool.jsonl").record(agent_id=agent, family=family, config=config,
                                                        feature_version="f-1", rationale="B12", results={"n": 1},
                                                        status="screened")
    registry, arts = _failed_registry(tmp_path, real, cell, [row])
    result = repair.repair(read_rows(registry), [arts], verify=_research_run)
    assert [(r["trial"], r["status"], r["agent_id"]) for r in result.rows][1] == (2, "screened", agent)
    assert agent.startswith("pooled_15m-g0-") and (result.k_before, result.k_after) == (2, 2)


def test_a_folder_not_named_by_its_run_id_is_refused(tmp_path: Path, repair: ModuleType) -> None:
    registry, arts, _ = _published(tmp_path)
    renamed = arts.rename(tmp_path / "B01")
    with pytest.raises(ValueError, match="run id"):
        repair.repair(read_rows(registry), [renamed], verify=_research_run)
