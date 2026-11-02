# 2026-11: retire the TradingView pull and webhook units, start the deploy timer

Owner decision 2026-11-02 (remove unused feeds). This release deletes `goldbot-tv-pull.service`
and `goldbot-webhook.service` from the repo, drops `webhook`/`tv-pull` from the deploy
script's heartbeat list (`BEAT_SERVICES`) and the health check registry, and drops `goldbot-webhook` from the
Windows VPS scripts. `goldbot-tv-ideas` (the followed authors' ideas, RUNBOOK 2.7d) is KEPT and keeps running
(owner, 2026-11-02): do not stop it. Server data is never touched: `tv.db`, `tv_ideas.db`, `tv_indicators/` and the old heartbeat files
stay where they are.

## Order (brain)

1. Before the deploy, stop and remove the retired unit, so the new code (which has no `tv-pull` or `webhook`
   command) never restarts them into a crash loop that fails the deploy's health window. One line, as the
   `ubuntu` user:

   `sudo systemctl disable --now goldbot-tv-pull && sudo rm -f /etc/systemd/system/goldbot-tv-pull.service && sudo systemctl daemon-reload`

   `goldbot-webhook` was never installed on the brain (check: `systemctl cat goldbot-webhook` says "No files found").
   If it was, add it to the same line.
2. Deploy as usual (`sudo goldbot-deploy <sha>`).
3. Start the deploy timer (see "Deploy timer" below): `sudo systemctl enable --now goldbot-deploy.timer`
4. Check: `systemctl list-units 'goldbot-tv-*' --all` lists only `goldbot-tv-ideas.service`, active; `systemctl list-timers goldbot-deploy.timer`
   shows a NEXT time; `goldbot run health` has no `heartbeat:tv-pull`/`heartbeat:webhook` and no `deploy_approval`.

The old `state/heartbeat_tv-pull.json` is now read by nothing; leave it (server data) or let the owner remove it.

## Rollback

Go back with `sudo goldbot-deploy --rollback <previous sha>`, then reinstall the units from git at that commit, root
from its own clone (never from `/opt/goldbot`), exactly as RUNBOOK 2.7c T4 describes (`U=goldbot-tv-pull`), then
`sudo systemctl enable --now goldbot-tv-pull`. Its data was kept, so pulling resumes where it stopped.
`goldbot-tv-ideas` is not touched by this release or its rollback.

## Windows VPS (if one still runs the old service set)

`vps_update.ps1` no longer stops or starts `goldbot-webhook`. Before the update, once, in an admin PowerShell:
`nssm stop goldbot-webhook; nssm remove goldbot-webhook confirm`

## Deploy timer

Found 2026-11-02 (docs/incidents/2026-11-02-deploy-timer-never-started.md): on the brain `goldbot-deploy.timer` was
enabled but inactive (`Trigger: n/a`) since the bootstrap of 2026-10-16. Fixed in the repo: the bootstrap now uses
`enable --now`, preflight requires the timer active, and health FAILs (`deploy_approval`) when an approved deploy has
waited more than 15 min. On the live brain it needs the one-line start in step 3; there is no pending approval today,
so starting it applies nothing until the owner next taps [Deploy].
Rollback: `sudo systemctl disable --now goldbot-deploy.timer` (deploys are then manual only, as before).
