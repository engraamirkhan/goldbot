- sr-v3 card D-3, cost part (2026-11-27, microstructure-researcher). Review not yet recorded: routed to
  `quant-reviewer` (countersigns the freeze) and `code-reviewer`.
  - Today's fit-only gate block "cost_version w2-point: the ub95 cost of record per cell is not frozen (D-3)" comes
    from the label rows, not the trainer: `dense_labels.label_rows` stamps every row w2-point and
    `dense_labels.gate_status` refuses it (quant A4 on #482). The trainer already charged rows srv3-cost-bb199294, but
    the label meta never learnt that.
  - Frozen cost of record `srv3-cost-bb199294fe509df0` (content sha256 bb199294fe509df0…c27db): record file
    `config/research/srv3_cost_record/srv3-cost-bb199294fe509df0.yaml` (table file sha256s, ECR ecr-2026-11-20.2 and
    its sha256, charge rule ub95, history months 2003-01..2025-09, 54 gold cells, class basis per instrument), and a
    recording line in ADR 0098 R-H. The table files stay local (`~/tmp/sr-v3-trainsets/costs`, built by #512).
  - `goldbot/research/srv3_cost_record.py`: `verify(version, files)` refuses an unrecorded version or any file sha256
    that differs; `apply(labels, meta, record, rt_bp=...)` re-stamps measured-class rows (cost_version, cost_rt_bp)
    and re-runs `dense_labels.gate_status` unchanged; prior-class rows (FX10, XAGUSD) keep the D-3 block with the
    instrument named. `dense_labels.py` is untouched, so its code hash and the label store keys hold.
  - Next, on #488: `StoreData._learn_frame` calls `apply` after `load_labels` when `self.costs` is set (rt_bp from
    `self.costs.rt_bp`), and `srv3_cost_record.py` joins the trainer's CODE_FILES. Gold units then block on D-4 only.
