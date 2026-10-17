---
number: 73
title: Auto-mode broker refusals reach the owner, at most once per cause per hour
stage: Execution and approvals (auto mode) · PROTECT · RUN
size: S
status: 'parked 2026-10-17 (product owner): auto mode is off (default propose) and is only offered after 100 decided proposals (A10), so no auto reject can happen yet; re-enters the table, ranked above any other notice work, when the decided-proposal count reaches 80 or before anyone switches /mode auto, whichever comes first · accountable: implementer · due: set on re-entry'
---
- **73. Auto-mode broker refusals reach the owner, at most once per cause per hour (docs/ux/order-not-placed.md open
  point 2).** Decided 2026-10-17 by program director + product owner (owner delegated): accepted, parked. Value (RUN,
  protect): in auto mode a definite reject is journalled (`auto_rejected:<retcode>`) and the owner is never told, so a
  repeating refusal (no money, invalid stops) can block every automatic entry unseen. It finds no edge. Depends on: 71
  (the wording table and notice); ambiguous auto results are 72's. Files: `goldbot/engine/runner.py` (the auto branch
  and a per-cause throttle), the 71 table. TRACEABILITY: A10 (auto mode), the A row 71 adds.
  Acceptance (`tests/test_auto_reject_notice.py`, written first):
  - `test_an_auto_definite_reject_queues_the_not_placed_notice_with_automatic_entries_carry_on`: 71's notice for the
    cause plus " Automatic entries carry on." (ui-ux-designer confirms the words).
  - `test_the_same_cause_notifies_at_most_once_an_hour` (cause = who refused + code; host clock; the second within the
    hour is journalled only); `test_two_causes_in_one_hour_each_notify_once`.
  - `test_a_ceiling_refusal_keeps_only_its_own_notice` (66's "Automatic entries pause until the next bar", never both);
    `test_an_auto_unconfirmed_result_is_left_to_72`.
  - A notice that cannot be queued is logged and never changes the decision or the auto pause (existing best-effort
    rule). Reviews: trading-safety-reviewer, code-reviewer, ui-ux-designer.
