"""Deployment by the owner's click or by hand (goldbot/ops/deploy.py): only merged commits that passed CI are offered,
an approval names one exact commit, results are reported once. Nothing deploys by itself."""
import json
import logging
import subprocess
from typing import Any

import pytest
import requests

from goldbot.ops.checks.services import DEPLOY_READ_STALE_S
from goldbot.ops.deploy import (
    ACTIONS_APP_ID,
    GIT_TIMEOUT_S,
    MAX_RESPONSE_BYTES,
    MAX_SUBJECT_CHARS,
    RESPONSE_DEADLINE_S,
    DeployWatch,
    _get_json,
    ci_status,
    offer_text,
)

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


class _HungGit(_Git):
    def __call__(self, *args: str) -> str:
        if args[0] == "ls-remote":
            raise subprocess.TimeoutExpired(["git", "ls-remote"], GIT_TIMEOUT_S)
        return super().__call__(*args)


def test_a_hung_ls_remote_times_out_with_one_warning_and_no_offer(tmp_path, caplog):
    """SRE review: check() is awaited in the bot's pump, so a git that hangs must end, not stall approvals."""
    w = _watch(tmp_path, _HungGit(A, B))
    with caplog.at_level(logging.WARNING, logger="goldbot.ops.deploy"):
        assert w.check() is None and w.check() is None
    assert len(caplog.records) == 1 and caplog.records[0].exc_info is None
    assert "timed out" in caplog.records[0].getMessage()
    assert not (tmp_path / "deploy" / "pending.json").exists()


def test_the_real_git_runner_has_a_timeout_and_never_prompts(tmp_path, monkeypatch):
    seen: dict[str, Any] = {}

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kw, argv=argv)
        return subprocess.CompletedProcess(argv, 0, stdout=f"{B}\trefs/heads/main\n", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    assert DeployWatch(tmp_path, tmp_path)._git("ls-remote", "origin", "refs/heads/main").startswith(B)
    assert seen["timeout"] == GIT_TIMEOUT_S and seen["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_a_successful_read_records_its_time_and_a_failure_keeps_it(tmp_path):
    git = _Git(A, A)
    w = DeployWatch(tmp_path, tmp_path, fetch=_GitHub(git), git=git, clock=lambda: 1000.0)
    w.check()
    rec = json.loads((tmp_path / "deploy" / "check.json").read_text())
    assert rec["last_ok"] == 1000.0 and rec.get("error") is None
    git.down = True
    w.clock = lambda: 5000.0
    w.check()
    rec = json.loads((tmp_path / "deploy" / "check.json").read_text())
    assert rec["last_ok"] == 1000.0 and rec["failing_since"] == 5000.0 and "ls-remote" in rec["error"]


def test_the_deploy_read_check_is_ok_while_reads_succeed(tmp_path):
    from goldbot.ops import health
    from tests.test_health import NOW, make_ctx, write
    assert health.check_deploy_read(make_ctx(tmp_path)) is None                 # deploys off on this host
    write(tmp_path / "deploy" / "check.json", {"last_ok": NOW.timestamp() - 600})
    c = health.check_deploy_read(make_ctx(tmp_path))
    assert c is not None and c.status == "ok"


def test_the_deploy_read_check_warns_and_tells_the_owner_after_two_hours_without_a_read(tmp_path):
    from goldbot.ops import health
    from tests.test_health import NOW, make_ctx, write
    write(tmp_path / "deploy" / "check.json", {"last_ok": NOW.timestamp() - DEPLOY_READ_STALE_S - 60,
                                               "failing_since": NOW.timestamp() - 3600,
                                               "error": "`git ls-remote` exited 255: fatal: x"})
    c = health.check_deploy_read(make_ctx(tmp_path))
    assert c is not None and c.status == "warn" and "exited 255" in c.reason and "RUNBOOK" in c.reason
    assert health._alerting(c.name, c.status)                                   # NOTIFY_ON_WARN
    assert c in health.run_checks(make_ctx(tmp_path)).checks


def test_the_deploy_read_check_warns_when_no_read_ever_succeeded(tmp_path):
    from goldbot.ops import health
    from tests.test_health import NOW, make_ctx, write
    write(tmp_path / "deploy" / "check.json", {"last_ok": None, "failing_since": NOW.timestamp() - 600, "error": "e"})
    c = health.check_deploy_read(make_ctx(tmp_path))
    assert c is not None and c.status == "ok"                                   # failing, but not for 2 h yet
    write(tmp_path / "deploy" / "check.json", {"last_ok": None, "failing_since": NOW.timestamp() - DEPLOY_READ_STALE_S - 1,
                                               "error": "e"})
    c = health.check_deploy_read(make_ctx(tmp_path))
    assert c is not None and c.status == "warn" and "never" in c.reason


@pytest.mark.parametrize("cmp", [
    {"status": "ahead", "ahead_by": 2, "commits": "not a list"},
    {"status": "ahead", "ahead_by": 2, "commits": ["not a dict"]},
    {"status": "ahead", "ahead_by": "two", "commits": []},
    {"status": "ahead", "ahead_by": 2, "commits": [{"commit": {}}]},
])
def test_a_malformed_compare_answer_gives_one_warning_and_no_offer(tmp_path, caplog, cmp):
    git = _Git(A, B)
    hub = _GitHub(git)
    w = DeployWatch(tmp_path, tmp_path, fetch=lambda url: cmp if "/compare/" in url else hub(url), git=git)
    with caplog.at_level(logging.WARNING, logger="goldbot.ops.deploy"):
        assert w.check() is None
    assert len(caplog.records) == 1 and caplog.records[0].exc_info is None
    assert not (tmp_path / "deploy" / "pending.json").exists()


class _Resp:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int) -> Any:
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


def test_a_github_answer_larger_than_the_cap_is_refused(monkeypatch):
    resp = _Resp([b"x" * (MAX_RESPONSE_BYTES // 2 + 1)] * 3)
    monkeypatch.setattr(requests, "get", lambda url, **kw: resp)
    with pytest.raises(ValueError, match="larger than"):
        _get_json("https://api.github.com/x")
    assert resp.closed


def test_a_github_answer_past_the_deadline_is_refused(monkeypatch):
    import goldbot.ops.deploy as deploy
    ticks = iter([0.0, 1.0, RESPONSE_DEADLINE_S + 1.0, RESPONSE_DEADLINE_S + 2.0])
    monkeypatch.setattr(deploy.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp([b"{", b"}"]))
    with pytest.raises(ValueError, match="deadline"):
        _get_json("https://api.github.com/x")


def test_a_small_github_answer_is_read_streamed_with_a_timeout(monkeypatch):
    seen: dict[str, Any] = {}

    def get(url: str, **kw: Any) -> _Resp:
        seen.update(kw)
        return _Resp([b'{"a": ', b"1}"])
    monkeypatch.setattr(requests, "get", get)
    assert _get_json("https://api.github.com/x") == {"a": 1}
    assert seen["stream"] is True and seen["timeout"]


def test_the_mt5_box_compare_asks_github_for_one_commit_only(tmp_path):
    _engine_state(tmp_path, "icm-demo", {"status": "ok", "bridge_commit": C})
    git = _Git(A, B)
    hub = _GitHub(git)
    urls: list[str] = []

    def fetch(url: str) -> Any:
        urls.append(url)
        return hub(url)
    DeployWatch(tmp_path, tmp_path, fetch=fetch, git=git).check()
    (mt5,) = [u for u in urls if f"/compare/{C}..." in u]
    assert mt5.endswith("?per_page=1")


def test_a_long_subject_is_cut_and_the_offer_text_fits_one_telegram_message(tmp_path):
    long = "feat: " + "y" * 500
    git = _Git(A, B)
    hub = _GitHub(git)

    def fetch(url: str) -> Any:
        if "/compare/" in url and f"/compare/{A}" in url:
            return {"status": "ahead", "ahead_by": 40, "commits": [{"commit": {"message": long}}] * 40}
        return hub(url)
    offer = DeployWatch(tmp_path, tmp_path, fetch=fetch, git=git).check()
    assert offer is not None and all(len(s) <= MAX_SUBJECT_CHARS for s in offer["subjects"])
    offer["mt5"] = ["MT5 box note " + "z" * 900] * 10                             # many long notes
    text = offer_text(offer)
    assert len(text) < 4000 and text.startswith(f"🚀 New version ready ({B[:8]}, CI passed)")
    assert "Deploy restarts the services" in text                               # the closing line survives the cut
    assert "… and 25 more" in offer_text({**offer, "mt5": []})
