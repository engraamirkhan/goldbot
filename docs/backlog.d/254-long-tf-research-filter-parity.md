---
number: 254
rank: 82
title: '1d/1w live == history: the common filter at the entry close, the hold from the decision close (ADR 0081)'
stage: Research · BUILD
size: S
status: 'ready (2026-11-13) · accountable: implementer · reviews: quant-reviewer, trading-safety-reviewer'
---
- **254. 1d/1w live == history: the common filter at the entry close, the hold from the decision close (ADR 0081).**
  Value: two gaps between the engine and a history run of a 1d/1w arm remain. (a) The engine judges the common filter
  at the entry (`RuleContext.filter_close = long_bars.entry_after(...)`); any research path that replays 1d/1w arms
  through `arm_rules` (shadow replay, run_arm_backtest, CONFIRM) must pass the same. (b) The live hold runs from the
  send close (up to 2.5 h after the 1d decision close, up to a day for a 1w catch-up), history's from the decision
  close; a cut-following rule whose hold is capped at the week's end (DOND) is therefore refused `weekend_hold` live
  (fail closed, `tests/test_exploration_engine.py`). Acceptance: every research caller passes `filter_close` for
  1d/1w bars; the time exit of a 1d/1w position counts from its decision close (order id, `_week_due`, the barrier and
  `crosses_friday` alike) with trading-safety review; a live == history test on a 1d and a 1w arm.
