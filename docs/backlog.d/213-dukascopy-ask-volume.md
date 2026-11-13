---
number: 213
rank: 74
title: Keep Dukascopy's ask-side tick volume in the 1m bars
stage: 'data (follow-up of R139, 2026-11-13)'
size: M
status: 'proposed · accountable: data-engineer · reviews: quant-reviewer'
---
- **213. Keep Dukascopy's ask-side tick volume in the 1m bars.** Value: `scripts/dukascopy_year.py` stores only the bid side's volume as `tick_count` and drops the ask side's, so R139's imbalance is a bulk-volume classification from price changes; the quoted-volume imbalance (bid vs ask volume) is a second, independent proxy. Acceptance, tests first: a nullable `tick_count_ask` column in the bar schema (absent = NaN, never 0; resample sums it like `tick_count`); `orderflow` gains `of_quote_imb_15m/60m` only where both sides exist; the re-pull's size and Actions minutes are estimated before any run, and the data stays local (no release).
