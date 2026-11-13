---
number: 211
rank: 74
title: Pre-register and wire the news / order-flow discovery screen
stage: 'research (follow-up of R139, plan item 8, 2026-11-13)'
size: M
status: 'proposed · accountable: research-director · reviews: quant-reviewer, data-engineer'
---
- **211. Pre-register and wire the news / order-flow discovery screen.** Value: R139 adds the `news_llm` and `orderflow` columns as opt-in candidates; `scripts/research_pass.py --discover` does not pass opt-in contexts yet (only `feature_ctx` takes `news=` / `orderflow_bars=`). Acceptance, tests first: `--news FILE` (a local export of the brain's `news` table, never published) and `--orderflow` (the run's own 1m bars) flags reach the discovery path through `feature_ctx`; the pre-registration (4-ai-systems.md s.5 rank 1) is written before any look: 1h and 4h labels, fold-internal stability selection, control = the same news columns from `news_features.shuffled_control` (received times permuted within each UTC day), gate = permutation importance above the control and top-decile net R 90 % LB > 0 vs the side-matched random twin; the news screen covers the collector's forward window only (no backfill), so its power is stated with the row count before the run. Adding the columns costs 0 trials; a survivor costs 1.
