"""The bridge's file set (goldbot/ops/bridge_files.py): one definition shared with goldbot-deploy, closed under the
bridge's imports (or each cut named with its reason), and the brain-side git comparison bridge_compat uses."""
from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

from goldbot.ops import bridge_files as bf

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "goldbot" / "ops" / "linux" / "goldbot-deploy.sh"


def _tracked() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def _module_file(mod: str) -> str | None:
    p = Path(*mod.split("."))
    if (REPO / p.with_suffix(".py")).exists():
        return str(p.with_suffix(".py"))
    if (REPO / p / "__init__.py").exists():
        return str(p / "__init__.py")
    return None


def _type_checking_lines(tree: ast.AST) -> set[int]:
    skip: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.If) and "TYPE_CHECKING" in ast.unparse(n.test):
            for b in n.body:
                skip.update(range(b.lineno, (b.end_lineno or b.lineno) + 1))
    return skip


def _goldbot_imports(rel: str) -> set[str]:
    """Repo files of every goldbot module `rel` imports, at top level or inside a function, TYPE_CHECKING aside."""
    tree = ast.parse((REPO / rel).read_text())
    skip = _type_checking_lines(tree)
    pkg = list(Path(rel).with_suffix("").parts[:-1])
    mods: set[str] = set()
    for n in ast.walk(tree):
        if getattr(n, "lineno", None) in skip:
            continue
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            m = n.module or ""
            if n.level:
                m = ".".join(pkg[: len(pkg) - (n.level - 1)] + ([m] if m else []))
            mods.add(m)
            mods.update(f"{m}.{a.name}" for a in n.names)
    files: set[str] = set()
    for m in mods:
        if not m.startswith("goldbot"):
            continue
        parts = m.split(".")
        for i in range(1, len(parts) + 1):
            f = _module_file(".".join(parts[:i]))
            if f:
                files.add(f)
    return files


def test_the_deploy_script_carries_the_same_definition():
    m = re.search(r"^BRIDGE_FILES_RE='([^']+)'", DEPLOY.read_text(), re.M)
    assert m, "goldbot-deploy.sh must define BRIDGE_FILES_RE='...'"
    assert m.group(1) == bf.BRIDGE_FILES_RE


def test_the_set_is_closed_under_the_bridges_imports():
    members = [f for f in _tracked() if f.endswith(".py") and bf.is_bridge_file(f)]
    assert len(members) > 20
    escapes = {f"{m} -> {d}" for m in members if m not in bf.NOT_FOLLOWED
               for d in _goldbot_imports(m) if not bf.is_bridge_file(d)}
    assert not escapes, ("a bridge file imports a file outside the bridge set: add it to BRIDGE_FILES_RE (in both "
                         f"places) or, if it never runs in the bridge, name the importer in NOT_FOLLOWED: {sorted(escapes)}")


def test_every_cut_is_a_member_that_really_leaves_the_set():
    for f, why in bf.NOT_FOLLOWED.items():
        assert bf.is_bridge_file(f) and (REPO / f).exists() and why, f
        assert any(not bf.is_bridge_file(d) for d in _goldbot_imports(f)), f"{f}: nothing to cut, drop it"


@pytest.mark.parametrize("path", ["goldbot/execution/bridge.py", "goldbot/execution/bridge_lane.py",
                                  "goldbot/execution/mt5_adapter.py", "goldbot/ops/services/bridge.py",
                                  "goldbot/ops/run.py", "goldbot/ops/login.py", "config/accounts.yaml",
                                  "config/instruments.yaml", "config/settings.yaml",
                                  "goldbot/ops/linux/mt5-bridge-requirements.txt"])
def test_the_bridges_own_files_are_in(path):
    assert bf.is_bridge_file(path) and (REPO / path).exists()


@pytest.mark.parametrize("path", ["goldbot/engine/runner.py", "goldbot/ops/health.py", "goldbot/research/arms.py",
                                  "web/src/App.tsx", "docs/RUNBOOK.md", "tests/test_bridge.py", "uv.lock",
                                  "config/research/candidates/x.yaml", "config/exploration_arms.yaml",
                                  "goldbot/ops/linux/goldbot-deploy.sh", "goldbot/execution/sub/x.py"])
def test_brain_side_files_are_out(path):
    assert not bf.is_bridge_file(path)


def test_the_bridge_changes_among_a_diff():
    assert bf.bridge_changes(["docs/a.md", "goldbot/execution/bridge.py", "", "web/x.ts"]) == [
        "goldbot/execution/bridge.py"]


def _git(cwd: Path, *args: str) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(cwd), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x.invalid"}
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env,
                          check=True).stdout.strip()


def _commit(repo: Path, rel: str, text: str) -> str:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", rel)
    return _git(repo, "rev-parse", "HEAD")


def test_differs_outside_bridge_reads_git(tmp_path):
    _git(tmp_path, "init", "-q")
    a = _commit(tmp_path, "goldbot/execution/bridge.py", "v1\n")
    b = _commit(tmp_path, "docs/x.md", "doc\n")
    c = _commit(tmp_path, "goldbot/execution/bridge.py", "v2\n")
    bf._DIFF_CACHE.clear()
    assert bf.differs_outside_bridge(tmp_path, a, b) is True
    assert bf.differs_outside_bridge(tmp_path, a, c) is False
    assert bf.differs_outside_bridge(tmp_path, a, "f" * 40) is None        # unknown commit: git cannot tell
    assert bf.differs_outside_bridge(tmp_path, a, "nope") is None


def test_the_start_check_passes_on_this_checkout_and_names_an_unknown_account():
    import yaml
    ids = list((yaml.safe_load((REPO / "config" / "accounts.yaml").read_text()) or {}).get("accounts", {}))
    assert ids
    assert bf.start_check(ids) == []
    assert bf.start_check(["no-such-account"]) == ["no-such-account: not in config/accounts.yaml"]


def test_the_cli_refuses_odd_account_names(capsys):
    assert bf.main(["check", "../x"]) == 2
    assert bf.main([]) == 2
