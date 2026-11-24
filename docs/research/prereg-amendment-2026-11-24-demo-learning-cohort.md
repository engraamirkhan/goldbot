# Amendment 2026-11-24: the self-retrain's ten retired arms trade again as a demo learning cohort

Status: **in force with the release that carries it** (owner 2026-11-24, "it is quite concerning that it is not
trading at all"; PD + research director + CFO: demo money is for learning, real money untouched). ADR 0095. Amends
prereg-amendment-2026-11-24-replay-only-retired-arms.md for ten of its rows; the original is not edited.

## Purpose

Real fills and real costs for the ten retired arms the weekly self-retrain judges (prereg-2026-11-24-self-retrain.md,
`research/self_retrain.PRENAMED`): RBR15, Z3D, Z1P, Z9D, Z10D, S1A, S1B, F1A, Z2P, Z5P. Since PR #424 only GD-1
(GD1D/GD1P, about two orders a day) reaches the broker, so the system measures almost no execution: slippage,
commission, fill latency and stop overshoot on the arms it is re-fitting.

The self-retrain's reads do not change. Its labels are the r2 replay of every candidate, taken or not; a candidate is
labelled the same whether it was recorded replay-only or sent. The fills add a broker record beside the labels; they
never replace one.

## Rows

| Arm | Rule, tf | Retired by (retired_day stands) | Registered |
|---|---|---|---|
| S1A, S1B | session_vwap_fade 5m | control thinning 11-06 point 4 (2026-11-13) | icm-probe |
| F1A | hurst_band_break 15m (cost arm) | profitability-2026-11-12 (a) (2026-11-13) | icm-probe |
| Z1P, Z2P | tick_surge, run_fade 1m | H-90 cost budget (2026-11-13) | icm-probe |
| Z5P | extreme_break 5m | survivor-reads KILL (2026-11-13) | icm-probe |
| Z3D | round_break 5m | H-90 cost budget (2026-11-13) | moved icm-demo -> icm-probe |
| Z9D, Z10D | htf_reclaim, three_horizon 5m | survivor-reads KILL (2026-11-13) | moved icm-demo -> icm-probe |
| RBR15 | redbox_break 15m | replay-only from birth (2026-11-24) | icm-probe |

Each row goes from `control_mode: replay_only` back to `broker`. Run policy only: no row is re-hashed and no id
changes. RBR15's hashed `end_utc` (2026-12-31 22:00 UTC) ends its candidates before the cohort ends.

## Rules

1. **Orders on icm-probe only, minimum lot.** The account names the cohort (accounts.yaml icm-probe
   `exploration.cohort`). No other account registers any of the ten (refused at load,
   `ops.accounts.cohort_clashes`). Every order is 0.01 lot through the RiskGate (`sizing: min_lot`, no adds).
2. **Still retired.** Each row keeps its `retired_day` and `retired_rule`. The learner gives it no weight, it is never
   a qualification and never in a family ranking, and the self-retrain's OOS split is unchanged. Its fills carry
   `evidence_class: learning_cohort`, never `qualified`.
3. **Caps of its own, on top of every existing cap** (none loosened: the RiskGate, the account's GBP 220 day and its
   weekly limit, the exploration budget, the 85-a-day and 30-scalp-a-day entry budget, the hour and session budgets,
   the slots, the margin and notional caps):
   - one open position per arm (`cohort_max_open`);
   - at most 30 cohort fills a UTC day and 4 a clock hour, all ten together (`cohort_daily_entries`,
     `cohort_hourly_entries`). The account's 8-an-hour budget keeps room for GD1P (trading-safety M2);
   - per arm, GBP 15 net loss since 2026-11-24. At the cap the arm stops for good. An entry is refused when the arm's
     loss + its open risk at the stops + the new trade's loss and round trip would reach GBP 15 (`cohort_loss_cap`).
     RBR15's per-trade cap goes from GBP 30 to GBP 15, so one trade never loses more than the arm may in all;
   - the ten together, GBP 50 net loss in the ISO week (Monday 00:00 UTC on). At the cap every arm is held until
     next Monday 00:00 UTC. An entry is refused when the week's loss + every member's open risk + the new trade would
     reach GBP 50 (`cohort_week_cap`). A loss counts in the week it closed (by exit), on fills sent since
     2026-11-24;
   - a close whose record is not written yet counts at its ledger row's loss at the stop plus its cost (else the
     arm's per-trade cap) until the record lands. A given-up close whose arm is unknown holds every cohort entry until
     it is repaired (trading-safety M1);
   - until 2027-01-15. After it every arm stops.
   - nothing is sent until the first ledger read succeeds, or while reads fail (`cohort_unread`, fail closed).
   - an arm stopped by its cap or by the end, or refused at a start outside since..until, records replay-only, as
     before this amendment. The self-retrain's candidate rows never stop. During a weekly hold, candidates are
     recorded blocked `cohort_week_cap`.
4. **The evidence stops that retired them do not apply to a member** while the cohort runs. These are: the daily
   review's evidence-stop retirements (H-61, family, futility, cost, follow and edge-cap rules), the F1A cost-read
   completion, the H-81 cost and edge-cap suspensions, and the learning block's control check and caps. They
   measured edge; the cohort measures fills, and its caps bound the money. Any other retirement written to aux.db
   (the arm lifecycle's, or a future writer's) still stops a member.
5. **Controls stay replay_only.** LCZ1P, LCZ2P, LCZ5P, LCZ3D, LCZ9D, LCZ10D, F1C and RBRC15 still never send.
   LCZ3D/LCZ9D/LCZ10D move to icm-probe with their arms (control_share 0.01, never used), so each arm and its twin
   record on one host and the strategy board keeps pairing them. The self-retrain keys evidence by broker (`icm`), so
   nothing changes there. S0, VEX* and the GD1C* controls are unchanged.
6. **GD-1 untouched**: its orders, controls, kill rules and registrations. No live or real-money account is touched.
7. **Exits are never gated.** A cap refuses entries only; an open position keeps its stop, target, hold and every exit.

## K

**K = 0 additional.** Trading the arms is not a new read. The self-retrain already books one K row per judged pair at
each run, and that read is unchanged. No row is added to the trial registry, no learning block and no qualification is
added. Any edge read of the fills themselves is a new pre-registered card, charged then.

## Reading these rows

- A row after 2026-11-24 with `taken = 1` is a cohort fill. Readers that split on `retired_day` keep doing so.
- **The fills are a selected subset of the candidates.** Selection comes from one open per arm, the per-trade and
  cohort caps, the budget and RBR15's GBP 15 per-trade cap (stops up to about USD 19 at 0.01 lot). Cost and slippage
  reads of them are per fill. Never read the fills' P&L as the arm's edge: that is the replay label's job.
- A week the cohort was held (journal `exploration_cohort_week_cap`) still has every candidate recorded and labelled.
- Cohort fills enter the r2 cost cells point in time, like any fill. `cost_model_version` is on every label, so a
  label shows which cost basis it was charged on.
- In the trade ledger a cohort fill is listed with evidence `not_evidence`. It is never in a strategy ranking row, a
  read's progress, the board's n or an H-61/H-81 stop unit.

## Kill and continue test (2027-01-15)

The cohort ends on 2027-01-15. Before then, the research director reads the cohort's fills against the replay labels
of the same candidates. The reading rule is fixed now:

- **Metrics:** cost per fill; slippage per leg (entry, exit); stop overshoot; mean(label_fill_r - label_sim_r).
- **Cells:** session x order type x timeframe. A cell is read only with at least 30 fills.
- **End early:** if the weekly cap binds 3 consecutive weeks.
- Fill P&L is never compared with a control or counted toward any H-61/H-81 unit. Any edge read is a new card with
  its own K.

The director then decides: renew it under a new card, fold it into an arm that passed the self-retrain, or end it.
Spending and real money stay the owner's.

## Expected volume

No replay rates since 13 Nov exist for these arms: they recorded nothing from 2026-11-13 until PR #435 (2026-11-24).
The last measured rates are icm-probe/icm-demo candidates over 2026-10-26..11-13: S1A 7, S1B 7, F1A 5, Z1P 6, Z2P 12,
Z3D 25, Z5P 5, Z9D 6, Z10D 7 a day, plus RBR15 at about 5 a day (the red-box week). That is about 85 signals a day.
Fills are bounded by the 30-a-day 1m/5m cap, the cohort's own 30 a day and 4 an hour, one open per arm and the
02:00-07:00 UTC gold block, so expect about **30-37 orders a day**: up to 30 scalp, about 4 F1A and 1-3 RBR15. At those arms' recorded net
expectancy (negative, which is why they retired), the GBP 50 weekly cap binds around mid-week. In that case expect
about 100-130 fills a week, and entry silence warns on icm-probe for the rest of the week by design.

## Rollback

1. Drop `cohort` from icm-probe.
2. Move Z3D/Z9D/Z10D and LCZ3D/LCZ9D/LCZ10D back to icm-demo (controls at 0.05), and set RBR15's max_loss back
   to 30.0.
3. Set the ten rows back to `control_mode: replay_only`.
4. Restart icm-probe and icm-demo.

Open positions keep every exit.
