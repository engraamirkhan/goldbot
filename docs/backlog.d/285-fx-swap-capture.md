---
number: 285
rank: 104
title: 'Ops: daily point-in-time FX10 swap-term capture at both brokers'
stage: 'Ops (cost measurement)'
size: S
status: proposed
---
- **285. Ops: daily point-in-time FX10 swap-term capture at both brokers.** Value: the U2 card charges "FX/XAG swap per side per rollover from the broker's symbol swap spec captured point in time"; no capture exists. Owners: sre (job), implementer (reader); starts with the backlog 283 deploy. Blocked on the quant ruling on swap for history rows (amendment `docs/research/prereg-amendment-2026-11-28-u2-fx-cost-probes.md` section 5 item 4; research director recommends option (b) rate differential + measured broker markup, (c) worse-side constant as sensitivity), due 2026-12-04. Acceptance: once a day at 12:00 UTC on icm-probe and vantage-demo, read-only `symbol_info` for FX10: `swap_long`, `swap_short`, `swap_mode`, `swap_rollover3days`, `captured_utc`, append-only in the ops DB; no account identifier in anything published.
