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
- Open, owner quant-reviewer, due 2026-12-02 (D-1): an r11-v2.2 fix check calibrated on the 2009-18 independent months
  only, for example the comparison minute and a tolerance set from those months' distribution. The calibration rule
  must not be chosen by looking at the 2019-25 numbers above, which are already reported. A regrade is one command.
  If the ruling keeps the check, gold has no confirm window before the holdout, and the confirm role passes to the
  sealed holdout or to a forward window.
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
