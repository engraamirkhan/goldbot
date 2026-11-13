---
number: 255
rank: 79
title: Cost/model ratio in the adaptive-limits replay
stage: Risk (limits) · PROTECT
size: S
status: 'open: after quant-reviewer signs off the R150 definition · accountable: implementer · reviews: quant-reviewer'
---
- **255. Cost/model ratio in the adaptive-limits replay.** Value: `scripts/replay_limits.py` (goldbot/ops/limits_replay.py) still passes `cost_ratio_20d = None`, so a replay validates every ADR 0031 rule except the cost rule the nightly job now feeds (R150). Acceptance: with `--data-root`, the replay reads each day's store `exit_fills` and 15m bars and calls `goldbot.ops.cost_ratio.cost_ratio_20d` exactly as the job does (no parameter of the definition passed in); the header says when it cannot (no data root: None); a test shows a replay day with a 1.3 ratio gives the floors. Only after quant-reviewer has signed off the definition, since ADR 0031 fixes inputs before any replay.
