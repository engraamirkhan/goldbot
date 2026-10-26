# The continuous learning loop: from idea to a model that keeps learning

Research director, 2026-10-26. Part of the time-structure programme (`README.md` in this folder; synthesis in
`../time-structure-2026-10-26.md`).

Owner directives (2026-10-26, verbatim):
- "strategies developed should be backtested on historical data to analyse learning, improve it and once confidence
  is high then it will be able to predict so the model is in contious learning mode"
- "if required multimodels caould be used"

Most of this loop is already built. This note maps each stage to the code and jobs that exist, names what is missing as
backlog items with acceptance criteria, and says what can honestly run while 2026Q4 has no trial slots left. It
redesigns nothing that works. The rails (RiskGate as the only path, 1% cap, daily limits, owner confirmation on real
money, exits never gated) are not loop variables at any stage.

## 0. The loop

```
 (1) free iteration          (2) one confirmation       (3) demo exploration arm     (4) promotion gate          (5) live learning
 2010-2017 only,             2018-01..2025-09,          icm-probe / vantage-demo,    walk-forward DSR at K,      retrain, recalibrate,
 walk-forward, every    -->  pre-registered rule,  -->  forward record, own   -->    holdout once, shadow,  -->  drift watch, own trades,
 variant counted             read once, 1 trial         stops, never evidence        confidence record           cost reconciliation
        ^                                                                                                               |
        +-------------------- forward results, cost gaps and retirements feed the next generation (stage 1) ------------+
```

"Confidence is high" has one meaning here: **every component of the confidence record (section 2, stage 4) clears its
own bar.** Components are never averaged, so a strong backtest cannot buy back a failing forward record.

## 1. The two data halves (why stage 1 can iterate freely)

The research window 2010-01..2025-09 is split, in writing and before any outcome was read (README 4.1):
- **Discovery half, 2010-01-01..2017-12-31.** This is where we iterate: as many variants as we like, each one logged.
- **Confirmation half, 2018-01-01..2025-09-30.** One pre-registered read per configuration.
- **Holdout, 2025-10-01..2026-09-30.** Scored once per passing configuration (`gates.holdout_verdict`), unchanged.

What the confirmation half is, and is not. It is fresh for the time-structure rules: no slice reads an outcome there.
It is not pristine for the families that have already run: trials #1-#103 used 2010-2025-09 walk-forwards. So the
confirmation read is deflated by **K_conf**, the number of confirmation reads ever made, and gate 1 still uses the
registry K (145 today). Both numbers are reported.

## 2. Stage by stage: what exists, what is missing

### Stage 1. Free iteration on 2010-2017, with walk-forward and a count of every variant

| Need | Exists | Where |
|---|---|---|
| Purged, embargoed walk-forward per timeframe | yes | `research/walkforward.py` (15m 24/3/3 months, 1h 36/6/6, 4h 48/6/6, 1d expanding) |
| Restrict the window to 2010-2017 | partly | `scripts/research_pass.py --from-year/--to-year`, but **every run writes a registry trial row** (a quarter budget slot) |
| Primary-signal screen before any model | yes | `research/screen.py` (P4: gross t >= 2, >= 1,000 events; daily floor 150, retire-only) |
| Feature discovery with fold-internal selection, max 40 features | yes | `research/discovery.py` (`--discover`, `survivor_unit: group`), `model.check_feature_cap` |
| A bounded parameter grid | yes | `research/generator.py` GRID_V1 (loop trials, `loop_trials_per_run` 16, counted in K, not in the quarter) |
| Descriptive measurement scripts | yes, ad hoc | `docs/research/candidates/*.py` (this programme: `time_structure_2026-10-26.py`) |
| **A ledger of discovery-half looks that spends no quarter slot, refuses any bar from 2018 on, and counts every variant** | **no** | gap **L-1** |

The gap matters for honesty. Today an idea is either iterated as registry trials, which spend budget and raise K for
everyone (the loop's trials did exactly that), or in a scratch script whose looks nobody counts. Neither is what the
owner asked for. L-1 closes it: iterate freely, count everything, and touch nothing after 2017.

### Stage 2. One confirmation on 2018+

| Need | Exists | Where |
|---|---|---|
| Pre-registration written before the run | yes | `registry.preregister` (status `preregistered`, `rule_ref`), the dated files under `docs/research/prereg-*` |
| Budget check and the reserved queue | yes | `registry.check_budget_reserved`, E-5 cap (open budget only via a cited `rule_ref`) |
| Gates on the read: years positive incl. 2021-22, trade minimum, DSR at K | yes | `research/gates.py` `research_gates`; `screen.py` verdicts; evaluator `record_verdict` |
| **A confirmation read type: window 2018-01..2025-09 only, one read per config hash, K_conf reported** | **no** | gap **L-2** (modelled on `holdout_scored`) |

A confirmation read looks at outcomes, so it is a **registry trial**: 1 slot. That is unavoidable and correct.

### Stage 3. Demo exploration arm with a forward record

| Need | Exists | Where |
|---|---|---|
| Frozen arm specs, config hashes, random controls | yes | `docs/research/arms-2026-10-25.md`, `research/arms.py`, `research/arm_rules.py` |
| Candidates logged taken or not, with propensity and a feature snapshot | yes | `exploration_data.record_candidate` / `record_fill` (aux.db, append-only) |
| Labels replayed at each broker's own cost, never pooled across brokers | yes | `exploration_data.ReplayCosts.broker`, `replay_label` |
| Thompson allocation (5% floor, 30% cap), per-arm stops | yes | `exploration_data.thompson_shares`; `daily_review` retires an arm at its H-61 stop |
| Evidence exclusions (feed gaps, restarts) | yes | `research/evidence_exclusions.py` (criterion v1, append-only) |
| Pots and caps per style and host | yes | ADR 0043 (scalp £50, intraday £48, swing £40; per-trade £6.50/£45/£40) |
| One graduation read at the 300th admissible trade, Bonferroni over live arms | yes (rule) | `prereg-2026-10-25-high-volume.md` H-61 |
| **Arm session windows on local clocks** (the measured activity peak follows New York's clock in the UK/US gap weeks, README 6.9) | **partly**: `opening_range_break` uses local clocks, `in_window` is UTC | gap **L-3** |
| **A written path to add an arm mid-quarter with a flat prior** (an H-60 history run is a counted look; with 2026Q4 full, a new arm cannot get a history prior this quarter) | **no** (the Vantage twins set the precedent: no history run, flat prior) | gap **L-4** |

**Stage-3 requirement: measure the cost at the signal's own times (quant cost audit, 2026-10-26).**

What the audit found:
- The research prior is about 1.07 $/oz (Dukascopy spread 0.70 + 0.37). The 142 probe fills measured 0.144 central
  and 0.23 conservative, so the prior is 5-7 times higher.
- The probes ran at quiet, scheduled times, and 14 stop exits overshot by a mean of $0.70.
- The model is conservative and defensible, with no bug. Every retired family failed on gross t below 2, so none
  flips. R-06 is the one that died on cost, and it cannot clear the DSR bar. H-53 (the 1h box rule, 2027Q1) is the
  right re-read.

What the loop requires:
- Any candidate that passes only at measured-fill cost (today slices F-1/F-2 and D-1/D-2) goes to stage 3 first, as
  an arm that fills **at its own signal times**: opens, breaks, news-adjacent bars, stop exits included.
- At measured cost it is judged on the **worse** of two numbers:
  - (a) the quiet-time probe cost: conservative $0.23/oz, plus the measured $0.70 mean overshoot on every stop exit;
  - (b) the cost its own signal-time fills measured.
  Spread, slippage, stop overshoot and commission are broker-keyed, read with a 95% upper bound once there are at
  least 30 fills per session cell.
- The research prior (about $1.07/oz) is not replaced. It stays the cost of record for every registry trial and for
  gate 1, and each read reports net under both.
- *Corrected 2026-10-26 (synthesis): an earlier wording said "the worse of the research prior and its own fills". That
  would fail every 15m candidate by construction, which is not what the audit ruled.*
- No stage-2 slot is spent on such a candidate until (b) exists. This is component C6 of stage 4, measured before
  the slot rather than after.

Exploration fills are **never promotion evidence**. They teach costs, fill rates, parity and which arms deserve a
stage-2 slot.

### Stage 4. Promotion at the gate, with a measured confidence record

| Need | Exists | Where |
|---|---|---|
| Walk-forward gates with the DSR at the day's K | yes | `gates.research_gates`, `gates.dsr_check`, `registry.passed_gates` |
| CPCV / PBO as a veto | yes | `research/cpcv.py`, job `cpcv_quarterly` |
| Holdout scored once per configuration | yes | `gates.holdout_verdict`, `registry.holdout_scored` |
| Shadow at live prices, 4 weeks and 40 clustered trades; Sharpe, hit rate, drawdown, turnover | yes | `research/promotion.py` `evaluate_promotion`, `effective_n` |
| Population promotion (DSR with population size), retirement at the lower 80% bound | yes | `research/population.py` |
| Calibration (ECE, Brier) | yes, live side | `research/drift.py`; `attribution.calibration` |
| **One confidence record per candidate strategy** that lines the components up against their bars | **no** | gap **L-5** |

**The confidence record (L-5).** Each component is a ratio to its own bar. The record is reported; it never replaces
a gate. Its headline is the **minimum** ratio (the weakest link), never a weighted sum.

| Component | Measure | Bar (ratio = measure / bar) |
|---|---|---|
| C1 walk-forward | model-filtered day-clustered t | the DSR t at the day's K (about 4.3 at K = 145) |
| C2 confirmation | stage-2 t on 2018-01..2025-09 | the pre-registered read t, deflated by K_conf |
| C3 calibration | out-of-fold ECE, and forward ECE once there are 100 taken trades | 0.08 (`drift.ece_size_down`), inverted |
| C4 forward demo | arm posterior P(mean net R > 0) at its 300th admissible trade, broker-keyed | 1 − 0.05/m (H-61) |
| C5 parity | daily (bar, side) parity of the live code against the research labels | 95% (H-52) |
| C6 costs | forward net-minus-gross R against the research cost model's prediction | ratio within [0.67, 1.5] |

C6 exists because of slice E's finding. On sweep-reversal #88 (H-48), net minus gross was −0.52 R, about five times
what the current broker cost would predict. My arithmetic says the research prior explains most of it: a 0.19 ATR15
spread plus $0.37/oz at the 2010-2019 ATR15 of about $1.8, on a 0.6-1.0 ATR15 stop, gives about 0.4-0.65 R. So it is
probably the prior's fixed dollar charge at low historical ATR, not a labelling bug. Two caveats:
- #88's verdict stands either way: its gross R was already −0.028.
- The finding still matters for the loop. If research costs and forward costs disagree by a factor like that, every
  stage-2 read is mispriced in one direction. C6 makes that visible per strategy instead of per audit. Slice E's
  check is the evidence; the synthesis weighs it when it lands.

### Stage 5. Scheduled retrain, drift watch, learning from its own trades

| Need | Exists | Where |
|---|---|---|
| Weekly retrain into a challenger on the rolling window; promotion only through the gates | yes | job `saturday_retrain` (frozen rule agents labelled on 1m barriers are never retrained, ADR 0038) |
| Weekly bounded recalibration on every candidate (taken or not) | yes | job `recalibrate` (Platt layer shrunk to the validated map, capped) |
| New-champion CUSUM watch, restore the previous champion | yes | job `model_watch`, `research/cusum.py` (5% quarterly false-alarm h; `cannot_alarm` reported) |
| Feature PSI, calibration, residual CUSUM, drawdown halts (entries only) | yes | job `drift_watch`, `research/drift.py` |
| Weekly tournament: fitness, retirement, cloning, capital shares | yes | job `tournament`, `research/population.py` |
| Nightly learner: replayed labels, Thompson shares, weekly H-62 style models | yes | `nightly_costs` -> `exploration_learn` (`exploration_data.learn`) |
| Daily learning-ops review: costs, fill rate, refusals, arm stops, cost-cell refits | yes | job `daily_review` (`research/daily_review.py`) |
| Evidence exclusions before the learner reads | yes | `evidence_exclusions.decide` in `nightly_costs` |
| Attribution: gross, spread, slippage, swap, net per trade | yes | job `attribution` |
| **Retrain blocked while a challenger sits in shadow** | known defect | feedback item 61 (implementer with ml-researcher), already owned; not re-raised here |
| **Forward results flowing back to the hypothesis that produced them** (forward net R vs the walk-forward expectation and the stage-2 read, per strategy, monthly) | **no** | gap **L-6** |

Stage 5 is where the system learns from its own trades. What is missing is closing the loop to stage 1. A strategy
whose forward record drifts from its research expectation should send a dated note back to the hypothesis portfolio:
retire it, re-cost it, or fund a neighbour. It should not just be resized quietly by the allocator.

## 3. Several models where one is not enough

The rules for any combined model:
- Each member model has at most 40 features and fold-internal selection.
- Every member's trials count in K.
- The members' configurations are frozen before the combined model's trial.
- **The combined model is judged as one strategy at the gate.** Picking the best member afterwards is a new trial for
  that member, charged in full.
- A gate (regime, session) is fitted inside the same folds as the experts. A stacker is fitted only on out-of-fold
  predictions of the members (nested cross-fitting), never on in-fold ones.

| Form | Exists | Missing |
|---|---|---|
| One specialist per family, each with its own meta-label model | yes: `goldbot/specialists/*`, `research/model.py` `MetaLabelModel` | — |
| Pooled meta-model across families per timeframe | yes: `research_pass.py --pooled` (family `pooled_<tf>`, one trial; R-07 shows AUC about 0.50 over primaries with no edge) | — |
| Per-session / per-regime experts | partly: `specialists/loop_filters.py` gives `session_filter` and `regime_filter` as candidate filters (one dimension at a time); `daily_review` and H-62 already split by session and style | per-session **fitted** models inside one strategy: gap **M-1** |
| Regime gate / mixture of experts | partly: `allocator/rules.py` is a rule table (ADX, ATR quartile, vol tercile, tier-1 blackout) times population fitness; its docstring plans a learned allocator "once ~300 out-of-fold records exist" | a learned gate, cross-fitted with the experts: gap **M-2** |
| Range/volatility model feeding direction and selection | partly: `research/h16.py` and `features/day_type.py` (research), H-54 forward log (proposed), H-13 cost filter (in build) | the opportunity score served at decision time to specialists and the allocator as a **selector**: gap **M-3** |
| Ensembles / stacking | no stacking code | gap **M-4**, only after a member passes P4 on its own (R-07: stacking cannot create an edge) |

Which slice findings would use this (evidence from README section 6; slices pending):
1. **"Range is predictable, direction is not"** (6.6, 6.7: RV20 -> next RV IC 0.40-0.50; event days 1.45x). This is
   the clearest case. A range model sits under the specialists as a selector and stand-aside input (M-3). At
   £1,000 it cannot raise size: 0.01 lot is the floor and the 1% cap binds. So its value is in skipping
   low-range days, where fixed USD costs are the largest share of R, and in arming runner exits on high-range days
   (H-17 V1u). This is H-16/H-19 and needs no new trial.
2. **Session-conditioned experts** (6.2 anchor reactions differ by session; 6.10 RSI fades flip sign by session).
   Weak grounds as measured. The flips look like noise at about 150 looks, so per-session experts are justified only
   for an effect that survives stage 2 in each session separately.
3. **A regime gate from variance ratio / Hurst** (`regime_stats` features, built for H-02). 6.7 found no regime-free
   momentum or reversion. Whether a regime-conditional one exists is H-02's question, and M-2 waits for H-02.
4. **An event-day expert** (FOMC and NFP days at 1.45x range). It is a natural separate member with its own costs.
   It overlaps H-18, which is held at about 690 events below the floor.

## 4. Running the loop while 2026Q4 has no slots (69/69 used; K = 145)

Honest list of what runs **without a registry slot**, and what it can and cannot conclude:

| Activity | Slot? | K | Can conclude |
|---|---|---|---|
| Stage-1 descriptive measurement on 2010-2017 (this programme's script; slices A-F) | no | counted in the looks ledger (L-1) / README section 6 count until L-1 exists | close an idea at 0 trials; nominate one for stage 2 |
| Features into a not-yet-pinned discovery (O-4 15m, H-02 4h) by dated amendment | no now; the discovery runs in 2027Q1 | K_eff of the group at that run | group survival only |
| Forward-only logs (H-54 style, sealed inputs) | no | 0 | a forward check after n days, never an entry rule |
| New demo exploration arm by prereg amendment before its first fill, flat prior (L-4) | no | arm counted once in H-61's m and K when its rows are read | costs, fills, parity; graduation earns a pre-registration only, never a promotion |
| Retrain, recalibration, drift, tournament, daily review, attribution | no (not trials) | 0 | model maintenance on existing agents |
| **Stage-2 confirmation read** | **yes, 1 each** | +1 | **cannot run in 2026Q4**; queued for the 2027Q1 free reserve (6 slots) by dated research-director notes |
| History prior for a new arm (H-60 rerun) | yes (+1, Q4 full) | +1 | not in 2026Q4 |

Plainly: **nothing from this programme can be promoted, or even confirmed, before 2027-01-01.** What happens in Q4
is measurement on the discovery half, closing weak ideas at 0 trials, and forward learning on demo arms. That is the
honest form of "continuous learning" under a closed quarter.

## 5. Backlog items (fragments in `docs/backlog.d/`, ranked by the product-owner)

| Id | Item | Acceptance criteria | Owner / review |
|---|---|---|---|
| L-1 | Discovery-half looks ledger | (0) K_eff of a batch of looks is eigenvalue-based (the effective number of independent tests from the correlation matrix of the looked-at statistics, e.g. Nyholt 2004 / Li and Ji 2005), replacing hand grouping (quant condition 4, 2026-10-26); (1) append-only `looks.jsonl` (or aux.db table with refusing triggers): config hash, family, window, statistic, n, script/commit; (2) the loader cuts every bar at or after 2018-01-01 and an assert proves none survived (as `arms.cut_at_holdout`); (3) no quarter budget is spent, and `n_looks` per family is reported beside every later stage-2 verdict; (4) `research_pass.py --discovery-half` writes here, never to the registry; (5) test: a frame with a 2018 bar raises | implementer · quant-reviewer |
| L-2 | Confirmation read type | (1) window fixed to 2018-01-01..2025-09-30; (2) at most one read per config hash, refused on a second (as `holdout_scored`); (3) the read needs a pre-registered row whose `rule_ref` names its reading rule; (4) K_conf = number of confirmation reads ever made, reported with the registry K; (5) it is a registry trial (1 slot) | implementer · quant-reviewer · evaluator |
| L-3 | Local-clock session windows for exploration arms | (1) `arm_rules.in_window` accepts local-clock windows (`Europe/London`, `America/New_York`, `Asia/Tokyo`), as `opening_range_break` already does; (2) test across the 2026 UK/US gap weeks; (3) existing UTC windows unchanged (config hashes stable) | implementer · trading-safety-reviewer |
| L-4 | Mid-quarter arm addition with a flat prior | (1) a dated prereg amendment is required before the arm's first fill, and the loader refuses an arm without one; (2) prior = flat (no H-60 look); (3) the arm enters H-61's m and K at its read; (4) the £ caps of ADR 0043 hold | strategy-researcher (text) · implementer |
| L-5 | Confidence record per strategy | (1) components C1-C6 of section 2 computed from existing outputs, read-only; (2) the headline is the minimum ratio, with no weighted sum anywhere; (3) shown on the dashboard and in the weekly digest; (4) changes no gate, size or allocation (test) | implementer · quant-reviewer · ui-ux-designer |
| L-6 | Forward-to-hypothesis reconciliation | (1) monthly per strategy: forward net R and net-minus-gross against the walk-forward and stage-2 expectations, broker-keyed, evidence exclusions applied; (2) a breach (C6 outside [0.67, 1.5], or forward mean below the stage-2 lower 80% bound at n >= 100) writes a dated note to `hypotheses.md` through a fragment for the research director to answer; (3) acts on nothing automatically | performance-analyst · implementer |
| M-1..M-4 | Multi-model forms | **not backlog yet.** Each becomes an item only when a member passes P4 on its own (M-4), H-02 reads (M-2), or H-16 reads continue (M-3). Writing them now would build machinery for an edge we do not have | research director, re-dated at each verdict |

What I would stop doing:
- Iterating ideas as registry trials on the full window. It spends slots and raises K for every later trial.
- Quoting a backtest t without its K and its looks.
