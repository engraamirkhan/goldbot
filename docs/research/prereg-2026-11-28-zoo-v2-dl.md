# Pre-registration: H-104, the sealed and unfitted learners on gold history (spec `zoo-v2`)

Research director, 2026-11-28. Owner directive of the same day: the models must reach a readable, judged state as
soon as possible. This card covers the learners that are sealed or unfitted:
- **(a) zoo-v2 tabular:** G-cw (with the D-40 dense prior as its inputs), HB, AVG;
- **(b) the SEQ-1 label head** (`seq1`, PR #489; quant ruling R-c says it needs its own card);
- **(c) RL-X1 fitted-Q exits and agg-v1 AdaHedge** (PR #489, ADR 0107);
- **(d) two cascade cells the owner asked for:** CAS-1 (SEQ-1 embedding feeding G-cw) and CAS-2 (a deep + tree
  stack).

**Revision 2 (2026-11-28)** applies the quant review of PR #521 (F1-F10, verdict CHANGES, text only) and adds the
owner's two cascade cells (section 2a). It is label-blind as before. The predicted result (0 cells started, 0 K) is
unchanged: S0-3 alone refuses every cell.

**Label-blind.** It was written without opening any sealed OOF score (`oof_scores.parquet`, or the `v_`/`k_`
columns of `oof.parquet`) and without any new outcome statistic.

**Seen and disclosed.** The following were seen before writing:
- the sr-v3 learn read of 2026-11-28 for L-cw and B8 (take-all S per unit; L-cw AUC 0.50-0.52; L-cw and B8 keep 0
  at 15m-4h);
- that read's cost-decile table for L-cw = B8. B8 is intercept-only at every unit, so its deciles rank by cost(x).

**Re-derived for this card (F8):** the take-all mean net R and the mean decision-time cost(x) per unit. They come from
`~/tmp/sr-v3-run-2026-11-27-xtv1/out/<unit>/oof.parquet`, read with a column projection of `net_read` and `cost` only:

```
uv run --no-project --with pyarrow --with pandas python -c "import pyarrow.parquet as pq; t = pq.read_table('<unit>/oof.parquet', columns=['net_read', 'cost'])"
```

The mean of each column was then taken. The sealed score files were read for their schema (column names) only, with
`pq.read_schema`.

**Status: proposed, revision 2.** The quant-reviewer and the evaluator sign before Stage 0 is recorded. This card
books **0 K**. Real money, sizing and live unlock stay owner-only. Nothing here touches the engine, the RiskGate or
any account.

## 0. Money first: what the history already says (numbers before any idea)

Gold Dukascopy XAUUSD, learn OOF 2012-17, judged head T2 (stop 1, target 2, both sides). Cost of record
`srv3-cost-2b3628372a9b3bfa` (ub95, worse broker).

| Unit | OOF rows | OOF days | Take-all mean net R (seen) | Mean cost(x) per candidate, R (decision-time) | Gross a kept trade needs for rule 2 (2 × cost) | Kept T2 hit rate for kept mean net +0.05 R (k = 1; S needs 0.05/k) |
|---|---|---|---|---|---|---|
| 15m | 217,842 | 1,210 | −0.953 | 0.950 | 1.90 R (T2 pays at most +2 R) | 0.667 |
| 30m | 108,590 | 1,210 | −0.362 | 0.361 | 0.72 R | 0.470 |
| 1h | 54,150 | 1,200 | −0.246 | 0.245 | 0.49 R | 0.432 |
| 2h | 28,106 | 1,184 | −0.160 | 0.174 | 0.35 R | 0.408 |
| 4h | 14,330 | 1,167 | −0.127 | 0.127 | 0.25 R | 0.392 |
| 1d+1w | 3,144 | 1,134 | −0.007 | 0.054 | 0.11 R | 0.368 |

How to read the table:
- **Gross per candidate is about 0 at 15m-4h.** Mean net is about −cost, so the loss is the cost.
- **The cheapest cost decile still loses** (seen): 15m −0.39 R, 30m −0.18, 1h −0.16, 2h −0.08, 4h −0.10 per candidate.
  Cost avoidance alone cannot reach S > 0 at 15m-4h.
- **The hit rate** is the approximation (1 + cost + 0.05)/3, ignoring overshoot and swap.

**The verdict this card predicts** (section 3, run mechanically by the evaluator): **every cell is NOT STARTED, 0 K.**
- **The record reason is S0-3 (ADR 0085 power).** Under r11-v2.1 (ADR 0105), gold 2019-01..2025-09 and 2003-08 are
  grade C. The gold confirm window holds 149 graded days (2018 only), and test_old holds none at 15m and slower. No
  pre-registered fresh route has n_eff ≥ 200 with gate power ≥ 0.5.
- **S0-2 (cost feasibility) is reported beside it and is never the sole reason** (F1). It was written after the
  L-cw/B8 read was seen.

So these learners are **UNTESTABLE at the cost of record on gold 2010-25 history**. This is not a NULL: no Stage L read
happens. Section 10 says what would change that.

## 1. Scope: cells (learner × unit)

| Learner | Family (ADR 0106) | State today | Units | Cells |
|---|---|---|---|---|
| G-cw (GBT-slow, LightGBM depth 2, 400 trees, lr 0.01, monotone in range-to-cost; inputs P30 + 2 D-40 + range_to_cost = 33) | C | OOF scores sealed. v0 artifacts at 15m-4h. **1d+1w: no v0 artifact and no per-seed OOF columns** | 15m, 30m, 1h, 2h, 4h, 1d+1w | 6 |
| HB (hierarchical ridge along the tf ladder) | F | OOF scores sealed. One v0 artifact over the ladder (sha 6599cf7e…) | same 6 | 6 |
| AVG (equal-weight rank fusion, weighted isotonic) | G | OOF scores sealed. **1d+1w: no v0 artifact and no per-seed columns** | same 6 | 6 |
| SEQ-1 label head (`seq1-v1`) | A | **Unfitted**. Sub-hour refused by spec (R11) | 1h, 2h, 4h, 1d+1w | 4 |
| RL-X1 (`rlx1-v1`) | D (execution) | **Unfitted**; baseline (ii) not implemented | 15m, 30m, 1h, 2h, 4h, 1d+1w | 6 |
| agg-v1 | G | **Unfitted** | 15m, 30m, 1h, 2h, 4h, 1d+1w | 6 |
| CAS-1 SEQ-1 embed → G-cw (section 2a) | A + C | **Unfitted** | 4h, 1d+1w | 2 |
| CAS-2 deep + tree stack (section 2a) | G over A + C | **Unfitted** | 4h, 1d+1w | 2 |
| **Total** | | | | **38** |

**D-40 is not a cell.** It has no keep of its own: its two columns (`dense_p_side`, `dense_range_cost`) are inputs of
G-cw only (ADR 0108 decision 5). It has one variant, `DENSE_PARAMS`, so its trial is G-cw's (ADR 0085 A2). No
standalone D-40 read is allowed.

**Out of scope:**
- gold 1m and 5m: blocked until D-15;
- the pooled body: a new population, so a new card (section 10);
- non-gold classes;
- `seq1_embed` as an sr-v3 block: every sr-v3 gold unit is below L3, so no block test runs. It enters here only
  inside CAS-1;
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
  ff653ea7…, cost of record srv3-cost-2b3628372a9b3bfa;
- code for `seq1-v1`, `seq1-emb-v1`, `rlx1-v1` and `agg-v1`: PR #489 head 607717a9b050d4fe815cdc82aefa921618740389.
  The specs are frozen at the code sha that merges to main. A change after that is a new spec.
- **S0-1 preconditions (F4f, F8):** PR #489 merged; ADR 0107 accepted; backlog 277 merged (acceptance 1-4).

A refit that does not reproduce these shas is a new spec and costs K.

## 2. Hypotheses, each with its cost-aware card (rule 2)

Common terms, label-blind:
- cost per candidate = the mean cost(x) column of section 0;
- £ per R at 0.01 lot (1 oz) ≈ ATR14 of the tf in USD / 1.27 (about £5.9 at 15m);
- candidates per day = OOF rows / OOF days (15m 180, 30m 90, 1h 45, 2h 24, 4h 12, 1d+1w 2.8, both sides);
- S(k) = k·μ + ρσφ(Φ⁻¹(1 − k)) − max(0, μ), with μ = −cost (gross prior 0 on dense entries).

**F1 (quant, verbatim):** "ρ is the Pearson correlation of the score with gross R (cost excluded). σ = 1.5 R, the
largest sd of a T2 {−1, +2} payoff, so the refusal holds for any σ ≤ 1.5. The bound 0.17 is about 3.4× the largest
measured price-only IC (0.05, H-101 learn OOF, another family, seen; an external prior, not label-blind).
In-population evidence: L-cw AUC 0.50-0.52 (seen). `report.json` `ic_within_instrument` is not an IC for this rule:
it is Spearman on `net_read` with cost on both sides. S0-2 was written after the L-cw/B8 read was seen, so it is never
the sole reason for a refusal or closure. Every record cites S0-3 (ADR 0085) first."

**ρ needed for S = +0.05 R at σ = 1.5:** 15m 0.55, 30m 0.29, 1h 0.23, 2h 0.20, 4h 0.169, 1d+1w 0.12.
- These were computed for this card on a fine k grid. The quant's review quotes 0.48 for 15m; the refusal is the same
  either way.
- Backlog 278 redefines `ic_within_instrument`.

| Id | Hypothesis | Mechanism (why gold would pay it) | Expected gross edge per kept trade | Expected net £/day at 0.01 lot | Prior: P(Stage L CONTINUE on ≥ 1 cell) |
|---|---|---|---|---|---|
| H-104a G-cw | Non-linear reads of P30 plus the dense prior find states where expected gross beats cost(x) | Volatility is forecastable even when direction is not, and monotone range-to-cost keeps only high-range, low-cost states. That is cost avoidance plus a little gross | At ρ 0.05: S ≈ 0 at 15m-4h and +0.01 R at 1d+1w | ≈ £0 at 15m-4h; about £1/day at 1d+1w (+0.01 R × 2.8 × about £47/R) | 3 % |
| H-104b HB | Partial pooling along the tf ladder lowers coefficient variance | Variance reduction only | ≤ G-cw | ≤ G-cw | 2 % |
| H-104c AVG | Equal-weight fusion beats its best base out of sample | Variance reduction only | ≤ best base + small | ≤ G-cw | 2 % |
| H-104d SEQ-1 head | Raw 64-bar windows carry gross information that engineered features miss | Path shape before a move; low prior (R-20, the 0.50 AUC record) | ≤ G-cw | ≈ £0 | 2 % |
| H-104e RL-X1 | A state-dependent exit beats the fixed exits after costs | Fewer swap nights and less overshoot. On zero-gross entries its expected gross is 0 (section 5) | Paired Δ small; net > 0 unattainable on dense entries | ≈ £0 | ≈ 0 on this population |
| H-104f agg-v1 | Online weighting picks the better expert, or abstains | Protection only | 0 | 0 (its value is the futility record) | n/a |
| H-104g CAS-1, H-104h CAS-2 | Section 2a | | | | |

**Rule 2:**
- At 15m-4h the gross a kept trade needs (2 × cost) is 0.25-1.90 R. No 15m-4h cell meets rule 2.
- 1d+1w meets it only at about 2.5 times the largest measured price IC.

## 2a. The owner's cascade cells (pre-registered under the same Stage 0, Stage L and confirm rules)

**Honest prior, both cells.** Combining reduces variance. It cannot create an edge that is absent in all of its
parts. A cascade can pass only if at least one part carries gross information about T2 beyond cost(x), and the parts'
errors are not perfectly correlated. Prior P(Stage L CONTINUE): 2 % each.

**CAS-1 "SEQ-1 embed → G-cw" (H-104g), units 4h and 1d+1w.**
- **Inputs (40-feature cap).** G-cw's 33 inputs, plus the 8 dims of `seq1-emb-v1` (`seq1_embed.DIM` = 8,
  outcome-free, quant R-c), less one column: 41 − 1 = **40**.
- **The dropped column, fixed now, label-blind:** `d1_slope_ema20`, the last column of P30 in the frozen level order
  (sr-v3 3.1 and Stage 0 plan). It is the column the ladder would charge last, and one of the two d1 columns sr-v3
  3.1 calls structurally weak today.
- **The embedding.** Refit per outer fold on that fold's training rows, purged by `feature_store.purge` with embargo =
  64 bars × tf + H, learn 2010-17 only (R-c, ADR 0107 point 0). It passes the A8 parity battery on the 8 dims and
  the 5-seed CKA stability report. The served embedding is seed index 0.
- **G-cw recipe unchanged:** GCW_PARAMS and DENSE_PARAMS, the same seeds by manifest key, the same monotone
  constraint, and the obj-v1 keep p_w ≥ 0.5.
- A new artifact, fit-only at 0 K, seals its scores like section 1.

**CAS-2 "deep + tree stack" (H-104h), units 4h and 1d+1w.** This is the zoo-v1 F2 STACK pattern under obj-v1.
- **Inputs:** logit(clip(G-cw p_w, 0.01, 0.99)), with p_w the mean over G-cw's 5 seeds, and the SEQ-1 head's
  E[gross R | x] − cost(x), in R.
- **Model:** an L2 logistic, C = 1.0, standardised on the stacker's training rows. y = 1[net R > 0], with weights
  u × min(|net R|, 10) (ADR 0098 §2). Keep iff p_w ≥ 0.5.
- **OOF only.** The stacker for year Y is fitted only on base OOF scores of years before Y, each from a base model
  trained before that row's year (plus the unit's gap). Year Y's stacker scores come from base OOF scores of Y.
  The stacker for year Y trains only on rows whose label horizon ends before the start of Y minus the unit's gap (the
  same full-horizon purge as section 4).
- The first OOF base year is 2012, so the stacker's judged rows are 2013-17. Every paired comparison uses those rows.
- An OOF-only stacking test is an S0-5 item, as in zoo-v1 test 3.

**Both cells:**
- Same cost of record, row population (P30-usable, intersection reported) and read requirements as section 4.
- To CONTINUE, each must pass section 4 gates 1-7 and 9-11. It must also **beat G-cw alone** on paired D per OOS
  session day, with a one-sided 95 % day-block lower bound > 0, on the shared rows. It must beat **B0, B1, B8 and
  buy-and-hold** after costs. A CAS cell starts only if the G-cw cell of its unit starts. CAS-2 starts only if the
  G-cw and SEQ-1 cells of its unit start.
- **Preconditions (S0-1):** PR #489 merged, ADR 0107 accepted, backlog 277 merged. CAS-2 also needs the SEQ-1 head
  fitted. Both need G-cw's 1d+1w v0 and per-seed columns.

**Predicted Stage 0:**

| Cell | S0-1 | S0-2 (ρ needed, reported) | S0-3 (record reason) |
|---|---|---|---|
| CAS-1 4h, CAS-2 4h | fail (preconditions; parts unfitted) | pass (0.169) | fail: Stage L power 0.10 (Holm), fresh n_eff 30 |
| CAS-1 1d+1w, CAS-2 1d+1w | fail (as above, plus G-cw has no 1d+1w v0) | pass (0.12) | fail: Stage L power ≤ 0.35, fresh n_eff 22 |

## 3. Stage 0 (0 K, label-blind, mechanical; the evaluator runs it and records it)

**Per cell (quant Q2): the ADR 0085 start rule applies per cell (procedure × judged unit, N18), not to the whole
card.** A cell starts, and only then books K and reads, iff S0-1 to S0-5 all hold.
- The evaluator evaluates every item for every cell and records all failing items.
- The record cites S0-3 first, through `power_first.start_or_refuse` (registry status `not_started_power`, no trial
  number, no K).

**S0-1 Fit complete and preconditions (label-free).**
- The cell's v0 artifact and sealed OOF scores exist with the section 1 shas.
- The per-seed OOF columns exist for G-cw, AVG and CAS-1.
- PR #489 is merged, ADR 0107 accepted, and backlog 277 merged.
- RL-X1 fails S0-1 until baseline (ii) (section 5) is implemented and tested.
- SEQ-1, RL-X1, agg-v1 and the CAS cells are fitted only for cells whose S0-3 passes, so no compute is spent on a
  cell that cannot start. Their fit-only runs are 0 K (ADR 0085 A2) and seal their scores.

**S0-2 Cost feasibility (reported; never the sole reason, F1).**
- **Filters** (G-cw, HB, AVG, SEQ-1, CAS-1, CAS-2): the ρ needed for S = +0.05 R at σ = 1.5 and μ = −mean cost(x) is
  ≤ 0.17.
- **RL-X1:** on zero-gross entries the policy net is −round trip in expectation (section 5). It therefore fails S0-2
  on dense entries by construction.
- **Structural conditions:**
  - agg-v1 needs ≥ 2 started learner experts at its unit;
  - a non-G-cw filter or CAS cell needs the G-cw cell of its unit to start;
  - AVG needs ≥ 2 started bases.

**S0-3 Power (ADR 0085, binding; the record reason).**
- Formula: power_g = Φ(0.10·√n_eff / 1.2 − z_g) per gate.
- A cell starts only if at least one pre-registered fresh route (confirm, test_old, or a forward window) has
  n_eff ≥ 200, every gate power is ≥ 0.5, and the joint power is ≥ 0.25 (F9 reconciliation, section 12).
- **Stage L n_eff (F6):** "The evaluator recomputes Stage L n_eff from the realised OOF day counts on P30-usable rows,
  label-free." The card 5.1 values are the prediction: 15m 3,040; 30m 2,012; 1h 1,553; 2h 776; 4h 310; 1d+1w ≤ 233.
- **Fresh n_eff:**
  - filters use confirm 2018-01..2025-09 under r11-v2.1, card-method gold judge 15m 292, 30m 193, 1h 149, 2h 74,
    4h 30, 1d+1w 22;
  - RL-X1 uses test_old (R-d), counted on tier-usable rows;
  - no forward window is pre-registered here (section 10, U1).
- **z_g:**
  - Stage L: Holm rank 1 of m = the cells passing S0-2, at α 0.10;
  - confirm: "m_j = max(3, members already recorded for 2027-01-08 + this card's S0-2 passers)" on the ADR 0085
    year-1 ledger (z ≥ 2.87).
- **RL-X1 (quant Q3):** the 1.2 R sd floor applies as written. RL-X1 in fact fails on the test_old n_eff floor. A
  paired-difference sd of sd_Δ = 1.5·√q̂ (q̂ the label-free share of episodes where the policy's action can differ) is
  noted as a future ADR proposal. It is not used here.
- Also reported, not gating: sr-v3 5.1 power at the label-free keep share k̂ (evaluator C5).

**S0-4 ABSTAIN (label-free).**
- Fewer than 100 kept OOF rows or 30 kept days. ABSTAIN is 0 K.
- **F8:** "S0-4 counts only from a column-projected read (`k_<learner>`, date), with the column list logged."

**S0-5 Leak and sanity suite**, run only for each cell passing S0-1 to S0-4:
- the sr-v3 N25 leak suite and the N6 shuffled-label keep check;
- SEQ-1's causal-window prefix and scramble tests;
- CAS-2's OOF-only stacking test;
- **sr-v3 N3 (top-decile net) is not run; it reads outcomes against sealed scores.**

**Predicted Stage 0** (the evaluator's run is the record; S0-3 is listed first):

| Unit | S0-3 Stage L power (Holm z 2.75) | S0-3 fresh n_eff / power | S0-2 filters (ρ needed at σ 1.5), reported | Cells starting |
|---|---|---|---|---|
| 15m | 0.97 | 292 / 0.07: fail | fail (0.55) | 0 |
| 30m | 0.84 | 193 (< 200): fail | fail (0.29) | 0 |
| 1h | 0.70 | 149 (< 200): fail | fail (0.23) | 0 |
| 2h | 0.33: fail | 74: fail | fail (0.20) | 0 |
| 4h | 0.10: fail | 30: fail | pass (0.169) | 0 |
| 1d+1w | ≤ 0.35 at z 1.645: fail | 22: fail | pass (0.12); G-cw and AVG also fail S0-1 | 0 |

- RL-X1 fails S0-3 (test_old n_eff), S0-1 (baseline ii) and S0-2 (by construction).
- agg-v1 fails S0-2: no unit has 2 started experts.
- CAS cells: section 2a.

**Predicted: 0 of 38 cells start, 0 K.**

## 4. Reading rule for a cell that starts (fixed now; used only if Stage 0 starts a cell)

**Population.**
- Learn OOF 2012-17 (CAS-2: 2013-17), expanding yearly folds, full-horizon purge, gap max(1 d, max H over the unit's
  tfs).
- Rows: P30-usable rows (rev 9 D1). Comparisons with L-cw, B8 or another learner use the intersection, and its count
  is reported.
- Cost: the ub95 cost of record srv3-cost-2b3628372a9b3bfa, at the worse broker. Every read states "S at the
  worse-broker cost". Statistics use `net_read`; keeps use cost(x).

**Stage L, a screen (N26: 2012-17 is seen; no go decision).** A filter cell CONTINUES iff all of these hold:
1. S ≥ +0.05 R with a one-sided 95 % day-block lower bound > 0 (BOOT 10,000, mean block 5 days, seed 20261206).
2. Kept mean net > 0.
3. Gross skill k × (kept gross − all gross) ≥ 0.
4. Kept mean − matched twin (B7) ≥ +0.05 R.
5. Holm across every started cell of this card, on max(p_S, p_ctrl) ≤ 0.10.
6. S ≥ 0 at 2× cost with the keep set fixed (1.5× reported).
7. Beats B1, B8, B0 and max(B3, B4, B5, B6) on point S, and beats buy-and-hold over the OOF span after one round trip
   (ADR 0106 decision 4).
8. Against G-cw (HB, AVG, SEQ-1, CAS-1, CAS-2): paired D per OOS session day > 0, with a one-sided 95 % day-block
   lower bound > 0, on shared rows. The Holm family is the started non-G-cw cells, at 0.10.
9. Seed stability: G-cw, AVG and CAS-1 pass `model_quality.seed_stability`. For SEQ-1, seed SD < 0.025 R, otherwise
   INCONCLUSIVE (a NULL).
10. Parameter shift (G-cw, CAS-1): S ≥ 0 at every point of the ±10 % grid.
11. No N22 veto. Gate 10 counts hold.

**Every read reports**, whatever its verdict:
- S by year;
- the share of rows where the ECR term bound the cost;
- the S split (gross skill, cost avoidance, base). A pass whose gross-skill CI includes 0 is labelled
  cost-avoidance-led;
- £/day at 0.01 lot;
- for 15m, S at the vantage cell charge (descriptive, never the gate);
- the losers next to the winners.

**Stage C, T and H.**
- Confirm: the batched read of 2027-01-08 12:00 UTC (members and shas by 2027-01-06 23:59 UTC). Holm within the batch
  on the ADR 0085 ledger. Gates 1-4 and 6-8, plus sr-v3 gates 9-12. Gold confirm holds only 2018 under r11-v2.1, so
  gate 12 cannot be met for gold.
- test_old is read once, passers only.
- The holdout 2025-10-01..2026-09-30 stays **sealed**. Nothing in this card reads, scores, refits or tunes on it.

**What a pass licenses.** Only what sr-v3 5.3 licenses. **Real money: owner-only.**

## 5. RL-X1: reward, cost and shield (fixed now)

- **Episodes.** One per dense T2 entry (both sides). A/A-consistent rows only (R-d), learn 2010-17, the section 4
  folds and gap.
- **Actions** at each bar close: hold; close at the close; breakeven (move the stop to entry, once, and only while
  the close is in profit, side-oriented, `close_r > 0`).
- **Shield:** the T2 stop (−1 R), the target (+2 R) and the time exit at H always stand. Never widen, never remove.
  Never live (`live: Literal[False]`, no engine import, `require_servable` refuses it).
- **Reward = net R** = exit gross R (mid, side-oriented; a gapped stop fills at the bar open, a gapped target at the
  target) − round trip at the **ub95 cost of record** (spread once + commission + slip_x, srv3-cost-2b3628372a9b3bfa,
  worse broker) − overshoot on a stop exit − swap of every rollover night held.
- **Cost version (F4c):** "A gate read refuses costs=None and any cost version without the `srv3-cost-` prefix
  (test)." Today `rl_exits.episodes_from_bars` and its caller default to `SimCosts.w2()` (rl_exits.py:133, 210).
- **Fit:** fitted-Q as `rlx1-v1` (30 iterations, ridge 1.0, fixed quadratic basis, seed 20261126). No grid.
- **Baselines:**
  - (i) the fixed T2 exit;
  - (ii) the best fixed template T0-T7, chosen on each fold's training years only. Not implemented today: S0-1 fails
    until it is implemented and tested;
  - (iii) the shuffled-state control.
- **Gate:**
  - "Δ vs max(baseline i, baseline ii) per fold" ≥ +0.03 R, with a one-sided 95 % session-day block lower bound > 0;
  - policy mean net > 0;
  - Δ vs the shuffled-state control > 0;
  - Δ ≥ 0 at 2× cost: every cost term doubled (round trip, ub95 slippage, overshoot, every bar's swap), baseline (ii)
    re-chosen on the training episodes at that cost, the policy not refit (label-blind, K 0; quant ruling on #489);
  - Holm with the card's started cells;
  - the fresh read is test_old once (R-d).
- **Why it cannot pass here (F4d).** On zero-gross entries the price at mid is, to first order, a martingale, so by
  optional stopping any exit policy's expected gross is 0. "Policy net > 0" then needs a round trip ≤ 0, which is
  unattainable on dense entries by construction. The proper population is entries with shown gross. That is a new
  card.

## 6. agg-v1: experts, rounds and the futility record (fixed now)

- **Experts:** the started learner cells of the unit, plus keep-all and abstain.
- **F3:** "The history run uses each expert's out-of-fold keep (`k_` columns, fold model trained on earlier years
  only). The frozen v0 keep is used only in the forward run."
- **Rounds** are session days. The loss of an expert is −(net R it keeps that day). AdaHedge (de Rooij et al. 2014),
  with no tuning constant. The aggregate keeps iff the keeping weight is ≥ 1/2.
- **History run:** warm start on OOF 2012. Runs are the calendar years 2013-2017. Weights use only earlier closed
  rounds.
- **Futility record (kill):** abstain holds the top weight at the close of 4 consecutive runs. agg-v1 is then retired
  for that unit, the same day, with no confirm.
- **Gate:**
  - an anytime-valid confidence sequence (Howard et al. 2021) on the cumulative net R of agg-v1 minus the comparator,
    "equal weight over the learner experts plus keep-all, excluding abstain", with a lower bound > 0 at the end of
    2017;
  - plus Stage L gates 1-6 on the aggregate keep;
  - for Holm, "p = min(1, 1/E_T) from the confidence sequence's e-process at the end of 2017";
  - read only with ≥ 2 started experts.
- **The forward run** (2027Q1, ADR 0099: 3 K) is a separate card.

## 7. K and budget

| Item | K per cell | Cells | Worst case | Predicted |
|---|---|---|---|---|
| Stage L, G-cw / HB / AVG | 1 | 18 | 18 | 0 |
| Stage L, SEQ-1 head | 1 | 4 | 4 | 0 |
| Stage L, RL-X1 | 1 | 6 | 6 | 0 |
| Stage L, agg-v1 | 1 | 6 | 6 | 0 |
| Stage L, CAS-1 / CAS-2 | 1 | 4 | 4 | 0 |
| Stage C, one per continuing cell | 1 | ≤ 38 | 38 | 0 |
| Stage T / H | 1 per passer | — | (cannot occur for gold under r11-v2.1) | 0 |
| **Total** | | | **76** (38 in 2026Q4, 38 in 2027Q1) | **0** |

- **Booking.**
  - One registry row per started cell (`zoo-v2:<learner>:<unit>`, `seq1-head:<unit>`, `rlx1:<unit>`,
    `agg-v1:<unit>`, `cas-1:<unit>`, `cas-2:<unit>`; rule_ref this file).
  - It is written after `check_budget` and before any read statistic.
  - Booking order if the budget binds: G-cw, HB, AVG, SEQ-1, CAS-1, CAS-2, RL-X1, agg-v1, slow units first.
- **2026Q4:** last recorded 73 free; after commitments about 55 are uncommitted, so 38 fits. The ops-VM registry is
  the count of record.
- **2027Q1 (F10):**
  - Worst case = ADR 0099's 164 − 4 (its zoo-v2 rows; the agg-v1 3 K is the separate forward card, section 6) + 38 = **198**. That is above the 180 discretionary
    freeze.
  - Cells refused for budget are NOT STARTED at 0 K, in the booking order above. The budget is not raised.
  - (The quant's 191 was computed with 34 cells.)
- **K rules carried.**
  - A zoo read of an sr-v3 unit under another cost version is K +1, and the first read stands.
  - Changing a recipe, a loss or a hyperparameter after any look is K +1.

## 8. Kill rules, dates and owners

| Date | Step | Owner |
|---|---|---|
| 2026-11-28 | Card rev 2 | research director |
| Last signature | Quant and evaluator sign | quant-reviewer, evaluator |
| **The day after the last signature, no later than 2026-12-04** | **Stage 0 runs and is recorded; the closure fires that day** | evaluator; research director |
| 2026-12-18 | Stage L reads of started cells (if any) | ml-researcher (fit), evaluator (read) |
| 2027-01-06 23:59 / 2027-01-08 12:00 UTC | Confirm members / batched confirm | evaluator |

**Kill rules:**
- **F5:** "All filter cells NOT STARTED retires them as UNTESTABLE at the cost of record on gold 2010-25 history (no
  Stage L read; not a NULL; N10 not invoked). A NULL needs a Stage L read."
- A Stage L NULL closes the cell. It is not re-run, widened or re-parameterised.
- A SEQ-1 NULL retires the sequence family (R6).
- An RL-X1 NULL retires fitted-Q exits on dense T2 entries.
- An agg-v1 futility record retires it for the unit.

**At closure:**
- `ZOO_V2_CARD` stays `None`;
- SEQ-1's label head, RL-X1, agg-v1 and the CAS cells are not fitted on gold;
- the sealed files are kept for audit and read only under a new card with a powered fresh route.

## 9. Integrity

- **Pre-registration.** Written before any sealed score was opened. The only outcome statistics behind it are the
  seen L-cw/B8 learn read and its re-derived take-all means (section 0, command above).
- **Holdout.** Sealed, untouched. Confirm and test_old are counted (outcome-blind), never read, at Stage 0.
- **Controls.** Every started cell is read against the matched twin (B7) by day, side and hour.
- **Sealing (F8).** "Backlog 277 merged (acceptance 1-4) is an S0-1 precondition for any cell. The evaluator checks
  the §9 file sha256 before Stage 0. S0-4 counts only from a column-projected read (`k_<learner>`, date), with the
  column list logged."
  - Today read mode writes `oof.parquet` with the withheld learners' `v_`/`k_` columns next to `net_read`. This card
    did not open them.
  - File sha256 of each `oof.parquet`:
    - 15m 0ee9966cec5c336c7ac6eadd2dfb2175f773cf4ccadd3805fdf6dc9da9dc8549;
    - 30m 0a25a24201973f6e3990a1dc09a94e1ddea50deb1db72544e67c9b15563d77b0;
    - 1h 43d5708dd70e59d0c1d91f2c53fbac84e8fd8525dc825807809483e9611a7c27;
    - 2h 728f1eb1dfdbe27d97d9886567d969a186a7125db78278ee8fa760fb0ae223ce;
    - 4h 87721da808ba6e30bc571e64083c1c4699836b06257cb50434ffd7292dad9937;
    - 1d+1w dcdde06df5859c586be84b81db70e2ce46f4df6486cc2d12c50871d033954fb9.
- **`ZOO_V2_CARD`** opens all three learners at every unit at once. It may be set only with backlog 277's per-cell
  allowlist. Under the predicted Stage 0 it is never set.
- **Deflation.** At the registry K of the read day, reported.

## 10. What would reopen this, and what it exposes

**Reopening routes (F2).**
- **U1.** "ADR 0105 fallbacks only: (1) a second independent 1m gold source graded under r11-v2.1; (2) a forward
  window from 2026-10-01 (N12 reserve weeks), pre-registered with these gates. Each is a new card."
- **U2.** The pooled body (gold + FX10 + XAG, after D-12-xt with a measured FX cost). "U2 is a new card (new
  population)."
- **U3. DEEP-DAILY.** Gold 1978-2002 daily is a pre-learn window, so it needs its own card. It is the one route where
  cost per candidate is small (1d+1w: 0.054 R).
- **U4. A cheaper cost of record** (a measured re-cell, n ≥ 100). It is a new version, so a read is K +1.

**What it exposes (rule 1 gap).**
- On dense gold entries at 15m-4h, the ub95 cost of record is the whole loss: 0.95 R per candidate at 15m, 0.25 R at
  1h. No learner or combination of learners on these inputs closes that.
- The money levers are:
  - cost itself (why the 15m ub95 round trip is about one 2012-17 15m ATR);
  - longer holds, where cost per R is small;
  - populations with shown gross (the owner's box break+close + MFI trades), not dense bars.
  - That is also the population RL-X1 and the cascades need.

## 11. Owner-only

None assumed. No sizing, risk limit, spending or live decision is taken. Real money stays owner-only.

## 12. Reconciliation texts and resolved questions

**Proposed amendments (F9; for the quant to land in the ADRs they amend):**
- **ADR 0085:** "The start rule applies per cell (procedure × judged unit, N18). A cell starts only if at least one
  pre-registered fresh route (confirm, test_old, or a forward window) has n_eff ≥ 200 and every gate power ≥ 0.5."
- **sr-v3 §5.1.4:** "Below its row at Stage L: NOT STARTED at Stage L. No powered fresh route under ADR 0085: NOT
  STARTED entirely, with no Stage L read."

**Resolved by the quant review of PR #521:**
- Q1: S0-2 is restated per F1. It is reported, never the sole reason.
- Q2: ADR 0085 applies per cell.
- Q3: the 1.2 R sd floor applies to RL-X1 as written. The sd_Δ amendment is a future ADR proposal.

**Open (evaluator):**
- the realised Stage L n_eff per unit (F6);
- RL-X1's test_old tier-usable counts;
- the K headroom on the ops VM;
- the §9 sha check before Stage 0.

## Sign-off

- Research director: 2026-11-28 (rev 1); rev 2 the same day (quant F1-F10, owner cascade cells).
- Quant-reviewer: rev 1 CHANGES (F1-F10), applied in rev 2; re-verify pending.
- Evaluator: pending.
