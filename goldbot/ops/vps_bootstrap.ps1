# goldbot Windows VPS bootstrap (run once in an elevated PowerShell on the VPS).
# Installs Python 3.11, git, NSSM, uv; clones the repo; creates the service account layout; installs two
# portable MT5 terminals (IC Markets, Vantage) into C:\MT5\<broker>; registers the services.
# Credentials are NEVER in this script: before starting the services the owner stores them in Windows Credential
# Manager with `python -m goldbot.ops.accounts add|set` (services cannot prompt; see docs/RUNBOOK.md).
$ErrorActionPreference = "Stop"
$Repo = "https://github.com/engraamirkhan/goldbot.git"
$Root = "C:\goldbot"

winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements
winget install -e --id NSSM.NSSM --silent --accept-package-agreements --accept-source-agreements
winget install -e --id Tailscale.Tailscale --silent --accept-package-agreements --accept-source-agreements
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

if (-not (Test-Path $Root)) { git clone $Repo $Root }
Set-Location $Root
python -m pip install --upgrade pip uv
python -m uv venv .venv
.\.venv\Scripts\python -m pip install -e ".[live]"

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
}
Write-Host "Installed. Log in to each MT5 terminal once (demo accounts), then: nssm start goldbot-supervisor; nssm start goldbot-engine-icm ..."

# ---- Public HTTPS access from any device (no VPN): Cloudflare Tunnel in front of the API (port 8787).
# Free Cloudflare account + a domain (or a free *.cfargotunnel.com hostname). The tunnel token is entered
# ONCE by the owner on the VPS (it is a secret): `cloudflared service install <TOKEN>`. The API enforces
# login (email + password + authenticator) and roles for everyone, including on the public URL.
winget install -e --id Cloudflare.cloudflared --silent --accept-package-agreements --accept-source-agreements
Write-Host "Then: cloudflared service install <tunnel token>  (from the Cloudflare Zero Trust dashboard; route the hostname to http://localhost:8787)"
