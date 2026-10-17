---
number: 84
rank: 20
title: An approval whose send raises is archived NOT CONFIRMED, never left placing
stage: Execution and reconciliation (entry outcome truth) · PROTECT
size: S
status: 'ready (trading-safety residual on BACKLOG 72, 2026-10-17) · accountable: implementer · reviews: trading-safety · due 2026-10-24'
---
- **84. An approval whose send raises is archived NOT CONFIRMED, never left placing.** Value: when `on_decision` raises during the send, the card stays `placing`: the dashboard shows "submitted", drops it after 10 minutes and never shows the resolution (the owner still gets every notice and entries stay refused). Rarer: an engine silent 60 s mid-send whose order then fills leaves the card NOT CONFIRMED with no "opened" notice. Acceptance: if `on_decision` raises, `_finish` archives the card not confirmed (placing cleared) then re-raises; a later fill found by the look-up still sends the "opened" notice and updates the card; tests for both; polling the outbox never returns APPROVED in either case.
