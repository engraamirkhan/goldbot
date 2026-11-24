# Amendment 2026-11-24: retired no-evidence arms record again as replay-only

Status: **in force with the release that carries it** (owner 2026-11-24, "more zero-risk learning data, do it now";
PD + PO). Amends prereg-amendment-2026-11-15-replay-only-controls.md section 1 point 1 ("an unpaired gold random
control is replay_only": the mode was for role control only). The original is not edited.

## Why

Since PR #424 (2026-11-22, only arms with evidence place orders) the system records about 160 labelled candidates a
day, nearly all from the random controls S0, VEX15, IEX1H and VEX1H. Every arm retired for lack of evidence also
stopped recording, so the meta-model rebuild (weekly-decisions-2026-11-22.md step 5) and every later screen lose the
cheapest data there is: the rule's own forward candidates, labelled by the r2 replay at zero broker risk.

## Rules

1. **`control_mode: replay_only` is allowed on a gold arm of any role.** On a retired row (`retired_day`) the
   retirement stands: no entry on any account, ever. The rule runs again only to record. Every candidate is written to
   aux.db `exploration_obs` with taken 0, blocked `replay_only`, mu 0, and is labelled by the existing r2 replay
   (`label_sim_r`, `label_mid_r`).
2. **Structurally unable to send.** A replay-only candidate is recorded at its signal and never enters the queue
   (`engine.exploration._signal` -> `_replay`). The queue sweep in `_enter` and the first check of `_send` stay as two
   further guards. Nothing reaches the budget, the RiskGate, an order intent or the order core
   (`tests/test_replay_only_retired_arms.py`). The RiskGate stays the only path to an order.
3. **What does not record:** a row whose hash does not match (edited after it was frozen), a bad row kept from its
   last good one, and an arm on a dark timeframe. A replay-only arm is never dormant and never de-registered.
4. **Bound.** At most `REPLAY_RETIRED_DAILY_CAP` = 1,000 records a UTC day per engine from arms that may send no
   entry. The first record over the cap is journalled `exploration_replay_cap`. Estimate below: about 280 a day over
   all hosts, so the cap only stops a runaway rule on the 2-core brain. It is a module constant, not a setting.

## Rows

Switched to replay-only, still retired (all gold, all registered on a demo account, retired 2026-11-13 for lack of
evidence: kill reads, H-90 cost budgets, controls left with no live arm). Approximate candidates a day are from the
icm-probe/icm-demo record of 2026-10-26..11-13:

| Arm | Rule, tf | Retired by | ~/day |
|---|---|---|---|
| S1A, S1B | session_vwap_fade 5m | control-thinning 11-06 point 4 | 7 + 7 |
| F1A, F1C | hurst_band_break 15m (cost arm + control) | profitability-2026-11-12 (a) | 5 + 5 |
| G1A, G1C | level_break 1h (cost arm + control) | profitability-2026-11-12 (a) | 1 + 1 |
| Z1P, Z2P, LCZ1P, LCZ2P | tick_surge, run_fade 1m + controls | H-90 cost budget | 6 + 12 + 16 + 13 |
| Z3D, LCZ3D | round_break 5m + control | H-90 cost budget | 25 + 17 |
| Z5P, Z9D, Z10D, LCZ5P, LCZ9D, LCZ10D | 5m learning arms + controls | survivor-reads KILL | 5 + 6 + 7 + 17 + 16 + 14 |
| ZCD | scalp_control 5m | profitability-2026-11-12 (a) | 15 |
| IEX15 | random_bracket 15m | profitability-2026-11-12 (a) | 54 |

New rows, the red-box rule (rule body of RB15/RB1H/RB4H and RBC15/RBC1H/RBC4H; `end_utc` 2026-12-31 22:00 UTC, re-decided
at the quarter review). The old rows cannot record: their hashed `end_utc` decides no candidate after 2026-11-22
22:00 UTC, and a new `end_utc` is a new frozen body, so it is a new id. RBR15, RBR1H, RBR4H (role strategy) and
RBRC15, RBRC1H, RBRC4H (their random-ladder twins, role control) are born retired and replay-only on icm-probe. The
red-box week ran about 30 candidates a day over the six.

**Expected total:** about 280 candidates a day more, about 440 a day with today's 160.

Left retired, recording nothing:

- **GS1, GS1C:** the evaluator's GS1 verdict (weekly-decisions-2026-11-22.md step 3, due 2026-11-27) is on a read
  truncated at 2026-11-22. Rows recorded after it under the same ids invite a contaminated read. Revisit after the
  verdict.
- **RB15, RB1H, RB4H, RBC15, RBC1H, RBC4H:** they stop at their `end_utc` (above); RBR*/RBRC* record their rule.
- **Every non-gold row** (S0-/I0-/LB-/LBC- per instrument, retired 2026-11-14 by the gold-only ruling): the r2 replay
  labels gold only, so a non-gold replay-only row stays refused at load.
- None of the gold arms retired 2026-11-13..11-23 was retired as broken (bad data or a wrong rule).
- Out of scope here: the A2/A5 retirements of 2026-10-30..11-06 (about 80 gold rows). Widening to them is a later
  amendment, once one day of this release's row count is measured. The nightly discovery top-N has no replay-only
  registration path apart from the learning class's own controls.

## K

No K. A trial is a read (ADR 0085: "each look is a scheduled, charged alpha spend"; the learning class charges its K
row at registration). Recording without reading is not a look:

- No row is added to the trial registry.
- No `learning` block or `qualification` is added. The RBR*/RBRC* rows carry neither and are never read for edge.
- The revived rows keep the K rows they already have; none is re-registered.
- Any later read of these candidates for edge is a new pre-registered card. It is charged then, and the retirement
  date splits the candidates it may read.
- The learner gives retired arms no weight (`exploration_data.entry_exclusions`); the labels feed the replay-label
  pool only.

## Reading these rows

A row recorded after its arm's `retired_day` with blocked `replay_only` is a replay record, never a fill, and never
part of the arm's pre-registered read window. Readers split on `decision_utc` against `retired_day`.

## Rollback

Remove `control_mode: replay_only` from the revived rows, delete the six RBR*/RBRC* rows and their six icm-probe
registrations, and restart the engines. The engine change is inert without replay-only retired rows.
