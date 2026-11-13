---
number: 215
rank: 75
title: Publish the brain's registry outbox from the Mac on a schedule
stage: 'ops (follow-up of R140, 2026-11-13)'
size: S
status: 'proposed · accountable: sre · reviews: security-reviewer, code-reviewer'
---
- **215. Publish the brain's registry outbox from the Mac on a schedule.** Value: the brain token stays read-only (decision 2026-11-01), so `scripts/registry_publish.sh` is the only way the brain's trial rows reach release `research-v1`, and it is run by hand: rows waited six days (incident 2026-11-13). Acceptance: a launchd agent (pattern of `scripts/install_autodisc_launchd.sh`: dry run by default, `--apply`, `--uninstall`) runs the publish daily and on wake, logs one line to `~/tmp/registry-publish/`, exits non-zero on failure; shellcheck clean; `feedback_loop` on the brain shows the registry ok within a day of install. Alternative to weigh first: a separate fine-grained token scoped to Contents write on this repo only, stored in the brain keyring (security-reviewer decides; the 2026-11-01 decision stands until then).
