# 0105: D-1: Dukascopy gold graded under r11-v2.1, GLD-implied PM fix, served tiers and A16 manifest

Status: proposed (data-engineer, 2026-11-26); the fix-check ruling (r11-v2.2) is the quant-reviewer's

## Context
The r11 tiers graded HistData against Dukascopy, so Dukascopy XAUUSD, the gold the system trains and trades on, had no
grade of its own (sr-v3 card item D-1; ADR 0103: gold rows read untiered). Grading it needs references that are
independent of Dukascopy:
- 2009-2018: HistData XAUUSD shares 0.00-0.04 of hour-ending closes with Dukascopy (evidence_r11-v2.csv): independent.
- 2019-2025-09: HistData is the same feed (shares 0.91-0.97). r11-v2.1 then needs an independent daily fix as well
  (B2) and grades the month P without one.
- 2003-2008: no HistData gold at all.
The official LBMA Gold Price PM is not reachable free: the LBMA's JSON sits behind a bot challenge (HTTP 403; it is not
bypassed), FRED and ALFRED removed every IBA/LBMA series in January 2022, and the Bundesbank holds only the pre-1999
Frankfurt fixing. The SPDR Gold Trust's sponsor publishes its daily NAV history, and the trust values its gold at the
LBMA Gold Price PM, so NAV per share / ounces per share is the PM price less accrued trust liabilities (at most about
3.3 bp, always low).

## Decision
1. The independent gold fix is the GLD-NAV-implied PM price (`scripts/gold_fix_gld.py`, raw/fixes/GLDNAV_XAU_PM.csv,
   local only: the source forbids redistribution), stamped 15:00 Europe/London, London closures and early closes
   dropped by the pinned XLON calendar, stale NAV days dropped, cut at the holdout. The 15:00 alignment was checked,
   not assumed: the dispersion against Dukascopy has a sharp minimum at 15:00-15:04 London in every year tested.
2. Dukascopy XAUUSD is graded by `scripts/dukascopy_gold_grade.py` with the frozen `grade_month_v2` (era_quality.py blob
   c6f283d7, verified at run time; the run refuses a dirty tree), HistData hourly as the native reference.
3. fs-v1 loads ONE tiers file, the served tiers (raw/tiers/tiers_served_r11-v2.1.csv): HistData tiers for every key
   but XAUUSD, the Dukascopy tiers for XAUUSD. HistData XAUUSD is not served (A16: one source per instrument-minute).
   `config/data/source_precedence.yaml` (generated) records, per instrument, which source serves each month and at what
   use (intraday at A/A-consistent, daily at B, none at C/F/P), with the sha256 of every input.
4. The grade is applied as frozen. The rule is not edited after seeing grades.

## Consequences
- Result (r11-v2.1, 269 months): 2003-2008 all C (no independent minute reference); 2009-2018 A 78, B 16, C 26;
  2019-2025-09 all C on `fix_vs_close`.
- The 2019-25 C is a property of the check, not evidence against the feed. Of the 78 months graded A against the
  independent HistData minutes, 0 pass the frozen fix check (median share within 10 bp 0.76). The check compares the
  bar that closes at the nominal 15:00 against a price an auction discovers minutes later. Measured at +2 min, the
  2019-25 years (0.70-0.93 within 10 bp, MAD 2.5-5.9 bp) look like the independent 2014-18 years (0.83-0.98,
  2.2-3.4 bp). 2020 is the outlier (0.70).
- The gold judge window 2018-01..2025-09 now holds 149 graded days (2018 only), against the card's projected 2,002.
  Under r11-v2.1 the gold confirm window cannot be judged. The learn window keeps 1,473 intraday days (card: 2,070).
- The quant ruled r11-v2.2 on 2026-11-26 (section below). The calibration made it VOID (see the result block), so the
  v2.1 grade stands, and gold 2019-2025-09 stays C.
- 2003-2008 stays C under any fix-only rule unless the quant rules that a daily fix alone can carry B for
  close-to-close use. The fix evidence for those years (median -1 to -12 bp, MAD 6-12 bp: wide early spreads, bid
  below the fix) is in fix_evidence.csv.

## Ruling r11-v2.2-2026-11-26 (quant, on D-1)
Only the GOLD fix check changes. The FX H.10 check and every other v2.1 constant stay as they are
(`goldbot.data.era_quality.R11_V22`, `gold_fix_window_d`, `calibrate_gold_tol`, `power_check`).
- **Per-day statistic.** d = the minimum, over the Dukascopy 1m bid closes of bars closing in (15:00, 15:10]
  Europe/London, of |ln(close / fix)| × 1e4.
  - A day with fewer than 5 of those 10 bars is dropped and counted.
  - There is no other window and no per-minute alignment, and the GLD bias is not corrected.
- **Month pass.** The share of days with d ≤ tol is at least 0.98, over at least 15 fix days.
- **Calibration set.** Dukascopy XAUUSD months graded A under v2.1 against independent HistData minutes, 2015-04..2018-12
  (IBA era, 38 months). Calibration never uses 2009-01..2015-03 or 2019-25.
- **tol.** The smallest value on a 0.5 bp grid at which at least 37 of the 38 calibration months pass.
- **Power check.** With the fix shifted by +2·tol and by −2·tol in every calibration month, at least 95 % of the shifted
  months must fail.
- **Void.** If tol > 15 bp or the power check fails, v2.2 is void and nothing is regraded.
- **One command.** `scripts/dukascopy_gold_grade.py grade` calibrates, records tol and the power result (summary and the
  block below), then regrades. It refuses unless the imported era_quality module is the v2.2 blob and the tree is clean,
  including untracked files under goldbot/ and scripts/.
- 2003-08 stays C.

**Disclosure.** Every gold confirm report lists:
- the C months of the confirm window;
- the realised-vol tercile mix of excluded vs kept months.

The helper is `confirm_disclosure` in `scripts/dukascopy_gold_grade.py`. Its output for 2018-01..2025-09 is written to
`confirm_disclosure_r11-v2.2.json`.

**Single use.**
- There will be no r11-v2.3 on the gold fix for 2018-25.
- Any later change to the gold fix check sends gold's confirm to the fallback, in this order:
  1. a second independent 1m source graded under v2.1;
  2. otherwise, a forward window from 2026-10-01, pre-registered with the same gates.
- The holdout is never confirm.

**Raw source archives are not sealed (code review L4).**
- The cut at 2025-10-01 applies to everything stored for research and to every derived file: the fix CSV, the grades,
  and the D-13 day-candle cache.
- The source archives as served are kept for lineage and still contain later rows: the GLD XLSX, the HistData 2025 zips
  and the Dukascopy year files.
- Nothing reads those archives except the parsers, and every parser cuts before computing.

## r11-v2.2 calibration result
<!-- r11-v2.2 calibration (generated by scripts/dukascopy_gold_grade.py) -->
- Rule r11-v2.2-2026-11-26, rule blob `9ea9be33655487b095d76cdc11af589e0ce583c8`, script sha256 `56a49042f357270bf3b8b8d4f2e1ac85aa7b3c92a8f26b4504239b749ef7ba99`, run at `a343592d6ebe801290cf4cc1bfc00299542e0b57`.
- Calibration months (38): 2015-04..2018-12, Dukascopy A under v2.1 against independent HistData minutes.
- tol = 39.0 bp; 37 of 38 calibration months pass.
- Power not run (void on tol): tol 39.0 bp > 15.0 bp, so v2.2 is VOID.
- Not regraded (void).
<!-- end -->

**Outcome (2026-11-26).** v2.2 is void.
- tol came out at 39 bp, above the 15 bp limit. The cause: a month passes only if at least 98 % of its 16-22 fix days
  are within tol, so in practice every day must be, and tol is set by the 37th-smallest monthly maximum of d.
- Across all 768 calibration days, the share with d within 5 / 10 / 15 bp is 0.918 / 0.969 / 0.983. The largest days
  were 2017-04-11 (92 bp) and 2017-12-27 (39 bp).
- Nothing was regraded: the served tiers stay the r11-v2.1 file (sha256 ff653ea7).
- Under the single-use rule, gold's confirm moves to the fallback: (i) a second independent 1m source graded under v2.1,
  else (ii) a forward window from 2026-10-01, pre-registered with the same gates. The holdout is never confirm.

## Fallback rules (quant, signed 2026-11-26; written before any candidate source is measured)
**Lesson (verbatim).** "Any share threshold in a future grading rule is checked for granularity before freezing: with n
days a month, a share s tolerates floor((1 − s)·n) bad days; state that number in the rule."

**(i) Admission precondition for a second independent 1m XAUUSD source (2019-01..2025-09).** A candidate counts only
if every year 2019-2025 passes all three:
- (a) its hour-ending exact-close share vs Dukascopy, after r11 clock alignment, is < 0.10;
- (b) its share of identical 1m close-to-close changes (in source points) vs Dukascopy is < 0.10;
- (c) there is no documented Dukascopy liquidity-provider or backfill relation; with unknown provenance, (a) and (b)
  decide.

What this admits and refuses:
- Broker MT5 history is admissible if it passes (a)-(c) each year. After the EET-with-DST conversion, its clock offset
  must be at most 1 min, else the month is C.
- CME GC is not admissible (futures basis).

**No shopping across sources.** ONE source is chosen BEFORE any grading: the one that passes (a)-(c) with the most
2019-25 coverage.
- The choice is recorded in this ADR.
- Only then is the source graded, under r11-v2.1-2026-11-25 unchanged, with the reference sha256 in the tiers rows.
- The A16 manifest is regenerated only after that.
- Candidates are measured on (a)-(c) only; none is graded before the choice is recorded.

**(ii) Backstop pre-registration (verbatim).** "Gold confirm forward window starts 2026-10-01 (after the holdout). Data:
Dukascopy 1m graded monthly under r11-v2.1 against the (i) reference if admitted, else against the traded broker's MT5
minutes under (a)-(c). Same gates as the card. The read happens once, at the first month-end where outcome-blind graded
n_eff ≥ the card's judge requirement (counted from graded days only, never from labels); no interim reads. The holdout
is never confirm."

**(i)(b) as ruled (quant, 2026-11-26; verbatim, written before any full-year number is computed).** "(b) = per year,
the share of identical 1m close-to-close changes, both series rounded to the coarser tick grid and clock-aligned by the
documented converter, over minutes where at least one series moved; < 0.10 every year 2019-25. Valid only if the
controls, computed first in the same run, give: HistData-vs-Dukascopy 2019-25 ≥ 0.50 each year, Dukascopy+37c synthetic
≥ 0.90, HistData-vs-Dukascopy 2010-18 < 0.10 each year. If a 2010-18 year is ≥ 0.10, the bound becomes the 2010-18
maximum. If a positive control fails, the candidate is not admitted. The 12-day sample (2026-11-25) is disclosed, not
evidence. Price field, clock converter and coverage are fixed from source documentation before grading; single use."

**12-day sample of 2026-11-25 (disclosed, not evidence).** Forexite, the 10th of each month (next weekday),
2019-01..2025-09, 80 usable days. Yearly means:
- (a), hour-ending exact-close share: 2019 0.000, 2020 0.040, 2021 0.018, 2022 0.015, 2023 0.025, 2024 0.025,
  2025 0.073.
- (b), identical changes counting minutes where both series are flat: 2019 0.145, 2020 0.076, 2021 0.062, 2022 0.055,
  2023 0.096, 2024 0.067, 2025 0.051.
- (b), counting only minutes where at least one series moved: 2019 0.094, 2020 0.061, 2021 0.048, 2022 0.043,
  2023 0.076, 2024 0.056, 2025 0.046.
- 1m return correlation with Dukascopy 0.91-0.96. Best shift -61 min in winter and -121 min in summer.

**Forexite inputs, fixed from its documentation before measuring (2026-11-26).** Source: forexite.com
free_forex_quotes, the archive pages forex_history_arhiv*.html (Russian; fetched 2026-11-26).
- Price field: NOT DOCUMENTED for the files used. The archive lists three files per day:
  - "ProSuite Bid" (`DDMMYY.xpo`);
  - "ProSuite Trade" (`DDMMYYtr.xpo`);
  - "MetaStock" (`DDMMYY.zip`), the ASCII 1m files measured here.

  Only the two ProSuite files carry a stated field, and those are a binary Omega Research export ("ExportFormat5.1")
  with no published layout. The ASCII MetaStock file's field (bid, mid or trade) is not stated anywhere on the site.
- Clock: the archive header reads "Период, GMT+1" (period, GMT+1). DST is not mentioned. The converter used is
  deterministic: local stamps are read as Europe/Berlin civil time (GMT+1, GMT+2 under EU summer time), converted to
  UTC, then shifted by −1 min to turn a close stamp into an open stamp. This adds EU DST to the documented "GMT+1", as
  the ruling specifies ("GMT+1/+2 with DST"), and the post-conversion r11 offset search must then find at most 1 min.
- Coverage: one file per calendar day, Sundays included. Year coverage = the share of Dukascopy session days (each with
  ≥ 60 % of its session minutes) on which Forexite holds ≥ 60 % of Dukascopy's minutes. A year is short below 0.95,
  which tolerates floor(0.05 × ~250) = 12 missing days a year (2025: Jan-Sep, ~187 days, 9).
- Tick grid: Forexite XAUUSD quotes 2 decimals (0.01). Dukascopy quotes 3 decimals. The coarser grid is 0.01, and the
  HistData controls use the coarser of their own detected grid and Dukascopy's.

- Rounding to the coarser grid uses integer arithmetic: each price becomes integer source points (round(price / own
  tick)), then the coarser grid by round-half-up in integers. A constant price shift then moves every price by the same
  number of ticks. Float rounding at half-ticks would not do that: a code dry run broke the +37c control that way.
- Disclosure: before the script was committed, a development dry run on 2019 checked the code. It used the float
  rounding that was later replaced, so it is not evidence. Its figures:
  - Forexite 2019: coverage 1.00, r11 clock offset 0 min, (a) 0.009, (b*) 0.094;
  - HistData-vs-Dukascopy: 2019 1.000, 2012 0.114;
  - +37c synthetic 2019: 0.548, the rounding defect above.

**Admission run (2026-11-26).** Script `scripts/research/gold_second_source_admission.py` at commit 32930508. The
script sha256 and every figure below are in raw/forexite_m1/_reports/admission.json (sha256 1bcb932d...). The controls
were computed first, in the same run.
- Negative control, HistData vs Dukascopy 2010-18, (b*) per year: 2010 0.037, 2011 0.074, 2012 0.114, 2013 0.160,
  2014 0.247, 2015 0.155, 2016 0.170, 2017 0.174, 2018 0.111. Six years are ≥ 0.10, so the bound becomes the 2010-18
  maximum, 0.247.
- Positive control, HistData vs Dukascopy 2019-25: 0.99996-1.00000 every year. The +37c synthetic is 1.000 every year.
  Both positive controls are valid.
- Forexite 2019-2025-09:
  - coverage 1.00 every year (258/256/257/257/256/259/193 session days);
  - r11 clock offset 0 min every year after the converter;
  - (a): 0.006, 0.020, 0.011, 0.017, 0.013, 0.017, 0.045;
  - (b*): 0.072, 0.038, 0.041, 0.041, 0.044, 0.038, 0.035.
- Input error, found after the run and disclosed here. The inputs above recorded Forexite's XAUUSD grid as 0.01. The
  files actually quote gold on a 0.1 grid: no 2019 or 2023 price has a non-zero cent digit. The ruling's "coarser
  tick grid" is therefore 0.1. The (a) and (b*) above, computed on 0.01, understate equality and are not evidence of
  passing. They are not recomputed: single use.

**Decision: Forexite is NOT admitted.** Its price field is not documented by the source (inputs above), and the ruling
fixes the price field from documentation before grading. That alone refuses it, whatever (a) and (b*) show. No other
candidate passes the precondition (broker MT5 terminals hold about 3 months of M1; FXCM's archive is behind a login;
CME GC is refused). **Source choice (i): none.** Nothing is graded against a second source. The served tiers, the A16
manifest and gold's n_eff stay as they are. **D-1 goes to fallback (ii):** the pre-registered forward window from
2026-10-01, above. The holdout is never confirm.

**Forexite closed (quant C2, verbatim).** Forexite is closed for D-1 and the 2018-25 confirm window: single use; a later documentation of its price field does not reopen it, and its (a)/(b*) figures are never cited as independence evidence.

**(ii) broker capture (quant K2, verbatim; replaces the C3 sentence).** Broker MT5 M1 from 2026-10-01 is captured daily by the brain's `feed_reconcile` (copy_rates_range, read-only bridge) into `bars_1m_broker`. That table has no retention pruning and is in the backup set (D-11/N13). A missed trading day raises the learning-liveness alert the same day. `scripts/broker_m1_capture.py` copies each closed month into raw/broker_m1/ with a sha256 per file and a gap log within 5 days of month-end, and before any grade. A month without a complete capture grades C. MT5 bars are Bid-built (MT5 Help, 'Charts', cited). The broker reference must pass (a)-(c) per month before it grades.

How each claim holds (2026-11-26):
- **Retention.** No job prunes or rotates `bars_1m_broker`. The store rewrites a partition only on a dedupe append,
  and rows are kept. Compaction covers other tables only and keeps every row. Tick retention applies to state/ticks.
- **Backup.** The table is in the daily restic backup: only `dukascopy`, `fred`, `features` and `labels` are excluded.
  The last backup was ok at 2026-11-25 22:24 UTC. Snapshot retention is daily 14, weekly 8, monthly 12.
- **Same-day alert.** Health check `learning:broker_m1_capture` (goldbot/ops/checks/capture.py, ops PR
  `ops/broker-m1-capture-guard`) FAILs from 23:50 UTC on a session day when icm-demo holds fewer than 60 % of that
  session's 1,380 minutes. A FAIL reaches the owner through the health alert loop. Until that PR is deployed, this
  claim is not live.
- **Month copy within 5 days.**
  - The daily scheduler job `broker_m1_copy` (23:55 UTC on weekdays, same ops PR) rewrites the previous and the
    current month into raw/broker_m1/icm-demo/ on the brain, with SHA256SUMS and gaps.json.
  - The same check FAILs when a closed month's copy is missing more than 5 days after month-end.
  - `scripts/broker_m1_capture.py` makes the same copy from the Mac before any grade.
  - Until that PR is deployed, this claim is not live.

**Capture guard requirements (quant, on #471, 2026-11-26).**
- **Freeze rule.** A closed month freezes once its 5-day deadline passes. Any later rewrite gets a new sha256 and an
  entry in that month's gap log (and in rewrites.jsonl), and is graded only under the sha recorded in its tiers rows.
- **Gate.** No fallback-(ii) grade runs until `learning:broker_m1_capture` has passed at least once on the brain after
  #471 is deployed.
- **Deadline.** If #471 is not live by 2026-12-05, October and November are copied from the Mac by that date
  (`scripts/broker_m1_capture.py`) and their sha256 are recorded in the D-1 status.
- **Two checks.** #471 has two checks, so a late month copy cannot hide a missed day or the reverse:
  - `learning:broker_m1_capture`, the daily capture. Weekends and CME metals full closures are never due.
  - `learning:broker_m1_copy`, the month copy and its sums.
- **Read-only replay before enabling (2026-11-26 03:39 UTC).** The daily-capture rule was replayed on the brain over
  the last 20 due icm-demo session days (2026-10-29..2026-11-25):
  - 0 false FAILs: 1,318-1,377 of 1,380 minutes, and the Mondays are 1,318.
  - Thanksgiving 2026-11-26 was not yet due at the run. On Dukascopy, Thanksgiving 2024 held 1,286 minutes and the
    day after 1,242, both well above the 828-minute line.

**Reference account (quant K3, verbatim).** Reference for (ii): icm-demo (the traded ICM feed). Fixed 2026-11-26, before any grade. Vantage and icm-probe are not graded against Dukascopy for D-1. If icm-demo fails (a)-(c) in a month, that month grades C. There is no switch to another account.

- Citation: MetaTrader 5 Help, "Charts" (metatrader5.com/en/terminal/help/charts_analysis/charts, fetched
  2026-11-26): "Bid prices are used for constructing charts."
- First copy, 2026-10-01..2026-11-25 23:19 UTC:
  - icm-demo: 54,964 minutes; every gap is the daily 63-min break.
  - vantage-demo: 55,011 minutes; every gap is the daily 62-min break, plus one 1-minute hole on 2026-10-01 at 05:00.
