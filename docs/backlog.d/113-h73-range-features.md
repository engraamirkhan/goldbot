---
number: 113
title: H-73 time-structure range feature group (<= 12 columns)
stage: 'Research · FEATURES'
size: M
status: 'retired (triage 2026-11-13): range forecasting is N-3 HAR (PR #342, item 145)'
---
- **113. H-73 time-structure range feature group (<= 12 columns).** Value: priority 1 (opportunity-day detection). The time-structure facts that forecast range beyond RV20 become one opt-in feature group for H-16 refreshes, H-54's sealed log and H-62 v2 (docs/research/prereg-2026-10-26-time-structure.md section 5). Acceptance: (1) at most 12 causal columns, each with a prefix-invariance test (the value at t is unchanged when later bars are deleted); (2) calendar columns join through `asof_join` on `available_utc`; (3) the wavelet columns drop their boundary region; (4) the drop rule (incremental R2 over HAR(1,5,22) < +0.005 on 2010-2017) is applied and recorded before any H-16 or H-62 read; (5) the group is K_eff +1 and is never an entry rule.
