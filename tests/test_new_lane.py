"""scripts/new_lane.sh: a lane worktree from origin/main, refused above the worktree cap or below the free-disk floor
(ROADMAP 5a FR-4: 373 worktrees and 168 GiB on 10-30)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "new_lane.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull,
                 "GIT_CONFIG_NOSYSTEM": "1", "LANE_MIN_FREE_GB": "0"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LANE_MAX_WORKTREES", raising=False)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    (main / "a.txt").write_text("a", encoding="utf-8")
    git(main, "add", "-A")
    git(main, "commit", "-q", "-m", "base")
    git(main, "remote", "add", "origin", str(remote))
    git(main, "push", "-q", "origin", "main")
    git(main, "checkout", "-q", "-b", "old-integration")
    (main / "b.txt").write_text("b", encoding="utf-8")
    git(main, "add", "-A")
    git(main, "commit", "-q", "-m", "not on main")
    return main


def run(root: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=root, capture_output=True, text=True,
                          env={**os.environ, **env})


def test_a_lane_is_created_from_origin_main_whatever_the_current_branch(repo):
    r = run(repo, "fr4-demo")
    assert r.returncode == 0, r.stderr
    wt = Path(r.stdout.strip().splitlines()[-1])
    assert wt == repo / ".claude" / "worktrees" / "fr4-demo" and wt.is_dir()
    assert git(wt, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/main")
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "worktree-fr4-demo"
    assert not (wt / "b.txt").exists()


def test_the_worktree_cap_refuses_and_prints_the_prune_command(repo):
    assert run(repo, "one", LANE_MAX_WORKTREES="3").returncode == 0
    assert run(repo, "two", LANE_MAX_WORKTREES="3").returncode == 0  # main + 2 = 3 now
    r = run(repo, "three", LANE_MAX_WORKTREES="3")
    assert r.returncode == 3
    assert "3 worktrees exist (limit 3)" in r.stderr and "prune_worktrees.sh" in r.stderr
    assert not (repo / ".claude" / "worktrees" / "three").exists()


def test_the_default_cap_is_40(repo):
    for i in range(39):
        git(repo, "worktree", "add", "-q", "--detach", str(repo.parent / f"wt{i}"), "main")
    r = run(repo, "forty-first")
    assert r.returncode == 3 and "limit 40" in r.stderr


def test_too_little_free_disk_refuses(repo):
    r = run(repo, "big", LANE_MIN_FREE_GB="100000000")
    assert r.returncode == 3
    assert "GB free" in r.stderr and "prune_worktrees.sh" in r.stderr
    assert not (repo / ".claude" / "worktrees" / "big").exists()


@pytest.mark.parametrize("name", ["", "../escape", "-rf", "a b", "x/y"])
def test_a_bad_lane_name_is_refused(repo, name):
    assert run(repo, name).returncode == 2


def test_an_existing_lane_is_not_overwritten(repo):
    assert run(repo, "dup").returncode == 0
    assert run(repo, "dup").returncode == 2


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed (CI runs it in pre-commit)")
def test_the_script_is_shellcheck_clean():
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
