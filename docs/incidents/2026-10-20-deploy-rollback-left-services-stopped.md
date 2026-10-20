# 2026-10-20: a failed deploy's rollback left api, scheduler, telegram, news and tv-pull stopped

Status: open (closed when the fixed goldbot-deploy is installed on the brain and its next deploy is recorded)
Severity: S1 alerts lost (the Telegram service was down; exits were not affected)
Author: sre. Reviewed by: pending (code-reviewer, security-reviewer: root-run script).

## Summary
`sudo goldbot-deploy latest` of main aee7823d on the brain failed its post-deploy check because the newly enabled
`goldbot-engine@icm-probe` could not start (its MT5 bridge was not logged in yet). The automatic rollback then also
stopped at that engine and left goldbot-api, goldbot-scheduler, goldbot-telegram, goldbot-news and goldbot-tv-pull
stopped until the operator started them by hand. Only goldbot-supervisor and goldbot-engine@icm-demo kept running.

## Impact
- From the rollback (shortly after 11:48 UTC) until the manual restart: no Telegram (owner alerts and entry approvals),
  no dashboard/API, no scheduled jobs, no news or TradingView input. The exact restart time is not in deploys.jsonl
  (a manual `systemctl start` is not recorded there); the journal on the brain has it.
- Trading: engine@icm-demo (demo) ran throughout; exits and broker-side stops were unaffected. icm-probe was not yet
  trading. No money at risk (demo accounts only).
- The outside dead man's switch did not fire within the gap as far as is known (to confirm from its log).

## Timeline (UTC)
| Time | Event |
| --- | --- |
| 11:48 | `sudo goldbot-deploy latest` (cur -> aee7823d). The start loop restarts supervisor, engine@icm-demo, then engine@icm-probe, which fails to start (bridge not logged in); `switch_to` returns at once, so api, scheduler, telegram, news and tv-pull are never started |
| ~11:48 | rollback to the previous commit: the same loop stops at engine@icm-probe again; record `failed`: "rollback did not recover every service" |
| later | operator sees the record and starts the five services by hand |
| later | icm-probe's bridge logged in; second deploy of 4b90445f succeeds: "deployed 4b90445f; degraded at deploy time (bridge_compat:icm-probe), now ok" |

## Root cause
`switch_to` in goldbot/ops/linux/goldbot-deploy.sh started units with `systemctl restart "$s" || return 1` in three
loops (supervisor, engines, the rest). The first unit that failed to start ended the function, so every unit after it
in the order stayed stopped, and the rollback ran the same code. Engines sort before the inputs, so one engine that
cannot start takes the dashboard, Telegram and the scheduler down with it, on the deploy and again on the rollback.
Two design gaps let it through: (1) the deploy had no notion of "what ran before", so a rollback tried to start every
enabled unit, including one that had never run; (2) one engine's failure was a whole-deploy failure even when the
cause was outside the brain (its MT5 box not logged in). No test covered a unit failing to start: the fake systemctl
could only make every unit inactive at once (`fail_at`).

## Detection
The deploy record said `failed` with "check journalctl", which a person read. Nothing said which services were down,
and no check alerts on goldbot-telegram being stopped through Telegram itself (by construction). The outside alert
(dead man's switch) is the backstop for a silent brain.

## Recovery
docs/RUNBOOK.md 0.5, "What ran before runs after": a `failed` record now ends `not running: <units>`; for each,
`systemctl status <unit>`, `journalctl -u <unit> -n 100`, fix, `sudo systemctl start <unit>`, then `goldbot run health`.

## Prevention
| Item | Type | Where | Status |
| --- | --- | --- | --- |
| Units active before the deploy are recorded; a failed start never stops the next one; a rollback starts only those units and afterwards starts each one that is not active, and records `running: / not running:` | check | goldbot/ops/linux/goldbot-deploy.sh (`record_pre_active`, `switch_to`, `ensure_started`) | shipped (branch fix/deploy-rollback-restarts) |
| A newly enabled engine is started last, without waiting, and judged on its own: blocked by its bridge = `deployed_degraded`, bridge ok = rollback; an engine that ran before and fails still rolls back | check | goldbot-deploy.sh (`judge_new_engines`) | shipped (same branch) |
| Shell tests: the exact incident; a rollback after a real failure restarts api/scheduler/telegram/news/tv-pull; a previously healthy engine still rolls back; the watcher's rollback | test | tests/test_goldbot_deploy_sh.py | shipped (same branch) |
| Install the fixed goldbot-deploy on the brain (hash-checked, RUNBOOK 0.5) | runbook | brain | open (sre/Claude, next release) |
| Enable a new engine only after its MT5 box passes `terminal:<account>` ok (RUNBOOK 0.3b order) | runbook | docs/RUNBOOK.md 0.3b | open (sre, with the next account box) |

## Lessons
- Went well: the record was written and the second deploy's degraded note named the real cause (bridge_compat).
- Did not: a fail-fast `|| return 1` is right for a build step and wrong for a start loop; recovery code must try every
  step and report, not stop at the first error.
- Lucky: only demo accounts, and the one engine that mattered started before the failing one in alphabetical order.
