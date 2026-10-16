#!/usr/bin/env python3
"""Render the MT5 bridge's hash-pinned requirements file from uv.lock (SRE: the Wine box's pip install was unpinned).

The Wine MT5 box (goldbot/ops/linux/mt5_bootstrap.sh, goldbot-deploy on role mt5) runs the bridge under a Windows
CPython 3.11 in a Wine prefix, where `uv sync` does not work. It installs exactly this file:

  wine $WINPY -m pip install --require-hashes --no-deps --only-binary :all: -r Z:/opt/goldbot/<OUT>

($WINPY: the absolute path of the prefix's python.exe, never a bare `wine python`.)

* Versions come from uv.lock, so the bridge runs the same packages the brain locked. The roots are the packages the
  bridge process imports (`BRIDGE_ROOTS`); their dependencies are walked through uv.lock with the markers evaluated
  for the target (CPython 3.11.9, win32, AMD64), so `--no-deps` installs a complete set.
* Only wheels for that target are listed (cp311 / abi3 / none-any on win_amd64), each with its uv.lock sha256. A
  package with no such wheel is an error here, never a source build on the box (`--only-binary :all:`).
* `check` (tests/test_bridge_requirements.py, so CI) fails when the committed file differs from uv.lock.

  python scripts/bridge_requirements.py write     # after `uv lock` changes a bridge package
  python scripts/bridge_requirements.py check     # exit 1 when stale

Standalone on purpose (stdlib + packaging, no goldbot import).
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from packaging import tags
from packaging.markers import Marker
from packaging.utils import parse_wheel_filename

REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / "uv.lock"
OUT = REPO / "goldbot" / "ops" / "linux" / "mt5-bridge-requirements.txt"
PROJECT = "goldbot"
# what the bridge process imports (goldbot.ops.run bridge, goldbot.execution.bridge, goldbot.ops.accounts); keyring
# is optional there (goldbot-mt5 runs with the fail backend) but kept as the bootstrap always installed it
BRIDGE_ROOTS = ("metatrader5", "numpy", "pandas", "pyarrow", "pydantic", "pyyaml", "requests", "keyring")
TARGET_PYTHON = (3, 11)
TARGET_ENV = {          # the Wine prefix's Windows Python (mt5_bootstrap.sh PYVER)
    "python_version": "3.11", "python_full_version": "3.11.9", "implementation_name": "cpython",
    "platform_python_implementation": "CPython", "sys_platform": "win32", "platform_system": "Windows",
    "os_name": "nt", "platform_machine": "AMD64", "platform_release": "10", "platform_version": "10.0",
    "implementation_version": "3.11.9", "extra": "",
}
HEADER = (
    "# GENERATED from uv.lock by scripts/bridge_requirements.py: do not edit (python scripts/bridge_requirements.py write)\n"
    "# The Wine MT5 box's Windows CPython 3.11 (win_amd64), installed as the mt5 user with\n"
    "#   wine $WINPY -m pip install --require-hashes --no-deps --only-binary :all: -r <this file>\n"
    "# $WINPY: the absolute path of the prefix's python.exe (WINPY in goldbot/ops/linux/mt5_bootstrap.sh);\n"
    "# never a bare `wine python`: Windows tries the current directory before PATH (incident 2026-10-16)\n"
)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _supported_tags() -> set[tags.Tag]:
    plat = ["win_amd64"]
    out = set(tags.cpython_tags(TARGET_PYTHON, abis=["cp311"], platforms=plat))
    out |= set(tags.compatible_tags(TARGET_PYTHON, "cp311", platforms=plat))
    return out


def _applies(dep: dict[str, Any]) -> bool:
    m = dep.get("marker")
    return True if not m else Marker(m).evaluate(dict(TARGET_ENV))


def _pick(packages: dict[str, list[dict[str, Any]]], dep: dict[str, Any]) -> dict[str, Any]:
    cands = packages.get(_norm(dep["name"]), [])
    if "version" in dep:
        cands = [p for p in cands if p["version"] == dep["version"]]
    if len(cands) != 1:
        raise ValueError(f"uv.lock: {dep['name']} {dep.get('version', '')} resolves to {len(cands)} packages")
    return cands[0]


def render(lock_text: str) -> str:
    lock = tomllib.loads(lock_text)
    packages: dict[str, list[dict[str, Any]]] = {}
    for p in lock["package"]:
        packages.setdefault(_norm(p["name"]), []).append(p)
    project = _pick(packages, {"name": PROJECT})
    top = [*project.get("dependencies", []), *project.get("optional-dependencies", {}).get("live", [])]
    roots = [d for d in top if _norm(d["name"]) in BRIDGE_ROOTS and _applies(d)]
    missing = set(BRIDGE_ROOTS) - {_norm(d["name"]) for d in roots}
    if missing:
        raise ValueError(f"uv.lock: {PROJECT} does not depend on {', '.join(sorted(missing))} for the target")
    chosen: dict[str, dict[str, Any]] = {}
    todo = list(roots)
    while todo:
        dep = todo.pop()
        pkg = _pick(packages, dep)
        key = _norm(pkg["name"])
        if key in chosen:
            if chosen[key]["version"] != pkg["version"]:
                raise ValueError(f"uv.lock: two versions of {key} for the target")
        else:
            chosen[key] = pkg
            todo += [d for d in pkg.get("dependencies", []) if _applies(d)]
        for extra in dep.get("extra", []):
            todo += [d for d in pkg.get("optional-dependencies", {}).get(extra, []) if _applies(d)]
    ok = _supported_tags()
    lines = [HEADER.rstrip("\n")]
    for key in sorted(chosen):
        pkg = chosen[key]
        hashes = sorted(w["hash"] for w in pkg.get("wheels", [])
                        if set(parse_wheel_filename(w["url"].rsplit("/", 1)[-1])[3]) & ok)
        if not hashes:
            raise ValueError(f"uv.lock: {key} {pkg['version']} has no wheel for CPython 3.11 win_amd64")
        lines.append(f"{key}=={pkg['version']} \\")
        lines += [f"    --hash={h}" + (" \\" if i < len(hashes) - 1 else "") for i, h in enumerate(hashes)]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="scripts/bridge_requirements.py")
    ap.add_argument("command", choices=["write", "check"])
    args = ap.parse_args(argv)
    want = render(LOCK.read_text(encoding="utf-8"))
    if args.command == "write":
        OUT.write_text(want, encoding="utf-8")
        print(f"wrote {OUT.relative_to(REPO)}")
        return 0
    if not OUT.exists() or OUT.read_text(encoding="utf-8") != want:
        print(f"{OUT.relative_to(REPO)} is out of date with uv.lock: python scripts/bridge_requirements.py write")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
