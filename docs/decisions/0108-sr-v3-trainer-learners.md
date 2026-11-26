# 0108: sr-v3 history trainer: one learner interface, GBT baseline, Stage 0 readings

Status: proposed (principal software engineer, 2026-11-26). Quant sign-off needed on decisions 3-5 before the first
real run. The run itself is blocked until PR #482 (A4) and PR #484 (X6) are merged.

## Context
The sr-v3 card (docs/research/prereg-2026-11-26-sr-v3.md, rev 6) specifies Stage 0 and the L-cw body. Three later
inputs widened the trainer's scope:
- SYNTH-ml (2026-11-25), rulings R2 and R4 and items 3 and 10: the zoo-v2 core of L-cw, G-cw GBT-slow, HB, AVG and
  the D-40 dense prior, with B6 and SEQ-1 as conditional slots.
- ADR 0106: GBT is the baseline, at least 5 seeds, a parameter-shift check, gapped time splits, past-only
  normalisation, a run log that rebuilds byte-exactly, and a buy-and-hold benchmark.
- The quant's ordering constraint on X6 (PR #484).

The card text does not settle every arithmetic choice, and the coded ladder needs each one fixed before any label is
read.

## Decision
1. **One learner interface** (`goldbot/research/learners.py`):
   - each learner produces a frozen JSON artifact with a SHA-256, plus `value` and `value_row` with the same arithmetic;
   - kind "gross" keeps iff ĝ − cost(x) > 0; kind "p" keeps iff p_w ≥ 0.5;
   - L-cw, G-cw, HB and AVG are built; FS is off; B6 and SEQ-1 are registered slots that refuse to fit.
2. **G-cw is the baseline.** Every training report gives each learner's paired D per OOS session day against G-cw,
   with a day-block band. D is reported, never judged. Under the card, G-cw cannot be the judged sr-v3 body (card 3.3
   bans GBT for sr-v3). It is trained and reported as the zoo-v2 baseline, and zoo-v2 books its own K.
3. **Stage 0 readings of Q1** (each is recorded in the Stage 0 report):
   - "Kish n_eff" = Σu × the Kish efficiency of the fold weights, with m̄ = Σm²/Σm of Σu per instrument-day,
     floored at 1. Under the rev-3 inputs this reproduces the card's gold ladder for 1m-1h on both folds (test 18 (i)).
   - The pooled unit intercept is charged at Σn_i / [1 + (k − 1)ρ_u,inst].
   - Unmeasured ρ_x (side, columns outside core-12, tfs 2h/1d/1w) = 1, the rev-3 floor.
   - ρ_u,day keeps ADR 0085 Q1's floors: 0.05 for holds ≤ 60 min, 0.25 above.
   - The pooled column of the rev-3 table could not be reproduced: the card does not state its per-instrument inputs.
4. **Data handling.**
   - L-cw and HB are fitted from additive moments over every row.
   - G-cw and D-40 are fitted on a deterministic Horvitz-Thompson sample of at most 2,000,000 rows (weights / π).
   - cost(x) per fold is affine in that fold's p_stop and night share, so it is exact per fold.
   - The gap before a test year is `windows.label_embargo(H)` = max(1 d, H).
   - Inputs are imputed with the medians of the 2010-11 fold and standardised per fold on training rows only.
5. **D-40** feeds G-cw only. It is cross-fitted forward: a row of year y takes the fold-y dense fit, and rows before the
   first OOF year are NaN. The L-cw body stays the card's prefix, and HB shares one column set (P30 minus
   dist_vwap48_atr) across the tf ladder.
6. **Serving.** L-cw exports one `self_retrain.LogitModel` per instrument with tau = 0.0. The sr-v2 rule then keeps
   everything (it never refuses), and `sr_v3_serve.gross_row` gives ĝ with score_row's own operations. Nothing gates:
   sr-v3 is not in GATE_SPECS. The hook needs are listed in `sr_v3_serve.ENGINE_HOOK_NEEDS` for the trading-safety
   PR (card M2).
7. **Run record.** There is no trainer-owned run log. `record_unit` writes one research/run_manifest.py manifest
   (PR #494, ADR 0109) per v0 artifact. Each carries model_quality verdicts:
   - seed_stability over 5 seeds (L-cw and HB are closed forms and are marked seed-invariant);
   - param_shift on the ±10 % / ±20 % grid;
   - beats_baseline against the G-cw manifest.
   The builder is `sr_v3_train.build_artifact`, so `run_manifest.rebuild` refits any model byte for byte.
8. **Core-12** follows the quant ruling of 2026-11-26: rv_ratio is position 4 and atr_ratio_14_100 moves to the rest
   of P30. The D-12a file has no ρ_x for rv_ratio, so the ladder charges it at ρ_x = 1 until the data team measures it.
9. **Refusals.** Labels are read only through `load_labels` on the learn window, with procedure `sr-v3` so every
   read is a consumption row the X6 legitimacy guard sees. The quant sequencing on #482 is binding: "no sr-v3
   label-value read until all three are on main: (1) #484 merged; (2) its one-line follow-up sets `X6_MERGED_UTC`;
   (3) the rev 8 card amendment (rv_ratio replacing atr_ratio_14_100 in core order) merged". The store checks it at
   every label read and every Stage 0 event read (`sequencing_gaps`, `SequencingRefused`), not only the CLI, so no
   caller reads around it. X6 is the last block of the served order.

## Consequences
- The first real run needs A4 recorded (#482), X6 merged (#484), the dl-v1 and fs-v1 stores built under the current
  code hash for every member and tf (2h has no rule yet), and the quant's sign-off on decision 3.
- Seeds affect only stochastic parts. Below the 2M-row cap, G-cw has no random component, and the report says so
  ("seed_invariant") instead of claiming stability.
- B2-B6, the block ΔS table, the arm-fire subset and the Stage L gates stay with the evaluator. The report lists them
  as not included.
