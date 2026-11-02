# 0073: Owner trial budget 2026Q4: 3,500
<!-- Copy of docs/decisions/0073 kept under config/ so the deploy settings check (which archives goldbot/ and config/ only) can verify the owner decision. -->

Status: accepted (owner, 2026-11-02)

## Context
The 2026Q4 research trial budget stood at 158 and the validator caps any quarter override at 500 unless an owner
decision is recorded. On 2026-11-02 the owner directed, in chat with the orchestrating session:

- "the team needs to find 200 signal strategies, backtest on historical data and deploy the successful ones"
- "now find ranked 3000 and test those"

Carrying that out needs about 3,500 trials in 2026Q4: the sprint-3000 catalogue (3,000 specs, up to 18 family and
null ensembles), the sprint-200 lanes, the C1 re-score composites, the POS-1 position books, the mlprob lanes and
the demo learning-class rows, with headroom.

The orchestrating session then asked the owner directly (interactive question, 2026-11-02):
"Approve 3,500 for 2026Q4 (with a hard ceiling of 5,000)? Every strategy still counts toward the total tried, so
this lowers no quality bar. With this many strategies tested, the run may honestly find zero survivors."
The owner's answer: "Approve 3,500 (Recommended)".

## Decision
- The 2026Q4 trial budget is 3,500 (`research.trial_budget_overrides` "2026Q4": 3500), backed by this record
  (`research.trial_budget_owner_decisions` "2026Q4").
- The 5,000 hard ceiling applies even with an owner decision; any other quarter above 500 needs its own owner ADR.
- Every trial counts in K whether it fires or not (pre-registration rows plus registry K at run time).

## Consequences
- Program K rises from about 3,335 to about 6,300. The deflated-Sharpe bar for the live-money gate rises with it, and
  zero survivors is a possible honest result of the sprint; that would not mean the gate is broken.
- Demo qualification uses BH-FDR at confirm (amendments C1 and W2), so the larger batch costs confirm power only
  through m, the number of explore passers.
- Reversal: a later owner decision may lower the quarter; trials already run stay counted.
