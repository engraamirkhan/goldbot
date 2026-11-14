---
number: 252
rank: 80
title: 'Quant ratification of ADR 0081: the common filter at a 1d/1w entry close (BACKLOG 144 blocker 5)'
stage: Research · REVIEW
size: XS
status: 'done: quant-reviewer APPROVE 2026-11-14 on 11b72577; no look-ahead; EET tier-1 22:30 vs 23:30 send gap noted, ~zero effect (no tier-1 USD event 22:30-00:15 UTC); its condition (research enforces filter_close in code) built in BACKLOG 254 (branch feat/engine-backlog-1114); blocker 5 left GO_LIVE_BLOCKERS_1D_1W'
---
- **252. Quant ratification of ADR 0081 (BACKLOG 144 blocker 5).** Value: the rules' standing common filter dropped
  every 1d/1w candidate at its 21:00 UTC close; ADR 0081 judges it at the candidate's entry close
  (`RuleContext.filter_ts`, `long_bars.entry_after`). It changes research semantics, so it needs the quant's ruling
  before the guard can lift. Acceptance: the quant-reviewer accepts or amends ADR 0081 in writing (its status line),
  and any 1d/1w CONFIRM run used for go-live judges the filter the same way (BACKLOG 254); then the blocker leaves
  `GO_LIVE_BLOCKERS_1D_1W`.
