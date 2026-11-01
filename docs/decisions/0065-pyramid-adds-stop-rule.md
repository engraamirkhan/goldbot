# 0065: Pyramid adds: every earlier stop to the newest add's stop

Status: accepted (quant 2026-11-01)

## Context
Growth sprint 2026-11-01 section 3 defers GS1's pyramid adds to 11-03 (a logged protocol deviation): "add a unit at
each +1 R from the first unit's entry, up to 3 adds; after every add, open risk stays <= 1 unit: move the stops of the
earlier units up so the total loss at the stops is <= the first unit's risk"; GS1C gets identical adds; every add goes
through the RiskGate and every cap still binds.

Three stop rules keep the group's netted loss at its stops at one unit:

1. The C2 screen's frozen rule (prereg-growth-2026-11-01.md): one common stop for every unit, avg fill - R / units.
   The add's own stop is then 1.33 R (add 2) or 1.75 R (add 3) from its fill, so at the first unit's lots the add
   alone risks more than the arm's per-trade cap (GS1/GS1C: £24, about one unit). The RiskGate refuses it
   (`exploration_arm_cap`), unless every unit is sized at 1/1.75 of the cap: a sizing change, not ours to make here.
2. Minimal move: the add keeps its own 1 R stop, and the earlier units go to their lot-weighted average entry. The
   netted loss is exactly one unit. But the engine's money bounds count each unit's loss at its own stop with a
   locked gain at 0 (`exploration._held`; never netting one unit's gain against another's loss). On that count the
   group holds 1.5 units after add 2 and 2 units after add 3, so the arm's day (£24) refuses adds 2 and 3.
3. Every earlier unit goes to the newest add's stop (fill - R), or tighter where the gate floored the add's stop wider
   than R. No earlier unit loses there, so the group holds exactly the newest add's one unit, both netted and as the
   engine counts it.

## Decision
Rule 3 (`engine/exploration_pyramid.group_stop`). The earlier stops move after the gate allowed the add and before it
is sent (`cost_probe._place` `before_send`), and again from its fill. If a move does not take, the add is not sent
(`stop_move_failed`). Every pass, whatever the entry state, a group above one unit at its stops has its earlier stops
moved back to the rule (restart safety). Stops only ever tighten. The add ladder and the stop rule are the same for
GS1 and GS1C. The engine's open-risk accounting is unchanged: no netting is introduced.

## Consequences
- GS1 is not compared to the C2 screen. There is no re-walk: C2 is retired, and a re-walk adds K for no decision. The
  live stops are tighter than C2's (every unit at +1 R after add 2, +2 R after add 3; the screen had +0.67 R and
  +1.25 R). The pre-registered read, GS1 - GS1C, compares identical mechanics. Growth sprint §3 logs this as
  protocol deviation 2.
- The add path moves the earlier stops by `modify` and confirms them at the broker before the add is sent
  (`confirm_move`). A rejected or raising modify refuses the add, and the engine's copy of a stop changes only to a
  level the broker confirmed (safety S1, S2).
- Each unit is 0.01 lots (`unit_lots`, quant Q2), so both legs' 8 units fit the 0.10 net lots. A pyramid unit has no
  profit target, and the group exits together at its stop or its end (quant Q3).
- A refused add's level is spent (tried once). The read never counts a refused add as a trade (quant Q4): it is listed
  as `refused_adds`, and its replayed R appears only in the "uncapped (approx.)" ledger.
- What would change this: a decision to net locked gains in the engine's open risk, under which the minimal move
  (rule 2) would fit.
