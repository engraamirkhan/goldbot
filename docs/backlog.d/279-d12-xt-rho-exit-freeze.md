---
number: 279
rank: 100
title: 'D-12-xt: measure_rho writes exit_freeze_sha and re-runs at xt-v1'
stage: 'Research (sr-v3 / H-106 U2)'
size: S
status: proposed
---
- **279. D-12-xt: measure_rho writes exit_freeze_sha and re-runs at xt-v1.** Value: every pooled-body power read (sr-v3 pooled Stage 0, H-106 U2 S0-3) is refused today by `config.xt_v1_rho_refusal`, because `scripts/research/measure_rho.py` never writes `exit_freeze_sha`; the quant ruling on #514 says the D-12 rho bindings do not carry over until re-run at the xt-v1 exit geometry. Owner: data-engineer; quant-reviewer signs; due 2026-12-04 (U2 precondition P2). Acceptance: (1) failing test first: the JSON `measure_rho.py` writes carries `exit_freeze_sha` equal to `dense_labels.EXIT_FREEZE_SHA256` and the run refuses labels stamped with another freeze; (2) the re-run at xt-v1 geometry on learn 2010-17 only (never confirm, test_old or holdout) is pinned by sha256 in settings, and `data_sufficiency.require_power_read_rho` accepts it; (3) the pooled `rho_u_inst_day_mean` is reported for gold + FX10 + XAG (it is null in rho-measurement-2026-11.json, so the 0.5 prior binds today); (4) label-blind with respect to the card's own family (ADR 0085: never the card's own outcomes).
