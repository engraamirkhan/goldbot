# Amendment 2026-11-15: replay-only gold control, the S0R exemption and the replay-parity monitor

Status: **in force with the release that carries `control_mode`** (performance-analyst spec 2026-11-15, PD + PO).
Amends prereg-amendment-2026-11-06-control-thinning.md (the original is not edited). Companion amendments of the same
day: prereg-amendment-2026-11-15-eval2-s5-calibration.md (EVAL-2 section 5) and
prereg-amendment-2026-11-15-hv-rule3-replay-control.md (HV Rule 3).

## Why

- S0 was "replayed" only as a side effect of the 11-06 thinning cold-start rule: a pair with no active arm takes
  p = 0, so every S0 candidate was recorded `control_thinned` and labelled, and none was sent. That is a replay twin by
  accident. It must be a rule, so that a later change to the pairing cannot silently put S0 back on the broker.
- A retirement (`retired_day`) is the wrong tool: a retired arm records no candidates, so S0 would leave no twin.
- S0R is a cost arm by its own registration (arms-2026-10-25.md section 4: "Read once at n = 100 stop exits"), and
  the 11-06 amendment kept it ("kept: S0R (overshoot read)"). Its thinning pairs it with the gold scalp edge arms,
  which have retired, so it was starved at 86 stop exits, short of its read. That is a defect in the 11-06 rule, not
  a decision anyone took.
- The 11-06 text says "S0 stops with S1A/S1B on 2026-11-13". That stop never reached config/exploration_arms.yaml.

## 1. Control-thinning amendment (amends prereg-amendment-2026-11-06-control-thinning.md point 2 and point 4)

1. **An unpaired gold random control is `replay_only`** (`control_mode: replay_only` on its spec row; not hashed, run
   policy like a retirement). Its candidates are recorded in `exploration_obs` and labelled by the r2 replay like any
   other candidate. No order is ever sent: the engine refuses it first, before the daily budget, the RiskGate, an
   order intent or the order core (`goldbot/engine/exploration.py` `_send`; blocked reason `replay_only`, mu 0).
   Today that is **S0** only. S0 is not retired: it keeps its row, its id, its K and its registry row.
2. **S0R is exempt from control thinning** (propensity 1) until it reaches **100 stop exits** (its stop or the
   engine's stop close, counted in the host's ledger over every account). Then it stops itself with
   `stop-exit read complete` (`exploration_arm_cost_read_complete`, `stop_exits` 100), the same path the cost arms
   F1A/G1A take at their cost reads. The day, hour and session budgets still bind it. If the section 4 read comes out
   inconclusive, its own registered extension (once, to n = 200) needs a dated amendment to raise the count.
3. **S1A and S1B carry `retired_day: 2026-11-13`**, as the 11-06 text said (point 4: "S0 stops with S1A/S1B on
   2026-11-13"). Neither has had an entry since 2026-10-30 (unqualified; cost-budget-audit-2026-11-13.md), so no fill
   is affected. S0 does not stop: it becomes replay-only under 1.
4. Unchanged and broker-traded: GS1C, IEX1H, VEX15, VEX1H.

## 2. What does not change

- K is unchanged. No arm is added, removed or re-registered; no config_hash, rule, timeframe, params or max_open
  changes. The edits to config/exploration_arms.yaml are the unhashed `control_mode` of S0 and the `retired_day` /
  `retired_rule` of S1A and S1B.
- No threshold, reading rule, window or matched-control method of any read changes here (HV Rule 3's control source
  is the companion amendment's).
- Exits are never gated. The RiskGate is still the only path to an order; a replay-only control has no path at all.
- S0's fixed share of the scalp pot (`control_share` 0.25) stays as configured: the other arms' room is not widened
  by this amendment.

## 3. The replay-parity monitor (a step of the daily review; `goldbot/research/replay_parity.py`)

A replay twin is only as good as the replay. The monitor reads, every day, whether the r2 replay still agrees with
what real fills do. It uses the EVAL-2 S5 quantity and population (eval2-method section 6, prereg-2026-11-09-eval2.md
section 5). Revised before merge on the quant and code reviews of 0fb8fbff: the first draft compared a bid/ask fill
with the cost-free `label_mid_r` and would have fired on the spread alone.

- **Population:** S5's pairs, `exploration_data.fill_replay_pairs`: gold candidates with a fill label and an r2 label
  (replay_version r2, not void, not excluded for the cost cells), closed before the reviewed day's end.
- **Gap per fill** = fill_r - replay_r (`label_fill_r` - r2 `sim_r`), the S5 quantity. Both sides are net of the
  spread and both are divided by the same stop distance (the candidate's `stop_dist`), so a fill that matches its
  replay apart from the spread both pay reads 0. For a broker in `DEMO_EXIT_AT_LEVEL` (vantage) the gap is the entry
  leg only (`entry_fill_minus_replay_r`), because its demo exits fill at the level.
- **Gap test (one-sided e-process, not a fixed-window CI).** For each scope there are two streams: fill better than
  replay beyond +0.02 R, and fill worse beyond -0.02 R. Each is `eval2.forward.e_execution` with one term per
  session day, as a running maximum. A scope FIRES when either stream reaches 20 x the streams formed in that run
  (2 per scope, Bonferroni), once the scope has at least 300 fills over at least 10 session days. The latest window
  of at least 300 fills over at least 10 days still gets its mean gap and day-clustered 95 % CI reported, but that
  figure is never judged.
  - Why not the CI rule: a simulation of daily re-looks (true gap 0, 13 scopes, 250 days, fill sd 0.3 R, 5-60 fills
    a day) fired on 97 % of runs under the fixed-window CI rule. Under the e-process it fired on 0 of 100 runs; an
    e-process is valid at every look.
  - The test `test_under_daily_relooks_a_true_gap_of_0_fires_in_under_5_percent_of_250_day_runs_over_13_scopes`
    keeps this property.
- **Detection (the price of anytime validity).** The bet starts after 5 day terms. At 300 fills over 10 days nothing
  can fire: a 1 R gap reaches e of about 5 against a threshold of 520 at 13 scopes. Below is the median first fire, in
  session days, over 5 seeds at 13 scopes ("-" means not within 120 days):

  | fills a day, fill sd | gap 0.04 R | 0.06 R | 0.1 R | 0.2 R | 0.5 R |
  |---|---|---|---|---|---|
  | 10, 0.1 R | - | 52 | 36 | 31 | 30 |
  | 10, 0.3 R | - | - | 61 | 36 | 30 |
  | 30, 0.1 R | 92 | 47 | 34 | 31 | 30 |
  | 30, 0.3 R | - | 79 | 40 | 33 | 30 |

  A gap of 0.1 R is caught in about 6-12 weeks, and a gross failure in about 6 weeks. Gaps at the 0.02 R tolerance
  are not detectable inside a quarter. The bound the twin reads carry is therefore "within about 0.06 R" in practice,
  not 0.02 R.
- **Agreement** compares the fill's exit leg with the label's barrier, counted only over exits a label can have:
  stop (the resting stop, and the engine's stop close, which closes at the stop the label reads), target, and time
  (the hold). A trailed stop, a weekend flat, a broker or manual close and an orphan are left out of both the
  numerator and the denominator, and their share is reported with each read. A scope FIRES when agreement is below
  90 % in two consecutive non-overlapping 300-fill windows that together span at least 10 session days.
- **Scopes:** every broker x TF band (`eval2.forward.TF_BAND`), plus one pooled read. The pooled read covers every
  broker and band except the informational 1m-5m band, and uses the entry leg only for exit-at-level brokers. **A
  pooled fire alerts only and revokes nothing; revocation is per scope.**
- **On fire:**
  - an incident in the daily review (`parity`, posted to the `learning-ops` issue);
  - for a broker x band scope, the feedback-loop health check fails for 3 days and an automatic `review_actions` row
    is written (kind `replay_parity`, one per scope);
  - that row unvalidates the scope's replay-twin reads for good, through the gate EVAL-2 already has. L4 forward
    reads stop counting: `eval2.runner.run_forward_read` requires `revoked`, read by
    `eval2.runner.forward_revocations`, which refuses the read (`ReadRefused`) while the revocations cannot be read.
    The S5 gap is charged in the deciding cost via `catalogue.costs.deciding_cost(s5_revoked=forward.revoked_for(...))`;
  - a fire that cannot be recorded is an `unreadable` incident, and the next review records it.
- A fire is never lifted by a later passing read, and **nothing re-enables a broker-traded control automatically**.
  Re-validation needs a dated amendment.
- The thresholds are pre-registered here. They are module constants, as S5's are, never settings.
- S0R's admissions (propensity 1 under point 1.2) are not counted against `controls_per_edge` in any review. They
  are a cost read's fills, bounded by the day, hour and session budgets and by the 100-stop-exit stop. While S0R's
  stop-exit count cannot be read, S0R enters nothing (`stop_exit_read_unread`).

## Bias statement

Written after the 2026-11-06..14 control fills and their costs were looked at. Acceptable because it changes volume
(S0 sends nothing; S0R keeps the fills its registered read needs) and adds a monitor that can only take evidence away.
It changes no threshold, reading rule, window or matched-control method.

## Rollback

Remove `control_mode: replay_only` from S0's row (S0 then thins as under the 11-06 rule) and restart the engines.
The S0R exemption and the monitor are code; revert the release.
