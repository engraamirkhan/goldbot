"""Shared by the service and command modules split out of goldbot/ops/run.py (a pure move; run.py re-exports every name).

goldbot.ops.run stays the public surface callers and tests import and patch (`patch("goldbot.ops.run.engine_pass")`,
`monkeypatch.setattr(run, "_SUP_ERR_LOGGED", {})`), exactly as when every function was written out there. So a moved
function reaches another moved name through goldbot.ops.run at call time: `resolve(fn)` for a function or class,
`patched(name, default)` for a module global. tests/test_ops_run_registry.py enforces it.

stdlib only: importing goldbot.ops.run must load no third-party package (the MT5 bridge's Wine Python installs a short,
hash-pinned list; scripts/bridge_requirements.py), so every service and command keeps importing lazily.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])
T = TypeVar("T")

log = logging.getLogger("goldbot.run")      # one logger for every service and command, as before the split


def resolve(fn: F) -> F:
    """`fn` as goldbot.ops.run exports it (the patch point), else `fn` itself."""
    found: F = getattr(importlib.import_module("goldbot.ops.run"), fn.__name__, fn)
    return found


def patched(name: str, default: T) -> T:
    """The module global `name` as goldbot.ops.run holds it (the patch point), else `default`."""
    found: T = getattr(importlib.import_module("goldbot.ops.run"), name, default)
    return found
