# 2026-11-02: the brain's deploy timer was enabled but never started

Status: open (closed when the timer runs on the brain and a Telegram-approved deploy is recorded in deploys.jsonl)
Severity: S3 latent (no deploy lost: every deploy since 2026-10-16 ran by hand; trading never affected)
Author: sre. Reviewed by: pending (code-reviewer).

## Summary
`goldbot-deploy.timer` on the brain showed `enabled`, `inactive (dead)`, `Trigger: n/a`. It had never been active since
boot. Approved deploys from Telegram (`state/deploy/approved.json`) would have waited forever with no signal.

## Impact
- None observed: deploys.jsonl (85 records) shows deploys done by hand with `sudo goldbot-deploy`, and no approval
  file was waiting on 2026-11-02. The one-click Telegram deploy path did not work for 17 days without anyone knowing.
- Trading and exits: unaffected (the timer only applies deploys).

## Timeline (UTC)
| Time | Event |
| --- | --- |
| 2026-10-16 17:03 | brain boots (still the current boot) |
| 2026-10-16 17:12:53 | `brain_bootstrap.sh` installs the timer and runs `systemctl enable goldbot-deploy.timer` (no `--now`) |
| 2026-10-16 to 11-01 | every deploy run by hand; the timer never fires (`LastTriggerUSec` empty, service never started) |
| 2026-11-02 06:50 | found during the TradingView unit retirement (read-only `systemctl show`) |

## Root cause
`systemctl enable` only links the timer into `timers.target.wants`; it starts at the next boot. The bootstrap ran
after boot and the brain has not rebooted since, so the timer was never started. Preflight checked `is-enabled` only,
which said `enabled`, so the setup check passed. Nothing watched for an approval that was not applied.

## Detection (why late)
No check looked at the timer's active state or at the age of an approved deploy. Manual deploys masked it.

## Prevention (shipped on chore/retire-tv-units)
- `brain_bootstrap.sh`: `systemctl enable --now goldbot-deploy.timer`.
- `goldbot run preflight`: `deploy_timer` requires enabled AND active.
- Health: `deploy_approval` FAILs (alerts) when `state/deploy/approved.json` is older than 15 min.
- Tests: tests/test_deploy_timer_and_retired_units.py, tests/test_setup_preflight.py.
- Rule: every timer or service a bootstrap installs for immediate use is enabled with `--now`; a setup check tests
  `is-active`, never `is-enabled` alone.

## Recovery
`sudo systemctl enable --now goldbot-deploy.timer`, then `systemctl list-timers goldbot-deploy.timer` shows NEXT.
