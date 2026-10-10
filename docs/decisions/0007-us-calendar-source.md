# 0007: Point-in-time US calendar (tiers 1 and 2) from FRED release dates and the Fed's FOMC calendars

Status: accepted (data engineer, 2026-10-10). The tier-2 blackout policy that would use it (BACKLOG 18, playbook G-3)
is an owner decision and is not part of this record.

## Context
Tier-2 US releases (PPI, retail sales, ISM, GDP, JOLTS, jobless claims, FOMC minutes, Fed speakers) move gold, and
nothing in the repo records their schedule (G-3). The tier-1 events have no history either. The live Forex Factory
archive (`goldbot/data/econ_calendar.py`) only records from deployment onwards, and research loads no events (finding
E-1). H-16, H-17 and H-18 need a point-in-time schedule from 2010 to 2025. Research needs DST-correct UTC times and a
stamp for when each schedule entry was known. Claude sandboxes cannot reach the data hosts, so the pull runs on
GitHub's runners. Checked from the Mac sandbox on 2026-10-10: bls.gov answered "Access Denied"; the FRED web calendar
timed out; api.stlouisfed.org answered (HTTP 400 without a key); federalreserve.gov served its FOMC calendar pages.

Options:
1. Agency calendars (BLS, Census, BEA HTML pages). Public domain, but there are four or more formats to scrape, BLS
   blocks scripts, and past years are spread over many pages.
2. FRED API `fred/release/dates`. ALFRED keeps past release dates, and `include_release_dates_with_no_data=true` lists
   scheduled future dates. It is one JSON format for every agency, goes back before 2010, and needs a free key.
3. Commercial calendars (Forex Factory, Investing, Trading Economics). They have consensus values, but their licences
   do not allow storing or republishing history.

## Decision
- **Agency releases come from option 2.** FRED release ids: 10 (CPI), 50 (Employment Situation = NFP), 54 (Personal
  Income and Outlays = PCE), 46 (PPI), 9 (advance retail sales), 53 (GDP), 192 (JOLTS) and 180 (weekly claims). Each
  id is checked against the release name FRED returns, and a mismatch fails that type. The key is the repo secret
  `FRED_API_KEY`. It is never printed, and errors omit the URL.
- **FOMC comes from the Federal Reserve Board's meeting calendars**: `fomccalendars.htm` for the recent years and the
  year ahead, and `fomchistorical<year>.htm` before that. Only scheduled meetings count. Unscheduled meetings,
  conference calls, notation votes and cancelled meetings were not known in advance, so they are left out.
  - A press conference is listed when the page links one, and at every meeting from 2019 onwards.
  - Minutes take the page's "Released" date, or the statement date + 21 days when the page has none yet.
  - Times by era: the statement was at 14:15 until April 2011. In 2011-2012 it was at 12:30 on press-conference days
    and 14:15 otherwise, and it has been at 14:00 since 2013. The press conference was at 14:15 in 2011-2012 and has
    been at 14:30 since 2013.
- **Tiers keep the labels in code** (`econ_calendar.TIER1_PATTERNS`, settings `risk.blackout.events`):
  - Tier 1 is CPI, NFP, PCE, the FOMC statement and the FOMC press conference.
  - GDP, including the advance estimate, stays tier 2, because it is not on the tier-1 list in code. Promoting it is
    a one-line change, and an owner or quant decision.
- **ISM is rule-derived** (source `rule`), because ISM is not on FRED and its content is licensed. Manufacturing is the
  first US business day of the month and services the third, both at 10:00 ET.
- **Fed speakers are not covered**, because no free, structured history of their schedule exists.
- **Licence.**
  - The release dates and FOMC calendars are facts published by US federal agencies (BLS, Census, BEA, DOL, Federal
    Reserve Board), which are US government works in the public domain.
  - FRED's API terms require the notice "This product uses the FRED(R) API but is not endorsed or certified by the
    Federal Reserve Bank of St. Louis" and no implied endorsement. The notice is on the `calendar-v1` release and in
    the module docstring.
  - No free source we may republish has consensus values, so the file has no actual or consensus columns.
- **Availability:**
  - A schedule entry is available from `event - 7 days`.
  - For agency events on or after the start of a shutdown, that stamp is raised to a floor some weeks after
    reopening: 2013-10-31, 2019-02-15 and 2025-12-05. The Fed is self-funded, so its events are exempt.
  - The stamp is never later than the event.
  - The opt-in features cap their horizon at the same 7 days, so history and live data look the same.
  - In later runs, a new event whose rule stamp has already passed is stamped at the download that first saw it. A
    future event that disappears gets a `cancelled` row, and one that comes back gets a `scheduled` row.
  - Published rows are never rewritten (`check_keeps_history`, the positioning approach).

## Consequences
- The owner must add the free `FRED_API_KEY` secret before the first run. Without it the workflow fails with a clear
  message and publishes nothing. Cost: about 1-2 runner-minutes a week, and an asset well under 1 MB.
- Research loads the calendar opt-in with `research_pass.py --us-calendar DIR`, which adds the features `tier1_events`
  and `tier2_events`. The default feature version does not change.
- Known approximations, documented and not corrected:
  - FRED gives dates only, so a release moved within its day is not seen.
  - ISM's real dates sometimes depart from the rule.
  - The minutes date for a meeting whose minutes are not out yet is a rule, and a different actual date leaves the
    rule date in history.
  - The shutdown floors are conservative but not checked to the day against agency notices.
  - Past release dates are the actual dates, so a reschedule before the first run, outside the shutdown windows, is
    stamped as if it had been known 7 days ahead.
- What would change this:
  - A licensed consensus feed (D-4), which would add actual and surprise columns.
  - FRED changing its terms or release ids. The name check would catch a changed id.
  - The Fed redesigning its calendar pages. The parser would then fail the FOMC source rather than return too few
    meetings.
