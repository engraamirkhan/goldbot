---
number: 10
title: Trader-toolkit features (sessions, S/R, FVG, order blocks)
stage: Idea generation · FIND
size: M
status: 'done (PR #61, main 8bc7952)'
---

### 10. Trader-toolkit features (done, PR #61)
Value: gives research the tools top discretionary gold traders use (session ranges, S/R, fair-value gaps, order
blocks) as point-in-time features (FIND).
Acceptance: each tool is a registered feature family with a docstring definition. The lookahead check passes on
truncated history. A unit test on a hand-built bar series shows each FVG and order block appearing only after the
bar that confirms it.

Sweep 2026-10-16 (product owner): Done (PR #61, main 8bc7952). Acceptance record: merged on green CI into main; checked against git and the named tests exist; the criteria were not re-run one by one in this sweep.
