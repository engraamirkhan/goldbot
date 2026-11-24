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

**Population (PD + research-director, 2026-11-24; quant to sign).** Measured on the brain on 2026-11-24:
- since 13 Nov, about 230 non-control gold candidates: RB15 146, RB1H 20, Z3D 16, RB4H 10, GS1 9, Z1P 6, Z9D 6,
  Z10D 5, and others;
- since 23 Nov, only 2 (GD1D / GD1P);
- after #424 there is no live strategy arm left.

Restricting to live, non-retired arms would therefore judge nothing, ever. So the **judged arms are a pre-named list
of 10 retired gold arms** (PR #435 makes them `replay_only`), constant `PRENAMED`:
RBR15, Z3D, Z1P, Z9D, Z10D, S1A, S1B, F1A, Z2P, Z5P.
- **Why these ten.** They are the 10 of PR #435's 11 with the most candidates. G1A is dropped: it is the smallest,
  with 11 fills and absent from the post-13-Nov counts.
- **Excluded:** RBR1H and RBR4H, which #432 STOPped.
- **RB15 is judged as RBR15.** RB15's row is hard-stopped by its `end_utc` (2026-11-22) and never records again. Its
  forward candidates come under the new id **RBR15** (PR #435: `retired_day` 2026-11-24, role strategy,
  `replay_only`, twin RBRC15).
  - Mapping (`HISTORY_OF` = {RB15: RBR15}): RB15's rows are RBR15's **history**. They are training rows only.
  - Every RBR15 OOS row is RBR15's own, decided after both RBR15's `retired_day` and RB15's last row.
  - RBRC15 is RBR15's named control once PR #435 names it. Until then the matched-stop rule applies.
- **Each pairing of "retired arm + learned filter" is a NEW pre-registered hypothesis.** It is not a re-read of the
  arm's retired verdict.
- **Rows of a pre-named arm, split in two:**
  - Rows decided **after its `retired_day`** (forward rows, never read) are the only out-of-sample and forward rows.
  - Rows decided before the retirement were already read, so they are **training rows only, never OOS**.
- **Judged on any broker.** At most 10 (broker, arm) pairs are judged a run, ranked by candidate count.
- **Live arms.** A live strategy arm, should one exist, is fitted and reported but not judged.

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
- **arms retired in the spec (`retired_day`), and `control_mode: replay_only` strategy or learning rows, unless the
  arm is pre-named.** Their candidates are neither training rows, nor judged arms, nor controls. A named control
  always stays. An ACTIVE model whose arm has no rows in a run is demoted ("no rows in run").

**Controls (quant Bias 3).** Control arms are the baseline only, never a training row. The baseline is the arm's
**named** control. Failing that, it is the **matched-stop** random controls:
- same timeframe;
- median stop within 10 % of the arm's (`STOP_MATCH`).

There is **no style fallback**, so a small-stop control can never flatter an arm. With no matched control the arm reads
"insufficient".

**Holdout.** A row inside the sealed holdout (2025-10-01..2026-09-30), or a snapshot available after its decision,
fails the run.

### Snapshot admission (R10: flagged for quant)

**What the brain shows.** Measured 2026-11-24: `exploration_feature_snapshots` holds 4,661 rows, every one
`pit = nominal`, and 0 `written`.
- `pit = written` needs all 90,000 trailing minutes to carry a write stamp.
- Stamping began on 2026-11-13.
- So under a written-only rule the trainer would read 0 rows until about February.

**The rule (quant S5).** Admit `pit = written` rows. Also admit a `pit = nominal` row only when **both** hold:
- it was decided on or after `NOMINAL_FROM` = 2026-11-15 00:00 UTC;
- the audit (`goldbot/research/snapshot_audit.py`) found every minute visible at its decision in [decision - 24 h,
  decision] carrying its write time.

**How the audit runs.**
- The weekly job runs it in memory before the trainer reads. If it fails, no nominal row is admitted that run.
- `scripts/research/audit_nominal_snapshots.py` runs it once by hand. It writes the excluded candidate ids and the
  counts.
- Backlog 267 moves the count into the snapshot writer itself.

**Correction to the sr-v1 / early sr-v2 text.** Earlier drafts said that from 2026-11-15 "every minute of the last day
before a decision carries its write time". That is false. Release minutes, from the reference feed the engine loads
after a restart, never get a write stamp, so a date alone proves nothing. The per-candidate audit replaces that claim.

**Justification for the minutes still unstamped.** These are minutes more than a day old. The rule rests on the repo's
standard nominal-availability convention for stored bars: a minute without a write stamp is taken at its nominal close,
as `feature_snapshots.py` and the history research do.
- The daily broker-M1 reconcile (23:20 UTC) had settled those minutes before the decision.
- A minute revised or written late inside the last day is resolved as of the decision, or not read at all
  (`minute_versions`).
- Restart windows are voided (`evidence_exclusions`) or held out (`held_out`).
- The DB CHECK keeps `available_utc_max_ns <= decision_ns`.

**Residual risk.** A minute more than a day old revised after the decision with no stamp. Its effect on 40 features
built mostly from recent bars is small, and it falls the same on both sides of the gate.

**Capped book days (quant S1).** A book whose replay records of refused arms reached the daily cap journals
`exploration_replay_cap` (PR #435). After that the rest of its day is not recorded. **Every row of that (account, UTC
day) is dropped**, counted as `capped_day`. If a journal cannot be read, the run fails (recorded; health WARNs).

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

**The judged set (K, section 5).** At most `MAX_JUDGED` = 10 (broker, arm) pairs a run:
- only pairs of the pre-named arms (section 2);
- only pairs meeting the minimum counts;
- ranked by candidate count. The ranking is label-blind, with ties broken by broker and then arm.

**A pass.** Out of sample, for a judged pair, all of the following hold:

| Condition | Threshold |
|---|---|
| OOS candidates / kept / OOS session days | >= 200 / >= 100 / >= 10 |
| Control candidates in the same OOS window | >= 50: the named control; otherwise matched-stop controls (same timeframe, median stop within 10 %); none: insufficient |
| Edge over **no-model**: kept mean minus all candidates' mean, r2 net R | >= +0.05 R |
| Edge over the **random control**: kept mean minus control mean, r2 net R | >= +0.05 R |
| Kept mean, r2 net R (quant S2: a filter that only loses less does not pass) | > 0 |
| Gross sanity: kept minus all on `label_mid_r` | >= 0 |
| Bootstrap p = (1 + #draws <= 0) / (1 + B), B = 10,000 day-block draws, seed 20261124 | see the next row |
| **max(p vs no-model, p vs control)**, intersection-union, **Holm-adjusted across every judged pair of the RUN** (all brokers) | <= 0.10 |

On the bootstrap:
- Arm days and control days are resampled independently.
- A draw with no kept row counts against the pass. The run records how many draws had none (`boot_no_kept`).

**ACTIVE.** All of the following must hold:
- a pass in **two consecutive runs** of the broker, where the earlier run's cutoff is **exactly 7 days** before this
  one's, so a missed week breaks the chain;
- the first passer's frozen model has a forward edge over no-model >= 0, once it has at least 30 forward rows. Forward
  rows are those decided after **its data cutoff**, not after its activation (code review L3): a catch-up pass
  activates late, so measured from activation its record would always be empty;
- no broker read of the run failed (quant (a)). A failed read leaves the Holm family incomplete, so nothing activates
  that run, and the failing broker's active models are demoted;
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
- **The realistic regime (quant S4): m = 1.**
  - Setup: 1 broker and 1 pre-named arm with `oos_from` set; 20 pre-retirement days, training only; 16 post-retirement
    days at 13 rows a day, then 21; a named control at 13 a day.
  - Every label is null, with mean **0**. That is the strictest null for the kept-mean condition: true costs make it
    about -0.1 R.
  - Gate: sr-v2 with S2.
  - **Measured on 2026-11-24 (seeds 9000-9999, 1,000 sims):**
    - single-run pass rate **2 / 1,000 = 0.2 %**;
    - two consecutive passes (16 days, then 21) **1 / 1,000 = 0.1 %**.
  - `tests/test_self_retrain.py::test_m1_null_single_run_pass_rate_is_at_most_ten_percent` (integration) asserts a
    single-run rate of at most 10 %.
- With 6 runs left in Q4, the expected number of false activations in the quarter is at most 6 x that rate. Most
  expanding-window passes repeat on overlapping data, so that bound is loose upward, not tight.

**What ACTIVE means here.** The judged arms are retired, so ACTIVE is a **re-activation candidate. Nothing trades.**
- The run records it (`reactivation_candidate`).
- The health line and the job output name it, and the job logs a warning. The #34 note is carried by the health and
  weekly report; this change adds no new poster.
- Re-activation as a demo learning arm goes through the existing learning-class route (ADR 0061 amendment), with
  trading-safety review. That is out of scope here.

The rest of this section describes the filter as it would apply to a re-activated arm.

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

**Expected rows for the judged arms.** These come from the brain counts in section 2. They are not measured per
broker.
- **Forward rows (OOS-eligible) on hand today:**
  - Z3D 16, Z1P 6, Z9D 6, Z10D 5;
  - RBR15 0 (new id from PR #435; RB15's 146 candidates since 13 Nov are its history, training only);
  - S1A, S1B, F1A, Z2P, Z5P about 0.
- **New forward rows need PR #435.** Since 23 Nov the engine records almost no candidates for retired arms (2 in
  all). Forward rows accrue only once PR #435 makes these arms `replay_only`, so their candidates are recorded and
  replay-labelled again.
- **Rates after #435.** At RB15's pre-retirement rates, RBR15 would add about 13 candidates a session day. The other nine
  would add 0.5-2 a day each.
- **The gate needs 200 OOS rows over at least 10 session days.**
  - RBR15 reaches that about 16 session days after #435 deploys, so about the 2026-12-20 run.
  - No other pre-named arm reaches 200 forward rows in Q4.

**What that means for Q4:**
- The first judged pair is about RBR15 on its broker at the 2026-12-20 run.
- The earliest re-activation candidate is the 2026-12-27 run, because two consecutive passes are needed.
- Every earlier run reads "insufficient". That is the correct output, not a fault.

**Prior.** That RBR15 plus its filter becomes a re-activation candidate within Q4: **about 5 %**. RB15 was retired on
its own evidence (#424), and the new information is the forward replay plus a cost-first filter.

## 5. K (multiple testing)

**K booked:**
- **One registry row per judged (broker, arm) pair in each run.** If arms were fitted but none was judged, one row is
  booked, because the run still looked. The results carry `n_judged`.
- The rows are written **before** any status change. If they cannot be written, the run fails closed: no model becomes
  or stays active.
- The rows are keyed by (spec, the run's cutoff, broker, arm). A rerun of the same slot books no second row (code
  review L4).

**Q4 cost:**
- **Worst case: 60.** That is at most 10 pre-named pairs a run, over 6 runs (the catch-up run for slot 2026-11-22,
  then the Sundays 11-29 to 12-27).
- **Realistic: about 6.**
  - The 4 runs up to 12-13 judge nothing and book 1 each.
  - The 12-20 and 12-27 runs each judge about 1 pair (RBR15).
- Both fit the roughly 82 left (ADR 0073). ADR 0088 is still Proposed. A run never books more than 10.

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
- Quant-reviewer: signed 2026-11-24 for sr-v2, conditional on S1-S5 (all landed in this PR: capped book days dropped, kept mean > 0, matched-stop control text, m = 1 null Monte Carlo, nominal-snapshot audit; with quant (a) and (b) and code review L1-L4).
