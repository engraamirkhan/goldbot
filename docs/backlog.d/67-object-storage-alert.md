---
number: 67
rank: 8
title: Object Storage size alert at 15 GB (restic and versioning vs the 20 GB free limit)
stage: Operations (backups, cost guard) · RUN
size: S
status: 'built 2026-10-18 (check object_storage, probe goldbot/ops/storage_size.py, RUNBOOK 0.6); open: reviews, one recorded run on #34 · accountable: sre · due 2026-10-23'
---
- **67. Object Storage size alert at 15 GB.** Decided 2026-10-16 by program director + product owner from
  `docs/finance/budget-2026-10.md` (Backups row and the follow-ups table). Value (RUN, cost and safety): restic to
  Oracle Object Storage is free up to 20 GB, and bucket versioning keeps previous object versions, so `restic prune`
  does not shrink usage; the 30-day previous-version expiry is already set (ADR 0002). Without an alert, usage can
  pass 20 GB unseen and either start a bill or fail the backup. It finds no edge; it keeps the backups free and
  working. TRACEABILITY: P8 (backups). Depends on: 1 (the bucket and the restic job running).
  Acceptance:
  - A health check reads the bucket's size, previous versions included, at least daily; it warns at 15 GB and fails
    at 19 GB; the warning reaches Telegram through the existing ops alerts (item 7); thresholds live in settings.
  - Tests: 14.9, 15.0 and 19.0 GB give ok, warn and fail; an unreadable size is reported as unknown (warn), never
    as ok.
  - On the demo box, one recorded run reports the current size on #34 (no bucket name, namespace or tenancy in the
    repo or the comment).
  - The finance-auditor's monthly Object Storage line (budget-2026-10.md, "first Sunday") cites this check.
  - Reviews: code-reviewer APPROVE and security-reviewer PUSH-OK (credentials stay in the keyring), recorded at the
    merge.
