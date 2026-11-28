---
number: 283
rank: 102
title: 'Engine: scheduled FX10 cost probes at icm-probe and vantage-demo (U2 P3)'
stage: 'Engine (cost measurement)'
size: M
status: proposed
---
- **283. Engine: scheduled FX10 cost probes at icm-probe and vantage-demo (U2 P3).** Value: no FX10 instrument has a fill at both brokers (530 FX fills 2026-11-03..11-13: 389 at icm-probe on the 7 USD pairs, 141 at vantage-demo on the 3 JPY crosses, 0 with an exit quote), so U2's measured FX cost of record (backlog 280a) cannot exist without probes. Owner-approved 2026-11-28; protocol fixed in `docs/research/prereg-amendment-2026-11-28-u2-fx-cost-probes.md` section 1, implementation list in its section 6. Owners: implementer; trading-safety-reviewer and code-reviewer before deploy; deploy by 2026-12-04 21:00 UTC, first slot no later than 2026-12-09 00:15 UTC. Acceptance (failing tests first): `cost_probe.fx_scheduled` refused on a non-demo account; values pinned to the amendment (12 UTC slots, 4 per instrument x broker x session a day, rotated order, 30-min window, seed 20261128 sha256 side with a test vector, 0.01 broker-min lot, 60 s hold then market close never gated, 20-pip server stop, no target, at most 2 open per account, same-instrument busy rule, GBP 25 per account per day allowance fail-closed, hard end 2026-12-23 00:00 UTC); a probe-only second-broker symbol for each pair (no strategy book); the exit quote is the probe's own symbol's tick, not `eng._last_tick` (gold); a ProbeRecord per order with symbol, quote-unit spread, `fx_acct_per_usd` per quote unit, deals' commission/swap, `basis: scheduled_fx_u2`, own magic offset, `cm-fx-` id <= 29 chars; a skip row per missed or refused slot (quoted spread on spread refusals); a counts-only daily report with no cost values before the cut-off.
