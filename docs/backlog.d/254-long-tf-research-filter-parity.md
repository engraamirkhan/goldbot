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
  Trading-safety notes (review of 6c90577c): when the hold origin or `weekend_hold` changes, re-check the calendar-time
  hold counting (`hold_bars` x the timeframe on the calendar, weekends included, in `crosses_friday`, the record and the
  swap projection). As built, a 1w arm is always refused `weekend_hold` (its hold reaches the Friday cut) unless the
  account has `demo_weekend_flat`.
  Quant amendment (ADR 0081 point 5): research fills at the next bar's open (`research/catalogue/sim.py`,
  `data/daily.py`): 1d ~21:00 UTC in winter (the reopen in summer) vs live 23:30 (22:30); 1w Friday 21-22 UTC holding
  the weekend gap vs live Monday 00:15 (Sunday 23:15); Friday 1d trades exist in research, never live. Acceptance adds:
  the CONFIRM of record re-fills at the first 1m open at or after the live entry close, Friday 1d dropped; no 1d/1w arm
  may qualify until that CONFIRM exists.
