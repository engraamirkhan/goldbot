# 0044: Keyless FRED/ALFRED release dates for calendar-v1

Status: proposed (data engineer, 2026-10-25). Amends ADR 0008 (source of the agency release dates; hand-checked
revision-only dates). Implements the program decision of 2026-10-20 (calendar-v1 from ALFRED, no key).

## Context
- calendar-v1 has never been published. ADR 0008 takes the agency release dates from the FRED API, which needs the
  `FRED_API_KEY` repo secret. Nobody can obtain that key: the sign-up needs a browser and an email inbox.
- The historical arms matrix (`research-arms-matrix.yml`), H-16/H-17/H-18 and the tier-1 blackout in research all
  need calendar-v1.
- Checked from the Mac sandbox on 2026-10-25 (GitHub's runners can also reach both St. Louis Fed hosts). The St. Louis
  Fed serves the same ALFRED release dates without a key:
  - `alfred.stlouisfed.org/release/downloaddates?rid=<id>&ff=txt` has every past date of a release.
  - `fred.stlouisfed.org/releases/calendar?rid=<id>&y=<year>` has past and scheduled dates, with a US Central time.
    It offers last year, this year and next year only; any other year falls back to last year.
  - Over 2025-2026 the two pages agree on every past date for all eight releases.
  - robots.txt allows both paths and asks for a crawl delay of 1 s on fred and 2 s on alfred.
- The first full build showed a fault that applies to the API path as well. ALFRED lists "the dates when any series
  from this release was revised", so seasonal-factor, benchmark and annual-revision days appear as releases.
  - The 20-day spacing quarantine (ADR 0008) then dropped both rows of each pair, the real release included.
  - Examples: every January CPI and PPI from 2010 to 2024; the August and September 2024 NFPs (around the 08-21
    benchmark preview); the 2024 January NFP.
  - The count check did not catch this, because the 10-13 range accepts 11.
  - GDP phantoms early in a quarter were labelled the quarter's advance: 2014-01-10, 2013-11-01, 2024-10-02, and
    2026-04-09, which was really the Q4 third estimate.

## Decision
- **Release dates are keyless when the key is absent.** `scripts/us_calendar_data.py --source auto` (the workflow
  default) uses the FRED API when `FRED_API_KEY` is set. Otherwise it uses the ALFRED dates combined with the FRED
  calendar for this year and next (`goldbot/data/us_calendar_keyless.py`).
  - Rows keep the source `fred`, because the data and its lineage are the same; only the transport differs. A later
    API run therefore merges with no spurious events or cancellations.
  - The workflow no longer skips without the secret. It needs no secret.
- **Guards, which fail only the release concerned:**
  - the release name must match on both pages;
  - the ALFRED file must have its dates block and a date within 120 days of the run;
  - the calendar page must be for the year requested and must list this year's dates.
- **Reported, not failed:**
  - past dates of this year on which the two pages disagree (the union is used);
  - days whose listed time is not the type's fixed time. Event times stay fixed per type (ADR 0008), so these days
    are for review. Flags today: PCE 2026-01-22 at 10:00 and 2026-03-13 at 11:00, GDP 2026-03-13 at 09:30, and retail
    sales 2026-03-06 at 07:30 and 2026-09-28 at 10:00.
- **`REVISION_ONLY`** (`goldbot/data/us_calendar.py`) lists ALFRED dates that are not a scheduled release of the
  type. They are dropped before the quarantine and before the GDP advance split, on both paths. Each was hand-checked
  on 2026-10-25:
  - BLS types: every ALFRED date from 2010 to 2026 was compared with the BLS yearly schedules (bls.gov/schedule/<year>);
  - PCE and GDP: compared with the BEA news-release archive;
  - retail sales: the Census annual revisions;
  - claims: second dates 1-5 days after a weekly report.
- **Hand-checked additions** to `SPACING_EXEMPT` (real close pairs), and the `GDP_ADVANCE_OVERRIDES` entry 2026-03-31
  -> 2026-04-30.
- **A monthly-gap report** lists gaps of more than 45 days between monthly releases, which catches a lost release.
- **Exact counts** (quant review, 2026-10-25):
  - monthly types: exactly 12 a year, apart from the listed `MONTHLY_COUNT_EXCEPTIONS`. These are JOLTS 2010 (5),
    PCE 2019 (11), the 2025 lapse year, and the 2026 catch-up years (13);
  - GDP advance: 4;
  - FOMC statements: 8 (7 in 2020).
- **The evidence behind `REVISION_ONLY`** is committed as `tests/fixtures/us_calendar/revision_only_evidence.csv`
  (one row per date, with its agency check). A test keeps the set and the file equal.
- **Response size:** responses are streamed and refused above 2 MB (security review).

## Consequences
- calendar-v1 can be published without an owner step. The owner's `FRED_API_KEY` action (OD-5) is no longer needed.
  The API stays optional.
- Local build on 2026-10-25, 2010-01 to 2027-12:
  - 3,159 rows, 0 quarantined;
  - 2015-2024: 12 a year each for CPI, NFP, PCE, PPI, retail sales and JOLTS; 4 GDP advances; 7-8 FOMC statements;
  - every gap is a shutdown gap (2013, 2018-19, 2025);
  - with the exact counts, the only count outside the expected range is jobless claims 2025 (46, the lapse);
    JOLTS 2010 is a listed exception, because ALFRED starts in August 2010;
  - every GDP advance from 2010 matches BEA's "advance/initial" titles. The exception is 2018-10-26, where BEA's
    archive page says 10-25.
- Cost: about 2-3 runner-minutes a week. That is 24 requests spaced 2 s apart plus 13 Fed pages, and the asset is
  under 1 MB.
- Risks:
  - a St. Louis Fed page redesign, which the name, year and dates guards would catch, leaving the published rows
    as they are;
  - a new revision-only date, which the quarantine and the gap report surface;
  - the ALFRED dates are vintage dates. A release moved within its day is not seen unless FRED's calendar lists
    its time and the day is in `TIME_OVERRIDES` (2026: PCE 01-22 10:00 and 03-13 11:00, GDP 03-13 09:30, retail
    sales 03-06 07:30). These are applied to `event_utc`, so the blackout window sits around the real release. Odd
    times before 2025 are not visible, because the calendar covers only last year onwards;
  - residual: the originally scheduled dates of PCE, GDP and retail sales in the 2013 and 2018-19 shutdowns are not
    listed as cancelled rows (ADR 0008 lists only the BLS ones and the 2019 GDP advance). Research therefore sees no
    "scheduled, then cancelled" event on those days. Only the delayed real release appears.
- The gate in ADR 0008 is unchanged: a quant review of the first published count table comes before any trial cites
  `tier1_events`/`tier2_events`.
- What would change this: St. Louis Fed restricting these pages; a licensed calendar; or a key becoming available.
  With a key, `--source auto` switches to the API with no other change.
