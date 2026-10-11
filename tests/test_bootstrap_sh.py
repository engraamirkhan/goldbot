"""The Linux bootstrap scripts (goldbot/ops/linux/brain_bootstrap.sh, mt5_bootstrap.sh) are run by hand as root over
SSH for the first deploy (docs/deploy/first-deploy.md). They must:

* take an optional commit id, refuse anything that is not 40 lowercase hex before doing anything (no root needed to
  see the refusal), and put root's own ops clone and the service user's tree on exactly that commit, so both VMs run
  the code the owner checked on GitHub's main, whatever merges in between;
* ignore SIGHUP and SIGPIPE before the first change (docs/AGENT_STANDARDS.md "Root-run tooling") and keep a root-only
  log, so a dropped session never leaves a half-installed box with no record;
* give the 1 GB MT5 box 4 GB of swap, and a brain on a fallback box under 4 GB the same
  (docs/deploy/free-hosting-options.md)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

LINUX = Path(__file__).resolve().parents[1] / "goldbot" / "ops" / "linux"
SCRIPTS = {"brain": LINUX / "brain_bootstrap.sh", "mt5": LINUX / "mt5_bootstrap.sh"}
BASH = shutil.which("bash") or ""
FIRST_CHANGE = re.compile(r"^\s*(?:apt-get|dpkg|fallocate|useradd|install|systemctl|git clone|mkdir)\b", re.M)


def _text(role: str) -> str:
    return SCRIPTS[role].read_text(encoding="utf-8")


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("role", sorted(SCRIPTS))
@pytest.mark.parametrize("ref", ["main", "abc123", "A" * 40, "0" * 39, "0" * 40 + ";id", "../" + "0" * 37])
def test_a_ref_that_is_not_a_full_commit_id_is_refused_before_anything(role, ref):
    out = subprocess.run([BASH, str(SCRIPTS[role]), ref], capture_output=True, text=True, timeout=20)
    assert out.returncode == 2
    assert "usage:" in out.stdout and "40-hex" in out.stdout


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("role", sorted(SCRIPTS))
def test_a_valid_ref_reaches_the_root_check(role):
    out = subprocess.run([BASH, str(SCRIPTS[role]), "0123456789abcdef0123456789abcdef01234567"],
                         capture_output=True, text=True, timeout=20)
    if out.returncode == 0:      # pragma: no cover - only if the suite ever runs as root, which it must not
        pytest.fail("bootstrap ran as root in a test")
    assert "usage:" not in out.stdout
    assert "run with sudo" in out.stdout or "x86_64" in out.stdout


@pytest.mark.parametrize("role", sorted(SCRIPTS))
def test_hangup_and_pipe_are_ignored_before_the_first_change(role):
    s = _text(role)
    trap = s.index("trap '' PIPE HUP")
    first = FIRST_CHANGE.search(s)
    assert first is not None and trap < first.start()
    assert 'tee -a "$LOG" --output-error=warn' in s and "LOG=/root/goldbot-bootstrap.log" in s


@pytest.mark.parametrize("role", sorted(SCRIPTS))
def test_both_clones_land_on_the_pinned_commit(role):
    s = _text(role)
    user = "goldbot" if role == "brain" else "mt5"
    assert 'fetch -q --depth 1 "$REPO" "$REF"' in s and "checkout -q --detach FETCH_HEAD" in s
    assert '[ "$(git -C "$OPS_SRC/src" rev-parse HEAD)" = "$REF" ]' in s
    assert f'[ -z "$REF" ] || sudo -u {user} -H git -C "$ROOT" checkout -q --detach "$REF"' in s


def test_the_mt5_box_gets_4_gb_of_swap():
    assert "fallocate -l 4G /swapfile" in _text("mt5")


def test_a_small_fallback_brain_gets_swap_and_the_a1_shape_none():
    s = _text("brain")
    assert "-lt 4000000 ] && ! swapon --show | grep -q /swapfile" in s and "fallocate -l 4G /swapfile" in s
    assert s.index("fallocate -l 4G") < s.index("npm ci")


def test_m1_the_brain_key_on_the_mt5_box_can_run_no_command():
    s = _text("mt5")
    line = next(x for x in s.splitlines() if "restrict,port-forwarding" in x)
    assert 'command=\\"/bin/false\\"' in line and 'permitopen=\\"127.0.0.1:8765\\"' in line
    assert "(no shell, no other ports)" not in s                    # the old comment claimed what the line lacked


@pytest.mark.parametrize("role", sorted(SCRIPTS))
def test_l2_an_existing_tree_on_another_commit_is_called_out(role):
    assert 'WARNING: $ROOT is at $head, not $REF' in _text(role)


def test_l1_the_brain_installs_python_packages_from_the_lockfile():
    s = _text("brain")
    assert "$UV sync --frozen --extra live --python 3.11" in s and "pip install" not in s


def test_switching_the_tunnel_target_restarts_a_running_tunnel():
    """`enable --now` leaves a running tunnel on the old host (first-deploy.md 6B: switching to option B)."""
    s = _text("brain")
    assert "systemctl enable goldbot-bridge-tunnel && systemctl restart goldbot-bridge-tunnel" in s
    assert "enable --now goldbot-bridge-tunnel" not in s
