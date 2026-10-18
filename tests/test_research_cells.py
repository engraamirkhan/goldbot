"""Parallel research trials, serialized registry record (goldbot.research.cells; .github/workflows/research.yml).

Every trial is one matrix cell that runs on a read-only snapshot of the registry and writes only its own rows; the
`record` job merges them into the fresh registry under the lock and restates every deflated Sharpe on the final trial
count. Nothing may vanish: a cell that crashed or timed out still leaves a `failed` row (a look at the data is a trial)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.research import cells
from goldbot.research.gates import DSR_BAR, research_gates
from goldbot.research.metrics import summarize
from goldbot.research.registry import TrialRegistry, n_trials_effective
from goldbot.research.registry_sync import merge_rows, read_rows

ROOT = Path(__file__).resolve().parents[1]


def _env(**kw: str) -> dict[str, str]:
    base = {"SPECIALIST": "session_open", "FROM_YEAR": "2010", "TO_YEAR": "2026", "RATIONALE": "why", "VARIANTS": "[{}]",
            "SCORE_HOLDOUT": "false", "SKIP_SCREEN": "false", "POOLED": "", "MACRO": "false", "DISCOVER": "false",
            "FAMILIES": "", "DISCOVER_CONFIG": "{}", "RULE_REF": "", "BATCH": ""}
    return {**base, **kw}


# ------------------------------------------------------------------------------------------------------ the plan
def test_the_single_trial_inputs_still_plan_one_cell_per_variant() -> None:
    plan = cells.plan(_env(VARIANTS='[{}, {"band_z": 1.5}]'))
    assert [c["idx"] for c in plan] == [0, 1]
    assert [c["variant"] for c in plan] == [{}, {"band_z": 1.5}]
    assert {c["specialist"] for c in plan} == {"session_open"} and plan[0]["from_year"] == 2010


def test_a_pooled_run_is_one_cell_that_ignores_specialist_and_variants() -> None:
    plan = cells.plan(_env(POOLED="1h", VARIANTS='[{}, {"band_z": 1.5}]'))
    assert len(plan) == 1 and plan[0]["pooled"] == "1h"


def test_a_batch_plans_every_object_over_the_top_level_defaults() -> None:
    batch = json.dumps([{"specialist": "tsmom", "variants": [{"band_z": 1}, {}], "timeframe": "1h"},
                        {"pooled": "15m"}, {"specialist": "session_open", "to_year": 2024, "rationale": "r2"}])
    plan = cells.plan(_env(BATCH=batch, RATIONALE="top"))
    assert [(c["specialist"], c["pooled"], c["variant"]) for c in plan] == [
        ("tsmom", "", {"band_z": 1, "timeframe": "1h"}), ("tsmom", "", {"timeframe": "1h"}),
        ("session_open", "15m", {}), ("session_open", "", {})]
    assert [c["idx"] for c in plan] == [0, 1, 2, 3]
    assert plan[3]["to_year"] == 2024 and plan[3]["rationale"] == "r2" and plan[0]["rationale"] == "top"


@pytest.mark.parametrize("kw", [
    {"BATCH": '[{"specialist": "x; rm -rf /"}]'}, {"BATCH": '[{"nope": 1}]'},
    {"BATCH": '[{"variants": ["slow"], "timeframe": "1h"}]'}, {"BATCH": '{"specialist": "tsmom"}'},
    {"BATCH": "not json"}, {"FAMILIES": "--registry=/tmp/x"}, {"POOLED": "--x"}, {"FROM_YEAR": "abc"},
    {"DISCOVER_CONFIG": "[1]"}, {"VARIANTS": "[]"}, {"VARIANTS": "[1]"},
    {"BATCH": json.dumps([{"specialist": "tsmom", "variants": [{}] * (cells.MAX_CELLS + 1)}])},
])
def test_a_malformed_plan_is_refused_before_any_cell_runs(kw: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        cells.plan(_env(**kw))


def test_cell_arguments_pass_every_value_in_the_equals_form_so_a_value_can_never_be_an_option() -> None:
    cell = cells.plan(_env(DISCOVER="true", FAMILIES="family", DISCOVER_CONFIG='{"k": 1}', RULE_REF="p.md#r",
                           SCORE_HOLDOUT="true", SKIP_SCREEN="true", MACRO="true", RATIONALE="--registry=/tmp/x"))[0]
    argv = cells.argv(cell)
    assert "--rationale=--registry=/tmp/x" in argv and "--registry=/tmp/x" not in argv
    assert {"--discover", "--families=family", "--discover-config={\"k\": 1}", "--rule-ref=p.md#r", "--score-holdout",
            "--skip-screen", "--macro=macro", "--specialist=session_open", "--variants=[{}]"} <= set(argv)
    for a in argv:
        assert a.startswith("--") and ("=" in a or a in {"--discover", "--score-holdout", "--skip-screen", "--lot-fit"})


# ------------------------------------------------------------------------------------------- the cell's own rows
def _row(reg: TrialRegistry, family: str = "session_open", **results: Any) -> dict[str, Any]:
    return reg.record(agent_id=f"a-{family}", family=family, config={"band_z": len(reg._rows())}, feature_version="fv",
                      rationale="r", results=results)


def test_a_cell_hands_back_only_the_rows_it_added_to_its_snapshot_copy(tmp_path: Path) -> None:
    snap = TrialRegistry(tmp_path / "snap.jsonl")
    _row(snap)
    work = tmp_path / "work.jsonl"
    work.write_text(snap.path.read_text())
    _row(TrialRegistry(work), family="tsmom")
    new = cells.new_rows(snap.path, work)
    assert [r["family"] for r in new] == ["tsmom"] and len(read_rows(snap.path)) == 1


# --------------------------------------------------------------------------------------------- DSR restatement
def _evaluated_results(n: int = 400, k_run: int = 5, seed: int = 3) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.004, 0.01, n)
    ts = pd.date_range("2021-01-01", periods=n, freq="1D", tz="UTC")
    taken = pd.DataFrame({"ts_utc": ts, "ret": ret})
    mf = summarize(taken, 100.0, k_run)
    gates = research_gates(2000, [100, 100], taken, mf)
    return {"model_filtered": mf, "all_candidates": summarize(taken, 100.0, k_run), "gates": gates,
            "swap": {"long_usd_per_lot": 1.0}, "cost_source": "t"}


def test_restating_on_a_larger_trial_count_lowers_the_dsr_and_redoes_the_gate_verdict() -> None:
    results = _evaluated_results()
    row: dict[str, Any] = {"status": "evaluated", "results": results}
    before = results["model_filtered"]["dsr"]
    out = cells.restate_dsr(row, 5000)
    after = out["results"]["model_filtered"]["dsr"]
    assert after < before and out["results"]["dsr_n_trials"] == 5000
    assert out["results"]["dsr_at_run"] == before
    dsr_check = next(c for c in out["results"]["gates"]["checks"] if c["name"] == "dsr")
    assert dsr_check["passed"] is (after >= DSR_BAR)
    assert out["results"]["gates"]["passed"] is all(c["passed"] for c in out["results"]["gates"]["checks"])
    assert row["results"]["model_filtered"]["dsr"] == before                # the input row is not mutated


def test_restating_at_the_run_count_changes_nothing_a_reader_can_see() -> None:
    results = _evaluated_results(k_run=7)
    out = cells.restate_dsr({"status": "evaluated", "results": results}, 7)
    assert out["results"]["model_filtered"]["dsr"] == pytest.approx(results["model_filtered"]["dsr"])
    assert out["results"]["gates"]["passed"] == results["gates"]["passed"]


def test_a_row_without_stored_inputs_is_left_alone_and_a_failed_row_is_never_restated() -> None:
    bare = {"status": "evaluated", "results": {"model_filtered": {"n": 5, "dsr": None, "dsr_inputs": None}}}
    assert cells.restate_dsr(bare, 100) == bare
    failed = {"status": "failed", "results": {"error": "x"}}
    assert cells.restate_dsr(failed, 100) == failed


# ----------------------------------------------------------------------------------------------- the record step
def _cell_dir(root: Path, idx: int, rows: list[dict[str, Any]] | None, *, exit_code: int = 0, log: str = "ok",
              report: str = "# report\n") -> None:
    d = root / f"cell-{idx}"
    d.mkdir(parents=True)
    if rows is not None:
        (d / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "status.json").write_text(json.dumps({"exit": exit_code}))
    (d / "research.log").write_text(log)
    if report:
        (d / "report.md").write_text(report)


def _one_row(tmp: Path, name: str, family: str = "session_open", config_n: int = 0, k_run: int = 3) -> dict[str, Any]:
    reg = TrialRegistry(tmp / f"{name}.jsonl")
    return reg.record(agent_id=f"a-{family}", family=family, config={"band_z": config_n}, feature_version="fv",
                      rationale="r", results=_evaluated_results(k_run=k_run, seed=config_n + 1), status="evaluated")


def test_record_merges_every_cell_into_the_fresh_registry_and_counts_each_trial_once(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "registry.jsonl")
    _row(registry)
    plan = cells.plan(_env(VARIANTS='[{"band_z": 1}, {"band_z": 2}]'))
    arts = tmp_path / "arts"
    _cell_dir(arts, 0, [_one_row(tmp_path, "a", config_n=1)])
    _cell_dir(arts, 1, [_one_row(tmp_path, "b", config_n=2)])
    out = cells.record(plan, arts, registry.path)
    rows = read_rows(registry.path)
    assert len(rows) == 3 and [r["trial"] for r in rows] == [1, 2, 3] and out.recorded == 2
    cells.record(plan, arts, registry.path)                                  # a re-run of the job adds nothing
    assert len(read_rows(registry.path)) == 3


def test_record_restates_each_cell_dsr_on_the_final_count_not_the_count_it_saw(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "registry.jsonl")
    for i in range(20):
        _row(registry, family=f"f{i}")
    plan = cells.plan(_env(VARIANTS='[{"band_z": 1}, {"band_z": 2}, {"band_z": 3}]'))
    arts = tmp_path / "arts"
    for i in range(3):                       # all three ran on the snapshot of 20, so each saw K = 21
        _cell_dir(arts, i, [_one_row(tmp_path, f"c{i}", config_n=i + 1, k_run=21)])
    out = cells.record(plan, arts, registry.path)
    final = read_rows(registry.path)
    assert len(final) == 23 and out.n_trials_effective == n_trials_effective(final) == 23
    for r in final[20:]:
        assert r["results"]["dsr_n_trials"] == 23
        assert r["results"]["model_filtered"]["dsr"] < r["results"]["dsr_at_run"]
    assert "provisional" in out.reports[0]["body"].lower() and "K = 23" in out.reports[0]["body"]


def test_a_cell_that_crashed_still_records_a_failed_trial_so_nothing_vanishes(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "registry.jsonl")
    plan = cells.plan(_env(VARIANTS='[{}, {"band_z": 2}]'))
    arts = tmp_path / "arts"
    _cell_dir(arts, 0, None, exit_code=1, log="Traceback (most recent call last):\n  boom\nValueError: x")
    # cell 1 produced no artifact at all (timeout, cancelled, runner lost)
    out = cells.record(plan, arts, registry.path)
    rows = read_rows(registry.path)
    assert [r["status"] for r in rows] == ["failed", "failed"] and out.failed == 2
    assert "boom" in rows[0]["results"]["error"] or "boom" in rows[1]["results"]["error"]
    assert all(r["family"] == "session_open" and r["results"]["cell"]["idx"] in (0, 1) for r in rows)
    assert n_trials_effective(rows) == 2                                     # a failed look is counted


def test_a_failed_row_never_passes_the_gates_or_the_holdout_checks(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "registry.jsonl")
    plan = cells.plan(_env())
    cells.record(plan, tmp_path / "none", registry.path)
    reg = TrialRegistry(registry.path)
    row = reg._rows()[0]
    assert row["status"] == "failed" and not reg.passed_gates(row["family"], row["config"])
    assert not reg.holdout_scored(row["family"], row["config"])


def test_a_refusal_before_any_look_is_reported_but_charges_no_trial(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "registry.jsonl")
    plan = cells.plan(_env())
    arts = tmp_path / "arts"
    _cell_dir(arts, 0, None, exit_code=1, log="trial budget exceeded: 2027Q1 allows 40 pre-registered trials", report="")
    out = cells.record(plan, arts, registry.path)
    assert read_rows(registry.path) == [] and out.refused == 1 and out.failed == 0
    assert "budget exceeded" in out.reports[0]["body"]


def test_a_cell_that_recorded_a_row_and_then_failed_keeps_its_row_and_adds_no_failed_one(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "registry.jsonl")
    plan = cells.plan(_env(VARIANTS=_VARIANTS))[:1]
    arts = tmp_path / "arts"
    _cell_dir(arts, 0, [_planned_row(tmp_path, "a")], exit_code=1, log="Traceback ...")
    out = cells.record(plan, arts, registry.path)
    assert [r["status"] for r in read_rows(registry.path)] == ["evaluated"] and out.failed == 0


def test_reports_are_grouped_one_per_issue_title_and_kept_under_the_comment_limit(tmp_path: Path) -> None:
    registry = TrialRegistry(tmp_path / "registry.jsonl")
    batch = json.dumps([{"specialist": "tsmom"}, {"specialist": "session_open"}, {"specialist": "session_open"},
                        {"pooled": "1h"}])
    plan = cells.plan(_env(BATCH=batch))
    arts = tmp_path / "arts"
    for i in range(4):
        _cell_dir(arts, i, None, exit_code=1, log="x" * 200_000 + "\nTraceback (most recent call last):\nboom")
    out = cells.record(plan, arts, registry.path)
    assert sorted(r["title"] for r in out.reports) == ["research: pooled_1h", "research: session_open", "research: tsmom"]
    assert all(len(r["body"]) <= cells.MAX_COMMENT for r in out.reports)


def test_the_merge_uses_the_registry_sync_union_so_a_concurrent_writers_rows_survive(tmp_path: Path) -> None:
    published = TrialRegistry(tmp_path / "pub.jsonl")
    _row(published, family="vps_monthly")
    mine = tmp_path / "mine.jsonl"
    mine.write_text(published.path.read_text())
    cells.record(cells.plan(_env()), tmp_path / "none", mine)
    assert len(merge_rows(read_rows(published.path), read_rows(mine))) == 2


# ------------------------------------------------------------------------------------------------- the workflow
WF = yaml.safe_load((ROOT / ".github/workflows/research.yml").read_text())
RAW = (ROOT / ".github/workflows/research.yml").read_text()


def test_workflow_has_a_plan_a_parallel_trials_matrix_and_a_serialized_record_job() -> None:
    jobs = WF["jobs"]
    assert set(jobs) == {"plan", "trials", "record"}
    trials, record = jobs["trials"], jobs["record"]
    assert trials["needs"] == "plan" and trials["strategy"]["fail-fast"] is False
    assert 1 < trials["strategy"]["max-parallel"] <= 10
    assert record["needs"] == ["plan", "trials"] and record["if"].startswith("${{ always()")
    assert record["concurrency"] == {"group": "research-registry", "cancel-in-progress": False}
    assert "concurrency" not in trials and "concurrency" not in jobs["plan"]


def test_only_the_record_job_can_write_and_only_from_main() -> None:
    assert WF["permissions"] == {}
    assert WF["jobs"]["record"]["permissions"] == {"contents": "write", "issues": "write"}
    for name in ("plan", "trials"):
        assert WF["jobs"][name]["permissions"] == {"contents": "read"}
    for job in WF["jobs"].values():
        assert "github.ref == 'refs/heads/main'" in job["if"]
    for name in ("plan", "trials"):
        steps = "\n".join(s.get("run", "") for s in WF["jobs"][name]["steps"])
        assert "registry_release.sh save" not in steps and "gh issue" not in steps and "release upload" not in steps


def test_only_the_record_job_saves_the_registry_and_the_trials_read_a_snapshot_copy() -> None:
    save = [n for n, j in WF["jobs"].items() if "registry_release.sh save" in "\n".join(s.get("run", "") for s in j["steps"])]
    assert save == ["record"]
    run = "\n".join(s.get("run", "") for s in WF["jobs"]["trials"]["steps"])
    assert "registry_release.sh fetch snapshot.jsonl" in run and "cp snapshot.jsonl cell/registry.jsonl" in run
    assert "--registry cell/registry.jsonl" in run and "--registry registry.jsonl" not in run


def test_inputs_reach_the_shell_only_through_env_and_actions_are_pinned() -> None:
    assert not re.search(r"\$\{\{\s*(inputs|matrix|github\.event)[^}]*\}\}", "\n".join(
        s.get("run", "") for j in WF["jobs"].values() for s in j["steps"]))
    for j in WF["jobs"].values():
        for s in j["steps"]:
            if "uses" in s:
                assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", s["uses"]), s["uses"]
    inputs = WF[True]["workflow_dispatch"]["inputs"]
    assert {"specialist", "variants", "pooled", "batch", "discover", "rule_ref", "macro"} <= set(inputs)


# ------------------------------------------------------------------------------ untrusted artifacts (hardening)
_VARIANTS = '[{"stop_atr": 1}, {"stop_atr": 2}]'      # settings the specialist resolves, so the plan names its agent


def _two_cell_plan_with(tmp: Path, first: str | list[dict[str, Any]]) -> tuple[Any, Path, TrialRegistry]:
    registry = TrialRegistry(tmp / "registry.jsonl")
    plan = cells.plan(_env(VARIANTS=_VARIANTS))
    arts = tmp / "arts"
    _cell_dir(arts, 1, [_planned_row(tmp, "b", 2, cell=1)])
    if isinstance(first, str):
        _cell_dir(arts, 0, None)
        (arts / "cell-0" / "rows.jsonl").write_text(first)
    else:
        _cell_dir(arts, 0, first)
    return plan, arts, registry


def _planned_row(tmp: Path, name: str, n: int = 1, cell: int = 0, **override: Any) -> dict[str, Any]:
    """A row as the cell's research_pass would write it: the agent and family the plan resolves for the cell."""
    agent, family, _, resolved = cells._planned(cells.plan(_env(VARIANTS=_VARIANTS))[cell])
    assert resolved
    row = TrialRegistry(tmp / f"{name}.jsonl").record(
        agent_id=agent, family=family, config={"stop_atr": n}, feature_version="fv", rationale="r",
        results=_evaluated_results(k_run=3, seed=n + 1), status="evaluated")
    row.update(override)
    return row


def test_a_truncated_rows_file_fails_that_cell_only_and_the_other_cells_still_record(tmp_path: Path) -> None:
    plan, arts, registry = _two_cell_plan_with(tmp_path, '{"trial": 1, "ts": "2026-01-01T00:0')
    out = cells.record(plan, arts, registry.path)
    rows = read_rows(registry.path)
    assert sorted(r["status"] for r in rows) == ["evaluated", "failed"] and out.failed == 1 and out.recorded == 1
    assert n_trials_effective(rows) == 2                                     # the bad cell still counts toward K
    assert "unreadable" in next(r for r in rows if r["status"] == "failed")["results"]["error"]


def test_a_rows_file_that_is_not_a_list_of_objects_is_a_failed_cell_not_a_crash(tmp_path: Path) -> None:
    plan, arts, registry = _two_cell_plan_with(tmp_path, "[1, 2]\n")
    out = cells.record(plan, arts, registry.path)
    assert out.failed == 1 and sorted(r["status"] for r in read_rows(registry.path)) == ["evaluated", "failed"]


def test_a_row_missing_registry_fields_fails_the_cell_with_the_reason(tmp_path: Path) -> None:
    row = _planned_row(tmp_path, "a")
    del row["config_hash"]
    plan, arts, registry = _two_cell_plan_with(tmp_path, [row])
    cells.record(plan, arts, registry.path)
    failed = [r for r in read_rows(registry.path) if r["status"] == "failed"]
    assert len(failed) == 1 and "config_hash" in failed[0]["results"]["error"]


def test_a_row_with_a_status_a_cell_may_not_write_is_rejected_and_the_cell_failed(tmp_path: Path) -> None:
    plan, arts, registry = _two_cell_plan_with(tmp_path, [_planned_row(tmp_path, "a", status="verdict")])
    out = cells.record(plan, arts, registry.path)
    rows = read_rows(registry.path)
    assert out.failed == 1 and "verdict" in next(r for r in rows if r["status"] == "failed")["results"]["error"]
    assert not any(r["status"] == "verdict" for r in rows)


@pytest.mark.parametrize("bad", [{"family": "other_family"}, {"agent_id": "someone-else"}])
def test_a_row_for_another_family_or_agent_than_the_cell_was_planned_for_is_rejected(
        tmp_path: Path, bad: dict[str, Any]) -> None:
    plan, arts, registry = _two_cell_plan_with(tmp_path, [_planned_row(tmp_path, "a", **bad)])
    out = cells.record(plan, arts, registry.path)
    rows = read_rows(registry.path)
    assert out.failed == 1 and "planned as" in next(r for r in rows if r["status"] == "failed")["results"]["error"]
    assert not any(r.get(k) == v for r in rows for k, v in bad.items())


def test_a_valid_row_for_the_planned_cell_is_accepted(tmp_path: Path) -> None:
    plan, arts, registry = _two_cell_plan_with(tmp_path, [_planned_row(tmp_path, "a")])
    out = cells.record(plan, arts, registry.path)
    assert out.failed == 0 and out.recorded == 2

def test_lot_fit_is_an_optional_validated_flag_off_by_default_and_passed_as_a_bare_option() -> None:
    assert "--lot-fit" not in cells.argv(cells.plan(_env())[0])
    assert cells.plan(_env())[0]["lot_fit"] is False
    on = cells.plan(_env(LOT_FIT="true"))
    assert on[0]["lot_fit"] is True and "--lot-fit" in cells.argv(on[0])
    item = cells.plan(_env(BATCH=json.dumps([{"specialist": "tsmom", "lot_fit": True}, {"specialist": "tsmom"}])))
    assert [c["lot_fit"] for c in item] == [True, False]
    inputs = WF[True]["workflow_dispatch"]["inputs"]
    assert "lot_fit" in inputs and inputs["lot_fit"]["default"] is False
    env = next(s for s in WF["jobs"]["plan"]["steps"] if s.get("id") == "plan")["env"]
    assert env["LOT_FIT"] == "${{ inputs.lot_fit }}"
