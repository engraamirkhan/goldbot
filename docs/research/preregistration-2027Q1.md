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
  Survivors go forward as features, so K_eff = features screened. Group stability uses the size-neutral **joint
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
  survivor_unit: feature
  top_k: 39
  ```

  (`l1_c` is unused with `method: lgbm` but is pinned, because it is part of the dump.)
- Reading rule (decided now): a feature cluster **survives** if its selection stability >= 0.6 across folds AND the
  walk-forward on the selected set has OOF AUC >= 0.53 with the lower 95% bound > 0.50 (cluster = a hypothesis
  cluster above; its selection stability = its group stability; the AUC condition is H-02's recorded reading
  verdict "continue"). Survivors earn their own
  trial (rows 6-9); if no cluster survives, rows 6-9 are cancelled and their budget returns to reserve.

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
- **Owed count (count-owed-events run 38931951849 on `main` @ `eafc6ce`, 2026-10-16; events only, no label, return
  or outcome; `count_h06` is still in quant review, BACKLOG 48):** 1,105 macro-gated signals (711 long / 394 short).
  The gate removed 1,049 of H-01's signals (0 for a missing macro value, 1,049 disagreeing; 1,105 + 1,049 = 2,154, the
  H-01 recount above). **One-at-a-time lower bound 107, floor in force 150: below the floor.** By the withdrawal rule
  above, H-06 is withdrawn once the quant reviewer confirms the count: the row is not written, `withdrawn:` is set in
  the spec, and `reservation` and `research.reserved_trials_quarter` 2027Q1 become 8. The table above then stays as
  the record of what would have run. The same run gives H-01's lower bound as 152 (154 above). That gap is for the
  H-01 recount paste (due 2026-11-01).

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

### tsmom 4h with swap (continuity)
- Config: `variants=[{"timeframe": "4h", "max_bars": 12}]`, as refused on 2026-10-09.
- Reading rule: if net mean R <= 0, the 4h tsmom horizon is retired (H-01 supersedes it).

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

## Q1 readiness: what must exist before 2027-01-01 (research director, 2026-10-11)

Checked on 2026-10-11 against the repo, `config/settings.yaml` and the shared registry (release `research-v1`: 20
Q4 2026 rows, **no `preregistered` row and no slot yet**). Releases present: `data-v1`, `research-v1`. **`macro-v1`,
`calendar-v1` and `costs-v1` do not exist yet:** `data-macro` and `data-us-calendar` have never run, and the VPS has
not published costs. There is also no command or workflow that writes a queued row or a slot into the shared
registry. `TrialRegistry.preregister` and `reserve_slot` exist only as library calls.

**1. Queued registry rows** (each written with `TrialRegistry.preregister(..., queue=True, target_quarter="2027Q1")`
and this file's freeze commit in the rationale, stamped by the registry's clock, then synced to `research-v1`)

| Row | Config (exact) | `rule_id` | Feature version | Owner | Date |
|---|---|---|---|---|---|
| H-02 | `--discover --families cluster`, `--specialist tsmom --variants '[{"timeframe": "4h", "max_bars": 12}]'`, the pinned `DiscoveryConfig` dump above (top_k 39, permutation confirmation, freq/stability 0.6, group_top_k 3, survivor_unit feature, H02_CLUSTERS), label 4h TB 3.0/1.5/12, macro on, bars range 2010-01-01 .. 2025-09-30 written into the config | none (read by the discovery verdict) | `f-51ac5ebabb` (default + macro drivers v5) | strategy researcher writes the config; quant reviewer checks it | config 2026-12-11; row 2026-12-18 .. 12-23 |
| H-01 | `tsmom`, `variants ["slow"]`, agent `tsmom-g0-c12c9afb24` | **`H-01`** | `f-079deacc61` | strategy researcher | row 2026-12-18 .. 12-23 |
| H-06 | frozen macro-gate preset (above), only if it survives its withdrawal rule | none: evaluator cites the rule | `f-51ac5ebabb` | strategy researcher (spec), implementer (preset), quant reviewer | spec done 2026-10-16 (run-input table under H-06); preset 2026-12-04; row 2026-12-18 .. 12-23 or withdrawn (count 2026-10-16: lower bound 107 < 150) |
| H-04 | `asia_drift`, `variants [{}]`, agent `asia_drift-g0-98dc13a518` | **`H-04`** | `f-079deacc61` | strategy researcher | row 2026-12-18 .. 12-23 |
| tsmom 4h continuity | `tsmom`, `variants [{"timeframe": "4h", "max_bars": 12}]` | **`C4H`** | `f-079deacc61` | strategy researcher | row 2026-12-18 .. 12-23 |

**2. Reserved slot rows.** Four survivor slots on H-02's queued row, written after it:
`reserve_slot(h02_row, "survivor", k, <derivation rule as amended 2026-10-11>, "2027Q1",
survivor_pool=["H-03", "H-05", "H-07", "H-08", "H-10"])`, k = 1..4, slot rule `{"min_stability": 0.6, "min_auc":
0.53, "min_auc_lower_bound": 0.5}`. **No follow-up and no holdout slots for Q1** (decided here). Q1 follow-ups are
funded from Q2's six-slot follow-up reserve, and any holdout scoring comes from the open budget under the E-5 cap.
`research.reserved_trials_quarter` = `{"2027Q1": 9}` (5 queued + 4 slots), or **8** if H-06 is withdrawn.
Owner: strategy researcher (rows), implementer (settings if 8). Date: with the rows, 2026-12-18 .. 12-23.

**3. Owed counts** (events only, no outcome). count-owed-events (in review, commit `9343f2f`) is merged to `main`
and run there, and its table is pasted here with the run's commit:

| Count | Needs | Owner | Date |
|---|---|---|---|
| H-04 entries, entries dropped by the 7-hour sample rule (with dates), days without a decision bar, ATR warm-up, holdout-crossing entry; calendar upper bound 4,087 | `data-v1` | release manager merges; strategy researcher dispatches and pastes | merge 2026-10-25, paste 2026-11-01 |
| H-01 recount (signals by side, one-at-a-time lower bound) against 2,154 / 154; any difference explained here | `data-v1` | as above | 2026-11-01 |
| H-06 signals by side and one-at-a-time lower bound, produced by count-owed-events (`count_h06`: the `slow_macro` preset's own `candidates_in_context` on data-v1 + `macro-v1`, with the H-01 signals the gate removes and the lower bound against the daily floor in force, `below_floor`); "not counted" until `macro-v1` exists | `macro-v1`, H-06 preset (merged with the count, BACKLOG 48) | strategy researcher dispatches and pastes | 2026-12-04 |
| Q2 counts from the same run (H-16, H-17, H-18, H-14) | `calendar-v1`, `macro-v1`; Q2 pins P-1 .. P-10 first | as above | not needed for Q1 |

**4. Freeze checklist, with an owner and a date for each item** (the boxes below are ticked only when done)

| Item | Owner | Date |
|---|---|---|
| Ruling A (daily event floor; recommendation 150, retire-only), B (budget 20, keep), C (consensus feed, decline for now), D (other instruments, out of scope), and the conservative K_eff default: recorded here and `research.screen_min_events_daily` committed | owner, via the program director | **done 2026-10-11** (owner approved; floor committed) |
| `macro-v1` published (`data-macro` dispatched on `main`) | data engineer, via the program director | 2026-10-18 |
| `calendar-v1` published (`data-us-calendar` on `main`; needed by Q2, not by Q1) | data engineer | 2026-10-18 |
| `costs-v1` published from the VPS, or "priors accepted" written here by the research director | owner and SRE (VPS step, BACKLOG 1); research director records the fallback | 2026-12-11 / fallback 2026-12-14 |
| A pre-registration writer: a command plus a `workflow_dispatch` job that writes queued rows and slots into the shared registry under its lock and syncs `research-v1` (BACKLOG 47; built 2026-10-11 as `scripts/preregister_quarter.py` + the `preregister-quarter` workflow, from `preregistration-2027Q1.yaml`, which must be updated with this file: H-02's config, H-06 or its withdrawal, the freeze commit) | implementer; quant and security reviewers | 2026-12-04 |
| H-06 preset merged and counted, or withdrawn by its rule (BACKLOG 48; preset and count built 2026-10-11, in review; the count waits for `macro-v1`) | strategy researcher, implementer | 2026-12-04 |
| H-10: no `jump_news` for Q1 (decided here). H-10 reads jumps only, which is inconclusive for H-10 by its rule, so the feature version stays put | research director | done 2026-10-11 |
| Feature-version line final (`f-079deacc61`; macro `f-51ac5ebabb`); any later bump amends it first | quant reviewer | 2026-12-11 |
| Freeze commit of this file; `<freeze commit>` filled in every run-input table | research director | 2026-12-14 |
| Rows and slots written, `reserved_trials_quarter` matches, `research-v1` synced | strategy researcher | 2026-12-18 .. 12-23 |
| Registry and this file agree (row count, configs, `rule_id`s, slot rule, reservation) | evaluator (independent) | 2026-12-28 |
| Nothing from this queue runs before 2027-01-01. Run order from 2027-01-01: H-02, H-01, H-06 (after H-01, which its rule compares against), H-04, C4H | research director | 2027-01-01 |

## Freeze checklist (by 2027-01-01)
- [x] Owner rulings A-D, the K_eff default and the costs-v1 rule recorded here (2026-10-11; `screen_min_events_daily: 150` committed)
- [ ] `costs-v1` published from the VPS by 2026-12-11, or "priors accepted" written here on 2026-12-14 (owner ruling 2026-10-11)
- [ ] `macro-v1` release present
- [x] `research_pass.py --discover` merged and tested (`scripts/research_pass.py --discover`, `tests/test_discovery.py`; checked 2026-10-11)
- [ ] count-owed-events merged and run on `main` (data-v1); its counts pasted here (H-04 entries and the 7h-rule drops;
  H-01 recount)
- [ ] H-06 preset merged and counted, or H-06 withdrawn and the reservation set to 8 (planned: 8 unless the count is >= 150)
- [ ] pre-registration writer merged (BACKLOG 47); the 4 survivor slots written on H-02's row
- [ ] each trial's `preregistered` registry row written with this file's commit id
