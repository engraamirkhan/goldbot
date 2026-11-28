---
number: 277
rank: 100
title: D-12-xt gate read also recomputes tau_a(tf) and the vs-gold level from the cells
stage: 'Data (sr-v3 rho)'
size: S
status: proposed (data-engineer; quant R3 on #525, not blocking)
---
- **277. D-12-xt gate read also recomputes tau_a(tf) and the vs-gold level from the cells.** Value: `data_sufficiency.verify_inst_by_tf` (PR #525) recomputes only the per-tf day-mean rho_u,inst from the cells file. tau_a(tf), which sets lambda_a, and the D-13 vs-gold level are still read as recorded, so an edited JSON could move them unseen. Acceptance: a gate read also recomputes, from the sha256-checked cells file, (a) tau_a(tf) = max(0.01, the max of between_inst_sd.hi95 over the T2, long_gross / both_gross, raw-orientation rows of the class at the tf), and (b) the vs-gold level by the cumulative-max rule over vs_gold_agg.hi95 with the D-12 `binds_with` bucket floor. Either differing from `binding_by_tf` by more than 1e-4 raises ValueError naming the cell. Tests: a tampered tau_a and a tampered vs-gold cell are each refused, through `measured_rho(gate_read=True)` and `sr_v3.rho_inputs(freeze_sha=...)`.
