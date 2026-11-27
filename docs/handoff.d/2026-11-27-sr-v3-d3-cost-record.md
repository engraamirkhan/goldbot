- sr-v3 card D-3, cost part (2026-11-27, microstructure-researcher). Quant review of #515 (CHANGES, F1-F5, F7)
  applied; re-review routed to `quant-reviewer`, and `code-reviewer`.
  - Today's fit-only gate block "cost_version w2-point: the ub95 cost of record per cell is not frozen (D-3)" comes
    from the label rows, not the trainer: `dense_labels.label_rows` stamps every row w2-point and
    `dense_labels.gate_status` refuses it (quant A4 on #482).
  - Frozen cost of record `srv3-cost-2b3628372a9b3bfa` (content sha256 2b3628372a9b3bfa…dfa684): record file
    `config/research/srv3_cost_record/srv3-cost-2b3628372a9b3bfa.yaml` (table file sha256s, ECR ecr-2026-11-20.2 and
    its sha256, charge rule ub95, history months 2003-01..2025-09, 54 gold cells with rt_bp, ECR ub95 and slip_x,
    class basis per instrument, `wait_charged_tfs` empty), and a recording line in ADR 0098 R-H. The unread draft
    srv3-cost-bb199294 (era term without slippage) was never recorded. Table files are local
    (`~/tmp/sr-v3-trainsets/costs-v2`); swap and spread are byte-identical to #512's, the cost table adds `slip_x_usd`.
  - `goldbot/research/srv3_cost_record.py`: `apply(labels, meta, version, paths, *, rt_bp, spec)` hashes the three
    table files' bytes itself, reads the spread from the verified file, refuses any measured row whose charge is not
    `gold_row_bp` within 1e-6 bp (quant R1; `charge_floor` is a diagnostic), re-stamps gold rows, keeps every other block, blocks every prior-class row (named) and gold 1m/5m (D-15).
    `gold_row_bp(rows, record, spread)` is the per-row charge of record (point in time). `dense_labels.py` untouched.
  - Read requirements (card 9a): first cost version read is the version of record (another is K+1); S by year and the
    share of rows where the ECR term bound; 15m reads report S at the vantage cell charge (descriptive); holdout read
    needs a new version; a 15m re-measure only as a new version with n ≥ 100 per cell before the first read.
  - Next: #512 adds slip_x to `gold_rows`/`row_rt_bp` (patch in the #515 report) and re-issues the manifest at
    srv3-cost-2b3628372a9b3bfa; #488 adds slip_x to `CostTable._gold_row_bp` (or calls `gold_row_bp`) and wires
    `apply` after `load_labels`. Gold 15m-1w units then block on D-4 only; 1m/5m also on D-15.
