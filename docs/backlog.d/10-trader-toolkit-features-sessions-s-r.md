---
number: 10
rank: 13
title: Trader-toolkit features (sessions, S/R, FVG, order blocks)
stage: Idea generation · FIND
size: M
status: 'done (PR #61)'
---

### 10. Trader-toolkit features (in progress)
Value: gives research the tools top discretionary gold traders use (session ranges, S/R, fair-value gaps, order
blocks) as point-in-time features (FIND).
Acceptance: each tool is a registered feature family with a docstring definition. The lookahead check passes on
truncated history. A unit test on a hand-built bar series shows each FVG and order block appearing only after the
bar that confirms it.
