---
number: 56
title: Digest Yesterday line on the opportunity day
stage: Performance review · RUN
size: S
status: 'done 2026-10-16 (147c68c + review fixes cc61ed1, merged at ba1c520; code-reviewer APPROVED, ui-ux wording applied; TRACEABILITY V10; ships to main in PR #79) · accountable: implementer'
---
- **56. Digest "Yesterday" line on the opportunity day.** Value: the digest's "Yesterday" proposals and trades
  lines still run 00:00-24:00 UTC (`goldbot/telegram/digest.py` `build_digest`). Its month line and the monthly and
  performance reports use the opportunity day (`goldbot/ops/report_days.py`). So a close at 23:00 UTC is in
  yesterday's trades line and in today's month line. Acceptance:
  - for a digest built on day D, "Yesterday" covers [D-2 22:00, D-1 22:00) UTC, the opportunity day D-1, through
    `report_days` (no new day arithmetic in the digest);
  - the header reads "Yesterday (to 22:00 UTC)" with the weekday and date of D-1;
  - tests at 21:59 and 22:00 UTC, on a Monday digest (the Sunday reopen counts on Monday), and in a US DST week;
  - the risk lines (loss-cap use) stay on the risk day, 00:00 UTC; no limit changes;
  - reviews: code, ui-ux (Telegram wording).
  Sweep 2026-10-16 (product owner): accountable `implementer`, due 2026-10-19. Acceptance as above.
  **Acceptance scorecard 2026-10-16 (product owner, AGENT_STANDARDS 7 checks):** (1) criteria: yes, with one approved wording change: the header reads "<day> trading day
  (ended 22:00 UTC, 23:00 UK)" instead of "Yesterday (to 22:00 UTC)" (ui-ux-designer's wording, cc61ed1). The
  21:59/22:00, Monday and US DST-week tests are in `tests/test_digest.py` and pass (re-run 2026-10-16); the risk lines
  stay on the 00:00 UTC risk day. (2) DoD: yes. (3) S1-S12: yes. (4) code APPROVED, ui-ux applied. (5) CI/merged:
  **no, in PR #79 (open)**. (6) PR text: yes (#79 Digest). (7) role and date: yes (due 2026-10-19, met). Accepted,
  conditional on #79 merging.
