---
number: 243
rank: 79
title: Trade detail shows MFE and MAE
stage: 'dashboard (follow-up of backlog 195 / R147)'
size: S
status: 'done (2026-11-14, branch feat/cleanup2-mfe-ui-1114, R155): TradeRow carries the R135 MFE/MAE, Details shows them; tests/test_trades_ledger.py and web/src/lib/trades.test.ts · reviews: code-reviewer'
---
- **243. Trade detail shows MFE and MAE.** Value: backlog 195 reads the R135 excursions in the perf report, but the trades API (`TradeRow`) carries no MFE/MAE field, so the dashboard's trade detail cannot show how far a closed exploration trade went for and against it. Acceptance, tests first: `TradeRow` gains optional `mfe_r`, `mae_r` and `excursion_approx` (None on rows closed before R135 and on every non-exploration row), the API contract is regenerated (`scripts/export_openapi.py`, `npm --prefix web run gen:api`), and the trade detail's "Details" disclosure shows them, "not recorded" when None (never 0 R) and labelled approximate when `excursion_approx`.
