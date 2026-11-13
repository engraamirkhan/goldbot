---
number: 207
rank: 74
title: 'Quant ruling: L3 cells with no registered time exit'
stage: 'Research · EVAL-2 L3 runner'
size: S
status: 'ruled (quant-reviewer, 2026-11-13) · enforced by eval2.runner.cut_at_holdout (branch fix/eval2-runner-integrity)'
---
- **207. Quant ruling: L3 cells with no registered time exit.** Value: BACKLOG 149(b) cuts L3 units before 2025-10-01 by each cell's maximum hold; a carried cell with no registered time exit has none, and cutting it to nothing would close the family as NOT CONFIRMED on no data. Ruling (quant-reviewer, review of f18881ab): "every carried cell must register its sim ev.hold as its time exit in the card before L2; realised-hold bounds not accepted". Acceptance: `eval2.runner.cut_at_holdout` refuses a carried cell without a registered time exit (`tests/test_eval2_runner.py::test_a_cell_with_no_registered_maximum_hold_is_refused_not_silently_cut`); the card writer (`freeze_batch_card` callers) registers the sim's `ev.hold` for every carried cell.
