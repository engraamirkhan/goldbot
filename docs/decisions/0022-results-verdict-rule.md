# 0022: Results verdict rule: day-clustered errors, a Bonferroni bar for breakdown rows

Status: accepted 2026-10-16 (quant reviewer; implementer's change to the review formula — CR1 and t on G−1 df — ruled
correct by the quant reviewer 2026-10-16, before any report was read under this rule). Amended the same day, before
merge and before any report was read: zero clustered se with dispersion is "no se" (quant re-review B1), and the
overall bar is max(2.0, t⁻¹_{G−1}(0.975)) (F4, adopted by the program director). Frozen from this date. Numbers 0019 (Wine), 0020 (MT5 installer) and 0021 (bridge-smoke gate split) are taken or reserved on other
lanes; this record was first committed as 0021 and renamed the same day before any code used it.

## Context
The attribution report (`goldbot/research/attribution.py`, TRACEABILITY G12) gives each cell (by timeframe, family,
agent, session, side, regime, decision and exit) a verdict: positive or negative when |t| >= 2. The quant review of
16 Oct found four faults:
- **No multiple-testing correction.** A report has dozens of breakdown cells. At |t| >= 2 a pure-noise report shows
  at least one "positive" or "negative" row most days. A strategy changed because of such a row is fitted to noise.
- **Trades on one day are not independent.** Several agents and timeframes trade the same move. The iid standard
  error counts them as separate evidence. Tripling a day's trades raised t by about sqrt(3).
- **The live-fill check compared the fills with themselves.** The measured slippage table is built from the same
  fills, so the excess tended to zero by construction.
- **A stale note** said live realised R is not recorded. It is: `ClosedTrade.r` in `closed_trades*.jsonl`.

The pre-registration needs this rule recorded before any report is read under it. This ADR is committed before the
code that implements it.

## Decision
**Results verdict: day-clustered SE; Bonferroni t_bar for breakdown rows; max(2.0, t⁻¹_{G−1}(0.975)) for the overall
champion-path cell; frozen from this date; changing a strategy because of a row is a new registry trial.**
(As first recorded the overall bar was a flat 2.0; F4 amended it the same day, before any report was read.)

1. **Standard error, clustered by entry UTC day** (`goldbot.data.timeutil.epoch_ns`; a live fill by its fill day).
   With G days, n trades and x̄ the cell mean:
   `se = sqrt(G / (G - 1)) * sqrt(Σ_d (Σ_{i∈d} (x_i − x̄))²) / n`.
   - `t_stat = mean / se`, and `ci95_net = mean ± 1.96 se` use this se.
   - A cell with fewer than 2 days has no se. Its verdicts are "indistinguishable from zero" (or "noise" below the
     minimum count). The one exception is zero dispersion (every trade the same R), where the sign is the evidence,
     as before.
   - A clustered se of exactly 0 with dispersion in the cell (every day's mean equal to the cell mean) is "no se":
     the cell is indistinguishable from zero. Zero dispersion (every trade the same R) keeps its sign.
2. **Row bar.** m = the number of breakdown cells with n >= `attribution.min_trades` in that report. The overall
   cell, the challengers and the live fills are not counted. The report's nominal bar is
   `t_bar = max(2.0, Φ⁻¹(1 − 0.025 / m))`: 2.81 for m = 10, 3.14 for m = 30, 3.29 for m = 50.
   - Each row applies the same tail probability on G − 1 degrees of freedom:
     `bar = max(2.0, t⁻¹_{G−1}(1 − 0.025 / m))`. For many days this is t_bar.
   - The report carries `t_bar` and `m`, and each cell carries the bar it applied (`Cell.t_bar`).
3. **Overall bar (F4).** The overall champion-path cell, the challengers and the live fills use
   `max(2.0, t⁻¹_{G−1}(0.975))`, G = the cell's days; a cell with fewer than 2 days uses 2.0. With 30 days this is
   2.045; from 62 days on it is 2.0. The live fills carry `t_bar` and `verdict_adj` too.
4. **Adjusted range and verdict.** Every cell carries `ci_adj = mean ± bar · se` and `verdict_adj`. `verdict_adj` is
   "positive" or "negative" if and only if `ci_adj` excludes 0. It is read from the stored (rounded) range, so the
   two can never disagree. A noise cell has no `ci_adj`.
   - The raw `verdict` (|t| >= 2 on the clustered t) stays as it is for the staff agents (`read_attribution`).
5. **Live fills** are compared with the backtest's prior slippage (`slippage_prior_usd`). This is the same comparator
   as the phase gate's fill-slippage item (`goldbot/ops/gates_phase.py` `_fill_slippage`). The account's cost table
   supplies the prior; without one, the canonical prior is used.
6. **A row is a lead, not a licence.** Changing a strategy because of a row is a new registry trial, with its own
   pre-registration and budget.

## Deviation from the review's formula (evidence)
The review gave `se = sqrt(Σ_d (Σ_{i∈d}(x_i − x̄))²) / n` and a normal bar. Its own acceptance test, 40 pure-noise
cells of N(0, 1) with n = 60, required a family-wise rate of at most 0.07. That formula misses the bar:
- **Theory.** The per-cell rate is 2·P(T₅₉ > 3.227·sqrt(59/60)) = 0.0022, so the family-wise rate is 0.085.
- **Simulation.** It came out at 0.072 over seeds 0–499.
- **Why.** Two effects add up. Without the G/(G − 1) factor the error is too small. The normal quantile also
  ignores the t tails at n = 60.

The rule above adds both standard small-sample fixes (CR1 and t on G − 1 degrees of freedom). Its theoretical rate is
1 − (1 − 0.00125)⁴⁰ = 0.049. With one trade per day it reduces to the classical t-test: `std(ddof=1) / sqrt(n)`, as
before this ADR.

**What would change this.** A quant-reviewer finding that the row bar should be the normal quantile in every cell.
That would be a superseding ADR, not an edit of this one.

## Consequences
- **Fewer, sturdier rows.** A typical report (m ≈ 30) needs |t| >= 3.14 on a row instead of 2. A real edge needs
  about 2.5 times as many trades to show as a row. The overall cell's bar rises above 2 only on few days.
- **Digest (quant ruling F3).** The Telegram digest names only rows whose `verdict_adj` is signed; otherwise it says
  no row clears the bar and gives the overall cell.
- **Same-day duplicates add no evidence.** Tripling every trade within its own day leaves t unchanged.
- **Single-day cells.** A cell whose trades all fall on one day is never positive or negative (except at zero
  dispersion).
- **Live fills.** The excess is now the cost the backtest did not charge, not zero by construction.
- **Not changed.** The gates and the stop rule (`goldbot/ops/gates_phase.py`) are unchanged. The stop rule's iid
  bound is an owner note only.
- **Consumers.** The director's attribution term (`goldbot/research/director.py`) recomputes its own t from the trade
  rows. The raw verdict's meaning (|t| >= 2) is unchanged for the staff agents.
