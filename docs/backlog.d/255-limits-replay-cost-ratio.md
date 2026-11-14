---
number: 255
rank: 79
title: Cost/model ratio in the adaptive-limits replay
stage: Risk (limits) · PROTECT
size: S
status: 'done 2026-11-14 (branch feat/limits-cost-ratio-replay-1114): quant-reviewer approved the R150 definition 2026-11-14; replay measures through limits_job._cost_ratio with store_exit_legs / store_label_bars; tests/test_cost_ratio_series.py test_a_replay_day_with_a_1_3_ratio_gives_the_floors_as_the_nightly_job_would, test_without_a_data_root_the_replay_leaves_the_cost_ratio_none_and_says_so, test_the_replay_reads_the_accounts_exit_fills_and_the_labels_dukascopy_bars_as_the_job_does · reviews: quant-reviewer'
---
- **255. Cost/model ratio in the adaptive-limits replay.** Value: `scripts/replay_limits.py` (goldbot/ops/limits_replay.py) still passes `cost_ratio_20d = None`, so a replay validates every ADR 0031 rule except the cost rule the nightly job now feeds (R150). Acceptance: with `--data-root`, the replay reads each day's store `exit_fills` and 15m bars and calls `goldbot.ops.cost_ratio.cost_ratio_20d` exactly as the job does (no parameter of the definition passed in); the header says when it cannot (no data root: None); a test shows a replay day with a 1.3 ratio gives the floors. Only after quant-reviewer has signed off the definition, since ADR 0031 fixes inputs before any replay.
