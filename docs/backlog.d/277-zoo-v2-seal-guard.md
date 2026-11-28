---
number: 277
rank: 100
title: 'zoo-v2 seal guard: per-cell allowlist before ZOO_V2_CARD, no withheld columns in oof.parquet'
stage: 'Research (sr-v3 trainer) · integrity'
size: S
status: proposed
---
- **277. zoo-v2 seal guard: per-cell allowlist before ZOO_V2_CARD, no withheld columns in oof.parquet.** Value: the sealed G-cw / HB / AVG OOF scores are sealed by convention only. In read mode `sr_v3_report.write_unit` writes `oof.parquet` with their `v_`/`k_` columns next to `net_read`, so anyone holding the file can compute their S. Setting `ZOO_V2_CARD` also opens all three learners at every unit at once, while the H-104 card (`docs/research/prereg-2026-11-28-zoo-v2-dl.md` sections 3 and 9) allows a read only for the (learner x unit) cells Stage 0 started. Acceptance: (1) in read mode, `oof.parquet` carries no `v_`/`k_` column of a learner outside `read_learners()` (test on a fixture with `ZOO_V2_CARD` None); (2) `read_learners(unit)` takes the unit and returns L-cw, B8 plus only the zoo learners on a frozen per-cell allowlist recorded with the card's sha; an empty allowlist opens none (test); (3) the allowlist can be set only with the card path and its sha256 (test); (4) existing sealed-score shas are unchanged (`scores_sha` test).
