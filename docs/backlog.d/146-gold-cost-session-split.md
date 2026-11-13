---
number: 146
rank: 54
title: Session-split gold cost of record in the catalogue sim
stage: 'Research (costs)'
size: S
status: 'done (implemented 2026-11-13, branch feat/gold-cost-session-split, R137; awaiting quant-reviewer) · accountable: implementer · reviews: quant-reviewer'
---
- **146. Session-split gold cost of record in the catalogue sim.** Value: the catalogue sim charges gold one round trip ($0.166, cor-2026-10-31.2 "all"), while the cost of record measures Asia $0.204, London $0.164 and New York $0.166; Asia entries are under-charged by about 0.04 $/oz. Acceptance: `costs.CostModel.round_trip` takes the fill instant and returns the cost-of-record cell of its session (worse of the two brokers); a test pins an Asia fill at $0.204; the reading rule names the session boundaries.
- Done 2026-11-13 (prereg-2026-11-07-catalogue-screen section 14a; runs after this commit only): the sim charges the dearer broker's session cell, never below $0.166: asia 0.204 (ICM, 309 fills; Vantage 0.158), london 0.293 (Vantage, 44 fills, ci90 0.158-0.45; ICM 0.164), newyork 0.263 (Vantage, 69 fills; ICM 0.166); the thin hours after the New York close pay the higher of the dearest session and every pooled figure (0.293). Thin (< 30 fills) cells read no lower than the broker's pooled cell. Summaries also report `net_icm_basis` (sensitivity, not gated); H-90 and deployment use per-broker costs, not the max. Catalogue gold net results move down; the EVAL-2 deciding cost (0.230) does not.
