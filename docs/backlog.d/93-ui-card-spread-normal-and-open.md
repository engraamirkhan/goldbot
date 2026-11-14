---
number: 93
title: Approval card shows spread now vs normal and what else is open
stage: 'UI (approvals) · PROTECT'
size: M
status: 'retired (sweep 2026-11-14, ADR 0066: no consumer): approval-card polish, real money only; re-proposed in the live-pilot readiness review'
---
- **93. Approval card shows spread now vs normal and what else is open.** Value: two essentials of the 90-second decision are missing from the card: spread now vs normal (the card shows only "22 pt", `ProposalCard.tsx:83`; `Proposal` has no normal spread, `goldbot/api/schema.py:82`) and what else is open on the account (nothing on the card). Acceptance: API `Proposal` gains `spread_normal_points` (median spread for this hour-of-week over the last 20 trading days from the engine's spread history; null when unknown) and `open_positions` (count and summed stop risk in account currency, `open_risk_amount`). Card shows "Spread $0.22 now · usual $0.18 (1.2×)" with amber at >= 1.5× (label, not colour alone) and "Also open: 1 trade, £8.20 at risk" or "Nothing else open". Telegram card carries both lines. Vitest for null, normal, wide; API test for the fields; contract regenerated.
