---
number: 129
rank: 49
title: 'One trade ledger API: every kind incl. exploration, filters, paging, blinding server-side'
stage: API · data
size: L
status: 'proposed · accountable: implementer · design: ui-ux-designer · reviews: code-reviewer'
---
- **129. One trade ledger API: every kind incl. exploration, filters, paging, blinding server-side.** Value: exploration trades (95%+ of fills) are invisible to Activity and Trades summary; Activity is capped at 200 events with no filter (docs/design/trades-ux-2026-10-27.md A2, A4). Fields: `GET /api/trades?period,account,family,timeframe,status,kind,exit_reason,evidence,cursor` returns `TradeRow` (docs/design/trades-ux-2026-10-27.md §2.2: trade_id, kind, account_label, family_label, variant, timeframe, arm_id, is_control, side, lots, entry, stop, stop_now, target, exit, exit_reason, censored, opened_utc, held_s, risk_amount, pnl/pnl_now, r/r_now, costs, why, evidence, blinded) plus filter totals; `GET /api/trades/{id}`. Sources: shadow_book, orders, closed_trades, aux cost_probes and exploration_trades; labels from a new config/strategy_labels.yaml. Acceptance: ADR 0050 blinding server-side, with a seeded-arm leak test; every registered arm and family has a label (CI test); p95 under 300 ms at 50k rows; unreadable sources named, never zero; API contract regenerated; quant-reviewer and code-reviewer pass.
