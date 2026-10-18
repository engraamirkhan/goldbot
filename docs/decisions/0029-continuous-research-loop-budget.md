# 0029: Continuous research loop budget (owner directive 2026-10-18)

Status: accepted (owner directive 2026-10-18, recorded by the program director)

## Context
The design capped research at `research.trial_budget_quarter` (20 trials a quarter). On 2026-10-18 the owner rejected
waiting for the quarterly schedule, in their words: "it should be able to backtest as much as it needs now in parallel
for strategies" and "back test strategies on multiple time frames in parallel, finding new strategies, back testing,
keeping the ones that work and continue doing it indefinitely and adding to system as a strategy is proven to be
profitable". The quant review of the research loop (phase 1) ruled that a loop budget beyond the quarterly figure needs
this decision on record.

## Decision
- Hand-dispatched trials keep the quarterly budget, with per-quarter overrides (`research.trial_budget_overrides`;
  2026Q4 = 53 for docs/research/batch-2026-10-18.md).
- The continuous loop (docs/plans/continuous-research-loop.md) has its own budget, `research.loop_trials_per_run`
  (16) per scheduled run, every 6 hours, outside the quarterly budget.
- Every trial, loop or hand-dispatched, is recorded in the trial registry. Gates use the raw running K (every trial
  ever run); a clustered K_eff is reported for information only. Freezing K at decision time is NOT adopted (it would
  change a gate; owner decision if ever wanted).
- The held-out year is never scored by the search; a survivor scores it once, at promotion.
- No trial reaches money except through the existing shadow and promotion gates; the owner confirms every entry.

## Consequences
- Many more trials: the deflated-Sharpe bar rises with K (about t 2.9 at K=20 to t 3.9+ at K in the hundreds), so
  only strong, high-trade-count strategies survive. That is the price of searching widely, paid honestly.
- Actions minutes are free for the public repo; the loop is limited by runner time, not money.
- Any change to this budget, or to how K counts, is an owner decision.
