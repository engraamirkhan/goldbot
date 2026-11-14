---
number: 221
title: Monday roll-up proposals join the action list; backlog suggestions open review issues
stage: 'research ops (review loop, architect plan 2026-11-13 item 2)'
size: S
status: 'done (sweep 2026-11-14, deployed with PRs through #393): PR #388 (R141) · accountable: implementer'
---
- **221. Monday roll-up proposals join the action list; backlog suggestions open review issues.** Value: the daily review's weekly-<sunday>.json variant proposals were written and never read. Each now joins `review_actions` once (stable id over the roll-up and title, whichever weekly review reads it first). Backlog kinds (unpaired families, cost drift) go to one GitHub issue a week labelled `review` through the daily review's scrubbed issue path (outbox without the token, posted once per week). Acceptance: `test_proposals_are_consumed_once`, `test_the_review_issue_is_scrubbed_and_labelled_review`, `test_restart_mid_run_keeps_one_action_set` (posted once).
