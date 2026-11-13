---
number: 235
rank: 79
title: calendar-v1 FOMC rows carry the Fed's published schedule date
stage: 'Research data · catalogue context'
size: S
status: 'catalogue side done 2026-11-13 (branch feat/exchange-holiday-calendar); release side open · accountable: data-engineer · reviews: quant-reviewer'
---
- **235. calendar-v1 FOMC rows carry the Fed's published schedule date.** Value: calendar-v1 gives every event `available_utc = event_utc - 7 days` (SCHEDULE_LEAD), but the Fed publishes the year's FOMC meeting schedule months ahead. Since R145, S07 reads only published meetings, and the 7-day lead blinded its "skip when the next meeting is under 10 days ahead" leg (explore S07-V0 went from 625 to 692 events). Done in the catalogue (2026-11-13): `data.us_events_avail` reads scheduled FOMC statement and press-conference rows from the Fed's calendar (source `fed`) as known from 1 Dec of the prior year. This is an assumed floor, like the exchange holidays, and S07 is back at 625/604. Open: the floor is not checked against the Fed's "tentative schedule" press releases, and the published calendar-v1 rows still carry the 7-day stamp, so features and the live calendar see it. Acceptance (release side): a hand-checked table of the Fed's schedule-announcement dates per year (federalreserve.gov press releases) is added as appended rows with source `fed`, vintage kept, never overwriting the published rows. The catalogue floor is replaced by those dates, with a test that a late-published schedule is not read before it is published.
