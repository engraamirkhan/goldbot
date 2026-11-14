---
number: 226
title: Monthly Telegram message, review-monthly issue and the review cadence
stage: 'process / review loop (R142)'
size: S
status: 'done (sweep 2026-11-14, deployed with PRs through #393): PR #388 (branch feat/monthly-review) · accountable: program-director'
---
- **226. Monthly Telegram message, review-monthly issue and the review cadence.** Value: a review nobody reads changes nothing; the loop needs a place the program-director clears every session, with no Claude cron and nothing pending on the owner. Acceptance, tests first: one Telegram message a month to the owner (a notice row relayed by the outbox; amounts in the account currency) and one GitHub issue labelled `review-monthly` (outbox file without a token, posted by the next run), each sent once a month; no MT5 login, host or balance in the public issue, accounts named as `account_name` (tests/test_monthly_review.py::test_message_has_no_account_identifiers, ::test_without_a_token_the_issue_text_waits_in_the_outbox). docs/process/review-cadence.md: the program-director's first step each session clears open `review` (performance-analyst + research-director, weekly) and `review-monthly` (finance-auditor, CFO, CTO) issues.
