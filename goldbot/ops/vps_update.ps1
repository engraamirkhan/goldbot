# goldbot safe update on the Windows VPS (run in an elevated PowerShell in C:\goldbot):
#
#   .\goldbot\ops\vps_update.ps1 -Ref origin/main            # or a tag / commit sha
#   .\goldbot\ops\vps_update.ps1 -Ref <sha> -Force           # even while a scheduler job is running
#
# What it does, in order:
#   1. records a health baseline and the current commit (the rollback target);
#   2. fetches and resolves -Ref BEFORE stopping anything (no network wait while the engines are down);
#   3. refuses to start while a scheduler job is mid-run (a killed job is not re-run for that slot) unless -Force;
#   4. stops the services in the order below, checks out the new commit, installs, rebuilds the dashboard if web/
#      changed, runs the static health checks (settings, accounts, secrets, disk);
#   5. starts the services, waits, runs the full health checks against the baseline;
#   6. on any new fail (or a failed step in 4) rolls back to the previous commit the same way and says so.
#
# Service order (why):
#   Stop:  webhook, news, scheduler, api, telegram  -> nothing new can come in and no entry can be approved
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
  [int]$SettleSeconds = 90       # wait after start: supervisor heartbeat (5 s), scheduler, first news poll
)
$ErrorActionPreference = "Stop"
$Root = "C:\goldbot"
$py = "$Root\.venv\Scripts\python.exe"
Set-Location $Root

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

# Check out a commit and install it; returns $true when every step succeeded. Every native command's output goes to
# Out-Host: in PowerShell anything a function leaves on the pipeline becomes part of its return value.
function Install-Commit([string]$Sha, [string]$OldSha) {
  git checkout --detach $Sha | Out-Host
  if ($LASTEXITCODE -ne 0) { Write-Warning "git checkout $Sha failed"; return $false }
  & $py -m pip install -e ".[live]" | Out-Host   # same extras as vps_bootstrap.ps1
  if ($LASTEXITCODE -ne 0) { Write-Warning "pip install failed"; return $false }
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
$schFile = "$Root\state\scheduler.json"
if ((Test-Path $schFile) -and -not $Force) {
  $sch = Get-Content $schFile -Raw | ConvertFrom-Json
  foreach ($j in $sch.jobs.PSObject.Properties) {
    $st = $j.Value
    if ($st.last_started -and (-not $st.last_finished -or ([datetime]$st.last_started -gt [datetime]$st.last_finished))) {
      throw "scheduler job $($j.Name) is running (started $($st.last_started)); retry later or pass -Force"
    }
  }
}

# ---- 4. stop (order above), check out, install, static checks
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
