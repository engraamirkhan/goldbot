---
number: 4
title: FRED macro data pipeline
stage: Market and macro preparation · FIND
size: M
status: 'done (PR #61, main 8bc7952)'
---

### 4. FRED macro data pipeline (done, PR #61)
Value: gold's best-documented drivers (real yields, the dollar) are absent from every model today (FIND).
Acceptance:
- A weekly `data-macro.yml` publishes DFII10, T10YIE, DTWEXBGS and GVZCLS with `available_utc` set to the
  publication time plus lag, as Parquet on a release, loaded like `data-v1`.
- `macro` is in the research candidate pool. The lookahead check passes on it. Every join to bars goes through
  `asof_join` on `available_utc`, and a test fails if a value is visible before `available_utc`.
- The allocator can read a real-yield and dollar regime input (20-day change), with a unit test.

Rows: D14 (loader only today); new row for the pull.

Sweep 2026-10-16 (product owner): Done (PR #61, main 8bc7952). Acceptance record: merged on green CI into main; checked against git and the named tests exist; the criteria were not re-run one by one in this sweep.
