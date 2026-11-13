- 2026-11-13, ML review cleanup (branch feat/ml-cleanup-snapshots; R143; BACKLOG 227-230; ADR 0080;
  `docs/research/ml-review-2026-11-13.md`). Four changes, none on the order path.
  - **Arm shares.** The nightly exploration learner now writes equal shares among each style's enterable arms. The
    5 % floor, the 30 % cap and each arm's own ceiling are unchanged, and retired or not-configured arms stay at 0.
    The settings switch is `research.exploration.allocator: equal`; the Thompson bandit stays selectable as
    `thompson`. The evidence: bandit +0.085 R against equal +0.086 R, rank correlation 0.003.
  - **H-62 meta-labeller.** Its Sunday retrain is retired (`research.exploration.meta_labeller: false`; 0 model rows,
    nothing trainable).
  - **Specialist retrain.** The Saturday LightGBM retrain of the specialists is off (`research.specialist_retrain:
    false`; shadow AUC 0.44 on 145 trades, Brier worse than the base rate, 0 live agents). Challengers already in shadow
    are still judged. The recalibrate, drift and model-watch jobs are unchanged.
  - **Feature snapshots.** `nightly_costs` now ends with point-in-time default-feature snapshots of the gold
    exploration candidates (`goldbot/research/feature_snapshots.py`).
    - The bars are the account's stored minutes visible by each decision.
    - It runs up to 600 decision bars a night, newest first.
    - The rows go to aux.db migration 30 (`exploration_feature_vectors` / `exploration_feature_snapshots`).
    - The engine never reads them.
    - The job reports `coverage_1d`.
    - Migration 30 may need renumbering if the weekly/monthly review lanes (30/31) merge first.
