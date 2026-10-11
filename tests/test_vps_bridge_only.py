"""Bridge-only Windows box (vps_bootstrap.ps1 / vps_update.ps1 -BridgeOnly, vps_bridge_authorize.ps1): the MT5 bridge
next to a Linux brain on a 1 GiB VM. No PowerShell runs in CI, so these guards read the scripts:

* the bridge-only path leaves before anything of the all-in-one install (no engine service: a second engine on the
  account; no Node build, no cloudflared, no scheduler on 1 GiB) and registers exactly one service, the bridge;
* it needs -RunAs (Credential Manager is per user) and a full commit id when pinned, sets a fixed pagefile, and opens
  SSH to Tailscale addresses only, with user mt5 limited to forwarding to the bridge port;
* the updater's bridge path never touches the all-in-one service list or health baseline, judges the bridge by its own
  /health commit and a stable process, and rolls back otherwise;
* the authorize helper accepts exactly what goldbot-mt5-authorize accepts on Linux."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "goldbot" / "ops"
BOOT = (OPS / "vps_bootstrap.ps1").read_text(encoding="utf-8")
UPD = (OPS / "vps_update.ps1").read_text(encoding="utf-8")
AUTH = (OPS / "vps_bridge_authorize.ps1").read_text(encoding="utf-8")


def _body(text: str, func: str) -> str:
    start = text.index(f"function {func}")
    end = re.search(r"^}", text[start:], re.M)              # the function's closing brace sits in column 0
    assert end is not None
    return text[start:start + end.start()]


def test_bridge_only_leaves_before_the_all_in_one_install():
    branch = BOOT.index("if ($BridgeOnly) { Install-BridgeOnly; exit 0 }")
    for marker in ("winget install -e --id Python.Python.3.11 --silent", "npm ci", "$svcs = @(", "--id Cloudflare.cloudflared"):
        assert branch < BOOT.index(marker), marker


def test_bridge_only_installs_one_bridge_service_and_nothing_else():
    body = _body(BOOT, "Install-BridgeOnly")
    assert body.count("nssm install ") == 1 and 'nssm install $svc $py "-m goldbot.ops.run bridge $Account"' in body
    assert '$svc = "goldbot-bridge-$Account"' in body
    for banned in ("engine", "supervisor", "scheduler", "telegram", "npm", "node", "cloudflared", "Vantage"):
        assert banned not in body.lower().replace("goldbot-bridge", ""), banned


def test_bridge_only_guards_its_inputs_and_sets_the_pagefile():
    body = _body(BOOT, "Install-BridgeOnly")
    assert 'if (-not $RunAs) { throw' in body
    assert "$Ref -notmatch '^[0-9a-f]{40}$'" in body and "$Account -notmatch '^[a-z0-9-]{1,40}$'" in body
    assert "AutomaticManagedPagefile = $false" in body and "InitialSize = [uint32]4096; MaximumSize = [uint32]6144" in body
    assert body.index("Set-CimInstance -InputObject $pf") < body.index("winget install")   # before the heavy installs


def test_the_tunnel_is_tailscale_only_and_forwarding_only():
    body = _body(BOOT, "Install-BridgeOnly")
    assert 'Set-NetFirewallRule -RemoteAddress "100.64.0.0/10"' in body
    block = body[body.index("Match User mt5"):body.index('"@')]
    for line in ("AuthorizedKeysFile __PROGRAMDATA__/ssh/goldbot_authorized_keys", "PasswordAuthentication no",
                 "AllowTcpForwarding local", "PermitOpen 127.0.0.1:8765", "PermitTTY no", "X11Forwarding no"):
        assert line in block
    assert 'icacls $keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"' in body


def test_the_bridge_update_never_touches_the_all_in_one_services():
    start = UPD.index("if ($BridgeOnly) {")
    end = re.search(r"^}", UPD[start:], re.M)
    assert end is not None and start + end.start() < UPD.index("# ---- 1. baseline")    # before anything is stopped
    branch = UPD[start:start + end.start()]
    assert "exit 0" in branch and "exit 1" in branch and "exit 2" in branch
    for banned in ("Stop-Goldbot", "Start-Goldbot", "goldbot.ops.run health", "health_before", "Get-RunningJobs"):
        assert banned not in branch, banned
    test = _body(UPD, "Test-Bridge")
    assert '"http://127.0.0.1:8765/health"' in test and "$h.commit -ne $Sha" in test and "$after -ne $before" in test
    assert branch.index("Install-Bridge $NewSha") < branch.index("Install-Bridge $OldSha")      # rollback after


# the authorize helper's pattern, read from the script and run with Python's re (same syntax for this pattern)
AUTH_RE = re.search(r"-cnotmatch '([^']+)'", AUTH)


@pytest.mark.parametrize("key, ok", [
    ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBbH goldbot@brain", True),
    ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBbH", True),
    ('command="cmd" ssh-ed25519 AAAAC3Nza', False),                # options are root's to add, never the caller's
    ("ssh-rsa AAAAB3NzaC1yc2E goldbot@brain", False),
    ("ssh-ed25519 AAAAé x", False),
    ("ssh-ed25519 AAAA x\x1b[2J", False),
])
def test_the_authorize_helper_accepts_one_plain_ed25519_line(key, ok):
    assert AUTH_RE is not None
    assert (re.fullmatch(AUTH_RE.group(1).strip("^$"), key) is not None) is ok
    assert "if ($Key -match \"[`r`n]\"" in AUTH                     # a second line is refused before the pattern
    assert 'restrict,port-forwarding,permitopen=`"127.0.0.1:8765`" $Key' in AUTH
