"""scripts/prune_worktrees.sh: remove lane worktrees whose work is merged, and nothing else.

Design rule (docs/process/tech-debt.md TD-8, ROADMAP 5a FR-4): a lane's worktree goes once its branch is merged
into the base (origin/main by default) and it holds nothing but regenerable build and test leftovers; the main
checkout, the current tree, a detached, locked, dirty, unmerged or recently used worktree is never touched, and
nothing is removed without `--apply` (dry run by default).
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

CASES = ("merged", "dirty", "unmerged", "locked", "fresh", "detached", "secret", "empty", "leftovers", "modified",
         "touched")
LEFTOVERS = ("data/bars/XAUUSD_1h.parquet", "state/core.db", "goldbot.egg-info/PKG-INFO", "web/dist/index.html",
             "web/node_modules/x/index.js", ".pytest_cache/v/x")


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
    """Make a worktree look untouched for two days (its HEAD, index and reflog, and every file in its tree)."""
    gitdir = Path(git(wt, "rev-parse", "--absolute-git-dir").strip())
    old = time.time() - TWO_DAYS
    for f in (gitdir / "HEAD", gitdir / "index", gitdir / "logs" / "HEAD", *wt.rglob("*")):
        if f.exists():
            os.utime(f, (old, old))


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(root: Path, *args: str, into: str | None = "integ") -> subprocess.CompletedProcess[str]:
    base = ["--into", into] if into else []
    return subprocess.run(["bash", str(SCRIPT), *base, *args], cwd=root, capture_output=True, text=True)


def removed(out: str) -> list[str]:
    return sorted(line.split()[2] for line in out.splitlines() if line.startswith("remove"))


@pytest.fixture
def repo(tmp_path, git_env):
    """The main checkout on `integ` plus one worktree per case; returns (main, {case: path})."""
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "integ")
    (main / ".gitignore").write_text(".env\n.venv/\n__pycache__/\n/data/\n/state/\n", encoding="utf-8")
    commit(main, "base")
    wts: dict[str, Path] = {}
    for case in CASES:
        wt = tmp_path / f"wt-{case}"
        if case == "detached":
            git(main, "worktree", "add", "-q", "--detach", str(wt), "integ")
        else:
            git(main, "worktree", "add", "-q", "-b", f"worktree-agent-{case}", str(wt), "integ")
        wts[case] = wt
    for case in ("merged", "dirty", "fresh", "secret", "leftovers", "modified", "touched"):  # merged --no-ff
        commit(wts[case], f"{case} lane work")
        git(main, "merge", "-q", "--no-ff", "--no-edit", f"worktree-agent-{case}")
    commit(wts["unmerged"], "not merged yet")  # an unpushed commit: not on the base
    write(wts["dirty"] / "scratch.py", "x = 1\n")  # real untracked work
    write(wts["modified"] / "modified lane work.txt", "edited, not committed")  # a modified tracked file
    write(wts["secret"] / ".env", "TOKEN=not-regenerable\n")  # ignored, not regenerable
    for regenerable in (".venv/bin/python", "pkg/__pycache__/m.pyc"):  # ignored and regenerable: no reason to keep
        write(wts["merged"] / regenerable)
    # build and test leftovers only, ignored (data/, state/) or not even ignored (*.egg-info, web/dist): regenerable
    for leftover in LEFTOVERS:
        write(wts["leftovers"] / leftover)
    git(main, "worktree", "lock", str(wts["locked"]))
    git(main, "branch", "worktree-agent-gone", "integ")  # merged, no worktree
    git(main, "branch", "worktree-agent-open", "worktree-agent-unmerged")  # unmerged, no worktree
    for case, wt in wts.items():
        if case != "fresh":
            age(wt)
    write(wts["touched"] / ".pytest_cache" / "lastfailed", "{}")  # a test run minutes ago: a leftover, but fresh
    return main, wts


def test_the_dry_run_lists_only_the_merged_clean_idle_worktrees_and_removes_nothing(repo):
    main, wts = repo
    r = run(main)
    assert r.returncode == 0, r.stderr
    assert removed(r.stdout) == sorted([str(wts["merged"]), str(wts["leftovers"])]), r.stdout
    assert any(line.startswith("delete branch worktree-agent-gone") for line in r.stdout.splitlines())
    assert "--apply" in r.stdout and "would go" in r.stdout
    assert all(wt.is_dir() for wt in wts.values())
    assert "worktree-agent-merged" in git(main, "branch", "--list", "worktree-agent-merged")


def test_every_kept_worktree_is_listed_with_its_reason_and_a_summary(repo):
    main, wts = repo
    out = run(main).stdout
    reasons = {"dirty": "uncommitted or untracked files: scratch.py", "unmerged": "not merged into integ",
               "locked": "locked", "fresh": "used in the last 24 h", "detached": "detached",
               "secret": "ignored files: .env", "empty": "no commits of its own", "modified": "modified lane work.txt",
               "touched": "used in the last 24 h"}
    for case, reason in reasons.items():
        line = next(line for line in out.splitlines() if line.startswith("keep") and str(wts[case]) in line)
        assert reason in line, line
    assert any(line.startswith("keep") and str(main) in line and "main checkout" in line for line in out.splitlines())
    assert "kept 10 worktree(s)" in out  # 9 cases + the main checkout


def test_sizes_are_printed_for_every_listed_worktree(repo):
    main, _ = repo
    out = run(main).stdout
    rows = [line.split() for line in out.splitlines() if line.startswith(("keep", "remove"))]
    assert rows and all(row[1][-1] in "KMG" and row[1][:-1].replace(".", "").isdigit() for row in rows), out
    assert "would go" in out.splitlines()[-1] and "(" in out.splitlines()[-1]


def test_apply_removes_merged_leftover_only_worktrees_and_their_branches_and_leaves_everything_else(repo):
    main, wts = repo
    assert run(main).returncode == 0  # a dry run first must not make the worktree look recently used
    r = run(main, "--apply")
    assert r.returncode == 0, r.stderr
    assert not wts["merged"].exists() and not wts["leftovers"].exists()
    assert git(main, "branch", "--list", "worktree-agent-merged", "worktree-agent-gone",
               "worktree-agent-leftovers") == ""
    assert str(wts["leftovers"]) not in git(main, "worktree", "list")
    for case in CASES:
        if case not in ("merged", "leftovers"):
            assert wts[case].is_dir(), case
    assert (wts["dirty"] / "scratch.py").is_file()
    assert (wts["secret"] / ".env").is_file()  # `git worktree remove` would delete an ignored file silently
    assert (wts["modified"] / "modified lane work.txt").read_text(encoding="utf-8") == "edited, not committed"
    assert git(main, "branch", "--list", "worktree-agent-open").strip()  # unmerged branch kept
    assert git(main, "branch", "--list", "worktree-agent-unmerged").strip()  # unpushed commits kept
    assert git(main, "branch", "--list", "worktree-agent-dirty").strip()  # merged but checked out: kept
    assert main.is_dir() and git(main, "rev-parse", "--abbrev-ref", "HEAD").strip() == "integ"


def test_a_merged_idle_lane_holding_an_ignored_env_file_is_kept_and_the_dry_run_says_why(repo):
    main, wts = repo
    out = run(main).stdout
    line = next(line for line in out.splitlines() if str(wts["secret"]) in line)
    assert line.startswith("keep") and ".env" in line


def test_a_file_written_recently_keeps_an_otherwise_idle_merged_lane(repo):
    """A lane running tests or editing files has not committed yet: its git metadata looks idle, its tree does not."""
    main, wts = repo
    assert run(main, "--apply").returncode == 0
    assert (wts["touched"] / ".pytest_cache" / "lastfailed").is_file()
    assert str(wts["touched"]) in removed(run(main, "--min-age-hours", "0").stdout)


def test_an_idle_lane_with_no_commits_of_its_own_is_kept_until_asked(repo):
    """A read-only reviewer lane never commits: its tip sits on the base line and looks merged."""
    main, wts = repo
    assert run(main, "--apply").returncode == 0
    assert wts["empty"].is_dir()
    assert str(wts["empty"]) in removed(run(main, "--min-age-hours", "0").stdout)
    out = removed(run(main, "--include-empty").stdout)
    assert str(wts["empty"]) in out and str(wts["fresh"]) not in out


def test_min_age_zero_lets_a_freshly_used_merged_worktree_go(repo):
    main, wts = repo
    assert str(wts["fresh"]) in removed(run(main, "--min-age-hours", "0").stdout)


def test_the_current_worktree_is_never_removed_even_when_merged_and_idle(repo):
    _, wts = repo
    r = run(wts["merged"], "--apply")
    assert r.returncode == 0, r.stderr
    assert wts["merged"].is_dir()
    assert any(line.startswith("keep") and "current" in line for line in r.stdout.splitlines())


def test_the_default_base_is_origin_main_and_unpushed_lane_commits_are_kept(tmp_path, git_env):
    """No --into: the base is origin/main (fetched), never the dead integration branch of 10-19."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    commit(main, "base")
    git(main, "remote", "add", "origin", str(remote))
    git(main, "push", "-q", "origin", "main")
    done, open_ = tmp_path / "wt-done", tmp_path / "wt-open"
    for wt, name in ((done, "done"), (open_, "open")):
        git(main, "worktree", "add", "-q", "-b", f"worktree-agent-{name}", str(wt), "main")
        commit(wt, f"{name} work")
    git(main, "merge", "-q", "--no-ff", "--no-edit", "worktree-agent-done")
    git(main, "push", "-q", "origin", "main")
    git(main, "merge", "-q", "--no-ff", "--no-edit", "worktree-agent-open")  # merged locally, never pushed
    age(done)
    age(open_)
    r = run(main, into=None)
    assert r.returncode == 0, r.stderr
    assert removed(r.stdout) == [str(done)], r.stdout
    line = next(line for line in r.stdout.splitlines() if str(open_) in line)
    assert line.startswith("keep") and "not merged into origin/main" in line


def test_an_unknown_target_branch_or_option_is_refused(repo):
    main, _ = repo
    assert subprocess.run(["bash", str(SCRIPT), "--into", "nope"], cwd=main, capture_output=True).returncode == 2
    assert run(main, "--force").returncode == 2


def test_the_script_never_pipes_into_an_early_exiting_grep_under_pipefail():
    """`printf ... | grep -q` can kill the writer with SIGPIPE, and pipefail then turns a match into a miss."""
    code = [line for line in SCRIPT.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if "|" in line and "grep -" in line and "q" in line.split("grep -")[1][:4]]


def test_no_script_defaults_to_the_dead_integration_branch():
    assert "gifted-goldberg" not in SCRIPT.read_text(encoding="utf-8")
    assert "gifted-goldberg" not in (ROOT / "scripts" / "new_lane.sh").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed (CI runs it in pre-commit)")
def test_the_script_is_shellcheck_clean():
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
