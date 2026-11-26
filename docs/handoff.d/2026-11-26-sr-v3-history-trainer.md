- **sr-v3 history trainer is built. It has not run on real labels.** The pipeline is Stage 0 (label-blind Riley
  ladder, level and served orders, tie rule, λ_a), then walk-forward fits of L-cw, G-cw (GBT baseline), HB and AVG
  with the D-40 dense prior, then a training report and run log per unit.
  - CLI: `scripts/research/train_sr_v3.py`. Modules: `goldbot/research/sr_v3.py`, `learners.py`, `sr_v3_train.py` and
    `sr_v3_serve.py`. Decisions are in ADR 0108.
  - The CLI refuses to run until PR #484 (X6) is merged, and `load_labels` refuses until A4 is recorded (#482).
