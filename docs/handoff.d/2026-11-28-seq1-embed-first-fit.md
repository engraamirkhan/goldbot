- 2026-11-28 SEQ-1 / RL-X1 on the frozen exits and cost of record (PR #489 after D-3 / D-4 merged):
  `learn_labels.load_learn` reads only rows stamped with the D-4 exit freeze xt-v1 (`NotExitFreeze` otherwise) and
  `xt_keys` resolves the one xt-v1 partition per learn year; with `costs` (`sr_v3_store.cost_table(manifest)`) every
  read is charged through `sr_v3_store.charge_of_record`, now the single module-level route the sr-v3 trainer's
  `StoreData` also uses (gold rows exactly `gold_row_bp`, gate status re-derived by `srv3_cost_record.apply`); without
  costs the D-3 block stays. RL-X1: an entry charged at the cost of record pays its own `cost_rt_bp`
  (`Episode.rt_bp`, kept in the episodes file so a rebuild charges the same) instead of SimCosts.rt_bp; a separate
  `slippage_ub95_bp` on such rows is refused (the record's ub95 already carries slippage); overshoot and swap stay the
  simulator's, as sr_v3_train.cost_parts. `seq1_embed` reads no label and needs no cost. New runner
  `scripts/research/train_seq1_embed.py` (outcome-free, no K): decisions from the xt-v1 skeleton, learn-year minutes
  only, one purged outer fold (pretext loss in/out of fold beside the zero-prediction loss; optional 5-seed CKA), then
  the recorded fit via `history_runs.record_seq1_embed`. macOS rule: torch must be imported before sklearn/lightgbm
  (each bundles libomp; two runtimes segfault the first multi-threaded epoch) - `seq1.TorchState` now refuses more than
  one thread when `omp_before_torch()` names a clash, and the runner and rebuild_model.py import torch first. SEQ-1
  judges 1h-1d only (15m is refused by design, R11).
