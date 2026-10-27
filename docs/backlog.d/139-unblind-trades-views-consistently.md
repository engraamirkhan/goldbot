---
number: 139
rank: 8.75
title: 'Trades views follow the ranking: per-strategy blinding retired consistently'
stage: 'API (trades) · UI · Telegram'
size: M
status: 'built 2026-10-27 (U34, incl. the Telegram 3-arm rule) · review findings of 39a335be fixed · reviews owed: quant-reviewer, code-reviewer'
---
- **139. Trades views follow the ranking: per-strategy blinding retired consistently.** Value: once the ranking shows per-family results, a filtered trades list or a Today total that still says "Results hidden" is contradictory noise; every view tells the same numbers; lifecycle: review; helps RUN (one truth about the money). Acceptance: (1) `trades_ledger.withheld_reason` no longer blinds rows or totals for unread arms (ADR 0053 supersedes ADR 0050 decisions 2-3); the payload leak tests are replaced by equality tests (a family filter's totals equal that family's ranking row for the same window); (2) sorting by result is enabled in the List; (3) the daily review's banned-token and `blind()` checks are removed or narrowed to ids and logins only; (4) "Results hidden" copy is gone from web and Telegram (grep test); (5) ADR 0050 decision 1 (money always visible) still holds (existing tests pass); (6) if the quant reviewer instead rules that unread families stay blinded, this item is rejected and 136-138 rank only read families and engine strategies (ADR 0053 "Revisit"). Depends: 137; quant ruling. TRACEABILITY: none yet; implementer amends the 129 U row.
