"""scripts/lane_test.sh: argument handling, seeding from the shared testmon DB, and the iter command (no -m)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "lane_test.sh"


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo holding a copy of the script, so the script's own `cd` lands there."""
    r = tmp_path / "repo"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, r / "scripts" / "lane_test.sh")
    _git(r, "init", "-q", "-b", "integration")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "base")
    return r


def run(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k not in ("GATES_WORKERS", "LANE_BASE")}
    full = {**base, "TESTMON_SHARED": str(repo.parent / "shared"), "INTEGRATION_BRANCH": "integration",
            "LANE_TEST_DRY_RUN": "1", **env}
    return subprocess.run(["bash", str(repo / "scripts" / "lane_test.sh"), *args], capture_output=True, text=True,
                          env=full)


@pytest.mark.parametrize("args", [(), ("bogus",), ("iter", "extra")])
def test_bad_arguments_print_usage_and_exit_2(repo: Path, args: tuple[str, ...]) -> None:
    p = run(repo, *args)
    assert p.returncode == 2
    assert "seed" in p.stderr and "refresh" in p.stderr


def test_help_exits_2_with_usage(repo: Path) -> None:
    assert run(repo, "--help").returncode == 2


def test_iter_runs_testmon_without_marker_selection(repo: Path) -> None:
    p = run(repo, "iter")
    assert p.returncode == 0
    assert p.stdout.strip() == "pytest --testmon -n 2 -q"
    assert "-m" not in p.stdout.split()


def test_iter_takes_workers_from_gates_workers(repo: Path) -> None:
    assert run(repo, "iter", GATES_WORKERS="6").stdout.strip() == "pytest --testmon -n 6 -q"


def test_final_points_at_the_full_gates(repo: Path) -> None:
    p = run(repo, "final")
    assert p.returncode == 0 and "scripts/gates.sh" in p.stdout


def test_seed_without_a_shared_db_says_the_first_run_is_full(repo: Path) -> None:
    p = run(repo, "seed")
    assert p.returncode == 0 and "full suite" in p.stdout
    assert not (repo / ".testmondata").exists()


def test_seed_copies_the_db_for_the_lanes_base_commit(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "lane")
    (repo / "x.txt").write_text("lane work")
    _git(repo, "add", "x.txt")
    _git(repo, "commit", "-q", "-m", "lane")
    shared = repo.parent / "shared" / base
    shared.mkdir(parents=True)
    (shared / ".testmondata").write_bytes(b"db-at-base")
    p = run(repo, "seed")
    assert p.returncode == 0 and base in p.stdout
    assert (repo / ".testmondata").read_bytes() == b"db-at-base"


def test_seed_with_an_unresolvable_base_fails(repo: Path) -> None:
    p = run(repo, "seed", LANE_BASE="no-such-ref")
    assert p.returncode == 1 and "LANE_BASE" in p.stderr


def test_refresh_refuses_a_modified_checkout(repo: Path) -> None:
    (repo / "scripts" / "lane_test.sh").write_text((repo / "scripts" / "lane_test.sh").read_text() + "\n# edit\n")
    p = run(repo, "refresh")
    assert p.returncode == 1 and "clean checkout" in p.stderr


def test_refresh_publishes_under_the_head_sha(repo: Path) -> None:
    sha = _git(repo, "rev-parse", "HEAD")
    p = run(repo, "refresh")
    assert p.returncode == 0
    assert f"shared/{sha}/.testmondata" in p.stdout
