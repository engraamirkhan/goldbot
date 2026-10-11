"""Bridge-only Windows box (vps_bootstrap.ps1 / vps_update.ps1 -BridgeOnly, vps_bridge_authorize.ps1): the MT5 bridge
next to a Linux brain on a 1 GiB VM. No PowerShell runs in CI, so these guards read the scripts:

* the bridge-only path leaves before anything of the all-in-one install (no engine service: a second engine on the
  account; no Node build, no cloudflared, no scheduler on 1 GiB) and registers exactly one service, the bridge;
* it runs the bridge as its own non-admin user, needs a full commit id when pinned, sets a fixed pagefile, and opens
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
    for marker in ("winget install -e --id NSSM.NSSM --silent", "npm ci", "$svcs = @(", "--id Cloudflare.cloudflared"):
        assert branch < BOOT.index(marker), marker


def test_bridge_only_installs_one_bridge_service_and_nothing_else():
    body = _body(BOOT, "Install-BridgeOnly")
    assert body.count("nssm install ") == 1 and 'nssm install $svc $py "-m goldbot.ops.run bridge $Account"' in body
    assert '$svc = "goldbot-bridge-$Account"' in body
    for banned in ("engine", "supervisor", "scheduler", "telegram", "npm", "node", "cloudflared", "Vantage"):
        assert banned not in body.lower().replace("goldbot-bridge", ""), banned


def test_bridge_only_guards_its_inputs_and_sets_the_pagefile():
    body = _body(BOOT, "Install-BridgeOnly")
    assert "$Ref -notmatch '^[0-9a-f]{40}$'" in body and "$Account -notmatch '^[a-z0-9-]{1,40}$'" in body
    assert "AutomaticManagedPagefile = $false" in body and "InitialSize = [uint32]4096; MaximumSize = [uint32]6144" in body
    assert body.index("Set-CimInstance -InputObject $pf") < body.index("winget install")   # before the heavy installs


def test_the_tunnel_is_tailscale_only_and_forwarding_only():
    body = _body(BOOT, "Install-BridgeOnly")
    assert '-InterfaceAlias "Tailscale" -RemoteAddress "100.64.0.0/10"' in body
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
    assert 'restrict,port-forwarding,permitopen=`"127.0.0.1:8765`",command=`"cmd.exe /c exit 1`" $Key' in AUTH


# ---------------------------------------------------------------------- security review fixes (H1, M2, L1, L2, L4)
def test_h1a_the_tunnel_user_can_run_nothing():
    body = _body(BOOT, "Install-BridgeOnly")
    block = body[body.index("Match User mt5"):body.index('"@')]
    assert "ForceCommand cmd.exe /c exit 1" in block and "AllowStreamLocalForwarding no" in block
    assert 'command=`"cmd.exe /c exit 1`"' in AUTH


ACL_ROOT = 'icacls $Root /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F"'
ACL_MT5 = 'icacls C:\\MT5 /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F"'


def test_h1b_the_tree_and_terminal_are_admin_owned_and_the_service_user_reads_only():
    acl = _body(BOOT, "Set-GoldbotAcl")
    assert ACL_ROOT in acl and ACL_MT5 in acl
    assert '"${User}:(OI)(CI)RX"' in acl
    for d in ("state", "logs"):
        assert f'icacls "$Root\\{d}" /grant "${{User}}:(OI)(CI)M"' in acl
    assert 'icacls "C:\\MT5\\$t" /grant "${User}:(OI)(CI)M"' in acl
    body = _body(BOOT, "Install-BridgeOnly")
    assert "Set-GoldbotAcl -User $BridgeUser -Terminals @(\"ICMarkets\")" in body
    full = BOOT[BOOT.index("if ($BridgeOnly) { Install-BridgeOnly; exit 0 }"):]
    assert "Set-GoldbotAcl -User $RunAs" in full                   # the all-in-one install too
    assert full.index("Set-GoldbotAcl") < full.index("$svcs = @(")
    assert "icacls \"$Root\\state\" /grant \"$($RunAs)" not in full       # the old grants are folded into the function


def test_h1c_the_bridge_runs_as_a_dedicated_non_admin_user():
    body = _body(BOOT, "Install-BridgeOnly")
    assert '$BridgeUser = "goldbot-bridge"' in body
    assert "New-LocalUser -Name $BridgeUser" in body
    assert "Add-LocalGroupMember -Group Users -Member $BridgeUser" in body
    assert "Administrators -Member $BridgeUser" not in body
    assert 'nssm set $svc ObjectName ".\\$BridgeUser"' in body
    assert "if (-not $RunAs)" not in body                          # the RDP admin is no longer the service account


def test_m2_the_mt5_installer_must_carry_a_valid_metaquotes_signature():
    assert BOOT.count("Assert-MetaQuotesSigned $mt5") == 2          # bridge-only and all-in-one
    sig = _body(BOOT, "Assert-MetaQuotesSigned")
    assert "(Get-AuthenticodeSignature $Path).Status -ne 'Valid'" in sig
    assert "SignerCertificate.Subject -notmatch 'MetaQuotes'" in sig
    for mode in (_body(BOOT, "Install-BridgeOnly"), BOOT[BOOT.index("if ($BridgeOnly) { Install-BridgeOnly"):]):
        assert mode.index("Assert-MetaQuotesSigned $mt5") < mode.index("Start-Process $mt5")


def test_l1_python_packages_come_from_the_lockfile():
    body = _body(BOOT, "Install-BridgeOnly")
    assert "python -m uv sync --frozen --link-mode copy --python-preference only-system --extra live --python 3.11" in body
    assert "pip install -e" not in body
    upd = _body(UPD, "Install-Bridge")
    assert "uv sync --frozen --link-mode copy --python-preference only-system --extra live --python 3.11" in upd and "pip install -e" not in upd


def test_l2_an_existing_tree_on_another_commit_is_called_out():
    body = _body(BOOT, "Install-BridgeOnly")
    assert "WARNING: $Root is at $head, not $Ref" in body


def test_l4_ssh_is_bound_to_the_tailscale_interface():
    body = _body(BOOT, "Install-BridgeOnly")
    assert '-InterfaceAlias "Tailscale" -RemoteAddress "100.64.0.0/10"' in body


# ---------------------------------------------------------------------- code/SRE review fixes
@pytest.mark.parametrize("name", ["vps_bootstrap.ps1", "vps_update.ps1"])
def test_no_pip_into_a_venv_uv_made_without_pip(name):
    """`uv venv` makes a venv with no pip, so a later `.venv\\Scripts\\python -m pip install` fails (option A dead at
    step 1b, updates leave the bridge stopped). Packages come from `uv sync --frozen` instead."""
    text = (OPS / name).read_text(encoding="utf-8")
    for m in re.finditer(r"uv venv(?![^\n]*--seed)", text):
        assert "-m pip install" not in text[m.end():], name
    assert not re.search(r"(\.venv\\Scripts\\python|\$py)\s+-m pip install", text), name
    assert "uv sync --frozen --link-mode copy --python-preference only-system --extra live --python 3.11" in text


def test_every_download_uses_basic_parsing():
    """Windows Server 2025's Windows PowerShell 5.1 can refuse Invoke-WebRequest without -UseBasicParsing."""
    for line in BOOT.splitlines():
        if "Invoke-WebRequest" in line and not line.lstrip().startswith("#"):
            assert "-UseBasicParsing" in line, line


def test_a_failed_bridge_install_still_restarts_the_bridge():
    inst = _body(UPD, "Install-Bridge")
    start = inst.index('nssm start "goldbot-bridge-$Account"')
    assert "return $false" not in inst[:start]                     # no early return between stop and start
    assert inst.index('nssm stop "goldbot-bridge-$Account"') < inst.index("git checkout") < start


def test_a_missing_ssh_firewall_rule_is_created_tailscale_only():
    body = _body(BOOT, "Install-BridgeOnly")
    assert 'New-NetFirewallRule -Name "OpenSSH-Server-In-TCP"' in body
    new = body[body.index("New-NetFirewallRule"):body.index("| Out-Null", body.index("New-NetFirewallRule"))]
    assert '-RemoteAddress "100.64.0.0/10"' in new and "-LocalPort 22" in new


# ---------------------------------------------------------------------- re-review fixes
def test_binding_to_the_tailscale_adapter_cannot_stop_the_install():
    """Before `tailscale up` the adapter may not exist: the bind is tried, and on failure the rule keeps the
    Tailscale address scope and the owner is told the one command to bind it later (first-deploy.md 3A)."""
    body = _body(BOOT, "Install-BridgeOnly")
    start = body.index("  try {\n    Set-NetFirewallRule")
    tr = body[start:body.index("\n  }\n", body.index("} catch {", start))]
    assert '-InterfaceAlias "Tailscale" -RemoteAddress "100.64.0.0/10" -ErrorAction Stop' in tr
    catch = tr[tr.index("} catch {"):]
    assert 'Set-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -RemoteAddress "100.64.0.0/10"' in catch
    assert "Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -InterfaceAlias Tailscale" in catch and "throw" not in catch


def test_python_is_machine_wide_and_uv_pinned_and_copying():
    assert BOOT.count("winget install -e --id Python.Python.3.11 --scope machine") == 2
    assert "Python.Python.3.11\", " not in BOOT                      # not in a per-user foreach any more
    assert re.search(r'\$UvVersion = "\d+\.\d+\.\d+"', BOOT) and BOOT.count('"uv==$UvVersion"') == 2
    for text in (BOOT, UPD):
        for line in text.splitlines():
            if "-m uv sync" in line and not line.lstrip().startswith("#"):
                assert "--link-mode copy" in line and "--python-preference only-system" in line, line


def test_the_bootstrap_proves_the_service_user_can_run_the_bridge_python():
    body = _body(BOOT, "Install-BridgeOnly")
    chk = body[body.index("$check = Start-Process"):body.index('Write-Host "check:')]
    assert "-FilePath $py" in chk and "import goldbot.execution.bridge, MetaTrader5" in chk
    assert '".\\$BridgeUser"' in chk and "-Wait -PassThru" in chk
    assert "if ($check.ExitCode -ne 0) {" in chk and "throw" in chk
    assert body.index('nssm set $svc ObjectName') < body.index("$check = Start-Process") < body.index("$cred = $null")
