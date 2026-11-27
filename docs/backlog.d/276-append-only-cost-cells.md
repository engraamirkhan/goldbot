---
number: 276
rank: 99
title: Append-only cost cell rows
stage: 'Data (replay costs)'
size: S
status: proposed
---
- **276. Append-only cost cell rows.** Value: `daily_review.write_cell` merges a same-day refit into one row (MAX slippage, MAX through_ns, MIN fitted_ns), so a label replayed at a slot between two same-day fits can see a value or a fill horizon that did not exist yet at that slot, and the point-in-time read (`_cells`, through_ns <= slot) then drops the whole row; quant review of PR #507. Acceptance: exploration_cost_cells rows are append-only, keyed (broker, session, day, fitted_ns), each fit keeping its own slippage, fills and through_ns; write_cell does no MAX/MIN merge; `_cells` takes per session the latest row with fitted_ns <= slot and through_ns <= slot; a test replays a slot between two same-day fits and gets the first fit's value; a migration keeps existing rows.
