---
number: 49
title: Retired-family trial lists match the registry (R-01, R-03)
stage: Idea generation (integrity) · FIND
size: S
status: 'done 2026-10-16 (94b3460, merged at 717baa3 with quant APPROVED; ships to main in PR #79) · accountable: strategy-researcher'
---
- **49. Retired-family trial lists match the registry (research director, 2026-10-11).** Value: the registry on
  `research-v1` holds four `mean_reversion` trials (#3, #5, #10, #12) and four `trend` trials (#2, #4, #9, #14), but
  `research.retired_families` and `hypotheses.md` B list `[3]` and `[2]`. The total (20) and so the DSR count are
  unaffected, but the governance mirror and the registry disagree. Do: correct both lists in one change (the director's
  `retired_drift` check fails if only one moves). Acceptance: `tests/test_director.py` green; lists equal the
  registry's trial numbers. Depends on: none.
  *Fixed 2026-10-16 (strategy researcher):* checked against `research-v1`'s `registry.jsonl` (uploaded 2026-10-09,
  20 rows, sha256 `2f3a5706321b`…; only trial number, family and agent id read, no result): `mean_reversion` is
  #3, #5, #10, #12 (one agent, `mean_reversion-g0-b0f878abf3`) and `trend` is #2, #4, #9, #14 (`trend-g0-adf39c2c14`).
  The other retired families already matched (session_open #1, #7, #11, #13; breakout #6, #8, #15;
  intraday_momentum #18, #19). `research.retired_families` and `hypotheses.md` B now carry those lists; the registry is
  unchanged. `RetiredFamily.trials` is read only by `retired_drift`, so no allocation moves. `tests/test_director.py`
  40 passed. Still open: nothing checks these lists against the registry itself (only settings against the doc); a
  `retired_drift` input from the registry rows is proposed to the research director, not built here.
  **Acceptance scorecard 2026-10-16 (product owner, AGENT_STANDARDS 7 checks):** (1) criteria: yes, `tests/test_director.py` green (re-run 2026-10-16) and the lists equal
  `research-v1`'s registry rows (checked by the researcher, numbers above). (2) DoD: yes. (3) S1-S12: yes. (4) quant
  APPROVED (717baa3). (5) CI/merged: **no, in PR #79 (open)**. (6) PR text: yes (#79 Research). (7) role and date:
  yes, met. Accepted, conditional on #79 merging. Not in scope: a `retired_drift` input from the registry rows,
  proposed to the research director.
