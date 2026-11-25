# Pre-registration and design card: multi-timeframe learning (H-102, spec `mtf-v1`), revision 2

ML researcher (writer), research director (revision 2026-11-25, rulings D9-D11 of the execution plan of 2026-11-25).
**Written before any data under this card was read:** no label, feature row or score under `mtf-v1`. Revision 2
replaces the draft of commit 3f66aeac before any read, so it costs no K. The draft's plumbing detail (its sections 3,
5 and 9) stays the build reference; where the two differ, this text decides.

**Status: proposed. The quant-reviewer signs section 9 before Stage 0.** Line 3 of the five research lines. This card
dispatches no trial, books no K and does not touch the holdout (2025-10-01..2026-09-30).

## 0. What the card does, in three parts (ruling D9)

| Part | What | K | Condition |
|---|---|---|---|
| (a) History-pretrained fits | every (timeframe x asset class) cell with history gets a base model fitted on learn history, then live-updated | **0**, only as a fit-only run under quant Q2 (no OOS, validation or net-R metric computed, persisted, logged or shown; no status, weight or decision change; frozen spec; a registry `kind=fit` row of weight 0) | always (owner rule A4) |
| (b) **One judged unit: gold 1h+4h pooled** | the filter of section 3, judged on history, then forward | **≤ 5** | runs regardless of H-101: it is the only unit where the cost lever works |
| (c) Further judged units | gold 15m, 30m, 2h, scalp; fx, index, energy, crypto (one unit per class, never pooled with gold) | per unit, booked when started | only if OMB-1 continues **or** (b) passes Stage L. **H-101 was NULL on 2026-11-25, so (c) is off today** |

**Every timeframe is kept (owner rules A3, D2 of the draft).** 1m to 1w keep their rows and labels. The draft's new
research-replay rows (F1A30, F1C30, IEX30, ML2H, MP2H, MC2H, MD1W, MC1D, MC1W) are recorded at 0 K (quant Q4) in
`config/research/multi_tf_arms.yaml`, which the engine never loads (a test pins it). No new rule body; no red-box,
tsmom or box row on a new timeframe.

## 0a. H-101 result (2026-11-25), recorded before any H-102 read

- **H-101 `zoo-v1` Stage L: NULL, nothing continues, K 8 booked** (registry 6,990 -> 6,998).
- Re-derived by the research director from the Stage L record (local, not in the repo), not relayed: 104,367 OOF
  candidates over 1,548 days for every candidate model; edge vs no-model +0.0040 to +0.0065 R for B1, B2, B4, B5, F1,
  F2, F3 (B3 −0.0001 R), against the +0.05 R bar; kept mean net −0.029 to −0.034 R; keep share 0.69-0.73; Holm p
  1.0 for all eight; shuffled-label AUC 0.497-0.502. The implied all-candidate mean is about −0.037 R (kept − edge),
  matching the labelled −0.036 R of the plan.
- **Scope of the null** (ML synthesis): an unweighted sign objective, keep about 2/3, sr-v2's 40 inputs, 10 retired
  intraday gold arms. It does **not** retire trees, the money objective (ADR 0098) or new information.
- **Consequence here:** part (c) loses its H-101 route and stays off. Part (b), the gold 1h+4h unit, runs as
  pre-registered. Its prior is not raised; if anything the null on 104k rows says price-only filters on these arms
  sort almost nothing, so the unit's value rests on the cost lever and on OMB-1's block.

## 1. Card (research-director rule 2) for the judged unit

| Item | Value |
|---|---|
| Mechanism | A filter can only earn where cost per R is small next to the spread of outcomes it can sort. At 1h and 4h the stop is wide, so cost per R is a fraction of the 1m-15m figure, and slow context (higher-timeframe state, the OMB-1 block) has time to matter |
| Expected gross lift on kept rows | +0.03 to +0.06 R (SYNTH-rd: kept gross about +0.06-0.10 R vs the unit's labelled gross) |
| Cost per trade | 1h 0.06-0.09 R, 4h 0.032-0.046 R (W2 bps, 1 ATR stop, 2014-17); D9 cites 0.002-0.03 R. The cost gate (section 3) uses the W1-M measured cost of record |
| Trades | about 500 candidates a year over 7 arms, about 2 a session day; about 1 a day kept |
| Net £/day at demo size, if it passes | +0.05 R x about 1 a day x £11-26 per R at 0.01 lot = **about £0.5-1.3 a day**. £50 a day needs a judged edge **and** size, and size is the owner's |
| Prior | about 3-5 % (in-house meta-model AUC about 0.50-0.525) |
| Budget | K ≤ 5 (2026Q4); about $40 of agent tokens; compute on the Mac or ops VM, never the brain |
| Dates | revision 11-28; Stage 0 by **12-11**; Stage L by **12-18** (joint with OMB-1, possibly 12-09); kill date **12-18** for Stage L |
| Owner | ml-researcher; the evaluator reads |

## 2. Owner rules A4-A7 as testable invariants

- **A4 history first.** No model serves or updates without a history base: serving refuses `history_rows == 0`.
  Each artifact shows `history_rows`, `history_from` and `live_rows`. A cell with no history opens
  `history_missing:<cell>`. There is **no live-only fallback**. sr-v2 (live-only) stays as signed until sr-v3
  replaces it.
- **A5 objective.** Every model uses obj-v1 (ADR 0098): net R incl. latency and swap, clip ±10 R, weighted logistic
  w = uniqueness x min(abs(net R), 10), keep iff p_w ≥ 0.5, **no keep floor** (the draft's [1/3, 1] clamp and its
  3 R cap are withdrawn, quant R14 as amended by D1-D2), ABSTAIN below MIN_KEPT. Selection and gate on
  S = mean(net R x keep) − max(0, mean(net R)). AUC is a diagnostic only.
- **A6 SOTA and RL.** The shortlist cites the ML SOTA review of 2026-11-25. RL here is limited to the allocation
  among gate-passed arms (section 6) and the exit bandit RL-1 (its own line). PPO, DQN, end-to-end deep RL and RL on
  live fills are declined this quarter (D12).
- **A7 data scale.** Per cell, real, phase and synthetic rows are shown separately. n_eff counts uniqueness of real
  rows only; synthetic rows count 0.

**Guards (quant R7-R10; all are tests that raise):**
- **R7 dense labels:** training only (pretraining or an auxiliary target). Never the judged population, never in
  n_eff, sd or power. Uniqueness-weighted. Controls never filter training rows.
- **R8 phase offsets:** training only, weight 1/P; all phases of a span are one series; purge and embargo across
  phases on calendar time; judging on the engine's live grid only (phase 0).
- **R9 synthetic or augmented rows:** training only (D11: phase offsets, block bootstrap, cost-ratio resampling at
  0 K). Never in OOS, calibration, threshold, early-stopping or validation folds, n_eff, sd, power or DSR. The
  generator is fitted inside each training fold only and must match real VR(2-96) and the ACF of r and abs(r). A
  `synthetic=1` column, and a guard on judged and calibration frames. Cross-asset real rows are real data, not
  augmentation. A judged augmentation ablation (K 1) only after a base passes.
- **R10 sources:** `em_atr_cost`, `spread_atr` and the tick columns are out of judged inputs unless re-standardised
  per source; PSI < 0.10 Dukascopy vs broker per served input.
- **Windows:** history priors and bases from learn 2010-01..2017-12 only (ruling S2: the draft's H-60
  `prior_from_history` on 2019+ rows is withdrawn). test_old once per family; confirm read once; the holdout is
  refused by the shared guard on every loader (S1). Embargo = max(1 day, the longest max hold).

## 3. The judged unit (b): gold 1h+4h

- **Population:** history replay of G1A, PDC1, MB1H (1h) and DON4, PDC4, KSQ4, BXF4H (4h), each with its matched
  control (VEX1H/IEX1H, W0); cost of record in bps; one Friday-cut rule; price-scaled stop clamps.
- **Inputs:** OMB-1's model B (one frozen block used twice, ruling D10): the gold-price inputs plus the block subset
  chosen by OMB-1's size ladder. One model per unit; no grid.
- **Stage 0 (0 K, counts and costs only):** the leak suite (T1-T9 of the draft plus the seeded future-release test);
  the cost gate per timeframe (cost of record ≤ 0.035 R, as OMB-1 section 4); the size rule; power-first. A refusal is
  NOT STARTED, 0 K.
- **Size, quant Q6:** the 40-feature cap counts distinct source columns; p_eff (coefficients incl. one-hot levels,
  interactions and copies) must meet Riley et al. 2019 at shrinkage ≥ 0.9 on n_eff. At anticipated AUC 0.57 that is
  about 4,400-4,500 n_eff for p_eff 8 and about 2,200-2,300 for p_eff 4 (re-derived for this revision). The unit's
  learn rows are about 4,500 raw before uniqueness, so **level 2 (p_eff 4) or a refusal is the likely outcome.** The
  draft's 30m-4h units with p ≤ 10 fail this rule at AUC 0.57 and are withdrawn. An anticipated AUC of 0.60 would
  need written justification; none exists in-house.
- **Stage L (K 1), out-of-fold 2012-17.** CONTINUE needs all of:
  - S ≥ +0.05 R with a one-sided 95 % lower bound > 0 (stationary day-block bootstrap, mean block 5 days, B 10,000);
  - kept mean net R > 0 and kept gross ≥ 0;
  - kept mean minus the side-matched control mean ≥ +0.05 R;
  - the DSR at the K of record reported beside it.
- **Stage C (K 1, passers only):** confirm 2018-01..2025-09 read once: S > 0, kept mean > 0, p ≤ 0.10.
- **Forward (K ≤ 3):** served in shadow through the W2-3 path, then **two consecutive judged reads of ≥ 100 unseen
  rows each**, both passing the Stage L tests, before any demo influence. For G3 it counts only as an sr-v3 model
  that is ACTIVE by the 2027-01-10 run.
- **Kill:** S ≤ 0.5 x the unit's cost per trade, or kept mean net ≤ 0, at Stage L closes part (b). No Stage L read by
  12-18 also closes it. Parts (a) and the recording rows continue, because A4 needs them. FUTILE-STOP of the draft
  applies to forward reads.

## 4. Part (a): history bases and the live update

- Base per cell on learn history, stored with the window, code hash and data sha256; built off the brain, shipped as
  JSON coefficients with provenance (data manifest sha, commit, `objective_id`); load refuses a mismatch.
- **Live update:** prior-centred L2 (history coefficients as the prior, intercept free), N0 = min(0.5 x the base's
  training n_eff, 1,000) pseudo-rows; recency half-life 30 d (1m), 60 d (5m), 180 d (15m-2h), none above.
- **Feature parity** must pass before a base serves a score (abs 1e-6 or rel 1e-4 on ≥ 99 % of rows, equal NaN).

## 5. Serving and abstention (ruling D2)

Full abstention is allowed. On demo, every abstained candidate is still replay-labelled, and a randomised 10 % sample
of abstained candidates is traded with its propensity logged, excluded from the model's kept-money read and used for
off-policy evaluation only. RiskGate stays the only path to an order. Real money is owner-only.

## 6. Reinforcement

Allocation among gate-passed arms only (draft R1, R2, R6-R8, K 0). Today nothing is gate-passed, so nothing is
reinforced. The allocator card (top-two Thompson or AdaHedge, K +1) is read only when ≥ 2 gated arms per cell exist.

## 7. Refusals and K conventions recorded here (quant Q4-Q6)

- Record-only rows: 0 K. Descriptive tf x class tables are looks: each cell shown is marked SEEN with a timestamp,
  and a card motivated by such a table books K = cells screened, with OOS starting after SEEN. The draft's "upper
  bound +3 in Q4" is withdrawn.
- **1d:** bracket reads are never judged; daily position reads only under quant Q5 (ADR 0085 amendment 2026-11-25),
  on their own card.
- 1w: context only.
- `sd_r_measured_by_tf` never takes rows from the unit's own arms.

## 8. K summary

2026Q4: Stage L 1 + Stage C 1 (passers only) + forward ≤ 3 = **≤ 5**, from the 73 free. Part (a) and recording rows: 0.
Part (c) units: per unit, under the 2027Q1 budget (ADR 0099), only when their condition holds.

## 9. Quant-reviewer signature

Pending: section 0 (D9 split), section 2 guards, section 3 (size ladder, cost gate, statistic), section 7.
