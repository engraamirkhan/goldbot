# 2026-11-13: trial-registry sync failed for 13 days with no alert (HTTP 403, then an unpublished outbox)

Status: closed 2026-11-14 (R140 deployed on the brain, outbox published from the Mac, weekly proposals marked)
Severity: S3 (research bookkeeping: the release copy of the registry fell behind the brain's; trading, exits, alerts unaffected)
Author: sre. Reviewed by: code-reviewer, security-reviewer.

## Summary
The brain's `github-token` has `Contents: Read-only` on the repository (kept read-only by decision 2026-11-01), so every
upload of the trial registry to release `research-v1` gets HTTP 403. From 2026-10-31 the sync failed with a 403 recorded
only in job results (`registry_sync: "failed: ..."` in monthly_research and cpcv_quarterly). From 2026-11-07 the sync
parks the rows in `state/registry_outbox/` for the Mac to publish (`scripts/registry_publish.sh`), a manual step nothing
schedules, and logs it at INFO. The rows have waited six days; nothing alerted.

## Impact
- The release copy of the registry (read by research.yml) lacks the brain's rows since 2026-11-07. The brain's own
  registry, the registry of record since 2026-11-09, is complete: no trial lost, K on the brain correct.
- Trading, exits, approvals and the owner's alerts: unaffected.

## Timeline (UTC)
| Time | Event |
| --- | --- |
| 2026-10-31 12:00 | first `trial registry sync failed: HTTP Error 403: Forbidden` (WARNING in the scheduler journal) |
| 2026-10-31 15:03 | cpcv_quarterly result carries the 403 (`RegistryForbidden` text naming the missing permission) |
| 2026-11-01 08:00 | monthly_research result carries the 403; decision: the brain token stays read-only, the Mac publishes |
| 2026-11-07 12:00 | outbox written by the weekly sync (tournament, research_director); INFO log only |
| 2026-11-13 | found by the review-loop audit (lane R1); R140 adds the check |
| 2026-11-13/14 | R140 deployed on the brain (PR #393); `scripts/registry_publish.sh` run from the Mac: 3259 rows published to `research-v1` |
| 2026-11-14 | unread weekly roll-ups marked read by the research director (Mac session); incident closed (BACKLOG 217) |

## Root cause
Not a fault in the HTTP code: the credential is read-only by design, and the error text says so. The fault is in
detection: the handoff to a manual Mac step had no deadline and no alarm, and a job whose sync failed still finished
`last_ok: true`, so every scheduler check stayed green.

## Detection
Late, by an audit. From R140: health `feedback_loop` FAILs on a failed latest sync or on rows waiting more than 36 h,
the daily `loop_check` job comments on the `ci-loop` issue, and the outbox keeps a `pending_since` stamp so a newer
payload does not reset the age.

## Recovery
Publish from the Mac with `scripts/registry_publish.sh` (RUNBOOK "Feedback loop"); the brain clears the outbox on its
next sync.

## Prevention
- Shipped: R140 `feedback_loop` health check and `loop_check` job (tests/test_loop_health.py).
- Shipped 2026-11-14: BACKLOG 217 (deploy, publish the waiting outbox, mark the proposals); BACKLOG 215 (launchd
  agent `com.goldbot.registry-publish` publishes daily once installed on the Mac, tests/test_registry_publish_launchd.py);
  BACKLOG 216 (the Mac research director reads and marks weekly proposals, `scripts/weekly_proposals.sh`,
  tests/test_weekly_inbox.py).
