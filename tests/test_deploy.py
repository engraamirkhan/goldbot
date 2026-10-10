"""Deployment by the owner's click or by hand (goldbot/ops/deploy.py): only merged commits that passed CI are offered,
an approval names one exact commit, results are reported once. Nothing deploys by itself."""
import json
import subprocess
from typing import Any

import pytest

from goldbot.ops.deploy import DeployWatch, ci_status, mark_deployed

A, B, C = "a" * 40, "b" * 40, "c" * 40


def _runs(**conclusions: str | None) -> dict[str, Any]:
    return {"check_runs": [{"name": n, "status": "completed" if c else "in_progress", "conclusion": c}
                           for n, c in conclusions.items()]}


def test_ci_status_needs_every_required_job_to_pass():
    assert ci_status(B, lambda url: _runs(backend="success", frontend="success")) == "yes"
    assert ci_status(B, lambda url: _runs(backend="success", frontend=None)) == "wait"
    assert ci_status(B, lambda url: _runs(backend="success")) == "wait"               # frontend not reported yet
    assert ci_status(B, lambda url: _runs(backend="failure", frontend="success")) == "no"


class _Git:
    def __init__(self, head: str, main: str, ff: bool = True) -> None:
        self.head, self.main, self.ff = head, main, ff

    def __call__(self, *args: str) -> str:
        if args[0] == "rev-parse":
            return self.head if args[1] == "HEAD" else self.main
        if args[0] == "merge-base" and not self.ff:
            raise subprocess.CalledProcessError(1, "git")
        if args[0] == "log":
            return "feat: one\nfix: two"
        return ""


def _watch(tmp_path, git: _Git, ci: str = "success") -> DeployWatch:
    return DeployWatch(tmp_path, tmp_path, fetch=lambda url: _runs(backend=ci, frontend="success"), git=git)


def test_a_new_commit_that_passed_ci_is_offered_once(tmp_path):
    w = _watch(tmp_path, _Git(A, B))
    offer = w.check()
    assert offer is not None and offer["sha"] == B and offer["subjects"] == ["feat: one", "fix: two"]
    assert w.check() is None                                          # offered once
    assert _watch(tmp_path / "x", _Git(A, A)).check() is None         # nothing new
    assert _watch(tmp_path / "y", _Git(A, B), ci="failure").check() is None
    assert _watch(tmp_path / "z", _Git(A, B, ff=False)).check() is None


def test_an_approval_names_the_exact_commit_on_offer(tmp_path):
    w = _watch(tmp_path, _Git(A, B))
    assert not w.approve(B, by=42)                                    # nothing on offer yet
    w.check()
    assert not w.approve(C, by=42) and not w.approve("not-a-sha", by=42)
    assert w.approve(B, by=42)
    assert json.loads((tmp_path / "deploy" / "approved.json").read_text()) == {"sha": B, "by": 42}
    assert not (tmp_path / "deploy" / "pending.json").exists()


def test_a_skipped_commit_is_not_offered_again(tmp_path):
    w = _watch(tmp_path, _Git(A, B))
    w.check()
    assert w.skip(B) and w.check() is None
    assert not (tmp_path / "deploy" / "approved.json").exists()


def test_results_are_reported_once(tmp_path):
    w = _watch(tmp_path, _Git(A, A))
    assert w.new_results() == []
    log = tmp_path / "deploys.jsonl"
    log.write_text(json.dumps({"result": "deployed", "to": B}) + "\n")
    assert [r["result"] for r in w.new_results()] == ["deployed"]
    assert w.new_results() == []
    with log.open("a") as f:
        f.write(json.dumps({"result": "rolled_back", "to": C}) + "\n")
    assert [r["result"] for r in w.new_results()] == ["rolled_back"]


@pytest.mark.parametrize("token, status, expected", [(None, 201, "no github-token"), ("t", 201, "marked"),
                                                     ("t", 403, "GitHub answered 403")])
def test_marking_a_deployment_for_the_mt5_box(token, status, expected):
    class R:
        status_code = status
    assert expected in mark_deployed(B, token, post=lambda *a, **k: R())
    assert mark_deployed("main", "t", post=lambda *a, **k: R()) == "invalid sha"


def test_health_reports_the_last_deploy(tmp_path):
    from goldbot.ops import health
    from tests.test_health import make_ctx
    ctx = make_ctx(tmp_path)
    assert health.check_deploy(ctx).status == "ok"
    log = tmp_path / "deploys.jsonl"
    for result, status in (("deployed", "ok"), ("rolled_back", "warn"), ("failed", "fail")):
        with log.open("a") as f:
            f.write(json.dumps({"result": result, "to": B, "role": "brain", "detail": "x"}) + "\n")
        assert health.check_deploy(ctx).status == status
