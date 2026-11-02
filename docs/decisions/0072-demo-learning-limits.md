# 0072: Demo learning limits

Status: proposed (implementer, 2026-11-02; trading-safety and quant review owed before merge)

## Context
The owner, 2026-11-02: "fix the limits of our system to trade ... for the system to be not bound to test, learn and
improve signals". Asked whether a demo "learning" class should run beside the qualification rule (ADR 0061), the owner
answered YES.

On 2026-11-02 the three demo learning hosts (icm-demo, icm-probe, vantage-demo) were bound by limits that were never
meant as risk limits, only as allocation and convenience:
- ADR 0061 refused every strategy arm, because none had qualified. Only controls, two cost arms and one owner
  experiment entered.
- ADR 0043's £ pots and per-arm day budgets refused entries inside the account's own day and per-trade caps.
- A hold that reached the Friday cut was refused all week. On a 4h or swing hold, that meant from Wednesday.
- The tier-1 news blackout refused every demo entry, although the arms' own rules already skip candidates in the
  blackout window, and their labels need those minutes.
- The host cap was 18 positions.
- Health raised a false "LOSS CAP hit" on demo accounts. It read the settings' 2% / 5% caps, not the ADR 0032
  ceilings the gate applies (5% / 6%).

## Decision
These changes apply on demo probe_only accounts only. Each flag is refused at load anywhere else, and each default is
the old behaviour.

1. **Learning class** (ADR 0061 amendment 2026-11-02). A `role: learning` row enters unqualified inside its own
   `learning` block, with its own stops (the details are in the amendment).
   - The engine (`engine/exploration_learning.py`) retires the arm at whichever comes first: 300 admissible fills
     (`exploration_arm_learning_read_due`), its loss cap (`exploration_arm_learning_loss_cap`, an unreadable P&L
     counted at its stop plus its round trip), or past `until`.
   - An unreadable ledger refuses learning entries. Every verdict is recomputed from the ledger, so it holds across a
     restart.
   - At most `exploration.learning_max_open` (6) learning positions are open at once (`learning_slots`).
   - Every record now carries `evidence_class` (the arm's role at send) and `in_blackout`.
2. **Pot budgets off** (`exploration.pot_budgets: false`). The engine drops the `pot` and `arm_budget` rooms; a pot is
   its slots only and needs no `gbp`. Still binding: the per-trade cap with the round trip (`arm_cap`), the
   exploration budget (`budget_share` x the day), every slot, the global and probe-slot caps, net lots and the RiskGate.
3. **Weekend flat** (`demo_weekend_flat`). At the Friday cut every own position closes, winners too (`weekend_flat`,
   a censored label), and a refused close is retried every tick. An entry is refused only in the last 15 minutes before
   the cut (`WEEKEND_ENTRY_BUFFER_S` = 900). A rule that follows the cut (WFRI) keeps its rule. The gate's account-level
   `weekend` reason is unchanged.
4. **News blackout entries off** (`news_blackout_entries: false` -> `RiskLimits.blackout_entries` false; never on
   the paper stand-in). The engine still computes the blackout (`news_blackout=True`) and stamps it on every record.
5. **Volume**. The host cap goes from 18 to 24 (`max_positions` le 24) with `max_open_total` 23. Pot slots go up about
   1.35x: icm-demo 11/10/3/2, icm-probe 19, vantage-demo 11/6/2.
6. **Probe sleeves (f)**. icm-probe cost_probe 0.1363 -> 0.2363 (exploration 0.7637); vantage-demo 0.075 -> 0.175
   (exploration 0.9 -> 0.825). Each day's shares sum to 1.0 under the unchanged £ limit.
7. **Health**. On a demo account the loss-cap check reads the engine's `limits_current`.

Not changed: the daily and weekly limits, the drawdown stages, the throttle, the per-trade arm caps, the margin,
notional and net-lot caps, every data, bridge and order-integrity guard, every exit, and every live account.
`engine_limits` of icm-live and vantage-live is identical but for the new key `blackout_entries: true`.

## Consequences
- Many more demo entries: 15 learning arms and 7 dedicated controls start, all pots lose their £ binding, blackout and
  late-week entries run, and the host cap is higher. More fills is the point (owner 2026-10-25/27: hundreds a day).
  The demo daily and weekly limits are what bound the loss now. A demo day can reach its £220 / £200 limit sooner.
- Weekend-flat closes are censored, so they never count as a label. A week's last holds are shorter in practice.
- Learning evidence is labelled and kept apart. It is never a qualification, never in a family ranking, never
  allocated by the bandit and never trains a style model. A good read leads to a pre-registered history qualification.
- The 22 K rows (`scripts/record_learning_k_rows.py`) count in K and in the 2026Q4 budget. If the budget is short,
  the learning arms stay refused (fail closed) until the owner raises it. The class needs no other owner step.
- Rollback: on each demo account set `pot_budgets: true` with the old `gbp` and slots, `max_positions` 18 /
  `max_open_total` 17, drop `demo_weekend_flat` and `news_blackout_entries`, restore the probe shares, then restart.
  To stop the learning class alone, set `learning_max_open: 0` and restart. Open positions keep every exit either way.
- What would change this: a demo week that hits its weekly 6% twice running, or learning fills whose costs or
  censoring make the reads unusable. Either means the limits return to the ADR 0043 pots.
