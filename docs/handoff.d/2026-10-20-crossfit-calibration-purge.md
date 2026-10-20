- Calibrator prior-set purge (ML leakage audit 2026-10 F2; BACKLOG 106 on `research/ml-leakage-audit`): the
  cross-fitted calibrator (`research/pipeline.py` `_cross_fitted`) now fits fold k's map only on earlier folds' rows
  that are also in fold k's purged `train_idx`, so a label that exits inside fold k's test window (or within its purge
  before it) never calibrates fold k. CPCV (`research/cpcv.py` `path_selection`, now with a required
  `calibration_train=`) passes `purged_train` with group g as the test group; the label-free tercile cut is unchanged
  (quant ruling 1). Walk-forward's dead embargo branch is removed and documented as a no-op (F9). The default-rule
  golden (`tests/data/tercile_default_golden.json`) was re-captured: walk-forward p moved by at most 0.021 with no
  selection flipped, CPCV paths took 2,378 trades instead of 2,325. Recorded verdicts are not re-read. Needs quant review.
