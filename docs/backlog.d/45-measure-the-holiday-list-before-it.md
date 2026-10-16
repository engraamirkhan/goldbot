---
number: 45
rank: 14
title: Measure the holiday list before it is adopted
stage: Operational risk (evidence) · RUN
size: S
status: 'in review: draft delivered 2026-10-16 (feda07a, docs/research/holiday-list-measurement-2026-10.md, a week early) · quant-reviewer ruling on the draft due 2026-10-23 · adopt-or-not decision on the v3 list (drop 2 Jan) by program director + product owner due 2026-10-30 · accountable: performance-analyst (draft), quant-reviewer (ruling) · due 2026-10-30'
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
