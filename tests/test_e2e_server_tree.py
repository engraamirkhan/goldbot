"""scripts/e2e_server.py refuses to run on a goldbot from another checkout (TD-3)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("e2e_server", REPO / "scripts" / "e2e_server.py")
assert _spec is not None and _spec.loader is not None
e2e_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e_server)


def test_this_tree_is_accepted() -> None:
    e2e_server.check_tree()


def test_another_tree_is_refused_with_both_paths(tmp_path: Path) -> None:
    other = tmp_path / "main-checkout" / "goldbot" / "__init__.py"
    with pytest.raises(SystemExit) as exc:
        e2e_server.check_tree(str(other))
    msg = str(exc.value)
    assert str(other.parent) in msg and str(REPO) in msg and "PYTHON=" in msg


def test_a_sibling_directory_with_the_same_prefix_is_refused() -> None:
    sibling = Path(str(REPO) + "-other") / "goldbot" / "__init__.py"       # not a string-prefix match
    with pytest.raises(SystemExit):
        e2e_server.check_tree(str(sibling))
