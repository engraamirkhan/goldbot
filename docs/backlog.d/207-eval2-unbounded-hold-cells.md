---
number: 207
rank: 74
title: 'Quant ruling: L3 cells with no registered time exit'
stage: 'Research · EVAL-2 L3 runner'
size: S
status: 'proposed (follow-up of 149, 2026-11-13) · accountable: quant-reviewer · reviews: research-director'
---
- **207. Quant ruling: L3 cells with no registered time exit.** Value: BACKLOG 149(b) cuts L3 units at decision + the registered maximum hold before 2025-10-01; a carried cell with no registered time exit has no maximum hold, so `eval2.runner.cut_at_holdout` refuses it (cutting it to nothing would close the family as NOT CONFIRMED on no data). Today such a cell can never be read at L3, TEST-OLD included. Acceptance: the quant-reviewer rules, as a prereg amendment, either that every L3-carried cell registers a time exit in its card before L2, or a bound the runner may use for a stop/target-only cell (e.g. the explore window's maximum realised hold, frozen in the card); the runner follows the ruling with a test that fails without it.
