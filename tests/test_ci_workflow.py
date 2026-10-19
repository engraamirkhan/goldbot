""".github/workflows/ci.yml invariants: the ones that, broken, silently lose tests, block merges or deploys, or weaken
the supply chain. scripts/ci_local.sh reads its shard counts from the same file, so local and CI stay in step."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml")) + sorted((ROOT / ".github" / "actions").glob("*/action.yml"))
AGGREGATES = {"backend", "frontend"}          # required checks on main; goldbot/ops/deploy.py REQUIRED_CHECKS
NOT_GATED = {"backend", "frontend", "deployable", "report-failure"}


def _ci() -> dict:
    return yaml.safe_load(CI.read_text())


def _on(wf: dict) -> dict:
    on: dict = wf["on"] if "on" in wf else wf[True]     # YAML 1.1 reads a bare `on:` key as True
    return on


def test_required_checks_exist_and_match_the_deploy_gate():
    from goldbot.ops.deploy import REQUIRED_CHECKS
    jobs = _ci()["jobs"]
    assert set(REQUIRED_CHECKS) == AGGREGATES <= set(jobs)
    assert all("name" not in jobs[j] for j in AGGREGATES)        # the check-run name is the job id


def test_every_check_job_gates_a_required_check_and_reports_failures():
    jobs = _ci()["jobs"]
    checks = set(jobs) - NOT_GATED
    gated = {n for a in AGGREGATES for n in jobs[a]["needs"]}
    assert checks <= gated, f"jobs no required check waits for: {checks - gated}"
    assert checks <= set(jobs["report-failure"]["needs"]), "a failing job would open no CI issue"
    for a in AGGREGATES:
        assert jobs[a]["if"] == "always()"     # a skipped or cancelled dependency must fail the aggregate, not skip it


def test_shard_denominators_equal_the_matrix_length():
    # GOLDBOT_SHARD=i/N with N != len(matrix) silently skips (or repeats) a slice of the suite
    jobs = _ci()["jobs"]
    for name in ("backend-unit", "backend-integration"):
        shards = jobs[name]["strategy"]["matrix"]["shard"]
        assert shards == list(range(len(shards)))
        assert jobs[name]["env"]["GOLDBOT_SHARD"] == f"${{{{ matrix.shard }}}}/{len(shards)}"
        assert jobs[name]["strategy"]["fail-fast"] is False


def test_pytest_markers_match_between_ci_and_ci_local():
    text = CI.read_text()
    local = (ROOT / "scripts" / "ci_local.sh").read_text()
    for marker in ('-m "not integration"', "-m integration"):
        assert marker in text and marker in local
    for read in ("backend-unit", "backend-integration", "e2e"):   # ci_local derives the matrix from ci.yml
        assert read in local


def test_e2e_matrix_is_the_playwright_projects():
    projects = _ci()["jobs"]["e2e"]["strategy"]["matrix"]["project"]
    config = (ROOT / "web" / "playwright.config.ts").read_text()
    assert sorted(projects) == sorted(re.findall(r'name: "([a-z0-9-]+)"', config))
    assert "failOnFlakyTests" in config        # a retry that passes must still fail the build in CI


def test_pull_requests_cancel_superseded_runs_but_main_never_does():
    c = _ci()["concurrency"]
    assert c["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert "github.sha" in c["group"]         # pushes to main: one group per commit


def test_ci_runs_on_every_pr_and_main_push_so_required_checks_always_report():
    on = _on(_ci())
    assert "paths-ignore" not in (on.get("pull_request") or {}) and "paths" not in (on.get("pull_request") or {})
    assert on["push"]["branches"] == ["main"]


def test_supply_chain_rules_hold_in_every_workflow():
    for path in WORKFLOWS:
        text = path.read_text()
        if path.parent.name == "workflows":
            assert "pull_request_target" not in _on(yaml.safe_load(text)), path   # never run PR code with a write token
        for ref in re.findall(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", text, re.M):
            assert ref.startswith("./") or re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{path.name}: {ref}"


def test_token_is_read_only_except_where_a_job_must_write():
    wf = _ci()
    assert wf["permissions"] == {}
    writers = {j: {k for k, v in (spec.get("permissions") or {}).items() if v == "write"} for j, spec in wf["jobs"].items()}
    assert {j: w for j, w in writers.items() if w} == {"report-failure": {"issues"}, "deployable": {"statuses"}}
    assert wf["jobs"]["deployable"]["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"


def test_ci_uses_no_secrets():
    for path in [CI, *sorted((ROOT / ".github" / "actions").glob("*/action.yml"))]:
        assert "${{ secrets." not in path.read_text(), path


def test_ci_never_deploys():
    text = CI.read_text().lower()
    assert "ssh " not in text and "scp " not in text
    assert "sudo goldbot-deploy" in text      # it prints the owner's command; it never runs it


def test_the_change_filter_runs_from_the_base_commit_never_the_pr():
    steps = _ci()["jobs"]["changes"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert checkout["if"] == "github.event_name == 'pull_request'"


def test_backend_installs_come_from_the_lock_file():
    action = (ROOT / ".github" / "actions" / "setup-backend" / "action.yml").read_text()
    assert "uv sync --frozen" in action and "cache-dependency-glob: uv.lock" in action and "pip install" not in action
    assert "uv lock --check" in CI.read_text() and "uv lock --check" in (ROOT / "scripts" / "ci_local.sh").read_text()


def test_no_test_job_waits_for_smoke_but_smoke_still_fails_the_run():
    # the whole PR run is as long as its slowest job, not smoke plus its slowest shard
    jobs = _ci()["jobs"]
    for name in ("backend-unit", "backend-integration", "e2e", "lint"):
        assert "smoke" not in jobs[name].get("needs", []), name
    assert "smoke" in jobs["backend"]["needs"] and "smoke" in jobs["report-failure"]["needs"]


def test_the_matrices_fit_the_20_concurrent_job_cap_of_the_free_plan():
    # `changes` is done before the shards start; the aggregates, deployable and report-failure run after them
    running = 0
    for name, spec in _ci()["jobs"].items():
        if name in NOT_GATED or name == "changes":
            continue
        matrix = (spec.get("strategy") or {}).get("matrix") or {}
        running += len(next(iter(matrix.values()))) if matrix else 1
    assert running <= 20, f"{running} jobs can run at once; GitHub's free plan allows 20"


def test_the_dry_run_is_in_the_lightest_integration_shard_and_no_shard_is_heavy():
    import json
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    from conftest import (
        shard_assignment,  # type: ignore[import-not-found]  # tests/ is on sys.path only at run time
    )
    job = _ci()["jobs"]["backend-integration"]
    count = len(job["strategy"]["matrix"]["shard"])
    weights = json.loads((ROOT / "tests" / "shard_weights.json").read_text())["seconds"]
    assignment = shard_assignment(list(weights), count, weights)
    loads = [sum(w for n, w in weights.items() if assignment[n] == s) for s in range(count)]
    dry = next(s for s in job["steps"] if s.get("name") == "dry-run")["if"]
    assert f"matrix.shard == {loads.index(min(loads))}" in dry
    assert max(loads) / 4 <= 110, "a shard's heavy tests alone exceed ~110 s at 4 workers"
