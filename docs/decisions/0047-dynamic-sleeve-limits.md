# 0047: Dynamic sleeve limits for exploration arms

Status: decided (CTO, 2026-10-26) with the quant reviewer's binding ruling of the same day folded into D3 and D5.
**Demo only, tighten-only.** No RiskGate limit, ceiling, `daily_loss_limit_abs`, `budget_share`, drawdown stage,
exit rule or Thompson share changes. Implementation needs trading-safety and quant review (list at the end).
Extends ADR 0039 (per-strategy r_s, k_vol/k_dd/k_day) and ADR 0043 (per-style exploration pots); both stay frozen
and are not edited. ADR 0031's rules are untouched.

## Context
- Owner, 2026-10-26: "limits should be dynamic for risk and type of strategy". Demo learning grows to about 100
  exploration arms (from about 24 in ADR 0043). Owner framing, same day: a strategy is one **family** across
  timeframes; timeframes are variants of it.
- Exploration arms trade the minimum lot (ADR 0043), so ADR 0039's per-trade r_s cannot act on them: there is no
  smaller size. The only lever left is **admission**: how much of a pot's £ and slots, and of an arm's room, is open.
- Existing anchors: ADR 0039 factors `goldbot/risk/adaptive.py:449-475`, applied in
  `goldbot/engine/limits_apply.py:108-135` (fail closed to the fixed values); ADR 0043 pots
  `goldbot/engine/exploration.py:160` (`Pot{gbp, max_open}`), within-pot shares `exploration.py:549-569` (controls
  at a fixed `control_share`), admission `_enter` `exploration.py:772`; candidate journal
  `goldbot/research/exploration_data.py:210` (`record_candidate`, with propensity) and `:375` (`replay_label`).
- Architect plan (2026-10-26): extend, do not duplicate; a factor layer in the exploration code before
  `RiskGate.check`, fed by a nightly decision. Quant reviewer ruling (2026-10-26): controls and pre-registered reads
  are exempt from outcome-driven factors; refused candidates must stay labelled (D5).

## Decision

### D1. Mechanism (extend the exploration layer; no parallel limit path)
Each admission multiplies ADR 0043's caps by factors that are each in (0, 1] (a cut, never a raise):

| Cap | Dynamic value |
|---|---|
| Pot £ (`Pot.gbp`) | `gbp x k_style` |
| Pot slots (`Pot.max_open`) | `max(1, floor(max_open x k_style))` (a pot never loses its last slot to a factor) |
| Arm room (share x pot £, capped by the arm's per-trade cap) | `room x k_style x k_arm`; `k_arm = 0` refuses the arm |

`k_style = k_vol x k_cost x k_alloc` per (style, family); `k_arm = k_dd` per arm (an arm is one family x timeframe
variant). Sleeve decisions are **keyed by (style, family)**, timeframes as variants (owner framing); the money split
of ADR 0043 is not changed: a family's part of a style pot is the sum of its arms' Thompson shares, untouched.

| Factor | Rule | Source |
|---|---|---|
| `k_vol` | 0.5 in the ADR 0039 high-volatility regime, else 1. **The same function** as ADR 0039 (20-day median of daily median ATR15 vs its 1-year p90, `atr15_daily_stats`); one regime definition in the code | nightly |
| `k_cost` | measured / pre-registered cost ratio c: c ≤ 1.10 gives 1; 1.10 < c ≤ 1.25 gives 0.75; c > 1.25 gives 0.5. Unknown cost is the ladder's pre-registered value (1) | nightly |
| `k_alloc` | the pot scaled to ADR 0031's current daily allowance on the host (allowance / the allowance ADR 0043 was sized on), capped at 1 | nightly |
| `k_dd` | arm drawdown in R: at D1 0.5; at D2 0; restored to 1 once the arm's drawdown is back below D_restore over its next N labelled candidates (D5: refused candidates are labelled, so a k=0 arm can recover) | engine, folded from aux.db `exploration_trades` at start and on every close (restart-safe) |

D1, D2, D_restore and N are set in `RiskSettings.sleeves` with the quant reviewer before the first enable and are
then frozen; changing them, the cost ladder or the 0.5 values is a new ADR. Every admission and refusal is journalled
with `k_vol, k_cost, k_alloc, k_dd`, the sleeve decision id and the reason (`sleeve_pot`, `sleeve_slots`,
`sleeve_arm`, `sleeve_dd`). The RiskGate, the host caps, the ADR 0043 -8% pause and exits run exactly as today.

The nightly `goldbot/ops/sleeve_job.py` writes **append-only** `sleeve_decisions` rows in core.db (a migration;
persistence-compat test required). New persisted state is in SQLite, not JSON (standing rule).

### D2. Missing, stale or corrupt nightly decision: the tight side, bounded (architect's option, refined)
| Case | Engine uses |
|---|---|
| Latest valid row ≤ 36 h old | that row (bridges one missed nightly run) |
| A corrupt row (fails validation) | skipped with a `dq_event` (`sleeve_decision_invalid`); the latest valid row if ≤ 36 h old |
| No valid row ≤ 36 h old | `k_vol = k_cost = k_alloc = 0.5` in place of the nightly product (so `k_style = 0.5`), `dq_event` `sleeve_decision_missing` once per engine day; `k_dd` still applies (it never needs the nightly) |

Why not ADR 0039's fall-back to the fixed values: there, fixed = the reviewed baseline per trade. Here the cost of a
wrong loose fallback is a faster run into ADR 0043's -8% pause and, behind it, the 12% halt (K2: at least 40 days
with no learning) across about 100 arms; the cost of a wrong tight fallback is half the labels for at most a day
before the `dq_event` is acted on. The asymmetry favours tight. Bound: the fallback is never 0 (it never stops
learning on its own), and two consecutive missing nights page the sre, who owns the job's restore (1 working day).

### D3. Controls and pre-registered reads (quant ruling, binding)
- **Controls** (S0, S0R, I0, W0 and their twins) take `k_vol` and the account and host caps only: no `k_cost`,
  `k_alloc` or `k_dd`. Their room is `control_share x Pot.gbp x k_vol`. Controls are the null; scaling them by
  results or by cost would remove the baseline every arm is judged against.
- **Arms under a pre-registered read** (F1A/F1C, G1A/G1C, WFRI, O-11/O-12) are exempt from `k_dd`; they keep
  `k_vol`, `k_cost`, `k_alloc`, their per-arm caps and the RiskGate.
- A control whose drawdown passes D2 is not shrunk: it is journalled `control_dd` and raised to the quant reviewer
  and sre as a probable execution or cost defect (a random-entry null should lose about the cost drift, no more).

### D4. Pairs and twins: the vol factor is identical within a pair (quant ruling, binding)
Within an original/twin pair: the same `k_vol` function, the same ATR feed read at the decision time, and the same
seeded draw per slot; a twin uses its original's vol reference. A pair test asserts it.

### D5. Learning-speed trade-off (CTO ruling)
Shrinking on losses means fewer trades against the owner's hundreds-a-day target. Ruling:
1. **Learning is labels, not fills.** Every candidate, taken or refused, is recorded with its admission probability
   (Thompson share x k) via `record_candidate`, and every refused candidate is labelled via `replay_label`. An arm's
   read is its full candidate stream (taken + refused), unweighted; IPW is used only for fill-based metrics (cost,
   slippage). A refused candidate is therefore still a label: shrinking cuts demo £ and fills, not research evidence.
   Propensity 0 (`k_dd = 0`) is excluded from IPW, never from the label read.
2. Room freed by one arm's `k_dd` stays in its pot for the other arms (pot £ is cut only by `k_style`), so pot
   throughput falls only when the whole style is in a cost or volatility regime, which is when fills are worth least.
3. **Tripwire, not a loosen:** if admitted exploration fills fall below 150 a trading day for 2 consecutive days
   while the -8% pause is off, the research director and the CTO review the factor values within 1 working day.
   A trade-count target is never a reason to raise a cap, skip a factor or weaken a fallback.

### D6. Scope guard
The sleeve layer loads only for an account that is `probe_only`, `mode: demo` and reports demo from the terminal;
otherwise it refuses to start with `sleeve_not_demo` and the account trades with ADR 0043's caps exactly. Nothing here
applies to icm-demo's strategy roster or any live account; ADR 0004's £50 real-money limit is untouched.

## Consequences
- New: `goldbot/risk/sleeve.py` (pure factor functions, reusing ADR 0039's `k_vol`), `RiskSettings.sleeves`,
  a core.db migration for `sleeve_decisions`, `goldbot/ops/sleeve_job.py`, and changes to the exploration layer at
  the pot room (`exploration.py` near `:626`) and admission (`_enter`, `:772`). Gate, caps, exits and Thompson shares
  are untouched. Rollback: `sleeves.enabled: false` and restart gives ADR 0043 exactly.
- Required tests (failing first): fallback table D2 (missing, stale > 36 h, corrupt row); controls get k_vol only;
  pre-registered reads skip k_dd; pair vol identity (D4); `k_dd` fold identical before and after a restart; slots
  never below 1 from a factor; refused candidates carry propensity and a label; non-demo refusal (D6);
  persistence-compat for the migration.
- Reviews: trading-safety (admission path, fail-closed, demo guard), quant (D1-D2 constants before enable, D3-D5
  statistics), data-engineer (migration, append-only table), sre (nightly job and its alert).
- Reversal: if the quant reviewer finds refused-candidate labels biased against fills (replay vs fill gap beyond the
  ADR 0037 cost tolerance), `k_dd` is withdrawn and arms fall back to their H-61 stops only; if the fallback fires
  more than twice in a month, the sre moves the job into the engine's own day roll.
