"""goldbot-mt5-authorize (written by goldbot/ops/linux/mt5_bootstrap.sh) runs as root: it accepts exactly one
ssh-ed25519 public key line and appends it, restricted to forwarding 127.0.0.1:8765, AS mt5 (`sudo -u mt5`), so a
symlink mt5 plants in its own ~/.ssh can only reach mt5's files. Anything else (another key type, options, embedded
newlines, control characters) is refused before sudo runs."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BOOT = REPO / "goldbot" / "ops" / "linux" / "mt5_bootstrap.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBx9J0ZbH1k2VpXy+7n4Qm/8TtLgq0Wq3cY1rZkq5u0a"


def _wrapper() -> str:
    m = re.search(r"cat > /usr/local/bin/goldbot-mt5-authorize <<'WRAP'\n(.*?)\nWRAP\n", BOOT.read_text(), re.S)
    assert m, "wrapper not found in mt5_bootstrap.sh"
    return m.group(1)


def _run(tmp_path: Path, key: str, *ports: str) -> tuple[int, str, list[str] | None]:
    script = tmp_path / "authorize"
    script.write_text(_wrapper())
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    calls = tmp_path / "sudo_args"
    stub = bindir / "sudo"                       # records its argv, NUL-separated, and writes nothing
    stub.write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\0" "$a"; done > "{calls}"\n')
    stub.chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    assert BASH is not None
    p = subprocess.run([BASH, str(script), key, *ports], capture_output=True, text=True, env=env, timeout=30)
    args = calls.read_bytes().decode().split("\0")[:-1] if calls.exists() else None
    if calls.exists():
        os.remove(calls)
    return p.returncode, p.stdout + p.stderr, args


def test_a_valid_key_is_appended_as_mt5_restricted_to_the_bridge_port(tmp_path):
    for key in (KEY + " goldbot@brain", KEY):
        rc, out, args = _run(tmp_path, key)
        assert rc == 0, out
        assert args is not None and args[:3] == ["-u", "mt5", "-H"]
        assert args[-2] == f'restrict,port-forwarding,permitlisten="127.0.0.1:1",permitopen="127.0.0.1:8765",command="/bin/false" {key}'
        assert args[-1] == KEY.split()[1]                       # the key material: an older line of it is replaced
        assert "~/.ssh/authorized_keys" in args[-4] and "umask 077" in args[-4] and "grep -vF" in args[-4]
        assert "forwarding to 127.0.0.1:8765 only" in out


def test_adr_0062_one_line_forwards_to_every_bridge_port_of_a_two_terminal_box(tmp_path):
    rc, out, args = _run(tmp_path, KEY, "8765", "8766")
    assert rc == 0 and args is not None
    assert args[-2] == f'restrict,port-forwarding,permitlisten="127.0.0.1:1",permitopen="127.0.0.1:8765",' \
                       f'permitopen="127.0.0.1:8766",command="/bin/false" {KEY}'
    assert "127.0.0.1:8765, 127.0.0.1:8766 only" in out


@pytest.mark.parametrize("port", ["22", "8765;id", "", "99999", "-1"])
def test_a_bad_bridge_port_is_refused_before_sudo(tmp_path, port):
    rc, out, args = _run(tmp_path, KEY, port)
    assert rc != 0 and args is None and "refused: a bridge port" in out


def test_the_replacement_keeps_every_other_key_and_drops_the_old_line_of_this_one(tmp_path):
    """The mt5-side script itself, run without sudo on a scratch home."""
    m = re.search(r"sudo -u mt5 -H sh -c '([^']+)'", _wrapper())
    assert m
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    keys = home / ".ssh" / "authorized_keys"
    keys.write_text(f'restrict,permitopen="127.0.0.1:8765" {KEY} old\nssh-ed25519 AAAAOTHER other\n')
    body = KEY.split()[1]
    subprocess.run(["sh", "-c", m.group(1), "_", f"restrict,new {KEY}", body], env={"HOME": str(home),
                   "PATH": "/usr/bin:/bin"}, check=True, timeout=30)
    assert keys.read_text().splitlines() == ["ssh-ed25519 AAAAOTHER other", f"restrict,new {KEY}"]


@pytest.mark.parametrize("key", [
    KEY + "\nssh-ed25519 AAAAEVIL other",                 # a second line would add an unrestricted key
    KEY + " c\n",
    KEY + "\r",
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ== x",
    'command="/bin/sh" ' + KEY,                          # options are ours to set, never the caller's
    "ssh-ed25519 AAAA$(id)",
    "ssh-ed25519 ",
    KEY + " comment\x1b]0;x\x07",
    "",
])
def test_anything_but_one_ed25519_key_line_is_refused_before_sudo(tmp_path, key):
    rc, out, args = _run(tmp_path, key)
    assert rc != 0 and args is None and "refused: expected one ssh-ed25519 public key line" in out
