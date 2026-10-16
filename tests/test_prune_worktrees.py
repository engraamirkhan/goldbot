"""scripts/prune_worktrees.sh: remove lane worktrees whose work is merged, and nothing else.

Design rule (docs/process/tech-debt.md TD-8, throughput plan lever 4d): a lane's worktree goes once its branch is
merged into the integration branch; the main checkout, the current tree, a detached, locked, dirty, unmerged or
recently used worktree is never touched, and nothing is removed without `--apply` (dry run by default).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prune_worktrees.sh"
TWO_DAYS = 2 * 24 * 3600

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def git_env(monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull,
                 "GIT_CONFIG_NOSYSTEM": "1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("INTEGRATION_BRANCH", raising=False)


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def commit(root: Path, name: str) -> None:
    (root / f"{name}.txt").write_text(name, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", name)


def age(wt: Path) -> None:
    """Make a worktree look untouched for two days (its HEAD, index and reflog)."""
    gitdir = Path(git(wt, "rev-parse", "--absolute-git-dir").strip())
    old = time.time() - TWO_DAYS
    for f in (gitdir / "HEAD", gitdir / "index", gitdir / "logs" / "HEAD"):
        if f.exists():
            os.utime(f, (old, old))


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), "--into", "integ", *args], cwd=root, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, git_env):
    """The main checkout on `integ` plus one worktree per case; returns (main, {case: path})."""
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "integ")
    (main / ".gitignore").write_text(".env\n.venv/\n__pycache__/\n", encoding="utf-8")
    commit(main, "base")
    wts: dict[str, Path] = {}
    for case in ("merged", "dirty", "unmerged", "locked", "fresh", "detached", "secret", "empty"):
        wt = tmp_path / f"wt-{case}"
        if case == "detached":
            git(main, "worktree", "add", "-q", "--detach", str(wt), "integ")
        else:
            git(main, "worktree", "add", "-q", "-b", f"worktree-agent-{case}", str(wt), "integ")
        wts[case] = wt
    for case in ("merged", "dirty", "fresh", "secret"):  # lanes with work of their own, merged --no-ff
        commit(wts[case], f"{case} lane work")
        git(main, "merge", "-q", "--no-ff", "--no-edit", f"worktree-agent-{case}")
    commit(wts["unmerged"], "not merged yet")
    (wts["dirty"] / "scratch.py").write_text("x = 1\n", encoding="utf-8")
    (wts["secret"] / ".env").write_text("TOKEN=not-regenerable\n", encoding="utf-8")  # ignored, not regenerable
    for regenerable in (".venv/bin/python", "pkg/__pycache__/m.pyc"):  # ignored and regenerable: no reason to keep
        (wts["merged"] / regenerable).parent.mkdir(parents=True)
        (wts["merged"] / regenerable).write_text("x", encoding="utf-8")
    git(main, "worktree", "lock", str(wts["locked"]))
    git(main, "branch", "worktree-agent-gone", "integ")  # merged, no worktree
    git(main, "branch", "worktree-agent-open", "worktree-agent-unmerged")  # unmerged, no worktree
    for case, wt in wts.items():
        if case != "fresh":
            age(wt)
    return main, wts


def test_the_dry_run_lists_only_the_merged_clean_idle_worktree_and_removes_nothing(repo):
    main, wts = repo
    r = run(main)
    assert r.returncode == 0, r.stderr
    remove = [line for line in r.stdout.splitlines() if line.startswith("remove")]
    assert len(remove) == 1 and str(wts["merged"]) in remove[0], r.stdout
    assert any(line.startswith("delete branch worktree-agent-gone") for line in r.stdout.splitlines())
    assert "--apply" in r.stdout
    assert all(wt.is_dir() for wt in wts.values())
    assert "worktree-agent-merged" in git(main, "branch", "--list", "worktree-agent-merged")


def test_every_kept_worktree_is_listed_with_its_reason(repo):
    main, wts = repo
    out = run(main).stdout
    reasons = {"dirty": "uncommitted or untracked", "unmerged": "not merged into integ", "locked": "locked",
               "fresh": "used in the last 24 h", "detached": "detached", "secret": "ignored files: .env",
               "empty": "no commits of its own"}
    for case, reason in reasons.items():
        line = next(line for line in out.splitlines() if line.startswith("keep") and str(wts[case]) in line)
        assert reason in line, line
    assert any(line.startswith("keep") and str(main) in line and "main checkout" in line for line in out.splitlines())


def test_apply_removes_the_merged_worktree_and_its_branch_and_leaves_everything_else(repo):
    main, wts = repo
    assert run(main).returncode == 0  # a dry run first must not make the worktree look recently used
    r = run(main, "--apply")
    assert r.returncode == 0, r.stderr
    assert not wts["merged"].exists()
    assert git(main, "branch", "--list", "worktree-agent-merged", "worktree-agent-gone") == ""
    for case in ("dirty", "unmerged", "locked", "fresh", "detached", "secret", "empty"):
        assert wts[case].is_dir(), case
    assert (wts["dirty"] / "scratch.py").is_file()
    assert (wts["secret"] / ".env").is_file()  # `git worktree remove` would delete an ignored file silently
    assert git(main, "branch", "--list", "worktree-agent-open").strip()  # unmerged branch kept
    assert git(main, "branch", "--list", "worktree-agent-dirty").strip()  # merged but checked out: kept
    assert main.is_dir() and git(main, "rev-parse", "--abbrev-ref", "HEAD").strip() == "integ"


def test_a_merged_idle_lane_holding_an_ignored_env_file_is_kept_and_the_dry_run_says_why(repo):
    main, wts = repo
    out = run(main).stdout
    line = next(line for line in out.splitlines() if str(wts["secret"]) in line)
    assert line.startswith("keep") and ".env" in line


def test_an_idle_lane_with_no_commits_of_its_own_is_kept_until_min_age_is_zero(repo):
    """A read-only reviewer lane never commits: its tip sits on the integration line and looks merged."""
    main, wts = repo
    assert run(main, "--apply").returncode == 0
    assert wts["empty"].is_dir()
    removed = "\n".join(line for line in run(main, "--min-age-hours", "0").stdout.splitlines()
                        if line.startswith("remove"))
    assert str(wts["empty"]) in removed


def test_min_age_zero_lets_a_freshly_used_merged_worktree_go(repo):
    main, wts = repo
    r = run(main, "--min-age-hours", "0")
    assert str(wts["fresh"]) in "\n".join(line for line in r.stdout.splitlines() if line.startswith("remove"))


def test_the_current_worktree_is_never_removed_even_when_merged_and_idle(repo):
    _, wts = repo
    r = run(wts["merged"], "--apply")
    assert r.returncode == 0, r.stderr
    assert wts["merged"].is_dir()
    assert any(line.startswith("keep") and "current" in line for line in r.stdout.splitlines())


def test_an_unknown_target_branch_or_option_is_refused(repo):
    main, _ = repo
    assert subprocess.run(["bash", str(SCRIPT), "--into", "nope"], cwd=main, capture_output=True).returncode == 2
    assert run(main, "--force").returncode == 2


def test_the_script_never_pipes_into_an_early_exiting_grep_under_pipefail():
    """`printf ... | grep -q` can kill the writer with SIGPIPE, and pipefail then turns a match into a miss."""
    code = [line for line in SCRIPT.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if "|" in line and "grep -" in line and "q" in line.split("grep -")[1][:4]]


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed (CI runs it in pre-commit)")
def test_the_script_is_shellcheck_clean():
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
