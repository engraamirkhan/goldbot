# Pre-registration amendment: H-02 and O-4 pulled forward into 2026Q4 (research director, 2026-10-18)

Written 2026-10-18, before any outcome of H-02 or H-14/O-4 exists. No H-02 discovery has run, and no H-14 candidate
has been counted, labelled or scored. The source rules are `preregistration-2027Q1.md` (H-02, O-4) and
`batch-2026-10-18.md`. The trigger is the owner directive of 2026-10-18 (ADR 0029: backtest as much as needed now).

This file changes **when** H-02 runs, **which data** it reads and **how its trial is funded**. It does not change its
configuration, its cluster map, its reading rule or how K is counted. It does **not** pull O-4 forward. O-4 cannot be
pinned today (section 3).

## 1. Ruling

| Row | Run in 2026Q4? | Why |
|---|---|---|
| **H-02** (4h coverage-masked feature discovery) | **Yes.** It runs once the conditions in section 2.4 hold, after B01-B12 of the batch. Target dispatch Fri 2026-10-23. | The config is complete in `preregistration-2027Q1.md`: the variant (amended 2026-10-11), the bars range (readiness table 1) and the full `DiscoveryConfig` dump (amended 2026-10-17). The pool-rule code is built (BACKLOG 88, `53f38b7`). What was still "pending" was the machine extract in the YAML, not a choice. |
| **O-4** (H-14 15m discovery) | **No.** It stays in Q1. It may run in Q4 only under section 3.3. | Its group map cannot be pinned, because two of its pinned groups do not exist in code. It also has no candidate generator, no `flat_at_local` (BACKLOG 31) and no `calendar-v1`. The details are in section 3. |

**Budget correction.** The brief assumed 20 spare trials in 2026Q4. There are **none**. The registry (`research-v1`)
holds 20 rows, all stamped 2026-10, so they count in 2026Q4. The override of 53 is 20 + 33 for the batch
(`batch-2026-10-18.md` says "20 + 33"). So 53 − 20 − 33 = 0.

H-02 is funded by **withdrawing batch B13** (pooled 1h) before it runs. B13 is the lowest north-star value in the
batch:
- it re-runs trial #20's configuration;
- a 1h survivor cannot place 0.01 lot at £1,000 (batch section 3).

The batch stays 32 trials and the quarter still totals 53, so the batch's reading K (53) is unchanged. No owner
budget change is needed for H-02. Section 4 lists what would need one.

## 2. H-02 in 2026Q4

### 2.1 What is unchanged
The following stay exactly as written in `preregistration-2027Q1.md`:
- **Config:** label, candidates, cluster map `H02_CLUSTERS`, the macro opt-in, feature version `f-51ac5ebabb`, and
  the pinned `DiscoveryConfig` dump (with `full_coverage_columns: [macro_spx_dd20]` and `survivor_unit: group`).
- **The per-fold SPX mask and the pool-rule exemption.**
- **The reading rule**, cited by `rule_ref`.
- **The H-10 reading restriction:** jumps alone are inconclusive for H-10.
- **The discovery holdout rule** for any survivor.
- **The conservative K_eff default:** about 10 groups are added to every later trial.

### 2.2 What this amendment pins (before any outcome)
1. **Variant and bars.** The variant is `tsmom` `{"timeframe": "4h", "max_bars": 12}`, expected agent
   `tsmom-g0-76d7b19171`. Bars run from 2010-01-01 to 2025-09-30, loaded as `from_year=2010`, `to_year=2025` and cut at
   `research.holdout_from` by `discovery_pass`. The run records its first and last bar. Count-owed run 38931951849
   gave 2010-01-01 00:02 .. 2025-09-30 23:59 on these files. The YAML row's `variant`, `bars_from` and `bars_to` are
   no longer pending. H-02 is removed from the Q1 YAML (section 5).
2. **Data: data-v1 as it is today, gaps accepted.** This replaces "data-v1 after the G1 repair" for H-02 only. The
   research-window files are pinned by their sha256 digests (`gh release view data-v1`, 2026-10-18):

   | year | sha256 (`xauusd_1m_dukascopy_<year>.parquet`) |
   |---|---|
   | 2010 | `26139f0b026b52cd2f953f574fe933395630fd63a868dd12d007b9364b5f54e6` |
   | 2011 | `a1497c69398a027d4909dff403246ab90016f3f52ee85e1ab5b435ba9c557abc` |
   | 2012 | `89f4b77dbfe3f433b2952b0a94c55b870e7fe94f50fd3ae4dec0d05d3552b372` |
   | 2013 | `fe31b84429021a0dcbbb5918e19588064fd53649d5a203f4ca6ed63d8fd2f73a` |
   | 2014 | `be2bf066f0c4b2308b7adc6599ea236b7200b694016c0127f92c35655224f842` |
   | 2015 | `046ddd848faeedc77ed9f8f36903702534ffe801fc8962d47fd63f2aec77db81` |
   | 2016 | `9c6073ccee3290eedd9e7de69e55a0f5bb7cd366dede1f59cff37cbcce445641` |
   | 2017 | `e04d7da0d7e92b3e2e9d2fca87f87955d050f0476175e847388584e6bda1b26f` |
   | 2018 | `a8a93abfe5beb035ce0da9ddbcd7c6e1d8f0c1e70c523697eb1c8726d06d5eed` |
   | 2019 | `f5e43125c9169cb9d84bd4d4ff110b1837a2d1870d6f7b6fd5869de269d4a2db` |
   | 2020 | `e8662ac20b9c156c9667c2b82d9d9c64d1831bceb10f3281c096464d2c55c53d` |
   | 2021 | `407f808973c35a36c729711d1352ba21db886d8aac9a7af6f25f7051f6aab525` |
   | 2022 | `a4ba7060b90e164ff63a6ded8a23fff3b82a94a718848146050124bf3ae106b3` |
   | 2023 | `e499a31d65c690094bfd79a418eaf51f58e8dc47844164cd3701ce2844f5de8f` |
   | 2024 | `8b7478d90da2179bd05ccd5cdb284b872d47099e2cb8188f18a638341e2f4461` |
   | 2025 | `ed30f401889c44724144d25beb4d831096ec3ae43c8c6eda6c16ff4839cd5906` |

   If a G1 repair publishes before the dispatch, the dispatch uses the repaired files. Their digests are pasted here
   in a dated line first. That choice is fixed now and cannot depend on any H-02 outcome, because none will exist.
   **After the dispatch there is no re-run on repaired data.** H-02 is one trial.
3. **Macro.** `macro-v1` refreshes weekly, and FRED's SP500 series is a rolling ten-year window, so its first row
   moves forward. The pin is therefore the `macro_fred.parquet` that exists at dispatch, with its digest recorded on
   the row.
   - Reference taken 2026-10-18: file sha256 `47473d2c3f9c4b35ae4b20368cbcb1f19678e65f40e81ed755363a6c45eaa2ed`.
   - That file has 34,381 rows with `available_utc` < 2025-10-01, matching run 38931951849.
   - The sha256 of those rows, sorted by series, value_date and vintage (`to_csv(index=False)`), is
     `ed98cbe4ef96e71e9c991af81981536850cc5e755e00b5a64445fe29e3c89596`.
   - The evaluator recomputes the window hash on the run's file and reports any difference (for example, SP500 now
     starting a few days later). The difference is reported, not read.
4. **Infrastructure failure.** A run that fails or times out before its result row is recorded has spent no trial. It
   is re-dispatched with the identical command and the same digests. A run that recorded a result is never re-run.

### 2.3 What the data gaps do to H-02, and the coverage mask
- **Research window.** There are 21 empty weekdays in 2010-01..2025-09: 2020-05-26..28, 2021-11-05, 2022-01-31,
  2023-11-08, 2024-01-10..12, 2024-06-07, 2024-07-01, 2024-07-16, 2025-02-26..28, 2025-03-26..28 and
  2025-05-12..14 (`data-coverage-2026-10-18.md`, data-engineer). The refetch recovered none of them, so they may be
  missing at source. They are about 0.5% of weekdays.
  - A gap day has no 4h bars, so it has no candidates.
  - A 12-bar label (about 2 trading days) that spans a gap is evaluated on the next available bars, like a weekend.
  - Rolling feature windows count bars, so they stretch over the gap.
  - None of these effects depends on the outcome, and the expected share of candidates touched is about 1-2%.
- **No bar-gap mask is added.** A mask is code, and it would change H-02's config hash. Gaps that are missing at
  source would also leave G1 open for good, so waiting for it is not a plan. The existing `full_coverage_columns`
  mask is unchanged. It covers SPX's coverage, not bar gaps.
- **Diagnostic (reported, never read).** The evaluator reports two things:
  - the number of candidates whose decision bar, or whose label window up to `max_bars`, touches a gap weekday;
  - if the recorded results hold per-candidate out-of-fold p, the OOF AUC without those candidates.
- **2026 gaps** (51 weekdays, plus the bid-imputed 02/03/05 months) are in the holdout. H-02 never loads it. They
  block only a survivor's promotion, which needs one holdout scoring after G2 closes.

### 2.4 Conditions before the dispatch (all must hold; dispatched by the research director)

| # | Condition | Accountable | Date |
|---|---|---|---|
| P1 | On `main`: BACKLOG 88 (`53f38b7`, quant-reviewer review), the 2026Q4 override (`566d90e`), `batch-2026-10-18.md` and this file | release-manager (quant-reviewer for 88) | 2026-10-21 |
| P2 | Quant-reviewer signs this amendment (2.2, 2.3, section 5) | quant-reviewer | 2026-10-21 |
| P3 | B01-B12 have completed. H-02 goes last, so no batch trial is read after H-02's K_eff exists, and the batch keeps K = 53 as written | research-director | after B12 |
| P4 | D4 (cluster coverage per 4h fold, `candidates/intraday_diag_2026-10-17.py`) is pasted here. Any cluster other than H-07 with 0 coverage in a 4h fold is a data defect, and H-02 waits for it | strategy-researcher | 2026-10-22 |
| P5 | `gh run list --workflow research.yml` shows nothing in progress or queued (batch blocker 0.2) | research-director | at dispatch |

If P1-P4 do not hold by **2026-11-27**, H-02 returns to Q1 unchanged and B13's trial stays unspent.

### 2.5 Dispatch command (one line)

```bash
gh workflow run research.yml --ref main -f specialist=tsmom -f from_year=2010 -f to_year=2025 -f variants='[{"timeframe":"4h","max_bars":12}]' -f macro=true -f discover=true -f families=cluster -f discover_config='{"clusters":{"H-03":["macro_gvz*","macro_vrp"],"H-05":["round_numbers","support_resistance","pdh_dist_atr","pdl_dist_atr","pwh_dist_atr","pwl_dist_atr"],"H-07":["macro_vix*","macro_spx*"],"H-08":["sweep_*","sess_high_dist_atr","sess_low_dist_atr","prev_sess_high_dist_atr","prev_sess_low_dist_atr","asia_high_dist_atr","asia_low_dist_atr","london_high_dist_atr","london_low_dist_atr"],"H-10":["jumps"]},"freq_threshold":0.6,"full_coverage_columns":["macro_spx_dd20"],"group_top_k":3,"inner_val_frac":0.2,"l1_c":0.05,"max_selected":39,"method":"lgbm","n_subsamples":50,"permutation":true,"permutation_repeats":3,"seed":0,"stability_threshold":0.6,"subsample_frac":0.5,"survivor_unit":"group","top_k":39}' -f rule_ref='preregistration-2027Q1.md#H-02' -f rationale='PULLFORWARD-2026Q4 H-02 feature discovery exactly as pre-registered (preregistration-2026Q4-pullforward.md), moved from Q1 2027 by owner directive 2026-10-18; funded by withdrawn batch B13'
```

The `discover_config` JSON was checked on 2026-10-18 against `DiscoveryConfig(**json).model_dump()`. It gives exactly
the 16 keys of the pinned dump. Before any look, the run checks the cluster columns (`check_cluster_columns`), the
group sizes and the SPX mask.

### 2.6 Reading rule (unchanged; cited) and what follows
- **The verdict** is read by `preregistration-2027Q1.md` H-02's reading rule, word for word. The evaluator records it
  with `TrialRegistry.record_verdict` before anything follows. The record states: K at the time; `k_eff`; feature
  version; data and macro digests; the gap diagnostic (2.3); D4.
- **Survivors.** These are the clusters the slot derivation rule would make live: verdict *continue* with OOF AUC
  >= 0.53 and lower bound > 0.50, and k clusters with group stability >= 0.6, ranked by stability.
  - Each earns one trial with exactly the configuration that rule fixes: H-02's family, spec and label, inputs
    `side` + cluster k's recorded members, tagged `from_discovery` and `survivor`, with the SPX mask where it
    applies.
  - The trials are funded as rows **S-1..S-4** of the Q1 open budget (section 5), not in 2026Q4, unless the owner
    raises the override (section 4).
  - A survivor is promoted only after its own trial passes the gates and the holdout rule (after G2).
- **No cluster survives** (or AUC < 0.53): S-1..S-4 are cancelled, and those trials return to Q1's free reserve.

## 3. O-4 (H-14 15m discovery): not pulled forward

### 3.1 Can the group map be pinned today? No.
These items are missing on `main` and on `claude/gifted-goldberg-lcb7pl` (checked 2026-10-18):

| Missing | What it blocks | Accountable | Date |
|---|---|---|---|
| The `liquidity` and `activity` feature families (in O-4's pinned group map since 2026-10-17) | the group map and its `group_map_sha256`, K_eff, the feature version | implementer (quant-reviewer) | 2026-12-11 |
| The H-14 candidate generator: every 15m close 08:00 London .. 16:00 New York, outside the tier-1 blackout, ATR(15m) >= round trip, both sides (E-2's candidate half; only the tercile cut is built) | the candidates themselves | implementer (quant-reviewer) | 2027-01-29 |
| BACKLOG 31 `flat_at_local` (flat 16:30 New York) | the label | implementer | 2027-01-29 |
| `calendar-v1` (no `FRED_API_KEY` secret; `gh secret list` is empty) | the tier-1 blackout, the count, P-3's test | owner (chaser: program-director) | key by 2026-11-30 |
| The candidate count, and the cost-in-R quartiles that decide O-4's withdrawal rule (count-owed-events) | the withdrawal check before the row is written | research-director dispatches, strategy-researcher pastes | after the above |
| BACKLOG 89 `tod_periodicity`: quant review (implemented 2026-10-18) | the group's inclusion | quant-reviewer | 2026-12-11 |

Pinning a group-map hash today would hash code that does not exist. Running O-4 without the candidate generator, the
label exit or the calendar would not be the registered configuration. **O-4 is not run early.**

### 3.2 What the data gaps do to O-4
- Labels are flat by 16:30 New York, so no label spans a gap day. A gap day simply has no candidates: about 21 × 52
  ≈ 1,100 of 100,000+, about 1%.
- The h1/h4/d1 context columns and `tod_periodicity` (complete slots only, `MAX_STALE` 5 days) skip the gap.
- No mask is needed. The same diagnostic as 2.3 is reported: candidates on the first trading day after a gap.

### 3.3 Amendments to O-4 (before any H-14 count; for quant-reviewer review)
1. **Drop, not delay** (the N1 rule, extended). If `liquidity` or `activity` is not merged by 2026-12-11, with
   `lookahead_check` reporting 0 columns and the group-size test passing, O-4 is pinned without it. K_eff falls by
   1 for each group dropped. O-4 never waits for a feature group.
2. **No queueing behind O-1..O-3.** O-4 depends on none of their verdicts. It runs as soon as its conditions hold,
   from 2027-01-04, instead of after O-2/O-3 ("Order of runs" step 4).
3. **Conditional Q4 run.** O-4 may run in 2026Q4, under the identical config and reading rule, only if both hold by
   2026-12-18:
   - every condition in its row of the Q1 table holds (group map and full dump pasted here);
   - the owner raises `trial_budget_overrides."2026Q4"` from 53 to 54.

   Its trial then leaves the Q1 open budget, and Q1's free reserve gains one. Its dispatch command is written here at
   that time. It cannot be written exactly today, because its `discover_config` does not exist yet.
4. **No cross-contamination from H-02.** O-4's group map, families and columns may change after H-02's result only
   by rule 1 above. Any other change written after H-02's result is recorded as such and makes O-4 exploratory.

## 4. Spare-trial allocation and owner options

**2026Q4: 53 = 20 run + 32 batch (B01-B12) + 1 H-02.** The 20 run are trials #1-#20; B13 is withdrawn. **Spare: 0.**
The batch keeps its order and reading rule. Neither H-02 nor O-4 displaces a 15m batch trial: those 13 are the only
trials that could place an order at £1,000.

These need the owner (via the program-director; budget is owner-only, ADR 0029):

| Option | Override | What it buys | Research recommendation |
|---|---|---|---|
| A | 53 (none) | H-02 in Q4; survivors and O-4 in Q1 | **Recommended.** It keeps the cost of K visible, and the survivors' configs are fixed by rule anyway. |
| B | 57 | Also runs H-02's survivors S-1..S-4 in Q4, the week after H-02's verdict | Acceptable if the owner wants speed. Each survivor still needs G2 and a holdout scoring before promotion, so B saves weeks, not a promotion. |
| C | +1 (54 or 58) | Lets O-4 run in Q4 if its conditions hold by 2026-12-18 (3.3 item 3) | Only if the engineering lands. Unlikely before Q1. |

## 5. Q1 2027 consequences (owed in one reviewed change: implementer, quant-reviewer; by 2026-10-30)
- **Q1 reservation.** H-01 and H-04 move to the batch, and H-02 with its four survivor slots moves here, so
  `research.reserved_trials_quarter."2027Q1"` falls **8 → 1**. Only C4H stays queued.
  - This change goes in the same reviewed change as `preregistration-2027Q1.yaml`: H-02, H-01 and H-04 marked moved,
    and `reservation: 1`.
  - `tests/test_preregister_quarter.py`, which pins the reservation, changes with it.
  - The pre-registration writer must not write an H-02 queued row in December.
- **Q1 open budget: 19.** It funds:
  - the nine conditional O-rows;
  - **S-1..S-4** (H-02 survivors, <= 4, conditional on H-02's verdict). These are ad-hoc pre-registrations with
    `rule_ref='preregistration-2027Q1.md#H-02'` under the E-5 cap.
  - the free reserve of 3;
  - **3 unallocated** trials (returned by moving H-01/H-04). Each of them is spent only through a dated note in
    `preregistration-2027Q1.md` written before its pre-registration.
- **Engineering owed before any S-k run** (implementer, quant-reviewer; by 2026-12-11). The ad-hoc survivor path
  must record `from_discovery` = H-02's `row_ref` and `survivor` = the cluster id, and must be checked by the same
  logic as `director.slot_refusal`, so that only the derivation rule's exact configuration runs. Without it, no S-k
  runs.

## 6. Does running early weaken the pre-registration? Yes, in four ways; stated plainly
1. **Code no longer protects the survivor slots.** In Q1 the four slots were held by the reservation and checked by
   `slot_refusal`. Here they depend on a written plan, plus the owed check in section 5. Until that check merges,
   survivor exactness depends on the evaluator's review.
2. **Gapped data, final.** H-02 reads data-v1 with 21 missing weekdays and is never re-run on repaired data. The
   effect is outcome-blind and small (2.3), but the registered sentence "after the G1 repair" no longer holds for
   H-02.
3. **The freeze path is skipped.** There is no queued row and no freeze commit, and the data-digest check
   (`queued_digest_refusal`) acts only on queued rows. So the digests are checked by the evaluator against this
   file, not refused by code.
4. **An early result can leak into Q1's unfrozen rows.** The guard: no Q1 row (C4H, O-1..O-10) may be amended citing
   H-02's result. An amendment written after H-02's result must say so, and it makes that row exploratory (3.3
   item 4).

**What is not weakened:**
- The configuration, cluster map, reading rule and slot derivation rule are unchanged, and they were all written
  before any H-02 outcome.
- K is counted honestly. H-02's K_eff of about 10 enters every later trial about 10 weeks earlier, which raises
  everyone's bar sooner. That is the price, and it is accepted.

No profit is forecast. The prior for a 4h discovery is low (0 of 20 trials have passed). Most likely H-02 reads stop or
inconclusive, and the record shows that.

Accountable: research-director (this file, P3/P5, dispatch). Quant-reviewer (P1 review of 88, P2). Strategy-researcher
(P4). Evaluator (verdict, 2.3 diagnostic). Implementer (section 5). Program-director (section 4 options to the owner
within 24 h; FRED key chase). Next check: Tue 2026-10-20.
