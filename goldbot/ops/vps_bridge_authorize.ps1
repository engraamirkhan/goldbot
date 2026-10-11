# Authorise the brain's tunnel key on a Windows bridge box (vps_bootstrap.ps1 -BridgeOnly). Elevated PowerShell:
#
#   .\goldbot\ops\vps_bridge_authorize.ps1 'ssh-ed25519 AAAA... goldbot@brain'
#
# The Windows twin of goldbot-mt5-authorize on the Linux MT5 box: exactly one ssh-ed25519 public key line is accepted
# (no options, no second line, printable ASCII), and it is written with `restrict,port-forwarding,permitopen` and a
# forced `cmd.exe /c exit 1`, so the key can only forward to the bridge on 127.0.0.1:8765 (`ssh -N` runs no
# command). The key file is the one sshd_config's `Match User mt5` names,
# readable by Administrators and SYSTEM only.
param([Parameter(Mandatory = $true)][string]$Key)
$ErrorActionPreference = "Stop"
if ($Key -match "[`r`n]" -or $Key -cnotmatch '^ssh-ed25519 [A-Za-z0-9+/=]+( [\x20-\x7E]*)?$') {
  throw "refused: expected one ssh-ed25519 public key line (the brain's id_ed25519.pub)"
}
$keys = "$env:ProgramData\ssh\goldbot_authorized_keys"
if (-not (Test-Path $keys)) { throw "run vps_bootstrap.ps1 -BridgeOnly first ($keys is missing)" }
Add-Content -Path $keys -Value "restrict,port-forwarding,permitopen=`"127.0.0.1:8765`",command=`"cmd.exe /c exit 1`" $Key" -Encoding ascii
icacls $keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F" | Out-Null
Write-Host "authorised (forwarding to 127.0.0.1:8765 only)"
