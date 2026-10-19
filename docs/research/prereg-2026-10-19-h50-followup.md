# H-48 / H-50 verdicts applied, the H-50 event count reconciled, and H-53 pre-registered (2026-10-19)

Research director, 2026-10-19 evening, after the evaluator's verdicts on registry trials #88 (H-48) and #89 (H-50),
runs 39279358684 and 39284389371 on `main`. Written before any H-53 data was read: the only computations behind this
note are **counts and holding times** of the frozen `box_break_close` rule on 2010-01..2025-09 (no return, hit or
barrier outcome printed, holdout not loaded). The scratch scripts are not committed.

## 1. Verdicts, applied by their pre-registered rules

| Trial | Rule | Result (rule-only, 2010-01..2025-09) | Verdict |
|---|---|---|---|
| #88 H-48 `sweep_absorb` 15m `{"rung": "prereg"}` | `prereg-kb-2026-10-18.md` s.1 | gross -0.028 R, t -1.17, n 3,210; net -0.552 R (t -24.8), n 3,247; long -0.566 R, short -0.538 R; 0 net-positive years | **STOP** (gross t < 2) |
| #89 H-50 `box_break_close` 15m `{}` | `prereg-2026-10-19-live-review.md` H-50 | gross +0.030 R, t 1.61, n 5,684; net -0.297 R (t -16.7), n 5,835; long -0.290 R, short -0.305 R; 0 net-positive years (2024 -0.130 R, 2025 -0.069 R); lot fit 97.4%, -£1.25 per fit trade at £1,000 | **STOP** (criterion 1 fails; criteria 2 and 3 fail too) |

What each rule says, and what is done (this commit unless stated):

- **H-48 STOP:** `sweep_absorb` is retired (`research.retired_families`, R-14), the shallow-sweep fade (H-08 15m form)
  is closed with it, no neighbour tests, and the cascade arm is never run.
- **H-50 STOP:** `box_break_close` is retired (`research.retired_families`, R-15). The four box agents stay shadow
  parity monitors with **no promotion route**. That route is already shut in code: promotion needs
  `trials.passed_gates` for the exact configuration (`ops/jobs.py`), and #89 is a failed screen. No status change in
  `population.json` is needed or made. **H-51a/b lapse with 0 trials**; the MFI stays unproven inside the box rule
  and H-40's retirement of the MFI filters stands.
- **Owner directive, reconciled.** The owner's box strategy keeps running on demo (memories owner-strategy-live,
  demo-for-learning). Both roster families keep their shadow agents in the engine. Retirement in
  `research.retired_families` governs research allocation only: no engine, roster or RiskGate code reads it.
- **One deliberate deviation: probes keep mirroring the roster agents.** Both rules (H-50 and rollout plan s.3)
  say "probes stop mirroring" on STOP. I do not apply that clause. It is recorded as an explicit trade-off in ADR 0040.
  Reasons:
  1. The owner's later directive binds: demo exists to learn, so maximise demo trades within the loss caps.
  2. A probe is a cost instrument (ADR 0037). It never enters `closed_trades` or any verdict, so mirroring a retired
     rule cannot contaminate evidence.
  3. The box agents are the most frequent signal source, so they are the fastest route to H-52's 30 filled probes.
     That cost read feeds every open pre-registration.
  4. Every probe limit is unchanged: 0.01 lot, demo-only lock, RiskGate, £4 per probe, the 0.2 × daily-limit
     budget, and real money untouched.
  The cost is small, bounded demo losses on a negative-expectancy rule. The clause is pre-registered, so this
  deviation is disclosed here, in ADR 0040, and in the R-15 row.
- **Real-money route: none.** Neither family can reach a live or real-money account. A future route needs a new
  pre-registered trial that passes (H-53 below for 1h only), the gates, the holdout look and the owner's one-click
  confirmation.

## 2. The event-count mismatch: the trial ran the frozen rule

The question: the trial recorded 5,835 events (net) against the pre-registration's "2,404 one at a time".

Identity checks:
- The trial row is config `{}`, hash `bf21a9e8fbc5`, agent `box_break_close-g0-d5dc90af47`. That is the roster's
  15m founder (the same identity function, config `{}`).
- Code at run head `442add04` contains every `box_break_close` / `smc_replica` commit up to `7a5cba1f`. Since the
  roster freeze (`58c7acbd`), the only change to the rule module is the 30m amendment (`HOLD_BARS`/`timeframes`
  entries for 30m). It does not touch the 15m constants or `decide`.
- `origin/main` has no later change to the rule, the replica, the labels or the pipeline.

Reproduction (same loader and frame as `research_pass.py`, 2010-2025 `data-v1`, entries before 2025-10-01):

| Timeframe | Raw candidates | One at a time, slot held for the full time limit (`HOLD_BARS`+1) | One at a time, slot freed at the label exit (the pipeline, `one_at_a_time`) | Median bars held |
|---|---:|---:|---:|---:|
| 15m | **9,147** | **2,404** | **5,835** | 7 |
| 30m | 4,750 | 1,444 | 3,037 | 8 |
| 1h | 2,436 | 907 | 1,625 | 7 |
| 4h | 650 | 319 | 436 | 7 |

The bold figures reproduce the pre-registration's raw and one-at-a-time counts and the trial's net n exactly.

**Finding.** The pre-registration counted "one at a time" as if every trade held its full 96 bars. That was the
only way to count without computing labels. The pipeline frees the slot at the actual exit, which is a median of
7 bars, and so does the shadow book. So the trial ran **the exact frozen rule under the pipeline's standard
convention**. The mismatch is an error in the pre-registration's counting method, not in the trial.

**What it means for the verdict: nothing, STOP stands.**
- The reading rule's thresholds (≥ 1,000 events, gross t ≥ 2, net > 0, per-year signs) do not depend on the
  convention. The trial cleared the event floor under both.
- A larger n only adds power. On the 2,404-event convention, the same mean would give t ≈ 1.61 × √(2,404/5,684) ≈
  1.05, which is further from 2.
- Net is negative on both sides and in every year, so no convention rescues criteria 2-3.

**What it does change.** The counts for 1h (907) and 4h (319) were made by the same method. On that basis R-11 and
the live review s.4 ruled the 1h/4h box rules "below the P4 floor, cannot be research-passed alone". Under the
pipeline's convention:
- **1h has about 1,625 events, above the 1,000 floor.** R-11's reason is wrong for 1h.
- 4h, at 436, stays below the floor.

Process fix, from now on: a counts-only pre-registration states both bounds, the time-limit block (lower) and raw
(upper). The strategy-researcher's template gets this line.

## 3. H-53 `box_break_close` 1h, the frozen roster rule, as a research trial (1 trial)

- **Decision: pre-register now; do not dispatch** until a slot exists (funding below).
- **Why.** At 1h the stop is wider in dollars (median 1.5 ATR14: $4-8 in 2010-2020, $8.6 in 2024, $15.4 in 2025,
  against $1.9-3.9 and $7.3 at 15m), so the same dollar cost is a smaller share of R.
  - It is the owner's own rule on a timeframe the owner trades, and his stated belief is that it works across
    timeframes.
  - The 1h roster agent is already running. Only a passed trial of its exact configuration gives it a route, and
    1h is the only slower timeframe that clears the event floor alone (section 2).
- **Not proposed, and why:**
  - 4h, below the floor at 436: an inconclusive at best, so it would be a wasted trial.
  - 30m: H-51b lapsed by its own rule, and 30m keeps most of the 15m cost burden (stop about 1.4x that of 15m).
- **Config.** Family `box_break_close`, config `{"timeframe": "1h"}`, which is exactly the roster's 1h founder. Module
  constants frozen as in `shadow-roster-2026-10-19.md`: BREAK_DEPTH 0.2; MFI(12, tick counts) at or beyond 30/70 on
  t-4..t and turning; swing trend not against; not in the golden zone; stop 1.5 ATR(14) with the structural stop
  inside it; target 3.0 ATR (2R); `HOLD_BARS["1h"]` 48.
- **Economic reason and evidence against it.** These are the same as H-50 (Osler 2003/2005 stop-order clustering,
  gross mechanism, FX; no gold evidence). Against it:
  - H-50's 15m gross was only +0.030 R (t 1.61);
  - R-04 1h breakouts were +0.015 R gross;
  - Bajgrowicz & Scaillet 2012: no net edge in technical rules after snooping control.
- **Data.** Dukascopy 1m `data-v1`, 2010-01-01..2025-09-30, cut before resampling, `mid`, tick counts. Holdout not
  loaded for the screen. About 1,625 events one at a time (the pipeline convention), raw 2,436, lower bound 907.
- **Expected edge against cost.**
  - Cost: the prior (bar spread + $0.37/oz) is about $0.6-0.7/oz, plus swap (long -$0.60/oz a night, x3 Wednesday).
    That is about 0.10-0.15 R historically and about 0.05 R at 2024-25 ATR, so gross ≥ about +0.15 R is needed over
    the full sample for net > 0.
  - DSR bar at K ≥ 111 (t ≈ 4.1, SD ≈ 1.2 R, n ≈ 1,600): net about +0.12 R.
  - Prior gross: 0 to +0.05 R.
  - **Stated now: P(CONTINUE) about 3%, P(LEAD) about 5%, expected STOP.** The value is a definitive answer for the
    owner's rule at the timeframe where costs bind least, for 1 trial.
- **Reading rule (fixed before any outcome).**
  - **CONTINUE** only if all hold:
    1. P4: gross mean R t ≥ 2.0 on ≥ 1,000 events;
    2. rule-only net mean R > 0 under the cost prior (bar spread + $0.37/oz + swap prior);
    3. net mean R > 0 in 2024 and in 2025-01..09;
    4. DSR ≥ 0.95 at the registry `n_trials_effective` at the verdict (≥ 111).

    Then the holdout is scored once (reserved), and the 1h agent becomes promotable through the normal tournament:
    ≥ 40 clean clusters, the evaluator's verdict, and the owner's confirmation for real money.
  - **LEAD** if 1-3 hold and 4 fails. No promotion. The 1h agent's clean forward shadow becomes the test: a read at
    300 clean one-at-a-time trades, net mean R > 0 with t ≥ 2. That read is 1 trial, charged when made.
  - **STOP** if 1 fails or net ≤ 0, at any count. R-15 then covers every box timeframe, R-11's reason is restated
    with the corrected counts, and no further box timeframe trial is proposed without new evidence (a new mechanism
    or measured costs below half the prior).
  - **Inconclusive** only if the gross event count comes out below 1,000. That would be a defect of this count,
    escalated, never re-run under another config.
- **Gates.** P4 screen, net > 0, the per-year rule, DSR at full K, holdout only for a CONTINUE (one look).
- **Cost and funding.** 1 trial, `k_eff` 0 (one config, no grid). It is a pre-registered exception to R-15: the
  family is retired, and this row is the only box_break_close trial allowed.
  - 2026Q4 is full (55 of 55).
  - **Default funding: the first 2027Q1 open slot** under the E-5 cap (a pre-registered `rule_ref`). It takes one
    of the two Q1 trials H-51a/b no longer use, so the queued Q1 rows are not displaced.
  - Earlier only if the owner raises the 2026Q4 override from 55 to 56. That is an owner decision (settings); it is
    routed to the program-director with a recommendation, not assumed.
- **Dispatch command (only on 2027-01-01 or later, or after a recorded 55 → 56 raise):**

  ```
  gh workflow run research.yml --ref main -f specialist=box_break_close -f variants='[{"timeframe": "1h"}]' \
    -f from_year=2010 -f to_year=2025 -f lot_fit=true \
    -f rule_ref='prereg-2026-10-19-h50-followup.md#h-53' \
    -f rationale='H-53: box_break_close 1h, the exact frozen roster rule (prereg-2026-10-19-h50-followup.md s.3); 1 trial, pre-registered exception to R-15'
  ```

## 4. Ledger

- K now: 89 registry trials, `n_trials_effective` 110. H-53 makes it 111 or more at its verdict.
- 2026Q4: 55 of 55 used. 2027Q1: 0 used; 8 reserved for the queue; H-53 takes 1 open slot that H-51 frees.
- Survivors seeding the next generation: none. Both STOPs retire their ideas. Nothing is cloned or mutated
  (`frozen_config`).
