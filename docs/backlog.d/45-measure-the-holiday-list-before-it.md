---
number: 45
rank: 14
title: Measure the holiday list before it is adopted
stage: Operational risk (evidence) · RUN
size: S
status: 'ready · accountable: performance-analyst · due 2026-10-30'
---
- **45. Measure the holiday list before it is adopted (performance analyst; review M3, 2026-10-11).** Value: the
  holiday window (item 17, `config/holidays_cme_metals.yaml`) is a proxy, not a measurement; blocking entries on a
  day that trades normally costs signals for nothing. Do: for every listed day in the stored ticks and bars, the
  median spread, tick count per hour and 15m ATR against matched normal days (same weekday, the two weeks either
  side, holidays excluded), by session. Report which listed days are thin and which are not, and any unlisted day
  that is (UK, JP, CN holidays, early closes). Acceptance: a table per day type with the ratio and its interval; the
  list is changed (new version, hand-reviewed set in the test) only on that evidence. Reporting only; quant review.
  Sweep 2026-10-16 (product owner): accountable `performance-analyst`, due 2026-10-30. It runs on stored bars and ticks, so it does not wait for the deploy. Acceptance as above. It is the evidence the owner needs to adopt 17.
