---
number: 66
title: Equity-scaled lot ceiling at the bridge (GuardedBroker)
stage: Execution and risk (independent backstop under RiskGate) · PROTECT · RUN
size: M
status: 'done, accepted 2026-10-17 by the product owner (on main via PR #84, merge 0bd0c10: plan 8567c4d; trading-safety APPROVE x2, code APPROVE, security PUSH-OK, SRE APPROVE, ui-ux APPROVE). Criteria: 0.01 lot passes and 0.02 refused at 1,000, scales at 10,000, unreadable figures refuse, closes never gated (tests/test_bridge.py ceiling tests, re-run pass); built stricter than asked (counts all open XAUUSD exposure) · accountable: release-manager'
---
- **66. Equity-scaled lot ceiling at the bridge (GuardedBroker).** Decided 2026-10-16 by program director + product
  owner from the CFO's capital plan (`docs/finance/capital-plan.md` section 6, second sre row). Value (RUN,
  protect): RiskGate is the only path to an order, but the only independent ceiling behind it is a fixed
  `MAX_ORDER_LOTS = 3.0` in `goldbot/execution/bridge.py` (constant at line 104, checked in
  `GuardedBroker.place_order` at line 266). The terminal read on 2026-10-16 (BACKLOG 27) shows 1:5000 leverage and
  £0.63 margin per 0.01 lot, so the broker would accept about 15.9 lots on £1,000, and the bridge would pass up to
  3.0 lots: about 950 x equity in notional, where the gate's own cap is 6 x equity (`gate.py` 540-543, 0.30 x
  `leverage_cap` 20). A bug or a corrupted intent past the gate would meet no backstop sized to the account. The
  ceiling only tightens and never touches an exit. It finds no edge; it bounds the worst single-order loss.
  TRACEABILITY: the architect names the row for the bridge guard (or adds one with `new-row`) in the plan.
  Depends on: 27 for the measured contract size and margin (already read from the terminal, see 27).
  Ranked third: it shrinks the worst case on the money path by two orders of magnitude, it does not block the Sunday
  engine start, and the architect lane is free (all-hands 2026-10-16, E4).
  Acceptance:
  - Plan: the architect writes the plan by Fri 2026-10-23 (formula; where equity, price and contract size come from
    on the bridge side; behaviour when any is unreadable; interaction with the minimum-lot exception of ADR 0004 and
    item 25). The trading-safety-reviewer records a verdict on the plan before any code.
  - `GuardedBroker.place_order` refuses an order whose notional (lots x contract size x price, in account currency)
    exceeds 6 x equity (the gate's combined-notional cap), with equity read on the bridge from the terminal, not
    taken from the brain's request. The fixed 3.0 lots stays as the outer bound.
  - Fail closed: if equity, price or contract size cannot be read, the order is refused with a reason. `modify` and
    `close` are never subject to the ceiling (exits are never gated).
  - Tests (bridge suite): at £1,000 equity and £3,150 notional per 0.01 lot, 0.01 lot is accepted and 0.02 lot
    (about 6.3 x) is refused; at £10,000 the ceiling scales with equity; an unreadable equity refuses the order; a
    close of a 3.0-lot position still goes through; the refusal reason reaches the engine journal.
  - Reviews: trading-safety APPROVE on the plan and on the build, code-reviewer APPROVE, security-reviewer PUSH-OK,
    each recorded at the merge with reviewer, verdict, sha and date (all-hands E1).
