# 0072: Demo learning limits

Status: accepted. Trading-safety review of PR #295 2026-11-02: OK on conditions M1-M3 and L1-L4, all applied. The
research lead's quant rulings (1)-(5) of the same day are applied too.

## Context
The owner, 2026-11-02: "fix the limits of our system to trade ... for the system to be not bound to test, learn and
improve signals". Asked whether a demo "learning" class should run beside the qualification rule (ADR 0061), the owner
answered YES.

On 2026-11-02 the three demo learning hosts (icm-demo, icm-probe, vantage-demo) were bound by limits meant for
allocation and convenience, not as risk limits:
- ADR 0061 refused every strategy arm, because none had qualified. Only controls, two cost arms and one owner
  experiment entered.
- ADR 0043's £ pots and per-arm day budgets refused entries inside the account's own day and per-trade caps.
- A hold that reached the Friday cut was refused all week. For a 4h or swing hold, that meant from Wednesday.
- The tier-1 news blackout refused every demo entry, although the arms' own rules already skip candidates in the
  blackout window and their labels need those minutes.
- The host cap was 18 positions.
- Health raised a false "LOSS CAP hit" on demo accounts: it read the settings' 2% / 5% caps, not the ADR 0032
  ceilings the gate applies (5% / 6%).

## Decision
Every change below applies on demo probe_only accounts only. Each flag is refused at load anywhere else, and each
default is the old behaviour.

1. **Learning class** (ADR 0061 amendment 2026-11-02, docs/research/learning-2026-11-02.md). A `role: learning` row
   enters unqualified inside its own `learning` block, with its own stops.
   - The engine (`engine/exploration_learning.py`) retires the arm at whichever comes first:
     - 300 admissible fills (`exploration_arm_learning_read_due`);
     - its loss cap (`exploration_arm_learning_loss_cap`), with an unreadable P&L counted at its stop plus its round
       trip;
     - a day past `until`.
   - Only fills sent on or after the block's `since` (the registration day) count (quant (1)). Censored fills are
     reported apart (quant (5)).
   - Each entry is refused once realised loss + open risk at the stops + the new trade's loss would reach the cap
     (trading-safety L1).
   - An unreadable ledger refuses learning entries. The verdicts are recomputed from the ledger, so they hold across a
     restart (L3).
   - At most `learning_max_open` (6) learning positions are open at once.
   - **Controls.** Every learning arm has its own dedicated control (quant (2)/(3)): the same timeframe, bracket and
     window. The control stops with its arm, and when its arm is suspended by its cost stop (L2).
   - **Shares.** Learning and strategy shares are normalised to sum to at most 1 within a style (quant (4)).
   - **Records.** Every record carries `evidence_class`, `in_blackout` and `blackout_kind`.
2. **Pot budgets off** (`exploration.pot_budgets: false`).
   - A pot without a `gbp` is slots only: the `pot` and `arm_budget` rooms are dropped.
   - A pot that still names its `gbp` keeps both rooms (trading-safety M1). icm-demo's growth pot keeps its £80, so
     the GS1/GS1C owner-experiment day of the ADR 0061 amendment 2026-11-01 is unchanged.
   - Still binding: the per-trade cap with the round trip, the exploration budget, every slot, the global and
     probe-slot caps, net lots and the RiskGate.
3. **Weekend flat** (`demo_weekend_flat`).
   - At the Friday cut every own position closes, winners too (`weekend_flat`, a censored label). A refused close is
     retried every tick.
   - A position that appears after the cut completed is closed at the next refresh, as in the always-open lane (L4).
   - An entry is refused only in the last 15 minutes before the cut.
   - On a listed thin day (config/holidays_cme_metals.yaml: 27 Nov, 24 and 31 Dec included) the effective cut is the
     earliest CME holiday halt (12:00 Chicago) less 15 minutes. An entry whose hold reaches it, or one sent between it
     and the 17:00 Chicago reopen, is refused (M3). An unusable list falls back to the old hold rule.
   - Unchanged: a rule that follows the cut (WFRI) keeps its rule, and the gate's account-level `weekend` reason stays.
4. **News blackout entries off** (`news_blackout_entries: false` -> `RiskLimits.blackout_entries` false; never on
   paper or live).
   - Only the tier-1 calendar window is lifted. A news shock, or a blackout of unknown kind, still refuses entries
     (M2).
   - The engine checks for shocks inside a lifted tier-1 window too, and a shock outranks it.
   - The kind is in the state (`AccountState.blackout_kind`) and on every exploration record.
5. **Volume.**
   - Host cap 18 -> 24, `max_open_total` 23, pot slots about 1.35x (icm-demo 11/10/3/2, icm-probe 19, vantage-demo
     11/6/2).
   - **The effective ceiling is the notional cap, not these numbers.** The engine's global cap is
     `floor(0.9 x min(6 x equity / notional of a minimum lot, equity / (3 x margin of one)))`.
   - With gold near $4,000 an oz and £0.78 to the dollar, a 0.01-lot position is about £3,100 of notional. At £10,000
     of equity the notional cap is about 19 minimum lots, so about 17 positions after the 0.9 share. At £11,000 it is
     about 21 lots, so about 19 positions. The 300% margin rule (about 21 lots at £10,000) binds later.
   - So the 23 / 24 never bind at today's balances. The cap rises and falls with equity and the price, and that cap is
     what binds.
6. **Probe sleeves (f).** icm-probe 0.1363 -> 0.2363 (exploration 0.7637); vantage-demo 0.075 -> 0.175 (exploration
   0.9 -> 0.825). Each day's shares sum to 1.0. K10's bound (scheduled probes at most 50% of the sleeve) is unchanged.
7. **Health.** On a demo account the loss-cap check reads the engine's `limits_current`.

**Not changed:** the daily and weekly limits, the drawdown stages, the throttle, the per-trade arm caps, the margin,
notional and net-lot caps, every data, bridge and order-integrity guard, every exit, and every live account. The
`engine_limits` of icm-live and vantage-live keeps every existing field identical; the only difference is the new key
`blackout_entries: true`.

## Consequences
- **More demo entries.**
  - 15 learning arms and 15 dedicated controls start.
  - The pots lose their £ binding, except icm-demo's growth pot.
  - Entries run through tier-1 windows and late in the week.

  The demo daily and weekly limits and the notional cap now bound the loss. A demo day can reach its £220 / £200 limit
  sooner.
- **Weekend-flat closes are censored labels.** A week's last holds are shorter in practice.
- **Learning evidence is labelled and kept apart.** It is never a qualification, never in a family ranking, never
  allocated by the bandit and never trains a style model. A good read leads to a pre-registered history
  qualification.
- **K rows.** The 30 K rows (`scripts/record_learning_k_rows.py`) count in K and in the 2026Q4 budget. If the budget is
  short, the learning arms stay refused (fail closed) until the owner raises it.
- **Rollback.**
  - Limits: on each demo account set `pot_budgets: true` with the old `gbp` and slots, `max_positions` 18 and
    `max_open_total` 17, drop `demo_weekend_flat` and `news_blackout_entries`, restore the probe shares, and restart.
  - The learning class alone: `learning_max_open: 0` and restart.
  - Either way, open positions keep every exit.
- **What would change this.**
  - A demo week that hits its weekly 6% twice running: the limits return to the ADR 0043 pots.
  - Learning fills whose costs or censoring make the reads unusable: the same.
