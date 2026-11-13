---
number: 214
rank: 74
title: MFE/MAE order-flow columns from our fills once lane F's fields exist
stage: 'data / research (follow-up of R139, 2026-11-13)'
size: S
status: 'proposed · blocked on lane F · accountable: data-engineer · reviews: quant-reviewer'
---
- **214. MFE/MAE order-flow columns from our fills once lane F's fields exist.** Value: plan item 8 asks for our own fills' MFE/MAE as order-flow proxies; on main the `fills` table carries no excursion fields (lane F records MFE/MAE on exploration closes only), so R139 built the side-signed slippage column alone. Acceptance, tests first: once excursion fields are stored per closed position, `orderflow` adds rolling MFE/MAE columns (in R) stamped at each position's close time (never its entry), joined with `asof_join` on available_utc, and a truncation-invariance test that seeds a later close; ctx["fills"] loading for research is added with item 211.
