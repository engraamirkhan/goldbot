---
number: 262
rank: 87
title: Closed exploration rows without a fill price
stage: Data integrity · exploration ledger
size: S
status: 'proposed 2026-11-14 (evaluator, weekly review 2026-11-14) · accountable: data-engineer · reviews: quant-reviewer, trading-safety-reviewer'
---
- **262. Closed exploration rows without a fill price.** Value: the evaluator found 7 closed rows in aux.db `exploration_trades` (on the brain) that have a P&L but no fill price: 5 Z7V and 2 ZCV. A closed row without its fill cannot be checked against the broker, and its slippage and cost cannot be measured. The reads, the cost of record and the ranking then rely on a P&L that cannot be audited. Acceptance: (1) find out why the fill price is missing on these rows, by code path and broker/host, and name the cause; (2) backfill the fill price from the broker deal where one exists, with each backfill recorded and none guessed; a row with no deal is reported, not filled in; (3) add a guard so a closed row cannot lack a fill price: a test on the writer, plus a health check that warns on any closed row without one; (4) the 7 rows are listed with their outcome (backfilled, or why not) in the item's done note. Out of scope for fix/weekly-read-due, which only records the gap (docs/research/weekly-decisions-2026-11-14.md).
