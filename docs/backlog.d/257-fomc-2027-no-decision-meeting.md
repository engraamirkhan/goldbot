---
number: 257
rank: 84
title: 'calendar-v1: the 26-27 Oct 2027 FOMC meeting has no policy decision'
stage: 'Research data · catalogue context'
size: S
status: 'done 2026-11-14 (branch fix/data-followups-1114); confirm on the next calendar release · accountable: data-engineer · reviews: quant-reviewer, trading-safety-reviewer'
---
- **257. calendar-v1: the 26-27 Oct 2027 FOMC meeting has no policy decision.** Value: the Fed's revised 2027 schedule (press release 2026-11-12) says the 26-27 October 2027 meeting has no policy decision, but calendar-v1 (2026-11-14) still lists fomc_statement:2027-10-27 as scheduled (the meeting page lists it); the live blackout and research would treat it as a statement day. Acceptance: a hand-checked rule drops the statement and press conference of a meeting the Fed marks as without a decision, stamped at the notice, without a later download reviving it; tested; confirmed on the next release.
  **Done 2026-11-14 (code).** Two hand-checked MANUAL_EVENTS (fomc_statement and fomc_press_conference 2027-10-27,
  cancelled at the Fed's notice 2026-11-12 12:45 ET, source manual). The hand-check wins: `calendar_frame` drops a
  downloaded row for any MANUAL_EVENTS id, and `merge_calendar` never revives one, so the meeting page that still lists
  the meeting cannot bring it back. On the published calendar-v1 the next merge appends the cancelled rows stamped at
  the notice; every published row is kept (check_keeps_history). The 7-8 Dec 2027 meeting that moved to 16-17 Nov is
  handled by the download itself (the old day is cancelled at retrieval, the new one known from the 2026-11-12 notice,
  BACKLOG 235). FOMC minutes for 2027-10-27 stay as downloaded (tier 2; the Fed publishes minutes of every meeting).
  Test: tests/test_us_calendar.py::test_a_meeting_without_a_policy_decision_is_cancelled_at_the_notice_and_never_revived.
  Confirmation: the next `data-us-calendar` run shows both events' latest status as cancelled.
