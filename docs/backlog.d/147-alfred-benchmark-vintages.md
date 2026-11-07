---
number: 147
rank: 55
title: ALFRED first-release benchmark rates for the cost model
stage: 'Research (data)'
size: S
status: 'proposed · accountable: data-engineer · reviews: quant-reviewer'
---
- **147. ALFRED first-release benchmark rates for the cost model.** Value: the catalogue cost model's OECD IR3TIB01 3-month rates (EUR, JPY, GBP, AUD, CAD, CHF, NZD) are the 2026-11-02 vintage, a small look-ahead in financing (code review LOW-3). Acceptance: the first-release value of each monthly observation is pulled from ALFRED (free) into raw/daily with its real `available_utc`; `costs.window_model` reads it; a test shows a revised month uses the first-release value before the revision date; the reading-rule note is removed.
