"""scripts/docs_assemble.py integration chores: the rendered-docs merge driver and ADR number reservation.

Design rule (docs/process/throughput-plan.md lever 4, docs/process/tech-debt.md TD-7, ADR 0025 amending 0012):
"no rendered file ever conflicts" -- a plain `git merge`, `rebase` or `cherry-pick` re-renders HANDOFF.md,
docs/TRACEABILITY.md and docs/BACKLOG.md from the merged fragments, while a real fragment conflict still fails
loudly; and `new-adr` reserves the next ADR number under a lock in the git common dir, so two worktrees never take
the same number, honouring the numbers the CTO register reserved.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.test_docs_assemble import ROOT, commit_all, da, git, old_repo, read

SCRIPT = ROOT / "scripts" / "docs_assemble.py"
ATTRIBUTES = "".join(f"{rel} merge=goldbot-docs\n" for rel in ("HANDOFF.md", "docs/TRACEABILITY.md",
                                                                 "docs/BACKLOG.md"))


@pytest.fixture
def git_env(monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull,
                 "GIT_CONFIG_NOSYSTEM": "1"}.items():
        monkeypatch.setenv(k, v)


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def driver_repo(root: Path) -> Path:
    """A migrated repo with the script, the .gitattributes routing and the driver installed (as a lane would)."""
    old_repo(root)
    assert da.main(["--root", str(root), "migrate"]) == 0
    (root / "scripts").mkdir()
    shutil.copy(SCRIPT, root / "scripts" / "docs_assemble.py")
    (root / ".gitattributes").write_text(ATTRIBUTES, encoding="utf-8")
    commit_all(root, "migrate")
    assert da.main(["--root", str(root), "install-merge-driver"]) == 0
    return root


def lane(root: Path, name: str, prefix: str, base: str = "main") -> None:
    git(root, "checkout", "-q", "-b", name, base)
    da.cmd_new_row(root, prefix, name, {"requirement": name, "status": "missing", "code": "c", "test": "t"})
    frag = da.cmd_new_handoff(root, name, "status")
    frag.write_text(f"- {name} done.\n", encoding="utf-8")
    da.cmd_render(root)
    commit_all(root, name)


def conflicted(root: Path) -> set[str]:
    return set(git(root, "diff", "--name-only", "--diff-filter=U").split())


# ------------------------------------------------------------------------------------------------- merge driver

def test_the_repo_routes_the_three_rendered_docs_to_the_goldbot_docs_merge_driver():
    lines = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for rel in da.RENDERED:
        assert any(line.split()[0] == rel and "merge=goldbot-docs" in line.split() for line in lines if line.strip()
                   and not line.startswith("#")), rel


def test_install_merge_driver_writes_the_driver_into_the_local_git_config(tmp_path, git_env):
    root = driver_repo(tmp_path)
    driver = git(root, "config", "--get", "merge.goldbot-docs.driver").strip()
    assert "scripts/docs_assemble.py merge-driver %O %A %B %P %S %X %Y" in driver
    assert git(root, "config", "--get", "merge.goldbot-docs.name").strip()


def test_two_lanes_that_both_add_fragments_merge_with_plain_git_and_the_rendered_docs_are_re_rendered(
        tmp_path, git_env):
    root = driver_repo(tmp_path)
    lane(root, "lane-a", "R")
    lane(root, "lane-b", "D")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "--no-edit", "lane-a")
    r = run_git(root, "merge", "--no-ff", "--no-edit", "lane-b")
    assert r.returncode == 0, r.stdout + r.stderr
    handoff, trace = read(root, "HANDOFF.md"), read(root, "docs/TRACEABILITY.md")
    assert "- lane-a done." in handoff and "- lane-b done." in handoff
    assert "| R3 | lane-a |" in trace and "| D2 | lane-b |" in trace
    assert "<<<<<<<" not in handoff + trace
    assert da.main(["--root", str(root), "check"]) == 0  # the merge commit holds a fresh render
    assert git(root, "status", "--porcelain") == ""


def test_rebasing_and_cherry_picking_a_lane_over_another_re_render_the_docs_too(tmp_path, git_env):
    root = driver_repo(tmp_path)
    lane(root, "lane-a", "R")
    lane(root, "lane-b", "D", base="main")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--ff-only", "lane-a")
    git(root, "branch", "pick", "main")
    original = git(root, "rev-parse", "lane-b").strip()  # its parent is the old main: a real three-way pick
    r = run_git(root, "rebase", "main", "lane-b")
    assert r.returncode == 0, r.stdout + r.stderr
    assert da.main(["--root", str(root), "check"]) == 0
    assert "- lane-a done." in read(root, "HANDOFF.md") and "- lane-b done." in read(root, "HANDOFF.md")
    git(root, "checkout", "-q", "pick")
    r = run_git(root, "cherry-pick", original)
    assert r.returncode == 0, r.stdout + r.stderr
    assert da.main(["--root", str(root), "check"]) == 0
    assert "| D2 | lane-b |" in read(root, "docs/TRACEABILITY.md")


def test_a_fragment_both_lanes_edited_still_conflicts_and_leaves_the_rendered_doc_conflicted(tmp_path, git_env):
    root = driver_repo(tmp_path)
    frag = da.cmd_new_handoff(root, "shared", "status")
    frag.write_text("- Shared note.\n", encoding="utf-8")
    da.cmd_render(root)
    commit_all(root, "shared fragment")
    for name in ("lane-a", "lane-b"):
        git(root, "checkout", "-q", "-b", name, "main")
        frag.write_text(f"- Shared note, edited by {name}.\n", encoding="utf-8")
        da.cmd_render(root)
        commit_all(root, name)
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--ff-only", "lane-a")
    r = run_git(root, "merge", "--no-edit", "lane-b")
    assert r.returncode != 0
    rel = frag.relative_to(root).as_posix()
    assert conflicted(root) == {rel, "HANDOFF.md"}
    assert rel in r.stdout + r.stderr  # the driver names the fragment to resolve


def test_a_fragment_both_sides_edited_on_different_lines_merges_like_git_and_the_docs_re_render(tmp_path, git_env):
    """Real case 2026-10-16 (merge 3e1ee0a): a lane based before .gitattributes existed edited BACKLOG item 1's
    detail while the integration branch edited its status; git merged the fragment cleanly, but the driver called
    it a conflict and left HANDOFF.md and BACKLOG.md conflicted."""
    root = old_repo(tmp_path)
    assert da.main(["--root", str(root), "migrate"]) == 0
    item = next((root / "docs" / "backlog.d").glob("01-*.md"))
    item.write_text(item.read_text(encoding="utf-8") + "Line two.\nLine three.\n", encoding="utf-8")
    da.cmd_render(root)
    commit_all(root, "migrate")
    git(root, "checkout", "-q", "-b", "lane-old")  # predates the driver's .gitattributes
    item.write_text(item.read_text(encoding="utf-8").replace("Line three.", "Line three, by the lane."),
                    encoding="utf-8")
    frag = da.cmd_new_handoff(root, "lane-old", "status")
    frag.write_text("- lane-old done.\n", encoding="utf-8")
    da.cmd_render(root)
    commit_all(root, "lane edits the detail")
    git(root, "checkout", "-q", "main")
    (root / "scripts").mkdir()
    shutil.copy(SCRIPT, root / "scripts" / "docs_assemble.py")
    (root / ".gitattributes").write_text(ATTRIBUTES, encoding="utf-8")
    item.write_text(item.read_text(encoding="utf-8").replace("status: done", "status: shipped"), encoding="utf-8")
    other = da.cmd_new_handoff(root, "main-side", "status")
    other.write_text("- main side done.\n", encoding="utf-8")
    da.cmd_render(root)
    commit_all(root, "driver and a status edit")
    assert da.main(["--root", str(root), "install-merge-driver"]) == 0
    r = run_git(root, "merge", "--no-edit", "lane-old")
    assert r.returncode == 0, r.stdout + r.stderr
    backlog, handoff = read(root, "docs/BACKLOG.md"), read(root, "HANDOFF.md")
    assert "| 1 | One | FIND | M | shipped |" in backlog and "Line three, by the lane." in backlog
    assert "- lane-old done." in handoff and "- main side done." in handoff
    assert da.main(["--root", str(root), "check"]) == 0


def test_two_lanes_that_took_the_same_id_leave_the_rendered_doc_conflicted_with_the_fix_ids_hint(
        tmp_path, git_env):
    root = driver_repo(tmp_path)
    lane(root, "lane-a", "R")
    lane(root, "lane-b", "R")  # both took R3
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--ff-only", "lane-a")
    r = run_git(root, "merge", "--no-edit", "lane-b")
    assert r.returncode != 0
    assert conflicted(root) == {"docs/TRACEABILITY.md"}
    assert "fix-ids" in r.stdout + r.stderr


def test_the_driver_falls_back_to_a_text_merge_when_it_cannot_tell_which_commits_are_merged(tmp_path, git_env):
    root = driver_repo(tmp_path)
    base, ours, theirs = (tmp_path / n for n in ("o", "a", "b"))
    base.write_text("x\ny\nz\n", encoding="utf-8")
    ours.write_text("x1\ny\nz\n", encoding="utf-8")
    theirs.write_text("x\ny\nz1\n", encoding="utf-8")
    code = da.main(["--root", str(root), "merge-driver", str(base), str(ours), str(theirs), "HANDOFF.md",
                    "Stash base", "Updated upstream", "Stashed changes"])
    assert code == 0
    assert ours.read_text(encoding="utf-8") == "x1\ny\nz1\n"


# ------------------------------------------------------------------------------------------- ADR reservation

def adr_repo(root: Path, reserved: list[int] | None = None) -> Path:
    root.mkdir(exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    d = root / "docs" / "decisions"
    d.mkdir(parents=True)
    (d / "README.md").write_text("# Decision records\n", encoding="utf-8")
    (d / "0001-first.md").write_text("# 0001: First\n", encoding="utf-8")
    (d / "0002-second.md").write_text("# 0002: Second\n", encoding="utf-8")
    if reserved:
        entries = [{"number": n, "slug": f"held-{n}", "by": "cto register"} for n in reserved]
        (d / "reserved.yaml").write_text(yaml.safe_dump({"reserved": entries}), encoding="utf-8")
    commit_all(root, "adrs")
    return root


def adr_number(path: Path) -> int:
    return int(path.name[:4])


def test_new_adr_takes_one_past_the_highest_number_and_writes_a_proposed_template(tmp_path, git_env):
    root = adr_repo(tmp_path)
    path = da.cmd_new_adr(root, "use-a-lock", "Use a lock", role="implementer", today=da.dt.date(2026, 10, 17))
    assert path == root / "docs" / "decisions" / "0003-use-a-lock.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# 0003: Use a lock\n\nStatus: proposed (implementer, 2026-10-17)\n")
    for heading in ("## Context", "## Decision", "## Consequences"):
        assert heading in text


def test_new_adr_skips_the_numbers_the_register_reserved(tmp_path, git_env):
    root = adr_repo(tmp_path, reserved=[3, 4])
    assert adr_number(da.cmd_new_adr(root, "next", "Next")) == 5


def test_new_adr_claims_a_reserved_number_only_when_asked_and_only_once(tmp_path, git_env):
    root = adr_repo(tmp_path, reserved=[3, 4])
    assert adr_number(da.cmd_new_adr(root, "held-4", "Held four", number=4)) == 4
    with pytest.raises(da.DocsError, match="taken"):
        da.cmd_new_adr(root, "again", "Again", number=4)
    with pytest.raises(da.DocsError, match="not reserved"):
        da.cmd_new_adr(root, "free-pick", "Free pick", number=9)


def test_two_worktrees_of_one_repo_never_take_the_same_adr_number(tmp_path, git_env):
    root = adr_repo(tmp_path / "main")
    other = tmp_path / "wt"
    git(root, "worktree", "add", "-q", "-b", "lane", str(other))
    first = da.cmd_new_adr(root, "from-main", "From main")
    second = da.cmd_new_adr(other, "from-lane", "From lane")  # the first is uncommitted and unseen in this tree
    assert (adr_number(first), adr_number(second)) == (3, 4)


def test_parallel_new_adr_processes_take_distinct_numbers(tmp_path, git_env):
    root = adr_repo(tmp_path / "main")
    trees = [root]
    for n in range(2):
        wt = tmp_path / f"wt{n}"
        git(root, "worktree", "add", "-q", "-b", f"lane{n}", str(wt))
        trees.append(wt)
    procs = [subprocess.Popen([sys.executable, str(SCRIPT), "--root", str(trees[i % 3]), "new-adr", "--slug",
                               f"p{i}", "--title", f"P{i}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for i in range(6)]
    outs = [p.communicate() for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    numbers = [int(Path(out.split()[0]).name[:4]) for out, _ in outs]
    assert sorted(numbers) == [3, 4, 5, 6, 7, 8]


def test_new_adr_counts_numbers_committed_on_any_local_branch(tmp_path, git_env):
    root = adr_repo(tmp_path)
    git(root, "checkout", "-q", "-b", "elsewhere")
    (root / "docs" / "decisions" / "0007-on-a-branch.md").write_text("# 0007\n", encoding="utf-8")
    commit_all(root, "adr on a branch")
    git(root, "checkout", "-q", "main")
    assert adr_number(da.cmd_new_adr(root, "after", "After")) == 8


def test_new_adr_counts_numbers_on_remote_tracking_branches_too(tmp_path, git_env):
    root = adr_repo(tmp_path)
    git(root, "checkout", "-q", "-b", "fetched")
    (root / "docs" / "decisions" / "0009-pushed-elsewhere.md").write_text("# 0009\n", encoding="utf-8")
    commit_all(root, "adr from another machine")
    git(root, "update-ref", "refs/remotes/origin/fetched", "fetched")
    git(root, "checkout", "-q", "main")
    git(root, "branch", "-q", "-D", "fetched")  # only the remote-tracking ref holds it now
    assert adr_number(da.cmd_new_adr(root, "after-remote", "After remote")) == 10


def test_check_fails_on_two_adr_files_with_one_number(tmp_path, capsys):
    root = Path(tmp_path)
    d = root / "docs" / "decisions"
    d.mkdir(parents=True)
    (d / "0021-one.md").write_text("# 0021\n", encoding="utf-8")
    (d / "0021-two.md").write_text("# 0021\n", encoding="utf-8")
    assert da.main(["--root", str(root), "check"]) == 1
    err = capsys.readouterr().err
    assert "0021" in err and "0021-one.md" in err and "0021-two.md" in err


def test_the_committed_reservation_file_lists_the_cto_registers_0023_to_0026():
    data = yaml.safe_load((ROOT / "docs" / "decisions" / "reserved.yaml").read_text(encoding="utf-8"))
    assert {e["number"] for e in data["reserved"]} >= {23, 24, 25, 26}
    assert da.adr_errors(da.FsTree(ROOT)) == []
