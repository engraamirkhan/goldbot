"""FR-6/FR-7/FR-8/FR-10 (ADR 0060): scripts/lib/common.sh and scripts/release.sh. The lane base is origin/main (no script
names the dead integration branch), a PR under 50 changed lines is refused unless it is a hotfix, and every release
writes one timing record under ~/tmp so the cost per merged PR can be measured."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / "scripts" / "lib" / "common.sh"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def sh(script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k != "INTEGRATION_BRANCH"}
    return subprocess.run(["bash", "-c", f". {COMMON}; {script}"], cwd=cwd, capture_output=True, text=True,
                          env={**base, **GIT_ENV, **env})


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo whose `origin/main` ref exists and whose branch `lane` is ahead of it."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("base\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "base")
    git(r, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(r, "checkout", "-q", "-b", "lane")
    return r


def commit_lines(repo: Path, n: int, name: str = "b.txt") -> None:
    (repo / name).write_text("".join(f"line {i}\n" for i in range(n)))
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", f"{n} lines")


def test_the_lane_base_defaults_to_origin_main_and_can_be_overridden():
    assert sh('echo "$GOLDBOT_BASE"', ROOT).stdout.strip() == "origin/main"
    assert sh('echo "$GOLDBOT_BASE"', ROOT, INTEGRATION_BRANCH="other").stdout.strip() == "other"


def test_no_script_still_defaults_to_the_dead_integration_branch():
    paths = [*sorted((ROOT / "scripts").glob("*.sh")), COMMON]
    assert [p.name for p in paths if "gifted-goldberg" in p.read_text() and p.name != "prune_worktrees.sh"] == []


def test_changed_lines_counts_insertions_and_deletions_against_the_base(repo: Path):
    commit_lines(repo, 12)
    assert sh("goldbot_changed_lines", repo).stdout.strip() == "12"


def test_a_pr_under_50_changed_lines_is_refused_with_the_reason_and_the_hotfix_way_out(repo: Path):
    commit_lines(repo, 49)
    r = sh("goldbot_pr_size_check 0", repo)
    assert r.returncode == 1 and "49" in r.stderr and "hotfix" in r.stderr and "50" in r.stderr


def test_a_pr_of_exactly_50_changed_lines_is_accepted(repo: Path):
    commit_lines(repo, 50)
    assert sh("goldbot_pr_size_check 0", repo).returncode == 0


def test_a_hotfix_under_50_lines_is_accepted(repo: Path):
    commit_lines(repo, 3)
    assert sh("goldbot_pr_size_check 1", repo).returncode == 0


def test_the_minimum_can_be_lowered_only_through_the_named_variable(repo: Path):
    commit_lines(repo, 10)
    assert sh("goldbot_pr_size_check 0", repo, RELEASE_MIN_LINES="5").returncode == 0


def test_a_timing_record_is_one_json_line_with_numbers_as_numbers(tmp_path: Path):
    f = tmp_path / "timings.jsonl"
    sh(f'goldbot_timing_append {f} branch=lane/x pr=17 lane_start=100 pushed=160 merged=700 outcome=merged', tmp_path)
    sh(f'goldbot_timing_append {f} branch=lane/y outcome=failed', tmp_path)
    first, second = (json.loads(line) for line in f.read_text().splitlines())
    assert first == {"branch": "lane/x", "pr": 17, "lane_start": 100, "pushed": 160, "merged": 700, "outcome": "merged"}
    assert second == {"branch": "lane/y", "outcome": "failed"}


def test_release_sh_uses_the_base_the_size_rule_and_the_timing_log():
    text = (ROOT / "scripts" / "release.sh").read_text()
    assert "lib/common.sh" in text and "goldbot_pr_size_check" in text and "goldbot_timing_append" in text
    assert "--hotfix" in text and "origin/main" in text
    assert "gifted-goldberg" not in text
    # the size rule runs before anything is pushed
    assert text.index("goldbot_pr_size_check") < text.index("git push")
