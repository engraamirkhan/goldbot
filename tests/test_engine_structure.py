"""Ratchets for splitting goldbot/engine/runner.py (TD-2, ADR 0023; docs/proposals/2026-10-runner-seam-map.md,
"Ratchets"). Each move-only step lowers them in the same PR and nothing may raise them (raising the cap needs a CTO
ruling): the line cap falls to at most 1,500 lines after step 7 (ADR 0023; 1,200 is the step-8 goal), and the
state-file I/O sites have left runner.py for engine/store.py.

Step 1 (engine/store.py): the cap is runner.py's measured size after the move, and the I/O allow-list is ADR 0023 A1.
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

RUNNER = Path(__file__).parents[1] / "goldbot" / "engine" / "runner.py"
LINE_CAP = 3447             # measured after step 1 (3471), lowered by moves out; <= 1,500 after step 7 (ADR 0023)

# A1 (ADR 0023 addendum): after step 1 runner.py writes no state file. The reads left are of files other jobs write, each
# allowed only in its named function, at today's count, until that function moves: a second read inside an allowed
# function fails, and so does a stale entry, so the list only shrinks and is empty after step 7.
READ_ALLOWED: dict[str, Counter[str]] = {
    # W2-1 F1 (approvals/pending, the bus's files)
    "Engine._archive_stale_proposals": Counter({"read_text": 1}),
    # step 5, engine/sizing.py (state/drift.json, drift_watch job)
    "Engine._drift": Counter({"json.loads": 1, "read_text": 1}),
    # step 5, engine/sizing.py (state/classifier_<account>.json, classifier job)
}


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
    """Seam map step 1 and ADR 0023 A1: runner.py never calls `write_atomic` (engine/store.py owns the engine, risk and
    orders files), and `json.loads` / `read_text` appear only in the READ_ALLOWED functions, each at its listed count.
    An entry whose function no longer reads a file is stale and fails until it is struck, so the list only shrinks."""
    got = _io_sites(RUNNER.read_text())
    writes = {fn: n for (fn, call), n in got.items() if call == "write_atomic"}
    assert not writes, f"write_atomic in runner.py: {writes}; durable writes go through engine/store.py"
    reads: dict[str, Counter[str]] = {}
    for (fn, call), n in got.items():
        reads.setdefault(fn, Counter())[call] += n
    assert set(reads) <= set(READ_ALLOWED), f"file reads outside the allow-list: {sorted(set(reads) - set(READ_ALLOWED))}"
    assert set(READ_ALLOWED) <= set(reads), f"stale allow-list entries (strike them): {sorted(set(READ_ALLOWED) - set(reads))}"
    changed = {fn: dict(reads[fn]) for fn in READ_ALLOWED if reads[fn] != READ_ALLOWED[fn]}
    assert not changed, f"reads in an allowed function changed from the listed counts: {changed}; never add one"


def test_the_io_site_finder_sees_every_spelling_it_polices():
    """The ratchet is only as good as its finder: each spelling is found and attributed to its method."""
    src = ("class E:\n"
           "    def a(self):\n        write_atomic(p, s)\n        base.write_atomic(p, s)\n"
           "    def b(self):\n        return json.loads(p.read_text())\n"
           "def c():\n    json.dumps({})\n")
    assert _io_sites(src) == Counter({("E.a", "write_atomic"): 2, ("E.b", "json.loads"): 1, ("E.b", "read_text"): 1})
