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
what real fills do. It reuses the EVAL-2 S5 form (day-clustered per broker and TF band, eval2-method section 6).

- **Population:** closed gold `exploration_trades` with a fill, not in `evidence_exclusions`, joined on candidate_id
  to their r2 label (`exploration_replay`, replay_version r2, not void), closed before the reviewed day's end.
- **Gap per fill** = side x (exit - fill) / stop_usd - label_mid_r (R; + = the fill did better than the replay's
  gross path).
- **Agreement** = the fill's exit reason against the label's barrier. Stop (resting stop, engine stop close, a
  runner's trailed stop) is stop, target is target; time, weekend and broker exits are time.
- **Window:** the latest fills reaching at least 300 fills over at least 10 session days, per broker x TF band
  (`eval2.forward.TF_BAND`) and pooled (every broker and band together).
- **FIRE** if the day-clustered 95 % CI of the mean gap lies entirely outside [-0.02, +0.02] R, **or** barrier
  agreement is below 90 % in two consecutive non-overlapping 300-fill windows.
- **On fire:** an incident in the daily review (`parity`, posted to the `learning-ops` issue) and the feedback-loop
  health check fails for 3 days; an automatic `review_actions` row (kind `replay_parity`, one per scope); and the
  scope's replay-twin reads are unvalidated for good, by the gate EVAL-2 already has: the S5 gap is charged in the
  deciding cost (`catalogue.costs.deciding_cost` `s5_revoked`) and L4 forward reads stop counting
  (`eval2.forward.apply_s5` `revoked`). A pooled fire unvalidates every scope.
- A fire is never lifted by a later passing read, and **nothing re-enables a broker-traded control automatically**.
  Re-validation needs a dated amendment.
- The thresholds are pre-registered here and are module constants (as S5's are), never settings.
- Vantage stores the exit at the level on demo (DEMO_EXIT_AT_LEVEL), so its gap read is optimistic. Each vantage read
  says so.

## Bias statement

Written after the 2026-11-06..14 control fills and their costs were looked at. Acceptable because it changes volume
(S0 sends nothing; S0R keeps the fills its registered read needs) and adds a monitor that can only take evidence away.
It changes no threshold, reading rule, window or matched-control method.

## Rollback

Remove `control_mode: replay_only` from S0's row (S0 then thins as under the 11-06 rule) and restart the engines.
The S0R exemption and the monitor are code; revert the release.
