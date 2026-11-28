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


_PLATFORM_TERM = re.compile(r"^sys_platform\s*(==|!=)\s*'([^']*)'$")


def _marker_holds(marker: str, platform: str) -> bool:
    """A uv resolution marker on `platform`: `sys_platform == / != 'x'` terms joined by `and` are evaluated; any
    other term is taken as true (a marker on other variables never hides a pin)."""
    for term in (t.strip() for t in marker.split(" and ")):
        m = _PLATFORM_TERM.match(term)
        if m and ((m.group(2) == platform) != (m.group(1) == "==")):
            return False
    return True


def locked_versions(lock_text: str, platform: str = sys.platform) -> dict[str, tuple[str, ...]]:
    """name -> the versions uv.lock pins for every registry package on `platform` (the project itself, an editable
    source, is skipped). A package locked once per platform carries `resolution-markers` (torch: PyPI on macOS and
    Windows, the PyTorch CPU index `+cpu` on Linux): only the entries whose markers hold on this platform count, so a
    CUDA wheel from PyPI on Linux is refused."""
    out: dict[str, set[str]] = {}
    for pkg in tomllib.loads(lock_text).get("package", []):
        if "registry" not in pkg.get("source", {}) or "version" not in pkg:
            continue
        markers = pkg.get("resolution-markers")
        if markers and not any(_marker_holds(m, platform) for m in markers):
            continue
        out.setdefault(norm(pkg["name"]), set()).add(pkg["version"])
    return {k: tuple(sorted(v)) for k, v in out.items()}


def installed_versions() -> dict[str, str]:
    return {norm(d.metadata["Name"]): d.version for d in metadata.distributions() if d.metadata["Name"]}


def problems(lock_text: str, installed: dict[str, str] | None, minor: tuple[int, int],
             wanted: tuple[int, int] = (3, 11), *, platform: str = sys.platform) -> list[str]:
    """Human lines, empty when the environment matches. installed None = do not check packages."""
    out = []
    if minor != wanted:
        out.append(f"Python {minor[0]}.{minor[1]} is running; this repo is pinned to {wanted[0]}.{wanted[1]} "
                   "(.python-version)")
    if installed is None:
        return out
    for name, want in sorted(locked_versions(lock_text, platform).items()):
        have = installed.get(name)
        pins = " or ".join(want)
        if have is None:
            if name in CORE:
                out.append(f"{name} is not installed (uv.lock pins {pins})")
        elif have not in want:
            out.append(f"{name} {have} is installed; uv.lock pins {pins}")
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
