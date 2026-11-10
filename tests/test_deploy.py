"""Deployment by the owner's click or by hand (goldbot/ops/deploy.py): only merged commits that passed CI are offered,
an approval names one exact commit, results are reported once. Nothing deploys by itself."""
import json
import logging
import subprocess
from typing import Any

import pytest
import requests

from goldbot.ops.deploy import ACTIONS_APP_ID, DeployWatch, ci_status

pytestmark = pytest.mark.clock_at("2026-10-30T12:00:00Z")

A, B, C = "a" * 40, "b" * 40, "c" * 40


def _runs(**conclusions: str | None) -> dict[str, Any]:
    return {"check_runs": [{"name": n, "status": "completed" if c else "in_progress", "conclusion": c}
                           for n, c in conclusions.items()]}


def test_ci_status_reads_only_the_latest_github_actions_runs():
    urls: list[str] = []

    def fetch(url: str) -> dict[str, Any]:
        urls.append(url)
        return _runs(backend="success", frontend="success")
    ci_status(B, fetch)
    assert f"app_id={ACTIONS_APP_ID}" in urls[0] and "filter=latest" in urls[0]


def test_ci_status_needs_every_required_job_to_pass():
    assert ci_status(B, lambda url: _runs(backend="success", frontend="success")) == "yes"
    assert ci_status(B, lambda url: _runs(backend="success", frontend=None)) == "wait"
    assert ci_status(B, lambda url: _runs(backend="success")) == "wait"               # frontend not reported yet
    assert ci_status(B, lambda url: _runs(backend="failure", frontend="success")) == "no"


class _Git:
    """The service user's git under ProtectSystem=strict: it may only read. A writing command fails the test."""

    def __init__(self, head: str, main: str, ff: bool = True) -> None:
        self.head, self.main, self.ff = head, main, ff
        self.down = False                                   # origin unreachable
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[0] in ("fetch", "pull", "remote", "update-ref", "gc", "worktree"):
            raise AssertionError(f"git {args[0]} writes /opt/goldbot/.git, read-only to the telegram service")
        if args[0] == "ls-remote":
            if self.down:
                raise subprocess.CalledProcessError(128, ["git", "ls-remote"],
                                                    stderr="fatal: unable to access remote\nmore detail\n")
            return f"{self.main}\trefs/heads/main"
        if args[0] == "rev-parse":
            return self.head
        return ""


class _GitHub:
    """GitHub's public API, read-only: check-runs and compare (base...head)."""

    def __init__(self, git: _Git, ci: str = "success", not_ancestor: tuple[str, ...] = ()) -> None:
        self.git, self.ci, self.not_ancestor = git, ci, not_ancestor

    def __call__(self, url: str) -> dict[str, Any]:
        if "/check-runs" in url:
            return _runs(backend=self.ci, frontend="success")
        base, head = url.split("/compare/")[1].split("?")[0].split("...")
        if base == head:
            return {"status": "identical", "ahead_by": 0, "commits": []}
        if not self.git.ff or base in self.not_ancestor:
            return {"status": "diverged", "ahead_by": 2, "behind_by": 1, "commits": []}
        return {"status": "ahead", "ahead_by": 2,           # oldest first, as GitHub lists them
                "commits": [{"commit": {"message": "fix: two\n\nbody"}}, {"commit": {"message": "feat: one"}}]}


def _watch(tmp_path, git: _Git, ci: str = "success", not_ancestor: tuple[str, ...] = ()) -> DeployWatch:
    return DeployWatch(tmp_path, tmp_path, fetch=_GitHub(git, ci, not_ancestor), git=git)


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
    assert not w.approve(B)                                    # nothing on offer yet
    w.check()
    assert not w.approve(C) and not w.approve("not-a-sha")
    assert w.approve(B)
    assert json.loads((tmp_path / "deploy" / "approved.json").read_text()) == {"sha": B}       # the commit only: who tapped is in the audit log (A15)
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


def test_health_reports_the_last_deploy(tmp_path):
    from goldbot.ops import health
    from tests.test_health import make_ctx
    ctx = make_ctx(tmp_path)
    assert health.check_deploy(ctx).status == "ok"
    log = tmp_path / "deploys.jsonl"
    for result, status in (("deployed", "ok"), ("rolled_back", "warn"), ("failed", "fail"),
                           ("rolled_back_manual", "warn"), ("rollback_reverted", "warn"),
                           ("deployed_degraded", "warn")):
        with log.open("a") as f:
            f.write(json.dumps({"result": result, "to": B, "role": "brain", "detail": "x"}) + "\n")
        assert health.check_deploy(ctx).status == status


def test_a_degraded_deploy_whose_checks_are_ok_now_reads_ok(tmp_path):
    """deployed_degraded is a point-in-time note: once the named checks read ok in the last health run it is ok."""
    import pandas as pd

    from goldbot.ops import health
    from goldbot.ops.checks._base import WATCH_FILE
    from tests.test_health import NOW, make_ctx
    ctx = make_ctx(tmp_path)
    rec = {"result": "deployed_degraded", "to": B, "role": "brain", "ts": "t",
           "detail": "engine stopped for the deploy: x; deployed (degraded: engine_loop:icm-demo,tick_errors:icm-demo); all ok"}
    (tmp_path / "deploys.jsonl").write_text(json.dumps(rec) + "\n")
    assert health.check_deploy(ctx).status == "warn"                       # no health run recorded
    watch = tmp_path / WATCH_FILE

    def run(ts, **st):
        watch.write_text(json.dumps({"ts": str(ts), "statuses": st}))
        return health.check_deploy(ctx)
    c = run(NOW, **{"engine_loop:icm-demo": "ok", "tick_errors:icm-demo": "ok"})
    assert c.status == "ok" and c.reason.startswith(f"deployed {B[:8]}; degraded at deploy time (engine_loop:icm-demo,"
                                                    "tick_errors:icm-demo), now ok")
    assert run(NOW, **{"engine_loop:icm-demo": "ok", "tick_errors:icm-demo": "warn"}).status == "warn"
    assert run(NOW, **{"engine_loop:icm-demo": "ok"}).status == "warn"      # a named check is missing
    assert run(NOW - pd.Timedelta(hours=2), **{"engine_loop:icm-demo": "ok", "tick_errors:icm-demo": "ok"}).status == "warn"


def _engine_state(tmp_path, account: str, compat: dict[str, Any] | None) -> None:
    (tmp_path / f"engine_{account}.json").write_text(json.dumps({"bridge_compat": compat}))


def test_the_offer_says_when_the_mt5_box_runs_an_older_commit(tmp_path):
    """G6 / BACKLOG 42: the one-click offer updates only the brain; it says when the MT5 box is behind."""
    _engine_state(tmp_path, "icm-demo", {"status": "ok", "bridge_commit": A})
    offer = _watch(tmp_path, _Git(A, B)).check()
    assert offer is not None and len(offer["mt5"]) == 1
    note = offer["mt5"][0]
    assert "icm-demo" in note and A[:8] in note and "older" in note and "MT5 box first" in note


def test_the_offer_is_silent_when_the_mt5_box_runs_the_offered_commit_and_flags_unknown_ones(tmp_path):
    _engine_state(tmp_path, "icm-demo", {"status": "ok", "bridge_commit": B})
    offer = _watch(tmp_path, _Git(A, B)).check()
    assert offer is not None and offer["mt5"] == []
    other = tmp_path / "o"
    other.mkdir()
    _engine_state(other, "icm-demo", {"status": "incompatible", "bridge_commit": None})
    _engine_state(other, "paper", None)                                            # no bridge: nothing to say
    offer = _watch(other, _Git(A, B)).check()
    assert offer is not None and len(offer["mt5"]) == 1 and "does not report its commit" in offer["mt5"][0]
    diverged = tmp_path / "d"
    diverged.mkdir()
    _engine_state(diverged, "icm-demo", {"status": "ok", "bridge_commit": C})
    offer = _watch(diverged, _Git(A, B), not_ancestor=(C,)).check()
    assert offer is not None and len(offer["mt5"]) == 1 and "not an ancestor" in offer["mt5"][0]
    junk = tmp_path / "j"
    junk.mkdir()
    (junk / "engine_icm-demo.json").write_text("{not json")
    _engine_state(junk, "x", {"status": "ok", "bridge_commit": "--upload-pack=evil"})
    seen: list[tuple[str, ...]] = []

    class _Rec(_Git):
        def __call__(self, *args: str) -> str:
            seen.append(args)
            return super().__call__(*args)
    offer = _watch(junk, _Rec(A, B)).check()
    assert offer is not None and len(offer["mt5"]) == 1                            # N3: said, not silently dropped
    assert "unrecognised commit" in offer["mt5"][0] and "upload-pack" not in offer["mt5"][0]   # never echoed
    assert not any("--upload-pack=evil" in a for call in seen for a in call)     # never passed to the VCS


def test_the_check_reads_the_remote_head_with_ls_remote_and_never_fetches(tmp_path):
    """goldbot-telegram runs under ProtectSystem=strict with /opt/goldbot/.git read-only: a `git fetch` there exits
    255 on every pass and no version is ever offered. The check reads origin's main with `git ls-remote`."""
    git = _Git(A, B)
    offer = _watch(tmp_path, git).check()
    assert offer is not None and offer["sha"] == B
    assert ("ls-remote", "origin", "refs/heads/main") in git.calls
    assert {c[0] for c in git.calls} <= {"ls-remote", "rev-parse"}               # read-only commands only


def test_the_offer_says_so_when_github_cannot_place_the_mt5_box_commit(tmp_path):
    _engine_state(tmp_path, "icm-demo", {"status": "ok", "bridge_commit": C})
    git = _Git(A, B)
    hub = _GitHub(git)

    def fetch(url: str) -> Any:
        if f"/compare/{C}..." in url:
            raise requests.HTTPError("403 rate limited")
        return hub(url)
    offer = DeployWatch(tmp_path, tmp_path, fetch=fetch, git=git).check()
    assert offer is not None and len(offer["mt5"]) == 1
    assert C[:8] in offer["mt5"][0] and "check it before deploying" in offer["mt5"][0]


def test_the_offer_lists_commit_subjects_newest_first_from_github(tmp_path):
    offer = _watch(tmp_path, _Git(A, B)).check()
    assert offer is not None and offer["subjects"] == ["feat: one", "fix: two"]
    assert offer["n_commits"] == 2 and offer["from"] == A


def test_a_new_remote_commit_is_detected_on_the_next_pass(tmp_path):
    git = _Git(A, A)
    w = _watch(tmp_path, git)
    assert w.check() is None
    git.main = B
    offer = w.check()
    assert offer is not None and offer["sha"] == B


def test_without_an_answer_from_github_nothing_is_offered(tmp_path, caplog):
    """The fast-forward is shown by GitHub's compare (the new commit is not in the read-only local repo): no answer,
    no offer, one warning without a traceback."""
    def down(url: str) -> Any:
        raise requests.ConnectionError("api.github.com unreachable")
    w = DeployWatch(tmp_path, tmp_path, fetch=down, git=_Git(A, B))
    with caplog.at_level(logging.WARNING, logger="goldbot.ops.deploy"):
        assert w.check() is None and w.check() is None
    assert len(caplog.records) == 1 and caplog.records[0].exc_info is None
    assert not (tmp_path / "deploy" / "pending.json").exists()


def test_an_ls_remote_failure_logs_one_concise_warning_per_outage_and_offers_nothing(tmp_path, caplog):
    git = _Git(A, B)
    git.down = True
    w = _watch(tmp_path, git)
    with caplog.at_level(logging.WARNING, logger="goldbot.ops.deploy"):
        assert w.check() is None and w.check() is None and w.check() is None
    assert len(caplog.records) == 1 and caplog.records[0].exc_info is None
    msg = caplog.records[0].getMessage()
    assert "ls-remote" in msg and "unable to access remote" in msg and "more detail" not in msg      # one line
    assert not (tmp_path / "deploy" / "pending.json").exists()
    caplog.clear()
    git.down, git.main = False, A
    assert w.check() is None                                                     # recovered: nothing new
    git.down = True
    with caplog.at_level(logging.WARNING, logger="goldbot.ops.deploy"):
        w.check()
    assert len(caplog.records) == 1                                              # a new outage is said again
