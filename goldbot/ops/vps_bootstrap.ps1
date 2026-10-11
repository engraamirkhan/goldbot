# goldbot Windows VPS bootstrap (run once in an elevated PowerShell on the VPS).
# Installs Python 3.11, git, NSSM, uv, Node 22 (as CI); clones the repo; builds the dashboard (web/dist);
# creates the service account layout; installs two portable MT5 terminals (IC Markets, Vantage) into
# C:\MT5\<broker>; registers the services.
# Credentials are NEVER in this script: before starting the services the owner stores them in Windows Credential
# Manager with `python -m goldbot.ops.accounts add|set` (services cannot prompt; see docs/RUNBOOK.md).
#
#   .\vps_bootstrap.ps1                       # services run as LocalSystem
#   .\vps_bootstrap.ps1 -RunAs ".\aamir"      # services run as the owner's Windows user (recommended)
#   .\vps_bootstrap.ps1 -BridgeOnly -RunAs ".\<user>" -Ref <40-hex sha>
#       MT5 BRIDGE box next to a Linux brain (e.g. a 1 GiB Azure free VM): only Python, Git, NSSM, Tailscale, one MT5
#       terminal, goldbot + MetaTrader5, a 4-6 GB pagefile, the OpenSSH server for the brain's tunnel (Tailscale
#       addresses only, user `mt5` may only forward to 127.0.0.1:8765) and ONE service, goldbot-bridge-<account>. No
#       Node, no dashboard, no engine (an engine here would be a second engine on the account), no cloudflared.
#       Afterwards: vps_bridge_authorize.ps1 for the brain's key; updates with vps_update.ps1 -BridgeOnly.
# Credential Manager is per user: the account that stored the secrets (accounts set/add) must be the account the
# services run as. With -RunAs the password is asked once via Get-Credential and handed to NSSM (which stores it
# with the service in the Windows service manager); it is never written to a file or echoed.
param(
  [string]$RunAs = "",         # optional NSSM ObjectName, e.g. ".\aamir" or "VPS01\aamir" (required with -BridgeOnly)
  [switch]$BridgeOnly,         # the MT5 bridge box of a Linux brain (see above)
  [string]$Ref = "",           # -BridgeOnly: the 40-hex commit id on main the owner checked (empty: main now)
  [string]$Account = "icm-demo"
)
$ErrorActionPreference = "Stop"
$NodeMajor = "22"              # match CI (.github/workflows/ci.yml NODE_VERSION)
$Repo = "https://github.com/engraamirkhan/goldbot.git"
$Root = "C:\goldbot"

function Install-BridgeOnly {
  if (-not $RunAs) { throw "-BridgeOnly needs -RunAs: Credential Manager is per user, the bridge service runs as you" }
  if ($Ref -and ($Ref -notmatch '^[0-9a-f]{40}$')) { throw "-Ref must be a 40-hex commit id on main" }
  if ($Account -notmatch '^[a-z0-9-]{1,40}$') { throw "-Account must be an account id from config/accounts.yaml" }
  $svc = "goldbot-bridge-$Account"
  Start-Transcript -Path "$env:ProgramData\goldbot-bootstrap.log" -Append | Out-Null

  # 1 GiB of RAM: a fixed 4-6 GB pagefile on C: (B2ats v2 has no temporary disk); takes effect after a restart
  $cs = Get-CimInstance Win32_ComputerSystem
  if ($cs.AutomaticManagedPagefile) { Set-CimInstance -InputObject $cs -Property @{ AutomaticManagedPagefile = $false } }
  $pf = Get-CimInstance Win32_PageFileSetting | Where-Object { $_.Name -ieq "C:\pagefile.sys" }
  if (-not $pf) { $pf = New-CimInstance -ClassName Win32_PageFileSetting -Property @{ Name = "C:\pagefile.sys" } }
  Set-CimInstance -InputObject $pf -Property @{ InitialSize = [uint32]4096; MaximumSize = [uint32]6144 }

  foreach ($id in "Python.Python.3.11", "Git.Git", "NSSM.NSSM", "Tailscale.Tailscale") {
    winget install -e --id $id --silent --accept-package-agreements --accept-source-agreements
  }
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
  if (-not (Test-Path "$Root\.git")) {
    git clone $Repo $Root
    if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
    if ($Ref) {
      git -C $Root checkout --detach $Ref
      if ($LASTEXITCODE -ne 0) { throw "commit $Ref not found" }
    }
  }
  Set-Location $Root
  New-Item -ItemType Directory -Force -Path "$Root\state", "$Root\logs", "C:\MT5\ICMarkets" | Out-Null
  python -m pip install --upgrade pip uv
  python -m uv venv .venv
  .\.venv\Scripts\python -m pip install -e ".[live]"     # goldbot + MetaTrader5 (win32 only) + pydantic>=2.12 (ADR 0010)
  if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
  if (-not (Test-Path "C:\MT5\ICMarkets\terminal64.exe")) {       # one terminal: the path config/accounts.yaml expects
    $mt5 = "$env:TEMP\mt5setup.exe"
    Invoke-WebRequest -Uri "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -OutFile $mt5
    Start-Process $mt5 -ArgumentList "/auto /path:C:\MT5\ICMarkets" -Wait
  }
  w32tm /config /manualpeerlist:"time.cloudflare.com,0x8" /syncfromflags:manual /update
  w32tm /resync

  # the brain's tunnel (goldbot-bridge-tunnel on the brain connects as mt5@<this box's Tailscale address>)
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
  Set-Service sshd -StartupType Automatic
  Start-Service sshd
  Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue | Set-NetFirewallRule -RemoteAddress "100.64.0.0/10"
  if (-not (Get-LocalUser -Name "mt5" -ErrorAction SilentlyContinue)) {
    $bytes = New-Object byte[] 32                                   # a password nobody ever sees: key logins only
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $pw = ConvertTo-SecureString ([Convert]::ToBase64String($bytes) + "aA1!") -AsPlainText -Force
    New-LocalUser -Name "mt5" -Password $pw -PasswordNeverExpires -UserMayNotChangePassword `
      -Description "goldbot brain tunnel: port forwarding to the bridge only" | Out-Null
    $pw = $null
  }
  $keys = "$env:ProgramData\ssh\goldbot_authorized_keys"
  if (-not (Test-Path $keys)) { New-Item -ItemType File -Path $keys | Out-Null }
  icacls $keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F" | Out-Null
  $conf = "$env:ProgramData\ssh\sshd_config"
  if (-not (Select-String -Path $conf -Pattern "^# goldbot tunnel" -Quiet)) {
    Add-Content -Path $conf -Value @"

# goldbot tunnel (vps_bootstrap.ps1 -BridgeOnly): the brain forwards to the bridge, nothing else
Match User mt5
    AuthorizedKeysFile __PROGRAMDATA__/ssh/goldbot_authorized_keys
    PasswordAuthentication no
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:8765
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
"@
  }
  Restart-Service sshd

  # ONE service: the bridge, as the owner's user (its Credential Manager holds the listen address, token and login)
  $py = "$Root\.venv\Scripts\python.exe"
  $cred = Get-Credential -UserName $RunAs -Message "Windows password for $RunAs (the bridge service runs as this user)"
  icacls "$Root\state" /grant "$($RunAs):(OI)(CI)M" | Out-Null
  icacls "$Root\logs" /grant "$($RunAs):(OI)(CI)M" | Out-Null
  nssm install $svc $py "-m goldbot.ops.run bridge $Account"
  nssm set $svc AppDirectory $Root
  nssm set $svc AppStdout "$Root\logs\$svc.log"
  nssm set $svc AppStderr "$Root\logs\$svc.err"
  nssm set $svc AppRotateFiles 1
  nssm set $svc AppRotateBytes 10485760
  nssm set $svc AppExit Default Restart
  nssm set $svc AppRestartDelay 10000
  nssm set $svc Start SERVICE_AUTO_START
  nssm set $svc ObjectName $cred.UserName $cred.GetNetworkCredential().Password | Out-Null
  $cred = $null
  Stop-Transcript | Out-Null
  Write-Host "Bridge box installed, nothing started. Restart Windows once (pagefile), then as ${RunAs}: tailscale up;"
  Write-Host "  log in to MT5 once (Save password); .\.venv\Scripts\python -m goldbot.ops.accounts bridge-serve $Account"
  Write-Host "  .\goldbot\ops\vps_bridge_authorize.ps1 '<the brain key line>'; nssm start $svc"
}

if ($BridgeOnly) { Install-BridgeOnly; exit 0 }

winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements
winget install -e --id NSSM.NSSM --silent --accept-package-agreements --accept-source-agreements
winget install -e --id Tailscale.Tailscale --silent --accept-package-agreements --accept-source-agreements

# Node.js 22 (latest 22.x MSI from nodejs.org, checked against the published SHA-256) unless node 22 is present
$haveNode = $false
try { $haveNode = ((node --version) -like "v$NodeMajor.*") } catch { $haveNode = $false }
if (-not $haveNode) {
  $rel = (Invoke-RestMethod "https://nodejs.org/dist/index.json") | Where-Object { $_.version -like "v$NodeMajor.*" } | Select-Object -First 1
  $msiName = "node-$($rel.version)-x64.msi"
  $msi = "$env:TEMP\$msiName"
  Invoke-WebRequest -Uri "https://nodejs.org/dist/$($rel.version)/$msiName" -OutFile $msi
  $sums = (Invoke-WebRequest -Uri "https://nodejs.org/dist/$($rel.version)/SHASUMS256.txt" -UseBasicParsing).Content
  $expected = (($sums -split "`n") | Where-Object { $_ -match "\s$([regex]::Escape($msiName))$" }) -split "\s+" | Select-Object -First 1
  if ((Get-FileHash $msi -Algorithm SHA256).Hash -ne $expected.ToUpper()) { throw "Node MSI checksum mismatch" }
  Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart" -Wait
}
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

if (-not (Test-Path $Root)) { git clone $Repo $Root }
Set-Location $Root
python -m pip install --upgrade pip uv
python -m uv venv .venv
.\.venv\Scripts\python -m pip install -e ".[live]"

# Dashboard: the API serves web/dist (re-run these two lines after every git pull that touches web/)
Push-Location "$Root\web"
npm ci
if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
npm run build
if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
Pop-Location

# MT5 portable terminals (installer from MetaQuotes; brokers' branded builds also work)
New-Item -ItemType Directory -Force -Path C:\MT5\ICMarkets, C:\MT5\Vantage, $Root\state, $Root\logs | Out-Null
$mt5 = "$env:TEMP\mt5setup.exe"
if (-not (Test-Path "C:\MT5\ICMarkets\terminal64.exe")) {
  Invoke-WebRequest -Uri "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -OutFile $mt5
  Start-Process $mt5 -ArgumentList "/auto /path:C:\MT5\ICMarkets" -Wait
  Start-Process $mt5 -ArgumentList "/auto /path:C:\MT5\Vantage" -Wait
}

# Time sync hourly
w32tm /config /manualpeerlist:"time.cloudflare.com,0x8" /syncfromflags:manual /update
w32tm /resync

# Services (restart on failure, logs rotated by NSSM)
$py = "$Root\.venv\Scripts\python.exe"
$svcCred = $null
if ($RunAs) {
  # asked interactively, kept only in memory; the account needs "Log on as a service" (NSSM grants it)
  $svcCred = Get-Credential -UserName $RunAs -Message "Windows password for $RunAs (services run as this user)"
  # the service user needs write access to state\ and logs\
  icacls "$Root\state" /grant "$($RunAs):(OI)(CI)M" | Out-Null
  icacls "$Root\logs" /grant "$($RunAs):(OI)(CI)M" | Out-Null
}
$svcs = @(
  @{ name="goldbot-supervisor"; args="-m goldbot.ops.run supervisor" },
  @{ name="goldbot-engine-icm"; args="-m goldbot.ops.run engine icm-demo" },
  @{ name="goldbot-engine-vantage"; args="-m goldbot.ops.run engine vantage-demo" },
  @{ name="goldbot-api"; args="-m goldbot.ops.run api" },
  @{ name="goldbot-webhook"; args="-m goldbot.ops.run webhook" },
  @{ name="goldbot-scheduler"; args="-m goldbot.ops.run scheduler" },
  @{ name="goldbot-telegram"; args="-m goldbot.ops.run telegram" },
  @{ name="goldbot-news"; args="-m goldbot.ops.run news" }
)
foreach ($s in $svcs) {
  nssm install $s.name $py $s.args
  nssm set $s.name AppDirectory $Root
  nssm set $s.name AppStdout "$Root\logs\$($s.name).log"
  nssm set $s.name AppStderr "$Root\logs\$($s.name).err"
  nssm set $s.name AppRotateFiles 1
  nssm set $s.name AppRotateBytes 10485760
  nssm set $s.name AppExit Default Restart
  nssm set $s.name AppRestartDelay 5000
  nssm set $s.name Start SERVICE_AUTO_START
  if ($svcCred) {
    nssm set $s.name ObjectName $svcCred.UserName $svcCred.GetNetworkCredential().Password | Out-Null
  }
}
$svcCred = $null
Write-Host "Installed. Log in to each MT5 terminal once (demo accounts), then: nssm start goldbot-supervisor; nssm start goldbot-engine-icm ..."
Write-Host "Check with: $py -m goldbot.ops.run health   Later updates: .\goldbot\ops\vps_update.ps1 -Ref origin/main (keeps this service list)"

# ---- Public HTTPS access from any device (no VPN): Cloudflare Tunnel in front of the API (port 8787).
# Free Cloudflare account + a domain (or a free *.cfargotunnel.com hostname). The tunnel token is entered
# ONCE by the owner on the VPS (it is a secret): `cloudflared service install <TOKEN>`. The API enforces
# login (email + password + authenticator) and roles for everyone, including on the public URL.
winget install -e --id Cloudflare.cloudflared --silent --accept-package-agreements --accept-source-agreements
Write-Host "Then: cloudflared service install <tunnel token>  (from the Cloudflare Zero Trust dashboard; route the hostname to http://localhost:8787)"
