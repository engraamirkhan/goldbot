# 0064: Cost of record: version pinning and the overshoot ruling for new pre-registrations

Status: accepted (quant reviewer ruling on PR #238, binding; microstructure researcher, 2026-10-31)

## Context

Research screens priced every trade with one frozen model, `config/arm_costs.yaml`:
- a round trip of $0.23/oz;
- a stop-exit overshoot reported at 0 and at $0.70.

The 0.70 came from 14 stops in the first week of probes (cost-model-1m-2026-10-25.md).

The week of 2026-10-26 measured both brokers' demo costs by session from 1,472 exploration fills
(docs/research/cost-of-record-2026-10-31.md, `config/cost_of_record.yaml`):
- **Round trip:** the upper bound exceeds 0.23 in ICM Asia and in Vantage London and New York.
- **ICM overshoot:** 0.110 pooled, with a clustered upper bound of 0.11 to 0.20 by session, about a sixth of 0.70.
- **Vantage overshoot:** its stops fill at exactly the stop price on demo, and it records no exit legs, so its overshoot
  cannot be measured.

A measured table also changes over time. A screen read against a later table than the one its pre-registration was
written against is a different test.

## Decision

1. **Version pinning.**
   - Every new pre-registration names the cost of record version it was written against (`cost_of_record: <version>`
     in its `research.yml` inputs and in `Qualification.cost_table`).
   - Its screen runs with `ScreenPin(broker, cost_version)` (`run_arm_backtest.py --screen-broker --cost-version`).
   - `ArmCosts.require_version` stops the screen before any bar is read if the loaded table is another version.
   - A refreshed table gets a new version. It never rewrites a pinned one.
2. **The round trip a new pre-registration charges** is `charged_round_trip_usd(broker, session)`: the larger of the
   session cell's clustered upper 90% bound and 0.23.
   - A trade outside every session is charged the broker's dearest cell.
   - A session name the session table does not know is refused. It is never read as the pooled cell.
3. **The overshoot ruling.** This applies to each stop exit of a new pre-registration.
   - **ICM:** charge the session cell's minute-clustered upper 90% bound, floored at $0.12. On `cor-2026-10-31.2` that
     is Asia 0.136, London 0.120 and New York 0.196. Until clustered numbers existed the interim charges were 0.15
     Asia and London and 0.167 New York; they are superseded by the table.
   - **Vantage:** charge $0.70 until its exit legs are recorded and its overshoot is trusted (ADR 0043: never another
     broker's measurement; backlog 141).
   - **The 0.70 stress** is reported beside the result (`net_r_cor_stress`). For ICM it is a stress line, not a gate.
4. **Registry rows keep the frozen model.** Every existing registry row and the historical arms matrix keep 0.23 and
   [0, 0.70] (`net_r`, `net_r_os`, the K-row config), so earlier reads stay comparable.

## Consequences

- New ICM screens charge 0.23 to 0.27 per round trip plus 0.12 to 0.20 per stop exit, in place of 0.23 plus 0.70.
  At a $3 stop with about 45% stop exits, that is about 0.10 to 0.12 R all-in, against 0.18 R before. Edges that only
  cleared under the stress line are no longer refused by an unmeasured cost. Edges that cleared only because Asia was
  under-charged now pay for it.
- Vantage screens stay at the stress level until backlog 141 lands.
- A cost refresh is a new table version. It does not move a running pre-registration's goalposts. A
  pre-registration that wants the newer table re-pins in an amendment, written before its data is read.
- Code: `goldbot/research/arms.py` (`ScreenPin`, `require_version`, `charged_round_trip_usd`, `overshoot_charge_usd`,
  `screen_net_r`), `goldbot/research/cost_of_record.py` (the builder), `scripts/run_arm_backtest.py` (the screen path).
