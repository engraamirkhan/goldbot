# Pre-registration: H-104, the sealed and unfitted learners on gold history (spec `zoo-v2`)

Research director, 2026-11-28. Owner directive of the same day: the models must reach a readable, judged state as
soon as possible. This card covers the learners that are sealed or unfitted:
- **(a) zoo-v2 tabular:** G-cw (with the D-40 dense prior as its inputs), HB, AVG;
- **(b) the SEQ-1 label head** (`seq1`, PR #489; quant ruling R-c says it needs its own card);
- **(c) RL-X1 fitted-Q exits and agg-v1 AdaHedge** (PR #489, ADR 0107).

**Label-blind.** It was written without opening any sealed OOF score (`oof_scores.parquet`, or the `v_`/`k_`
columns of `oof.parquet`) and without any new outcome statistic.

**Seen and disclosed.** The following were seen before writing:
- the sr-v3 learn read of 2026-11-28 for L-cw and B8 (take-all S per unit; L-cw AUC about 0.50; L-cw and B8 keep 0
  at 15m-4h);
- that read's cost-decile table for L-cw = B8. B8 is intercept-only at every unit, so its deciles rank by cost(x).

**Re-derived for this card:**
- the take-all mean net R and the mean decision-time cost(x) per unit, from `oof.parquet`, reading only the
  `net_read` and `cost` columns;
- the column names (schema only) of the sealed score files.

**Status: proposed.** The quant-reviewer and the evaluator sign before Stage 0 is recorded. This card books **0 K**.
Real money, sizing and live unlock stay owner-only. Nothing here touches the engine, the RiskGate or any account.

## 0. Money first: what the history already says (numbers before any idea)

Gold Dukascopy XAUUSD, learn OOF 2012-17, judged head T2 (stop 1, target 2, both sides). Cost of record
`srv3-cost-2b3628372a9b3bfa` (ub95, worse broker).

| Unit | OOF rows | OOF days | Take-all mean net R (seen) | Mean cost(x) per candidate, R (decision-time) | Gross a kept trade needs for rule 2 (2 × cost) | Kept T2 hit rate needed for S = +0.05 R |
|---|---|---|---|---|---|---|
| 15m | 217,842 | 1,210 | −0.953 | 0.950 | 1.90 R (T2 pays at most +2 R) | 0.667 |
| 30m | 108,590 | 1,210 | −0.362 | 0.361 | 0.72 R | 0.470 |
| 1h | 54,150 | 1,200 | −0.246 | 0.245 | 0.49 R | 0.432 |
| 2h | 28,106 | 1,184 | −0.160 | 0.174 | 0.35 R | 0.408 |
| 4h | 14,330 | 1,167 | −0.127 | 0.127 | 0.25 R | 0.392 |
| 1d+1w | 3,144 | 1,134 | −0.007 | 0.054 | 0.11 R | 0.368 |

How to read the table:
- **Gross per candidate is about 0 at 15m-4h.** Mean net is about −cost(x), so the loss is the cost.
- **The cheapest cost decile still loses** (seen): 15m −0.39 R, 30m −0.18, 1h −0.16, 2h −0.08, 4h −0.10 per candidate.
  Cost avoidance alone cannot reach S > 0 at 15m-4h; it would take gross skill.
- **The needed hit rate** is a label-blind approximation: (1 + cost + 0.05)/3, ignoring overshoot and swap.

**The verdict this card predicts** (fixed in section 3, run mechanically by the evaluator):
- **Every cell is NOT STARTED, 0 K.** Two reasons, each enough on its own:
  1. **No fresh window.** Under r11-v2.1 (ADR 0105), gold 2019-01..2025-09 and 2003-08 are grade C. The gold confirm
     window holds 149 graded days (2018 only), and test_old holds none at 15m and slower. ADR 0085's start rule
     refuses a card whose fresh-window n_eff is below 200 or whose gate power is below 0.5.
  2. **Cost.** At 15m-4h the cost of record is above the gross skill any learner on these inputs has shown.

The owner's question gets an answer at zero trials: **on gold-only history at the cost of record, these learners
cannot be judged, and at 15m-4h they could not earn even if they could be judged.** Section 10 says what would change
that.

## 1. Scope: cells (learner × unit)

| Learner | Family (ADR 0106) | State today | Units | Cells |
|---|---|---|---|---|
| G-cw (GBT-slow, LightGBM depth 2, 400 trees, lr 0.01, monotone in range-to-cost), D-40 dense prior as its inputs | C | OOF scores sealed. v0 artifacts at 15m-4h. **1d+1w: no v0 artifact and no per-seed OOF columns** | 15m, 30m, 1h, 2h, 4h, 1d+1w | 6 |
| HB (hierarchical ridge along the tf ladder) | F | OOF scores sealed. One v0 artifact over the ladder (sha 6599cf7e…) | same 6 | 6 |
| AVG (equal-weight rank fusion, weighted isotonic) | G | OOF scores sealed. **1d+1w: no v0 artifact and no per-seed columns** | same 6 | 6 |
| SEQ-1 label head (`seq1-v1`: TCN, L = 64, about 58k parameters, obj-v1 offset form, 5 seeds) | A | **Unfitted**. Sub-hour refused by spec (R11) | 1h, 2h, 4h, 1d+1w | 4 |
| RL-X1 (`rlx1-v1` fitted-Q exits on T2: hold / close / breakeven) | D (execution) | **Unfitted** | 15m, 30m, 1h, 2h, 4h, 1d+1w | 6 |
| agg-v1 (AdaHedge over the frozen keeps + keep-all + abstain) | G | **Unfitted** | 15m, 30m, 1h, 2h, 4h, 1d+1w | 6 |
| **Total** | | | | **34** |

**D-40 is not a cell.** It has no keep of its own: its two columns (`dense_p_side`, `dense_range_cost`) are inputs of
G-cw only (ADR 0108 decision 5). It has one variant, `DENSE_PARAMS`, cross-fitted forward. Its trial is G-cw's (ADR
0085 A2: one trial per D-40 variant; here it is the only variant and it exists only inside G-cw). No standalone D-40
read is allowed.

**Out of scope:**
- gold 1m and 5m: blocked until D-15 pins the decision-to-fill wait cost;
- the pooled body: refused until D-12-xt re-runs ρ at xt-v1, and FX costs are priors, so no FX row is a gate read;
- non-gold classes;
- `seq1_embed`: R-c places it as a block (K 1) under sr-v3/zoo-v2. Every sr-v3 gold unit is below L3, so no block
  test runs and the embedding has no consumer. It is not run;
- B6 TabPFN and the fitted stack FS: off.

**Frozen inputs.** Each item below is identified by its sha:
- sealed OOF scores (content sha, `scores_sha`): 15m fcf3587e462893bb58de7f923a0b44c6fbdcf6f65932c68191d27424e1ed1a49;
  30m e1df957974c005def2c0a62a59eb6af9a51ab683321f861aac3da352ce589e57; 1h 8e52f132d7f4a5f5f7efc6e16e65bd975ec921c4253e2a7e13bd2ca0092cd7a8;
  2h 1037ed6924b9747d278f81cf880ba855e29b93897ef3624f751d98cc764c35a5; 4h 52f55227909378d9c23b316d44a40cef0aa21450fab07b36ed7c2e50ca6d5eac;
  1d+1w d904d5b201225b7eac0612e7ba2d520588858ddbd8923fbc0448496ccedb1446;
- v0 artifacts (sha256 prefix):
  - G-cw: 15m 1b476f62, 30m d4885e4b, 1h 98a98b16, 2h 9b5f9892, 4h aef609a1;
  - D-40: 459e8f6a, 30cee168, d36600bb, 1a9616ba, 719f69c0;
  - AVG: e7f294d8, 32239702, 488397f2, 234c020e, 9de0a5aa;
  - HB: 6599cf7e;
- the run: `~/tmp/sr-v3-run-2026-11-27-xtv1/out` (local only, ADR 0048), xt-v1 labels (exit freeze b68e2f7b…), tiers
  ff653ea7…, cost of record srv3-cost-2b3628372a9b3bfa.

A refit that does not reproduce these shas is a new spec and costs K.

## 2. Hypotheses, each with its cost-aware card (rule 2)

Common terms, label-blind:
- cost per candidate = the mean cost(x) column of section 0;
- £ per R at 0.01 lot (1 oz) ≈ ATR14 of the tf in USD / 1.27 (about £5.9 at 15m);
- candidates per day = OOF rows / OOF days (15m 180, 30m 90, 1h 45, 2h 24, 4h 12, 1d+1w 2.8, both sides);
- S(k) = k·μ + ρσφ(Φ⁻¹(1 − k)) − max(0, μ), σ = 1.16 R (sr-v3 §0), with μ = −cost (gross prior 0 on dense entries).

**ρ needed for S = +0.05 R:** 15m 0.71, 30m 0.38, 1h 0.30, 2h 0.25, 4h 0.22, 1d+1w 0.16. Best in-house price-only
rank IC: 0.03-0.05 (H-101, R-A).

| Id | Hypothesis | Mechanism (why gold would pay it) | Expected gross edge per kept trade | Expected net £/day at 0.01 lot | Prior: P(Stage L CONTINUE on ≥ 1 cell) |
|---|---|---|---|---|---|
| H-104a G-cw | Non-linear reads of P30 plus the dense prior find states where expected gross beats cost(x) | Volatility is forecastable even when direction is not, and monotone range-to-cost keeps only high-range, low-cost states. That is cost avoidance plus a little gross | At ρ 0.05: S ≈ 0 at 15m-4h and +0.006 R at 1d+1w | ≈ £0 at 15m-4h; about £0.7/day at 1d+1w (+0.006 R × 2.8 × about £47/R); £7/day only at ρ 0.17, which nothing in-house has shown | 3 % |
| H-104b HB | Partial pooling along the tf ladder lowers coefficient variance | Variance reduction only, no new information | ≤ G-cw | ≤ G-cw | 2 % |
| H-104c AVG | Equal-weight fusion beats its best base out of sample (the forecast-combination result) | Variance reduction only | ≤ best base + small | ≤ G-cw | 2 % |
| H-104d SEQ-1 head | Raw 64-bar windows carry gross information that engineered features miss | Path shape (compression, failed breaks) before a move; low prior (R-20, the 0.50 AUC record) | ≤ G-cw | ≈ £0 | 2 % |
| H-104e RL-X1 | A state-dependent exit (close early, breakeven) beats the fixed T2 exit after costs | Fewer swap nights and less stop overshoot; earlier exits when the path signals a stall. It cannot create gross on random entries | Paired Δ ≤ +0.05 R (prior upper bound) | ≈ £0 (the dense population is net negative) | 3 % |
| H-104f agg-v1 | Online weighting picks the better expert, or abstains | Protection only. With every expert net negative it converges on abstain | 0 | 0 (its value is the futility record) | n/a |

**Rule 2 check, applied now:**
- At 15m-4h the gross a kept trade needs (2 × cost) is 0.25-1.90 R. The reachable IC gives S ≈ 0. **No 15m-4h cell
  meets rule 2.**
- 1d+1w meets it only at an IC four times the measured price IC.
- The card is still written in full, so that the refusal is a recorded, mechanical, signed result and not an opinion.

## 3. Stage 0 (0 K, label-blind, mechanical; the evaluator runs it and records it)

A cell starts, and only then books K and reads, iff S0-1 to S0-5 all hold, in this order. The first failure ends the
cell with its NOT STARTED reason. It is recorded through `power_first.start_or_refuse` (registry status
`not_started_power`, no trial number, no K), with the other reasons in the record's text.

**S0-1 Fit complete (label-free).**
- The cell's v0 artifact and its sealed OOF scores exist with the section 1 shas, and the per-seed OOF columns exist
  for G-cw and AVG (ADR 0106: 5 seeds).
- SEQ-1, RL-X1 and agg-v1 are fitted only for cells that pass S0-2 and S0-3 first, so no compute is spent on a cell
  that cannot start. Their fit-only runs are 0 K (ADR 0085 A2) and seal their scores the same way.
- **G-cw and AVG at 1d+1w fail S0-1 today.**

**S0-2 Edge feasibility (decision-time cost only; no outcome).**
- For filters (G-cw, HB, AVG, SEQ-1): with μ = −mean cost(x) over the unit's OOF rows, the ρ needed for S = +0.05 R by
  the section 2 formula must be **≤ 0.17**. 0.17 is the top of sr-v3 §0's needed band and 3-5 times the measured
  price IC.
- For RL-X1: max(0.03, mean cost(x)) must be **≤ 0.05 R**, the prior upper bound on exit Δ. The policy can pass the
  net > 0 leg only if Δ covers the cost on entries with gross prior 0.
- agg-v1 needs ≥ 2 started experts (learner cells) at the unit, besides keep-all and abstain.
- A non-G-cw filter cell needs the G-cw cell of its unit to start (it must be read against G-cw; ADR 0106). AVG also
  needs ≥ 2 of its bases started.

**S0-3 Power (ADR 0085, binding).**
- Formula: power_g = Φ(0.10·√n_eff / 1.2 − z_g) per gate. The cell starts only if every power_g ≥ 0.5, the joint power
  is ≥ 0.25, and n_eff ≥ 200 at each fresh-data gate.
- Inputs, outcome-blind:
  - Stage L n_eff = card 5.1 scored-window n_eff: 15m 3,040; 30m 2,012; 1h 1,553; 2h 776; 4h 310; 1d+1w ≤ 233
    (card 2.2 days/hold × 6/8, an upper bound);
  - fresh-window n_eff: filters use confirm 2018-01..2025-09 under r11-v2.1, card-method gold judge 15m 292, 30m 193,
    1h 149, 2h 74, 4h 30, 1d+1w 22. RL-X1 uses test_old (R-d), counted on tier-usable rows by the evaluator.
- z_g:
  - Stage L: Holm rank 1 of m = the number of cells passing S0-1 and S0-2, at α 0.10;
  - confirm: the ADR 0085 ledger's year-1 α_j at Holm rank 1 (z 2.87 at m_j = 3, higher with more members).
- Also reported, not gating: sr-v3 5.1 power at the label-free keep share k̂ from the sealed `k_` columns (evaluator
  C5).

**S0-4 ABSTAIN (label-free).** Fewer than 100 kept OOF rows or 30 kept days, counted from the sealed `k_` columns.
ABSTAIN is 0 K (ADR 0098 §3).

**S0-5 Leak and sanity suite.** The sr-v3 N25 suite and the N6 shuffled-label keep check (sr-v3 5.1), per started
cell. SEQ-1 adds its causal-window prefix and scramble tests. Failure: NOT STARTED, 0 K.

**Predicted Stage 0 (computed for this card from the inputs above; the evaluator's run is the record):**

| Unit | S0-2 filters (ρ needed) | S0-3 Stage L power | S0-3 fresh n_eff / power | Cells starting |
|---|---|---|---|---|
| 15m | fail (0.71) | 0.97 | 292 / 0.07 | 0 |
| 30m | fail (0.38) | 0.84 | 193 (< 200) / 0.04 | 0 |
| 1h | fail (0.30) | 0.70 | 149 (< 200) / 0.03 | 0 |
| 2h | fail (0.25) | 0.33 | 74 / 0.02 | 0 |
| 4h | fail (0.22) | 0.10 | 30 / 0.01 | 0 |
| 1d+1w | pass (0.16); G-cw and AVG fail S0-1 | ≤ 0.35 | 22 / 0.01 | 0 |

- RL-X1 fails S0-2 at every unit (mean cost 0.054-0.95 R > 0.05 R) and S0-3 at every unit.
- agg-v1 fails S0-2: no unit has 2 started experts.
- Stage L power is shown at z 2.75 (Holm over 34); at 1d+1w it is at z 1.645, the most lenient case.

**Predicted: 0 of 34 cells start, 0 K.**

## 4. Reading rule for a cell that starts (fixed now; used only if Stage 0 starts a cell)

**Population.**
- Learn OOF 2012-17, expanding yearly folds, full-horizon purge, gap max(1 d, max H over the unit's tfs) (sr-v3 R-B,
  rev 9 D3).
- Rows: P30-usable rows (rev 9 D1). Every comparison with L-cw, B8 or another learner is made on the intersection,
  and its count is reported.
- Cost: the ub95 cost of record srv3-cost-2b3628372a9b3bfa, at the worse broker. Every read states "S at the
  worse-broker cost". Statistics use `net_read` (realised: RT ub95 + overshoot on stops + swap per night held); keeps
  use cost(x).

**Stage L, a screen (N26: 2012-17 is seen; no go decision).** A filter cell CONTINUES iff all of these hold:
1. S ≥ +0.05 R with a one-sided 95 % day-block lower bound > 0 (BOOT 10,000, mean block 5 days, seed 20261206).
2. Kept mean net > 0.
3. Gross skill k × (kept gross − all gross) ≥ 0.
4. Kept mean − matched twin (B7, side- and hour-matched random keep at the same share) ≥ +0.05 R.
5. **Holm across every started cell of this card**, on max(p_S, p_ctrl) ≤ 0.10.
6. S ≥ 0 at 2× cost with the keep set fixed (1.5× reported).
7. **Beats B1 (take-all), B8 (intercept-only), B0, and max(B3, B4, B5, B6)** on point S (sr-v3 5.2), and beats
   buy-and-hold of gold over the OOF span after one round trip (ADR 0106 decision 4).
8. **Against G-cw** (HB, AVG, SEQ-1): paired D per OOS session day > 0, with a one-sided 95 % day-block lower bound
   > 0, on the shared rows. The Holm family is the started non-G-cw cells, at 0.10. G-cw itself must pass 1-7.
9. Seed stability: G-cw and AVG pass `model_quality.seed_stability` over the 5 seeds. For SEQ-1, seed SD < 0.025 R,
   otherwise INCONCLUSIVE (a NULL).
10. Parameter shift (G-cw): S ≥ 0 at every point of the ±10 % shift grid (`model_quality.param_shift`).
11. No N22 veto (shuffle, oracle canaries, feature shift, calibration slope CI wholly below 0.7). Gate 10 counts hold
    (≥ 1,500 learn candidates, ≥ 60 per fold).

**Every read reports**, whatever its verdict:
- S by year 2012-17;
- the share of rows where the ECR term bound the cost;
- the S split (gross skill, cost avoidance, base). A pass whose gross-skill CI includes 0 is labelled
  cost-avoidance-led;
- £/day at 0.01 lot;
- for **15m**, S at the vantage cell charge, as a descriptive sensitivity and never the gate (ADR 0098 R-H read
  requirements);
- the losers next to the winners.

**Stage C (confirm, batched 2027-01-08 12:00 UTC, N26 / ROADMAP N10).**
- Continuing cells join the evaluator's batch. Members and frozen artifact shas are recorded by 2027-01-06 23:59 UTC.
- The frozen 2010-17 v0 is scored once, with Holm within the batch on the ADR 0085 ledger.
- Gates 1-4, 6, 7 and 8, plus sr-v3 gates 9-12 (arm-fire subset, n_eff ≥ 200, 3 positive years incl. 2021 or 2022).
- Gold confirm holds only 2018 under r11-v2.1, so gate 12 cannot be met. Gold cells cannot pass confirm under today's
  grade. That is stated now, not discovered later.

**Stage T and H.**
- test_old is read once, passers only.
- The holdout 2025-10-01..2026-09-30 stays **sealed**. Nothing in this card reads, scores, refits or tunes on it. A
  holdout read needs a new cost version and a quant + research-director unseal (sr-v3 5.3).

**What a pass licenses.** Only what sr-v3 5.3 licenses (evaluator C7/C9). A learner never gates demo trades beyond the
learning class without trading-safety review. **Real money: owner-only.**

## 5. RL-X1: reward, cost and shield (fixed now)

- **Episodes.** One per dense T2 entry of the unit (both sides), replayed on its own bars from the decision to the
  horizon end. A/A-consistent rows only (R-d), learn 2010-17. Policy OOF uses the same folds and gap as section 4.
- **Actions** at each bar close: hold; close at the close; breakeven (move the stop to entry, once, and only while the
  close is above entry).
- **Shield:** the T2 stop (−1 R), the target (+2 R) and the time exit at H always stand. The policy never widens or
  removes a stop. RL-X1 is never live (`live: Literal[False]`, no engine import, `require_servable` refuses it).
- **Reward = net R** = exit gross R (mid, side-oriented; a gapped stop fills at the bar open, a gapped target at the
  target) − round-trip cost at the **ub95 cost of record** (spread once + commission + slip_x of
  srv3-cost-2b3628372a9b3bfa, worse broker) − overshoot on a stop exit − swap of every rollover night actually held.
  The close action pays the same round trip as any exit, so actions differ in cost only through overshoot and nights.
- `rl_sim.SimCosts` must be built from the cost-of-record file. A W2-point or zero-slippage simulator is not a gate
  read (ADR 0107 consequences).
- **Fit:** fitted-Q iteration as `rlx1-v1`: 30 iterations, ridge 1.0, a fixed quadratic basis on the 7 state
  features, seed 20261126. No grid.
- **Baselines:**
  - (i) the fixed T2 exit;
  - (ii) the best fixed template T0-T7, chosen on each fold's training years only;
  - (iii) the shuffled-state control.
- **Gate (ADR 0098 RL row as amended by R-H):**
  - paired Δ = mean net R(policy) − mean net R(baseline ii) ≥ +0.03 R, with a one-sided 95 % session-day block lower
    bound > 0;
  - policy mean net > 0;
  - Δ vs the shuffled-state control > 0;
  - Δ ≥ 0 at 2× cost;
  - Holm with the card's started cells.
- **Fresh read:** test_old once, under a booked read id (R-d).

## 6. agg-v1: experts, rounds and the futility record (fixed now)

- **Experts:** the started learner cells of the unit (their frozen keeps), plus keep-all and abstain.
- **Rounds** are session days. The loss of an expert is −(net R of the candidates it keeps that day). Weights come
  from AdaHedge (de Rooij et al. 2014), with no tuning constant. The aggregate keeps iff the weight of the keeping
  experts is ≥ 1/2.
- **History run:** warm start on OOF 2012. Runs are calendar years 2013, 2014, 2015, 2016 and 2017. Each round's
  weights use only earlier closed rounds.
- **Futility record (kill):** abstain holds the top weight at the close of 4 consecutive runs. agg-v1 is then retired
  for that unit, recorded the same day, with no confirm.
- **Gate (ADR 0098 allocation row):**
  - an anytime-valid confidence sequence (Howard et al. 2021) on the cumulative net R of agg-v1 minus equal weight,
    with a lower bound > 0 at the end of 2017;
  - plus Stage L gates 1-6 on the aggregate keep;
  - read only with ≥ 2 started experts.
- **The forward run** (2027Q1, ADR 0099: 3 K) is a separate card. It has no expert unless a cell here starts.

## 7. K and budget

| Item | K per cell | Cells | Worst case | Predicted |
|---|---|---|---|---|
| Stage L, G-cw / HB / AVG | 1 | 18 | 18 | 0 |
| Stage L, SEQ-1 head | 1 | 4 | 4 | 0 |
| Stage L, RL-X1 | 1 | 6 | 6 | 0 |
| Stage L, agg-v1 | 1 | 6 | 6 | 0 |
| Stage C, one per continuing cell | 1 | ≤ 34 | 34 | 0 |
| Stage T / H, one per passer | 1 | — | (cannot occur for gold under r11-v2.1) | 0 |
| **Total** | | | **68** (34 in 2026Q4, 34 in 2027Q1) | **0** |

- **Booking.**
  - One registry row per started cell: family `zoo-v2:<learner>:<unit>`, `seq1-head:<unit>`, `rlx1:<unit>` or
    `agg-v1:<unit>`, rule_ref this file.
  - It is written after `check_budget` and before any read statistic.
  - Booking order, if the budget binds: G-cw, HB, AVG, SEQ-1, RL-X1, agg-v1, slow units before fast. A cell refused for
    budget is NOT STARTED (budget), 0 K, and is never read.
  - NOT STARTED rows take no trial number (ADR 0085).
- **2026Q4 headroom.** Last recorded: 73 free (sr-v3 card, 2026-11-26). Commitments: sr-v3 worst case 13 and
  multi-tf ≤ 5, so about 55 are uncommitted. The worst case of 34 fits. The ops-VM registry is the count of record at
  booking; the local snapshot is stale (2026-11-07).
- **2027Q1.** ADR 0099 plans zoo-v2 core 4 and agg-v1 3. A confirm worst case above 7 goes to the PD + quant
  checkpoint (60/120/180); the ceiling of 250 holds.
- **K rules carried.**
  - The cost version a unit is first read under is its version of record. The sr-v3 units were first read (L-cw/B8)
    under srv3-cost-2b3628372a9b3bfa, so a zoo read under another version is K +1 and the first read stands.
  - Changing a recipe, a loss or a hyperparameter after any look is K +1 (ADR 0085 A2, ADR 0098).

## 8. Kill rules, dates and owners

| Date | Step | Owner | Rule |
|---|---|---|---|
| 2026-11-28 | Card proposed | research director | — |
| 2026-12-01 | Quant and evaluator sign, or return changes | quant-reviewer, evaluator | — |
| 2026-12-02 | Stage 0 run and recorded (registry `not_started_power` rows with the S0 reasons) | evaluator | mechanical |
| **2026-12-02 (kill date)** | **If 0 cells start, H-104 on gold-only history is CLOSED that day, 0 K.** It is not re-run, widened or re-parameterised | research director | rule 3 |
| 2026-12-18 | Stage L reads of started cells (if any), with sr-v3's training report | ml-researcher (fit), evaluator (read) | a NULL closes the cell |
| 2027-01-06 23:59 UTC | Confirm members and shas | evaluator | |
| 2027-01-08 12:00 UTC | Batched confirm | evaluator | |

**Kill rules:**
- A Stage L NULL closes the cell.
- All filter cells NULL or NOT STARTED retires "a learner over P30 (+D-40) on gold dense T2 at the cost of record"
  (sr-v3 N10 stop rule).
- A SEQ-1 NULL retires the sequence family (R6).
- An RL-X1 NULL retires fitted-Q exits on dense T2 entries.
- An agg-v1 futility record retires it for the unit.

**At closure the sealed scores stay sealed:**
- `ZOO_V2_CARD` stays `None`;
- SEQ-1's label head, RL-X1 and agg-v1 are not fitted on gold;
- the sealed files are kept for audit, and no read of them is allowed except under a new card with a powered fresh
  window.

## 9. Integrity

- **Pre-registration.** Written before any sealed score was opened. The only outcome statistics behind it are the
  seen L-cw/B8 learn read and its re-derived take-all means (section 0).
- **Holdout.** Sealed, untouched. No row decided or resolved at or after 2025-10-01 enters anything here. Confirm and
  test_old are counted (outcome-blind), never read, at Stage 0.
- **Controls.** Every started cell is read against the matched twin (B7) by day, side and hour.
- **Sealing is by convention today (blocker, code):**
  - read mode writes `oof.parquet` holding the `v_`/`k_` columns of G-cw, HB and AVG next to `net_read`. Anyone with
    the file could compute their S. This card did not open those columns.
  - File sha256, recorded now, so the evaluator can check the seal holds:
    - 15m 0ee9966cec5c336c7ac6eadd2dfb2175f773cf4ccadd3805fdf6dc9da9dc8549;
    - 30m 0a25a24201973f6e3990a1dc09a94e1ddea50deb1db72544e67c9b15563d77b0;
    - 1h 43d5708dd70e59d0c1d91f2c53fbac84e8fd8525dc825807809483e9611a7c27;
    - 2h 728f1eb1dfdbe27d97d9886567d969a186a7125db78278ee8fa760fb0ae223ce;
    - 4h 87721da808ba6e30bc571e64083c1c4699836b06257cb50434ffd7292dad9937;
    - 1d+1w dcdde06df5859c586be84b81db70e2ce46f4df6486cc2d12c50871d033954fb9.
  - The trainer must stop writing withheld learners' columns into `oof.parquet` (backlog item of this PR).
- **`ZOO_V2_CARD` opens all three learners at every unit at once** (`read_learners`). It may be set only together
  with a per-cell allowlist of the cells Stage 0 started (backlog item of this PR). Under the predicted Stage 0 it is
  never set.
- **Deflation.** At the registry K of the read day, reported.

## 10. What would reopen this, and what it exposes

**Reopening routes.** Each needs new evidence, not a re-run.
- **U1. Gold 2019-25 regrade.** ADR 0105 notes the C grade is a property of the fix check, not evidence against the
  feed. A quant-signed rule that grades those months would give the confirm window about 1,850 days. That is a tier
  change after labelled counts, so it costs K (A2/A4) and needs a new card. P would then pass at 15m-1h; S0-2 still
  fails at 15m-4h.
- **U2. Pooled body (gold + FX10 + XAG).** Needs D-12-xt and a measured FX cost (today FX costs are priors, so not a
  gate read). The refit is fit-only, 0 K, and seals its scores. Stage 0 under this card is then re-run mechanically
  at 0 K, with the same rule and the new counts.
- **U3. DEEP-DAILY.** Gold 1978-2002 daily is a pre-learn window, so it needs its own card (quant + evaluator). It is
  the one route where cost per candidate is small (1d+1w: 0.054 R).
- **U4. A cheaper cost of record** (a measured re-cell with n ≥ 100). It is a new version, so a read is K +1, and
  S0-2 is re-run.

**What it exposes (rule 1 gap).**
- On dense gold entries at 15m-4h, the ub95 cost of record is the whole loss: 0.95 R per candidate at 15m, 0.25 R at
  1h. No learner on these inputs closes that.
- The money levers are:
  - cost itself (microstructure: why the 15m ub95 round trip is about one 2012-17 15m ATR);
  - longer holds, where cost per R is small (1d+, with deeper history);
  - populations with shown gross. The owner's box break+close + MFI trades are one; dense random bars are not.

## 11. Owner-only

None assumed. No sizing, risk limit, spending or live decision is taken. Real money stays owner-only.

## 12. Questions the reviewers must settle before signing

1. **Quant:** S0-2 uses the cost of record and the sr-v3 §0 approximation as a label-blind edge refusal, alongside
   ADR 0085's power rule. Is ρ ≤ 0.17 the right bound, and is μ = −cost (gross prior 0) acceptable?
2. **Quant:** ADR 0085 refuses a card whose fresh-window n_eff is below 200. sr-v3 5.1.4 says "NOT STARTED for that
   window". This card applies ADR 0085: no Stage L read without a powered fresh window. Please confirm, and reconcile
   sr-v3's wording.
3. **Quant:** RL-X1's power uses the 1.2 R sd floor. A paired exit Δ has a smaller sd. Does the floor bind for paired
   differences? With the floor, RL-X1 cannot start anywhere. The decision does not change the prediction (S0-2 fails
   first).
4. **Evaluator:** 1d+1w Stage L n_eff for P30 rows (233 is an upper bound), and RL-X1 test_old tier-usable counts.
5. **Evaluator:** confirm the K headroom on the ops-VM registry.

## Sign-off

- Research director: 2026-11-28 (this card).
- Quant-reviewer: pending.
- Evaluator: pending.
