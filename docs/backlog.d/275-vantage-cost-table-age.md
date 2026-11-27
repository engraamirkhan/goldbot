---
number: 275
rank: 98
title: Cost table age limit
stage: 'Data (replay costs)'
size: S
status: proposed
---
- **275. Cost table age limit.** Value: PR #507 ages the refit cost cells, but the nightly measured cost table a broker's r1 replay rests on (`broker_cost_table`) has no age limit; vantage-demo is replay-only (PR #505), so its table stops refreshing and would cost labels on old fills forever. Acceptance: A cost table whose newest fill is older than CELL_MAX_AGE at the slot charges max(table slippage, settings slippage prior) and tags +table-stale:<day>; drop_stale_cost_labels treats it like the cells tag. Needs a newest-fill timestamp on CostTable.
