---
number: 45
title: Measure the holiday list before it is adopted
stage: Operational risk (evidence) · RUN
size: S
status: 'parked from the table 2026-10-17 (cap of 15; work and dates unchanged, it feeds a window that stays off): in review: draft feda07a ruled 2026-10-16 (quant CHANGES, v3 list SAFE TO ADOPT; ruling applied by the research director) · DECIDED 2026-10-16: ADOPT cme-metals-v3 (implementation: 66, due 2026-10-30) · final with scripts/measure_holidays.py due 2026-10-30; G1 re-run addendum by 2026-11-13 · accountable: performance-analyst · due 2026-10-30'
---
- **45. Measure the holiday list before it is adopted (performance analyst; review M3, 2026-10-11).** Value: the
  holiday window (item 17, `config/holidays_cme_metals.yaml`) is a proxy, not a measurement; blocking entries on a
  day that trades normally costs signals for nothing. Do: for every listed day in the stored ticks and bars, the
  median spread, tick count per hour and 15m ATR against matched normal days (same weekday, the two weeks either
  side, holidays excluded), by session. Report which listed days are thin and which are not, and any unlisted day
  that is (UK, JP, CN holidays, early closes). Acceptance: a table per day type with the ratio and its interval; the
  list is changed (new version, hand-reviewed set in the test) only on that evidence. Reporting only; quant review.
  Sweep 2026-10-16 (product owner): accountable `performance-analyst`, due 2026-10-30. It runs on stored bars and ticks, so it does not wait for the deploy. Acceptance as above. It is the evidence the owner needs to adopt 17.
Research job check 2026-10-16 (research director): not blocked, so it starts now; draft table to the research director by Fri 2026-10-23, final with quant review by 2026-10-30. Days inside the data-v1 coverage gaps (G1/G2, Q1 readiness list item 5) are excluded and listed, not read as thin days.
  Decision schedule 2026-10-16: the v3 holiday list (drop 2 Jan) is decided AFTER the quant ruling on this draft. Quant-reviewer ruling by 2026-10-23; decision by 2026-10-30, recorded here with the evidence it rests on. Decided 2026-10-16 by program director + product owner (owner delegated). Adopting a list that only blocks entries is conservative and within the delegation; the list change ships as a new version with the hand-reviewed set in its test (acceptance above).
  **Decision 2026-10-16 (program director + product owner, under the owner's delegation; owner-only items untouched):
  ADOPT cme-metals-v3** (drop 2 Jan, which has no effect before 2029-01-02; keep every other listed day, with
  Juneteenth, Easter Monday, the day after Thanksgiving and 26 Dec marked unresolved; add none), with the final of
  the note on 2026-10-30. Evidence: `docs/research/holiday-list-measurement-2026-10.md` (draft feda07a, quant ruling
  applied 2026-10-16), Tables 1 and 4: 2 Jan is NORMAL on every reading, including Bonferroni and year clusters.
  Adopting the list does not switch the window on: `risk.holidays.enabled` stays off and stays with the owner, after
  46. The list change is item 70 (implementer, due 2026-10-30).
  Review record (E1): quant-reviewer · CHANGES (v3 list SAFE TO ADOPT; text fixes M1-M7, H-A/H-B/H-C to rewrite before pre-registration) · draft feda07a · 2026-10-16.
  Still owed by the performance analyst: the final (2026-10-30) with `scripts/measure_holidays.py` and its test (Table 4
  variants and the listed-day overlap rule included), and the re-run addendum on the repaired data-v1 in the same run
  as the H-04 recount, by 2026-11-13.
