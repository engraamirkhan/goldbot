# Pre-registration: H-106 (U2), G-cw on the pooled body (gold + FX10 + XAG), learn 2010-17, fresh confirm 2018-25

Research director, 2026-11-28. Owner approval of 2026-11-28 for the fresh routes of H-104 §10 (PR #521; evaluator
verdict 2026-11-28). This card is route **U2**: "the pooled body (gold + FX10 + XAG, after D-12-xt with a measured FX
cost). U2 is a new card (new population)."

**Label-blind.** No pooled label, score or outcome exists. Inputs: the served tiers file
`raw/tiers/tiers_served_r11-v2.1.csv` (sha256 ff653ea7..., grades only), `dense_labels.input_lookback`, the sr-v3 2.2
per-day n_eff rates, `sr_v3.POOLED_MEMBERS`, the cost record file and the D-12 measurement JSON (its binding table,
public).

**Status: proposed.** Quant-reviewer and evaluator sign. **0 K now.** Real money, sizing and live unlock are
owner-only.

## 0. Money first

- Gold on dense candidates loses its cost (H-104 §0, seen): mean cost(x) 0.95 R at 15m, 0.36 R at 30m, 0.25 R at 1h.
  The ρ a filter needs for S = +0.05 R is 0.55 / 0.29 / 0.23 there, against a largest measured price IC of 0.05.
- The pooled body does not change gold's cost. It adds FX rows to learn from and, above all, **FX confirm days that are
  graded A** (gold has 149 graded confirm days, 2018 only). So a pooled pass is mostly FX evidence. For gold money it
  licenses learning-class demo gating of gold candidates only through the gold-subset check in section 5.
- FX and XAG costs are **priors** in `srv3-cost-2b3628372a9b3bfa` (`basis: prior`), and `srv3_cost_record.apply` keeps
  the D-3 block on every prior row. No pooled gate read can happen until they are measured (section 3).

## 1. Cells and Holm m (fixed now)

| Unit | 15m | 30m | 1h | 2h | 4h | 1d+1w |
|---|---|---|---|---|---|---|
| G-cw on the pooled body | 1 | 1 | 1 | 1 | 1 | 1 |

- **6 cells. Holm m = 6 at Stage L (α 0.10, z 2.13) and m = 6 in U2's confirm batch** (ADR 0085 ledger, z 3.08 in ledger
  year 1). A cell NOT STARTED or ABSTAIN enters with p = 1; m never moves.
- **G-cw only.** Its recipe is H-104 §1 (LightGBM depth 2, 400 trees, lr 0.01, monotone in range-to-cost, P30 + 2 D-40
  + range_to_cost = 33 inputs, obj-v1 keep p_w ≥ 0.5, the same seeds by manifest key), fitted on the pooled rows with
  instrument levels under the sr-v3 2.3 tie rule. HB and AVG are not registered: their mechanism is variance reduction
  only (H-104 priors 2 % each, ≤ G-cw), and each would raise m from 6 to 12 or 18 and push the 1h confirm below power.
- **L-cw on the pooled body is sr-v3's own unit** (card 2.3 tie rule, 4 step 1). It is not re-booked here. Its K and its
  confirm batch stay sr-v3's.
- Out of scope: SEQ-1, CAS, RL-X1, agg-v1, index, energy, crosses, 1m/5m.

## 2. Population and windows

- **Members:** `POOLED_MEMBERS` = XAUUSD (Dukascopy), AUDJPY, AUDUSD, EURJPY, EURUSD, GBPJPY, GBPUSD, NZDUSD, USDCAD,
  USDCHF, USDJPY (HistData), XAGUSD. A member enters only if its class has a **measured** cost in a recorded version on
  the Stage 0 day (section 3). The membership is fixed at Stage 0 by that rule and recorded, label-blind.
- **Rows:** P30-usable rows (G-cw is P30-wide; rev 9 D1; evaluator finding 1), T2 head, both sides, xt-v1 exits.
- **Learn:** 2010-01..2017-12, OOF 2012-17 by expanding yearly folds, full-horizon purge, gap max(1 d, max H). It is a
  screen (N26: 2012-17 is seen for gold).
- **Fresh route (counted below): confirm 2018-01..2025-09**, rows whose horizon ends before 2025-10-01 (C6 edge rule).
  FX is graded A/A-consistent across it; gold only in 2018 under r11-v2.1; XAG has no P30-usable row in any window.
- test_old 2003-09 (where tiered) is read once, passers only. The holdout is sealed.

**Label-free counts (method: for each member, month and weekday, a decision is P30-usable iff every month in
[d − strict lookback, d + H] is A/A-consistent and every month in [d − close-only lookback, d] is A, A-consistent or B,
`input_lookback` of P30 split by `close_only`; days weighted to the tiers file's `days` per month; n_eff = Σ usable days ×
sr-v3 2.2 rate / (1 + (k − 1)·0.5), ρ_inst prior 0.5 because the pooled ρ_inst is not measured):**

| Unit | Learn OOF 2012-17 n_eff (gold alone) | Confirm 2018-25 n_eff (gold alone) | k | Stage L power (z 2.13) | Confirm power (z 3.08) |
|---|---|---|---|---|---|
| 15m | 3,511 (1,182) | 3,573 (210) | 11 | 1.00 | 0.97 |
| 30m | 2,324 (782) | 2,365 (139) | 11 | 0.97 | 0.84 |
| 1h | 1,790 (601) | 1,818 (106) | 11 | 0.92 | 0.68 |
| 2h | 893 (299) | 904 (52) | 11 | 0.64 | 0.28 |
| 4h | 308 (84) | 290 (21) | 11 | 0.25 | 0.05 |
| 1d+1w | 44 (0) | 39 (0) | 5 / 3 | 0.06 | 0.01 |

The 1d+1w figures are sums of 1d and 1w (an upper bound; ADR 0085 Q3 computes uniqueness across tfs). Check of the
method: gold P30 learn-OOF usable days come out 603 / 603 / 600 / 597 / 419 / 0, against the realised 614 / 614 / 611 /
605 / 421 / 0 from the `oof.parquet` `p30_ok` column (re-derived for this card), within 2 %.

## 3. Preconditions (S0-1), each with owner and date

| Item | What | Owner | Due |
|---|---|---|---|
| P1 | xt-v1 dense labels for FX10 and XAG (`~/tmp/dense-labels`, exit freeze b68e2f7b...), produced sealed; no read | main session (data-engineer signs the manifest) | 2026-12-02 |
| P2 | **D-12-xt (backlog 279):** `scripts/research/measure_rho.py` writes `exit_freeze_sha` = `dense_labels.EXIT_FREEZE_SHA256` into its JSON (today it does not: `config.xt_v1_rho_refusal` refuses every pooled power read), re-run at xt-v1 geometry, pinned; quant signs | data-engineer; quant-reviewer | 2026-12-04 |
| P3 | **Measured FX10/XAG cost of record (backlog 280)** (below) recorded on an ADR 0098 R-H line | microstructure-researcher; quant countersigns | 2026-12-11 |
| P4 | Backlog 277 (seal guard on `oof.parquet` columns, H-104 F8) merged | implementer | with #521 |
| P5 | 2027Q1 budget in code: ADR 0099's `trial_budget_overrides "2027Q1": 250`, reservation withdrawn (not on main today, evaluator finding 3) | release-manager | 2026-12-05 |

**What a measured FX/XAG cost needs (P3).** The same rule as gold's record, per instrument:
- an ECR (realised fill-to-bar cost) per instrument × broker × session from demo fills, ub95, at least `ecr_min_fills`
  30 per cell (a sparse cell takes the session `all` cell);
- an era term from Dukascopy **bid + ask** quoted spreads per month (HistData FX is bid-only and has no spread), plus each
  broker's FX commission, plus slip_x;
- floored at W2, the worse broker charged, swap from the swap table.

A class without enough fills stays `prior`, and its member leaves the body before Stage 0. That rule is label-blind:
membership follows fill counts, never outcomes.

**How priors are handled for a gate read: never.** A prior-basis row keeps the D-3 block (`srv3_cost_record.apply`). A
prior-costed fit may run fit-only at 0 K with sealed scores. Nothing is read from it. If fewer than two non-gold members
are measured by the kill date, U2 is NOT STARTED (cost).

## 4. Hypothesis (rule 2 card)

| Id | Hypothesis | Mechanism | Expected gross per kept trade | Cost per candidate | Expected net £/day at 0.01 lot | Prior P(confirm pass, ≥ 1 cell) |
|---|---|---|---|---|---|---|
| H-106 | G-cw on 11-12 instruments finds states where expected gross beats cost(x) | More independent days and instruments lower coefficient variance; FX costs per R are expected to be far below gold's 15m-1h cost, so cost avoidance has room | unknown until P3: at ρ 0.05 and FX cost c, S ≈ 0.05·σ·φ(Φ⁻¹(1−k)) − k·c | gold 0.95 / 0.36 / 0.25 R (15m / 30m / 1h); FX: P3 | gold ≈ £0 (cost as H-104); FX only if FX arms trade on demo: ≈ £1-3/day if S = +0.05 R | 4 % |

**S0-2 is binding here** (rule 2). It is written before any pooled outcome exists, so H-104 F1's objection (S0-2 written
after the gold L-cw/B8 read) does not apply to the pooled body: a cell starts only if the ρ needed for S = +0.05 R at
σ 1.5 and μ = −(pooled mean cost(x) under P3) is ≤ 0.17. Gold-only rows are reported beside it.

## 5. Gates and reading rule (fixed now; sr-v3 / H-104 reused)

- **Stage 0 per cell:** S0-1 (section 3), S0-2 (section 4, binding), S0-3 power (ADR 0085: Stage L z 2.13 at m 6; the
  confirm route n_eff ≥ 200 with power ≥ 0.5 at z 3.08 (ledger year 1, m 6); joint ≥ 0.25), S0-4 ABSTAIN (< 100 kept OOF
  rows or < 30 kept days, column-projected count), S0-5 leak suite (sr-v3 N25, N6). The evaluator records every failing
  item; S0-3 is cited first.
- **Stage L (screen):** H-104 §4 gates 1-7, 9, 10, 11 on P30-usable pooled rows, Holm at m 6 (α 0.10). Seed stability
  and the ±10 % parameter grid as H-104 gates 9-10.
- **Confirm (U2's own batch on the ADR 0085 ledger, read once):** **2027-02-12 12:00 UTC**, members and shas recorded by
  2027-02-10 23:59 UTC. If Stage 0 has not run by 2027-01-22, the date is **2027-03-12 12:00 UTC** (members by
  2027-03-10). Both dates are fixed now, before any Stage L read, and never move after one. Gates: sr-v3 5.3 gates 1-8,
  11, 12 (≥ 3 calendar years with positive kept net, ≥ 10 kept trades each, at least one in 2021 or 2022; meetable on FX),
  13 reported, 14 at 15m. Holm m 6 within the batch.
- **Gold subset (gold gating only):** on gold confirm rows, point S ≥ 0 and gross skill ≥ 0, reported with its CI and
  n (sr-v3 gate 9 form). If it fails, a pooled pass gates no gold candidate.
- Stationary day-block bootstrap resampling session days with all instruments of a day together (BOOT 10,000, mean
  block 5, seed 20261206). Matched control B7 by day, side and hour per instrument. Every read reports S by year and by
  instrument, the S split, £/day at 0.01 lot and the losers next to the winners.

## 6. Predicted Stage 0 (label-free; the evaluator's run is the record)

| Cell | S0-1 today | S0-2 | **S0-3** | Predicted |
|---|---|---|---|---|
| G-cw 15m | fail (P1-P5) | gold rows fail (0.55); pooled not computable until P3 | pass (L 1.00, confirm 3,573 / 0.97) | NOT STARTED today; starts only if P1-P5 land and S0-2 passes on measured FX cost |
| G-cw 30m | fail | gold fail (0.29); pooled: P3 | pass (0.97, 2,365 / 0.84) | as 15m |
| G-cw 1h | fail | gold fail (0.23); pooled: P3 | pass (0.92, 1,818 / 0.68) | as 15m |
| G-cw 2h | fail | gold fail (0.20) | **fail:** confirm power 0.28 | NOT STARTED (power) |
| G-cw 4h | fail | gold pass (0.169) | **fail:** L 0.25, confirm 290 / 0.05 | NOT STARTED (power) |
| G-cw 1d+1w | fail | gold pass (0.12) | **fail:** n_eff 44 / 39 < 200 | NOT STARTED (power) |

**Predicted: 0 of 6 start today. If P1-P5 land, at most 3 (15m, 30m, 1h), and only where measured FX cost passes S0-2.**
These counts use the ρ_inst prior 0.5. The D-12-xt measured value replaces it at Stage 0 (P2) and can move them either
way.

## 7. K, budget, kill rules, dates

- **K:** 0 now. Worst case 12 (6 Stage L + 6 confirm). Expected after P1-P5: ≤ 3 Stage L + ≤ 3 confirm.
- **Budget (code-backed):** Stage L in 2026Q4 if read by 2026-12-31 (80 free on the stale 2026-11-24 brain snapshot;
  the ops-VM registry is the count of record), else 2027Q1 (`trial_budget_quarter` 20, reserved 8: 12 open on main
  until P5 lands). The confirm books in 2027Q1. Booking order if the budget binds: 1h, 30m, 15m (cheapest cost per
  candidate first). A cell refused for budget is NOT STARTED at 0 K. The budget is never raised by this card.
- **Kill rules (quant F5 form):**
  - P1-P5 not met by **2027-01-31**: every cell NOT STARTED, U2 closes **UNTESTABLE (cost/data)**. Not a NULL.
  - A cell NOT STARTED at Stage 0 (power or S0-2) is closed UNTESTABLE at that unit.
  - A Stage L NULL closes the cell: no re-run, widening or re-parameterisation.
  - A confirm fail closes the cell. test_old is read once, passers only.
- Rows: one registry row per started cell (`u2-gcw-pooled:<unit>`, rule_ref this file), written after `check_budget`
  and before any statistic.

| Date | Step | Owner |
|---|---|---|
| 2026-12-02 / 12-04 / 12-11 / 12-05 | P1 / P2 / P3 / P5 | main session / data-engineer / microstructure-researcher / release-manager |
| 2026-12-14 | Stage 0 recorded | evaluator |
| 2026-12-21 | Stage L reads of started cells | ml-researcher (fit), evaluator (read) |
| 2027-02-10 23:59 / 2027-02-12 12:00 UTC | Confirm members / batched confirm (fallback 03-10 / 03-12) | evaluator |
| 2027-01-31 | Kill date for P1-P5 | research director |

## 8. Integrity

- Pre-registered before any pooled label value, score or outcome exists. The evaluator checks at Stage 0 that the dl-v1
  consumption ledger holds no FX10/XAG xt-v1 read.
- Holdout sealed. Confirm and test_old are counted (outcome-blind), never read, at Stage 0.
- K counted per started cell; deflation at the registry K of the read day, reported. Losers reported beside winners.

## Sign-off

- Research director: 2026-11-28.
- Quant-reviewer: pending (S0-2 binding, ρ_inst, cost P3).
- Evaluator: pending.
