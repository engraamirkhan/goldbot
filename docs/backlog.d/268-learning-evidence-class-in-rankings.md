---
number: 268
rank: 93
title: Learning-class fills (evidence_class learning) stay out of rankings, read progress and board n
stage: 'Research integrity · API'
size: S
status: proposed
---
- **268. Learning-class fills (evidence_class learning) stay out of rankings, read progress and board n.** Value (quant
  review C2 of #440, ADR 0095): demo learning cohort fills (`evidence_class: learning_cohort`) are now listed as
  `not_evidence` and kept out of every strategy ranking row, read progress, board n and H-61/H-81 stop unit
  (`trades_ledger._arm_row`, `strategy_ranking`, `labs.counts_toward_reads`). Learning-class fills (`evidence_class:
  learning`, unqualified demo evidence per the ADR 0061 amendment 2026-11-02) have the same gap: they are `counted` in
  the trade ledger and can enter a family ranking row and read progress, although "never a qualification, never in a
  family ranking". Acceptance:
  - quant decides which readers may count a learning fill (the learning arm's own kill read and its loss cap do;
    rankings and the board's n do not);
  - the trade ledger, the strategy ranking and the board follow that decision;
  - a test shows a learning fill is listed but in no ranking row, read progress or board n where the ruling says
    not.
