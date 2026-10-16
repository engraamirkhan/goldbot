---
number: 49
rank: 11
title: Retired-family trial lists match the registry (R-01, R-03)
stage: Idea generation (integrity) · FIND
size: S
status: 'ready · accountable: strategy-researcher · due 2026-10-23'
---
- **49. Retired-family trial lists match the registry (research director, 2026-10-11).** Value: the registry on
  `research-v1` holds four `mean_reversion` trials (#3, #5, #10, #12) and four `trend` trials (#2, #4, #9, #14), but
  `research.retired_families` and `hypotheses.md` B list `[3]` and `[2]`. The total (20) and so the DSR count are
  unaffected, but the governance mirror and the registry disagree. Do: correct both lists in one change (the director's
  `retired_drift` check fails if only one moves). Acceptance: `tests/test_director.py` green; lists equal the
  registry's trial numbers. Depends on: none.
  Sweep 2026-10-16 (product owner): accountable `strategy-researcher`, due 2026-10-23 (no date before this sweep). Checked today: `config/settings.yaml` still lists `trials: [3]` for R-01 and `[2]` for R-03. Acceptance as above.
