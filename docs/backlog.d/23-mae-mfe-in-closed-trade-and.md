---
number: 23
title: MAE/MFE in closed-trade and shadow records (G-9)
stage: Post-trade review · FIND
size: S
status: 'done (PR #73, main d28a482; a91f01f); quant sign-off not on record (TRACEABILITY V6 in review)'
---
- **23 (G-9).** Maximum adverse and favourable excursion in R on every shadow and closed trade, shown by exit
  type in attribution. **In review (2026-10-11, TRACEABILITY V6):** price, R and time of each on `ShadowTrade`
  (bars, label semantics) and `ClosedTrade` (ticks; approximate from stored bars across a restart); median MAE/MFE
  in R in the monthly report and per exit type in attribution. Needs trading-safety and quant review.
  Sweep 2026-10-16 (product owner): Done (PR #73, main d28a482; a91f01f); quant sign-off not on record (TRACEABILITY V6 in review). Acceptance record: merged on green CI into main; checked against git and the named tests exist; the criteria were not re-run one by one in this sweep.
