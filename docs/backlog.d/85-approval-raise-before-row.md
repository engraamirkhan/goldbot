---
number: 85
title: A raise before the order row exists is archived as a "not sent" refusal, never "approved and sent"
stage: Execution and reconciliation (entry outcome truth) · PROTECT
size: S
status: 'done (triage 2026-11-13): PR #90 (d7540c23, 85f0fc74)'
---
- **85. A raise before the order row exists is archived as a "not sent" refusal, never "approved and sent".** Value: when the approval path raises before `_execute` writes the order row (e.g. a bridge read failure at approval), the card says "approved and sent" though nothing was sent; the dashboard lists it unresolved for 8 days and the promised 10-minute notice never comes. Acceptance: `_on_decision` archives a "not sent" refusal code on a raise before the row exists (wording via not_placed.py, ui-ux approved); the card and dashboard show it; polling the outbox never returns APPROVED; test.
