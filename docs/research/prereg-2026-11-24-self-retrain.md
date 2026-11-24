# Pre-registration: weekly self-retrain of a per-arm meta-label filter (H-100, spec `sr-v1`)

Principal ML researcher, 2026-11-24, before any data is read. Owner directive the same day: goldbot must retrain
itself. Code: `goldbot/research/self_retrain.py` (the constants named here are the module's, and a test pins them),
job `self_retrain` in `goldbot/ops/jobs.py`, aux.db migration 43. **Status: proposed. The quant-reviewer signs below
before the first run counts.**

## 1. What it replaces, and why this is not a re-test

On 2026-11-13 two retrains were retired (`docs/research/ml-review-2026-11-13.md`):
- **The specialists' Saturday LightGBM.** Shadow AUC 0.44 on 145 trades, and Brier 0.206 against a 0.197 base rate.
- **The H-62 meta-labeller.** It had 0 model rows, because 0 of 19,990 candidates carried a feature snapshot. Its
  `retrain` flag also never fired: it was tied to Sunday, inside `nightly_costs`, which runs Monday to Friday only.

**What is new is information, not a new algorithm.** Since 2026-11-13 every gold candidate gets a point-in-time
snapshot of the engine's default feature row (ADR 0080, `exploration_feature_snapshots`). That snapshot is our own
broker's stream:
- every candidate, taken or not;
- its replay net-R label under the broker's own cost model;
- matched random controls.

R-07, R-20 and MLNET all trained on Dukascopy proxies with prior costs. None of them saw this stream.

**Known failures, not re-tested:**
- R-07: meta-models over primaries with no gross edge.
- R-20 / A27: ML-M1, M1c and M2.
- MLNET V3.
- MLEXEC.
- PROBTREND.
- R-18: discovery v2.
- The H-62 LightGBM spec.

`sr-v1` is a different model class, uses a cost-first feature list, makes a filter-only decision and is read on new
data.

## 2. Spec (fixed: no grid, no tuning)

- **Rows, per broker** (never pooled across brokers).
  - Every gold exploration candidate, taken or not, from strategy arms only. Control arms are the baseline and never
    a training row.
  - Label: the r1 replay label `label_sim_r` (net of costs), resolved by the cutoff. The cutoff is the last Friday
    21:00 UTC (`model_cutoff`). `label_mid_r` is the gross diagnostic.
  - Excluded: quarantined rows, rows void as posterior evidence, and rows held out by an unresolved quarantine span.
  - Snapshot: `status ok`, `pit = written` only, and `available_utc_max_ns <= decision_ns` (enforced by a DB CHECK and
    checked again).
  - Any row inside the sealed holdout (2025-10-01..2026-09-30), any label exiting after the cutoff, or any snapshot
    after its decision **fails the run**. None is dropped quietly.
- **Model.** One per (broker, strategy arm): median-imputed, standardised L2 logistic regression at C = 0.1.
  - Inputs (40, side included, `check_feature_cap`): 39 frozen market columns plus `side`.
    - 8 cost and range-to-cost columns (`em_*_cost`, `spread_atr`, ...).
    - 8 volatility-regime columns.
    - 5 clock columns.
    - 8 trend columns.
    - 7 location columns.
    - 3 daily-context columns.
  - The 11 directional columns are centred and multiplied by the side.
  - Target: y = 1[label_sim_r > 0].
  - The model is stored as numbers in JSON. There is no pickle.
- **Filter.** Keep a candidate when p >= tau. Tau is the 1/3 quantile of the model's own training scores, so the
  filter drops the bottom third. Tau is fixed before any test row is scored.
- **Walk-forward.** Expanding window over session days. The last half of the days is split into 4 contiguous test
  blocks. A training row must meet both conditions:
  - its label **exited** before the block's first decision (purge);
  - it was decided at least 1 day before that decision (embargo).

  A fold needs at least 100 training rows and both classes.

## 3. Gate (reading rule, written before any data is seen)

**ACTIVE.** An arm's model becomes ACTIVE only if every condition below holds out of sample:

| Condition | Threshold |
|---|---|
| OOS candidates / kept / OOS session days | >= 200 / >= 100 / >= 10 |
| Control candidates in the same OOS window | >= 50. The arm's named control (`learning.control`); otherwise every control of its style on its broker |
| Edge over **no-model**: mean net R of kept minus mean of all the arm's candidates | >= +0.05 R |
| Edge over the **random control**: mean net R of kept minus mean of the control | >= +0.05 R |
| One-sided day-block bootstrap p of the no-model edge (1,000 draws, seed 20261124), **Holm-adjusted across the arms judged in that run** | <= 0.10 |

Otherwise the model stays SHADOW. Serving switched off (`research.self_retrain.serve: false`) keeps every model in
shadow.

**Demotion.** An ACTIVE model is DEMOTED in either case:
- the next run's walk-forward fails the gate;
- its forward record is weak. That record is the candidates decided after its activation, scored by the frozen
  model. It fails when it shows an edge over no-model below 0.0 R on at least 60 candidates.

A newer passer supersedes the arm's previous active model. Switching the job off demotes every active model.

**What ACTIVE may do.** It may only drop candidates of a demo strategy arm before the RiskGate. It never adds an
order, raises a size, or touches a control, a `replay_only` arm, a risk limit or a live account.

**Engine serving is not in this change.** It is a separate change, which the trading-safety-reviewer reviews. Until
it lands, ACTIVE is recorded and reported only, and `tests/test_self_retrain.py` checks that no engine, execution or
risk module names these tables.

**Cost sizing in R.** Kept and all candidates carry the same per-trade cost, so the no-model edge is cost-neutral.
The control edge is read on both sides' own net labels. Replay r1 charges slippage max(0.15, measured) USD/oz a leg.
r2 (measured legs) is not used here; moving to r2 needs an amendment.

## 4. Expectation (P4-style screen, stated honestly)

- **Prior.** That any arm reaches ACTIVE within Q4: **10-15 %**. ML on gold entries here has held near AUC 0.50
  (section 1). The information that is new is the cost-relative regime read on our own fills.
- **Earliest possible ACTIVE.** The gate needs at least 10 OOS session days. That means about 20 session days of
  written-pit snapshots, which have existed since 2026-11-13. So the first possible pass is the **2026-12-13** run.
- **Runs before that** are expected to read "insufficient" for every arm. That is the correct output, not a fault.

## 5. K (multiple testing)

**K booked:**
- **One registry row per run** that fits at least one arm (`ctx.trials.record`, family `self_retrain_meta_filter`,
  `rule_ref` this file). One run is one look of one fixed spec.
- **Holm** handles the multiplicity across arms inside the run.
- **No grid, and no model selection.** The weekly refit of the same frozen spec is the look itself, not a search.

**Q4 cost:**
- At most 6 rows: the first-deploy catch-up run (slot 2026-11-22), then the Sundays 11-29, 12-06, 12-13, 12-20 and
  12-27.
- That fits the roughly 82 left of 3,500 (ADR 0073). ADR 0088 makes the cap reported, not refusing, and counts every
  look.

**Not promotion evidence.** An ACTIVE filter is not ledger-stamped (ADR 0088 section 4). It never qualifies an arm,
and it never reaches real money (learning-class rules, ADR 0061 amendment).

**Changes.** A change to any constant above is a new `SPEC_VERSION`, a new card and a new K row.

## 6. Run record and health (learning never silently off)

- **`self_retrain_runs`.** One row per (run, broker), with:
  - labels, labels with features, controls and features (40);
  - the weight count;
  - per arm: the OOS read (n, kept, AUC, both edges, Holm p), the forward read and the decision.
- **Health `self_retrain` WARNs** when:
  - the job is switched off;
  - there has been no run for more than 8 days;
  - the newest run failed;
  - the newest run had 0 labels with features.

  Before the first run, `job:self_retrain` (`run_if_never_run`) reports instead.
- **Reporting.** Counts are reported, never schedules.

## 7. Owner-only decisions

None are assumed. It is demo only, adds no spend, and changes no sizing or risk limit.

## Sign-off

- Principal ML researcher: 2026-11-24 (author).
- Quant-reviewer: _pending_.
