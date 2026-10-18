# data-v1 coverage after the 2026-10-17/18 refetch (data-engineer, 2026-10-18)

Gate used: G1 = 0 unexpected weekday gaps (weekday under 700 1m bars, not a listed closure) in 2010-01..2025-09.
G2 = holdout 2026 repaired (no gap weekdays, no bid-imputed months). Source: coverage issues #24 #25 #27 #29 #30 #2 #32.

## Runs
| run | scope | status |
|---|---|---|
| 39035057414 | 10 months 2020-2025 | completed success (2026-10-17 19:35Z) |
| 39035068394 | 2026-02..05 | completed success (2026-10-18 00:44Z); 2026 file published |
| 39097230486 | 2026-06..09 | in progress at 04:29Z (2026 build step still downloading) |

## G1: NOT closed
Month bar counts all clear 15,000, but the day check still lists the same 21 empty weekdays as before the refetch
(0 bars each time, so the refetch recovered nothing; counts unchanged e.g. 2020-05 24,535).
Months short (gap days): 2020-05 (26-28), 2021-11 (05), 2022-01 (31), 2023-11 (08), 2024-01 (10-12), 2024-06 (07),
2024-07 (01, 16), 2025-02 (26-28), 2025-03 (26-28), 2025-05 (12-14). Every other month 2020-01..2025-09 has 0 gap days.
Pre-2020 unchanged (closures and Sunday-reopen artefact only, per the 10-16 triage).

## G2: NOT closed
Gap weekdays per month in 2026 (file after run 39035068394): 04: 10, 06: 7, 07: 11, 08: 6, 09: 8, 10: 9 (51 total).
01, 02, 03, 05: 0 gap days, but 02, 03, 05 are bid-imputed on every bar. Runs for 06-09 not finished; the 51 count
may drop after 39097230486 publishes (06-09 were "kept" in the earlier run, i.e. not re-downloaded). 2026-04 was
re-downloaded and is still 10 empty days.

## Re-run
Identical zero days after a real re-download point at throttling or no data at source, not a missed month. Wait for
39097230486, then a single probe of the hardest case before spending ~1 h of minutes:
`gh workflow run data-dukascopy.yml --ref main -f months=2020-05`
If that again returns 0 for 05-26..28, the days are unavailable from Dukascopy and G1 needs a different source or an
accepted-gap ADR (decision for owner/research director). If it fills, run the full list:
`gh workflow run data-dukascopy.yml --ref main -f months=2021-11,2022-01,2023-11,2024-01,2024-06,2024-07,2025-02,2025-03,2025-05,2026-04`
(Confirm the input name/format in data-dukascopy.yml before dispatch.)

## Update 2026-10-18 (later)
Run 39097230486 is still in_progress (1h44m at check time), so the 2026 gap count stays 51 (04: 10, 06: 7, 07: 11,
08: 6, 09: 8, 10: 9) and no new coverage issue has opened since #32. The probe was NOT dispatched: one concurrency
group, and a second dispatch would cancel a pending run. Input confirmed: `months` (comma-separated YYYY-MM, max 24).
Next step once the run completes: `gh workflow run data-dukascopy.yml --ref main -f months=2020-05`; re-check
05-26..28 (and the 2026 count) after it publishes.

### Decision options if Dukascopy cannot serve the gap days (owner / research director)
Scale: 21 empty weekdays in 2020-2025 (10 months), 51 in 2026 pending run 39097230486.
| option | what | cost / risk |
|---|---|---|
| A. Broker M1 backfill | IC Markets demo MT5 `copy_rates_range` for the gap days, stored as its own source with `source` and `available_utc` set | Free, VPS-only (MT5 is Windows). History depth for XAUUSD M1 is limited; broker quotes and spread differ from Dukascopy, so basis shift at the seams; must be flagged per bar |
| B. TradingView OANDA / IC history (TV pull) | Pull the gap days only through the TV reader | A second feed, never primary. Unofficial cookie pull (ToS risk already accepted), rate limits, bar-count caps on M1, different volume and price basis. Use as a gap patch, flagged `source=tv`, never blended silently |
| C. Accepted-gap ADR | Leave gaps; document them | Simplest and cleanest provenance. Walk-forward rule: drop gap days from features, labels and fills, with no forward-fill across them; labels whose horizon touches a gap day are dropped; folds report the excluded-day count; holdout 2026 must have none (G2 stays a hard gate) |
Recommendation: C for 2020-2025 (21 days of about 1,500 is 1.4%, history only), with the exclusion rule enforced in the
loader and tested. For the 2026 holdout use A (broker M1 same-feed as live execution) if run 39097230486 leaves gaps;
B only as a cross-check. Any patched day carries `source` and a dq_events row. Owner decides; G1 wording would change
to "0 unexplained gaps".

## Update: pre-2010 extension (data-engineer, 2026-10-18)
Run 39097230486 finished success (5h21m). Dispatched one run for the earlier years:
`gh workflow run data-dukascopy.yml --ref main -f from_year=2003 -f to_year=2009` -> run 39117170589 (2003 and 2004
first; matrix max-parallel 2, up to 340 min per year, so expect roughly 20 h end to end). Input is from_year/to_year,
no month list needed. Dukascopy's earliest XAUUSD date could not be probed from here (market-data hosts blocked); the
2003 job reports it (a year with no bars publishes nothing and opens a failure issue). Per-year bar counts, gap days
and bid-imputation status: PENDING, fill from coverage issues "data coverage 2003..2009" once published, then run
`python scripts/check_coverage.py xauusd_1m_dukascopy_<year>.parquet`.

### What hardcodes 2010 (change before research can start earlier)
| where | what | action |
|---|---|---|
| `.github/workflows/research.yml` L13 | from_year default 2010 | dispatch with explicit from_year; change default only after the earliest good year is known |
| `scripts/research_pass.py` L599, `scripts/count_owed_events.py` L594, `goldbot/research/cells.py` L110, `goldbot/research/generator.py` L90 `FROM_YEAR` | default 2010 | same; generator FROM_YEAR also feeds cell metadata/pre-registration, so change deliberately |
| `scripts/research_pass.py` L221 `DAY_TYPE_MAX_FROM_YEAR = 2010` | day_type/h16 need from-year "2010 or earlier" | already allows earlier; H-16 "OOS from ~2012-04" shifts earlier with more warm-up data |
| `scripts/dukascopy_months.py` `FIRST_YEAR = 2010` + workflow error text | `months=` refetch rejects pre-2010 months (test in tests/test_dukascopy_year.py) | raise to the earliest published year, update test, so pre-2010 gap repair works |
| `goldbot/data/us_calendar.py` `COVERAGE_START = 2010-01-01` | US calendar has no pre-2010 events | must be extended (and FRED release dates checked) or pre-2010 event features are silently absent |
| macro (`goldbot/data/macro.py`) | FRED fetch starts 2003-01-01; GVZ starts 2008-06 | covered from 2003; GVZ NaN before mid-2008 |
| `scripts/count_owed_events.py` L548/556 | year columns 2010-2025 | cosmetic |
| `scripts/check_coverage.py` | closure rules, no year floor | OK; first year taken from the data |
| holdout (`research.holdout_from` 2025-10-01, `director.DEFAULT_HOLDOUT_FROM`) and walk-forward (`walkforward.py`, rolling by months) | no year floor | no change; more folds appear automatically. Pre-registration `data_digests` pin the yearly files, so an old row still refuses a different --from-year (intended) |
| `smc_structure` 2010 pin (research-director ruling) | not present in this branch's tree | listed only; not changed |

### Quality caveats for pre-2010 gold (expectation, verify against the coverage reports)
Spreads were wider and stepped (several dollars ask-bid in 2003-2007 versus cents now), so cost models fitted on
2010+ understate old costs; liquidity and 1m bar density are thinner (many zero-volume or flat minutes, more empty
minutes overnight); the gold regime (2003-2008 bull, 2008 crisis, 2011 peak) differs from the 2010+ sample and
older bars are likely bid-only imputed where the ask side is missing; expect more gap days. Use per-year
flags (gap days, imputed share) to choose the earliest good year; treat pre-2010 as a separate regime in
walk-forward reporting and do not blend its cost assumptions with the live model.
