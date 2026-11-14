---
number: 261
rank: 84
title: 'calendar-v1: the 26-27 Oct 2027 FOMC meeting has no policy decision'
stage: Research data · catalogue context
size: S
status: 'proposed · accountable: data-engineer · reviews: quant-reviewer, trading-safety-reviewer'
---
- **261. calendar-v1: the 26-27 Oct 2027 FOMC meeting has no policy decision.** Value: the Fed's revised 2027 schedule (press release 2026-11-12) says the 26-27 October 2027 meeting has no policy decision, but calendar-v1 (2026-11-14) still lists fomc_statement:2027-10-27 as scheduled (the meeting page lists it); the live blackout and research would treat it as a statement day. Acceptance: a hand-checked rule drops the statement and press conference of a meeting the Fed marks as without a decision, stamped at the notice, without a later download reviving it; tested; confirmed on the next release.
