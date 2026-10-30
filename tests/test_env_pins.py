"""FR-1 (ADR 0060): one interpreter and one set of package versions everywhere. `.python-version`, `requires-python`
and uv.lock agree on 3.11, and scripts/check_env.py refuses (exit 2) a venv on another minor or with a package that
differs from uv.lock, so no lane reports a type or test result from a mismatched environment."""
import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_env", ROOT / "scripts" / "check_env.py")
assert _spec is not None and _spec.loader is not None
ce = importlib.util.module_from_spec(_spec)
sys.modules["check_env"] = ce
_spec.loader.exec_module(ce)

LOCK = """
version = 1
requires-python = "==3.11.*"

[[package]]
name = "numpy"
version = "2.4.6"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "PyYAML"
version = "6.0.3"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "pandas"
version = "3.0.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "mypy"
version = "1.20.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "goldbot"
version = "0.1.0"
source = { editable = "." }

[[package]]
name = "pywin32"
version = "311"
source = { registry = "https://pypi.org/simple" }
"""
GOOD = {"numpy": "2.4.6", "pyyaml": "6.0.3", "pandas": "3.0.1", "mypy": "1.20.0", "goldbot": "0.1.0"}


def test_python_version_file_pins_3_11():
    assert (ROOT / ".python-version").read_text().strip() == "3.11"


def test_requires_python_allows_only_3_11_and_uv_lock_agrees():
    spec = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    assert "<3.12" in spec and ">=3.11" in spec
    lock = tomllib.loads((ROOT / "uv.lock").read_text())["requires-python"]
    assert lock.replace(" ", "") in {"==3.11.*", ">=3.11,<3.12"}


def test_locked_versions_skip_the_project_itself_and_normalise_names():
    assert ce.locked_versions(LOCK) == {"numpy": "2.4.6", "pyyaml": "6.0.3", "pandas": "3.0.1", "mypy": "1.20.0",
                                        "pywin32": "311"}


def test_a_matching_environment_has_no_problems():
    assert ce.problems(LOCK, GOOD, (3, 11)) == []


def test_python_3_12_is_refused():
    assert any("3.12" in p for p in ce.problems(LOCK, GOOD, (3, 12)))


def test_a_package_that_differs_from_the_lock_is_refused_with_both_versions():
    out = ce.problems(LOCK, {**GOOD, "numpy": "2.5.3"}, (3, 11))
    assert out and "numpy" in out[0] and "2.5.3" in out[0] and "2.4.6" in out[0]


@pytest.mark.parametrize("name", ["numpy", "pandas", "mypy", "pyyaml"])
def test_a_missing_core_package_is_refused(name):
    assert any(name in p for p in ce.problems(LOCK, {k: v for k, v in GOOD.items() if k != name}, (3, 11)))


def test_a_locked_package_not_installed_on_this_platform_is_not_a_problem():
    assert "pywin32" not in " ".join(ce.problems(LOCK, GOOD, (3, 11)))   # locked, absent here


def test_the_cli_exits_2_on_a_package_mismatch_and_checks_the_interpreter_alone_with_skip_packages(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK.replace("2.4.6", "0.0.0"))   # nothing installed has numpy 0.0.0
    cmd = [sys.executable, str(ROOT / "scripts" / "check_env.py"), "--lock", str(lock)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 2 and "numpy" in r.stderr
    ok = subprocess.run([*cmd, "--skip-packages"], capture_output=True, text=True)
    assert ok.returncode == (0 if sys.version_info[:2] == (3, 11) else 2)


@pytest.mark.parametrize("script", ["gates.sh", "ci_local.sh", "lane_test.sh", "merge_smoke.sh"])
def test_every_gate_script_runs_check_env_before_any_gate(script):
    text = (ROOT / "scripts" / script).read_text()
    assert "goldbot_check_env" in text, f"{script} must refuse a mismatched venv"
    # the call line comes before the first line that runs a gate command
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    call = next(i for i, ln in enumerate(lines) if "goldbot_check_env" in ln and "()" not in ln)
    gate = next(i for i, ln in enumerate(lines) if any(w in ln for w in ("pytest ", "ruff ", "mypy ")))
    assert call < gate
