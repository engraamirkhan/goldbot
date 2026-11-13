---
number: 214
rank: 74
title: Per-account MFE/MAE columns from our own closes once lane F's fields exist
stage: 'data / research (follow-up of R139, 2026-11-13)'
size: S
status: 'proposed · blocked on lane F · accountable: data-engineer · reviews: quant-reviewer'
---
- **214. Per-account MFE/MAE columns from our own closes once lane F's fields exist.** Value: plan item 8 asks for our own fills' MFE/MAE as order-flow proxies; on main the `fills` table carries no excursion fields (lane F records MFE/MAE on exploration closes only), and R139's fill-slippage column was dropped in quant review (pooled across accounts and symbols, endogenous, NaN before October 2026 = a date marker). Acceptance, tests first: once excursion fields are stored per closed position, a column is proposed only if it is per account and symbol, stamped at each position's close time (never its entry), joined with `asof_join` on available_utc, with a truncation-invariance test that seeds a later close, and the quant reviewer accepts that it is not endogenous to the arms it would screen; otherwise this item closes as not worth a column.
