"""BACKLOG 215: the Mac publishes the brain's registry outbox daily through a launchd user agent (the brain token stays
read-only, decision 2026-11-01). The runner logs one line per run to ~/tmp/registry-publish/ and exits non-zero on a
failed publish; the installer is a dry run unless --apply (pattern of scripts/install_autodisc_launchd.sh). `uname`,
`launchctl` and the publish script are faked, so these run the same on the Linux CI runner."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.goldbot.registry-publish"
TEMPLATE = ROOT / "goldbot" / "ops" / "macos" / f"{LABEL}.plist.template"
RUNNER = ROOT / "scripts" / "registry_publish_mac.sh"
INSTALL = ROOT / "scripts" / "install_registry_publish_launchd.sh"


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


def _repo_with_publish(tmp: Path, body: str) -> Path:
    """A copy of the runner next to a fake scripts/registry_publish.sh."""
    repo = tmp / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(RUNNER, repo / "scripts" / RUNNER.name)
    pub = repo / "scripts" / "registry_publish.sh"
    pub.write_text(f"#!/usr/bin/env bash\n{body}\n")
    pub.chmod(0o755)
    return repo


def test_the_plist_runs_the_runner_daily_and_logs_under_tmp_registry_publish():
    text = (TEMPLATE.read_text().replace("__REPO__", "/r").replace("__LOGDIR__", "/h/tmp/registry-publish")
            .replace("__HOME__", "/h"))
    p = plistlib.loads(text.encode())
    assert p["Label"] == LABEL
    assert set(p["StartCalendarInterval"]) == {"Hour", "Minute"}       # daily; launchd runs a missed slot on wake
    assert p["ProgramArguments"] == ["/bin/bash", "/r/scripts/registry_publish_mac.sh"]
    assert p["WorkingDirectory"] == "/r" and p["StandardOutPath"].startswith("/h/tmp/registry-publish/")
    assert "/opt/homebrew/bin" in p["EnvironmentVariables"]["PATH"]  # gh and uv under launchd's bare PATH
    assert "__" not in text


def test_the_runner_logs_one_ok_line_per_run(tmp_path):
    repo = _repo_with_publish(tmp_path, 'echo "outbox entries read from the brain: 2"; echo "published 7 rows"')
    env = env_for(tmp_path, "Darwin")
    out = subprocess.run(["/bin/bash", str(repo / "scripts" / RUNNER.name)], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    log = Path(env["HOME"]) / "tmp" / "registry-publish" / "publish.log"
    lines = log.read_text().splitlines()
    assert len(lines) == 1 and " ok " in lines[0] and "published 7 rows" in lines[0]
    assert any(p.name.startswith("run-") for p in log.parent.iterdir())         # the full output of the run


def test_the_runner_exits_non_zero_and_logs_fail_when_the_publish_fails(tmp_path):
    repo = _repo_with_publish(tmp_path, 'echo "ssh: connect refused" >&2; exit 3')
    env = env_for(tmp_path, "Darwin")
    out = subprocess.run(["/bin/bash", str(repo / "scripts" / RUNNER.name)], env=env, capture_output=True, text=True)
    assert out.returncode == 3
    line = (Path(env["HOME"]) / "tmp" / "registry-publish" / "publish.log").read_text().splitlines()[-1]
    assert " FAIL " in line and "rc=3" in line and "connect refused" in line


def test_the_runner_never_runs_off_the_mac(tmp_path):
    repo = _repo_with_publish(tmp_path, "exit 0")
    out = subprocess.run(["/bin/bash", str(repo / "scripts" / RUNNER.name)], env=env_for(tmp_path, "Linux"),
                         capture_output=True, text=True)
    assert out.returncode != 0 and "Mac only" in out.stderr


def test_the_installer_is_a_dry_run_by_default_and_writes_nothing(tmp_path):
    env = env_for(tmp_path, "Darwin")
    out = subprocess.run(["/bin/bash", str(INSTALL)], env=env, capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert "dry run" in out.stdout and "<key>StartCalendarInterval</key>" in out.stdout
    assert str(ROOT) in out.stdout and f"{env['HOME']}/tmp/registry-publish" in out.stdout
    assert not (Path(env["HOME"]) / "Library").exists() and not (tmp_path / "launchctl.log").exists()


def test_the_installer_applies_and_uninstalls_only_on_a_mac(tmp_path):
    env = env_for(tmp_path, "Darwin")
    out = subprocess.run(["/bin/bash", str(INSTALL), "--apply"], env=env, capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    plist = Path(env["HOME"]) / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    assert plistlib.loads(plist.read_bytes())["ProgramArguments"][1] == f"{ROOT}/scripts/registry_publish_mac.sh"
    assert "bootstrap" in (tmp_path / "launchctl.log").read_text()
    out = subprocess.run(["/bin/bash", str(INSTALL), "--uninstall"], env=env, capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0 and not plist.exists()
    linux = tmp_path / "linux"
    linux.mkdir()
    out = subprocess.run(["/bin/bash", str(INSTALL), "--apply"], env=env_for(linux, "Linux"), capture_output=True,
                         text=True, cwd=ROOT)
    assert out.returncode != 0 and "Mac only" in out.stderr


@pytest.mark.parametrize("path", [RUNNER, INSTALL])
def test_the_scripts_are_executable(path):
    assert os.access(path, os.X_OK)
