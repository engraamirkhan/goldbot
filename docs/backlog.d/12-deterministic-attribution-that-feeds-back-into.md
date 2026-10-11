---
number: 12
rank: 15
title: Deterministic attribution that feeds back into research
stage: Performance attribution, learning · FIND
size: M
status: in review (daily report + staff-agent feedback done; director input and dashboard open)
---

### 12. Deterministic attribution that feeds back
Value: analytics today are LLM prose. A deterministic table lets the director, tournament and owner act on where
P&L actually comes from (FIND).
Acceptance:
- A weekly job writes `state/attribution.json` from the shadow book and live journal: R, count and t by family,
  timeframe, session, side, regime and cost component. Golden-file test on a fixture journal.
- `research_director` reads it. A test shows a family or session with negative attributed R over 20+ trades
  reduces that family's priority in the plan.
- A dashboard attribution view (API schema regenerated, e2e smoke).

Rows: lifecycle "Performance attribution"; G2.

Status (2026-10-10): in review. Done: daily (not weekly) job `attribution` -> `state/attribution.json` and `.md`
(goldbot/research/attribution.py) from the shadow book, fills, orders and the cost table, by family, agent,
timeframe, session, side, regime, decision, exit and cost component, small cells marked noise; tests on a synthetic
shadow book (tests/test_attribution.py) instead of a golden file (determinism is tested: same book, same bytes); the
improvement agent and research analyst read it (`read_attribution`). Open: `research_director` priority input (a
change to research budget allocation, so it needs quant review and its own test), the dashboard view (goldbot/api
and web/ were out of scope), live realised R (item 8's engine trade record). Row G12.
