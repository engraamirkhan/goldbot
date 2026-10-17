---
number: 69
title: Monthly audit checks the recorded leverage and the largest order against the gate limits
stage: Review and audit (monthly finance audit) · RUN
size: S
status: 'parked 2026-10-17 (cap of 15; the work is unchanged): re-enters the table on 2026-11-01 for the November audit · accountable: finance-auditor · due 2026-11-04 (the November audit)'
---
- **69. Monthly audit: leverage and the largest order.** Decided 2026-10-16 by program director + product owner from
  `docs/finance/capital-plan.md` section 6 (finance-auditor row). Value (RUN, protect): the broker allows 1:5000
  (BACKLOG 27), so nothing outside goldbot stops an oversized order; a monthly check shows whether any order came
  near the limits and whether the account leverage changed (owner decision C5). It finds no edge.
  TRACEABILITY: X18 (finance audit record). Depends on: 27 (leverage recorded); 66 for the bridge ceiling it is
  checked against (until 66 lands, the check compares with the gate limits only).
  Acceptance:
  - `docs/finance/audit-framework.md` gains a row (the next free RC id): sources `state/broker_terms_<account>.json`
    (leverage, contract size) and the month's orders and closed trades; method: the recorded leverage and any change
    since the last audit; the month's largest order in lots and in notional / equity at entry, compared with the
    gate's 6 x equity notional cap, the 1% hard risk clamp and the bridge ceiling (66). Fail on any order above a
    limit; "not computable" while 27's file is missing.
  - The Wed 2026-11-04 audit runs the row and reports its result, or "not computable" with the reason.
  - The trading-safety-reviewer reads the row's method once, recorded in the audit with the date.
