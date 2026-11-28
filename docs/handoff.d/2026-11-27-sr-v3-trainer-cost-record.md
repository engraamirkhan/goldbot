- sr-v3 trainer on the D-3 cost of record (quant F6 on #515) and the D-12-xt rho guard (code review of #514).
  `sr_v3_store.cost_table(manifest)` builds the trainer's `CostTable` (the training script and the run_manifest
  rebuild, `sr_v3_report.build_artifact`, both use it); for a version recorded under
  `srv3_cost_record` it verifies the table files from their bytes and charges gold rows the record's own
  `gold_row_bp` (slip_x included), and refuses a recorded manifest without its gold_spread_table.
  `StoreData._learn_frame` passes every label load through `srv3_cost_record.apply`, so the stored gate status is
  the record's (gold 15m-1w gate-eligible on cost; 1m/5m blocked "(D-15)"; non-gold blocked). The guarded fit path
  (`sr_v3_train`) never imports `srv3_cost_record`: the charge is injected as `CostTable.gold_charge`. A recorded
  table loaded with plain `CostTable.from_manifest` keeps the legacy charge, which `apply` refuses (fail closed).
  `srv3_cost_record.py` is in the trainer's CODE_FILES.
- Pooled Stage 0 (`sr_v3.rho_inputs`, body != gold) refuses a rho file whose `exit_freeze_sha` is not xt-v1; the
  caller passes `dense_labels.EXIT_FREEZE_SHA256` (`stage0(..., freeze_sha=)`, scripts/research/train_sr_v3.py), and
  no sha refuses. Gold reads neither rho_u,inst nor tau_a and is unaffected. Until D-12-xt re-runs the grid at
  xt-v1, pooled Stage 0 is refused (quant ruling on #514).
- Read rules (code review of #515, LOW): gold rows before the first complete spread month have no era term, so
  `apply` refuses them; the spread table of record starts at 2003-05, so a test_old (2003-09) read drops or embargoes
  rows decided before 2003-06-01 first. The learn window (2010-17) is fully covered. The record's content
  sha256 uses `pd.util.hash_pandas_object`: a pandas upgrade that changes its hashing makes every read refuse (fail
  closed) until the record is re-issued under a new version.
