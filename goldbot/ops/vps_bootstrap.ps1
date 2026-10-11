# goldbot Windows VPS bootstrap (run once in an elevated PowerShell on the VPS).
# Installs Python 3.11, git, NSSM, uv, Node 22 (as CI); clones the repo; builds the dashboard (web/dist);
# creates the service account layout; installs two portable MT5 terminals (IC Markets, Vantage) into
# C:\MT5\<broker>; registers the services.
# Credentials are NEVER in this script: before starting the services the owner stores them in Windows Credential
# Manager with `python -m goldbot.ops.accounts add|set` (services cannot prompt; see docs/RUNBOOK.md).
#
#   .\vps_bootstrap.ps1                       # services run as LocalSystem
#   .\vps_bootstrap.ps1 -RunAs ".\aamir"      # services run as the owner's Windows user (recommended)
#   .\vps_bootstrap.ps1 -BridgeOnly -Ref <40-hex sha>
#       MT5 BRIDGE box next to a Linux brain (e.g. a 1 GiB Azure free VM): only Python, Git, NSSM, Tailscale, one MT5
#       terminal, goldbot + MetaTrader5, a 4-6 GB pagefile, the OpenSSH server for the brain's tunnel (Tailscale
#       interface only, user `mt5` may only forward to 127.0.0.1:8765) and ONE service, goldbot-bridge-<account>, run
#       as the non-admin local user goldbot-bridge (created here; you choose its password). No
#       Node, no dashboard, no engine (an engine here would be a second engine on the account), no cloudflared.
#       Afterwards: vps_bridge_authorize.ps1 for the brain's key; updates with vps_update.ps1 -BridgeOnly.
# Credential Manager is per user: the account that stored the secrets (accounts set/add) must be the account the
# services run as. With -RunAs the password is asked once via Get-Credential and handed to NSSM (which stores it
# with the service in the Windows service manager); it is never written to a file or echoed.
param(
  [string]$RunAs = "",         # optional NSSM ObjectName, e.g. ".\aamir" or "VPS01\aamir" (all-in-one only)
  [switch]$BridgeOnly,         # the MT5 bridge box of a Linux brain (see above)
  [string]$Ref = "",           # -BridgeOnly: the 40-hex commit id on main the owner checked (empty: main now)
  [string]$Account = "icm-demo"
)
$ErrorActionPreference = "Stop"
$NodeMajor = "22"              # match CI (.github/workflows/ci.yml NODE_VERSION)
$Repo = "https://github.com/engraamirkhan/goldbot.git"
$Root = "C:\goldbot"
$UvVersion = "0.11.15"         # pinned uv (security review L1); bump deliberately

# Windows ACLs (security review H1b): the tree and the terminals are written by Administrators only; the service user
# reads them and writes only its own folders (and its terminal's folder, which MT5's live update writes). Without
# this, C:\goldbot inherits C:\'s "Authenticated Users: create/append", so any local user could plant code the
# services run. $User may be empty (services as LocalSystem): then only SYSTEM and Administrators keep access.
function Set-GoldbotAcl([string]$User, [string[]]$Terminals, [string[]]$Writable = @("state", "logs")) {
  icacls $Root /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "icacls $Root failed" }
  icacls C:\MT5 /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "icacls C:\MT5 failed" }
  if (-not $User) { return }
  icacls $Root /grant "${User}:(OI)(CI)RX" | Out-Null
  icacls C:\MT5 /grant "${User}:(OI)(CI)RX" | Out-Null
  foreach ($d in $Writable) {
    if ($d -eq "state") { icacls "$Root\state" /grant "${User}:(OI)(CI)M" | Out-Null }
    if ($d -eq "logs") { icacls "$Root\logs" /grant "${User}:(OI)(CI)M" | Out-Null }
    if ($d -eq "data") { icacls "$Root\data" /grant "${User}:(OI)(CI)M" | Out-Null }
    if ($d -eq "models") { icacls "$Root\models" /grant "${User}:(OI)(CI)M" | Out-Null }
  }
  foreach ($t in $Terminals) { icacls "C:\MT5\$t" /grant "${User}:(OI)(CI)M" | Out-Null }
}

# The MT5 installer runs as Administrator, so it must carry a valid Authenticode signature from MetaQuotes
# (security review M2); a tampered or unsigned download is refused before it runs.
function Assert-MetaQuotesSigned([string]$Path) {
  $sig = Get-AuthenticodeSignature $Path
  if ((Get-AuthenticodeSignature $Path).Status -ne 'Valid') { throw "mt5setup.exe signature is $($sig.Status): not run" }
  if ($sig.SignerCertificate.Subject -notmatch 'MetaQuotes') { throw "mt5setup.exe is not signed by MetaQuotes: not run" }
}

function Install-BridgeOnly {
  if ($Ref -and ($Ref -notmatch '^[0-9a-f]{40}$')) { throw "-Ref must be a 40-hex commit id on main" }
  if ($Account -notmatch '^[a-z0-9-]{1,40}$') { throw "-Account must be an account id from config/accounts.yaml" }
  $svc = "goldbot-bridge-$Account"
  # the bridge runs as its own NON-admin local user (security review H1c), never as the RDP administrator
  $BridgeUser = "goldbot-bridge"
  Start-Transcript -Path "$env:ProgramData\goldbot-bootstrap.log" -Append | Out-Null

  # 1 GiB of RAM: a fixed 4-6 GB pagefile on C: (B2ats v2 has no temporary disk); takes effect after a restart
  $cs = Get-CimInstance Win32_ComputerSystem
  if ($cs.AutomaticManagedPagefile) { Set-CimInstance -InputObject $cs -Property @{ AutomaticManagedPagefile = $false } }
  $pf = Get-CimInstance Win32_PageFileSetting | Where-Object { $_.Name -ieq "C:\pagefile.sys" }
  if (-not $pf) { $pf = New-CimInstance -ClassName Win32_PageFileSetting -Property @{ Name = "C:\pagefile.sys" } }
  Set-CimInstance -InputObject $pf -Property @{ InitialSize = [uint32]4096; MaximumSize = [uint32]6144 }

  # the bridge user's password is chosen by the owner (password manager): it is needed again for `runas` (MT5 login
  # and bridge-serve as this user); it goes to New-LocalUser and NSSM only, never to a file or the screen
  $cred = Get-Credential -UserName $BridgeUser -Message "Choose a password for the local user $BridgeUser (save it in your password manager first)"
  if (-not (Get-LocalUser -Name $BridgeUser -ErrorAction SilentlyContinue)) {
    New-LocalUser -Name $BridgeUser -Password $cred.Password -PasswordNeverExpires `
      -Description "goldbot MT5 bridge service (not an administrator)" | Out-Null
  }
  try { Add-LocalGroupMember -Group Users -Member $BridgeUser -ErrorAction Stop } catch { Write-Host "$BridgeUser already in Users" }

  # Python for the whole machine (C:\Program Files): a per-user install under the admin's profile is unreadable for
  # goldbot-bridge, and the venv's python.exe runs the base interpreter
  winget install -e --id Python.Python.3.11 --scope machine --silent --accept-package-agreements --accept-source-agreements
  foreach ($id in "Git.Git", "NSSM.NSSM", "Tailscale.Tailscale") {
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
  } elseif ($Ref) {           # a re-run never moves an installed tree; say so when it is on another commit
    $head = "$(git -C $Root rev-parse HEAD)".Trim()
    if ($head -ne $Ref) { Write-Warning "WARNING: $Root is at $head, not $Ref: left as it is (update with vps_update.ps1 -BridgeOnly -Ref $Ref)" }
  }
  Set-Location $Root
  New-Item -ItemType Directory -Force -Path "$Root\state", "$Root\logs", "C:\MT5\ICMarkets" | Out-Null
  # exact versions from uv.lock (security review L1). Run as Administrator: the bridge user may only read the tree.
  # copies, not hardlinks (a hardlink keeps the admin's uv cache ACL); the machine-wide Python only, never a uv-managed one
  python -m pip install --upgrade "uv==$UvVersion"
  python -m uv sync --frozen --link-mode copy --python-preference only-system --extra live --python 3.11     # goldbot + MetaTrader5 + pydantic>=2.12
  if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
  if (-not (Test-Path "C:\MT5\ICMarkets\terminal64.exe")) {       # one terminal: the path config/accounts.yaml expects
    $mt5 = "$env:TEMP\mt5setup.exe"
    Invoke-WebRequest -UseBasicParsing -Uri "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -OutFile $mt5
    Assert-MetaQuotesSigned $mt5
    Start-Process $mt5 -ArgumentList "/auto /path:C:\MT5\ICMarkets" -Wait
  }
  Set-GoldbotAcl -User $BridgeUser -Terminals @("ICMarkets")
  w32tm /config /manualpeerlist:"time.cloudflare.com,0x8" /syncfromflags:manual /update
  w32tm /resync

  # the brain's tunnel (goldbot-bridge-tunnel on the brain connects as mt5@<this box's Tailscale address>)
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
  Set-Service sshd -StartupType Automatic
  Start-Service sshd
  # SSH only on the Tailscale interface and from Tailscale addresses (security review L4)
  if (-not (Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)) {   # some images lack it
    New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" -Direction Inbound -Protocol TCP `
      -LocalPort 22 -Action Allow -RemoteAddress "100.64.0.0/10" | Out-Null
  }
  # Bind to the Tailscale adapter when it exists; before `tailscale up` it may not, and that must not stop the install
  try {
    Set-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -InterfaceAlias "Tailscale" -RemoteAddress "100.64.0.0/10" -ErrorAction Stop
  } catch {
    Set-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -RemoteAddress "100.64.0.0/10"
    Write-Warning "SSH is limited to Tailscale addresses (100.64.0.0/10) but not yet bound to the Tailscale adapter. After 'tailscale up' run: Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -InterfaceAlias Tailscale"
  }
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
    AllowStreamLocalForwarding no
    ForceCommand cmd.exe /c exit 1
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
"@
  }
  Restart-Service sshd

  # ONE service: the bridge, as the bridge user (its Credential Manager holds the listen address, token and login)
  $py = "$Root\.venv\Scripts\python.exe"
  nssm install $svc $py "-m goldbot.ops.run bridge $Account"
  nssm set $svc AppDirectory $Root
  nssm set $svc AppStdout "$Root\logs\$svc.log"
  nssm set $svc AppStderr "$Root\logs\$svc.err"
  nssm set $svc AppRotateFiles 1
  nssm set $svc AppRotateBytes 10485760
  nssm set $svc AppExit Default Restart
  nssm set $svc AppRestartDelay 10000
  nssm set $svc Start SERVICE_AUTO_START
  nssm set $svc ObjectName ".\$BridgeUser" $cred.GetNetworkCredential().Password | Out-Null
  # the service user must be able to run the venv and import the bridge (Python, packages and tree readable for it)
  $check = Start-Process -FilePath $py -ArgumentList '-c "import goldbot.execution.bridge, MetaTrader5; print(1)"' `
    -Credential (New-Object System.Management.Automation.PSCredential(".\$BridgeUser", $cred.Password)) `
    -WorkingDirectory $Root -LoadUserProfile -WindowStyle Hidden -Wait -PassThru
  $cred = $null
  if ($check.ExitCode -ne 0) {
    throw "$BridgeUser cannot run $py (exit $($check.ExitCode)): check that Python is installed for all users and that icacls $Root lists $BridgeUser (RX)"
  }
  Write-Host "check: $BridgeUser runs the bridge's Python"
  Stop-Transcript | Out-Null
  Write-Host "Bridge box installed. The bridge service is NOT started (the SSH server is). Restart Windows once (pagefile)."
  Write-Host "Then (docs/deploy/first-deploy.md):"
  Write-Host "  tailscale up --unattended;  runas /user:$BridgeUser C:\MT5\ICMarkets\terminal64.exe   (log in once, Save password, exit)"
  Write-Host "  runas /user:$BridgeUser `"cmd /k cd /d C:\goldbot && .venv\Scripts\python -m goldbot.ops.accounts bridge-serve $Account`""
  Write-Host "  .\goldbot\ops\vps_bridge_authorize.ps1 '<the brain key line>'; nssm restart $svc"
}

if ($BridgeOnly) { Install-BridgeOnly; exit 0 }

winget install -e --id Python.Python.3.11 --scope machine --silent --accept-package-agreements --accept-source-agreements
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
  Invoke-WebRequest -UseBasicParsing -Uri "https://nodejs.org/dist/$($rel.version)/$msiName" -OutFile $msi
  $sums = (Invoke-WebRequest -Uri "https://nodejs.org/dist/$($rel.version)/SHASUMS256.txt" -UseBasicParsing).Content
  $expected = (($sums -split "`n") | Where-Object { $_ -match "\s$([regex]::Escape($msiName))$" }) -split "\s+" | Select-Object -First 1
  if ((Get-FileHash $msi -Algorithm SHA256).Hash -ne $expected.ToUpper()) { throw "Node MSI checksum mismatch" }
  Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart" -Wait
}
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

if (-not (Test-Path $Root)) { git clone $Repo $Root }
Set-Location $Root
python -m pip install --upgrade pip "uv==$UvVersion"
python -m uv sync --frozen --link-mode copy --python-preference only-system --extra live --python 3.11   # uv.lock; copies; machine Python
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

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
  Invoke-WebRequest -UseBasicParsing -Uri "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -OutFile $mt5
  Assert-MetaQuotesSigned $mt5
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
}
# Administrators own the tree and the terminals; the service user reads them and writes state, logs, data (the bar
# store) and models (the scheduler's retrain) only (security review H1b)
New-Item -ItemType Directory -Force -Path "$Root\data", "$Root\models" | Out-Null
Set-GoldbotAcl -User $RunAs -Terminals @("ICMarkets", "Vantage") -Writable @("state", "logs", "data", "models")
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
