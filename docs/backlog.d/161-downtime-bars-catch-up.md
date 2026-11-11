---
number: 161
rank: 65
title: Missed base closes during engine downtime count toward open trades' holds
stage: 'engine (found in the review of backlog 160, 2026-11-11)'
size: S
status: 'proposed · accountable: implementer · reviews: trading-safety-reviewer, quant-reviewer'
---
- **161. Missed base closes during engine downtime count toward open trades' holds.** Value: base bars that close while the engine is down are never counted in `bars_held`, so after downtime a live hold runs longer than its pre-registered hold by the downtime length (a holding-period bias between live trades and their labels); broker SL/TP still run. Also `_closed_through` is in memory only, so a restart can count one close twice (a time exit one bar early). Acceptance, tests first: at the first base close after a restart each open trade is credited the base closes it missed (open market hours by the session table, from the last close saved with the orders file, or its open), saved so a second restart never counts them again; `_closed_through` (or the last counted close per trade) is persisted so no close is counted twice; exits only get earlier versus today, never later; the replay golden is unchanged (a replay never restarts).
