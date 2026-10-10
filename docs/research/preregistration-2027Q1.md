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
- DSR trial count: 20 (Q4 2026) + Q1 trials run so far + K_eff of H-02. K_eff is the number of units a survivor
  was picked from: the features screened (about 300) when survivors go forward as individual features (the default,
  `survivor_unit=feature`), the groups screened (about 10) only when whole groups go forward (`survivor_unit=group`).
  The unit is fixed in the pre-registration row before the run.
- **Conservative default, pending the owner's confirmation:** H-02's K_eff is added to the DSR trial count of EVERY later
  trial, survivor or not (`registry.n_trials_effective`, used by `research_pass.py` for every trial), not only to trials
  of an H-02 survivor. This over-deflates trials unrelated to H-02 (their DSR is biased down, never up); it is kept
  because a per-trial "is this a survivor?" link is easy to get wrong and an error there would bias DSR up.
- **Extra promotion rule for discovery survivors:** any H-02 survivor must, besides its own pre-registered trial and the
  design's gates, also pass the holdout rule (one scoring of 2025-10-01 .. 2026-09-30, `--score-holdout`; enforced in
  code: `TrialRegistry.passed_gates` requires it for configs tagged `from_discovery`) before it
  can be promoted.

## Order and budget (13 planned of 20; 7 reserve)

**Reserved in code:** `research.reserved_trials_quarter: 13` (config/settings.yaml) holds these 13 trials before the
research director or the monthly label grid can spend any of the quarter's 20 (`research/director.py`
`reserved_trials`). Each trial run against its `preregistered` registry row uses one up; a queued preregistered row is
always covered. The director and the grid get at most 20 - used - reserved (7 if nothing else runs first).

| # | ID | Trial | Depends on | Trials |
|---|---|---|---|---|
| 1 | H-13 | Volatility-forecast cost filter, applied as a reporting overlay to every trial below (net R with / without) | none | 0 |
| 2 | H-02 | Feature discovery (one trial) | tooling `research_pass.py --discover` | 1 |
| 3 | H-01 | Slow TSMOM | owner ruling A | 1 |
| 4 | H-06 | Macro-conditioned slow TSMOM | H-01 run; macro release | 1 |
| 5 | H-04 | Asia-session drift | none | 1 |
| 6-9 | H-03, H-05, H-07, H-08, H-10 | Each only if its features survive H-02 (stability >= 0.6) | H-02 | 1 each, at most 4 |
| 10 | tsmom 4h, max_bars 12, with swap | The trial refused by the budget guard on 2026-10-09, kept for continuity | none | 1 |
| - | reserve | follow-ups decided by these rules only | | 7 |

## Trials and reading rules

### H-02 Feature discovery
- Config: label = 4h triple barrier, target 3.0 ATR / stop 1.5 ATR / 12 bars (tsmom 4h defaults), candidates = every 4h
  bar (no rule), side from the sign of the 20-day vol-scaled return; features = every registered family (incl.
  trader-toolkit, survey and macro), grouped by family (`--families`); survivors go forward as features, so K_eff =
  features screened (group stability is reported by summed member importance per subsample, top-k over groups).
- Method: stability selection inside each training fold only (50 subsamples x 50%), top-40 by gain; permutation
  importance on an inner split as a tie-break.
- Reading rule (decided now): a feature cluster **survives** if its selection stability >= 0.6 across folds AND the
  walk-forward on the selected set has OOF AUC >= 0.53 with the lower 95% bound > 0.50. Survivors earn their own
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

### H-04 Asia-session drift
- Config: long at 00:00 UTC, exit 07:00 UTC (flat before London), stop 1.5 ATR(1h), every trading day, Data before
  2025-10 only (press figures for H1 2026 fall in the holdout).
- Reading rule: **continue** if gross t >= 2.0 on >= 1,000 events AND net mean R > 0. Expected thin: <= 0.03% per
  session against ~0.01% round trip, so a pass needs the measured cost table.
- Implemented as the specialist `asia_drift` (`goldbot/specialists/asia_drift.py`, `tests/test_asia_drift.py`), agent
  id `asia_drift-g0-98dc13a518`. Research only: `Specialist.screening` keeps it out of the default population founders
  and the pooled 1h model until a trial passes; it is active (not in `research.retired_families`).
- **Frozen parameters (written 2026-10-11, BEFORE any H-04 outcome was seen; no label, return or outcome was
  computed on real data).** Each is enforced in code or stated as the reading of the run:
  - *Entry:* long only, at 00:00 UTC: the decision bar is the 1h bar closing at 00:00 UTC (23:00-00:00), entry at its
    close on the ask (the open of the first 1h bar at or after 00:00 UTC). No entry when that bar is missing, when the
    session calendar is closed at 00:00 UTC (`DEFAULT_SESSIONS.is_open`: weekend, daily break on the Europe/Athens
    server clock), or on 25 December / 1 January (UTC dates). So entries are Monday to Friday, at most one a day.
  - *Exit:* time barrier at the close of the 7th 1h bar after entry (`max_bars` 6), 07:00 UTC, in both DST regimes
    (London opens 08:00 UTC in winter, 07:00 UTC in summer: flat before or at the open). A plain barrier, no exit
    policy, so labels, shadow book and live count the same bars (`test_shadow_book_reproduces_the_labels_time_barrier`).
    A data gap inside the window lengthens the hold in calendar time (bars are counted, as everywhere else).
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
  - *Walk-forward (only if the screen passes):* the 1h window (train 36 / test 6 / step 6 months, purge 5 d, embargo
    2 d), the declared 12-feature list (`AsiaDriftSpecialist.model_features`). The reading rule above is read on the
    rule-only screen alone; a "continue" means only that model research on H-04 may be pre-registered.
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
  weekday 25 Dec / 1 Jan), about 260 a year. The data count (days whose 23:00 UTC bar exists, less the ATR warm-up)
  can only be lower; to be recorded here before the freeze by a run with data-v1 present.

### tsmom 4h with swap (continuity)
- Config: `variants=[{"timeframe": "4h", "max_bars": 12}]`, as refused on 2026-10-09.
- Reading rule: if net mean R <= 0, the 4h tsmom horizon is retired (H-01 supersedes it).

## Needs the owner (before freezing)
- **A. Event floor for daily-signal families (owner's decision; not yet decided).** The P4 screen needs >= 1,000
  events; slow TSMOM produces 154-2,154 in 15.75 years (one position at a time; recount after the stub fix, see
  H-01). **Recommendation (quant review):** floor 150 rule-only events, gross t >= 2.0 unchanged, set in code before
  the run (`research.screen_min_events_daily: 150`); H-01 can retire, not promote; < 150 = inconclusive. Until you
  decide, the code keeps 1,000 for every rule (`screen_min_events_daily: null`), under which H-01 can only be
  inconclusive (fewer events than the floor is inconclusive whatever the sign or t).
  - `research.screen_min_events_daily` must be committed in settings before the run.
- **B. Trial budget.** 13 planned + 7 reserve fits the default 20; raising it is yours to decide.
- **C. Paid economic-calendar consensus feed** (H-11 needs surprises); otherwise H-11 stays blocked.
- **D. Other instruments** (large-tick futures where trend still works) are out of scope unless you decide otherwise.

## Freeze checklist (by 2027-01-01)
- [ ] Owner rulings A-D recorded here
- [ ] `costs-v1` measured table published from the VPS (or the priors accepted and stated)
- [ ] `macro-v1` release present
- [ ] `research_pass.py --discover` merged and tested
- [ ] each trial's `preregistered` registry row written with this file's commit id
