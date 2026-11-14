- **1d/1w long history, live == history, rule-code versions (BACKLOG 251, 252, 254, 105; R154; 2026-11-14).** A daily
  job `long_history_refresh` (21:40 UTC) appends the store's complete days to the engine-only `raw/engine_daily/bars/xauusd_1d.parquet` (never the research file, never a holdout-dated day; `stale` past 120 days; quant review)
  (`source` `engine_1m:<broker>`), the preflight checks the history reaches the engine window once 1d/1w are live, and
  health `long_history:<account>` tells the owner when an arm is held back more than a day. ADR 0081 is ratified; the
  1d/1w filter is enforced on every research caller, a 1d/1w hold and order id count from the decision close, and
  `run_arm_backtest` is the CONFIRM of record (21:00 UTC roll, fill at the live entry close, Friday 1d dropped). Only
  blocker 7 (BACKLOG 253) keeps 1d/1w dark. Every arm rule carries a version (`research/arm_rule_versions.py`, pinned
  by source hash of the rule, every arm_rules* helper it reaches and the common screen, quant review); `exploration_obs.arm_rule_version` (aux migration 36) records it.
