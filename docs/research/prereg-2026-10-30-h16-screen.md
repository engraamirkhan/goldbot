# Pre-registration: H-16 screen on 2010-2017 (amendment A3)

Written 2026-10-30 by the opportunity-researcher, **before any H-16 output on any window was computed**. Base commit
`c19c7d8a` (origin/release/agents-rd-cto merged). Nothing in this file was chosen after seeing a target, a forecast
or a statistic. Data inspection before this freeze: file listings, series names, date ranges and row counts of the
macro and calendar files only (no price, no target, no forecast).

## What is screened

H-16 exactly as pre-registered (`preregistration-2027Q2-draft.md` "H-16", `preregistration-2027Q1.md` O-1,
`hypotheses.md` A2 row 1): target y_D = ln(W_D / A_D), HAR-only baseline (intercept + ln mean w over 1/5/22 days) vs
the full score (HAR + ln(GVZ/YZ20), tier-1 count on D, ln prior range/A_D, NR4, NR7, inside day, |reopen gap|/A_D),
every input as known at 06:30 UTC on D, GVZ by `asof_join` on `available_utc`, monthly expanding OLS refits with a
1-day purge and >= 500 rows, first OOS month by the frozen row-count rule (expected about 2012-04). No direction term.

## Frozen code and inputs (quant condition 1)

- Evaluation code: `goldbot/research/h16.py` (sha1 `d5fa78b0...`, `h16.code_hash()` recorded in the output) and
  `goldbot/features/day_type.py` (sha1 `95c59bd5...`, `VERSION = "1"`), unchanged; driver
  `scripts/research/h16_screen.py`. All three are frozen by **the commit that adds this file**; the screen doc
  records that hash. The driver calls `h16.evaluate_h16(..., holdout_from="2018-01-01")`: bars, GVZ rows (by
  `available_utc`) and calendar rows (by `available_utc` and `event_utc`) are cut at 2018-01-01 before any feature is
  built, so every HAR and full-score fit is walk-forward **inside 2010-2017**.
- Bars: `raw/xauusd_1m_dukascopy_2010..2017.parquet` only (Dukascopy 1m, resampled to 15m mid), sha256 prefixes
  2010 `26139f0b`, 2011 `a1497c69`, 2012 `89f4b77d`, 2013 `fe31b844`, 2014 `be2bf066`, 2015 `046ddd84`,
  2016 `9c6073cc`, 2017 `e04d7da0`. No 2018+ file is opened; the holdout is never touched.
- GVZ: `~/tmp/ts/macro/macro_fred.parquet` (series `gold_vix` = GVZCLS; sha256 `918e7673`), stamps audited in
  `macro-pit-audit-2026-10.md` (GVZ about one day conservative; 0.04% revised values to 2018-01).
- Calendar: `~/tmp/ts-data/cal/us_calendar.parquet` (keyless build, sha256 `7188df20`; rule-derived ISM rows are
  tier 2 and do not enter the tier-1 count).

## Reading rule (unchanged from O-1; A3)

**Pass** if, pooled over the screen's out-of-sample days, Spearman rho(full, y) - rho(HAR, y) >= 0.05 **and** the
mean W/A of the full score's top quintile >= 1.5 x its bottom quintile's (inclusive, 1e-12 float tolerance).
Otherwise **fail**. A NaN statistic or fewer than **1,200** evaluation days (calendar estimate about 1,450 for
2012-04..2017-12 minus GVZ-stale days) refuses the screen before any statistic; nothing is read.

- **Pass:** the formal O-1 trial on 2018-01-01..2025-09-30 is pre-registered next and read once by the same rule.
  The screen is not evidence for adoption; it only permits the trial.
- **Fail:** the line's extra inputs are killed: GVZ, tier-1 events and compression/gap are retired as range inputs,
  the day factor uses HAR alone, and the formal O-1 trial is **cancelled** (not run). H-17 is unaffected (it
  conditions on the HAR baseline only).
- **Quant condition 2:** the screen can only cancel the formal trial. Any change to features, model or rule after
  this point is a new pre-registration with a new id, never a re-run of this one.

## Sample floor for the 2018-2025-09 trial (quant condition 3)

Evaluation days restricted to 2018-01-01..2025-09-30 (fits may use earlier rows, expanding, as frozen). Calendar
estimate about 93 months x 21 = ~1,950 stub-free days, less GVZ-stale days. Floor: `expected_eval_days` counted
target-blind before the trial freeze and the run refused below 90% of it, and in any case refused below **1,700**
evaluation days. Note: `evaluate_h16` today reads every month from the first OOS month; the trial needs an
evaluation-start parameter (2018-01-01) added and reviewed before its freeze.

## Reported, not in the rule

20-day block-bootstrap 95% interval of the rho gain; drop-one contributions; rho gain by year; HAR-only quintile
ratio; north-star capture: share of the best realised days (top W/A quintile) in the score's top two quintiles and
of the poorest days (bottom quintile) in its bottom two, for both models; median W_D in multiples of the planning
round-trip cost ($0.25 and $0.85 per oz, `prereg-cost-measurement-2026-10.md`) by score quintile. No trades, so no
DSR and no net-R gate; costs enter only as that diagnostic.

## Counting

K +1 (screen, counted, not a registry trial). The registry and the trial budget are not touched.

## Status of A3

A3 is a draft awaiting the quant-reviewer's signature (target 2026-11-03). The research director dispatched this
screen on 2026-10-30 with the quant-reviewer's three binding conditions (above), which this file implements.
