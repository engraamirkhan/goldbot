# 0008: Point-in-time US calendar (tiers 1 and 2) from FRED release dates and the Fed's FOMC calendars

Status: accepted (data engineer, 2026-10-10; revised 2026-10-11 after the quant, data and security review of 514d338).
Amended by ADR 0044 (2026-10-25): without `FRED_API_KEY` the same release dates come keyless from ALFRED and the FRED
release calendar, and hand-checked revision-only ALFRED dates (`REVISION_ONLY`) are dropped before the quarantine.
The tier-2 blackout policy that would use it (BACKLOG 18, playbook G-3) is an owner decision and is not part of this
record.

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
3. Commercial calendars. They have consensus values, but their licences do not allow storing or republishing history.

## Decision
- **Agency releases come from option 2.** FRED release ids: 10 (CPI), 50 (Employment Situation = NFP), 54 (Personal
  Income and Outlays = PCE), 46 (PPI), 9 (Advance Monthly Sales for Retail), 53 (GDP), 192 (JOLTS) and 180 (weekly
  claims).
  - Each id is checked against the release name FRED returns, and a mismatch fails that type.
  - The key is the repo secret `FRED_API_KEY`. It is never printed, and errors omit the URL.
  - GDP is split into two types. `gdp_advance` is the first GDP release after each quarter end. `gdp` holds the later
    estimates.
- **FOMC comes from the Federal Reserve Board's meeting calendars**: `fomccalendars.htm` for the recent years and the
  year ahead, and `fomchistorical<year>.htm` before that. Only scheduled meetings count.
  - A press conference is listed when the page links one, and at every meeting from 2019 onwards. The link check
    ignores case and the spelling `fomcpressconf` (January 2026).
  - Minutes use the "Released" date inside the minutes block when it falls 14-28 days after the statement.
    Otherwise they use the statement date + 21 days.
  - Times by era: the statement was at 14:15 until April 2011. In 2011-2012 it was at 12:30 on press-conference days
    and 14:15 otherwise, and it has been at 14:00 since 2013. The press conference was at 14:15 in 2011-2012 and has
    been at 14:30 since 2013.
- **Tiers keep the labels in code.**
  - Tier 1 is CPI, NFP, PCE, the FOMC statement and the FOMC press conference.
  - Everything else is tier 2. That includes `gdp_advance`, whose promotion to tier 1 is an owner or trading-safety
    decision.
- **ISM is rule-derived** (source `rule`, first and third US business day, 10:00 ET).
  - `RULE_OVERRIDES` moves or drops a hand-checked date.
  - Research reports how many rows are rule-derived, and can leave them out with `--us-calendar-exclude-rule`.
- **Fed speakers are not covered**, because no free, structured history of their schedule exists.
- **Licence.**
  - The dates and FOMC calendars are facts published by US federal agencies, which are US government works in the
    public domain.
  - FRED's API terms require the notice "This product uses the FRED(R) API but is not endorsed or certified by the
    Federal Reserve Bank of St. Louis". It is on the release and in the module docstring.
  - The file has no actual or consensus columns, because no free source we may republish has them.
- **Availability.**
  - A schedule entry is available from `event - 7 days`.
  - When a shutdown closed an event's agency, events on or after the shutdown's start are raised to a floor:
    - 2013 and 2025: BLS, DOL, Census and BEA;
    - 2018-19: Census and BEA only;
    - January 2026: BLS only, until other agencies are verified.
  - The Fed and ISM are never closed, so they get no floor.
  - The stamp is never later than the event.
  - The features cap their horizon at the same 7 days.
- **History.**
  - `last_ok` is kept per event type. A type never downloaded before is a backfill and keeps its rule stamps (review
    finding H1). For a type downloaded before, a new event whose rule stamp has passed is stamped at the download that
    first saw it.
  - Future events that disappear get `cancelled` rows.
  - A run that would cancel more than 2 future events of one type is refused for that type. After verification
    the RUNBOOK step adds the dates to `MANUAL_EVENTS`, or an operator applies them once with
    `--allow-cancellations TYPE`, which is audited in the step summary.
  - `last_ok` metadata in the old source-keyed format is refused, so it can never be misread.
  - Published rows are never rewritten (`check_keeps_history`).
- **Hand-checked cancellations** (`MANUAL_EVENTS`, source `manual`). A download only lists what happened, so events
  that were scheduled and then cancelled or moved are added by hand, each with a scheduled row (event - 7 days) and a
  cancelled row:
  - the March 17-18 2020 FOMC, cancelled at 2020-03-15 17:00 ET;
  - the original BLS dates in the 2013, 2025 and 2026 lapses (bls.gov lapse tables; the 2013 table as reproduced by
    Calculated Risk);
  - the 2019 GDP advance (New York Fed);
  - the 2025 PCE releases that were folded into later ones (BEA blog).

  Agency events up to a verified reopening (2013, 2025) are cancelled at the shutdown's start. Later ones, and
  the 2026 lapse rows (whose start date is unverified), are cancelled at the shutdown floor or one minute before the
  event, whichever is earlier, because the exact announcement times are not recorded.
- **Quality guards.**
  - FOMC: any year panel, the year ahead included, or historical page with fewer than 6 scheduled meetings fails the
    source.
  - Both rows of any pair of monthly releases less than 20 days apart are quarantined and reported, unless a row
    is listed in `SPACING_EXEMPT` (`nfp:2013-10-22`, `nfp:2013-11-08`). A phantom listed before the real release is
    therefore never kept in its place.
  - A GDP advance more than 45 days after its quarter end is flagged unless `GDP_ADVANCE_OVERRIDES` lists the
    quarter. The overrides are 2019-02-28 (NY Fed), 2025-12-23 and 2026-02-20 (BEA). For Q4 2025, 2026-01-22 was
    the Q3 update and PCE, not the Q4 advance.
  - A per-type, per-year count table against the expected counts is written to the step summary:
    - monthly releases: 10-13;
    - claims: 51-53;
    - `gdp_advance`: 4;
    - FOMC statements: 7-8;
    - FOMC minutes: 7-9;
    - press conferences: 3 in 2011, 5 in 2012 and 4 in 2013-2018, all counted on the Fed's pages, then 7-8 from 2019.

## Consequences
- The owner must add the free `FRED_API_KEY` secret before the first run. Cost: about 1-2 runner-minutes a week, and
  an asset well under 1 MB.
- **Gate:** no trial may cite `tier1_events` or `tier2_events` until a quant review of the first live run's count
  table (step summary) has signed it off (HANDOFF, TRACEABILITY D27).
- Known approximations:
  - FRED gives dates only, so a release moved within its day is not seen. For example, the 2026-01-22 PCE release
    was at 10:00 ET.
  - ISM's real dates sometimes depart from the rule.
  - The cancellation times for post-reopening events are conservative.
  - The original dates of non-BLS releases in the 2013 and 2018-19 shutdowns are not in the hand-checked table
    because they could not be verified: Census retail sales, and the 2013 BEA GDP and PCE.
  - PCE at 08:30 ET in 2010-2012 is unverified.
- Two tier-2 definitions exist (amended 2026-10-11, review L1 of BACKLOG 17/18):
  - this calendar (calendar-v1, research): tier 2 is the fixed list of event types above (`TIERS`);
  - the live Forex Factory archive (store table `calendar_events`, used by the engine's tier-2 entry window): tier 2
    is the G-3 list matched by title (`econ_calendar.TIER2_PATTERNS`, whatever the feed's impact) plus any other
    high-impact USD event. Rows archived before 2026-10-11 were tiered by impact alone (claims, rated Medium, were
    tier 3).
  - Rule: no trial or report may cite the archive's `tier` column across 2026-10-11 without re-tiering the archived
    rows with the current `tier_of` first; research uses calendar-v1's tiers.
- What would change this: a licensed consensus feed (D-4), FRED changing its terms or ids (the name check would catch
  it), or the Fed redesigning its pages (the meeting-count guard would fail the source).
