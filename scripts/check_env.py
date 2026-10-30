#!/usr/bin/env python3
"""Refuse a mismatched environment before any gate runs (FR-1, ADR 0060).

Exit 2 when the running interpreter is not the 3.11 that `.python-version` pins, or when an installed package differs
from the version `uv.lock` pins. Run it with the venv's python: `.venv/bin/python scripts/check_env.py`.

Packages the lock names but this platform does not install (win32-only wheels) are not a problem; a missing
numpy, pandas, mypy or pyyaml is, because those four decide results (types, arrays, frames, settings).
Standard library only. `--skip-packages` checks the interpreter alone.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ("numpy", "pandas", "mypy", "pyyaml")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_versions(lock_text: str) -> dict[str, str]:
    """name -> version of every registry package in uv.lock (the project itself, an editable source, is skipped)."""
    out = {}
    for pkg in tomllib.loads(lock_text).get("package", []):
        if "registry" in pkg.get("source", {}) and "version" in pkg:
            out[norm(pkg["name"])] = pkg["version"]
    return out


def installed_versions() -> dict[str, str]:
    return {norm(d.metadata["Name"]): d.version for d in metadata.distributions() if d.metadata["Name"]}


def problems(lock_text: str, installed: dict[str, str] | None, minor: tuple[int, int],
             wanted: tuple[int, int] = (3, 11)) -> list[str]:
    """Human lines, empty when the environment matches. installed None = do not check packages."""
    out = []
    if minor != wanted:
        out.append(f"Python {minor[0]}.{minor[1]} is running; this repo is pinned to {wanted[0]}.{wanted[1]} "
                   "(.python-version)")
    if installed is None:
        return out
    for name, want in sorted(locked_versions(lock_text).items()):
        have = installed.get(name)
        if have is None:
            if name in CORE:
                out.append(f"{name} is not installed (uv.lock pins {want})")
        elif have != want:
            out.append(f"{name} {have} is installed; uv.lock pins {want}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lock", type=Path, default=ROOT / "uv.lock")
    ap.add_argument("--skip-packages", action="store_true")
    a = ap.parse_args(argv)
    found = problems(a.lock.read_text(), None if a.skip_packages else installed_versions(), sys.version_info[:2])
    if found:
        print("check_env: this environment does not match uv.lock, so no gate result from it can be trusted:",
              file=sys.stderr)
        for line in found:
            print(f"  - {line}", file=sys.stderr)
        print("  fix: scripts/worktree_env.sh   (uv venv --python 3.11 + uv sync --frozen --extra dev)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
