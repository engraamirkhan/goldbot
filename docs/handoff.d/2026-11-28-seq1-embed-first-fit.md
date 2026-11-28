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
  Quant review of #489 (2026-11-28, fixed in this lane): `seq1.read` and `rl_exits.run` are gate reads - refused
  before scoring unless the read id is booked (`windows.require_booked_read`, family registered with the signed study
  plan) and every row is charged at one D-3 cost of record (`gate_cost.require_cost_of_record`); RL costs are never
  defaulted; RL-X1 adds baseline (ii) (best fixed template T0-T7 chosen on training episodes), `delta_vs_best`, a 2x
  cost read, episode decision/end times with a train/test embargo refusal and the per-row swap of record
  (`swap_rate`, = cost_parts per night); agg-v1 applies a round only after it closes (`replay`), `history_rounds`
  refuses non-OOF keeps, `mix_keep` takes n; embeddings carry `train_end`, `embed_after` refuses rows inside
  train_end (window_from_utc > train_end), `fold_embed` is forward-only; re-verify B1/B2: the day-block bound is
  also against the better of baselines (i)/(ii), and a booked id must equal the read's `read_key` (spec, costs, data);
  trading-safety: size-v1 now runs under the live limits (2 % day, 5 % week, 8 % size-down, 12 % halt, 1 % max risk;
  validators refuse looser specs, `simulate` re-validates), reserves a gapped stop plus cost before each trade and
  counts breaches; the services and the API load no history model and no torch (subprocess test); ADR 0107 point 1 restated and accepted. Lesson: never edit
  `decision_cost.py` (or any `dense_labels.CODE_FILES` file) in a non-label change: it re-keys every built partition.
  Fits (gold xt-v1, learn 2010-17, 8 threads, commit e54c3f33, ~/tmp/seq1-embed-2026-11-28b, local; pretext loss
  fit / val / out of fold, baselines on the same out-of-fold rows: zero, fit mean, trailing range):
  1h - 47,280 rows, manifest 73341c09..., rebuilt byte-identical; 0.881 / 0.996 / 0.887 vs 2.02 / 0.987 / 1.103;
  5-seed mean linear CKA 0.925 (min 0.912). Pooled 1h+2h+4h+1d - 87,252 rows, manifest 51167a36...; 0.872 / 0.953 /
  0.881 vs 2.00 / 0.977 / 1.158. The learned pretext beats the best constant by about 10 % out of fold. No label
  read, no K.
