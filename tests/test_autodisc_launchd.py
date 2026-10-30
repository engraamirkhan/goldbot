"""The owner's Mac runs the discovery search nightly (ADR 0058 decision 6: "the search and history reads run on the
owner's Mac"; the brain never searches): a launchd agent at 01:30 local runs `scripts/autodisc.py nightly` for 15m, 1h
and 4h in turn, logging to ~/tmp/autodisc. The installer is a dry run unless --apply. `uname` is faked on PATH, so these
run the same on the Linux CI runner."""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "goldbot" / "ops" / "macos" / "com.goldbot.autodisc.plist.template"
RUNNER = ROOT / "scripts" / "autodisc_nightly_mac.sh"
INSTALL = ROOT / "scripts" / "install_autodisc_launchd.sh"


def fake_bin(tmp: Path, system: str) -> Path:
    d = tmp / "bin"
    d.mkdir(exist_ok=True)
    for name, body in (("uname", f"echo {system}"), ("launchctl", f"echo launchctl \"$@\" >> {tmp}/launchctl.log")):
        (d / name).write_text(f"#!/bin/sh\n{body}\n")
        (d / name).chmod(0o755)
    return d


def env_for(tmp: Path, system: str) -> dict[str, str]:
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    return {"PATH": f"{fake_bin(tmp, system)}:/usr/bin:/bin", "HOME": str(home)}


def test_the_plist_template_runs_the_mac_runner_at_0130_local_and_logs_under_tmp_autodisc():
    text = TEMPLATE.read_text().replace("__REPO__", "/r").replace("__LOGDIR__", "/h/tmp/autodisc")
    p = plistlib.loads(text.encode())
    assert p["Label"] == "com.goldbot.autodisc"
    assert p["StartCalendarInterval"] == {"Hour": 1, "Minute": 30}
    assert p["ProgramArguments"] == ["/bin/bash", "/r/scripts/autodisc_nightly_mac.sh"]
    assert p["WorkingDirectory"] == "/r" and p["StandardOutPath"].startswith("/h/tmp/autodisc/")
    assert "RunAtLoad" not in p or p["RunAtLoad"] is False


def test_the_runner_runs_15m_1h_4h_in_turn_through_the_nightly_command():
    src = RUNNER.read_text()
    assert src.index("15m") < src.index("1h") < src.index("4h")
    assert "scripts/autodisc.py nightly --timeframe" in src and "tmp/autodisc" in src


def test_the_runner_never_runs_off_the_mac(tmp_path):
    out = subprocess.run(["/bin/bash", str(RUNNER)], env=env_for(tmp_path, "Linux"), capture_output=True, text=True)
    assert out.returncode != 0 and "never on the brain" in out.stderr


def test_the_installer_is_a_dry_run_by_default_and_writes_nothing(tmp_path):
    env = env_for(tmp_path, "Darwin")
    out = subprocess.run(["/bin/bash", str(INSTALL)], env=env, capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert "dry run" in out.stdout and "<key>StartCalendarInterval</key>" in out.stdout
    assert str(ROOT) in out.stdout and f"{env['HOME']}/tmp/autodisc" in out.stdout
    assert not (Path(env["HOME"]) / "Library").exists() and not (tmp_path / "launchctl.log").exists()


def test_the_installer_applies_only_with_apply_and_only_on_a_mac(tmp_path):
    env = env_for(tmp_path, "Darwin")
    out = subprocess.run(["/bin/bash", str(INSTALL), "--apply"], env=env, capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    plist = Path(env["HOME"]) / "Library" / "LaunchAgents" / "com.goldbot.autodisc.plist"
    assert plistlib.loads(plist.read_bytes())["ProgramArguments"][1] == f"{ROOT}/scripts/autodisc_nightly_mac.sh"
    assert "bootstrap" in (tmp_path / "launchctl.log").read_text()
    assert (Path(env["HOME"]) / "tmp" / "autodisc").is_dir()
    linux = tmp_path / "linux"
    linux.mkdir()
    out = subprocess.run(["/bin/bash", str(INSTALL), "--apply"], env=env_for(linux, "Linux"), capture_output=True,
                         text=True, cwd=ROOT)
    assert out.returncode != 0 and "Mac only" in out.stderr


@pytest.mark.parametrize("path", [RUNNER, INSTALL])
def test_the_scripts_are_executable(path):
    assert os.access(path, os.X_OK)
