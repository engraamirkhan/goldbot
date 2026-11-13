---
number: 146
rank: 54
title: Session-split gold cost of record in the catalogue sim
stage: 'Research (costs)'
size: S
status: 'done (implemented 2026-11-13, branch feat/gold-cost-session-split, R137; awaiting quant-reviewer) · accountable: implementer · reviews: quant-reviewer'
---
- **146. Session-split gold cost of record in the catalogue sim.** Value: the catalogue sim charges gold one round trip ($0.166, cor-2026-10-31.2 "all"), while the cost of record measures Asia $0.204, London $0.164 and New York $0.166; Asia entries are under-charged by about 0.04 $/oz. Acceptance: `costs.CostModel.round_trip` takes the fill instant and returns the cost-of-record cell of its session (worse of the two brokers); a test pins an Asia fill at $0.204; the reading rule names the session boundaries.
- Done 2026-11-13: as accepted, with the worse-of-two rule London and New York also rise (Vantage 0.293 and 0.263 on 44 and 69 fills, both >= 30), so the sim now charges asia 0.204, london 0.293, newyork 0.263, never below the flat 0.166; thin (< 30 fills) cells read no lower than the broker's pooled cell. Every catalogue gold result net of cost moves down; the EVAL-2 deciding cost (0.230) does not.
