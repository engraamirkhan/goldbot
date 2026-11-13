---
number: 195
rank: 74
title: Read MFE/MAE in the performance and exits reports
stage: 'research reporting (follow-up of R135, research-director ruling 2026-11-13)'
size: S
status: 'proposed · accountable: performance-analyst · reviews: quant-reviewer'
---
- **195. Read MFE/MAE in the performance and exits reports.** Value: R135 records `mfe_r`/`mae_r`/`mfe_usd`/`mae_usd` (and `excursion_approx`) on every exploration close from 2026-11-14; no report reads them yet, so the exits researchers still cannot see how much of a gold 1m/5m trade's favourable move the fixed bracket gives back. Acceptance, tests first: the performance report's per-arm table shows median MFE R and MAE R with n, leaving out rows without the fields (older rows) and counting `excursion_approx` rows separately; a test with a mixed old/new ledger proves the old rows are skipped, not read as 0 R.
