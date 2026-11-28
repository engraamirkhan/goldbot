# Pre-registration: H-106 (U2), G-cw on the pooled body (gold + FX10), learn 2010-17, fresh confirm 2018-25

Research director, 2026-11-28. Owner approval of 2026-11-28 for the fresh routes of H-104 §10 (PR #521; evaluator
verdict 2026-11-28). This card is route **U2**: "the pooled body (gold + FX10 + XAG, after D-12-xt with a measured FX
cost). U2 is a new card (new population)."

**Revision 2 (2026-11-28)** applies the quant review of PR #523 (CHANGES, text only, items 1-11). Where the quant gave
text, it is quoted. Predictions are unchanged in kind. Registering only 15m, 30m and 1h lowers m from 6 to 3, which
raises the power figures. K stays 0.

**Label-blind.** No pooled label, score or outcome exists. Inputs:
- the served tiers file `raw/tiers/tiers_served_r11-v2.1.csv` (sha256 ff653ea7..., grades only);
- `dense_labels.input_lookback` and the sr-v3 2.2 per-day n_eff rates;
- `sr_v3.POOLED_MEMBERS`;
- the cost record file and the D-12 measurement JSON (its binding table, public);
- the brain registry snapshot of 2026-11-24.

**Status: proposed, revision 2.** Quant-reviewer and evaluator sign. **0 K now.** Real money, sizing and live unlock are
owner-only.

## 0. Money first

- **Gold loses its cost on dense candidates** (H-104 §0, seen): mean cost(x) is 0.95 R at 15m, 0.36 R at 30m and 0.25 R
  at 1h. A filter needs ρ of 0.55, 0.29 and 0.23 there for S = +0.05 R. The largest measured price IC is 0.05.
- **The pooled body does not change gold's cost.** It adds FX rows to learn from, and FX confirm days that are graded.
  Gold has 149 graded confirm days, all in 2018.
- **How the FX confirm days are graded** (quant, verbatim): "FX 2018 is A; FX 2019-2025-09 is A-consistent (same-source,
  no independent reference) with 8-18 C months per pair (2023: 62 of 120 pair-months C)."
  - Re-derived from the tiers file: 2023 has 62 C, 57 A-consistent and 1 B of 120 pair-months.
  - On my count, 2019-01..2025-09 holds 7-17 C months per pair.
- **So a pooled pass is mostly FX evidence, and mostly A-consistent evidence.** For gold money, it licenses
  learning-class demo gating of gold candidates only through the gold-subset check in section 5.
- **FX costs are priors.** In `srv3-cost-2b3628372a9b3bfa` they are `basis: prior`, and `srv3_cost_record.apply` keeps
  the D-3 block on every prior row. No pooled gate read can happen until they are measured (section 3).

## 1. Cells and Holm m (fixed now)

**Cells: G-cw on the pooled body at 15m, 30m and 1h. That is 3 cells.**
- **Holm m = 3** at Stage L (α 0.10, z 1.83) and **m = 3** as U2's contribution to its confirm batch (ADR 0085
  year-1 m_j minimum 3, z 2.87).
- A cell that is NOT STARTED or ABSTAIN enters with p = 1. m never moves.

**2h, 4h and 1d+1w are not cells.** They are recorded as certain refusals, label-free:

| Unit | Confirm n_eff | Confirm power |
|---|---|---|
| 2h | 904 | 0.36 at z 2.87 |
| 4h | 290 | 0.07 |
| 1d+1w | 39 | below the 200 floor |

**Why G-cw only.**
- The recipe is H-104 §1: LightGBM depth 2, 400 trees, lr 0.01, monotone in range-to-cost.
- Inputs are P30 + 2 D-40 + range_to_cost = 33. The keep is obj-v1, p_w ≥ 0.5, with the same seeds by manifest key.
- It is fitted on the pooled rows with instrument levels under the sr-v3 2.3 tie rule.
- HB and AVG are not registered. Their mechanism is variance reduction only (H-104 priors 2 % each, ≤ G-cw), and they
  would triple m.

**Also outside this card:**
- L-cw on the pooled body is sr-v3's own unit (card 2.3, 4 step 1). It is not booked again here.
- SEQ-1, CAS, RL-X1, agg-v1, index, energy, crosses and 1m/5m are out of scope.

## 2. Population and windows

**Members.** "XAGUSD is not a member of this card. k = 11." (quant, verbatim)
- XAG has no P30-usable row in any window, so k = 11: XAUUSD (Dukascopy) and the FX10 pairs (HistData).
- A member enters only if its class has a **measured** cost in a recorded version on the Stage 0 day (section 3).
- Membership is fixed at Stage 0 by that rule and recorded, label-blind.

**Rows.** P30-usable rows (G-cw is P30-wide; rev 9 D1; evaluator finding 1), T2 head, both sides, xt-v1 exits.

**Learn.** 2010-01..2017-12, OOF 2012-17, expanding yearly folds, full-horizon purge, gap max(1 d, max H). It is a
screen (N26: 2012-17 is seen for gold).

**Fresh route (counted below): confirm 2018-01..2025-09.** Rows count only if their horizon ends before 2025-10-01
(C6 edge rule).
- FX is A in 2018 and A-consistent in 2019-2025-09.
- Gold has graded rows only in 2018.
- "Every read is also reported on A-only rows (ADR 0085 A4); that report is descriptive, unpowered (15m n_eff about 460)
  and never gating. Gate 12 is met only on A-consistent years, and the read says so." (quant, verbatim)

**test_old and holdout.** test_old 2003-09 (where tiered) is read once, passers only. The holdout is sealed.

**Label-free counts.**
- A decision is P30-usable if two conditions hold:
  - every month in [d − strict lookback, d + H] is A or A-consistent;
  - every month in [d − close-only lookback, d] is A, A-consistent or B.

  The lookbacks are `input_lookback` of P30, split by `close_only`, and days are weighted to the tiers file's `days`.
- n_eff = Σ usable days × sr-v3 2.2 rate / (1 + (k − 1)·ρ_inst).
- "ρ_inst used at Stage 0 = max(0.5, the D-12-xt measured value); power is also reported at 0.7." (quant, verbatim)

| Unit | Learn OOF 2012-17 n_eff (gold alone) | Confirm 2018-25 n_eff (gold alone) | Stage L power z 1.83 (ρ 0.5 / 0.7) | Confirm power z 2.87 (ρ 0.5 / 0.7) |
|---|---|---|---|---|
| 15m | 3,511 (1,182) | 3,573 (210) | 1.00 / 0.99 | 0.98 / 0.93 |
| 30m | 2,324 (782) | 2,365 (139) | 0.99 / 0.95 | 0.88 / 0.74 |
| 1h | 1,790 (601) | 1,818 (106) | 0.95 / 0.89 | 0.75 / 0.58 (on the edge) |

**Check of the method.** Gold P30 learn-OOF usable days come out 603 / 603 / 600 at 15m / 30m / 1h. The realised
counts from the `oof.parquet` `p30_ok` column are 614 / 614 / 611 (re-derived), so the method is within 2 %.

## 3. Preconditions (S0-1), each with owner and date

| Item | What | Owner | Due |
|---|---|---|---|
| P1 | xt-v1 dense labels for FX10 (`~/tmp/dense-labels`, exit freeze b68e2f7b...), produced sealed; no read | main session (data-engineer signs the manifest) | 2026-12-02 |
| P2 | **D-12-xt (backlog 279):** `scripts/research/measure_rho.py` writes `exit_freeze_sha` = `dense_labels.EXIT_FREEZE_SHA256` (today it does not, so `config.xt_v1_rho_refusal` refuses every pooled power read), re-runs at xt-v1 geometry, and the result is pinned; quant signs | data-engineer; quant-reviewer | 2026-12-04 |
| P3 | **Measured FX10 cost of record (backlog 280)**, recorded on an ADR 0098 R-H line | microstructure-researcher; quant countersigns | 2026-12-11 |
| P4 | Backlog 277 (seal guard on `oof.parquet` columns, H-104 F8) merged | implementer | with #521 |

The 2027Q1 budget is **not** an S0-1 item. Worst case 6 K fits the 12 open in code, and `check_budget` covers it
(quant item 11).

**What a measured FX cost needs (P3).** The same rule as gold's record, per instrument:
- an ECR (realised fill-to-bar cost) per instrument × broker × session from demo fills, at ub95, with at least
  `ecr_min_fills` 30 per cell. A sparse cell takes the session `all` cell.
- an era term from Dukascopy **bid + ask** quoted spreads per month (HistData FX is bid-only), plus each broker's FX
  commission and slip_x;
- a floor at W2, with the worse broker charged;
- swap (quant, verbatim): "FX/XAG swap per side per rollover from the broker's symbol swap spec captured point in time,
  triple on the broker's triple day, charged on every row whose horizon crosses a rollover."
- Fill cut-off, Stage 0 date and thin instruments (quant, verbatim): "Fills dated after 2026-12-11 23:59 UTC are not
  counted. Stage 0 runs once, on the first day P1-P5 hold or on 2027-01-31, whichever is first, and is never re-run. An
  instrument with fewer than 30 fills at either broker in every session stays `prior` and leaves the body; the measured
  broker is never charged alone." P5 is withdrawn, so this reads "P1-P4" here.

**Membership is label-blind.** It follows fill counts, never outcomes.

**How priors are handled for a gate read: never.**
- A prior-basis row keeps the D-3 block (`srv3_cost_record.apply`).
- A prior-costed fit may run fit-only at 0 K with sealed scores. Nothing is read from it.
- "The 'at least two non-gold members' rule counts only members with at least one P30-usable confirm row." (quant,
  verbatim) With fewer than two such measured members by the kill date, U2 is NOT STARTED (cost).

## 4. Hypothesis (rule 2 card)

| Id | Hypothesis | Mechanism | Expected gross per kept trade | Cost per candidate | Expected net £/day at 0.01 lot | Prior P(confirm pass, ≥ 1 cell) |
|---|---|---|---|---|---|---|
| H-106 | G-cw on 11 instruments finds states where expected gross beats cost(x) | More independent days and instruments lower coefficient variance; FX costs per R are expected to be far below gold's 15m-1h cost, so cost avoidance has room | unknown until P3: at ρ 0.05 and FX cost c, S ≈ 0.05·σ·φ(Φ⁻¹(1−k)) − k·c | gold 0.95 / 0.36 / 0.25 R (15m / 30m / 1h); FX: P3 | gold ≈ £0 (cost as H-104); FX only if FX arms trade on demo: ≈ £1-3/day if S = +0.05 R | 4 % |

**S0-2 is binding here (rule 2).**
- It was written before any pooled outcome existed.
- A cell starts only if two conditions hold: μ = −(pooled mean cost(x) under P3, Stage 0 membership), and the ρ needed
  for S = +0.05 R at σ 1.5 is ≤ 0.17.
- "S0-2 can only refuse. The 0.17 bound is inherited verbatim from H-104 F1, not tuned here, and pooled μ uses the
  Stage 0 membership." (quant, verbatim)
- Gold-only rows are reported beside it.

## 5. Gates and reading rule (fixed now; sr-v3 / H-104 reused)

**Stage 0 per cell.** The evaluator records every failing item and cites S0-3 first.
- S0-1: section 3.
- S0-2: section 4, binding.
- S0-3 power (ADR 0085): Stage L at z 1.83 (m 3). The confirm route needs n_eff ≥ 200 and power ≥ 0.5 at z 2.87,
  computed at ρ_inst = max(0.5, measured). Joint power ≥ 0.25. S0-3 confirm power is computed at z for m_j = max(3,
  members already recorded for the target batch on the Stage 0 day + this card's cells passing S0-1/S0-2), α_j of
  that batch's ledger year; the z 2.87 figures in section 2 are the m_j 3 lower bound.
- S0-4 ABSTAIN: fewer than 100 kept OOF rows or 30 kept days, from a column-projected count.
- S0-5 leak suite: sr-v3 N25 and N6.

**Stage L (screen).** H-104 §4 gates 1-7, 9, 10 and 11 on P30-usable pooled rows, Holm at m 3 (α 0.10). Seed stability
and the ±10 % parameter grid follow H-104 gates 9-10.

**Confirm (read once): the N26 batched confirm by default.**
- U2 joins the batch of **2027-01-08 12:00 UTC** if its Stage L CONTINUE is recorded by 2027-01-06 23:59 UTC.
- Otherwise it joins the next N26 batch, whose date the evaluator sets before U2's Stage L read (sr-v3 C3).
- U2 gets its own ledger slot only by an evaluator ruling.
- U2 contributes a fixed m of 3. The batch's Holm count is the evaluator's. The powers in section 2 are quoted at z 2.87
  (m_j 3) and are upper bounds if the batch has more members.
- Gates: sr-v3 5.3 gates 1-8 and 11; gate 12 (met only on A-consistent years, stated); gate 13 reported; gate 14 at
  15m.

**Gold subset (gold gating only).**
- On gold confirm rows, point S must be ≥ 0 and gross skill ≥ 0, reported with its CI and n (sr-v3 gate 9 form).
- If it fails, a pooled pass gates no gold candidate.
- "The gold subset is a veto, not evidence: it passes about half the time with no gold edge." (quant, verbatim)

**Quote basis** (quant, verbatim): "Every read reports S by side × quote_basis, and a sensitivity charging an extra half
as-of era spread on bid-only short rows. The spread is charged once, in cost(x)."

**Statistics and reporting.**
- Stationary day-block bootstrap, resampling session days with all instruments of a day together (BOOT 10,000, mean
  block 5, seed 20261206).
- Matched control: B7 by day, side and hour, per instrument.
- Every read reports S by year and by instrument, the A-only descriptive read, the S split, £/day at 0.01 lot, and the
  losers next to the winners.

## 6. Predicted Stage 0 (label-free; the evaluator's run is the record)

| Cell | S0-1 today | S0-2 | **S0-3** | Predicted |
|---|---|---|---|---|
| G-cw 15m | fail (P1-P4) | gold rows fail (0.55); pooled not computable until P3 | pass (L 1.00, confirm 3,573 / 0.98; 0.93 at ρ 0.7) | NOT STARTED today. It starts only if P1-P4 land and S0-2 passes on measured FX cost |
| G-cw 30m | fail | gold fail (0.29); pooled: P3 | pass (0.99, 2,365 / 0.88; 0.74 at ρ 0.7) | as 15m |
| G-cw 1h | fail | gold fail (0.23); pooled: P3 | pass (0.95, 1,818 / 0.75; 0.58 at ρ 0.7, on the edge) | as 15m |

**Predicted: 0 of 3 start today. If P1-P4 land, at most 3, and only where measured FX cost passes S0-2.**

## 7. K, budget, kill rules, dates

**K.** 0 now. Worst case 6 (3 Stage L + 3 confirm).

**Budget (code-backed).**
- Stage L books in 2026Q4 if it is read by 2026-12-31. There are 80 free on the stale 2026-11-24 brain snapshot; the
  ops-VM registry is the count of record.
- Otherwise Stage L books in 2027Q1: `trial_budget_quarter` 20, reserved 8, so 12 open on main.
- The confirm books in the quarter of its batch.
- `check_budget` runs before each booking. A cell refused for budget is NOT STARTED at 0 K, in this order: 1h, 30m, 15m.
  This card never raises the budget.

**Kill rules (quant F5 form).**
- Stage 0 runs once (section 3). If P1-P4 are not met by **2027-01-31**, every cell is NOT STARTED and U2 closes
  **UNTESTABLE (cost/data)**. Not a NULL.
- A cell NOT STARTED at Stage 0 (power or S0-2) is closed as UNTESTABLE at that unit.
- A Stage L NULL closes the cell: no re-run, widening or re-parameterisation.
- A confirm fail closes the cell. test_old is read once, passers only.

**Registry rows.** One row per started cell (`u2-gcw-pooled:<unit>`, rule_ref this file), written after `check_budget`
and before any statistic.

| Date | Step | Owner |
|---|---|---|
| 2026-12-02 / 12-04 / 12-11 | P1 / P2 / P3 | main session / data-engineer / microstructure-researcher |
| first day P1-P4 hold, or 2027-01-31 | Stage 0 recorded once | evaluator |
| within 7 days of Stage 0 | Stage L reads of started cells | ml-researcher (fit), evaluator (read) |
| 2027-01-06 23:59 / 2027-01-08 12:00 UTC, else the next N26 batch | Confirm members / batched confirm | evaluator |
| 2027-01-31 | Kill date for P1-P4 | research director |

## 8. Integrity

- Pre-registered before any pooled label value, score or outcome exists. At Stage 0 the evaluator checks that the dl-v1
  consumption ledger holds no FX10 xt-v1 read.
- **Earlier reads of FX10 2018-01..2025-09: none found.** The query was run on 2026-11-28 against the brain registry
  snapshot of 2026-11-24 (stale, 6,990 rows). It looked for rows with status screened, evaluated or verdict that name an
  FX10 symbol and carry any 2018-2025 date. It returned 0 rows.
  - `docs/research/cross-asset-screen-2026-11.md` states that its window was 2010-2017 only.
  - The evaluator re-runs the query on the ops-VM registry at Stage 0.
- "sr-v3's pooled confirm (2027-01-08) reads these rows first; U2's members, dates and every parameter are fixed by this
  card and by a mechanical membership rule before then." (quant, verbatim)
- Holdout sealed. Confirm and test_old are counted outcome-blind, never read, at Stage 0.
- K is counted per started cell, with deflation at the registry K of the read day, reported. Losers are reported beside
  winners.

## Sign-off

- Research director: 2026-11-28 (rev 1); rev 2 the same day (quant items 1-11 on #523).
- Quant-reviewer: rev 1 CHANGES, applied in rev 2; re-verify pending.
- Evaluator: pending (own-slot ruling, if any).
