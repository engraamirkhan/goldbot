# Pre-registration: Q1 2027 research trials (DRAFT: freeze before 2027-01-01)

Written 2026-10-10, before any of these configurations has been run on data. Source of the ranking:
`docs/research/hypotheses.md` (section A), `state-of-the-art.md`, `indicator-survey.md`. Each trial below is written
into the registry as status `preregistered` (with this file's commit id) before it runs; its result is read only by the
rule stated here. Changing a rule after seeing data turns the trial into an exploratory one that cannot justify
promotion.

## Fixed for every trial
- Data: Dukascopy 1m XAUUSD 2010-01 .. 2025-09 (release `data-v1`); holdout 2025-10-01 .. 2026-09-30 untouched;
  macro release `macro-v1` (first releases, available next US business day 23:00 UTC) only where stated.
- Costs: the measured canonical cost table (release `costs-v1`) if it exists at run time, otherwise the settings priors;
  the report states which. Swap charged per rollover held (`triple_barrier(swap=...)`).
- Evaluation: purged walk-forward per `WINDOWS` (4h: 48/6/6 months, purge 10 d, embargo 4 d; 1d: 60/12/12 expanding),
  cross-fitted calibration and threshold, one position per agent, lookahead check must report 0 columns.
- Gates (unchanged, `research/gates.py`): >= 1,500 candidates, >= 60 per complete test fold, positive expectancy in
  >= 3 calendar years including one of 2021/2022, DSR >= 0.95 on >= 200 model-filtered trades with the real trial count.
- Rule-only gates are reported alongside (informational).
- Features: feature version `f-079deacc61` (sessions v2, DST-aware: `session`, `session_zones`, `market_structure`
  version 2), `f-51ac5ebabb` (was `f-ac2be79efe` with `macro_drivers` v4, whose stub rule read the data's bar spacing, `f-e7d2dc11e1` with `macro_drivers` v3, and `f-f999b359e2` before v3 added the VRP spread `macro_vrp`; v4 drops stub days from its HAR-RV; v5 reads each bar's declared length, amended 2026-10-11) where the macro drivers are added. A result on another feature version is recorded with a
  `deviation` against its pre-registration (`TrialRegistry.record`), and a holdout scoring needs a gate pass on the same
  feature version (`passed_gates(feature_version=)`). Any further feature change before a run amends this line first.
- DSR trial count: 20 (Q4 2026) + Q1 trials run so far + K_eff of H-02. K_eff is the number of units a survivor
  was picked from: the features screened (about 300) when survivors go forward as individual features (the default,
  `survivor_unit=feature`), the groups screened (about 10) only when whole groups go forward (`survivor_unit=group`).
  The unit is fixed in the pre-registration row before the run.
- **Conservative default, confirmed by the owner 2026-10-11 (see Owner rulings):** H-02's K_eff is added to the DSR trial count of EVERY later
  trial, survivor or not (`registry.n_trials_effective`, used by `research_pass.py` for every trial), not only to trials
  of an H-02 survivor. This over-deflates trials unrelated to H-02 (their DSR is biased down, never up); it is kept
  because a per-trial "is this a survivor?" link is easy to get wrong and an error there would bias DSR up.
- **Extra promotion rule for discovery survivors:** any H-02 survivor must, besides its own pre-registered trial and the
  design's gates, also pass the holdout rule (one scoring of 2025-10-01 .. 2026-09-30, `--score-holdout`; enforced in
  code: `TrialRegistry.passed_gates` requires it for configs tagged `from_discovery`) before it
  can be promoted.

## Order and budget (5 queued + 4 contingent H-02 survivor slots of 20; amended 2026-10-10)

**Reserved in code (amendment 2026-10-10, before the freeze; no reading rule changes; ADR 0007):**
`research.reserved_trials_quarter: {"2027Q1": 9}` (config/settings.yaml; keyed by quarter, so no other quarter holds
any) = **5 queued + 4 contingent H-02 survivor slots**, held
before the research director or the monthly label grid can spend any of the quarter's 20 (`research/director.py`
`reserved_trials`: reserved = max(9 - run - withdrawn - expired slots, pending, 0)). The 5 queued rows are H-02, H-01,
H-06, H-04 and tsmom 4h continuity; each trial run against its `preregistered` row uses one up, and a queued row is
always covered. Rows 6-9 are written before 2027-01-01 as four survivor slots on H-02's queued row
(`TrialRegistry.reserve_slot(h02_row, "survivor", k, derivation_rule, "2027Q1", survivor_pool=["H-03", "H-05",
"H-07", "H-08", "H-10"])`, k = 1..4, with the default slot rule `{"min_stability": 0.6, "min_auc": 0.53,
"min_auc_lower_bound": 0.5}`). **Derivation rule (amended 2026-10-11 after the quant re-review, before the freeze):**
"slot k is live only if H-02's recorded reading verdict is *continue* with OOF AUC >= 0.53 and the lower 95% bound
> 0.50, AND at least k of the hypothesis clusters H-03, H-05, H-07, H-08, H-10 have group stability >= 0.6
(`results.discovery.group_stability`; H-02 groups by these clusters, `discovery.H02_CLUSTERS`); clusters are ranked
by group stability (ties by that order), and survivor k's trial is H-02's family (`tsmom`), spec and label exactly,
with inputs = `side` + cluster k's recorded members (`results.discovery.group_members`), tagged `from_discovery` =
H-02's row reference (timestamp and config hash) and `survivor` = the cluster id". Otherwise the slot is freed by this
rule. Survivor k's trial is pre-registered in the quarter (`preregister(..., slot_ref=slot_k)`) and uses the slot only
if it is exactly that configuration (`director.slot_refusal`); a retired family is never covered. No holdout slots
are written for Q1: any holdout scoring of a survivor comes from the open budget under the E-5 cap. A row dropped
before it runs is withdrawn (`TrialRegistry.withdraw`), which frees its trial. **E-5 cap for 2027Q1**
(`research.open_budget_preregistered_only: ["2027Q1"]`): research-plan section 5 rules out further fast-tsmom
horizons (R-06), so the open budget is not the director's default tsmom pool: the research analyst spends it only on
an ad-hoc pre-registration naming a pre-registered rule (`rule_ref`) or on the retired families' one shared
exploration trial, and the label grid spends none. Follow-ups of H-01/H-04/tsmom 4h go live only on the evaluator's
recorded verdict "continue" (`TrialRegistry.record_verdict`), if follow-up slots are written for them before the
freeze (each fixes its family and allowed configs). The director and the grid get
at most 20 - used - reserved (11 if nothing else runs first). *Superseded:* "13 planned + 7 reserve" held 8 trials
all quarter that no run could spend, because in-quarter survivor rows could not draw on the reservation.

**Who may spend the reservation (decided 2026-10-10, quant review).** Every path that records a trial checks the same
reservation (`TrialRegistry.check_budget_reserved`, `research_pass.py` and `--discover` included): only a run whose
family and config hash match a pending `preregistered` row of the quarter uses a reserved trial, and its result row is
linked to that row. Anything else (an ad-hoc variant, a primary-signal screen, a pooled run, a discovery that wrote
its own pre-registration just before running) must fit in 20 - used - reserved. **Holdout scorings** (`--score-holdout`)
are trials charged to the budget and follow the same rule: they come out of the unreserved remainder unless a
`preregistered` row for that exact configuration is pending (the row that pre-registered the walk-forward trial is
already used by it, so a holdout scoring needs its own row). CPCV re-evaluations are evidence, not trials (ADR 0003):
no budget slot, no reservation. H-02 uses its reserved slot only when its exact discovery config (including the bars
range) was pre-registered before the run.

**Decision rules keyed on the row (amended 2026-10-11, quant re-review).** The queued rows of H-01, H-04 and the
tsmom 4h continuity trial are written with `rule_id="H-01"`, `"H-04"` and `"C4H"`
(`TrialRegistry.preregister(..., rule_id=)`). The evaluator's verdict on a screened trial linked to such a row is
checked against that coded rule (`director.preregistered_decisions`, from the recorded results): H-01 continues only
with >= 3 positive net years incl. 2021 or 2022 on both sides together AND on long-only (`rule_only_split`
`n_long_positive_years`, `long_chop_year_positive`) plus the net t and long-only tests; the 4h continuity trial is
either retired (net mean R <= 0) or "inconclusive" (not retired) and never continues. Any other screened trial with a
passing screen may continue only when the verdict cites its linked pre-registration's reading rule exactly.

**Writing the queued rows (2026-10-10, batch review).** Each row above is written with
`TrialRegistry.preregister(..., queue=True, target_quarter="2027Q1")` before 2027-01-01 (a queued row written once its
quarter is under way is refused, and a hand-written one is treated as ad hoc). It then holds the 2027Q1 reservation
and is used up by its trial whenever that runs. Without `target_quarter` the default is the quarter of the writing
date, or the next quarter within 14 days of its start (from 2026-12-18 for 2027Q1). The row is stamped with the
registry's clock (it cannot be backdated). **H-01 and H-02 must not run before 2027-01-01:** every runner (research
analyst, `research_pass.py`, `--discover`, the label grid) refuses a config queued for a later quarter, and a trial
stamped before its row's quarter would never use the row up.
**If the rows are not written before 2027-01-01 (quant review M1, 2026-10-11):** `scripts/preregister_quarter.py
--allow-late "<reason>"` writes them only as ad-hoc pre-registrations naming their rule (`queue=False`, `rule_ref`),
which hold no reservation, and writes no slot (a slot exists only before its quarter). So rows 6-9 are lost: any H-02
survivor trial would then come from the open budget, under the E-5 cap, like any other ad-hoc run. The writer refuses
a late write unless `research.reserved_trials_quarter` for 2027Q1 equals the queued rows and slots already standing in
the registry (normally 0), and that settings change ships in the same change as the late write.

| # | ID | Trial | Depends on | Trials |
|---|---|---|---|---|
| 1 | H-13 | Volatility-forecast cost filter, applied as a reporting overlay to every trial below (net R with / without) | none | 0 |
| 2 | H-02 | Feature discovery (one trial) | tooling `research_pass.py --discover` | 1 |
| 3 | H-01 | Slow TSMOM | owner ruling A (150, retire-only; decided 2026-10-11) | 1 |
| 4 | H-06 | Macro-conditioned slow TSMOM | H-01 run; macro release | 1 |
| 5 | H-04 | Asia-session drift | none | 1 |
| 6-9 | H-03, H-05, H-07, H-08, H-10 | Each only if its features survive H-02 (stability >= 0.6); contingent survivor slots k = 1..4 (amended 2026-10-10) | H-02 | 1 each, at most 4 |
| 10 | tsmom 4h, max_bars 12, with swap | The trial refused by the budget guard on 2026-10-09, kept for continuity | none | 1 |
| - | reserve | follow-ups decided by these rules only (open budget, not reserved) | | 11 |

*Corrected 2026-10-17 (research director):* with H-06 withdrawn the reservation is 8, so the open budget is **12**,
and it is allocated by the section "Q1 open-budget round" below (9 conditional trial rows + 3 free reserve; amended the same day after the quant review). The table
above is the queued part only.

## Trials and reading rules

### H-02 Feature discovery
- Config: label = 4h triple barrier, target 3.0 ATR / stop 1.5 ATR / 12 bars. **Candidates (amended 2026-10-11,
  quant review F1 of the registry writer, before any outcome; the earlier "every 4h bar (no rule), side from the sign
  of the 20-day vol-scaled return" cannot be executed: `--discover` takes its candidates from the specialist):** the
  candidates of `tsmom` with the variant `{"timeframe": "4h", "max_bars": 12}` (agent `tsmom-g0-76d7b19171`, config
  `lb_fast_h 24, lb_mid_h 120, lb_slow_h 480, vol_window_h 96, min_score 0.5, schedule_h 4, target_atr 3.0,
  stop_atr 1.5, max_bars 12`). A 4h bar is a candidate when |tsmom_score| >= 0.5, where tsmom_score is the mean of
  the 1-, 5- and 20-day (6, 30 and 120 four-hour bars) vol-scaled returns, with volatility over 24 bars. The side is
  the sign of that score, not of the 20-day return. Candidates are thinned one position at a time
  (`labels.one_at_a_time`) on the labels above. This is the same candidate rule as Q4 trial #17 and the 4h
  continuity trial; H-02's row differs from both by its discovery config. Features = every registered family (incl.
  trader-toolkit, survey and macro), grouped by **hypothesis cluster** (`--families cluster`, `DiscoveryConfig.clusters
  = discovery.H02_CLUSTERS`, amended 2026-10-11 after the quant re-review to follow `hypotheses.md` exactly):
  - **H-03** gold VRP: `macro_gvz*` and the explicit spread `macro_vrp` = GVZ^2 - HAR-RV^2 (built point-in-time in the
    opt-in `macro_drivers`, version 5: equal-weight HAR of daily realised variance over completed, stub-free days
    before the bar (a day whose bars' declared lengths sum to < 12 h, e.g. the Sunday reopen, is dropped), annualised; feature version
    `f-51ac5ebabb`);
  - **H-05** stop-cluster levels: `round_numbers` (distance to the nearest $5 / $10 / $25 / $50 level and its
    touches; **no $100 level**, so the $100 round-number step listed under H-05 in `hypotheses.md` is not screened
    here), `support_resistance`, and the prior day/week extremes
    `pdh/pdl/pwh/pwl_dist_atr` (from `session_zones`);
  - **H-07** equity-stress haven: `macro_vix*`, `macro_spx*`;
  - **H-08** liquidity-sweep reversal: `sweep_*` and the session / Asia / London extreme distances
    (`sess_*`, `prev_sess_high/low`, `asia_*`, `london_*` `_dist_atr`);
  - **H-10** post-jump behaviour: `jumps` only. **H-10 tests jumps only, not the news / no-news split**: the US tier-1
    calendar is merged, but the split needs an opt-in `jump_news` feature (changing `jumps` would move the pinned
    default feature version) and the calendar wired into `--discover`; unless both land before the freeze, H-10 reads
    jumps alone. **Reading restriction (amended 2026-10-11, quant re-review):** a jumps-only result can neither
    retire nor continue H-10. H-10's hypothesis is the news / no-news contrast, so a null on jumps alone is recorded
    as *inconclusive for H-10* and only the news-split version (`jump_news` plus the E-1 calendar in `--discover`, in
    its own pre-registered trial) can retire or continue it. A jumps-only H-10 cluster still ranks for a survivor
    slot by the rule above, but its survivor trial reads jumps, not H-10;
  - first match wins; every other column stays in its feature family's group (`realised_vol` and `vol_estimators` in
    volatility, `swings` and `bos_*` in structure). Before any look the run checks that every cluster has at least
    one non-constant, non-NaN column with macro opted in (`discovery.check_cluster_columns`, recorded as
    `cluster_columns`) and that each cluster has <= 39 columns.
  Survivors go forward as features, so K_eff = features screened. *(Superseded 2026-10-17, see the amendment below
  the pinned dump: survivors go forward as whole clusters, and K_eff = groups screened.)* Group stability uses the size-neutral **joint
  permutation importance** (all a cluster's columns permuted together on the training fold's rows outside each
  subsample; ADR 0007 amendment 2026-10-11), top-k over groups with `group_top_k: 3` set explicitly in the config
  (quant ruling Q-3).
- Method (amended 2026-10-11, quant review F1, before any outcome): stability selection inside each training fold
  only (50 subsamples x 50%). Each subsample selects its **top 39** features by gain (`top_k: 39` = `max_selected:
  39`, because `side` is the 40th input; the earlier "top-40" is withdrawn). **Permutation confirmation**, not a
  tie-break: a feature with permutation importance <= 0 on the inner validation split (20%, 3 repeats) is dropped
  from the fold's selection (`permutation: true`). A feature or cluster is selected in a fold when its frequency is
  >= 0.6 (`freq_threshold: 0.6`). It is stable when that holds in >= 60% of folds (`stability_threshold: 0.6`, matching
  the reading and slot rules; the code's default of 0.7 does not apply).
- **Pinned discovery config.** The full `DiscoveryConfig.model_dump()`, which the registry writer's spec and the
  queued row carry verbatim (any other value is a different configuration):

  ```yaml
  clusters: discovery.H02_CLUSTERS   # the full map, written out in the spec: H-03, H-05, H-07, H-08, H-10 as listed above
  freq_threshold: 0.6
  full_coverage_columns: [macro_spx_dd20]   # pinned 2026-10-17 (BACKLOG 88): exempt from the 80% pool rule, per-fold mask
  group_top_k: 3
  inner_val_frac: 0.2
  l1_c: 0.05
  max_selected: 39
  method: lgbm
  n_subsamples: 50
  permutation: true
  permutation_repeats: 3
  seed: 0
  stability_threshold: 0.6
  subsample_frac: 0.5
  survivor_unit: group    # amended 2026-10-17 (Q-H02U); was feature
  top_k: 39
  ```

  **Amendment 2026-10-17 (quant review of 0f9d6d8, finding 3, Q-H02U; before any H-02 outcome; no reading rule
  changes).** `survivor_unit` changes from `feature` to `group`, because the slot rule forwards whole clusters. K_eff
  becomes the groups screened, recorded as `k_eff` on the discovery row, and the gate-1 bar for every later trial
  falls from about t 4.6 to about t 4.0. `group_top_k: 3` is already explicit, as the group unit requires, and every
  cluster is <= 39 columns. The pinned dump above now reads `survivor_unit: group`; every other value is unchanged.
  `preregistration-2027Q1.yaml` carries the same dump.
  - **Sentence for the owner (#34, posted by the quant reviewer):** "Before any Q1 result exists, H-02's discovery
    now counts its feature groups (about ten), not its ~300 features, as the tests it ran. This is the honest count,
    because whole clusters go forward. It lowers the bar every later strategy must clear at gate 1 from about t 4.6
    to about t 4.0. Every rail and every other gate is unchanged."

  (`l1_c` is unused with `method: lgbm` but is pinned, because it is part of the dump.)
- Reading rule (decided now): a feature cluster **survives** if its selection stability >= 0.6 across folds AND the
  walk-forward on the selected set has OOF AUC >= 0.53 with the lower 95% bound > 0.50 (cluster = a hypothesis
  cluster above; its selection stability = its group stability; the AUC condition is H-02's recorded reading
  verdict "continue"). Survivors earn their own
  trial (rows 6-9); if no cluster survives, rows 6-9 are cancelled and their budget returns to reserve.
- **Amendment 2026-10-17 (strategy researcher, quant review of 1f0447e finding 3; before any H-02 outcome; no reading
  rule changes; `candidates/intraday-2026-10-17.md` IC-3).**
  - **The `macro_spx_dd20` early-fold gap.** FRED serves about ten years of SP500 (`scripts/fred_macro.py`), so
    `macro_spx_dd20` is NaN before about 2016. A lower H-07 group stability in the early 4h folds is **expected; D4
    measures it**. D4 is the per-fold share of decision bars with an informative column per cluster, and the NaN
    share per `macro_*` column. It is reported on H-02's row before the run.
  - **Declared option (one only):** H-07 is a **true VIX-only cluster in any fold whose training window lacks full
    SP500 coverage.** In such a fold, `macro_spx_dd20` is set to NaN in **both** the training and the test rows.
    - *Corrected 2026-10-17, quant re-verify of 4c98477, before any H-02 outcome.* "VIX-only by data" was false.
      With the 4h rolling 48-month training window, test folds from about 2016-10 to about 2020-10 train on windows
      that only partly cover SP500. There `macro_spx_dd20` is NaN before about 2016 and present after, so the NaN
      marks the era and the model can learn it.
    - Full coverage means the column is finite on every training row of the fold. Masking is per fold and uses
      only the fold's own training rows' coverage, never the test rows.
    - The cluster map and the reading rule are unchanged.
    - A coverage rule that relabels a cluster "inconclusive by coverage" is not adopted.
    - **Owed before the freeze (2027-01-01): a small code item** (implementer, quant-reviewer review). The fold mask
      goes into the discovery and walk-forward path. The implementer adds the test
      `test_spx_dd20_is_nan_in_train_and_test_for_folds_without_full_coverage`, which also checks that folds with
      full coverage are untouched.
    - Without that merged code, H-02 does not run, because its configuration would not be the one registered here.
    - *Implemented 2026-10-17 (BACKLOG 82, implementer; not yet pinned).* The config field is
      `DiscoveryConfig.full_coverage_columns`; H-02 would set it to `["macro_spx_dd20"]`. It is in the dump and the
      config hash only when set, so pinning it changes H-02's hash; the research-director pins it.
  - **Macro release pinned.** `macro-v1` refreshes weekly, so H-02's row records the sha256 of the `macro_fred.parquet`
    it reads. That digest is written into the config at the 2026-12-11 config date (data-engineer), alongside the G4
    bar digests. A run on any other digest is a deviation.
  - **Pinned 2026-10-17 (research director, before any H-02 outcome and before D4 is run; BACKLOG 82 merged at
    0120db0).** Three pins. Each changes H-02's config hash, and nothing else: the cluster map, the reading rule and
    the slot rule are unchanged.
    1. **`full_coverage_columns: ("macro_spx_dd20",)` is pinned in H-02's `DiscoveryConfig`** (pinned now, by this
       text). The line goes into the pinned dump above and into `preregistration-2027Q1.yaml` in the same change as
       the code item below. It is not written there today, because the coded dump returns a tuple and the YAML holds a
       list, so `tests/test_preregister_quarter.py` (the verbatim-dump test) fails until it compares them
       normalised. H-02's row is not written until the dump, the YAML and the code agree.
    2. **Pool rule: the exemption is chosen; "H-07 is VIX-only" is not.** `macro_spx_dd20` is finite on only about
       57% of candidates (SP500 from about 2016-10), so the global > 80% pool rule (`pipeline.eligible_columns`,
       used by `discovery.discovery_pool`) would drop it from **every** fold. H-07 would then be VIX-only
       throughout, and the per-fold mask above would never act. That would test a narrower hypothesis than H-07
       (an equity-drawdown haven), and the mask was built to avoid exactly that. Rule: **columns named in
       `full_coverage_columns` are exempt from the 80% pool rule; the per-fold full-coverage mask governs them**
       (NaN in both train and test in a fold whose training rows lack full coverage, untouched otherwise). Every
       other column keeps the 80% rule.
       - **Code owed** (new BACKLOG item, implementer; quant-reviewer review): the exemption in the discovery pool,
         plus a test that `discovery_pool` keeps a named column at 57% coverage and still drops an unnamed column at
         57%. The same item writes `full_coverage_columns` into the pinned dump and the YAML, and makes the
         verbatim-dump test compare tuple and list alike. Due **2026-12-11**, the config date. **Without that merged code, H-02 does not run.**
       - D4 is still reported on H-02's row before the run, as stated above. It is descriptive and does not choose
         between the options: this choice is made now, before D4's numbers exist.
    3. **Scope of the mask.** H-02 is walk-forward only. **CPCV and the pooled path are out of scope**: they do not
       carry the mask, and an H-02 result is never read through them. **Survivor slots 6-9 carry the mask:** a
       survivor trial whose inputs include a `full_coverage_columns` column runs with the same per-fold mask
       (`evaluate(..., full_coverage_columns=...)`), because a survivor trial is H-02's configuration with cluster
       k's inputs. `run_specialist` passes the field through to `evaluate`, in the same BACKLOG item and with a
       test. Without it, a slot whose cluster contains `macro_spx_dd20` (H-07) does not run under the slot.
- **Amendment 2026-10-18 (research director, owner directive ADR 0029; before any H-02 outcome): H-02 is pulled
  forward into 2026Q4** by `preregistration-2026Q4-pullforward.md`. Its config, cluster map, reading rule and slot
  derivation rule are unchanged. That file pins the variant, the bars and the data-v1 and macro digests, and accepts
  the 21 G1 gap weekdays for H-02 (no re-run on repaired data). It funds the trial by withdrawing batch B13. The four
  survivor slots become Q1 open-budget rows S-1..S-4. H-02 is not written as a Q1 queued row, and the reservation
  goes to 1 (C4H) in the change owed there (section 5).
- **Amendment 2026-10-18 (implementer, executing the research director's D-TVR ruling, `prereg-tv-replica-2026-10-18.md`
  B4a item 2; before any H-02 outcome; quant-reviewer sign-off due 2026-10-25): one more feature group,
  `smc_replica`.** The 23 `smc_replica` columns form one group in the cluster map (no `H02_CLUSTERS` entry names them,
  so they stay in their family's group, as `structure` does; `H02_CLUSTERS` itself is unchanged), at most 39, no
  `structure` column in it, computed on our own bars. **K_eff +1 (groups screened); no registry trial.** A TVR FAIL
  does not remove it. **Owed before H-02 runs (not done here):** the replica is an opt-in family, so an H-02 run that
  includes it has a feature version other than the pinned `f-51ac5ebabb`; the YAML row and
  `preregistration-2026Q4-pullforward.md` pin must be re-pinned by the research director before dispatch, or H-02 runs
  without the group (K_eff unchanged). Pinned by `tests/test_smc_replica_group.py`.
  - **Ruling 2026-10-18 (research director, before any H-02 outcome): H-02 runs without the group.** The feature
    version stays `f-51ac5ebabb` (recomputed on `main` a327699 and on the branch 91dc9cf; with the group it would be
    `f-9a074bd2f8`) and H-02's K_eff is unchanged. The reason: no `research.yml` input enables the replica, and the
    replica is not on `main`. O-4 keeps the group, so D-TVR adds +1 in total, not +2. Details are in
    `preregistration-2026Q4-pullforward.md` 2.4a.

### H-01 Slow TSMOM
- Config: `specialist=tsmom`, signal from 1d bars (20/60/120-day vol-scaled returns, 60-day vol), execution and
  labels on 4h bars, target 3.0 ATR(1d) / stop 1.5 ATR(1d), time barrier 20 trading days, long and short, swap
  charged.
- Implemented as the tsmom preset `slow` (`goldbot/specialists/time_series_momentum.py`; tsmom's default
  configuration and labels unchanged, `tests/test_tsmom_slow.py`). Signal on the feature-day bars (d1 context), read on
  the first 4h bar whose close is at or after the settlement, entry at that bar's close (the open of the first 4h bar
  after the signal is visible), at most one signal per daily bar; target/stop frozen at entry (R = one 1.5 x ATR(1d)
  stop); one position at a time; swap per rollover in the net labels. Walk-forward: the 4h window (train 48 / test 6 /
  step 6 months, embargo 4 days) with purge raised from 10 to 31 days, because the label lives up to 28 calendar days
  plus holidays (M14). Agent id `tsmom-g0-c12c9afb24` (was `tsmom-g0-cf39165431` before `schedule_h` was dropped; see
  below).
- **Frozen parameters (added 2026-10-10 after the quant review, BEFORE any H-01 outcome was seen; only events were
  ever counted, no label or return).** Each is enforced in code or stated as the reading of the run:
  - *Friday stub:* the feature-day after Friday's settlement (Friday 13:30-17:00 New York, 3.5 trading hours) is
    dropped from the signal's and the ATR's daily inputs: every daily bar spanning under 12 trading hours (the weekend
    closure Friday 17:00 to Sunday 18:00 New York not counted) is removed (`time_series_momentum.drop_stub_days`). So
    20/60/120 days are 5-a-week trading days (4/12/24 weeks), and Monday's return and true range run from Friday's
    settlement close (`tests/test_tsmom_slow.py::test_friday_stub_is_dropped_so_lookbacks_count_five_trading_days_a_week`,
    `::test_daily_atr_has_no_friday_stub`).
  - *ATR:* Wilder ATR(14) (`features.technical.atr`: true range = max(high - low, |high - prev close|, |low - prev
    close|), exponential mean with alpha 1/14) on the **mid** prices of the stub-free daily bars, the value last
    visible at the signal bar's close.
  - *tsmom_score:* on the stub-free daily mid closes, z_h = (ln C_t - ln C_{t-h}) / (sigma_60 x sqrt(h)) for h = 20,
    60, 120, where sigma_60 is the sample standard deviation (ddof 1) of the last 60 daily log returns; score = mean of
    the three z_h (NaN until 120 + 1 closes exist).
  - *min_score 0.5 is binding:* long when score >= 0.5, short when <= -0.5; no other value is tried under H-01.
  - *Time barrier:* 124 four-hour decision bars after the entry bar (≈20 weekdays, ≈28 calendar days; holidays extend
    it).
  - *`schedule_h` dropped:* it does nothing under `signal_tf` and is left out of the configuration and agent id
    (`Specialist.unused_config`); tsmom's default id is unchanged.
  - *Costs:* extra_cost_usd = 0.37 $/oz round trip beyond the spread (2 x slippage prior 0.15 + 2 x commission 3.5 USD
    per lot side on the canonical broker / 100 oz: `execution.costs.settings_extra_cost_usd`); the spread is paid in
    the labels (ask in, bid out). Replaced by the measured `costs-v1` table if published before the run (the report states which). Swap source: the
    settings priors `costs.swap_long_usd_per_lot: -60.0`, `swap_short_usd_per_lot: 0.0`, triple on Wednesday, on the
    canonical broker's server clock (Europe/Athens), or the measured table's swap if it has one.
  - *Swap bias:* the short swap prior of 0 flatters shorts for 2010-2021 (when short swap was negative) by about
    0.1 R per 28-night hold. The report gives net mean R for both sides together, with and without the short trades
    (that is, long-only), and the reading rule is read on both sides together AND on long-only; a pass that exists only
    with shorts is reported as such and does not continue the hypothesis.
  - *Holdout:* every label that has not exited before 2025-10-01 is dropped (`pipeline.prepare`), including trades
    entered in the research window that would exit into the holdout.
  - *Positive year:* a calendar year (by entry time) counts as positive only with at least 10 trades
    (`gates.MIN_TRADES_PER_YEAR`) and positive mean net R.
  - *t-stat:* mean R / (sample std of R, ddof 1) x sqrt(n) over every trade in the research window
    (`metrics.expectancy`), gross for the screen and net for the reading rule; one-sided thresholds as written below.
  - *DSR trial count:* the registry's `n_trials_effective` + 1 at run time (every recorded Q4 2026 and Q1 2027 trial
    plus H-02's K_eff, per "Fixed for every trial").
  - *Inconclusive branch:* any screen with fewer events than the floor is `inconclusive (event floor)`, whatever the
    sign or t of its gross mean R (`research.screen`, recorded with status `screened`, charged to the budget;
    `tests/test_screen_pool.py::test_gross_t_of_1_5_on_300_events_with_a_floor_of_1000_is_inconclusive_not_fail`): no
    model, and it neither retires nor continues slow TSMOM. A screen on at least the floor's events that misses the
    sign or the t is a plain fail. (Tightened 2026-10-10, before any outcome was seen: previously only "positive and
    significant, too few events" was inconclusive.)
  - *Data warm-up:* the bars start 2010-01-01 (data-v1's first file) and nothing earlier is loaded. The score needs
    120 + 1 stub-free daily closes, so no signal exists before about mid-June 2010 (the first counted signal is
    2010-06-18 16:00 UTC); the research window is 2010-01-01 .. 2025-09-30 with that warm-up at its start.
  - *Net tests:* the reading rule's net t >= 1.65 and positive-year tests use the **rule-only** net R (spread, slippage,
    commission, swap) over **every candidate** (one position at a time, no model filter), on both sides together and on
    long-only. Every report prints them (`research.screen.rule_only_split`: net mean R, t and n for long-only
    (side == 1) and short-only (side == -1), and the positive net years over every candidate;
    `tests/test_screen_pool.py::test_rule_only_net_split_reports_long_only_short_only_and_positive_net_years`).
- **What H-01 can and cannot decide.** At the expected 154-400 events no walk-forward fold reaches the design's model
  gates (>= 1,500 candidates, >= 60 per complete test fold, DSR on >= 200 model-filtered trades), so the model gates
  cannot apply: H-01 is evidence about the rule alone. It can **retire** slow TSMOM; it cannot **promote** it.
  Promotion needs separate, pre-registered model research that meets the unchanged gates.
- Run (Actions -> research -> Run workflow), exactly these inputs:

  | input | value |
  |---|---|
  | specialist | `tsmom` |
  | from_year | `2010` |
  | to_year | `2026` |
  | rationale | `H-01 slow TSMOM (preregistration-2027Q1.md, commit <freeze commit>)` |
  | variants | `["slow"]` |
  | score_holdout | false |
  | skip_screen | false |
  | pooled | (empty) |
  | macro | false |

  `["slow"]` expands to `[{"timeframe": "4h", "signal_tf": "1d", "atr_tf": "1d", "lb_fast_h": 480, "lb_mid_h": 1440,
  "lb_slow_h": 2880, "vol_window_h": 1440, "min_score": 0.5, "target_atr": 3.0, "stop_atr": 1.5, "max_bars": 124}]`
  (either spelling is the same configuration and agent id; a `schedule_h` added to it is ignored). One trial. The
  floor ruled in A is set in `config/settings.yaml` (`research.screen_min_events_daily`) and committed before the run.
- Event count on data-v1 (2026-10-10, counted without labels or any outcome; research window, holdout excluded):
  2,631 daily signals (1,760 long, 871 short); one position at a time, if every trade ran to the 20-day time
  barrier, 165 events. The true count lies between, below the ~450 estimated for the 10-day daily option, and
  probably below the 400 floor first proposed in ruling A: **ruling A must be decided on this count**. The 2,631 was
  counted before the Friday stub was dropped (about one daily bar in six fewer now); superseded by the recount below.
- **Recount after the Friday-stub fix** (2026-10-10, commit `b17de44`, data-v1 2010-01-01 .. 2026-10-05; events only,
  no label, no return, no outcome; research window: a signal counts only if its time barrier ends before 2025-10-01):
  **2,154 daily signals** (1,466 long, 688 short); one position at a time with every trade held to the 124-bar time
  barrier, **154 events** (the lower bound fell from 165 because the stub-free signals are sparser). The true
  one-at-a-time count lies between 154 and 2,154, nearer the lower end.
- **Recount by count-owed-events (2026-10-16, run 38931951849 on `main` `eafc6ce`; data-v1 cut before 2025-10-01;
  events only, no outcome read):** **2,154 signals (1,466 long / 688 short), one-at-a-time lower bound 152.** The
  signals match exactly; the lower bound is 2 below the 154 above. *Provisional explanation (2026-10-16, quant
  review):* identical 2,154 signals; the gap is the lower-bound arithmetic; the 154 was an unscripted count at
  `b17de44`; fixed script count to be pasted by 2026-11-01 (strategy researcher; the script's lower-bound loop was
  one bar stricter than `one_at_a_time`, fixed 2026-10-16). 152 >= 150, the retire floor in force, so **H-01 stays
  queued** as a retire-only test under the decision (c) below. The margin is 2 events: if the explanation shows 152 is the right number and the floor
  committed at the freeze is still 150, the row stands; nothing is tuned to keep it.
- **AT RISK (research director, 2026-10-16 job check; no outcome read).** The 152 was counted on gapped data: the
  coverage triage of 2026-10-16 found 21 whole weekdays missing from data-v1 in the research window (2020-2025, two
  of them NFP Fridays; freeze gap G1). A margin of 2 events leaves no room for that. The planned 2026-11-01 script
  recount and the G3 recount on repaired data are merged into **one count-owed-events run on `main` after G1 closes,
  by 2026-11-13** (readiness list, items 3 and 5). That run's lower bound is the one read against the retire floor:
  >= 150 keeps H-01 queued; < 150 withdraws it by the same rule as H-06, and the 2027Q1 reservation goes from 8 to 7.
  Nothing is tuned and the floor is not moved to keep the row.
- Reading rule (outcomes below written 2026-10-10, before any H-01 outcome was seen; net tests on rule-only net R over
  every candidate, see *Net tests*): **continue** if the P4 screen passes (gross mean R > 0, t >= 2.0, on the event
  floor ruled in A) AND net mean R > 0 with t >= 1.65 AND positive net years >= 3 incl. 2021 or 2022, on both sides
  together and on long-only (swap bias above). **Stop slow trend in gold** if net mean R <= 0 on both sides together
  (the literature's best case then does not survive our costs), or if the screen fails on at least the floor's events.
  If both sides together pass but long-only net mean R <= 0 or long-only t < 1.65, the result is **inconclusive
  (short-only edge under a flattering short-swap prior)** (added 2026-10-11, before any outcome was seen). **Inconclusive** if
  the screen has fewer events than the floor (whatever the sign or t), OR if the screen passes but net mean R > 0
  with t < 1.65, or with fewer than 3 positive net years (or none in 2021/2022): recorded and charged, cannot retire
  the hypothesis and does not continue it. A "continue" means only that model research on slow TSMOM may be
  pre-registered; it is not a promotion.
- Expected: gross +0.05..0.10 R; net depends on the short side because longs pay swap.

### H-06 Macro-conditioned slow TSMOM
- Config: H-01 with candidates kept only when the 20-day change of DFII10 and of the broad dollar both agree with the
  trade side (falling real yield / dollar for longs). Macro release required.
- Reading rule: **continue** only if net mean R exceeds H-01's by >= 0.03 R AND 2022-2024 net is positive (the period
  the yield link broke). Otherwise the macro gate is retired.
- **Reading-rule pins (quant review of H-06, 2026-10-11, before any outcome was seen):**
  - *G1:* "H-01's net mean R" is H-01's **recorded** rule-only net mean R (every candidate, one position at a time),
    and H-06 must run on the **same cost source** H-01 ran with (measured `costs-v1` or the priors; a mismatch is a
    deviation and the comparison is not read).
  - *G2:* "2022-2024 net is positive" means the pooled mean net R over trades entered 2022-01-01 .. 2024-12-31 UTC
    is > 0.
  - *G3:* below the event floor the result is `inconclusive (event floor)`: the macro gate is **not** retired.
  - *G4:* a block-bootstrap interval of the difference (H-06 minus H-01 net mean R) is reported as a diagnostic,
    outside the rule.
- **Readiness gap and withdrawal rule (research director, 2026-10-11, before any outcome was seen).** H-06 has no
  implementation: the `tsmom` presets hold only `slow`, and no specialist reads DFII10 or the broad dollar. It has
  no coded `rule_id` either (`PREREGISTERED_RULE_IDS` = H-01, H-04, C4H), so the evaluator reads the rule above by
  citing it exactly. Before the freeze: (1) the gate is specified as a frozen preset (the 20-day change of DFII10 and
  of the broad dollar index on `macro-v1`, `asof_join` on `available_utc`, as known at the signal bar's close; a
  missing value drops the candidate); (2) its events are counted with no outcome read (signals by side, and the
  one-at-a-time lower bound as for H-01). **If that lower bound is below the daily event floor in force (ruling A),
  the H-06 row is not written, `research.reserved_trials_quarter` for 2027Q1 is set to 8, and no trial is spent.**
  Under any floor H-06 can only be inconclusive on fewer events, so a run would buy no information. If the preset is
  not merged by 2026-12-04, the same withdrawal applies.
  **Planning (2026-10-11, after the owner's ruling A):** the floor applied is the one committed in settings at the
  freeze (150 today). The quant reviewer estimates H-06's one-at-a-time lower bound at about 80-130, so withdrawal
  is likely even at 150. **The Q1 reservation is planned at 8** (4 queued + 4 slots; 7 if H-01 is also withdrawn
  under the research director's decision of 2026-10-11 below). It goes back to 9 only if the
  owed count comes in at 150 or more.
- **Implemented (BACKLOG 48, 2026-10-11; no outcome read, nothing tuned; quant review pending).** The tsmom preset
  `slow_macro` = `slow` + `"macro_gate": true`, agent id `tsmom-g0-fed4baec9f` (`slow` stays `tsmom-g0-c12c9afb24`;
  `goldbot/specialists/time_series_momentum.py`, `tests/test_tsmom_macro_gate.py`). The gate keeps an H-01 candidate
  only when `macro_drivers`' `macro_real_yield_chg20` (DFII10) and `macro_dollar_chg20` (DTWEXBGS), joined by
  `asof_join` on `available_utc` at the close of the 4h bar the candidate fires on, are both < 0 for a long or both
  > 0 for a short; a NaN or zero change drops it. The run needs the macro release (`--macro`, which also gives the
  pre-registered feature version `f-51ac5ebabb`): without `ctx["macro"]` the preset refuses to run rather than spend
  a trial on no candidates. Readings taken where the text above allows more than one, each the most literal, for the
  quant reviewer to confirm or amend before the freeze: (a) "20-day change" = 20 observations of each series' first
  releases (about 20 US business days; the `f-51ac5ebabb` columns); (b) "the signal bar" = the 4h decision bar the
  trade enters on, as H-01's ATR (not the daily bar's settlement, nor the 4h bar's open; with the 23:00 UTC stamps the
  three differ only on data-gap days); (c) "agree" is strict, so a zero change keeps neither side; (d) the dollar
  change is a ratio and DFII10's a difference (the sign, which is all the gate reads, is the same); (e) the model's
  declared inputs are H-01's (no macro column), and the macro columns in the frame are not read by the rule.
- **Counted and withdrawn by its rule (research director, 2026-10-16; no outcome read).** count-owed-events run
  38931951849 on `main` `eafc6ce` (data-v1 cut before 2025-10-01; `macro-v1` 34,381 rows, published 2026-10-13 by
  the scheduled `data-macro` run 38394945106): **1,105 signals (711 long / 394 short); one-at-a-time lower bound
  107**, against the retire floor in force, 150 (`below_floor: True`; the script on `main` still uses the old label
  `daily_floor`). The gate removed 1,049 of H-01's 2,154 signals as disagreeing or zero and 0 for a missing macro value.
  **107 < 150, so H-06 is withdrawn by the rule written above: its row is not written and the 2027Q1 reservation is
  8.** The quant estimate (80-130) is confirmed. The spec change (`withdrawn:` on H-06 and `reservation: 8` in
  `preregistration-2027Q1.yaml`, with `research.reserved_trials_quarter` 2027Q1 = 8 in `config/settings.yaml`, one
  reviewed change, as `preregistration.py` requires) was made by the implementer on 2026-10-16 (quant review
  2026-10-16: count_h06 and 9754ca0 signed off; withdrawal to be confirmed on the merged recount run (must run on a
  commit containing 9754ca0)).
  No owner decision is needed (the rule was pre-registered); the owner is told as an FYI.
- **Run (written 2026-10-16 by the strategy researcher, before any H-06 outcome was seen; no reading rule changes).**
  Only if H-06 survives its withdrawal rule, and only after H-01 has run (its rule compares against H-01's recorded net
  mean R, on the same cost source: pin G1). Actions -> research -> Run workflow, exactly these inputs:

  | input | value |
  |---|---|
  | specialist | `tsmom` |
  | from_year | `2010` |
  | to_year | `2026` |
  | rationale | `H-06 macro-conditioned slow TSMOM (preregistration-2027Q1.md, commit <freeze commit>)` |
  | variants | `["slow_macro"]` |
  | score_holdout | false |
  | skip_screen | false |
  | pooled | (empty) |
  | macro | true |

  `discover`, `families`, `discover_config` and `rule_ref` stay at their defaults (false, empty, `{}`, empty). The run
  uses the queued row because its family and config hash match. H-06 has no coded `rule_id`, so the evaluator cites
  the reading rule above exactly. `["slow_macro"]` expands to `[{"timeframe": "4h", "signal_tf": "1d", "atr_tf": "1d",
  "lb_fast_h": 480, "lb_mid_h": 1440, "lb_slow_h": 2880, "vol_window_h": 1440, "min_score": 0.5, "target_atr": 3.0,
  "stop_atr": 1.5, "max_bars": 124, "macro_gate": true}]`. That is H-01's configuration plus `macro_gate`, agent
  `tsmom-g0-fed4baec9f`, config hash `5650ad206bda`. With `macro` true the feature version is `f-51ac5ebabb`. One
  trial. Data: `data-v1` (the same bars and research window as H-01; the holdout is cut by `pipeline.prepare`) and
  `macro-v1` (`macro_fred.parquet`, published 2026-10-13; the workflow fails rather than run without it, and the
  preset refuses to run without `ctx["macro"]`). Floor: `research.screen_min_events_daily` as committed at the freeze.
  Registry writer (`scripts/preregister_quarter.py --quarter 2027Q1 --from docs/research/preregistration-2027Q1.yaml
  --registry <registry> --bars <data-v1 folder>`): the spec row `H-06` (`specialist tsmom`, `variant slow_macro`,
  `agent_id tsmom-g0-fed4baec9f`, `config_hash 5650ad206bda`, `macro: true`, `feature_version f-51ac5ebabb`, no
  `rule_id`, the rationale above with `{commit}`, the reading rule above verbatim). It is written only when `pending`
  is cleared and `freeze_commit` is set. `--bars` pins the data-v1 digests of every research-window file (a specialist
  row). The dry run (2026-10-16, this commit's tree) rebuilds the agent id, config hash and feature version from code
  and accepts them.

### H-04 Asia-session drift
- Config: long at 00:00 UTC, exit 07:00 UTC (flat before London), stop 1.5 ATR(1h), every trading day, Data before
  2025-10 only (press figures for H1 2026 fall in the holdout).
- Reading rule: **continue** if gross t >= 2.0 on >= 1,000 events AND net mean R > 0. Expected thin: <= 0.03% per
  session against a round trip of about 0.025-0.03% (the frozen 0.37 $/oz plus the spread at ~$2,000-2,500; corrected
  2026-10-11 from "~0.01%" after the quant review, before any outcome was seen; the rule itself is unchanged), so a
  pass needs the measured cost table and the net test is likely to bind.
- Implemented as the specialist `asia_drift` (`goldbot/specialists/asia_drift.py`, `tests/test_asia_drift.py`), agent
  id `asia_drift-g0-98dc13a518`. Research only: `Specialist.screening` keeps it out of the default population founders,
  the pooled 1h model, the research director's plan, the label grid, the monthly loop and the research analyst
  (`jobs.research_families`), so the pre-registered run below is its only trial; it is active (not in
  `research.retired_families`). The RiskGate refuses its intents (`time_exit_ev_unsupported`) until the EV of a
  time-exit rule is implemented (HANDOFF, "before live").
- **Frozen parameters (written 2026-10-11, BEFORE any H-04 outcome was seen; no label, return or outcome was
  computed on real data).** Each is enforced in code or stated as the reading of the run:
  - *Entry:* long only, at 00:00 UTC: the decision bar is the 1h bar closing at 00:00 UTC (23:00-00:00), entry at its
    close on the ask (the open of the first 1h bar at or after 00:00 UTC). No entry when that bar is missing, when the
    session calendar is closed at 00:00 UTC (`DEFAULT_SESSIONS.is_open`: weekend, daily break on the Europe/Athens
    server clock), or on 25 December / 1 January (UTC dates). So entries are Monday to Friday, at most one a day.
  - *Exit:* time barrier at the close of the 7th 1h bar after entry (`max_bars` 6), 07:00 UTC, in both DST regimes
    (London opens 08:00 UTC in winter, 07:00 UTC in summer: flat before or at the open). A plain barrier, no exit
    policy, so labels, shadow book and live count the same bars (`test_shadow_book_reproduces_the_labels_time_barrier`).
    `max_bars` above 6 is refused by the constructor (never past 07:00 UTC).
  - *Sample rule (added 2026-10-11 after the quant review, before any outcome was seen):* an entry is kept only when
    the bar closing its time barrier closes exactly 7 h after the entry, i.e. every 1h bar 00:00-07:00 UTC is in the
    data (`AsiaDriftSpecialist.complete_windows`, applied in `research.pipeline.prepare` before labelling; bar
    timestamps only, no prices; `test_research_drops_entries_whose_window_is_not_exactly_00_to_07_utc`). This drops
    gap days and closures not in the calendar (e.g. Good Friday) whose bar-counted exit would land after 07:00 UTC.
    Live and shadow have no such rule (they cannot see the window ahead): on such a day they would exit at the 7th
    bar; the report states how many entries the rule dropped (the owed data count below).
  - *Stop:* 1.5 x Wilder ATR(14) of the 1h mid bars, the value at the decision bar's close (completed bars only),
    frozen for the trade; R = that stop distance. *No target* (`target_atr` 50, out of reach in 7 hourly bars).
  - *One position at a time* (`labels.one_at_a_time`); a 7-hour hold never overlaps the next day's entry.
  - *Swap:* none. 00:00-07:00 UTC is 02:00-09:00 (winter) / 03:00-10:00 (summer) server time, so no server midnight
    is held through (`test_no_trade_is_held_through_the_server_rollover`: `swap_nights` 0 in both regimes).
  - *Costs:* extra_cost_usd = 0.37 $/oz round trip beyond the spread (as H-01), the spread paid in the labels (ask in,
    bid out); replaced by the measured `costs-v1` table if it exists at run time (the report states which).
  - *Event floor:* `research.screen_min_events` (1,000, an intraday rule: the daily override does not apply). Fewer
    events is `inconclusive (event floor)` whatever the sign or t.
  - *t-stat, net test, positive years, holdout, DSR count:* as frozen for H-01 (mean R / sd x sqrt(n), gross for the
    screen, rule-only net R over every candidate for "net mean R > 0"; holdout-crossing labels dropped; DSR count =
    `n_trials_effective` + 1). Long only, so no long/short split is read.
  - *No walk-forward, no model (quant review M2):* the no-target labels make `target_hit` 0 on every row, so a
    meta-model would fit y = 0. `research_pass.py` never runs the model stage for a `screening` family, pass or fail,
    with or without `skip_screen`: the screen is recorded as one charged trial with status `screened` (the report
    says "rule-only screen: no model was fitted"; `test_a_screening_family_that_passes_its_screen_is_recorded_and_
    never_fits_a_model`). The reading rule above is read on that screen alone; a "continue" means only that model
    research on H-04 (with a time-exit meta-label) may be pre-registered.
  - *Swap spec:* the run passes the settings' SwapSpec (as every research_pass run); `swap_nights` is 0 by
    construction, and the screened row records the SwapSpec.
  - *Live subset (quant review L4):* a live entry at 00:00 UTC needs the owner's click within 90 s at about 01:00
    London time (00:00 GMT / 01:00 BST), so live will trade a subset of the shadow and research events (the
    unconfirmed days are shadow-only candidates). Any later live comparison is on the confirmed subset.
- Run (Actions -> research -> Run workflow), exactly these inputs:

  | input | value |
  |---|---|
  | specialist | `asia_drift` |
  | from_year | `2010` |
  | to_year | `2026` |
  | rationale | `H-04 Asia-session drift (preregistration-2027Q1.md, commit <freeze commit>)` |
  | variants | `[{}]` |
  | score_holdout | false |
  | skip_screen | false |
  | pooled | (empty) |
  | macro | false |

  `[{}]` is the default configuration `{"stop_atr": 1.5, "target_atr": 50.0, "max_bars": 6}`. One trial.
- Event count (2026-10-11, on `00c16d7` plus this change, the commit that adds `asia_drift`; counting only, no prices, labels or outcomes): **data-v1 is not
  available on this machine, so no data count was made.** Calendar upper bound for the research window
  2010-01-01 .. 2025-09-30: **4,087** entry days (4,108 weekdays open at 00:00 UTC on the session calendar, less 21
  weekday 25 Dec / 1 Jan), about 260 a year. The data count (days whose 23:00 UTC bar exists, less the ATR warm-up,
  less the entries the sample rule drops) can only be lower; to be recorded here before the freeze by a run with
  data-v1 present, together with **the number of candidate entries whose 7th bar does not close exactly 7 h after
  entry (exit - entry != 7 h)**, which the sample rule drops.
  **Run count-owed-events and paste the counts before the freeze** (Actions -> count-owed-events -> Run workflow;
  `scripts/count_owed_events.py`: the specialist's own `candidates` + `complete_windows` on data-v1 cut at the holdout,
  no label, return or outcome read). Paste its H-04 rows here: entries, dropped by the 7h sample rule (with their
  dates), calendar upper bound, days without a decision bar, ATR warm-up and the entry whose window crosses the
  holdout, with the run's commit. The same run recounts H-01 (signals by side, one-at-a-time lower bound) as a check
  against the 2,154 / 154 above; a difference is explained here before the freeze.
- **Counted (2026-10-16, count-owed-events run 38931951849 on `main` `eafc6ce`; data-v1 2010-01-01 00:02 ..
  2025-09-30 23:59, cut before the holdout; no outcome read):** **4,025 entries**; **10 dropped by the 7h sample
  rule** (2012-01-02, 2020-05-26, 2021-11-05, 2022-01-31, 2023-11-08, 2024-01-10, 2024-06-07, 2024-07-16,
  2025-02-26, 2025-03-26); calendar upper bound **4,088**, 52 days without a decision bar, ATR warm-up 0. Per year
  250-260 entries (182 for 2025 to September). 4,025 is above the 1,000-event floor in the reading rule. Owed by the
  strategy researcher by 2026-11-01: the 1-day difference between the script's 4,088 and the hand count 4,087 above,
  and confirmation that 4,025 is net of the 10 sample-rule drops and of the entry whose window crosses the holdout (the
  script on `main` does not print that row separately).
  *Note (research director, 2026-10-16 job check):* 9 of the 10 sample-rule drop dates are the whole-day gaps found
  by the coverage triage (G1), so these drops are data loss, not market closures. H-04 is re-read in the same single
  recount run on repaired data, by 2026-11-13; the strategy researcher's explanation above is due with that run's
  output, not separately on 2026-11-01.

### tsmom 4h with swap (continuity)
- Config: `variants=[{"timeframe": "4h", "max_bars": 12}]`, as refused on 2026-10-09.
- Reading rule: if net mean R <= 0, the 4h tsmom horizon is retired (H-01 supersedes it).

## Q1 open-budget round (12 trials under the E-5 cap; research director, 2026-10-17, before any outcome)

Written 2026-10-17, before any configuration below has been run, counted on outcomes or labelled. It allocates the
quarter's **12 open trials** (20 − 8 reserved; `research.reserved_trials_quarter` 2027Q1 = 8). Nothing here changes a
queued row, a slot, a reading rule above or the reservation. North-star (ADR 0004 §6): £50+ on days the market offers
it, less or nothing otherwise; at £1,000 only 15m can trade (M1), so the open budget goes first to the 15m lane that
the Q2 draft had queued, **pulled forward into Q1 where its inputs exist in time**. Where they do not, the row stays
in Q2 and its trial goes to the free reserve. No profit is promised: the 15m record is negative (R-01, R-02, R-05) and
the most likely outcome of this round is more retirements, recorded honestly.

**How the open budget is protected.** The E-5 cap (`research.open_budget_preregistered_only: ["2027Q1"]`) lets the
open budget be spent only by an ad-hoc pre-registration whose `rule_ref` cites a reading rule in this file
(`preregistration-2027Q1.md#O-<n>`, naming the row below), or by the retired families' one shared exploration trial; the director's default pool
and the label grid spend none. In-quarter rows cannot be queued or draw on the reservation, so **this plan is the
protection**: a row not listed here is not run from the open budget in Q1. Each row is written to the registry
(`preregister(..., queue=False, rule_ref=...)`) before its run, after this file's freeze commit.

### Gate-1 power: what the no-rerun decision implies (`gate1-rerun-decision-2026-10-17.md`)

1. **Gate 1 reads only model-filtered walk-forward rows** (`gates_phase.best_backtest_trial`): >= 500 trades
   (`gates.backtest_min_trades`), DSR from >= 200 trades (`metrics.MIN_TRADES_FOR_DSR`), recomputed **on the day's**
   `n_trials_effective`, swap and cost source recorded. A rule-only screen (H-01, H-04, the C4H screen, H-17, H-24,
   H-18) can never pass gate 1. Its best outcome is "continue", which only permits pre-registered model research.
2. **Retention is set by the pinned threshold rule, not by evidence** (amended 2026-10-17, quant review of 0f9d6d8,
   finding 8). The coded rule (`pipeline.THRESHOLD_MARGIN`, `cpcv.py`, `discovery.reading_rule`) takes a candidate
   when its cross-fitted calibrated p > break-even + 0.02. On trial #16 that kept 33 of 3,829 candidates (0.9%).
   **0.9% is the floor case used in this file**, an illustration and not a forecast. At that retention, 500 trades
   need about 55,000 candidates. **Thresholds are never changed to reach 500 trades.** So:
   - **Only 15m designs with many candidates a day can reach gate 1 in one generation.** H-14 has an estimated
     100,000+ candidate bars (count owed). At the floor case that gives about 900 trades before one position at a
     time.
   - **H-02 survivor trials** run on the 4h tsmom pool (about 3,800 candidates, as #17) under the coded rule. They
     need >= 13% retention, against a floor case of 0.9%, so they are expected to be **evidence, not gate-1 passes**.
     Their rules are unchanged.
   - **H-01 cannot read "continue"** under the strict floor: its count (152 lower bound) is below the 1,000 pass
     floor, so it reads at best "inconclusive". The Q2 follow-up items 1-2 (H-01 model research and exit variant)
     therefore cannot arise.
3. **The DSR bar in t units.** The DSR as coded uses var(SR) = 1/n with E[max] from Bailey and López de Prado. The
   approximation below ignores skew and kurtosis. A gate-1 pass needs a model-filtered t of about E[max Z_K] + 1.645.
   Net mean R needed at a per-trade SD of 1.4 R (2:1 barrier):

   | K = `n_trials_effective` at gate time | t needed | 500 trades | 1,000 trades | 5,000 trades |
   |---|---|---|---|---|
   | 25 | 3.6 | 0.23 R | 0.16 R | 0.07 R |
   | 40 (registry rows only, end of Q1) | 3.8 | 0.24 R | 0.17 R | 0.08 R |
   | ~60 (40 + H-02 K_eff at the group unit, ~10 + H-14 ~12): **the case adopted (Q-H02U)** | 4.0 | 0.25 R | 0.18 R | 0.08 R |
   | ~350 (40 + H-02 K_eff at the feature unit, ~300 + H-14 ~12): superseded | 4.6 | 0.29 R | 0.20 R | 0.09 R |

   Run order cannot lower the bar, because gate 1 recomputes the DSR on the count of the day it is read. Nothing in
   this round is ordered to dodge deflation.
4. **Q-H02U, ruled by the quant reviewer 2026-10-17 (review of 0f9d6d8, finding 3), before any H-02 outcome: H-02
   moves to `survivor_unit: group`.** Its slot rule forwards **whole clusters** (`group_members`), which is the group
   unit's premise. K_eff becomes the groups screened (about 10) instead of the features (about 300). The
   owner-confirmed conservative default (H-02's K_eff is added to every later trial) is unchanged. **This lowers the
   gate-1 bar for every later trial, from about t 4.6 to about t 4.0.** The dated amendment and the new pinned dump
   are under H-02 above. PD+PO record it, and the owner is told on #34.
5. **No rerun slot in Q1.** The rerun criterion (a row with `gates.passed: true` and >= 200 trades but no
   `dsr_inputs` or cost record) cannot arise from rows #21 on, because they store both. If it ever does, the free
   reserve funds it by a written note.

### Fixed for every row in this round (in addition to "Fixed for every trial")

- **Statistic (ADR 0022).** The reading t is mean / day-clustered se: CR1, clustered by entry UTC day, on G − 1
  degrees of freedom, with G = the number of distinct entry days. The iid t (`metrics.expectancy`) is reported
  beside it. With at most one trade a day (H-17, H-24, H-18, H-04 follow-ups) the two are equal, so nothing changes
  for those rows. The clustered t binds for H-14 and its survivors, where several trades share a day. Power is
  stated in days. Day clustering applies **to the trade t only**. *Amended 2026-10-17 (quant review of 0f9d6d8,
  finding 2):* every AUC lower bound keeps the coded **weekly-block** bootstrap (`discovery.auc_lower_bound`: the
  candidates of one ISO week resampled together, 500 resamples, seed 0). No day-block AUC option is added.
- **Threshold rule, per row (amended 2026-10-17, quant review of 0f9d6d8, finding 1).** The coded model-stage
  threshold is: take a candidate when its **cross-fitted calibrated p > break-even + 0.02**
  (`pipeline.THRESHOLD_MARGIN`, `cpcv.py`, `discovery.reading_rule`). No tercile cut exists in code (built by E-2,
  2026-10-17, as amended below). Each row below
  names its threshold:
  - O-5, O-6, O-8 and O-10 use the coded rule.
  - O-4's pre-set rule uses a top-tercile cut, which **E-2 must implement**: the cut is the 2/3 quantile of the
    **inner cross-fitted out-of-fold p** of the training fold's rows, never in-sample training p, and it must come
    with a test proving that the cut never sees a test-fold row. Without that merged code O-4 does not run.
  - *Amended 2026-10-17 (E-2 build, quant plan review of the BACKLOG 75 plan, conditions 1-7; written before any
    H-14 candidate count):* the cut is pinned as coded (`pipeline.fold_cuts`, `DiscoveryConfig.threshold_rule:
    tercile_inner`). For test fold k it is `np.quantile(p, 2/3, method="linear")` of the raw out-of-fold p of the
    rows that are **both a test row of an earlier fold and in fold k's training rows** (`train_idx`: inside its
    24-month rolling window, after the purge), finite p only. With fewer than 50 such rows the fold has no cut and
    takes no trade. A candidate is taken when its raw test-fold p >= the cut; there is **no break-even floor**
    (the share of trades below break-even is reported, never read). q = 2/3 and the linear method are part of the
    rule: any other q or method is a new trial. Folds whose test windows overlap are refused. Under this rule the
    discovery's reading rule replaces "(cross-fitted, break-even + margin)" with the tercile clause and adds:
    "promotion needs a separate trial under the live rule". A tercile result does not carry to live trading, which
    keeps break-even + 0.02.
  - Thresholds are never changed to reach a trade count.
- **Costs (P-5, as the Q2 draft).** The source is pinned at this file's freeze. It is the named `costs-v1` version
  if published by 2026-12-11, otherwise the priors ("priors accepted" on 2026-12-14): 0.37 $/oz beyond the spread,
  the spread paid in the labels (ask in, bid out), and swap −60 long / 0 short USD per lot-night, triple on
  Wednesday. It is never switched before the run, and a later `costs-v1` is a reported sensitivity only.
  - Every intraday row is flat by 16:30 New York, so `swap_nights` is 0, with a test in each specialist.
  - **15m yardstick:** on a normal day a spread is 0.19 of a 15m ATR (BACKLOG 45 baseline). At a 1.5-ATR stop that
    is about 0.13 R, plus about 0.14 R of extra cost at the window's mean 15m ATR ($1.80), so **about 0.27 R per
    round trip**. On holiday and quiet days it is up to 1.3-1.65× that. A 15m rule needs a gross edge above about
    0.3 R for net > 0. That cost, not t, is the binding test for every 15m row. *The 0.27 R is for sizing the plan
    only, and no rule reads it (quant finding 9).* In the London and New York windows the spread is 0.15-0.18 of a
    15m ATR. At H-14's cost-gate floor (ATR(15m) = the round trip) the cost is 1 ATR on a 1.5-ATR stop, **up to about
    0.67 R**. Commitment: the count-owed-events run for H-14 publishes the **quartiles of cost in R** across the
    gated candidates here, before O-4's row is written.
- **Data.** `data-v1` after the G1 repair, with research-window digests pinned at the freeze (G4). The holdout is
  never loaded except by O-10, and only after G2 closes. `calendar-v1` is needed where stated.
- **Overlays (0 trials).** H-13 (net R with and without the cost filter, frozen above) and H-19 (net R by HAR band,
  from `opp_har_band`; BACKLOG 30a is merged) are reported on every trade-producing row. They are never part of a
  rule. The opportunity-day report (BACKLOG 33) is printed when it exists, and is diagnostic only.
- **Q1 cut-off.** A row whose condition does not hold by its date, or that has not run by **2027-03-26**, is not run
  in Q1. No trial is spent, and it returns to the Q2 draft unchanged. Its trial joins the free reserve.
- **Spec by reference.** For H-16, H-17 and H-14, the configuration is the section of the same name in
  `preregistration-2027Q2-draft.md` **at this file's freeze commit** (both files sit in that commit), with that
  section's settled pins P-1, P-2 and P-5. The differences are stated per row below. A later edit of the Q2 draft
  does not change a Q1 row. *Quant ruling 2026-10-17 (review of 0f9d6d8, finding 4):* P-3 is accepted, with a
  test that the research blackout set equals `risk.blackout.events` in settings. P-6 and P-7 are accepted, with
  `stability_threshold: 0.7` written explicitly in H-14's dump. Pins P-4 and P-8 (and any P-3/P-6/P-7 detail left
  open) must be ruled by the quant reviewer before the first
  count of the row they touch. Any config detail fixed after the freeze is recorded here as a dated amendment before
  that row's count runs.

### Ranked allocation (12 trials)

Rank = the unified north-star rank (`hypotheses.md` A3). The order of runs is given below the table.

| Row | Rank | ID | Trials | Runs in Q1 only if | Condition holds by | Else |
|---|---|---|---|---|---|---|
| O-1 | 1 | H-16 opportunity score (range test) | 1 | `calendar-v1` published; `first_oos_month` and `expected_eval_days` counted and pasted | 2027-01-29 | stays in Q2 |
| O-2, O-3 | 4 | H-17 V0, V1 (trend day + runner) | 2 | E-3 `trend_day` and BACKLOG 31 (`flat_at_local`) merged; `calendar-v1`; `high_events_strict` >= 1,000 on `trend_day`'s own code; P-3/P-8 ruled | 2027-02-12 | count < 1,000: **withdrawn** (no trial); otherwise Q2 |
| O-4 | 5 | H-14 15m discovery (owner request) | 1 | E-2 spec and BACKLOG 31 merged; `calendar-v1`; group map hash and full `DiscoveryConfig` pinned; P-3/P-6/P-7 ruled; candidate count pasted | 2027-02-12 | Q2 |
| O-5, O-6 | 5 | H-14 survivor groups | <= 2 | O-4 reads continue | O-4 verdict by 2027-03-12 | free reserve |
| O-7 | 9 (H-04 lane) | H-24 West-hours drift (short) | 1 | H-04 reads **continue**; screening specialist merged | 2027-03-05 | Q2 Q1-follow-up reserve |
| O-8 | 9 | H-04 model research (time-exit meta-label) | 1 | H-04 reads **continue**; config written here as an amendment before the run | 2027-03-05 | as O-7 |
| O-9 | — (holiday rule) | H-C session-scoped holiday block: **0-trial diagnostic** (amended 2026-10-17, quant finding 7) | 0 | H-04 reads **continue**; holiday instance list frozen | 2027-03-05 | not run |
| O-10 | — | Holdout scoring of a Q1 gate passer | 1 | a Q1 row passes gate 1 (or an H-02/H-14 survivor needs its discovery holdout rule); G2 closed | 2027-03-19 | Q2 free reserve |
| — | — | **Free reserve** | 3 | only by a written research-director note before the pre-registration: a 3rd H-14 or 5th H-02 survivor, a qualifying rerun, or the retired families' shared exploration trial | — | unspent |
| **Total** | | | **12** | 9 conditional + 3 reserve | | |

**Zero-trial items** in the same round: the H-13 and H-19 overlays, **H-C** (O-9), **H-A** (the forward broker check of the
holiday proxy) and **H-B** (holiday cost in R on frozen candidate sets, family by family after each verdict).

**Why this order.**
- **H-16 first.** It is the one forecastable property of the day (R-grade evidence) and serves two KPIs, opportunity
  days captured and drawdown, without an entry edge. Its code is merged (E-4, `goldbot/research/h16.py`); only
  `calendar-v1` is missing.
- **H-17 next.** It is the only idea aimed at the £50-day mechanism at £1,000: a trend day caught early and held
  with a runner. It has a low prior.
- **H-14 is the owner's standing request.** It is the only design in the pipeline whose candidate pool can reach
  gate 1 in one generation (point 2 above).
- **The H-04 follow-ups** come from the one Q1 mechanism with a Tokyo-hours thesis, but they trade 1h (M2), so they
  rank below the 15m lane.
- **H-C** is now a 0-trial diagnostic (O-9; quant finding 7). With about 13% power its only allowed Q1 outcome is
  "the whole-day block stays". Its trial returns to the free reserve.
- **O-10** exists because a discovery-derived pass cannot be promoted without its one holdout scoring.
- **Considered and not funded:**
  - H-18: owner ruling O-1 is still open, and its ~690 events are below the 1,000 floor.
  - H-11: no consensus feed (ruling C).
  - H-03/H-05/H-07/H-08/H-10: they run only through the H-02 slots.
  - The retired families' exploration trial: no family has post-retirement out-of-sample evidence, because there is
    no shadow book yet.
  - A #16 rerun: see the no-rerun decision.

**Order of runs.**
1. From 2027-01-01, the queued rows run in their frozen order: H-02, H-01, H-04, C4H.
2. O-1 runs as soon as its condition holds.
3. O-2 and O-3 run together. They do not wait for O-1's verdict, because they read the HAR baseline only.
4. O-4 runs next.
5. O-5 to O-9 run only after the verdict of the row they depend on, and O-10 runs last.

### Row texts

#### O-1 H-16 opportunity score (1 trial; no trades)
- Config: as the Q2 draft's H-16 (unit, target, baseline, full score, monthly refits, frozen `first_oos_month` and
  `expected_eval_days` in the config hash, `research_pass.py --h16`). No difference.
- Reading rule (O-1, H-16; written 2026-10-17 before any H-16 outcome, as the Q2 draft and `hypotheses.md` A2):
  **continue** if, pooled out of sample, Spearman ρ(full, y) − ρ(HAR, y) >= 0.05 AND the mean W/A of the full
  score's top quintile is >= 1.5 × its bottom quintile's; otherwise **fail**: the day factor (BACKLOG 30b) uses HAR
  alone and GVZ, tier-1 event count and compression are retired as range inputs. A run below 90% of
  `expected_eval_days` is refused before any statistic and records nothing.
- Power: one row per day, so no clustering issue. About 3,380 evaluable days (calendar estimate, count owed). With
  an assumed standard error of about 0.015 on the ρ difference, a true gain of 0.07 passes about 90% of the time
  and a true gain of 0.03 about 9%. This is a threshold rule, not a significance test. The 20-day block-bootstrap
  interval is reported, not read.
- Costs: none (no trades). Kill: the fail branch above. H-17 is unaffected (it reads HAR only).
- Data first: `calendar-v1` (owner: `FRED_API_KEY`), `macro-v1` (present), G1/G4.

#### O-2, O-3 H-17 trend-day continuation, V0 and V1 (2 trials)
- Config: as the Q2 draft's H-17 (HAR-baseline high half, noise boundary 1.0 × B, first breach only with the literal
  blackout [−15, +30) min, stop 1.5 ATR(15m), V0 target 3 ATR, V1 50% at 2 ATR + chandelier 2.5 ATR armed at 1.5 ATR,
  cap 10 ATR, flat 16:30 New York), specialist `trend_day` (E-3), exits through `policy_step` (BACKLOG 31).
- Reading rule (O-2/O-3, H-17; written 2026-10-17 before any H-17 outcome, the Q2 draft's rule with quant ruling
  Q-1): V0 and V1 each **pass** their screen if gross t >= 2.0 on >= 1,000 events, net mean R > 0, and >= 3 positive
  net years including 2021 or 2022. **Continue with the runner** if V1 passes, V1 net mean R >= V0 net mean R, and
  the high half's gross mean R exceeds the low half's by a Welch t >= 2.0 on weekly blocks; **continue with fixed
  exits only** if V0 passes, the Welch test holds, and V1 fails or is below V0; **retired as a repeat of R-02/R-05,
  final,** if the Welch t < 2.0, whatever else holds. V1 is read only if V0 passes its screen and the Welch test
  (otherwise it is recorded as run and not read, and still costs its trial). Fewer than 1,000 events: inconclusive
  (event floor). "Continue" permits only pre-registered model research or shadow.
- Power: one trade a day, so the clustered t equals the iid t. At 1,000–1,500 events and SD about 1.2 R, gross
  t >= 2 is reached with 80% power at a true gross of 0.09–0.11 R. The weekly Welch test detects a high-minus-low
  gap of about 0.12 R at 80% (about 600 weeks a half, weekly-mean SD about 0.76 R). **Net > 0 binds:** it needs gross
  above about 0.27 R. Gate 1 is out of reach for a rule-only row (point 1).
- Kill: Welch t < 2 (final retirement, joining R-02/R-05); `high_events_strict` < 1,000 (withdrawn before the run,
  and the median cut is never moved to raise it).
- Data first: `calendar-v1`, G1/G4; engineering E-3 and BACKLOG 31.

#### O-4 H-14 15m scalping discovery (1 trial; owner request)
- Config: as the Q2 draft's H-14 (candidates every 15m close 08:00 London .. 16:00 New York outside the tier-1
  blackout and where ATR(15m) >= the round trip; both sides labelled; triple barrier 3.0/1.5 ATR, 16 bars, flat 16:30
  New York; default feature families on `f-079deacc61` grouped by family; 15m walk-forward 24/3/3 months, purge 2 d,
  embargo 1 d; `survivor_unit: group`, `group_top_k: 3`, `stability_threshold: 0.7` (P-7)). **Pinned here (before
  any count):** `top_k: 39` = `max_selected: 39` (as H-02, so a survivor group plus `side` fits one model; quant
  finding 5) and `stability_threshold: 0.7` written explicitly in the dump (P-7). The AUC lower bound is the coded
  **weekly-block** bootstrap (finding 2). The full `DiscoveryConfig.model_dump()` and
  `group_map_sha256` are written here as an amendment when E-2 merges, before the candidate count.
- *Amendment 2026-10-17 (group map; quant review of `candidates/scalping-2026-10-17.md` at 61695d4, findings 1-2;
  written before any H-14 count, label or outcome, from column counts on synthetic bars only):*
  - **Why.** At 15m the decision frame adds h1/h4/d1 context columns of `atr`, `realised_vol`, `moving_averages`
    and `trend_strength` (`pipeline.CONTEXT_FEATURES`, `context_tfs("15m")`), and `column_groups` puts each context
    column in its base column's group. Grouped by family, `trend` then has about 72 columns and `volatility` about
    49-53, both over `max_selected` 39, so `check_group_sizes` refuses O-4 as configured. `structure` has 38 and
    playbook D-5's $100 step would take it to 40.
  - **One splitting rule (pinned now).** Decision-timeframe columns stay in their family. **Every context column
    goes to the group `ctx_<tf>` of its timeframe (`ctx_h1`, `ctx_h4`, `ctx_d1`), across families** (about 26
    columns each). New 15m columns never go into `structure`: they form two new families, `liquidity` (sweep
    depth/age by reference, the $100 step, stop-cluster distances) and `activity` (time-of-day-relative Dukascopy
    volume `volbid_*`, quote fraction, v3 THIN-day flag, LBMA auction / overlap / 08:30 ET windows). Any group still
    over 39 columns after this rule refuses the run; it is never truncated or re-split after a count.
  - **H-08 spans two groups.** The existing `sweep_*` columns stay in `structure` (`market_structure` v2);
    `liquidity` therefore measures only what it adds to them. A `structure` survival is not evidence for the new
    columns, and a `liquidity` survival is not evidence for `sweep_*`.
  - **Feature version.** O-4's pinned feature version `f-079deacc61` is **superseded for O-4 only** by the version
    that adds `liquidity` and `activity`. That version and the group map hash are written here when they merge
    (due 2026-12-11), before the count. No other row's feature version changes.
  - **Code owed (BACKLOG item, product-owner files; implementer, quant-reviewer):** (a) the grouping above, as a
    pinned map or a cluster map whose globs take precedence over the base-column join; (b) a test that O-4's pinned
    map, **with context columns included**, has no group over 39; (c) `check_group_sizes` moved **before**
    `reg.preregister` in `discovery_pass` (today it runs after it, `discovery.py` ~838-860, so a refusal leaves a
    pre-registered row), with confirmation and a test that such a row spends no trial.
  - **K_eff.** Groups screened = 11 families + 3 context groups + 2 new families = **about 16** (was "about 10-15").
    The adopted gate-1 case becomes about 66 (40 + H-02 ~10 + H-14 ~16): t ≈ 4.03, still 4.0 at one decimal, so the
    "Gate-1 power" table is unchanged.
  - **Pinned in `DiscoveryConfig`: `context_groups: per_timeframe`** (in the dump and the config hash; code 6705096).
    *(Added 2026-10-17, research director, before any H-14 count, label or outcome.)*
  - **Group count verified.** On `f-079deacc61` the coded map gives **14 groups** (verified by
    `tests/test_group_size_before_preregister.py`). The two new families `liquidity` and `activity` bring it to about
    16, so "about 16" stands. The exact K_eff and `group_map_sha256` (the pool map) are written here when the
    liquidity/activity feature version merges, before the count.
  - **Amendment 2026-10-18 (strategy researcher; before any H-14 count, label or outcome; group map not yet
    pinned): one more feature group, `tod_periodicity` (N1, `candidates/next-2026-10-18.md`).** Six columns
    (`slot_mean_ret_{5,10,20}d`, `slot_hit_rate_20d`, `slot_t_20d`, `slot_ret_over_atr`), a separate group, **not**
    inside `activity` (C-5/C-6 overlap). Slots are 30-minute UTC bins; a 15m bar maps to the slot containing its open
    (both 15m bars of a slot map to it). Each column uses only the same slot on earlier days, so
    `available_utc` = the prior day's slot close; joined by `asof_join`; `lookahead_check` must report 0 columns.
    **K_eff about 16 -> about 17; the adopted gate-1 case about 66 -> about 67** (40 + H-02 ~10 + H-14 ~17; the "Gate-1
    power" table is re-read at that case before the count). The group must be coded before the pin (due 2026-12-11).
    Mechanism: Heston, Korajczyk & Sadka 2010, *J. Finance* 65(4) (equities, gross; untested on gold). No registry
    trial; no extra event or trade-rate pre-check (a slot summary of past returns is an outcome read).
    - **Signed 2026-10-18, research director: ADOPTED into O-4 (Q1), 0 registry trials, K_eff +1.** Checked before
      signing: (1) **allowed now.** O-4's group map and `DiscoveryConfig` dump are not pinned (due 2026-12-11), the
      file is not frozen (`freeze_commit: null`, freeze 2026-12-14), O-4 has no registry row and no row in
      `preregistration-2027Q1.yaml`, and no H-14 count, label or outcome exists; O-4's own condition date
      (2027-02-12) is later still. (2) **No hashed row changes.** The YAML rows stay as they are: H-02's pinned
      `DiscoveryConfig` dump and `f-51ac5ebabb`, H-01 `561030150146`, H-04 `3d33124f210c`, C4H `184bddf12019`, the
      withdrawn H-06 `5650ad206bda`, and the default version `f-079deacc61`, **on one condition, pinned here: `tod_periodicity` is an opt-in family, never
      in `DEFAULT_FEATURE_NAMES`** (as `day_type`), because `features.registry.feature_version` hashes the family
      list. A build that adds it to the default set is refused at review. Only O-4's own (superseded, not yet
      pinned) feature version and `group_map_sha256` include it. (3) **Drop, not delay.** If the group is not merged
      with `lookahead_check` reporting 0 columns and the group-size test passing by 2026-12-11, O-4 is pinned
      without it (K_eff back to about 16) and N1 waits for the next 15m discovery; O-4 never waits for N1.
      (4) **Budget.** The Q1 table is unchanged: 8 reserved, 12 open (9 conditional rows + 3 free reserve). Code:
      BACKLOG item `tod-periodicity-feature-group` (implementer; quant-reviewer, code-reviewer).
    - **Pinned 2026-10-18 (implementer, BACKLOG 89, quant condition on 2c000ef; before any H-14 count, label or
      outcome).** `feature_version` hashes names, versions and signed patterns, not code, so the open choices of
      `goldbot/features/tod_periodicity.py` (VERSION "1") are fixed here; any later change bumps VERSION:
      (1) only complete slots are observations (a slot missing a bar is left out); (2) a window is the last N complete
      observations of the slot, with no calendar cap, NaN with fewer than N; (3) the hit rate counts ret > 0 only (ties
      are misses); (4) `slot_t_20d` = mean / (sample sd, ddof=1, / sqrt(20)), NaN at sd 0; (5) a bar reads the latest
      observation at most 5 days old (`MAX_STALE`), else NaN; (6) `slot_ret_over_atr` = the latest observation /
      (ATR14 / close at that observed slot's last bar); (7) a slot return is log(last close / first open) of the slot,
      the gap from the previous close is excluded; (8) neutrals: mean 0, hit rate 0.5, t 0, ret_over_atr 0.
  - **Amendment 2026-10-18 (implementer, executing the research director's D-TVR ruling, `prereg-tv-replica-2026-10-18.md`
    B4a item 2; before any H-14 count, label or outcome; group map not yet pinned; quant-reviewer sign-off due
    2026-10-25): one more feature group, `smc_replica`.** The 23 columns of `goldbot.features.smc_replica.COLUMNS` form
    one group (family `smc_replica`, an opt-in family, never in `DEFAULT_FEATURE_NAMES`; 23 <= 39, so no split). No
    `structure` column is in it (`bos_*` and `sweep_*` of `market_structure` stay in `structure`; the replica's
    `bos_*_{internal,swing}` are different columns, and a test pins that no name is shared), so a survival measures what
    the replica adds. Computed by our own code on our own Dukascopy bars; no charting-site data is read. A TVR FAIL
    does not remove the group (features are not rules). **K_eff about 17 -> about 18; the adopted gate-1 case about 67
    -> about 68** (t still about 4.0). No registry trial. Pinned by `tests/test_smc_replica_group.py`. As for `tod_periodicity`,
    O-4's own feature version and `group_map_sha256` include it; no hashed row changes.
- Threshold (amended 2026-10-17, E-2; quant review R1): `threshold_rule: tercile_inner`,
  `tercile_q: 0.6666666666666666` and `tercile_method: linear` in the pinned `DiscoveryConfig` dump (so in the config
  hash: another q is another trial). The coded verdict needs at least 1,000 taken trades for "continue", as the
  reading rule below. The walk-forward must have `step_months >= test_months`, which is checked before any row is
  written. This is the top-tercile cut as pinned under "Threshold rule, per row" (q = 2/3, linear, prior set =
  earlier folds' out-of-fold rows inside fold k's training rows, no break-even floor). O-4's result row records
  `threshold_rule`, `tercile_q`, `tercile_method`, `fold_cuts`, `retention` and `share_taken_below_breakeven`.
  Reading the same candidates under the coded rule would be another trial. A survivor is promoted only through a
  separate trial under the live rule (O-5/O-6).
- Reading rule (O-4, H-14; written 2026-10-17 before any H-14 outcome): a group **survives** if it is selected in
  >= 70% of folds (frequency >= 0.6) AND the walk-forward on the selected set has out-of-fold AUC >= 0.53 with a
  weekly-block bootstrap lower 95% bound > 0.50 (`discovery.auc_lower_bound`) AND the pre-set rule (trade when the
  test-fold p >= the 2/3 quantile of the training fold's inner cross-fitted out-of-fold p, as implemented and tested
  in E-2, never in-sample training p; one position at a time, both sides) has net mean R > 0 on >= 1,000 trades (P-6: trades, not candidate rows),
  with its day-clustered t reported. At most two surviving groups (highest stability first) earn O-5 and O-6.
  **Stop** if AUC < 0.52: the 15m bulk features carry no directional information, stay as cost and risk filters only,
  O-5/O-6 are cancelled and the plan records that 15m at £1,000 has no research path besides H-17 and H-18.
  AUC 0.52–0.53: inconclusive, O-5/O-6 cancelled.
- Power: a calendar upper bound of about 4,000 days × about 52 bars, less the cost gate (larger in 2012–2019), so
  probably 100,000+ candidate bars and twice as many labelled rows (count owed). Under weekly blocks (about 700
  weeks) the AUC's standard error is expected to be under 0.01, so the 0.53 bound is resolvable. **Every trade count
  here is conditional on E-2's tercile cut.** A top tercile of 100,000+ candidates gives several thousand trades
  after one position at a time. The floor case, the coded rule's retention on #16 (0.9%), gives about 900. Either
  way the net-R test is about cost (0.27 R typical, up to 0.67 R at the gate floor), not power. K_eff = groups screened (about 10–15; about 16 under the 2026-10-17 group-map amendment above) is added to every later trial.
  **Withdrawal (written 2026-10-17, before any count or outcome; quant review finding 3):** if the cost-in-R
  diagnostic (`candidates/scalping-2026-10-17.md` D-1, inputs at the decision bar only) gives a median round trip
  above 0.35 R at **every** admissible gate floor (1×, 1.5×, 2× the round trip, each leaving >= 55,000 candidate
  bars), O-4 is **withdrawn before it runs**: no trial is spent and nothing is added to K.
- Kill: AUC < 0.52 (above). The 15m lane is then H-17 alone, and research-plan §7 applies if H-17 also fails.
- Data first: `calendar-v1`, G1/G4; engineering E-2 and BACKLOG 31.
- **Amendment 2026-10-18 (research director; before any H-14 count, label or outcome; for quant-reviewer review).**
  O-4 is **not** pulled forward. Its group map cannot be pinned yet: `liquidity` and `activity` are not coded, and
  the candidate generator, BACKLOG 31 and `calendar-v1` are missing. `preregistration-2026Q4-pullforward.md`
  section 3.3 adds three rules:
  - **Drop, not delay:** `liquidity` and `activity` are dropped if they are not merged by 2026-12-11.
  - O-4 runs as soon as its conditions hold from 2027-01-04, without waiting for O-1..O-3.
  - O-4 may run in 2026Q4 only if all its conditions hold by 2026-12-18 and the owner raises the Q4 override by 1.

  Its map may not change citing H-02's result.

#### O-5, O-6 H-14 survivor groups (<= 2 trials)
- Config: a walk-forward model on H-14's label and candidates, inputs = the surviving group's recorded columns +
  `side` (<= 40), tagged `from_discovery`. Written here as an amendment after O-4's verdict and before the run.
- Threshold: the coded rule (cross-fitted calibrated p > break-even + 0.02), not a tercile cut
  (`threshold_rule: break_even_margin`, the default; amended 2026-10-17, E-2). This is the live rule, so O-5/O-6 are
  the separate trial under the live rule that a tercile-read O-4 survivor needs before promotion.
- Reading rule (O-5/O-6, H-14 survivors; written 2026-10-17 before any H-14 outcome; day-summed DSR inputs specified
  2026-10-17 per quant finding 6): **pass** only if the unchanged design gates pass (>= 1,500 candidates, >= 60 per
  complete test fold, positive net expectancy in >= 3 calendar years incl. 2021 or 2022, DSR >= 0.95 on >= 200
  model-filtered trades at the real trial count) AND, stricter than the gates and fixed now, the **day-summed DSR**
  is also >= 0.95. That DSR is computed on one summed net R per entry UTC day, over days with at least one
  model-filtered trade only (n = G such days), with var(SR) = 1/G, the skew and kurtosis of those daily sums, and the
  same K = `n_trials_effective` as the gate (both discoveries' K_eff included). It is a coded check (E-6:
  `metrics.day_summed_dsr`, `tests/test_day_summed_dsr.py`), and without it the row does not run; promotion additionally needs the discovery holdout rule (O-10). Otherwise **fail** and the group
  is retired as a 15m entry signal (kept as a filter feature only).
  - Amendment 2026-10-17, before any H-14 outcome (quant re-review of E-6; noted for the owner by the program
    director). The rule gets stricter in three ways:
    - **G floor.** The day-summed DSR also needs G >= 200 trading days, the same asymptotic floor as the per-trade
      DSR's 200 trades. With fewer days the check reports nothing and the row fails closed (**fail**, or
      **inconclusive** only when the stored inputs are missing).
    - **Entry day.** A trade's entry UTC day is that of its signal bar's open plus one bar (`ts_utc + timeframe`, the
      real entry at the next bar's open).
    - **Gate DSR.** The gate's DSR in this rule is the per-trade DSR on `ret`, as the phase gate reads it
      (`gates.current_dsr`). The per-trade figure stored beside the day-summed one is on R and is shown only.
  - The rule is coded as `rule_id` "O-5"/"O-6" on the run's pre-registration (`director.survivor_decisions`,
    `survivor_refusal`). A verdict, a holdout slot or a promotion counts a survivor's pass only with both DSRs
    >= 0.95 on today's K.
- Power (conditional on O-4's candidate count): gate 1 needs >= 500 model-filtered trades. At the floor case (0.9%
  of 100,000+ candidates, about 900 trades) and K ≈ 60 (t about 4.0), that is about 0.19 R net per trade. A higher
  retention lowers the bar per trade, but the threshold is never changed to get one. The day-summed check raises
  the bar further when several trades share a day.
- Kill: fail → retired as an entry signal.

#### O-7 H-24 West-hours drift, short (1 trial; only if H-04 continues)
- Config: as `hypotheses.md` H-24 (short at 08:30 New York, flat 16:00 New York, stop 1.5 ATR(1h), every trading
  day, no target, rule-only screening specialist like `asia_drift`, swap 0 by construction).
  - **Amendment 2026-10-17 (strategy researcher, quant review of 1f0447e finding 2; written before H-04's verdict and
    before any H-24 outcome; `candidates/intraday-2026-10-17.md` IC-1).**
    - **Entry.** On UTC-floored 1h bars (`resample.py`), "08:30 New York" falls inside a bar. It is also inside the
      tier-1 blackout [−15, +30) min of an 08:30 ET release. The entry is therefore pinned at the **close of the 1h
      bar closing 09:00 America/New_York**, DST-aware (13:00 UTC in summer, 14:00 UTC in winter), short on the bid,
      Monday to Friday when the session calendar is open.
    - **Exit.** A time barrier at the close of the 1h bar ending 16:00 New York (7 bars). Stop 1.5 × ATR14(1h) at
      the decision bar, frozen. No target.
    - **Estimand change.** H-24 now tests the West-hours drift from 09:00 to 16:00 New York. The first 30 minutes
      after the open, including the 08:30 release reaction, are excluded. A result does not speak to 08:30–09:00.
      The reading rule below is unchanged.
    - **Withdrawal rule** (no trial spent). Before the row is written, the median round-trip cost in R of the pinned
      candidates (spread at the decision bar + 0.37 $/oz, over 1.5 × ATR14(1h)) is computed on the **repaired**
      data-v1 (after G1, digests pinned at G4) by the promoted diagnostic (`candidates/intraday_diag_2026-10-17.py`
      D3, once moved into `scripts/` with its tests). If the median exceeds **0.10 R**, O-7 is withdrawn. The
      expected edge (<= 0.03% a session, about 0.07 R) could then never be net positive.
    - The count and the median are pasted here before the row is written.
- Reading rule (O-7, H-24; written 2026-10-17 before any H-04 or H-24 outcome, as the Q2 draft's follow-up item 4):
  **continue** if gross t >= 2.0 on >= 1,000 events AND net mean R > 0 (rule-only, every candidate, one position at a
  time); **retire** the West leg if net mean R <= 0 or the screen fails on >= 1,000 events; fewer events:
  inconclusive (event floor).
- Power: about 4,000 events, one a day. Gross t >= 2 at 80% power needs a true gross of about 0.045 R (SD about
  1 R). The net test binds (round trip about 0.025–0.03% of price against an expected edge of <= 0.03%).
- Kill: as the rule. Data first: G1/G4; engineering: the screening specialist, merged within 3 weeks of H-04's
  continue and by 2027-03-05.

#### O-8 H-04 model research (1 trial; only if H-04 continues)
- Config written here as a dated amendment after H-04's verdict and before its run: `asia_drift` candidates (4,025
  counted on gapped data), a time-exit meta-label, inputs <= 40.
- Reading rule (O-8, H-04 model research; written 2026-10-17 before any H-04 outcome): **pass** only if the unchanged
  design gates pass at the real trial count; otherwise **fail** and H-04 stays a rule-only result (shadow attribution
  only).
- Power: gate 1 needs >= 500 model-filtered trades out of about 4,000 candidates, so a retention of >= 12.5%. That is
  stated now, and a model whose fold thresholds retain less cannot pass gate 1 whatever its DSR (lesson of #16).
- Data first: G1/G4.

#### O-9 H-C session-scoped holiday block (0-trial diagnostic; only if H-04 continues)
- Claim, measure, margin and actions as in `holiday-list-measurement-2026-10.md` (H-C). **Amended 2026-10-17, before
  any H-04 outcome:** the note's condition "H-04 not retired" becomes **"H-04 reads continue"**. Session scoping
  matters only when there is an Asia strategy to trade, so an inconclusive H-04 gives H-C no user.
- **Amended 2026-10-17 (quant review of 0f9d6d8, finding 7): a 0-trial diagnostic, not a trial.** Its NON-INFERIOR
  branch is removed for Q1, and its trial returns to the free reserve (now 3).
- Reading rule (O-9, H-C diagnostic; written 2026-10-17 before any H-04 outcome): the gross mean R difference
  (holiday Asia entries minus matched controls, US early-close holidays MLK, Presidents, Memorial, Juneteenth,
  4 July, Labor, Thanksgiving) and its 95% interval are reported, with net and mechanical cost separately. **The only
  allowed outcome is "the whole-day block stays"** (labelled WORSE if the upper bound is below 0, otherwise
  INCONCLUSIVE). No result scopes the block.
- Power: about 97 entries with about 4 controls each, standard error about 0.12 R, so **about 13% power** for
  non-inferiority at −0.10 R. H-C is reopened as a trial only when pooled Asia holiday entries (more families or more
  years) give >= 50% power at that margin, under a new pre-registration. One entry per day, so the clustered and iid
  t agree.
- Data first: the 2010-01..2025-09 holiday **instance list** (dates by day type, as the BACKLOG 45 final computes
  them; the v3 YAML covers 2026-2028 only), committed with its sha256 before H-04 runs, re-read after the G1 repair.

#### O-10 Holdout scoring of a Q1 gate passer (1 trial)
- Reading rule (O-10, holdout; the coded `gates.holdout_verdict`, fixed before any holdout read): the model-filtered
  trades in 2025-10-01 .. 2026-09-30 have mean R > 0 with one-sided t >= 1.65 (`HOLDOUT_MIN_T`); the day-clustered
  t is reported beside it. One scoring per configuration, ever.
- Power: at about 500 trades in the year (15m) and SD 1.4 R, 80% power needs a true mean of about 0.16 R; at 250,
  about 0.22 R. A 1h/4h passer with tens of trades is close to unreadable. That is stated now so a fail is not
  re-scored.
- Data first: **G2** (holdout repaired: 42 missing weekdays and the bid-imputed months). No scoring before G2 closes.

### Zero-cost holiday items (H-A, H-B; `holiday-list-measurement-2026-10.md`)
- **H-A (forward broker check):** window 2026-10-18 .. 2027-04-30. The THIN set is Thanksgiving 2026-11-26, 24 Dec,
  31 Dec, MLK 2027-01-18 and Presidents 2027-02-15. It needs >= 4 clean THIN days (recorder up >= 90% of the day's
  open minutes). **At risk:** no daily `copy_ticks` recorder job exists on the brain VM, and BACKLOG 1 (demo
  terminal) is late. Without a recorder by 2026-11-25, Thanksgiving is lost and one more miss makes H-A
  INCONCLUSIVE by construction.
- **H-B (cost in R):** read family by family after each verdict, never before. Expected: H-01 about 6 THIN-day
  candidates (INCONCLUSIVE by construction), H-04 about 110. C4H's expected count is stated by the strategy researcher
  before its row is written. It uses the same frozen instance list as O-9.

### What must land first (dependencies of this round)

| Dependency | Rows | Accountable | Needed by | Status 2026-10-17 |
|---|---|---|---|---|
| G1 data-v1 2020-2025 repair → G3 merged recount → G4 digests pinned | every row | data-engineer (G1, G4), research-director (G3) | 2026-11-06 / 2026-11-13 / 2026-12-14 | targeted refetch in the release PR of 2026-10-17 (CI running); not on `main` |
| G2 holdout repair | O-10 | data-engineer | 2026-11-06 (needed before 2027-03-19) | open |
| `calendar-v1` (owner adds `FRED_API_KEY`; data-engineer dispatches `data-us-calendar`) | O-1, O-2/O-3, O-4 | owner (chaser: program-director) | key by **2026-11-30** (escalated now, 6 weeks ahead) | blocked on the owner; release absent |
| `costs-v1` or "priors accepted" | every row (P-5 pin) | owner / research-director | 2026-12-11 / 2026-12-14 | open; fallback likely |
| E-2 15m discovery candidate spec + BACKLOG 31 `flat_at_local`. E-2 includes the top-tercile cut from inner cross-fitted p, with a test that the cut never sees a test-fold row (quant finding 1) | O-4 (31 also O-2/O-3) | implementer (quant-reviewer) | 2027-01-29 | tercile cut built 2026-10-17 (BACKLOG 75, TRACEABILITY M48), quant and code review pending; the 15m candidate spec still open |
| E-6 day-summed DSR coded check (`metrics.day_summed_dsr`: daily net-R sums over days with trades, var(SR) = 1/G, skew and kurtosis of the sums, K = `n_trials_effective`; `tests/test_day_summed_dsr.py`) | O-5, O-6 | implementer (quant-reviewer) | 2027-02-26 | new; BACKLOG item owed (program-director to file) |
| H-02 `survivor_unit: group` amendment (doc + yaml dump, done here); PD+PO record; owner note on #34 | every later row's K | research-director (doc), PD+PO (record), quant-reviewer (#34 note) | 2026-12-11 | doc done 2026-10-17; record and note open |
| E-3 `trend_day` screening specialist | O-2/O-3 | implementer (quant-reviewer) | 2027-02-05 | not started |
| Q2 pins P-3, P-4, P-6, P-7, P-8 ruled | O-2/O-3, O-4 | strategy-researcher drafts, quant-reviewer rules | 2026-10-25 (Q2 draft date; unchanged) | open |
| Q-H02U (H-02 survivor unit) | K for every later row | quant-reviewer | 2026-12-11 | new, open |
| count-owed-events: H-16 month/days, H-17 `high_events_strict`, H-14 candidates | O-1, O-2/O-3, O-4 | research-director dispatches, strategy-researcher pastes | after `calendar-v1`; by 2027-01-22 (H-16), 2027-02-12 (H-17, H-14 on their own code) | not countable yet |
| BACKLOG 70 cme-metals-v3 list + BACKLOG 45 final with the 2010-2025 instance list committed (sha256) | O-9, H-B | implementer (70), performance-analyst (45) | 2026-10-30; instance list frozen before 2027-01-01 | 70 ready; 45 draft ruled |
| Daily broker tick recorder (`copy_ticks`, one Parquet per server day) on the demo | H-A | sre (with data-engineer) | 2026-11-25 | **no item exists**; to the program-director |
| `research-plan.md` §6 and the Q2 draft synced to this round (H-16/H-17/H-14 move to Q1 conditionally); holiday note's H-C | — | research-director | 2026-10-23 | **done 2026-10-17** |

## Owner rulings (decided 2026-10-11; source: owner approved the research director's recommendations, 2026-10-11)

The owner's words were "yea listen to research director". Each ruling adopts the recommendation in the research
director's cycle report of 2026-10-11. None changes a reading rule written above.

- **A. Event floor for daily-signal families: 150 rule-only events, retire-only.** `research.screen_min_events_daily:
  150` is committed in `config/settings.yaml`; gross t >= 2.0 is unchanged. Below 150 a daily-signal screen is
  `inconclusive (event floor)` whatever its sign or t. At 150 or more it can fail and retire. It cannot promote:
  promotion still needs the unchanged design gates (`research/gates.py`: >= 1,500 candidates, >= 60 per complete
  test fold, DSR on >= 200 model-filtered trades), which 154-2,154 events can never reach. The floor applied to a
  trial is the one committed at the freeze.
  - **Code check (2026-10-11, code not changed).** `goldbot/research/screen.py` `screen_verdict` (the verdict line,
    about l.75) returns `pass` for a daily rule with >= 150 events, a positive gross mean and t >= 2.0. That pass
    (a) lets `scripts/research_pass.py` go on to the walk-forward model stage in the same trial for a family that is
    not `screening` (the `min_events_for` call, about l.686, then the model branch), and (b) lets H-01's coded rule
    read `continue` (`goldbot/research/director.py` `preregistered_decisions`, H-01 branch). "Continue" permits
    only pre-registering model research, and the model stage fails the gates on so few candidates. So the floor can
    retire but never promote. A screen with 150-999 events **can** still read "pass". If "retire-only" is meant in
    that stricter sense, it is a code change for the quant reviewer and the implementer, not made here.
  - **Strict reading (owner, 2026-10-11; code changed 2026-10-11, before any outcome was seen).** The owner ruled
    the floor retire-only in the strict sense: `screen_verdict` now takes a retire floor and a pass floor
    (`screen.screen_floors`: the daily override is the retire floor, `research.screen_min_events` the pass floor). A
    daily rule with 150-999 events reads `fail` when it misses the sign or the t, and `inconclusive (event floor)`
    otherwise; it never reads `pass`, so it never reaches the model stage and H-01's coded rule reads it
    `inconclusive`. No reading rule above changes.
- **B. Trial budget: 20 a quarter, unchanged.** 4 or 5 queued rows + 4 contingent H-02 survivor slots (ADR 0007),
  the rest open.
- **C. Consensus feed: none.** H-11 stays blocked.
- **D. Other instruments: out of scope.**
- **K_eff default: confirmed.** H-02's K_eff is added to the DSR trial count of every later trial, survivor or not.
- **costs-v1:** if the demo terminal runs and `costs-v1` is published by **2026-12-11**, Q1 uses it under the rule
  above (the measured table if it exists at run time). Otherwise, on **2026-12-14**, the research director writes
  "priors accepted" here, and Q1 runs on the stated priors (0.37 $/oz beyond the spread; swap long -60 / short 0 USD
  per lot per night).
- **Owner's trade history:** a complete export, analysed locally (`python -m goldbot.ops.run owner-history`); only
  the summary (R, %, counts, K) is shared (`owner-method-study.md`). No Q1 trial depends on it.

## Research director decision: daily-signal rows under the strict floor (2026-10-11, before any outcome)

**Question.** Under the strict retire-only floor (150 events retire; a pass needs 1,000), a daily-signal row whose
one-at-a-time count is below 1,000 can never read "pass". H-01's lower bound is 154 (recount owed). H-06's is
expected at about 80-130 (quant estimate). Each row costs 1 of the quarter's 20 trials, and its best possible outcome
is "inconclusive". Options: (a) keep both; (b) withdraw every row whose lower bound is below the pass floor; (c) keep
H-01 only, as the family's retire test. The owner delegated this decision to the research director.

**Decision: (c) keep H-01 as a retire-only test; H-06 is withdrawn unless its count reaches the retire floor.**
Concretely:
- **H-01 stays queued, conditionally.** It is written only if the count-owed-events recount of its one-at-a-time
  lower bound is >= 150 (the retire floor committed at the freeze). Below that it is withdrawn like H-06, because it
  could then not even retire.
- **H-06 is withdrawn** if its lower bound is < 150. This is the rule already written above, which the quant
  estimate makes the expected outcome. If it is withdrawn, the reservation is 8.
- **If both are withdrawn**, the reservation is 7 (H-02, H-04, C4H + 4 slots).

**Why (c), not (a) or (b):**
- *H-06 has no decision value.* Below 150 its only possible outcome is "inconclusive (event floor)", and "inconclusive"
  neither retires nor continues. Even above 150 it can only retire the macro gate, and that is a variant of a family
  whose base rule has not been read. Spending a trial on it would buy no information and would add 1 to every later
  trial's DSR count.
- *H-01 can still change a decision.* Slow TSMOM is the best-supported hypothesis on the list (MOP 2012, Hurst et al.
  2017). A "stop" (net mean R <= 0 on both sides, or a failed screen on >= 150 events) would end the slow-trend lane:
  no model research, no H-01 follow-up slot in Q2, H-06 and H-03 deprioritised. It would also record that the
  literature's best case does not survive our costs. Either outcome returns a point estimate of net R after real costs.
  That estimate is the one number the owner's "other instruments" question needs if it is ever reopened.
- *(b) would leave the family untested at no saving that matters.* In Q1, 11 trials are open under the E-5 cap. They can
  be spent only on `rule_ref` pre-registrations or the shared retired-family exploration trial, so one trial for
  H-01 displaces nothing planned. The cost is +1 in every later trial's DSR count. It is accepted for the one row
  that can retire a family.

**Known weakness, stated before any outcome (not a rule change).** At 154-400 events the screen has low power. A real
gross edge of +0.05-0.10 R would give t of only about 0.6-1.5, so a true edge would very likely also read "fail" and
be retired. A "stop" from H-01 is therefore weak evidence of absence. It is recorded as **"retired at low power"**.
Like every retired family, it can be reinstated by out-of-sample evidence (`research/director.py` reinstatement
path) or by a later trial on more events, for example a multi-instrument or longer-history test, if the owner ever
reopens ruling D. A "stop" does not stop the 1d/4h shadow book from logging slow-TSMOM signals for attribution,
which costs no trial. This weakness is why (c) keeps H-01 only as a retire test: it is not expected to find an edge.

**Owner confirmation:** not required. The decision was delegated, stays inside the 20-trial budget, spends fewer
trials than the plan, and touches no rail. It is reported to the owner as an FYI, with the low-power caveat.

**Count labels.** `scripts/count_owed_events.py` reports the floor against which H-01's and H-06's lower bounds are
compared. It is being relabelled from `daily_floor` to **`retire_floor`** (another lane). The withdrawal tests above
read that `retire_floor`, equal to `research.screen_min_events_daily` at the freeze (150).

## Q1 readiness: what must exist before 2027-01-01 (research director; live list, last checked 2026-10-16)

**How this list is kept.** Every row names ONE accountable role and a date (`docs/AGENT_STANDARDS.md`, "Accountability
and cadence"); others may contribute. The research director checks it against `gh release list`, `gh run list` and
the registry twice a week and corrects a status the day it changes. Statuses: **done**, **on track**, **blocked (on
whom, chase date)**, **late (new date, reason)**.

**Check of 2026-10-16** (`gh release list`, `gh run list`, `gh secret list`; corrects the 2026-10-11 text, which said
`macro-v1` did not exist):
- Releases present: `data-v1` (2026-10-01), `research-v1` (registry.jsonl, 2026-10-09; 20 Q4 2026 rows, no
  `preregistered` row and no slot yet), **`macro-v1` (published 2026-10-13 12:07 UTC by the scheduled `data-macro` run
  38394945106, success; asset `macro_fred.parquet`, 34,381 rows)**. Absent: `calendar-v1`, `costs-v1`.
- `data-us-calendar` has never run (0 runs): the repo has no `FRED_API_KEY` secret (`gh secret list` is empty), which
  only the owner can add. Its Sunday schedule (06:17 UTC) will fail on 2026-10-18 if the key is not set by then.
- count-owed-events (on `main` since 2026-10-11, commits `9343f2f`, `c7a2889`, `d2f18c6`) was **dispatched on `main`
  by the research director on 2026-10-16: run 38931951849, success**
  (https://github.com/engraamirkhan/goldbot/actions/runs/38931951849, commit `eafc6ce`). It runs without
  `calendar-v1` and reports the calendar-dependent rows as "not counted". Its Q1 counts are pasted under H-01, H-04
  and H-06 above and summarised in item 3.
- The pre-registration writer (`preregister-quarter`, BACKLOG 47) is on `main` (`ab24d87`) and has never run.
- **Job check of 2026-10-16 (research director, acting on the coverage triage; nothing dispatched).** data-v1 has 21
  whole weekdays missing in the research window and 42 in the holdout (triage bullet in HANDOFF). Five data gaps
  G1-G5 are added below as item 5. H-01 is **AT RISK** (lower bound 152 against the floor of 150, counted on gapped
  data). The 2026-11-01 script recount and the 2026-11-13 recount on repaired data are merged into one
  count-owed-events run after G1 closes. W1-5 (H-13 cost filter) is scheduled to start Mon 2026-10-19 and the
  performance analyst starts BACKLOG 45 now (item 6). The evaluator adds a G3 check row by 2026-11-14 (item 4).

**1. Queued registry rows** (each written with `TrialRegistry.preregister(..., queue=True, target_quarter="2027Q1")`
and this file's freeze commit in the rationale, stamped by the registry's clock, then synced to `research-v1`; written
by the `preregister-quarter` workflow from `preregistration-2027Q1.yaml`)

| Row | Config (exact) | `rule_id` | Feature version | Accountable | Date | Status |
|---|---|---|---|---|---|---|
| H-02 | `--discover --families cluster`, `--specialist tsmom --variants '[{"timeframe": "4h", "max_bars": 12}]'`, the pinned `DiscoveryConfig` dump above (top_k 39, permutation confirmation, freq/stability 0.6, group_top_k 3, survivor_unit group (amended 2026-10-17, Q-H02U), H02_CLUSTERS), label 4h TB 3.0/1.5/12, macro on, bars range 2010-01-01 .. 2025-09-30 written into the config | none (read by the discovery verdict) | `f-51ac5ebabb` (default + macro drivers v5) | strategy-researcher (quant-reviewer checks the config) | config 2026-12-11; row 2026-12-18 .. 12-23 | on track (`macro-v1` now exists) |
| H-01 | `tsmom`, `variants ["slow"]`, agent `tsmom-g0-c12c9afb24` | **`H-01`** | `f-079deacc61` | strategy-researcher | row 2026-12-18 .. 12-23 | **AT RISK (2026-10-16)**: lower bound 152 vs floor 150, counted on data-v1 with 21 weekdays missing (G1); stays queued (retire-only) until the single recount on repaired data (by 2026-11-13, item 3) is read: < 150 withdraws it by its rule (reservation 8 -> 7) |
| H-06 | frozen macro-gate preset `slow_macro` | none | `f-51ac5ebabb` | strategy-researcher | withdrawn 2026-10-16 | **withdrawn by its rule** (lower bound 107 < 150): no row is written |
| H-04 | `asia_drift`, `variants [{}]`, agent `asia_drift-g0-98dc13a518` | **`H-04`** | `f-079deacc61` | strategy-researcher | row 2026-12-18 .. 12-23 | on track (4,025 entries counted) |
| tsmom 4h continuity | `tsmom`, `variants [{"timeframe": "4h", "max_bars": 12}]` | **`C4H`** | `f-079deacc61` | strategy-researcher | row 2026-12-18 .. 12-23 | on track |

**2. Reserved slot rows.** Four survivor slots on H-02's queued row, written after it:
`reserve_slot(h02_row, "survivor", k, <derivation rule as amended 2026-10-11>, "2027Q1",
survivor_pool=["H-03", "H-05", "H-07", "H-08", "H-10"])`, k = 1..4, slot rule `{"min_stability": 0.6, "min_auc":
0.53, "min_auc_lower_bound": 0.5}`. **No follow-up and no holdout slots for Q1** (decided here). Q1 follow-ups are
funded from Q2's six-slot follow-up reserve, and any holdout scoring comes from the open budget under the E-5 cap.
Settings and the spec today: `research.reserved_trials_quarter` = `{"2027Q1": 8}` (4 queued + 4 slots). **H-06 was
withdrawn by its rule on 2026-10-16**, so the reservation went from 9 (5 queued + 4 slots) to 8, or 7 if H-01 is also
withdrawn; the spec, the settings, this sentence and `tests/test_preregister_quarter.py` (which pins it) changed
together, in one reviewed change.

| Item | Accountable | Date | Status |
|---|---|---|---|
| `preregistration-2027Q1.yaml`: H-06 `withdrawn:` (reason: lower bound 107 < 150, run 38931951849), `reservation: 8`; `config/settings.yaml` `reserved_trials_quarter` 2027Q1 = 8, in one reviewed change (quant-reviewer checks) | implementer | 2026-10-23 | **done 2026-10-16** (quant review 2026-10-16: count_h06 and 9754ca0 signed off; withdrawal to be confirmed on the merged recount run (must run on a commit containing 9754ca0)) |
| Four survivor slots written on H-02's row | strategy-researcher | with the rows, 2026-12-18 .. 12-23 | on track |

**3. Owed counts** (events only, no outcome). Source: count-owed-events run 38931951849 on `main` `eafc6ce`,
2026-10-16 (bars 2010-01-01 00:02 .. 2025-09-30 23:59, cut before holdout 2025-10-01; macro 34,381 rows; calendar
absent).

| Count | Value (run 38931951849) | Accountable | Date | Status |
|---|---|---|---|---|
| H-04 entries / dropped by the 7h sample rule / calendar upper bound / no decision bar / ATR warm-up | 4,025 / 10 (dates under H-04; 9 are G1 gap days) / 4,088 / 52 / 0 | strategy-researcher (explains 4,088 vs 4,087 and the holdout-crossing entry from the merged recount) | 2026-11-13 (was 2026-11-01; merged into the G3 run) | counted 2026-10-16 on gapped data; re-read in the merged recount |
| H-01 recount: signals (long / short), one-at-a-time lower bound | 2,154 (1,466 / 688), 152 | strategy-researcher (pastes the fixed script count from the merged recount) | 2026-11-13 (was 2026-11-01; merged into the G3 run) | **AT RISK**: counted 2026-10-16 on gapped data, margin 2; 152 vs 154 explained provisionally (lower-bound arithmetic) |
| **Merged recount** (replaces the planned 2026-11-01 and 2026-11-13 runs): one count-owed-events dispatch on `main` on the repaired data-v1, with the fixed lower-bound loop; reads H-01 and H-04 and records the data-v1 digests it ran on | research-director dispatches; strategy-researcher pastes and explains | after G1 closes (2026-11-06); by Fri 2026-11-13 | scheduled; not dispatched (inputs do not exist yet: G1 open). One run, not two: the second would recount on the same gapped data and spend Actions minutes the December runs need (G5) |
| H-06 signals (long / short), lower bound vs retire floor, removed (no macro / disagreeing or zero) | 1,105 (711 / 394), 107 vs 150 (below), 0 / 1,049 | research-director | 2026-10-16 | **done: withdrawn by its rule** |
| Q2 counts (H-16, H-17, H-18, H-14) | H-16 first OOS month 2012-03 (expected 2012-04); the rest "not counted" (`calendar-v1` absent; E-2/E-3 not merged) | strategy-researcher | rerun after `calendar-v1` exists | not needed for Q1; blocked on `calendar-v1` (owner: `FRED_API_KEY`) |

**4. Freeze checklist, with one accountable role and a date for each item** (the boxes below are ticked only when
done)

| Item | Accountable | Date | Status |
|---|---|---|---|
| Ruling A (daily event floor; recommendation 150, retire-only), B (budget 20, keep), C (consensus feed, decline for now), D (other instruments, out of scope), and the conservative K_eff default: recorded here and `research.screen_min_events_daily` committed | owner (chaser: program-director) | 2026-10-11 | **done** (owner approved; floor committed) |
| `macro-v1` published | data-engineer | 2026-10-18 | **done 2026-10-13** (scheduled `data-macro` run 38394945106; weekly Tuesday refresh) |
| `calendar-v1` published (`data-us-calendar` on `main`; needed by Q2, not by Q1) | owner (sets `FRED_API_KEY`; chaser: program-director), then data-engineer dispatches | key 2026-10-18; release 2026-10-19 | **blocked on the owner**: no `FRED_API_KEY` secret (checked 2026-10-16); chase 2026-10-18 |
| `costs-v1` published from the VPS, or "priors accepted" written here | owner (VPS step, BACKLOG 1; chaser: program-director), research-director records the fallback | 2026-12-11 / fallback 2026-12-14 | open; the 13 Oct demo deploy has not happened, so the fallback is likely |
| Pre-registration writer (BACKLOG 47; `scripts/preregister_quarter.py` + `preregister-quarter` workflow) | implementer | 2026-12-04 | merged to `main` (`ab24d87`); never run. First dry run (`gh workflow run preregister-quarter.yml --ref main -f dry_run=true`) by the research director: not yet dispatched (the 2026-10-16 dispatch was refused by this session's permission policy; to be run with approval), new date 2026-10-20 |
| H-06 preset merged and counted, or withdrawn by its rule (BACKLOG 48) | research-director | 2026-12-04 | **done 2026-10-16**: preset on `main` (`d2f18c6`), counted, withdrawn (107 < 150) |
| Reservation and spec set to 8 after H-06's withdrawal | implementer | 2026-10-23 | **done 2026-10-16** |
| H-10: no `jump_news` for Q1 (decided here). H-10 reads jumps only, which is inconclusive for H-10 by its rule, so the feature version stays put | research-director | 2026-10-11 | **done** |
| Feature-version line final (`f-079deacc61`; macro `f-51ac5ebabb`); any later bump amends it first | quant-reviewer | 2026-12-11 | on track |
| Freeze commit of this file; `<freeze commit>` filled in every run-input table | research-director | 2026-12-14 | on track |
| Rows and slots written, `reserved_trials_quarter` matches, `research-v1` synced | strategy-researcher | 2026-12-18 .. 12-23 | on track |
| **G3 check** (evaluator adds this row to the evaluator's checks): the merged recount ran on `main` on the repaired data-v1 (digests match the G1 release, 0 unexpected weekday gaps in 2010-01..2025-09), H-01 and H-04 are read by their written rules against the retire floor (150) with nothing tuned, and the reservation in the spec and settings matches the result (8, or 7 if H-01 is withdrawn) | evaluator | row added by Sat 2026-11-14; verdict within 2 days of the recount | open (recorded 2026-10-16) |
| Registry and this file agree (row count, configs, `rule_id`s, slot rule, reservation) | evaluator | 2026-12-28 | on track |
| Nothing from this queue runs before 2027-01-01. Run order from 2027-01-01: H-02, H-01, H-04, C4H (H-06 withdrawn) | research-director | 2027-01-01 | on track |

**5. Data gaps from the coverage triage (2026-10-16; full evidence in the HANDOFF triage bullet, roadmap W1-6)**

| Gap | What must be true | Accountable | Date | Status |
|---|---|---|---|---|
| G1 | data-v1 2020-2025 repaired to 0 unexpected weekday gaps in 2010-01..2025-09: `scripts/check_coverage.py` (code by Tue 2026-10-27), a merge that never replaces a published day with fewer bars, then targeted refetches of the 15 affected months. No `full_refresh` dispatch of `data-dukascopy` until that merge lands | data-engineer | Fri 2026-11-06 | open; blocks the merged recount (item 3) and G3 |
| G2 | Holdout (2026) repaired: 42 missing weekdays, Feb/Mar/May bid-imputed, in the same lane, before any holdout scoring | data-engineer | Fri 2026-11-06 | open; not needed for Q1 runs, needed before any holdout score |
| G3 | count-owed-events dispatched on `main` again on the repaired data; H-01 (AT RISK) and H-04 re-read. This is the merged recount of item 3 | research-director | Fri 2026-11-13 | scheduled after G1; evaluator check row by 2026-11-14 (item 4) |
| G4 | Research-window files (2010-2025) final at the freeze commit; the rows of 12-18..12-23 pin their sha256 digests (BACKLOG 50), so a later rewrite refuses every Q1 run | data-engineer | Mon 2026-12-14 | open |
| G5 | The Saturday `data-dukascopy` run stops re-downloading the three bid-imputed 2026 months for about 4 h (about 1,000 of 2,000 free Actions minutes a month that the December pre-registration and research runs need): cap or skip the retry | data-engineer | Tue 2026-10-27 | open |

**6. Research lanes feeding the freeze and the opportunity-day work**

| Item | Accountable | Date | Status |
|---|---|---|---|
| W1-5: H-13 volatility-forecast cost filter overlay (new `research/costfilter.py`, frozen as the Q1 overlay; 0 trials), freeze prep with the budget-guard test (BACKLOG 5) | strategy-researcher (quant-reviewer) | start Mon 2026-10-19; due Fri 2026-10-30 | **not started** (checked 2026-10-16); scheduled to start Mon 10-19 |
| BACKLOG 45: holiday-list measurement (spread, ticks per hour and 15m ATR on each listed day against matched normal days, by session) from stored bars and ticks | performance-analyst (quant review) | draft delivered 2026-10-16 (feda07a); final Fri 2026-10-30; G1 re-run addendum by Fri 2026-11-13 (same run as the H-04 recount) | **draft ruled 2026-10-16** (quant CHANGES, v3 list SAFE TO ADOPT; applied by the research director); v3 ADOPTED by PD+PO 2026-10-16, list change is BACKLOG 70 (implementer, 2026-10-30). H-A/H-B/H-C rewritten in the note. H-A, H-B and H-C (row O-9) are pre-registered in "Q1 open-budget round" (2026-10-17), **all 0-trial** (H-C made a diagnostic by quant finding 7) |

**7. Q1 open-budget round** (section "Q1 open-budget round", added 2026-10-17): its dependency table is part of this
live list (one accountable role and a date per row) and is walked with it twice a week. Its rows O-1 .. O-10 hold no
reservation; none is written to the registry before this file's freeze commit.

## Freeze checklist (by 2027-01-01)
- [x] Owner rulings A-D, the K_eff default and the costs-v1 rule recorded here (2026-10-11; `screen_min_events_daily: 150` committed)
- [ ] `costs-v1` published from the VPS by 2026-12-11, or "priors accepted" written here on 2026-12-14 (owner ruling 2026-10-11)
- [x] `macro-v1` release present (published 2026-10-13 by the scheduled `data-macro` run 38394945106; checked 2026-10-16)
- [x] `research_pass.py --discover` merged and tested (`scripts/research_pass.py --discover`, `tests/test_discovery.py`; checked 2026-10-11)
- [x] count-owed-events merged and run on `main` (data-v1); its counts pasted here (run 38931951849, 2026-10-16: H-04
  entries and the 7h-rule drops; H-01 recount). The two small differences (H-01 152 vs 154; H-04 upper bound 4,088 vs
  4,087) are explained with the merged recount on repaired data (by 2026-11-13; was 2026-11-01)
- [ ] data gaps closed: G1 and G5 (data-engineer), G3 merged recount (research director, 2026-11-13) with the
  evaluator's G3 check (2026-11-14), G4 research-window files final at the freeze commit (2026-12-14). H-01 AT RISK
  until the merged recount is read
- [x] H-06 preset merged and counted, or H-06 withdrawn (withdrawn 2026-10-16: lower bound 107 < 150)
- [x] the reservation set to 8 in the spec and in settings (implementer, done 2026-10-16)
- [ ] pre-registration writer merged (BACKLOG 47; merged `ab24d87`) and dry-run on `main`; the 4 survivor slots written on H-02's row
- [ ] each trial's `preregistered` registry row written with this file's commit id
- [ ] open-budget round (O-1 .. O-10): Q-H02U ruled (done 2026-10-17: group; PD+PO record and #34 note open), E-2 tercile cut and E-6 coded, the 2010-2025 holiday instance list frozen with its
  sha256 (O-9, H-B), and the round's rule texts in this file at the freeze commit; its later conditions are dated in its
  dependency table, not here
