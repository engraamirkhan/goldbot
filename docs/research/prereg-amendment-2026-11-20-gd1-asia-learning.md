# Pre-registration amendment: GD-1 A1 (H-98) as a demo learning arm (2026-11-20)

Principal software engineer, 2026-11-20, written before any fill or replay label under this rule exists. It registers
GD-1 variant A1 (`prereg-2026-11-20-gold-daily.md`, hypotheses H-98, registry trial #3440) on demo as a
**learning-class** arm (ADR 0061 amendment 2026-11-02, `learning-2026-11-02.md`): two arms, **GD1D** on icm-demo and
**GD1P** on icm-probe, each with its own replay-only control, **GD1CD** and **GD1CP**. It is unqualified demo
evidence: never a qualification, never in a family ranking, never real money. Live stays owner-only.

Inputs: the research director's deploy plan (`~/tmp/plan-1120/gold-daily.md`, "Demo deploy plan"), the pre-arming
checks (`~/tmp/plan-1120/gd1-checks.md`: ARM, as a learning arm only), and the quant's kill rule for the learning arm
(section 5). Neither file is in the repo; the figures this amendment relies on are restated here.

## 1. What is being measured, honestly framed

- A1's pass rests on 2003-09. Five of its seven years (2003-07) fail the repo's intraday data-quality ruling
  (2026-11-10: intraday test_old is 2008-09 only), and in those years the 00:00 print sits 1-3 bp below its
  neighbours and the 07:00 print above them, which lifts the measured 7-hour return by about 1-4 bp a day. Disclosed,
  not adjusted (a further historical read would spend the window again).
- On the clean years 2008-09 the effect is about **+2.7 bp/day net**; on 2010-25 (spent by H-04, context only) it is
  **+1.3 bp/day net, +0.9 bp/day excess over drift, 95 % CI -0.42 to +2.25**.
- So the arm measures a **~1-2 bp/day historical drift forward**. At 0.01 lot that is about $0.05 a day: learning,
  not money. It is not a live candidate on this evidence. 2003-09 is spent for this family; from here the evidence is
  forward only.

## Rules

Code: `goldbot/research/arm_rules_clock_hold.py` (rule-code version 1), the same function for history and live.
Spec rows: `config/exploration_arms.yaml` GD1D, GD1CD, GD1P, GD1CP (1h, max_open 1, forward_only).

1. **Entry.** Long on the close of the 1h bar ending at 00:00 UTC, every weekday by the UTC day of that close (Monday
   to Friday; `entry_utc` "00:00", `weekdays` [0..4]). A 15m+ arm has no send delay, so the market order goes on the
   first tick the engine handles at or after 00:00 UTC. No signal, no filter of its own; the common filters of every
   arm apply (a tier-1 blackout at 00:00 drops that day's entry; the rollover and Friday-evening filters never touch
   00:00). ICM's daily break ends about 23:01 UTC in winter (22:01 in summer), so 00:00 is about an hour after the
   reopen, Mondays included. A day without the 23:00-00:00 bar (a holiday close: 2026-12-25, 2027-01-01) has no entry.
2. **Exit.** Time exit at 07:00 UTC: the hold is 7 bars of 1h (`hold_bars` 7), and the engine also closes the position
   on the wall clock at its signal close plus its hold (`exploration._clock_due`), so a missing decision bar never holds
   it past 07:00. No target: a target at 50 R (`target_r` 50, $2,400 away), out of reach.
3. **Stop.** A fixed **$48** catastrophic stop at the broker (`stop_usd` 48). Exits are never gated.
4. **Size.** Exactly the minimum lot, 0.01, inside the exploration fence (section 3).
5. **Control.** Section 4.

## 3. The order path and the stop

- **Why the exploration fence.** As an ordinary signal the RiskGate refuses a rule with no reachable target:
  `time_exit_ev_unsupported` (`goldbot/risk/gate.py`, "p x target would be fiction"). The exploration purpose skips only
  the expected-value rules and only inside its fence: a demo probe_only account, a terminal that reports demo, exactly
  the minimum lot and an arm registered with its per-trade cap (`_exploration_reasons`, `_exploration_sized`). The
  RiskGate is not changed; it remains the only path to the order. Both accounts are demo and probe_only.
- **Why $48 and not 3 x daily ATR.** Three daily ATRs (about $200-250 at today's price) is about GBP 150-190 at 0.01
  lot, above every per-trade ceiling there is; the cap refuses such an entry, it never resizes it (ADR 0043: "1d and
  macro stops above about $53 are skipped"). The deploy plan's 2 % disaster stop (about $83) is above it too. So the
  stop is the widest that fits the **ADR 0043 swing cap of GBP 40**: $48 at 0.01 lot is about GBP 36 at 0.752 GBP per
  USD and GBP 38.4 at 0.80, plus a round trip of about GBP 0.3. One sd of the 7-hour window is about $19 (the plan's
  $190 at 0.10 lot), so the stop is about 2.5 sd away: by the reflection principle it is touched on roughly 1-2 % of
  days, more with fat tails. It is a catastrophe stop, not a bracket. A stop touch is the arm's own exit (counted).
- **Registration.** Style `swing`, `max_loss` GBP 40 on each account. icm-demo already has a swing pot; icm-probe gets
  a swing pot of one slot (slots only, like every pot there; max_open_total, max_positions and the GBP 220 day still
  bind). Learning slots: icm-demo 6, icm-probe 14, both unchanged.

## 4. The matched control (replay-only)

- **Rows.** GD1CD (icm-demo, dedicated to GD1D) and GD1CP (icm-probe, dedicated to GD1P): rule `clock_hold_control`,
  the same side (long), stop ($48), target (50 R) and hold (7 x 1h) as the arm, on the close of the 1h bar at **one
  seeded hour a weekday drawn from 07:00..13:00 UTC** (`entry_hours_utc` [7..13]; the draw is
  `arm_rules.hash_u(control id, the UTC day)`, so the two controls draw independently, restart-safe and the same in
  history and live). Each window ends by 20:00 UTC, before the 20:45 UTC rollover; a row whose window would cross it is
  refused at load.
- **Never broker-traded.** Both rows carry `control_mode: replay_only` (amendment 2026-11-15, #403): every candidate is
  recorded (`exploration_obs`, blocked `replay_only`, mu 0) and labelled by the r2 replay; `exploration._send` refuses
  it before any budget, the RiskGate or an order. Both are named in `refusals_novel.SCOPE_CONTROLS`.
- **Why this control.** The learning-arm validator (`research.arms.control_match_refusal`) accepts a control row of
  role control on the same timeframe with the same fixed bracket (stop, target, hold). It is the deploy plan's
  "control twin: the same lot held 07:00 -> 00:00 (the complement)" cut into 7-hour pieces that the validator can match:
  the complement itself (17 hours through the rollover) has another hold and is refused. Rejected alternatives:
  - a random-side twin at 00:00 UTC: its long half is the arm itself (excess identically 0 on those days) and its short
    half only mirrors the drift; it measures nothing the arm does not;
  - the card's 7/23 x buy-and-hold drift book: not a row the validator can match; it is reported beside the read
    (section 6), never as the control.
- **What it reads.** Arm minus control is the Asia window's excess over the same 7 hours of the western day: the
  session split the card's literature describes (gold up in eastern hours, down in western ones). If western hours
  drift down, this excess is larger than the excess over the whole-day drift; section 6 reports both.
- **The nightly lifecycle** (`research/arm_lifecycle.py`) matches a candidate to its control's candidates in the same
  (UTC hour, side) cell. A clock arm's hour (00) never meets its control's (07-13), so that read HOLDs as not readable
  (above 10 % unmatched) and never decides on evidence; this arm's own kill rule (section 5) decides.

## 5. The kill rule (quant, learning arm only)

Per arm, on its own admissible fills (one a weekday on its account), each check judged **once** on the arm's first
fills (`config/arm_stops.yaml` units GD1D and GD1P, source `GD1`, applied by the daily review through
`exploration_arm_status`; entries only, every exit kept):

1. **Cost.** Kill if the mean **realised round-trip cost > 1.1 bp after 40 fills**. Realised round trip per fill
   (`arm_stops.realised_cost_bp`): the spread crossed at the send (the full spread stands for the half spread of each
   leg), the entry slippage against the requested price (adverse positive) and the broker's commission per oz, in bp
   of the fill price. The exit leg's own slippage has no reference price in the ledger and is not in it (the checks
   measured about 0.15 bp a round trip of slippage on ICM exits; the 1.1 bp threshold sits about 0.5 bp above the
   measured 0.56-0.77 bp all-in). A fill without a readable cost is skipped: the check waits for 40 readable fills.
2. **Loss.** Kill if the **mean net <= -4 bp at 120 fills** (net: the broker's net P&L over the entry notional,
   `arm_stops.net_bp`).
3. **Continue.** At **250 fills** continue only if the **mean net >= +3.5 bp**: below it, kill.
4. Beside them: H-61's swing stop (running net R -10 R within 40 fills), the learning class's **loss cap of GBP 800**
   (20 x the GBP 40 per-trade cap, `learning.loss_cap_gbp`), its read at 300 admissible fills and its end.
5. The controls stop with their arm (`follows`), and the engine retires a dedicated control once its arm may not enter.

Both accounts trade the same price path at the same minute, so their net verdicts will nearly agree; each account's
cost check is its own. The counts in checks 2-3 are per arm, so they stand for weekdays as the quant wrote them.

## 6. Dates, K and the budget

- **Run.** `since` 2026-11-23 (the first entry, Monday 00:00 UTC), `until` 2027-01-04 (inside the class's 45 days).
  About 27 entry days per arm in this run: the 40-fill cost check falls due near its end at the earliest; the 120- and
  250-fill checks need later runs. A further run is a dated amendment with a new `since` and `until`; the arm-stop
  checks count every fill of the arm id ever (not from `since`), so they carry across runs.
- **K +4**, one learning K row per arm and per dedicated control (family `learning_class`, `scripts/
  record_learning_k_rows.py`, written on the brain before the first fill; without its row the arm is refused, fail
  closed). The request to make the registration K-neutral cannot be met inside the learning class: its rows count in K
  and in the quarter's budget (`learning-2026-11-02.md` "K and the budget"). GD-1's own history trial (#3440) is not
  re-counted.

| arm | account | rule, timeframe | stop / target / hold | k_id | dedicated control | control rule | control K row |
|---|---|---|---|---|---|---|---|
| GD1D | icm-demo | clock_hold, 1h, 00:00 UTC weekdays | $48 / 50 R / 7 x 1h | 1fc498509f07 | GD1CD | clock_hold_control, seeded 07-13 UTC, replay_only | 8e83a9a6de3c |
| GD1P | icm-probe | clock_hold, 1h, 00:00 UTC weekdays | $48 / 50 R / 7 x 1h | 764e5fe04d07 | GD1CP | clock_hold_control, seeded 07-13 UTC, replay_only | d76e9e0e1c20 |

- **Reported with each read (descriptive, never a gate):** the arm's mean net bp a day and its fills' mean realised
  cost; the control's mean r2 replay label over its candidates; the arm's own r2 replay label over every candidate,
  taken or refused; the 7/23 x buy-and-hold drift of the same days from the engine's bars; the 00:00 entry slippage of
  every fill (the checks flagged hour-00 slippage on n = 7).

## 7. Other rules in force, disclosed

- **HAR abstention** (`prereg-2026-11-07-n3-har.md`): gold learning arms are in its scope, so on a predicted
  low-range day (about 1 in 5) the arm's entry is refused `har_low_range` and the candidate recorded and replayed.
  The kill rule's fills are therefore the other days; the replay labels cover every weekday. Not changed here.
- **The 02:00-07:00 block** (`prereg-amendment-2026-11-07-kill-only.md`) refuses entries decided in [02:00, 07:00);
  00:00 is outside it and a position opened before 02:00 keeps every exit.
- **The daily entry budget**: the 00:00 entry is the first of the UTC day in the asia segment, which always has at
  least one fill of budget.
- No swap: the window never crosses the rollover.

## 8. What does not change

The RiskGate, the trial budget rules, the holdout (no bar at or after 2025-10-01 is read; no historical read at all),
the phase gate, the live unlock and every other arm. No threshold, window or reading rule of GD-1's card changes; this
amendment adds a forward measurement only.

## Bias statement

Written after GD-1's result (A1 PASS on 2003-09, decayed since 2010) and the pre-arming checks were read, and before
any forward fill or label. The arm's rule is A1's verbatim; the stop, the control hours and the kill thresholds were
chosen from the per-trade caps, the deploy plan and the quant's rule, not from any outcome under them.
