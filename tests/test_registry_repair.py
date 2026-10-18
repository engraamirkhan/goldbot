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


def test_a_folder_not_named_by_its_run_id_is_refused(tmp_path: Path, repair: ModuleType) -> None:
    registry, arts, _ = _published(tmp_path)
    renamed = arts.rename(tmp_path / "B01")
    with pytest.raises(ValueError, match="run id"):
        repair.repair(read_rows(registry), [renamed], verify=_research_run)
