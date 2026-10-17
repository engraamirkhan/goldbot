"""ADR 0021 fence F3: only goldbot/ops/smoke.py calls `RiskGate.check_operational`, and the engine, the allocator,
the API and the Telegram service never import goldbot.ops.smoke, so an operational order (no model checks) can never
be reached from a path that trades on a model."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FENCED = ("engine", "allocator", "api", "telegram")


def _sources() -> list[Path]:
    return sorted((ROOT / "goldbot").rglob("*.py"))


def test_only_ops_smoke_calls_check_operational():
    users: set[str] = set()
    for f in _sources():
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            # any reference counts, not only a call: `fn = gate.check_operational` would be a way round
            ref = (isinstance(node, ast.Attribute) and node.attr == "check_operational") or \
                  (isinstance(node, ast.Name) and node.id == "check_operational")
            if ref:
                users.add(f.relative_to(ROOT).as_posix())
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and any(
                    isinstance(a, ast.Constant) and a.value == "check_operational" for a in node.args):
                users.add(f.relative_to(ROOT).as_posix())
    assert users == {"goldbot/ops/smoke.py"}, users


def test_engine_allocator_api_telegram_never_import_ops_smoke():
    bad: list[str] = []
    for pkg in FENCED:
        for f in sorted((ROOT / "goldbot" / pkg).rglob("*.py")):
            for node in ast.walk(ast.parse(f.read_text())):
                if isinstance(node, ast.Import) and any(a.name.startswith("goldbot.ops.smoke") for a in node.names):
                    bad.append(f.relative_to(ROOT).as_posix())
                if isinstance(node, ast.ImportFrom) and (
                        (node.module or "").startswith("goldbot.ops.smoke")
                        or (node.module == "goldbot.ops" and any(a.name == "smoke" for a in node.names))):
                    bad.append(f.relative_to(ROOT).as_posix())
                if isinstance(node, ast.Constant) and node.value == "goldbot.ops.smoke":   # importlib by name
                    bad.append(f.relative_to(ROOT).as_posix())
    assert bad == []
    # and not transitively: importing every fenced package leaves goldbot.ops.smoke unloaded
    code = ("import sys, goldbot.engine, goldbot.allocator, goldbot.api.app, goldbot.telegram.bot; "
            "print('goldbot.ops.smoke' in sys.modules)")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, PYTHONPATH=str(ROOT)))
    assert p.returncode == 0, p.stderr[-2000:]
    assert p.stdout.strip().splitlines()[-1] == "False"
