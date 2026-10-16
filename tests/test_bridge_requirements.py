"""The Wine MT5 box installs its Windows-Python packages only from a hash-pinned file rendered from uv.lock
(scripts/bridge_requirements.py; it was an unpinned `pip install MetaTrader5 pandas ...`). These tests are the CI
check that the committed file matches uv.lock, and that every pip install in the Linux ops scripts is hash-checked."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LINUX = REPO / "goldbot" / "ops" / "linux"
_spec = importlib.util.spec_from_file_location("bridge_requirements", REPO / "scripts" / "bridge_requirements.py")
assert _spec is not None and _spec.loader is not None
br = importlib.util.module_from_spec(_spec)
sys.modules["bridge_requirements"] = br
_spec.loader.exec_module(br)


def _pins(text: str) -> dict[str, str]:
    return dict(re.findall(r"^([a-z0-9-]+)==(\S+) \\$", text, re.M))


def test_the_committed_file_is_in_sync_with_uv_lock():
    want = br.render(br.LOCK.read_text(encoding="utf-8"))
    assert br.OUT.read_text(encoding="utf-8") == want, "run: python scripts/bridge_requirements.py write"
    assert br.main(["check"]) == 0


def test_every_root_is_pinned_with_a_hash_and_versions_match_the_lock():
    text = br.OUT.read_text(encoding="utf-8")
    pins = _pins(text)
    assert set(br.BRIDGE_ROOTS) <= set(pins)
    # every requirement line carries at least one sha256 (pip --require-hashes refuses the file otherwise)
    body = "\n".join(x for x in text.splitlines() if not x.startswith("#"))     # the header, however long
    blocks = re.split(r"\n(?=[a-z0-9-]+==)", body)
    assert all(re.search(r"--hash=sha256:[0-9a-f]{64}", b) for b in blocks)
    import tomllib
    lock = tomllib.loads(br.LOCK.read_text(encoding="utf-8"))
    versions = {(br._norm(p["name"]), p["version"]) for p in lock["package"]}
    assert all((n, v) in versions for n, v in pins.items())
    # the brain-only stack is not shipped to the 1 GB box
    assert not {"lightgbm", "duckdb", "fastapi", "anthropic", "python-telegram-bot"} & set(pins)


LOCK = """
version = 1
requires-python = ">=3.11, <3.13"

[[package]]
name = "goldbot"
version = "0.1.0"
source = { editable = "." }
dependencies = [
    { name = "numpy", version = "2.4.6", marker = "python_full_version < '3.12'" },
    { name = "numpy", version = "2.5.3", marker = "python_full_version >= '3.12'" },
    { name = "lightgbm" },
]

[package.optional-dependencies]
live = [{ name = "metatrader5", marker = "sys_platform == 'win32'" }]

[[package]]
name = "numpy"
version = "2.4.6"
wheels = [
    { url = "https://x/numpy-2.4.6-cp311-cp311-win_amd64.whl", hash = "sha256:aa" },
    { url = "https://x/numpy-2.4.6-cp311-cp311-manylinux_2_28_x86_64.whl", hash = "sha256:bb" },
    { url = "https://x/numpy-2.4.6-cp312-cp312-win_amd64.whl", hash = "sha256:cc" },
]

[[package]]
name = "numpy"
version = "2.5.3"
wheels = [{ url = "https://x/numpy-2.5.3-cp312-cp312-win_amd64.whl", hash = "sha256:dd" }]

[[package]]
name = "metatrader5"
version = "5.0.1"
dependencies = [{ name = "numpy", version = "2.4.6", marker = "python_full_version < '3.12' and sys_platform == 'win32'" }]
wheels = [{ url = "https://x/metatrader5-5.0.1-cp311-cp311-win_amd64.whl", hash = "sha256:ee" }]

[[package]]
name = "lightgbm"
version = "4.7.0"
wheels = [{ url = "https://x/lightgbm-4.7.0-py3-none-win_amd64.whl", hash = "sha256:ff" }]
"""


def test_render_takes_the_win_amd64_cp311_wheels_of_the_target_versions(monkeypatch):
    monkeypatch.setattr(br, "BRIDGE_ROOTS", ("metatrader5", "numpy"))
    out = br.render(LOCK)
    assert "numpy==2.4.6 \\\n    --hash=sha256:aa\n" in out          # not the linux or cp312 wheel, not 2.5.3
    assert "metatrader5==5.0.1 \\\n    --hash=sha256:ee\n" in out
    assert "2.5.3" not in out and "lightgbm" not in out


def test_render_refuses_a_package_without_a_target_wheel(monkeypatch):
    monkeypatch.setattr(br, "BRIDGE_ROOTS", ("metatrader5", "numpy"))
    no_wheel = LOCK.replace('{ url = "https://x/metatrader5-5.0.1-cp311-cp311-win_amd64.whl", hash = "sha256:ee" }',
                            '{ url = "https://x/metatrader5-5.0.1-cp312-cp312-win_amd64.whl", hash = "sha256:ee" }')
    with pytest.raises(ValueError, match="no wheel for CPython 3.11 win_amd64"):
        br.render(no_wheel)


def test_render_refuses_a_root_the_project_does_not_depend_on(monkeypatch):
    monkeypatch.setattr(br, "BRIDGE_ROOTS", ("metatrader5", "numpy", "pandas"))
    with pytest.raises(ValueError, match="does not depend on pandas"):
        br.render(LOCK)


def test_check_fails_on_a_stale_file(tmp_path, monkeypatch, capsys):
    stale = tmp_path / "reqs.txt"
    stale.write_text(br.OUT.read_text(encoding="utf-8").replace("numpy==", "numpy===", 1), encoding="utf-8")
    monkeypatch.setattr(br, "OUT", stale)
    monkeypatch.setattr(br, "REPO", tmp_path)
    assert br.main(["check"]) == 1
    assert "out of date with uv.lock" in capsys.readouterr().out


def test_every_pip_install_in_the_linux_scripts_is_hash_checked_and_binary_only():
    hits = []
    for f in sorted(LINUX.glob("*.sh")):
        text = f.read_text(encoding="utf-8").replace("\\\n", " ")
        for line in text.splitlines():
            if line.lstrip().startswith("#") or not re.search(r"\bpip\s+install\b", line):
                continue
            if not all(flag in line for flag in ("--require-hashes", "--no-deps", "--only-binary :all:", "-r ")):
                hits.append(f"{f.name}: {line.strip()}")
    assert hits == []
    mt5 = (LINUX / "mt5_bootstrap.sh").read_text(encoding="utf-8")
    assert "mt5-bridge-requirements.txt" in mt5 and "pip install --upgrade" not in mt5 and "-e 'Z:" not in mt5
    deploy = (LINUX / "goldbot-deploy.sh").read_text(encoding="utf-8")
    assert "BRIDGE_REQS=goldbot/ops/linux/mt5-bridge-requirements.txt" in deploy
    assert 'grep -qE "^(uv.lock|$BRIDGE_REQS)\\$"' in deploy


def test_the_header_names_the_absolute_interpreter_not_a_bare_wine_python():
    """Incident 2026-10-16: a bare `wine python` ran the installer saved in the working directory."""
    header = [x for x in br.HEADER.splitlines() if x.startswith("#")]
    assert header == br.HEADER.rstrip("\n").splitlines()
    assert not any(re.search(r"\bwine\s+python\s+-m", x) for x in header)
    assert any("wine $WINPY -m pip install --require-hashes" in x for x in header)
    assert not any(x.endswith("\\") for x in header), "a trailing backslash would read as a continuation"
