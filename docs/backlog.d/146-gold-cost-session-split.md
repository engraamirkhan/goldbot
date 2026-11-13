---
number: 146
rank: 7
title: Session-split gold cost of record in the catalogue sim
stage: 'Research (costs)'
size: S
status: 'ready · accountable: implementer · reviews: quant-reviewer · due 2026-11-18 (triage 2026-11-13)'
---
- **146. Session-split gold cost of record in the catalogue sim.** Value: the catalogue sim charges gold one round trip ($0.166, cor-2026-10-31.2 "all"), while the cost of record measures Asia $0.204, London $0.164 and New York $0.166; Asia entries are under-charged by about 0.04 $/oz. Acceptance: `costs.CostModel.round_trip` takes the fill instant and returns the cost-of-record cell of its session (worse of the two brokers); a test pins an Asia fill at $0.204; the reading rule names the session boundaries.
