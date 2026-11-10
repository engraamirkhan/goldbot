# 2026-11-10: the Telegram deploy check failed on every pass (git fetch under ProtectSystem=strict)

Status: open (closed when the fix is deployed on the brain and the sandboxed `deploy check` prints JSON/null with no warning)
Severity: S3 (no new version was offered on Telegram; deploys by hand worked; trading, exits and alerts unaffected)
Author: sre (with the implementer). Reviewed by: security-reviewer, sre.

## Summary
`DeployWatch.check()` in goldbot-telegram ran `git fetch origin main` every 10 minutes. The unit runs with
`ProtectSystem=strict` and `ReadWritePaths` limited to state, logs, data, models and /var/lib/goldbot, so the fetch could
not write `/opt/goldbot/.git` and exited 255 on every pass. No new version was ever offered with [Deploy] [Skip].

## Impact
- The one-click deploy offer did not work from at least 2026-11-09 20:10 UTC (40 failures in 12 h in the journal). It
  may have been broken since the sandbox was added; the journal before that window was not examined.
- Deploys themselves were unaffected: `sudo goldbot-deploy <sha>` runs as root outside the sandbox, and every deploy in
  the window ran that way.
- Trading, exits, approvals and alerts: unaffected. Each failed pass logged a full traceback (`log.exception("deploy
  pass")`), adding noise to the telegram journal.

## Timeline (UTC)
| Time | Event |
| --- | --- |
| 2026-11-09 20:10 (at the latest) | first `git fetch` exit 255 in the goldbot-telegram journal |
| 2026-11-09 20:10 to 11-10 08:10 | 40 failing passes, each a traceback; no health check, no alert |
| 2026-11-10 | found while reading the telegram journal; fix branch `fix/deploy-check-readonly` (R120) |

## Root cause
The check needed the new commit in the local object store (`fetch`, then `merge-base`, then `log`) and so had to write
`.git`, but the service's sandbox makes the whole tree read-only except the listed paths. The sandbox was right; the
check assumed write access it should never have had. The tests used a fake git that accepted any command, so nothing
showed that `fetch` writes.

## Detection
Late, and by a person reading logs. No health check covered "the deploy check has not read origin for a long time";
the failure was a traceback in the pump's catch-all, which nobody is alerted on. It should have been the health check
added now.

## Recovery
The fix makes the check read-only (`git ls-remote` for main; GitHub's compare API for the fast-forward, the subjects
and the MT5-box ancestry). Until it is deployed, deploy by hand with `sudo goldbot-deploy latest`. RUNBOOK 0.5 "No new
version offered" has the steps, including the sandboxed one-shot check.

## Prevention
| Item | Type | Where | Status |
| --- | --- | --- | --- |
| Check reads origin with `git ls-remote`, never writes `.git` | code + test | goldbot/ops/deploy.py; tests/test_deploy.py (`never_fetches`, the fake git refuses writing commands) | shipped (fix/deploy-check-readonly) |
| git bounded by a 30 s timeout, no credential prompt; GitHub answers streamed, capped at 5 MB, 30 s total | code + test | goldbot/ops/deploy.py | shipped (fix/deploy-check-readonly) |
| One concise warning per outage instead of a traceback each pass | code + test | goldbot/ops/deploy.py | shipped (fix/deploy-check-readonly) |
| Health `deploy_read` warns and tells the owner after 2 h without a successful read | check + test | goldbot/ops/checks/services.py | shipped (fix/deploy-check-readonly) |
| Runbook step "No new version offered" | runbook | docs/RUNBOOK.md 0.5 | shipped (fix/deploy-check-readonly) |
| Post-deploy host check in the service's sandbox | runbook | HANDOFF (R120) | open (first deploy of the fix) |

## Lessons
- A service sandbox needs a test of the code paths that run inside it: a fake that accepts any command hides writes.
- A periodic job that can fail silently needs a "last success" timestamp and a health check, not only a log line.
- Lucky: deploys by hand were the norm, so nothing was held back.
