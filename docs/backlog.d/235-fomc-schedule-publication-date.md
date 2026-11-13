---
number: 235
rank: 79
title: calendar-v1 FOMC rows carry the Fed's published schedule date
stage: 'Research data · catalogue context'
size: S
status: 'proposed (follow-up of R145) · accountable: data-engineer · reviews: quant-reviewer'
---
- **235. calendar-v1 FOMC rows carry the Fed's published schedule date.** Value: calendar-v1 gives every event `available_utc = event_utc - 7 days` (SCHEDULE_LEAD). The Fed publishes the year's FOMC meeting schedule many months ahead, so since R145 (S07 reads only published meetings) S07's "skip when the next meeting is under 10 days ahead" leg cannot see a meeting 8-10 days out and under-skips. Explore S07-V0 went from 625 to 692 events, partly for this reason. Acceptance: FOMC statement rows take the publication date of the Fed's schedule (source `fed`, vintage kept; revisions append, never overwrite). A test seeds a schedule published late and S07 does not skip on it before publication. Explore S07 counts before and after are reported.
