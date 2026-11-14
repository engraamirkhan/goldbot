- 2026-11-14 (branch feat/cleanup2-mfe-ui-1114; ADR 0083; BACKLOG 142): cleanup phase 2. Retired the scheduler jobs
  `gap_watch`, `monthly_research`, `cpcv_quarterly` and the brain's `autodisc_nightly` (settings, job code, tests),
  deleted `goldbot/ops/gap_watch.py` and the `gaps:` settings (`daily_realised_vol` moved to research/attribution.py).
  Their keys are in `config.RETIRED_KEYS`, so a server settings.local.yaml naming one loads with a warning; the
  scheduler's prune drops their state (no `job:` health line). Kept, with the reader list in ADR 0083:
  `saturday_retrain`, `tournament`, `model_watch`, `recalibrate` (the engine reads the registry and population they
  write; model_watch is a rollback) and `research_director`, `attribution` (R141 weekly_review's consumer and input).
  News shock (D16): option 2, already live as ADR 0079's rules-v1 scorer. CPCV now by hand (`research_pass.py
  --cpcv`); discovery stays on the Mac's launchd. No order, exit, RiskGate, budget or gate path changed. Needs the
  trading-safety and quant GOs before merge.
