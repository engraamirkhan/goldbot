# Pre-registration amendment: the edge cap at the engine cost of record (standing, 2026-11-20)

Principal data engineer, 2026-11-20, for the quant-reviewer's condition C3 on ADR 0092 (engine cost of record from
realised fills). It is written and committed before `costs.engine_record` is set on any deployed host, so no fill read
by this rule was decided under it. It registers no arm and qualifies none.

## 1. Why

ADR 0092 lowers the engine's cost on most cells from the $0.15/side slippage prior to the measured round trip. A lower
charge lets more entries through the gate, including entries of arms whose fills already show they cannot pay even
that lower cost. This rule stops those arms. It is separate from the H-81 cost stop (which reads cost per R alone) and
from every R stop (which read net R).

## 2. The rule

For each exploration arm, its **measured fills** are its closed gold fills on every account the record in force
measures (`EngineCostRecord.brokers`). They are measured as `goldbot.research.realised_costs.trade_costs` measures
them: pyramid adds and cost-cell voids are dropped, and a row that cannot be measured is dropped, never imputed.

- **Gross R** of a fill = (side x (exit - fill) + spread + entry slip + exit slip) / stop distance (`stop_usd`). This is
  the fill's move before every price cost, in its own R. A stop at its level reads -1 + spread / stop.
- **Cost share** of the arm = the mean over its fills of the record's charge for the fill's account x session x
  timeframe cell, over its stop distance. A fill with no cell uses the cost the engine recorded at its send.
- **Trip:** the arm has at least **200** measured fills, AND the one-sided 95 % upper bound of its mean gross R is below
  its cost share. The bound uses an SE clustered by UTC day (CR1, `arm_stops.upper_bound`, z = 1.645).

When it trips, the arm is **suspended**: no new entry. The mechanism is the existing H-81 cost-stop path, the simpler of
the two options in the ruling.

- **Engine:** the arm's share is 0 in `exploration.shares` (`_cost_scale`, read at most hourly), so the entry is
  refused `cost_suspended`.
- **Daily review:** it retires the arm in `exploration_arm_status` with the rule `EDGE_CAP_RULE`, which names this file.
- Open positions are never touched.

## 3. Exempt

- **Random controls** are exempt (the arms with a `control_share`, role `control`, or `labs.control_arm_ids`). They are
  the null every arm is read against; by construction they have no gross edge.
- **Cost arms** are exempt (role `cost`, `research.arms.COST_ARMS`). Their prereg cost reads end them.

## 4. Multiplicity and honesty

- **K-neutral.** The rule can only stop an arm. It never qualifies, ranks or sizes one, and it adds no trial. A stopped
  arm's fills stay in every read that already counts them.
- **Fixed in advance.** The threshold (200 fills, one-sided 95 %) and the gross-R and cost-share definitions are fixed
  here. A change is a new dated amendment.
- **Costs are versioned.** The cost share is taken at the record in force (`costs.engine_record`). A new record version
  re-reads every arm on the new charge. The version is named in the record and in each row's `cost_version`.
- **No record, no cap.** Without a record in force (`costs.engine_record: null`), the cap reads nothing. It is defined
  at the record's charge.

## 5. Sunset

The rule stands until a dated amendment replaces it. The quant-reviewer reviews it with the next engine cost of record
version (backlog 266).
