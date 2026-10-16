# 2026-10-16: the first restore drill on the brain fails on a restic lock the no-delete key cannot remove

Status: open. The fix (d657510) is shipped. The incident closes when the drill passes on the brain after the deploy.
Severity: S3. A backup step was blocked, nothing was trading, and the backups themselves were good.
Author: sre. Reviewed by: (code-reviewer and security-reviewer at the release review).

## Summary
On the first real backup setup on the brain, `goldbot run backup --init` and `goldbot run backup` succeeded, but
`goldbot run restore-drill` failed:
`restic check exited 1: unable to create lock in backend: repository is already locked by PID 9005 on goldbot-brain
by goldbot ... lock was created 52 s ago`.
The backups were valid. What was missing was proof that they restore, which is the drill's job. No trading was
running.

## Impact
- From the failed drill until the fix is deployed, there is no recorded restore drill on the brain. Health
  `restore_drill` stays not-OK, and the 1-hour restore target is not measured yet.
- Backups kept working, and every snapshot written is valid. No data was lost or exposed.
- No trading impact: the engine starts on Sun 18 Oct 22:02 UTC.

## Timeline (UTC, 16 Oct 2026; minutes are not in this record)
| Time | Event |
| --- | --- |
| evening | The program director creates the bucket, versioning, lifecycle rule, users, groups and policy with the OCI CLI (RUNBOOK 0.6). The first `restic init` returns 403 until the new policy has propagated. |
| evening | `goldbot run backup --init` and `goldbot run backup` succeed. |
| evening | `goldbot run restore-drill` fails on the lock (detected by the drill, the first time it ran). |
| evening | SRE lane: the cause is confirmed with real restic 0.12.1 and 0.16.4 on a no-delete repository. The fix is d657510. |
| pending | Deploy, then `goldbot run restore-drill` on the brain. Closes the incident. |

## Root cause
Every restic command that locks writes a lock file and deletes it when it ends. By design (ADR 0002, mandatory), the
brain's key has no `OBJECT_DELETE`, so none of those files is ever deleted. `backup`, `stats`, `snapshots` and the
drill's `restore` each left a shared lock. The drill's `restic check` needs an exclusive lock, which any lock
blocks, and restic does not skip stale locks when it takes a new one. So `check` could never succeed from the brain
after the first backup.

Why the process let it through:
- The fake restic in tests/test_backup.py ran every command against a repository that removes its locks. The one
  property the security review added (no delete) was never modelled in a test.
- ADR 0002 and RUNBOOK 0.6 said that failed lock removals are "logged as a warning only". That was true for
  `unlock`, but no one checked what leftover locks do to the next command that wants an exclusive lock.
- The real-restic integration test uses a local repository with full rights, so it could not show the conflict.

## Detection
The drill found it the first time it ran: in setup, before anything depended on the backups. That is what the
drill is for. Without the manual run, the Sunday 10:00 drill would have failed and health `restore_drill` would
have alerted. The cost is 3.5 days of unproven restores rather than a silent gap.

## Recovery
- Code (d657510): read-only restic commands run with `--no-lock`. The brain no longer calls `restic unlock`. A
  host-local fcntl lock (`host_lock`) keeps a backup and a drill from running at the same time.
- Box step after the deploy: `goldbot run restore-drill`, then `goldbot run health` (`restore_drill` OK). The locks
  already left in the repository need no action: `--no-lock` commands ignore them, and the Mac's monthly retention
  run clears them.

## Prevention
| Item | Type | Where | Status |
| --- | --- | --- | --- |
| Read-only restic commands run with `--no-lock`. No `unlock` on the brain. | code | goldbot/ops/backup.py `READ_ONLY_COMMANDS`, `Restic.run` | shipped (d657510) |
| A backup and a drill never overlap on the host | code + test | `host_lock`; `test_a_drill_never_overlaps_a_backup_on_the_same_host` | shipped (d657510) |
| The fake restic models the no-delete key: locks are never removed, and exclusive and shared locks conflict as in restic | test | tests/test_backup.py `FakeRestic`; `test_restore_drill_runs_after_a_backup_under_the_no_delete_key`, `test_the_brain_takes_a_lock_only_to_write_a_snapshot`, `test_daily_backups_run_on_top_of_the_locks_the_no_delete_key_leaves` | shipped (d657510) |
| Options, choice and evidence recorded | decision | ADR 0002 "Update 2026-10-16" | shipped (same commit as this postmortem) |
| RUNBOOK 0.6: what the OCI CLI set up, policy propagation, leftover locks, when not to run retention | runbook | docs/RUNBOOK.md 0.6 | shipped (same commit as this postmortem) |
| Drill passes on the brain | check | `goldbot run restore-drill` after the deploy | open (program director, at the next deploy, before Sun 18 Oct 22:02 UTC) |
| Found while fixing: restic 0.16 cancels a backup at about 22 min under the no-delete key (a refresh cannot delete the old lock). A 30-minute test backup failed with `context canceled`. | check + test | `backup_age` warns at 10 min (`BACKUP_SLOW_WARN_S`); `test_a_slow_backup_warns_before_restic_cancels_it`; ADR 0002 | shipped (7c0a3c1) |

## Lessons
- Went well: the drill was run by hand straight after the first backup, so the failure showed up during setup and
  not on a Sunday with the engine live.
- Did not go well: a security property (no delete) was added to the design without a test that exercised its
  side effects. **Rule:** when a permission is removed from a service key, the fake for that service models the
  removal, and a test runs the full job sequence (backup, then drill, then backup) against it.
- Lucky: restic's `--no-lock` covers `check` in both versions apt can install. Without it, the only fallback would
  have been the rest-server.
