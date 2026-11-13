---
number: 222
title: Sunday digest "Changed this week" section and the tighten-only rule
stage: 'research ops / Telegram (review loop, architect plan 2026-11-13 item 2)'
size: S
status: 'implemented (2026-11-13, branch feat/weekly-review; R141) · reviews: trading-safety-reviewer'
---
- **222. Sunday digest "Changed this week" section and the tighten-only rule.** Value: the owner sees what the loop did without reading issues. The weekly digest counts the latest weekly review: arms stopped at their registered rule, actions queued for research, backlog suggestions sent (counts only, no arm id; left out when no review was recorded, the digest's rule). The only automatic action is a stop already registered in config/arm_stops.yaml that the daily review found breached and exploration_arm_status does not hold yet (`daily_review.retire`, INSERT OR IGNORE): nothing is deleted, re-opened or loosened. Acceptance: `test_actions_only_tighten`, `test_a_dry_or_unreadable_daily_record_stops_nothing`, `test_the_sunday_digest_says_what_changed_this_week`, `test_the_digest_leaves_the_section_out_when_no_review_was_recorded`.
