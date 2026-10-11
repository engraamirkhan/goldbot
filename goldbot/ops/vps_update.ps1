# goldbot safe update on the Windows VPS (run in an elevated PowerShell in C:\goldbot):
#
#   .\goldbot\ops\vps_update.ps1 -Ref origin/main            # or a tag / commit sha
#   .\goldbot\ops\vps_update.ps1 -Ref <sha> -Force           # even while a scheduler job is running
#   .\goldbot\ops\vps_update.ps1 -Ref <sha> -BridgeOnly      # a bridge box (vps_bootstrap.ps1 -BridgeOnly): only its
#                                                            # bridge service; the brain's health judges the rest
#
# What it does, in order:
#   1. records a health baseline and the current commit (the rollback target);
#   2. fetches and resolves -Ref BEFORE stopping anything (no network wait while the engines are down);
#   3. refuses to start while a scheduler job is mid-run (a killed job is not re-run for that slot) unless -Force;
#      then stops the scheduler FIRST and checks again, so no job can start between the check and the stop;
#   4. stops the other services in the order below, checks out the new commit, installs, steps aux.db back to
#      schema 4 when the commit predates schema compatibility (Sync-AuxSchema), rebuilds the dashboard if web/
#      changed, runs the static health checks (settings, accounts, secrets, disk);
#   5. starts the services, waits, runs the full health checks against the baseline;
#   6. on any new fail (or a failed step in 4) rolls back to the previous commit the same way and says so.
#
# Service order (why):
#   Stop:  scheduler (step 3), webhook, news, api, telegram -> nothing new can come in and no entry can be approved
#          supervisor                               -> engines that are still running now fail closed for entries
#                                                      (heartbeat > 60 s) but keep managing exits
#          engines LAST                             -> exits are never gated: an engine closes positions on its time
#                                                      barrier and the 12% kill switch until the very last moment.
#                                                      While it is down every position keeps its server-side stop
#                                                      loss and take profit at the broker; only time-barrier exits
#                                                      wait, so the down window is kept to install + checks.
#   Start: supervisor FIRST (fresh heartbeat, else engines start in fail-closed halt), engines next (shortest
#          unmanaged window), then api, telegram, webhook, scheduler, news.
#
# Notes: open proposals expire while the engines are down (the Telegram service reports them as expired).
# config\accounts.yaml and config\settings.yaml are edited on the VPS (MT5 logins, Telegram ids); git keeps those
# edits across the checkout unless the new commit changes the same file, in which case the checkout fails and the
# script restarts the old version untouched. Credentials stay in Windows Credential Manager; nothing here reads them.
param(
  [Parameter(Mandatory = $true)][string]$Ref,
  [switch]$Force,
  [switch]$BridgeOnly,           # the bridge box of a Linux brain: stop the bridge, check out, install, start, check
  [string]$Account = "icm-demo",
  [int]$SettleSeconds = 90       # wait after start: supervisor heartbeat (5 s), scheduler, first news poll
)
$ErrorActionPreference = "Stop"
$Root = "C:\goldbot"
$py = "$Root\.venv\Scripts\python.exe"
Set-Location $Root

# ---- bridge box (vps_bootstrap.ps1 -BridgeOnly). Do it with the book flat or at the weekend: while the bridge is
# down the brain's engine cannot reach the terminal (it restarts every 10 s and reconciles on reconnect; open positions
# keep their broker-side stop and target). The bridge is healthy when GET /health answers with the new commit and the
# service has not restarted; otherwise the previous commit is restored the same way.
function Test-Bridge([string]$Sha) {
  $before = (Get-CimInstance Win32_Service -Filter "Name='goldbot-bridge-$Account'").ProcessId
  Start-Sleep -Seconds $SettleSeconds
  $after = (Get-CimInstance Win32_Service -Filter "Name='goldbot-bridge-$Account'").ProcessId
  if (-not $after -or $after -ne $before) { Write-Warning "bridge service restarted or stopped during the check"; return $false }
  try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 10 } catch { Write-Warning "bridge /health did not answer"; return $false }
  if (-not $h.ok -or $h.commit -ne $Sha) { Write-Warning "bridge /health reports commit $($h.commit), expected $Sha"; return $false }
  Write-Host "bridge healthy on $Sha (api_version $($h.api_version), zone $($h.server_tz))"
  return $true
}

function Install-Bridge([string]$Sha) {
  nssm stop "goldbot-bridge-$Account" | Out-Host
  $ok = $true
  git checkout --detach $Sha | Out-Host
  if ($LASTEXITCODE -ne 0) { Write-Warning "git checkout $Sha failed"; $ok = $false }
  if ($ok) {
    python -m uv sync --frozen --link-mode copy --python-preference only-system --extra live --python 3.11 | Out-Host     # uv.lock; copies
    if ($LASTEXITCODE -ne 0) { Write-Warning "uv sync failed"; $ok = $false }
  }
  nssm start "goldbot-bridge-$Account" | Out-Host    # always: a stopped bridge cuts the engine off from the terminal
  Start-Sleep -Seconds 5
  return $ok
}

if ($BridgeOnly) {
  if ($Account -notmatch '^[a-z0-9-]{1,40}$') { throw "-Account must be an account id from config/accounts.yaml" }
  $OldSha = (git rev-parse HEAD).Trim()
  git fetch origin
  if ($LASTEXITCODE -ne 0) { throw "git fetch failed; nothing was stopped" }
  $NewSha = git rev-parse --verify --quiet "$Ref^{commit}"
  if ($LASTEXITCODE -ne 0 -or -not $NewSha) { throw "unknown ref $Ref; nothing was stopped" }
  $NewSha = "$NewSha".Trim()
  if ($NewSha -eq $OldSha) { Write-Host "already at $NewSha; nothing to do"; exit 0 }
  Write-Host "bridge update $OldSha -> $NewSha (rollback target $OldSha)"
  if ((Install-Bridge $NewSha) -and (Test-Bridge $NewSha)) { Write-Host "bridge update to $NewSha done"; exit 0 }
  Write-Warning "bridge update to $NewSha failed; rolling back to $OldSha"
  if ((Install-Bridge $OldSha) -and (Test-Bridge $OldSha)) { Write-Warning "rolled back to $OldSha (healthy)"; exit 1 }
  Write-Warning "ROLLBACK to $OldSha is not healthy either: read logs\goldbot-bridge-$Account.err"
  exit 2
}

# Service names exactly as registered by vps_bootstrap.ps1, in STOP order (start order is built below).
$StopOrder = @(
  "goldbot-webhook", "goldbot-news", "goldbot-scheduler", "goldbot-api", "goldbot-telegram",
  "goldbot-supervisor",
  "goldbot-engine-icm", "goldbot-engine-vantage"
)
$StartOrder = @(
  "goldbot-supervisor",
  "goldbot-engine-icm", "goldbot-engine-vantage",
  "goldbot-api", "goldbot-telegram", "goldbot-webhook", "goldbot-scheduler", "goldbot-news"
)

function Stop-Goldbot {
  foreach ($s in $StopOrder) {
    Write-Host "stopping $s"
    nssm stop $s | Out-Null          # NSSM sends Ctrl-C, then terminates after its timeout
    if ($LASTEXITCODE -ne 0) { Write-Warning "nssm stop $s returned $LASTEXITCODE (not installed or already stopped)" }
  }
}

function Start-Goldbot {
  foreach ($s in $StartOrder) {
    Write-Host "starting $s"
    nssm start $s | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Warning "nssm start $s returned $LASTEXITCODE" }
    if ($s -eq "goldbot-supervisor") { Start-Sleep -Seconds 10 }   # first heartbeat before the engines start
  }
}

# A commit from before the state store's step 3 (2026-10-10) knows aux.db only up to schema 4 and refuses the newer
# file (its dashboard would not start). Schema 5 only added tables, so it is stepped back to 4 with every row kept;
# the next update re-applies 5 without losing them. Code from step 3 on records compat_back_to and needs nothing.
$AuxDowngrade = @'
# BEGIN aux-downgrade
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
have = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
if have <= 4:
    sys.exit(0)
if have != 5:
    print(f"aux.db schema version {have}: only 5 -> 4 is known, left unchanged")
    sys.exit(3)
conn.execute("DELETE FROM schema_version WHERE version = 5")
conn.execute("PRAGMA user_version = 4")
conn.commit()
print("aux.db schema 5 -> 4 for the older release (rows kept; the next update re-applies 5)")
# END aux-downgrade
'@

function Sync-AuxSchema {
  $mig = "$Root\goldbot\db\migrations.py"
  $aux = "$Root\state\aux.db"
  if (-not (Test-Path $mig) -or -not (Test-Path $aux)) { return }
  if (Select-String -Path $mig -Pattern "compat_back_to" -Quiet) { return }    # this commit opens newer files
  $script = Join-Path $env:TEMP "goldbot_aux_downgrade.py"
  Set-Content -Path $script -Value $AuxDowngrade -Encoding UTF8
  & $py $script $aux | Out-Host
  if ($LASTEXITCODE -ne 0) { Write-Warning "aux.db could not be stepped back for this commit: its dashboard will not open it" }
}

# Running scheduler jobs (names) from aux.db, else scheduler.json (a release before `goldbot.db scheduler-status`).
function Get-RunningJobs {
  $sch = $null
  $schJson = $null
  try { $schJson = & $py -m goldbot.db scheduler-status --state-dir "$Root\state" 2>$null } catch { $schJson = $null }
  if ($LASTEXITCODE -eq 0 -and $schJson) { $sch = ($schJson -join "`n") | ConvertFrom-Json }
  elseif (Test-Path "$Root\state\scheduler.json") { $sch = Get-Content "$Root\state\scheduler.json" -Raw | ConvertFrom-Json }
  $running = @()
  if ($sch) {
    foreach ($j in $sch.jobs.PSObject.Properties) {
      $st = $j.Value
      if ($st.last_started -and (-not $st.last_finished -or ([datetime]$st.last_started -gt [datetime]$st.last_finished))) {
        $running += "$($j.Name) (started $($st.last_started))"
      }
    }
  }
  return ,$running
}

# Check out a commit and install it; returns $true when every step succeeded. Every native command's output goes to
# Out-Host: in PowerShell anything a function leaves on the pipeline becomes part of its return value.
function Install-Commit([string]$Sha, [string]$OldSha) {
  git checkout --detach $Sha | Out-Host
  if ($LASTEXITCODE -ne 0) { Write-Warning "git checkout $Sha failed"; return $false }
  python -m uv sync --frozen --link-mode copy --python-preference only-system --extra live --python 3.11 | Out-Host   # as vps_bootstrap.ps1
  if ($LASTEXITCODE -ne 0) { Write-Warning "uv sync failed"; return $false }
  Sync-AuxSchema                                  # services are stopped here
  # rebuild the dashboard only when web/ changed (the API serves web\dist)
  git diff --quiet $OldSha $Sha -- web
  if ($LASTEXITCODE -ne 0) {
    Push-Location "$Root\web"
    npm ci | Out-Host
    $ok = ($LASTEXITCODE -eq 0)
    if ($ok) { npm run build | Out-Host; $ok = ($LASTEXITCODE -eq 0) }
    Pop-Location
    if (-not $ok) { Write-Warning "dashboard build failed"; return $false }
  }
  # static checks need no running service: the new code imports and settings/accounts/secrets/disk are sane
  & $py -m goldbot.ops.run health --static --baseline "$Root\logs\health_before.json" | Out-Host
  if ($LASTEXITCODE -ne 0) { Write-Warning "static health checks failed"; return $false }
  return $true
}

# ---- 1. baseline: what already fails is not blamed on the update
New-Item -ItemType Directory -Force -Path "$Root\logs" | Out-Null
& $py -m goldbot.ops.run health --out "$Root\logs\health_before.json" | Out-Null
$OldSha = (git rev-parse HEAD).Trim()
Write-Host "current commit $OldSha (rollback target)"

# ---- 2. fetch and resolve the target while everything is still running
git fetch --tags origin
if ($LASTEXITCODE -ne 0) { throw "git fetch failed; nothing was stopped" }
$NewSha = git rev-parse --verify --quiet "$Ref^{commit}"
if ($LASTEXITCODE -ne 0 -or -not $NewSha) { throw "unknown ref $Ref; nothing was stopped" }
$NewSha = "$NewSha".Trim()
if ($NewSha -eq $OldSha) { Write-Host "already at $NewSha; nothing to do"; exit 0 }
Write-Host "updating $OldSha -> $NewSha"

# ---- 3. do not kill a scheduler job mid-run (its slot is already recorded and would not run again)
#      the status comes from aux.db (state store step 3), which a failed scheduler.json export cannot leave stale.
#      The scheduler is stopped first and checked again: a job that started in between was interrupted by the stop,
#      so the scheduler is started again and the update refused (nothing else was stopped).
if (-not $Force) {
  $running = Get-RunningJobs
  if ($running.Count -gt 0) { throw "scheduler job $($running -join ', ') is running; retry later or pass -Force" }
}
Write-Host "stopping goldbot-scheduler"
nssm stop goldbot-scheduler | Out-Null
if (-not $Force) {
  $running = Get-RunningJobs
  if ($running.Count -gt 0) {
    nssm start goldbot-scheduler | Out-Null
    throw "scheduler job $($running -join ', ') started while the scheduler was stopping and was interrupted; scheduler restarted, nothing else stopped; retry later"
  }
}

# ---- 4. stop the rest (order above; the scheduler is already down), check out, install, static checks
Stop-Goldbot
$installed = Install-Commit $NewSha $OldSha

# ---- 5. start and check against the baseline
$healthy = $false
if ($installed) {
  Start-Goldbot
  Start-Sleep -Seconds $SettleSeconds
  & $py -m goldbot.ops.run health --out "$Root\logs\health_after.json" --baseline "$Root\logs\health_before.json"
  $healthy = ($LASTEXITCODE -eq 0)
}
if ($healthy) {
  Write-Host "update to $NewSha done and healthy"
  exit 0
}

# ---- 6. roll back to the previous commit, same order, same checks
Write-Warning "update to $NewSha failed; rolling back to $OldSha"
Stop-Goldbot
$back = Install-Commit $OldSha $NewSha
Start-Goldbot
Start-Sleep -Seconds $SettleSeconds
& $py -m goldbot.ops.run health --baseline "$Root\logs\health_before.json"
if (-not $back -or $LASTEXITCODE -ne 0) {
  Write-Warning "ROLLBACK to $OldSha is not healthy either: check logs\*.err and python -m goldbot.ops.run health"
  exit 2
}
Write-Warning "rolled back to $OldSha (healthy); the update to $NewSha was NOT applied"
exit 1
