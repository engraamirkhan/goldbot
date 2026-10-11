---
number: 6
rank: 9
title: Live exit policies and blackout early close
stage: Trade management, exit · FIND + RUN
size: M
status: in review (safety fixes)
---

### 6. Live exit policies and blackout early close (in review)
Value: momentum edges are made in the exits. Labels cannot test exits the engine cannot run, and exits are never
gated, so they must work unattended (FIND + RUN).
Acceptance:
- The engine executes each family's `exit_policy`: trend trails at 1.5 ATR once 1.25 ATR in profit (M3); breakout
  closes half at 1.0 ATR and trails the rest at 1.0 ATR (M6); session-open goes flat 1 h before the next session
  (M7). Each has a test on the paper broker with a scripted price path.
- Shadow-book parity: the shadow book and the label generator produce identical outcomes for each policy on the
  same path (extends the existing `triple_barrier` parity test).
- R21: in a tier-1 blackout, open positions whose re-scored p < 0.5 are closed at market. Tested.
- No exit path consults the approval queue (test asserts it).

Rows: M3, M6, M7, R21.
