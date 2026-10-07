# goldbot Windows VPS bootstrap (run once in an elevated PowerShell on the VPS).
# Installs Python 3.11, git, NSSM, uv, Node 22 (as CI); clones the repo; builds the dashboard (web/dist);
# creates the service account layout; installs two portable MT5 terminals (IC Markets, Vantage) into
# C:\MT5\<broker>; registers the services.
# Credentials are NEVER in this script: before starting the services the owner stores them in Windows Credential
# Manager with `python -m goldbot.ops.accounts add|set` (services cannot prompt; see docs/RUNBOOK.md).
#
#   .\vps_bootstrap.ps1                       # services run as LocalSystem
#   .\vps_bootstrap.ps1 -RunAs ".\aamir"      # services run as the owner's Windows user (recommended)
# Credential Manager is per user: the account that stored the secrets (accounts set/add) must be the account the
# services run as. With -RunAs the password is asked once via Get-Credential and handed to NSSM (which stores it
# with the service in the Windows service manager); it is never written to a file or echoed.
param(
  [string]$RunAs = ""          # optional NSSM ObjectName, e.g. ".\aamir" or "VPS01\aamir"
)
$ErrorActionPreference = "Stop"
$NodeMajor = "22"              # match CI (.github/workflows/ci.yml NODE_VERSION)
$Repo = "https://github.com/engraamirkhan/goldbot.git"
$Root = "C:\goldbot"

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
