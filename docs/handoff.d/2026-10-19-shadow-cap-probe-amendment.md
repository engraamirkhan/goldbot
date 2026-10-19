- Research director ruling 2026-10-19 parts B and C ("the system needs to learn, so more trades need to be opened and
  closed"). B: the shadow book records every signal as its own trade, up to `shadow.max_open_per_agent: 3` open per
  agent and version (beyond it: counted in `VersionBook.skipped_cap`, logged "skipped: cap"); `PerfStats` gains
  `n_clusters` (one per agent per session day, 22:00 UTC roll) and a cluster-robust `t_stat`; promotion's 40 counts
  clusters, the shadow Sharpe is on cluster sums, the hit-rate SE uses the cluster count; a record without clusters is
  never ready (fail closed); 4-week minimum unchanged. C (ADR 0037 amendment): up to `cost_probe.max_open: 2` probes,
  one new per bar close, never two on one agent; each stop clamped to a loss of at most `max_loss` (£4) from the
  broker's `loss_per_lot` at 0.01 lot (refused when unknown); no new probe once realised probe loss + open probe risk +
  the new probe's would exceed `budget_share` x the day's limit (£10). Revisit 3 probes after 4 weeks of clean data.
