---
section: next
---
- **Q1 quant-review fixes and BACKLOG 50 (2026-10-11; quant review pending; due before the 2026-12-11 freeze).**
  **BACKLOG 50:** `--discover` now cuts the 1m bars at `research.holdout_from` before the config is built, so H-02's
  hashed `bars_to` is the last bar before 2025-10-01 whatever data-v1 reaches. The writer
  (`preregister_quarter.py --bars raw`, and the `preregister-quarter` workflow, which now downloads data-v1 in both
  jobs) pins the sha256 digests of the research window's yearly files (years up to the holdout's) in every queued
  row's `plan.data_digests`; a write without them is refused. `research_pass.py` and `--discover` verify them before
  any look for every pending queued row of the run's family and agent: a re-pull, a missing file or another
  `--from-year` is refused with the file and both digests named. A run whose family and agent have a pending queued
  row with another config hash is refused (`QueuedConfigMismatch`, naming the row, its spec id and the differing
  keys) instead of falling back to the open budget. **Retire-only daily floor (owner ruling A, strict):** between
  `screen_min_events_daily` (150) and `screen_min_events` (1,000) a screen reads `fail` or `inconclusive (event
  floor)`, never `pass` (`screen.screen_floors`), so a daily rule never reaches the model stage there and H-01's coded
  rule reads it `inconclusive`. **passed_gates:** a gate pass (and a survivor's holdout pass) counts only when its row
  records `results.swap` and `results.cost_source`; swap stays out of `config_hash` (no pinned hash moves). The
  analyst's and label grid's walk-forward rows now record `cost_source` (`jobs.live_cost_source`); their older rows
  (and any pre-swap research pass) no longer count for promotion or holdout scoring until re-run. **Freeze commit:**
  must also be an ancestor of HEAD. **Spec:** H-02's `discovery` is the markdown's full pinned `DiscoveryConfig` dump,
  clusters written out (same hash as naming `H02_CLUSTERS`); H-02 still owes its variant and bars range.
