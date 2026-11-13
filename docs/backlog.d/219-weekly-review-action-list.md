---
number: 219
title: Weekly review job and its ranked action list
stage: 'research ops (review loop, architect plan 2026-11-13 item 2)'
size: M
status: 'implemented (2026-11-13, branch feat/weekly-review; R141) · reviews: quant-reviewer, trading-safety-reviewer'
---
- **219. Weekly review job and its ranked action list.** Value: nothing read the week's evidence as a whole; each report stopped at its own screen. Scheduler job `weekly_review` (Saturday 13:00 UTC, after `research_director`) reads per family the net result against its matched control (the strategy ranking's figures, handed in by the job; a missing control reads "unpaired", never an edge), loss attribution (state/attribution.json, cells negative after the row bar), cost drift against config/cost_of_record.yaml, reads due at their registered size, MFE/MAE exit diagnostics (exploration_trades `mfe_r`/`mae_r`, rows without them counted, never guessed) and writes a ranked list to aux.db `review_actions` (migration 30), one set a week (a restart keeps the first). Acceptance: tests/test_weekly_review.py (`test_missing_control_reads_unpaired_not_edge`, `test_restart_mid_run_keeps_one_action_set`, cost, loss, exit, read-due, ranking tests).
