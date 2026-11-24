# Pre-registration: weekly self-retrain of a per-arm meta-label filter (H-100, spec `sr-v2`)

Principal ML researcher, 2026-11-24, before any data is read.
- **Owner directive (same day):** goldbot must retrain itself.
- **Code:** `goldbot/research/self_retrain.py` (the constants named here are the module's; a test pins them), job
  `self_retrain` in `goldbot/ops/jobs.py`, aux.db migration 43.
- **Version history:**
  - `sr-v1` was the first draft of this card, never run. Quant review of PR #434 returned "sign with changes" (R1-R11).
  - Code review of PR #434 added MEDIUM and LOW items.
  - Quant C1 on PR #435 added the retired-arm rule.
  - **`sr-v2`** applies all of them.
- **Status:** proposed. The quant-reviewer signs below before the first run counts.

## 1. What it replaces, and why this is not a re-test

On 2026-11-13 two retrains were retired (`docs/research/ml-review-2026-11-13.md`):
- **The specialists' Saturday LightGBM.** Shadow AUC 0.44 on 145 trades, and Brier 0.206 against a 0.197 base rate.
- **The H-62 meta-labeller.** It had 0 model rows, because 0 of 19,990 candidates carried a feature snapshot. Its
  `retrain` flag also never fired: it was tied to Sunday, inside `nightly_costs`, which runs Monday to Friday only.

**The new information is our own broker's stream:**
- every candidate, taken or not;
- its replay net-R label under the broker's own measured costs;
- its matched random controls;
- the engine's feature row at each decision (ADR 0080, since 2026-11-13).

R-07, R-20 and MLNET all trained on Dukascopy proxies with prior costs.

**Known failures, not re-tested:**
- R-07: meta-models over primaries with no gross edge.
- R-20 / A27: ML-M1, M1c and M2.
- MLNET V3.
- MLEXEC.
- PROBTREND.
- R-18: discovery v2.
- The H-62 LightGBM spec.

## 2. Spec (fixed: no grid, no tuning)

### Rows, per broker (never pooled across brokers)

**Population.** Every gold exploration candidate, taken or not, from strategy arms only.

**Label.** The **r2** replay label, from `exploration_replay`: measured legs per session and leg, never a credit, the
stop's overshoot included.
- The target is r2 `sim_r`.
- The gross diagnostic is r2 `mid_r`.
- r1 `label_sim_r` is read alongside and reported as `edge_r1`, never used to decide.
- Each label must be resolved by the cutoff, the last Friday 21:00 UTC (`model_cutoff`).

**Full horizon.** A row is read only when decision + 60 s + `max_hold_s` <= cutoff.
- 60 s bounds `arms.send_delay_s`, whose largest value is 7 s.
- Without this rule, a quick stop of a 1-day hold could enter while its still-open twins do not, which biases the
  labels toward early exits.
- A label exiting after the cutoff is **excluded as unresolved**.

**Excluded rows:**
- quarantined rows;
- rows void as posterior evidence;
- rows held out by an unresolved quarantine span;
- **arms retired in the spec (`retired_day`), and `control_mode: replay_only` strategy or learning rows.** Their
  candidates are neither training rows, nor judged arms, nor style-fallback controls. A named control always stays.
  An ACTIVE model whose arm has no rows in a run is demoted ("no rows in run").

**Controls.** Control arms are the baseline only, never a training row.

**Holdout.** A row inside the sealed holdout (2025-10-01..2026-09-30), or a snapshot available after its decision,
fails the run.

### Snapshot admission (R10: flagged for quant)

**What the brain shows.** Measured 2026-11-24: `exploration_feature_snapshots` holds 4,661 rows, every one
`pit = nominal`, and 0 `written`.
- `pit = written` needs all 90,000 trailing minutes to carry a write stamp.
- Stamping began on 2026-11-13.
- So under a written-only rule the trainer would read 0 rows until about February.

**The rule.** Admit `pit = written` rows. Also admit `pit = nominal` rows decided on or after `NOMINAL_FROM` =
2026-11-15 00:00 UTC.

**Justification: the repo's standard nominal-availability convention for stored bars.**
- A minute without a write stamp is taken at its nominal close, as `feature_snapshots.py` and the history research
  do.
- From 2026-11-15, every minute in the last day before a decision carries its write time. Stamping started on
  2026-11-13, and a day of margin was added.
- So a minute revised or written late is resolved as of the decision, through `minute_versions`, or not read at all.
- The unstamped minutes such a snapshot still reads are more than a day old. The daily broker-M1 reconcile had
  already settled them (23:20 UTC, last day) before the decision.
- Restart windows are excluded twice over:
  - spans with a recorded engine restart are voided as evidence (`evidence_exclusions`);
  - open spans are held out (`held_out`).
- The DB CHECK keeps `available_utc_max_ns <= decision_ns`.

**Residual risk.** A minute more than a day old revised after the decision with no stamp. Its effect on 40 features
built mostly from recent bars is small, and it is the same on both sides of the gate.

**Alternative, if quant rejects this rule.** Recompute features at train time from stored bars through `asof_join`
on `available_utc`. It is not implemented here.

### Model, filter and walk-forward

**Model.** One per (broker, strategy arm): median-imputed, standardised L2 logistic regression at C = 0.1.
- 40 inputs, side included (`check_feature_cap`): 39 frozen market columns plus `side`.
  - 8 cost and range-to-cost columns.
  - 8 volatility-regime columns.
  - 5 clock columns.
  - 8 trend columns.
  - 7 location columns.
  - 3 daily-context columns.
- The 11 directional columns are centred and multiplied by the side.
- Target: y = 1[r2 net R > 0].
- The model is stored as JSON numbers. There is no pickle.

**Filter.** Keep a candidate when p >= tau, where tau is the 1/3 quantile of the training fold's own scores.

**Walk-forward.** Expanding window over session days. The last half of the days is split into 4 contiguous blocks. A
training row must meet three conditions, each judged against the block's first decision:
- its full horizon ended before it;
- its label exited before it;
- it was decided at least 1 day before it.

A fold needs at least 100 training rows and both classes. A test checks truncation invariance: perturbing every
label decided or resolved at or after a block's start leaves that block's scores and keep set unchanged.

## 3. Gate (reading rule, written before any data is seen)

**The judged set (K, section 5).** At most `MAX_JUDGED` = 10 (broker, arm) pairs a run.
- These are the pairs meeting the minimum counts, ranked by candidate count. The ranking is label-blind, with ties
  broken by broker and then arm.
- **PD and quant pre-name them.** `PRENAMED` is empty, so the count rule applies. From here I cannot see the brain's
  per-arm counts, so the first catch-up run records its judged set in `self_retrain_runs.detail`. PD and quant may pin
  that list in `PRENAMED` by amendment. No owner escalation is needed.

**A pass.** Out of sample, for a judged pair, all of the following hold:

| Condition | Threshold |
|---|---|
| OOS candidates / kept / OOS session days | >= 200 / >= 100 / >= 10 |
| Control candidates in the same OOS window | >= 50 (the named control; otherwise admitted controls of the arm's style on its broker) |
| Edge over **no-model**: kept mean minus all candidates' mean, r2 net R | >= +0.05 R |
| Edge over the **random control**: kept mean minus control mean, r2 net R | >= +0.05 R |
| Gross sanity: kept minus all on `label_mid_r` | >= 0 |
| Bootstrap p = (1 + #draws <= 0) / (1 + B), B = 10,000 day-block draws, seed 20261124 | see the next row |
| **max(p vs no-model, p vs control)**, intersection-union, **Holm-adjusted across every judged pair of the RUN** (all brokers) | <= 0.10 |

On the bootstrap:
- Arm days and control days are resampled independently.
- A draw with no kept row counts against the pass. The run records how many draws had none (`boot_no_kept`).

**ACTIVE.** All of the following must hold:
- a pass in **two consecutive runs** of the broker;
- the first passer's frozen model has a forward edge over no-model >= 0, once it has at least 30 forward rows (rows
  decided after its activation);
- serving is on (`research.self_retrain.serve`).

Otherwise the model stays SHADOW.

**Timing.** A model activates at the next Sunday 22:00 UTC open after the run. A catch-up run therefore never
activates in the past.

**Demotion, read first.** Demotion is checked before anything else in a run.
- **Forward decay.** An ACTIVE model's forward record is read with its frozen model. If it has at least 60 rows and
  an edge that is missing or below 0.0 R, the model is demoted, **and that run's new model of the arm is forced to
  shadow.**
- **Otherwise,** an ACTIVE model is:
  - superseded by a new active model of its arm;
  - demoted when this run's walk-forward fails the gate;
  - demoted when it has no rows in the run.
- **Fail-closed cases** (no model becomes or stays active):
  - the job is switched off, which demotes every active model;
  - the K rows cannot be written (section 5).
- **Why demotion uses 0 R, not +0.05 R.** Demotion reads a shorter, noisier forward record of a frozen model, so its
  threshold is the no-edge line. It is not the OOS promotion edge.

**Expected false-activation rate.** Null Monte Carlo in `tests/test_self_retrain.py` (integration):
- Setup: 200 runs, 4 brokers x 10 arms, 20 days, a day-clustered noise sd of 1.2 R, every label null.
- The single-run pass rate is asserted to be <= 0.12.
- **Measured on 2026-11-24** (seeds 1000-1199, 200 runs): **0 runs with any pass, 0 passes** in 8,000 null
  (broker, arm) reads. The one-sided 95 % upper bound is about 1.5 % a run.
- ACTIVE needs two consecutive passes, plus a non-negative forward edge for the first passer. So per run, the null
  activation rate is at most the single-run pass rate.
- With 6 runs left in Q4, the expected number of false activations in the quarter is at most 6 x that rate. Most
  expanding-window passes repeat on overlapping data, so that bound is loose upward, not tight.

**What ACTIVE may do.** It may only drop candidates of a demo strategy arm before the RiskGate.
- **A dropped candidate is still recorded** in `exploration_obs` (`taken = 0`, `blocked_reason =
  self_retrain:<model_version>`, propensity 0) **and still replay-labelled. It is never removed.** So the learner
  keeps seeing both what the filter kept and what it dropped.
- It never adds an order, raises a size, or touches a control, a replay_only arm, a risk limit or a live account.

**Engine serving is not in this change.** It is a separate change, which the trading-safety-reviewer reviews. Until
it lands, ACTIVE is recorded and reported only, and a test checks that no engine, execution or risk module names
these tables.

**Costs in R.** Kept and all candidates of one arm pay the same per-trade cost **in USD/oz**, not the same cost in R,
because the cost in R scales with each candidate's own stop distance. So the no-model edge is **not** cost-neutral.
Both edges are therefore read on each candidate's own r2 net R, and the gross edge must be >= 0, so that a filter
that only selects wider stops (cheaper in R) with no gross edge cannot pass.

## 4. Expectation (stated honestly)

- **Prior.** That any arm reaches ACTIVE within Q4: **5-10 %**. This is lower than under sr-v1, because two
  consecutive passes and intersection-union are now required.
- **Earliest possible ACTIVE.** The gate needs at least 10 OOS session days. That means about 20 session days of
  admitted rows from 2026-11-15, so the first possible pass is the run of about 12-13 December. ACTIVE needs a second
  consecutive pass, so the run of about 19-20 December.
- **Runs before that** are expected to read "insufficient". That is the correct output, not a fault.

## 5. K (multiple testing)

**K booked:**
- **One registry row per judged (broker, arm) pair in each run.** If arms were fitted but none was judged, one row
  is booked, because the run still looked. The results carry `n_judged`.
- The rows are written **before** any status change. If they cannot be written, the run fails closed: no model
  becomes or stays active.

**Q4 cost:**
- At most 10 per run, across 6 runs (the catch-up run for slot 2026-11-22, then the Sundays 11-29 to 12-27): 60 at
  most.
- Runs before about 12-13 December judge no pair and book 1 each, so the realistic total is about 4 + 2 x (at most 10).
- That fits the roughly 82 left (ADR 0073). ADR 0088 is still Proposed.

**Not promotion evidence.** It is never ledger-stamped and never reaches real money (learning-class rules, ADR 0061
amendment).

**Changes.** Any change to a constant here is a new `SPEC_VERSION`, a new card and new K.

## 6. Run record and health

- **`self_retrain_runs`.** One row per (run, broker), with:
  - counts: labels, open horizon, with features, nominal, no snapshot, controls, features and weights;
  - per arm: the OOS read (n, kept, AUC, the three edges, both p values, the Holm p, `boot_no_kept`), whether it was
    judged, the forward and confirmation reads, the decision and the reason;
  - `k_ok`.
- **Health `self_retrain`** WARNs when the job is off, has not run for more than 8 days, failed, or had 0 labels with
  features. Active models are counted from the table.

## 7. Owner-only decisions

None are assumed. It is demo only, adds no spend, and changes no sizing or risk limit.

## Sign-off

- Principal ML researcher: 2026-11-24 (author, sr-v2).
- Quant-reviewer: _pending (sr-v1 review: sign with changes; this version answers R1-R11 and C1)_.
