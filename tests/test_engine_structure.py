"""Ratchets for splitting goldbot/engine/runner.py (TD-2, ADR 0023; docs/proposals/2026-10-runner-seam-map.md,
"Ratchets"). Each move-only step lowers them in the same PR and nothing may raise them: the line cap falls towards the
seam map's target of at most 1,200 lines, and the state-file I/O sites leave runner.py for engine/store.py.

Captured at 7dc12a3 (PR 0, tests only): today's line count and today's I/O sites, so both pass on the current code.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

RUNNER = Path(__file__).parents[1] / "goldbot" / "engine" / "runner.py"
LINE_CAP = 3552             # CTO ruling 12 (2026-10-17): one-time raise for lanes 71+62 that pre-dated PR 0; lowered by every step

# (enclosing function, call) -> count. Step 1 (engine/store.py) removes STORE_SITES; the seam map places no step for
# OTHER_SITES (reads of files other jobs write), which the architect assigns before the list can reach zero.
STORE_SITES: Counter[tuple[str, str]] = Counter({
    ("Engine._save_orders", "write_atomic"): 1,
    ("Engine._load_orders", "json.loads"): 1,
    ("Engine._load_orders", "read_text"): 1,
    ("Engine._save_risk_state", "write_atomic"): 1,
    ("Engine._load_risk_state", "json.loads"): 1,
    ("Engine._load_risk_state", "read_text"): 1,
    ("Engine._write_state", "write_atomic"): 1,
})
OTHER_SITES: Counter[tuple[str, str]] = Counter({
    ("Engine._archive_stale_proposals", "read_text"): 1,    # approvals/pending (the bus's files)
    ("Engine._drift", "json.loads"): 1,                     # state/drift.json (drift_watch job)
    ("Engine._drift", "read_text"): 1,
    ("Engine._account_class", "json.loads"): 1,             # state/classifier_<account>.json (classifier job)
    ("Engine._account_class", "read_text"): 1,
})


def _io_sites(source: str) -> Counter[tuple[str, str]]:
    """Calls of `write_atomic`, `json.loads` and `.read_text()` by enclosing function (Class.method)."""
    found: Counter[tuple[str, str]] = Counter()

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, [*scope, child.name])
                continue
            if isinstance(child, ast.Call):
                f = child.func
                name = None
                if isinstance(f, ast.Name) and f.id == "write_atomic":
                    name = "write_atomic"
                elif isinstance(f, ast.Attribute) and f.attr == "write_atomic":
                    name = "write_atomic"
                elif isinstance(f, ast.Attribute) and f.attr == "loads" and isinstance(f.value, ast.Name) \
                        and f.value.id == "json":
                    name = "json.loads"
                elif isinstance(f, ast.Attribute) and f.attr == "read_text":
                    name = "read_text"
                if name is not None:
                    found[(".".join(scope) or "<module>", name)] += 1
            visit(child, scope)

    visit(ast.parse(source), [])
    return found


def test_runner_py_stays_under_its_line_cap():
    """ADR 0023 step plan: runner.py only shrinks; a step that moves code out lowers LINE_CAP in the same PR."""
    lines = len(RUNNER.read_text().splitlines())
    assert lines <= LINE_CAP, f"runner.py has {lines} lines, over its cap of {LINE_CAP}: move code out, never in"


def test_runner_does_no_state_file_io():
    """Seam map step 1: runner.py keeps no `write_atomic` / `json.loads` / `read_text`; the store owns the files.
    Until the steps land the test holds runner.py to today's sites exactly: a new site fails, and a removed one fails
    until it is struck from the allow-list, so the list only shrinks."""
    got = _io_sites(RUNNER.read_text())
    assert got == STORE_SITES + OTHER_SITES, (
        f"new: {dict(got - (STORE_SITES + OTHER_SITES))}; gone (strike them from the list): "
        f"{dict((STORE_SITES + OTHER_SITES) - got)}")


def test_the_io_site_finder_sees_every_spelling_it_polices():
    """The ratchet is only as good as its finder: each spelling is found and attributed to its method."""
    src = ("class E:\n"
           "    def a(self):\n        write_atomic(p, s)\n        base.write_atomic(p, s)\n"
           "    def b(self):\n        return json.loads(p.read_text())\n"
           "def c():\n    json.dumps({})\n")
    assert _io_sites(src) == Counter({("E.a", "write_atomic"): 2, ("E.b", "json.loads"): 1, ("E.b", "read_text"): 1})
