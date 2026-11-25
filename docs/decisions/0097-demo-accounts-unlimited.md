# 0097: Demo accounts without limits (owner decision 2026-11-25)

Status: proposed (2026-11-25, branch feat/demo-no-limits; reviews: trading-safety, quant, code, security, sre)

## Context

Owner, 2026-11-25: "there are no limits anymore for demo accounts", then "allow absolutely anything". What the owner
kept: the RiskGate is the only path to an order, the broker carries a stop on every order, exits are never gated, the
demo margin floor is one switch, and real money is unchanged. Statistical gates are not limits and stay (execution
plan 2026-11-25, A8).

Trading-safety reviewed the first diff on 2026-11-25. Its findings were: H1 (exits can starve behind entries once the caps lift: the MT5 box is serial and the
tick thread waits for every entry's reply); H2 (no ceiling on what the management loop and the broker can carry; the
broker's own limit retcodes 10040/10033 were never checked); M1 to M6; and L1 to L4. Each is answered below.

The same owner words, "allow absolutely anything", are also the owner decision record for the **2027Q1 trial-budget
override** (execution plan 2026-11-25 §0 and §6: "decided by 'allow absolutely anything' (11-25)"). This ADR records
that as an owner decision for the demo half. The budget half is ADR 0099 (ceiling 250, plan 120) and
`config/owner_decisions/2027Q1-trial-budget.md`. Nothing here changes the trial budget, the holdout, the phase gate or
the live unlock.

## Decision

### 1. The switch

- `demo_unlimited: true` in config/accounts.yaml. It is allowed only on an account that is `mode: demo` and
  `probe_only`, and is refused at load anywhere else (`Account._demo_limits_fenced`).
- `ops.services.engine.engine_limits` copies the flag into `RiskLimits.demo_unlimited` against the real terminal only,
  never the paper stand-in.
- `RiskGate.unlimited(intent)` is true only when the account flag is set, the account is probe_only and the intent is
  demo by the one predicate (`is_demo`: registry, phase file, terminal). Any non-demo fact puts every cap back (fail
  closed).
- Rollout (execution plan D22): every account ships with the flag set to **false**. It is set true on icm-probe only
  at the canary (Sun 2026-11-29 22:00 UTC, 24 h read Mon 22:00 UTC). The other demo accounts follow only after a clean
  canary and the W2-4 EDF dispatch.

### 2. Kept (K1-K6), each pinned by a test

- K1: the RiskGate is the only order path. Exploration, cost probes, auto and approvals all go through `check`, and
  the bridge smoke goes through `check_operational`.
- K2: every order carries its broker SL in the request. `spread_over_stop` still refuses a stop inside the spread.
- K3: exits are never gated and never queued behind entries. See section 4.
- K4: the demo floor is one switch, `risk.demo_floor.enabled`.
- K5: live and real money are unchanged. `test_the_limits_of_every_account_that_is_not_demo_probe_only_are_byte_identical_to_before_adr_0097`
  pins `engine_limits` of every non-probe account to origin/main 0bb07649. These refusals still win over every new
  path: owner halt, supervisor halt, `account_disabled`, data and terminal integrity, rollover and the weekend.
- K6: no path widens a stop or adds size after entry. The only exception is a pyramid add, which is a new gated entry.

### 3. The floor (the one guard left)

- `risk.demo_floor` (settings.yaml): entries are refused `demo_equity_floor` below 20 % of the closed-balance
  high-water mark.
- Entries are refused `demo_margin_floor` when the margin level after the trade would fall below 200 %. This uses the
  broker's own margin figure, or 1:20 when the broker cannot answer.
- Entries only; exits are never gated.
- **M4 (basis):** the mark is flow-adjusted (ADR 0042, demo lowers on a withdrawal):
  - a reset down booked as a balance withdrawal lowers the mark;
  - a reset up or a top-up raises it;
  - a withdrawal the deal history does not reconcile leaves the mark, and the floor then refuses (fail-safe).
  - Health `demo_floor:<account>` FAILs at the floor, so a stuck floor is an alert, not a silent stop
    (tests/test_demo_floor_basis.py).
- **M5:**
  - Health WARNs at half the mark. Exploration journals `exploration_dd_warning` at 50 %, well before the floor.
  - One arm's day (realised loss, plus open risk at its stops, plus the new trade's loss and round trip) is held within
    `ARM_RUNAWAY_FRAC` = 10 % of the mark (`arm_runaway`). This is containment of shared learning capacity, not a cap
    on normal learning: £1,000 a day on a £10,000 demo is 30-200 minimum-lot stops, so only a fault reaches it.
  - Isolation of high-variance families (RL sizing, 1d/1w gaps) on their own demo account is the structural fix. It is
    recorded here for the W2/W3 lanes that introduce them.

### 4. Exits before entries and the capacity ceiling (H1, H2): engineering, not risk

- On an unlimited account (goldbot/engine/order_send.py):
  - **Exit-first.** No entry while any book has:
    - a close the engine decided and has not got filled (excluding one backing off because the broker says the
      market is closed);
    - a kill-switch flatten pending;
    - a stop modify whose retry is due.
  - **Entry duty ceiling.** No entry starts once this pass's entries have held the tick thread 1.5 s, the bridge
    order RTT p95 bound. No entry starts while entries held it for half of the last 10 s.
  - **Broker capacity.** A 10033 (orders) or 10040 (positions) answer is a definite reject. It holds entries while the
    account holds at least as many positions as it did then. It is never a retry loop.
- A yield is an `EntryYield` (an `OrderInFlight`). Every caller journals it `entry_refused:<reason>` and never treats
  it as a tick error. The exploration send loop stops and keeps the remaining signals queued for the next pass, where
  their staleness limit still applies.
- **SLO.** The gap between loop passes with a position open is the longest a due exit waits. The engine publishes the
  p99 of the last 600 gaps (`exit_wait_p99_s`). Health `exit_latency:<account>` FAILs above 5 s on an unlimited
  account and WARNs on any other.
- **Measured.** On the paper bridge with modelled call times (order 1.5 s, close and modify 0.15 s, read 0.05 s), 300
  open positions, two exits due and six entries wanted every pass:
  - with exit-first and the duty ceiling: exit wait p99 < 5 s;
  - without them: p99 > 5 s on the same load (tests/test_exit_first.py, the control).
  - Quiet passes cost about 4.5 µs of CPU per open position at p50 (1.4 ms at 300, 4.5 ms at 1,000). Their bridge
    calls do not grow with n (one positions read, one tick read).
- **Capacity ceiling = 300 open positions** (`gate.CAPACITY_CEILING`, reason `capacity_ceiling`). This is the load at
  which the SLO was measured to hold. Above it the SLO is unmeasured. It is raised only on a new measurement recorded
  here. It is an engineering ceiling that protects K3, not a risk limit.
- Not measured: brain production pass times against n. The canary reads `exit_wait_p99_s` per hour. On Vantage a
  single fill can hold the tick thread up to `WAIT_S` (5 s), so the SLO there needs the W2-4 dispatch (one worker per
  terminal) before that account is unlimited.

### 5. Inventory of every pre-gate and gate refusal under the flag (M3)

Classes:
- **limit**: an account-configured cap. It is lifted.
- **statistical**: the protocol evidence is judged on, a pre-registered rule or a research verdict. It stays.
- **integrity**: data, terminal, halt or market structure. It stays.
- **engineering**: K3 capacity. It stays and is new.

Tests are under tests/; `gate` = test_gate_demo_unlimited.py, `expl` = test_exploration_demo_unlimited.py,
`exit` = test_exit_first.py.

| Refusal (where) | Class | Under the flag | Test |
|---|---|---|---|
| max_positions, daily_cap, weekly_cap, drawdown_halt, news_blackout (tier-1 and shock), spread_too_wide, tier2_blackout (gate account) | limit | lifted | gate::test_with_demo_unlimited_no_account_cap_refuses_a_demo_entry |
| entry windows: reopen_window, holiday_thin (gate `window_reasons`) | limit | lifted | gate::test_with_demo_unlimited_no_account_cap_refuses_a_demo_entry (tier2 row; same code path) |
| throttle, same_direction_cap, heat_cap, open_risk_unknown/day_start_unknown for the projections (gate) | limit | lifted | gate::test_with_demo_unlimited_the_throttle_heat_and_open_risk_projection_do_not_refuse |
| daily_loss_limit_abs, combined/group_exposure_cap, margin_level_floor 300 % (gate sized) | limit | lifted (demo floor instead) | gate::test_with_demo_unlimited_the_absolute_daily_loss_limit_and_combined_exposure_do_not_refuse, ::test_the_floor_uses_the_brokers_margin_not_the_retail_1_to_20_figure |
| min_lot_exceeds_risk (1 % `min_lot_risk_cap`, swing stops included) | limit | lifted | gate::test_with_demo_unlimited_the_minimum_lot_is_placed_whatever_its_risk_against_equity, ::test_a_swing_arm_whose_minimum_lot_risks_more_than_the_1_percent_exception_is_placed_when_unlimited |
| exploration_arm_cap for a minimum-lot arm (gate fence) | limit | lifted | gate::test_with_demo_unlimited_the_arm_per_trade_cap_no_longer_refuses_but_the_lot_stays_the_minimum |
| drawdown stage machine and kill switch (`RiskGate.stage_step`) | limit | no stage move | gate::test_with_demo_unlimited_the_drawdown_stage_machine_never_moves_so_no_kill_switch_fires |
| bridge smoke caps (`check_operational`, M2) | limit | lifted on demo only | gate::test_an_operational_order_on_an_unlimited_demo_account_keeps_no_cap_either |
| exploration daily/hourly/session entry budgets | limit | lifted | test_exploration_demo_unlimited::test_with_the_flag_the_daily_entry_budget_no_longer_refuses |
| global_cap, cap_unknown, probe_slot, pot_slots, learning_slots, same_side (net lots), pot/arm_budget/budget rooms | limit | lifted | expl::test_with_the_flag_every_slot_room_and_budget_cap_lets_the_entries_through |
| 8 % drawdown pause (`exploration.paused`) | limit | lifted | expl::test_the_drawdown_pause_is_lifted_but_an_unknown_drawdown_still_pauses |
| cohort caps: arm loss cap, weekly hold, one open per arm, day/hour fills, cohort_unread (ADR 0095, accounts.yaml) | limit | lifted | test_demo_learning_cohort_unlimited (both tests) |
| arm max_open (frozen, hash-checked ARM-SPEC) | statistical | kept (the S3 overlap design effect) | expl::test_the_arms_frozen_max_open_still_binds_with_the_flag |
| learning arm loss cap and `until` (registered spec, kill rule of the 11-02 learning class) | statistical | kept | test_exploration_learning_unlimited (both tests) |
| entry_refusals (prereg kill-only amendment, e.g. window_0207), control_thinned, cost_suspended (H-81), retired/unqualified/edge-capped arms, replay_only | statistical | kept | expl::test_with_the_flag_a_replay_only_row_still_never_sends; the rest are unchanged code paths behind `free` (tests/test_entry_refusals.py, test_exploration_engine.py) |
| negative_ev, target_below_cost_floor, standard_account_edge/15m, time_exit_ev_unsupported (gate model checks) | statistical | kept | unchanged; tests/test_risk_gate_split.py |
| supervisor_halt, owner_halt, account_disabled, drift_system_halt, rollover, weekend, market_closed, stale_data, tick_error, terminal_account_changed, server_clock_mismatch, bridge_incompatible, exposure_unknown, data_recovering, unconfirmed_order_pending, equity_unconfirmed, account_class_unknown, invalid_state | integrity | kept | gate::test_with_demo_unlimited_halts_market_hours_and_data_integrity_still_refuse |
| spread_over_stop, exploration_not_min_lot, exploration_arm_unregistered, the demo-terminal fence, the learning-probe and cost-measurement fences | integrity (K2, fence) | kept | gate::test_with_demo_unlimited_a_stop_inside_the_spread_is_still_refused, ::test_with_demo_unlimited_the_exploration_fence_still_refuses |
| weekend_hold (Friday cut), not_auto (demo/auto lock), loss_unknown, open_risk_unknown (exploration) | integrity | kept | unchanged code paths (test_exploration_engine.py) |
| unknown drawdown pauses (`exploration.paused`) | integrity | kept | expl::test_the_drawdown_pause_is_lifted_but_an_unknown_drawdown_still_pauses |
| demo_equity_floor, demo_margin_floor | floor (K4) | new | gate::test_the_floor_* |
| arm_runaway (M5) | containment | new | expl::test_one_runaway_arm_is_held_before_it_can_take_the_shared_demo_to_its_floor |
| capacity_ceiling, exit_first, entry_duty, broker_capacity | engineering (K3) | new | gate::test_the_capacity_ceiling_refuses_an_entry_at_the_measured_open_position_count; tests/test_exit_first.py |
| PER_PASS (4 exploration orders a book's pass) | engineering | kept | unchanged (test_exploration_engine.py) |

Not inventoried as entry refusals, and unchanged on these accounts:
- the runner strategy path's dynamic-limits controller (risk/adaptive.py). It sets the gate's terms; on an unlimited
  account the gate ignores the terms it would refuse on.
- the supervisor's combined caps. Probe engines are already left out (supervisor.py, ADR 0037 amendment 4).

### 6. M1: the news-shock refusal

The ADR 0072 M2 rule ("a shock always refuses") is **lifted** on an unlimited demo account, by the owner's rule. Every
exploration fill carries `in_blackout` and `blackout_kind` ("news_shock" or "tier1"). Cost and learning reads can
therefore leave shock-window fills out (`test_a_fill_in_a_news_shock_window_is_sent_and_tagged_so_reads_can_drop_it`).
The default for cost-of-record reads is the quant reviewer's call.

### 7. Evidence tags

Every exploration record sent on an unlimited account carries `limits_epoch: "unlimited"` (JSON body, no migration).
Every engine `order` journal row there carries the same tag. Other accounts' rows are unchanged. Sizes, concurrency and
shock-window fills under this epoch do not transfer to a capped or live account. The quant reviewer rules on which
reads keep them.

### 8. Small findings

- L1: the drawdown stage does not move while the flag is on (`RiskGate.stage_step`).
  - `update_stage` stays byte-identical: it is the owner's re-arm path (tests/test_demo_roll.py pins it).
  - When the flag goes off, the next read takes the computed stage at once, possibly HALTED (fail closed). Only the
    owner's /rearm clears that.
- L2: `unlimited()` without an intent in exploration is backstopped by the demo/auto lock and the gate's `is_demo`.
  This is accepted as is.
- L3: this ADR's number was reserved through docs_assemble (0096 is held by the statistics-integrity lane).
  Traceability row R180 covers this change and K1-K6. The rows of the caps it lifts keep their text: they still hold
  on every account without the flag.
- L4: the gate snaps the sized lot to the instrument's own `volume_step` (`gate.on_step`). For gold (0.01) this is the
  same float as before. A 0.001-step minimum lot no longer becomes 0.0.

## Consequences

- Throughput is decided by the duty ceiling and the broker, not by caps: at a 1.5 s order, about one entry per pass and
  at most half of every 10 s.
  - This is enough for the plan's 150-250 demo trades a day per account.
  - "Allow anything" shows up as more trades only where fills are fast (ICM). Vantage waits for W2-4.
- M6: the same-side and net-lot caps are lifted. Opposite-position fills (76 % of fills, about £417, an upper bound
  that counts fills, not legs) raise account cost bleed until netting (gap 3) lands. This is expected and accepted for
  learning.
- A wiped shared demo halts all learning. The floor at 20 % is a last resort; the 50 % warning and `arm_runaway` come
  first.
- The engine state file gains six keys on every account: `exit_wait_p99_s`, `exit_wait_max_s`, `exit_wait_n`,
  `exit_slo_s`, `entry_duty` and `demo_unlimited`. tests/data/engine_store_golden was regenerated for that. A
  per-case check found the six keys to be the only difference in all 192 cases.
- New health rows `exit_latency:<account>` and `demo_floor:<account>`. `daily_cap`/`weekly_cap` rows are not shown for
  an unlimited account (no such cap there). RUNBOOK sections "Exit latency" and "Demo floor" say what to do.
- Demo results under this epoch are not live evidence. The live unlock, the phase gate, the trial budget and the
  holdout are untouched.
- What would change this: a canary with orphans > 0, broker-SL coverage < 100 %, an `exit_latency` FAIL, or the floor
  crossed without an alert. Any of these sets the flag back to false (one line) and is reviewed before a retry.
