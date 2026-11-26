- **sr-v3 history trainer is built. It has not run on real labels.** The pipeline is Stage 0 (label-blind Riley
  ladder, level and served orders, tie rule, λ_a), then walk-forward fits of L-cw, G-cw (GBT baseline), HB and AVG
  with the D-40 dense prior, then a training report and one run manifest (ADR 0109) per v0 artifact.
  - CLI: `scripts/research/train_sr_v3.py`. Modules: `goldbot/research/sr_v3.py`, `learners.py`, `sr_v3_train.py` and
    `sr_v3_serve.py`. Decisions are in ADR 0108.
  - Quant sequencing (binding): the store refuses every label read and Stage 0 statistic
    (`sr_v3_train.sequencing_gaps`, `SequencingRefused`) until #482 (A4), #484 (X6), card rev 8 (`rv_ratio` at core-12
    position 4) and `price_action_x6.X6_MERGED_UTC` are all on main; the CLI stops before any phase on the same check.
  - Core-12 follows card rev 8: `rv_ratio` replaces `atr_ratio_14_100` at position 4 (charged at ρ_x = 1 until the
    D-12a file measures it).
