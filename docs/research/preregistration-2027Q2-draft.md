# Pre-registration: Q2 2027 research trials (DRAFT, not frozen: freeze by 2027-03-31)

Written 2026-10-10 by the research director, base `1bcfe1c`, before any of these configurations has been run on data
and before any Q1 2027 result exists. Agenda and ranking: `research-plan.md`. Hypothesis rows: `hypotheses.md` A
and A2. Evidence: `opportunity-days.md` ("OD"), `xauusd-trader-playbook.md` ("playbook"), `state-of-the-art.md`.

**This is a draft.** No registry row is written, no trial is reserved in code, no budget is spent, the holdout
(2025-10-01 .. 2026-09-30) is not read, and the Q1 2027 pre-registration is not changed. Every rule below may still be
amended before the freeze; once a trial's `preregistered` row is written (with this file's freeze commit), its rule
is fixed and a change after seeing data makes it exploratory.

**Sync with Q1 (research director, 2026-10-17).** H-16, H-17 V0/V1 and H-14 (with ≤ 2 survivors) are
pre-registered against Q1 2027's open budget (`preregistration-2027Q1.md`, "Q1 open-budget round", rows O-1 to O-6).
Each runs there only if its inputs land by its date (2027-01-29 / 2027-02-12) and it runs by 2027-03-26. A row that
runs in Q1 is removed from this draft at its next revision, and its trials join this quarter's free reserve. A row
that does not run in Q1 stays here unchanged. The Q1 texts add three things for those rows, which this draft adopts
at its freeze:
- the ADR 0022 day-clustered trade t, with weekly-block AUC bounds;
- the top-tercile cut from inner cross-fitted p (E-2);
- the day-summed DSR check for H-14 survivors (E-6).

H-01 can no longer read "continue" (strict floor), so follow-up items 1-2 below cannot arise. H-02 moved to
`survivor_unit: group` (Q-H02U, 2026-10-17).

## Fixed for every trial (as Q1 unless stated)

- **Data:** Dukascopy 1m XAUUSD 2010-01 .. 2025-09 (release `data-v1`); `macro-v1` (first releases, `asof_join` on
  `available_utc`) only where stated; the historical tier-1 schedule **E-1** (below) where stated. The holdout is
  never loaded; every label that has not exited before 2025-10-01 is dropped (`pipeline.prepare`).
- **Costs (pin P-5, adopted 2026-10-11 after the quant review):** the cost source is **pinned at the freeze** in
  every config: a named `costs-v1` version if one exists at the freeze, otherwise the settings priors. It is never
  switched between the freeze and the run, and gates (H-14's cost gate) and labels use the same pinned source. If
  `costs-v1` exists at the freeze, it is pinned and every owed count that depends on costs is recounted with it
  before the freeze. Otherwise the priors are pinned and any later `costs-v1` result is a reported sensitivity only.
  Priors: `extra_cost_usd = 0.37 $/oz` round trip beyond the spread (2 × slippage 0.15 + 2 × commission 3.5 USD per lot side
  / 100 oz, `execution.costs.settings_extra_cost_usd`), with the spread paid in the labels (ask in, bid out). The
  report states which. **Swap:** every Q2 trading rule is flat by 16:30 New York, before the 17:00 New York
  rollover, so `swap_nights` is 0 by construction; each specialist carries a test that no trade is held through the
  server rollover in either DST regime (as `asia_drift`).
- **Statistics (as frozen for H-01):** t = mean R / sd(R, ddof 1) × √n; a positive year has ≥ 10 trades and positive
  mean net R; net tests use rule-only net R over every candidate, one position at a time; any screen below its event
  floor is `inconclusive (event floor)` whatever its sign or t, charged and recorded, and neither retires nor
  continues the hypothesis.
- **Event floor:** `research.screen_min_events` (1,000) for every intraday rule. The daily override does not apply.
- **DSR trial count:** `registry.n_trials_effective` + 1 at run time = 20 (Q4 2026) + every Q1 and Q2 trial recorded
  so far + H-02's K_eff (Q1, conservative default: added to every later trial) + H-14's K_eff once it has run.
- **Feature versions:** `f-079deacc61` (sessions v2, DST-aware) for 15m rules and discovery; `f-51ac5ebabb` (was `f-ac2be79efe` with `macro_drivers` v4, whose stub rule read the data's bar spacing, `f-e7d2dc11e1` with macro drivers v3 and `f-f999b359e2` before v3 added the VRP spread; v4 drops stub days from its HAR-RV; v5 reads each bar's declared length, amended 2026-10-11) where
  the macro drivers are used (no Q2 trial since H-16 pins `day_type` alone, below). The opt-in `day_type` family (BACKLOG 30a) is not in the default set, so it
  leaves `f-079deacc61` and `f-51ac5ebabb` unchanged. A feature set that includes it has its own version (pinned in
  `tests/test_day_type_features.py`): **`f-1eada57050`** (default + `day_type` v1); the default + macro
  drivers + `day_type` hash moves with every macro-driver version, so it is worked out at the freeze (no Q2 trial uses
  it: H-16 pins `day_type` alone). Each row that uses `day_type` also records its inputs (`day_type`: family version,
  GVZ present, calendar present, refit schedule, minimum fit rows). `research_pass --day-type` refuses to run without
  `--us-calendar`, with `--discover` or `--cpcv`, or with `--from-year` after 2010. A run on another version is
  recorded with a `deviation` (`TrialRegistry.record`), per ADR 0006.
- **Opportunity-day report (BACKLOG 33)** is printed for every trade-producing trial: £/day distribution re-sized to
  a £1,000 GBP account (0.01 lot or refused), opportunity days captured (ADR 0004 §6 definition and OD §3.4's stricter
  one), stand-aside accuracy, drawdown. **Reported, never part of a reading rule.**
- **£1,000-tradeable share** (diagnostic): the share of entries the £1,000 account could take today: stop ÷ entry
  price ≤ 0.32% (stop ≤ $13.30 at $4,150, ADR 0004 §2), so historical trades are judged at today's price level.
- **Overlays (0 trials):** H-13 (net R with and without the volatility-forecast cost filter) and H-19 (net R by
  HAR-score band low / mid / high, cuts at the 33rd and 80th percentiles of the current fit's in-sample fitted
  values, and the effect of skipping the low band) on every trade-producing trial.

## Owner rulings that bind this draft (decided 2026-10-11; source: owner approved the research director's recommendations, 2026-10-11)

- Trial budget 20 a quarter, unchanged; the plan below fits it.
- No consensus feed: H-11 stays out of Q2.
- No other instruments.
- The conservative K_eff default is confirmed: H-02's (Q1) and H-14's K_eff are added to every later trial's DSR
  count.
- The daily-signal floor (150, retire-only) applies to no Q2 rule: every Q2 rule is intraday and keeps
  `research.screen_min_events` (1,000).
- `costs-v1`: published by 2026-12-11 if the demo terminal runs, otherwise "priors accepted" for Q1 on 2026-12-14.
  Q2 pins its own cost source at its freeze (P-5).
- Still open: O-1 (H-18's event floor). It was not among the recommendations the owner approved.

## Order and budget (20 trials)

| # | ID | Trial | Trials | Depends on | Why it earns the slot |
|---|---|---|---|---|---|
| 1 | H-13, H-19 | Overlays on every trial below | 0 | 30a (HAR column) | Costs decide most outcomes at 15m; standing aside on quiet days is the cheapest drawdown control |
| 2 | H-16 | Opportunity score vs HAR-only range forecast | 1 | 30a, E-1, E-4, `macro-v1` | The one forecastable thing about gold's day (R evidence); decides which range inputs the allocator uses (30b) |
| 3 | H-17 V0 | Trend-day continuation, fixed target | 1 | **quant ruling Q-1**, 30a, 31, E-1, E-3; event count ≥ 1,000 | Only Q2 idea that can put a 15m trade on the £1,000 demo and aims at the £50-day mechanism; V0 is the control for V1 |
| 4 | H-17 V1 | Trend-day continuation, scale-and-run | 1 | as #3, plus 32 before any shadow/live use | Tests whether a runner exit pays on range-selected days, which is the best-day KPI's only route at £1,000 |
| 5 | H-14 | 15m scalping discovery, cost-gated, survivor unit = group | 1 | E-1, E-2, 31; **quant ruling Q-3** | Owner request; screens every 15m mechanism (sweeps, levels, session flow, jumps) for one trial instead of one each |
| 6–7 | H-14 survivors | One model trial per surviving group | ≤ 2 | H-14 verdict | Only if a group survives; otherwise both slots return to reserve |
| 8 | H-18 | Post-blackout event continuation | 1 | **owner ruling O-1** (event floor) or tier-2 pooling (BACKLOG 18, D-4), E-1, E-3, 31 | Event days are opportunity days (R); needs no consensus feed, unlike H-11. Withdrawn before freeze if no ruling |
| — | Q1 follow-ups | Reserved for Q1 survivors (below) | 6 | Q1 verdicts | Survivors seed the next generation before new ideas |
| — | Free reserve | Not spent unless a rule below calls for it | 7 | — | Holdout scorings of any Q2 passer, confirmations |

Planned 4 (#2–#5) + conditional ≤ 3 (#6–#8) + Q1 follow-ups 6 + free reserve 7 = 20.

**Q1 follow-up reserve (6), in priority order.** Each is pre-registered only after the Q1 evaluator verdict it
depends on, and before it runs:
1. H-01 **continue**: pre-registered model research on slow TSMOM that meets the unchanged design gates (1).
2. H-01 **continue**: exit variant C-5 (breakeven or wider trail) for the slow-TSMOM survivor (1).
3. H-04 **continue**: model research with a time-exit meta-label (1).
4. H-04 **continue**: H-24, the West-hours leg (short 08:30–16:00 New York, stop 1.5 ATR(1h), rule-only screen, read
   by H-04's rule: gross t ≥ 2.0 on ≥ 1,000 events AND net mean R > 0) (1; added 2026-10-11 by the session-coverage
   review, `hypotheses.md` A4 G-2, before any H-04 outcome exists).
5. H-02 survivors that Q1's rows 6–9 could not fund (≤ 2).
6. A holdout scoring (`--score-holdout`) of any Q1 gate passer (1; a holdout scoring is a charged trial).
The list is in priority order and stops at six live items; anything past the sixth waits for the free reserve, which
funds it only by a written note from the research director before its pre-registration.
If every Q1 hypothesis fails or is inconclusive, the six slots join the free reserve by a written note from the
research director after the last Q1 verdict.

**Order of runs.** H-16 first (its verdict decides 30b, not H-17). H-17 V0 and V1 together. H-14 at any time; its
survivor trials after its verdict. H-18 last, only if ruled.

**Reservation in code (design note for the freeze).** `research.reserved_trials_quarter` holds slots only for queued
`preregistered` rows written before 2027-04-01 (`TrialRegistry.preregister(..., queue=True,
target_quarter="2027Q2")`). Rows written once Q2 has started (H-14 survivors, most Q1 follow-ups) cannot be queued,
cannot draw on the reservation, and must fit in 20 − used − reserved (`TrialRegistry.check_budget_reserved`,
`director.reserved_trials`: reserved = max(setting − queued rows run, pending)). A setting above the number of queued
rows therefore locks the excess for the whole quarter. **Updated 2026-10-10 (ADR 0007):** in-quarter rows can now be
protected by **contingent slots** written before 2027-04-01 (`TrialRegistry.reserve_slot`): H-14 survivor slots
k = 1, 2 on H-14's queued row, and holdout slots where a rule calls for one; a slot is held only while its condition
can still be met (reserved = max(setting − run − withdrawn − expired slots, pending, 0)) and is used only by the
k-th survivor's own pre-registration (`slot_ref`) running exactly the derived configuration (H-14's family, spec
and label, the group's recorded members), and only while H-14 reads continue under its rule. Q1 follow-ups depend on
Q1 verdicts and are written as `followup` slots on their Q1 parent rows where the parent is known before the freeze;
each fixes its family and allowed configurations, and a rule-only parent's follow-up goes live only on the
evaluator's recorded verdict "continue" (`TrialRegistry.record_verdict`). The setting is keyed by quarter
(`{"2027Q2": n}`), and the E-5 cap (`research.open_budget_preregistered_only`) can be extended to 2027Q2. **Recommendation:** set the Q2 value to
queued rows + slots at freeze; this answers E-5 for the in-quarter allocations. Q1's setting was corrected the same
way (13 → 9).

## Trials and reading rules

### H-16 Opportunity score (range forecast; no trades)

- **Unit:** one row per stub-free `day_type` opportunity day D (22:00 UTC rollover; a day spanning under 12 hours of
  bars is a stub), 2010-01-04 .. 2025-09-30. Daily inputs are computed from the first bar of 2010
  (`research_pass --day-type` refuses a later `--from-year`). The bars are cut at `research.holdout_from`
  (2025-10-01) before E-4 builds any feature, so no holdout bar is loaded.
- **First out-of-sample month (frozen rule):** the first calendar month M whose baseline fit at M's refit instant
  (below) has ≥ 500 usable rows. Every later month to 2025-09 is out of sample. By the calendar this is about **2012-04**: about
  44 days of warm-up (20-day ATR, its one-day lag, 22-day regressors and their one-day lag) plus 500 rows. The exact
  month is counted from the data before the freeze and written here; it is not chosen after seeing any result.
- **Target:** y_D = ln(W_D ÷ A_D). W_D = high − low of the 15m mid bars from 08:00 London to 16:30 New York on D
  (DST-aware local clocks, the window the Q2 rules trade; bars starting at or after 08:00 Europe/London and before
  16:30 America/New_York). A_D = mean of the last 20 true ranges of stub-free `day_type` days (mid, 22:00 UTC
  rollover) through D−1, the value visible at 06:30 UTC on D. This ATR(1d) is not the 1d-bar ATR of the decision
  frame (13:30 New York settlement days, Wilder smoothing). Code: `goldbot/features/day_type.py` `daily_inputs`.
- **Baseline (HAR-only):** OLS of y_D on ln of the mean of w_d = W_d ÷ A_d over the previous 1, 5 and 22 trading
  days, plus an intercept.
- **Full score:** the baseline plus, all visible at 06:30 UTC on D:
  - ln(GVZ ÷ YZ20 annualised), GVZ by `asof_join` on `available_utc` (D−2's close at the London open; OD 1.8). A day
    is dropped from **both** models' evaluation (count reported) when the latest known GVZ `value_date` is more than
    5 US business days (`US_BDAY`, federal holidays skipped) before D (`opp_gvz_stale` = 1);
  - number of distinct tier-1 releases scheduled on D, as known at 06:30 UTC (E-1; scheduled times only; an FOMC
    statement and its press conference count as one release, `day_type.RELEASE_OF`);
  - ln(prior stub-free daily range ÷ A_D); NR4, NR7 and inside-day flags on stub-free daily bars (H-20);
  - |reopen gap| ÷ A_D at the last reopen before 06:30 UTC (the Sunday reopen on Mondays, else the daily reopen).
  - No direction term, no `side`, nothing after 06:30 UTC on D.
- **Fitting:** OLS refitted once a month on every earlier row whose target day closed at least 1 day before the
  refit instant (expanding, ≥ 500 rows, 1-day purge).
  - **Refit instant for month M:** the close (22:00 UTC) of the last weekday opportunity day before M's first day.
    When M starts at a weekend this is Friday's close, so Monday gets M's fit.
  - The rule is calendar-only, so it is known in advance. "The last stub-free day" would not be: whether that day
    becomes a stub is known only at its own close.
  - A forecast emitted at day k's close uses the latest fit whose refit instant is at or before that close. The
    fitted-value quantiles 0.33, 0.5 and 0.8 of each fit are stored with it (`HarFit.fitted_quantiles`) and are
    emitted per row as `opp_har_cut33/50/80` and `opp_har_band` for H-17 and H-19.
  - Code: `day_type.fit_har` (any column list, so the full model shares it), `monthly_refits` and `har_forecast`.
  - **Quant ruling Q-2 (2026-10-11): approved.** The models have 4 and 11 coefficients, 500 days is ample, and a
    monthly refit gives about 3 more out-of-sample years (from 2012 instead of 2015) than the default 1d walk-forward
    (60/12/12 expanding). H-17 uses the same scheme.
  - **Frozen first out-of-sample month:** the first calendar month whose baseline fit has ≥ 500 prior evaluable rows,
    counted from the calendar and data gaps only (no target, no outcome) and written here as a month before the
    freeze (expected about 2012-04). No other refit schedule or window is tried under H-16.
- **Reading rule** (from `hypotheses.md` A2, unchanged): **continue** if, pooled out of sample, Spearman
  ρ(full, y) − ρ(HAR, y) ≥ 0.05, AND the mean W/A of the full score's top quintile is ≥ 1.5 × its bottom quintile's.
  **Fail:** the day factor (30b) uses HAR alone, and GVZ, events and compression are retired as range inputs.
  Reported, not in the rule: the 20-day block-bootstrap 95% interval of the ρ difference; each input's drop-one
  contribution; the London+60 and NY+60 refreshes with the noise ratio (H-21) as diagnostics.
- **Counting:** one trial (counted for honesty though it tests range, not returns). It produces no trades, so no
  DSR, no event floor, and no opportunity-day report.
- **Events owed from data before the freeze, frozen in the config:** the number of evaluable days (expected ≈ 3,380
  out of sample by the calendar: 13.5 years from about 2012-04 × ~250; minus stale-GVZ days and data gaps). It is
  counted from the calendar and data gaps before the freeze and written here and into the pre-registered config as
  `expected_eval_days`, with the month as `first_oos_month` (both in the config hash). A run with fewer than 90% of it
  (`h16.MIN_EVAL_SHARE`) is refused before any statistic is computed, and so is a NaN statistic; nothing is recorded
  either way. **Frozen values: owed (not counted yet; no data was read for this draft).**
  **Run count-owed-events and paste the counts before the freeze** (Actions -> count-owed-events;
  `scripts/count_owed_events.py` counts `first_oos_month` and `expected_eval_days` with `h16.first_oos_month` and the
  `evaluate_h16` evaluation-set masks on placebo prices: it reads the 15m bar timestamps, the GVZ dates and the
  calendar rows, never a real price or the target). Paste both values, the drop counts and the run's commit here and
  into the pre-registered config.
- **Feature version (amended 2026-10-11, quant review of E-4, M1):** H-16 pins what it reads,
  `feature_version(["day_type"])` (`h16.h16_feature_version()`; `f-52c853166b` for `day_type` v1 today), with the GVZ
  series (`GVZCLS`) named in the config. It is worked out at the freeze and checked at run time: the pre-registration
  row carries the version worked out at the freeze, and `run_trial` works it out again and refuses before reading
  anything when it differs (no read followed by a recorded deviation). `tests/test_day_type_golden.py` ties
  `day_type`'s outputs to `day_type.VERSION`, so a change to the columns without a version bump fails CI. The combined
  default + macro + `day_type` hash no longer applies to H-16; `day_type`'s inputs (GVZ and calendar both present) are
  still recorded on the row.
- **Code (E-4, built 2026-10-11; quant review: merge after fixes, fixes applied):** `goldbot/research/h16.py`, run as
  `research_pass.py --h16 --macro macro/ --us-calendar calendar/` (refused with `--discover`, `--cpcv`,
  `--score-holdout`, `--pooled`, `--day-type`, `--positioning`, `--us-calendar-features`, `--variants`, a
  `--from-year` after 2010, or without GVZ or the calendar).
  - H-16 runs once, against its pre-registration. Under the registry lock and before anything is read, the run is
    refused when any trial of `h16_opportunity_score` exists (whatever its config), when there is not
    exactly one unused pre-registration of the family, when its config is not
    `h16.h16_config(first_oos_month, expected_eval_days)` (500 rows), when its `code_hash` (sha1 of `h16.py`'s source,
    also on the result row) differs from the code's now, when its feature version differs from the one worked out at
    run time, or when the budget is spent. `gvz_series` in the config is informational: what GVZ means to the inputs
    is pinned by `tests/test_day_type_golden.py` and `day_type.VERSION`. The frozen month and day count are read from that config.
  - The 1m bars, the macro rows and the calendar rows are cut at `research.holdout_from` before anything is resampled
    or built. Each full-score input is taken as known at 06:30 UTC on D (`DECISION_UTC`).
  - Both models are fitted by `fit_har(regressors=...)` with the same monthly refits and purge. D's forecast uses the
    latest fit at or before the previous stub-free day's close, as `har_forecast` does, so the baseline is the
    published `opp_har_range_hat`.
  - When the rule on the data gives another first month than the frozen one, the run is refused before any forecast
    is read, and nothing is recorded.
  - The run records one trial (status `evaluated`, `results.kind` = `range_test`, linked to the pre-registration)
    holding both rho, the gain, the quintile means and ratio, n, the first OOS month, the dropped-day counts
    (GVZ-stale, no HAR forecast, no full forecast, and the last by missing input), the feature version and the
    verdict (continue/fail).
  - Reported only: the 20-day block-bootstrap interval (1,000 resamples, seed 0) and the drop-one contributions. The
    rule's boundaries are inclusive, with a 1e-12 tolerance for float representation only. Not built: the London+60
    / NY+60 refreshes (diagnostics).

### H-17 Trend-day continuation with a runner (V0 and V1: two trials)

- **Specialist:** `trend_day` (E-3), timeframe 15m, rule-only screening family like `asia_drift` (no model stage,
  never a founder until it passes, refused by the RiskGate until BACKLOG 32 lands).
- **Day condition (HAR-only, by design):** a day is *high* when H-16's **baseline** forecast for D (fitted exactly as
  in H-16) is at or above the median of the current fit's in-sample fitted values. The baseline, not the full score,
  is used so that H-17's configuration does not depend on H-16's verdict (one config hash, written before either
  runs) and needs no macro data. *This deviates from `hypotheses.md` A2 ("H-16's frozen model"); the full-score
  version is a Q3 neighbour if H-16 passes.* Median of fitted values, not a trailing-250-day rank, so the condition
  exists from the first out-of-sample month.
- **Reference open:** O_D = open of the 15m bar starting 08:00 London.
- **Noise boundary:** for a bar closing at London local time τ, B = mean over the previous 14 trading days of
  |close at τ − O| on those days (days without that bar are skipped; fewer than 14 available → no entry).
- **Entry:** the first 15m bar of D whose close falls in [09:00 London, 10:30 New York] (each in its own DST-aware
  clock) with |close − O_D| > 1.0 × B; side = sign(close − O_D); entry at that close. At most one trade a day.
  **First breach only, literal (pin P-2, adopted 2026-10-11 after the quant review):** the candidate is the day's
  first breaching bar inside the window with B defined. If its close falls in a closed session, if ATR(14) is not
  defined there, or if it is in a tier-1 blackout (below), the day has **no entry**: no later bar is taken. The E-3
  `trend_day` specialist follows the same rule. **Blackout, literal rule (program decision 2026-10-11 after the quant
  review, before any count or outcome):** the candidate is the day's *first* breaching bar only; if that bar's close
  lies inside a tier-1 blackout, **[−15, +30) min around the scheduled release, on the bar close** (E-1), the day has
  **no entry**: no later breaching bar of that day is taken. (The allocator zeroes every weight over [−30, +30] min
  around a tier-1 event, `allocator/rules.py`; that is a live sizing rule, not part of this trial's event definition.)
  count-owed-events reports the literal count (`high_events_strict`, the trial count) and, for transparency only, the
  count that would take the next breaching bar outside the blackout (`high_events_next_bar`). The blackout is built
  from the tier-1 rows of E-1 as known at that bar (status `scheduled`, `available_utc` before the bar). The closed-session
  and ATR cases follow the same literal rule (P-2, above). The low half (shadow) uses the identical rule.
- **Stop:** 1.5 × Wilder ATR(14) of the 15m mid bars at the entry bar's close, frozen; R = that distance.
- **V0 (fixed):** target 3.0 ATR; otherwise flat at the first bar close at or after 16:30 New York.
- **V1 (scale-and-run):** 50% off at 2.0 ATR; chandelier trail 2.5 ATR from the best price, armed at 1.5 ATR (ATR
  frozen at entry); cap target 10 ATR; flat at 16:30 New York. R per trade is the size-weighted blend of both parts.
  Both variants run through `policy_step` (BACKLOG 31: `flat_at_local`), so labels, shadow and live agree.
- **Events:** high days only. The same rule on low days is computed and reported as *low half (shadow)*; it is not
  part of the trial's event count and is needed by the reading rule.
- **Reading rule** (A2, unchanged in substance; written before any outcome):
  - V0 and V1 each **pass** their screen if gross t ≥ 2.0 on ≥ 1,000 events, net mean R > 0, and ≥ 3 positive net
    years including 2021 or 2022.
  - **Continue H-17 with the runner** if V1 passes, V1 net mean R ≥ V0 net mean R, AND the low half's gross mean R is
    below the high half's.
  - **Continue with fixed exits only** if V0 passes, the low half is below the high half, and V1 fails or is below V0
    (runner retired for this family).
  - **Retired as a repeat of R-02 / R-05** if the low half's gross mean R ≥ the high half's, whatever else holds.
  - **Quant ruling Q-1 (2026-10-10), binding on the rules above:** "the low half is below the high half" means a
    **Welch difference test, t ≥ 2.0**, on the high-half minus low-half gross mean R computed over **weekly blocks**
    (each ISO week's trades averaged into one value per half, so serially correlated days count once; unequal
    variances). t < 2.0 counts as "not below" and retires H-17 as a repeat of R-02 / R-05. **V1 is conditional on V0
    passing:** V1's screen is read only if V0 passes its screen and the Welch test; otherwise V1 is recorded as run
    and not read (it still costs its trial). **A fail is final:** a retirement under this rule joins the R-02 / R-05
    retirement records in `hypotheses.md` section B, and H-17 is not re-tested under another range condition, median
    cut or exit without the reinstatement path of those retired families.
  - Fewer than 1,000 events: inconclusive (event floor). "Continue" permits only pre-registered model research or
    shadow; it is not a promotion.
- **Events owed from data before the freeze (events only, no labels or returns):** high days with a qualifying
  breach, from H-16's first out-of-sample month (about 2012-04) to 2025-09.
  Calendar upper bound ≈ 1,690 (half of ≈ 3,380 days); the OD estimate is 1,000–1,500.
  **If the count is below 1,000, both H-17 rows are withdrawn before the freeze (no trial spent) and their slots go
  to the free reserve;** the median cut is not moved to raise the count.
  **Run count-owed-events and paste the counts before the freeze** (literal-rule high-half events `high_events_strict` = the trial count, low-half
  events, per year). Until `trend_day` (E-3) is merged the workflow counts from this written rule (labelled
  "spec-derived"); the count that decides the withdrawal is the one recounted with `trend_day`'s own code, and a
  difference from the spec-derived count is explained here.
- **Feature version:** `f-1eada57050` (default + `day_type` v1). Only `opp_har_range_hat` and `opp_har_cut50` are
  read: the "HAR column only" condition. The calendar enters through `ctx["us_calendar"]` for the blackout and
  day_type's tier-1 columns, which are not read. The `tier1_events`/`tier2_events` families stay off
  (`--us-calendar-features` not passed).
- **Retired-family overlap (quant ruling Q-1, needed before the freeze).** H-17 re-uses ideas of R-02 (session-open
  breakout, 15m) and R-05 (intraday momentum). Its claim to be new evidence is the ex-ante range condition and the
  runner exit (OD §5.1). If the quant reviewer rules it a retired-family re-test, H-17 can run only through the
  shared retired-family exploration trial, which the director's code allocates (`research/director.py`), and both
  rows are withdrawn. **Ruled 2026-10-10 (Q-1):** H-17 may run as new evidence only under the Welch difference test,
  V0-first and fail-is-final conditions written in its reading rule.

### H-14 15m scalping discovery (one trial)

- **Spec (E-2):** a 15m two-sided discovery candidate set, run as `research_pass.py --discover` on that spec.
  - Candidates: every 15m bar close from 08:00 London to 16:00 New York (DST-aware, both ends included), outside the
    tier-1 blackout (**[−15, +30) min around the scheduled release, on the bar close**, E-1; the allocator's live
    [−30, +30] min zero-weight window is a sizing rule, not part of the candidate set) and the first 15 min after the
    daily reopen, and only where the H-13 cost gate holds: the 3.0 ATR target ≥ 3 × the round-trip cost in USD, i.e.
    ATR(15m) ≥ the round trip (the daily-reopen exclusion lies outside this window in practice; kept for clarity).
  - **Cost source for the gate (P-5, adopted 2026-10-11; replaces the earlier "costs-v1 never read by the gate"):**
    the gate and the labels use the cost source pinned at the freeze ("Fixed for every trial"). If `costs-v1` exists
    at the freeze, its named version is pinned and the H-14 count is recounted with it. Otherwise the prior is pinned:
    round trip = the candidate 15m bar's own Dukascopy mean spread (`spread_mean`, USD/oz) + the settings extra cost
    (`execution.costs.settings_extra_cost_usd`, 0.37 $/oz today; written into the config and so into its hash), and
    `costs-v1` is a reported sensitivity only. Nothing switches between the count, the freeze and the run. **The run applies the extra cost frozen in the
    config** (0.37 $/oz today), whatever `settings.yaml` says at the run. A different settings value is only
    reported (count-owed-events records the check); it never changes the gate or the labels (quant review,
    2026-10-11).
  - Both sides are labelled for every candidate (two rows, `side` the 40th input), so the label depends on no primary
    (survey 4b). Both rows of a bar always fall in the same fold and are purged together.
  - Label: triple barrier, target 3.0 / stop 1.5 × Wilder ATR(14) of 15m mid bars at the candidate close, time
    barrier 16 bars, and flat at 16:30 New York (`flat_at_local`, BACKLOG 31).
- **Features:** every registered default family on `f-079deacc61` (trader toolkit, levels and sweeps, sessions v2,
  volatility estimators, structure), grouped by family (`--families`). No macro, positioning or `day_type` columns:
  slow context was screened at 4h by H-02, and keeping them out holds the group count down. If playbook D-5 ($100
  step, LBMA auction and 08:30 ET windows) lands before the freeze, this line is amended to include it.
- **Discovery config** (aligned with H-02's frozen row; any other difference is listed at the freeze):
  `{"n_subsamples": 50, "subsample_frac": 0.5, "survivor_unit": "group", "group_top_k": 3, "method": "lgbm",
  "freq_threshold": 0.6, "stability_threshold": 0.7, "permutation": true, "seed": 0}` (`group_top_k` explicit, as
  every group-unit config must be: the code refuses one without it). **Differences from H-02's pinned config (Q1,
  amended 2026-10-11, quant review F1):** stability_threshold 0.7 here vs 0.6 there (P-7), and `top_k` is unpinned
  here, so the code default of 10 would apply, vs 39 there. At the Q2 freeze the full `DiscoveryConfig.model_dump()`
  is written here, as for H-02, with `top_k` chosen before any count. Walk-forward: the 15m window (train 24 / test 3 /
  step 3 months, purge 2 days, embargo 1 day).
- **Survivor unit = group (quant ruling Q-3).** At 15m the question is which mechanism (sweep, level, session flow,
  jump) carries information, not which column, and K_eff = groups screened (about 10–15) instead of features (about
  300). Under the conservative default every later trial's DSR count carries this K_eff, so the feature unit would
  add ~300 to every trial for the rest of the program.
- **Q-3 conditions (ruled 2026-10-10):** every group has **≤ 39 columns** (a survivor group plus `side` must fit one
  model); the column → group map is **frozen by its hash** (`discovery.group_map_hash`, sha256 of the sorted map,
  written here at the freeze and recorded on the discovery row as `group_map_sha256`; a different hash at run time
  is a deviation read against this rule); a single group over 39 columns is refused before any look; otherwise the
  walk-forward and its verdict run and are recorded, and the surviving groups go forward **whole, never truncated**,
  in group-stability order while they fit within 39 columns, the rest recorded as `skipped_survivor_groups`
  (`discovery.survivor_selection`, amended 2026-10-11 after the quant re-review), because K_eff = groups screened is
  honest only if whole groups go forward. The report gives each survivor group's feature count beside it.
- **Reading rule:**
  - A group **survives** if it is selected in ≥ 70% of folds (frequency ≥ 0.6) AND the walk-forward on the selected
    set has out-of-fold AUC ≥ 0.53 with block-bootstrap lower 95% bound > 0.50 AND the pre-set rule (trade when p ≥
    the training fold's top-tercile cut, one position at a time, both sides) has net mean R > 0 on ≥ 1,000 events.
  - At most two surviving groups (highest stability first) earn rows 6–7.
  - **Stop** if AUC < 0.52: the 15m bulk features carry no directional information; they stay as cost and risk
    filters only, rows 6–7 are cancelled, and the plan records that 15m at £1,000 has no research path besides H-17
    and H-18. AUC 0.52–0.53: inconclusive, rows 6–7 cancelled.
- **Survivor trials (rows 6–7), pre-registered after H-14's verdict and before running:** a walk-forward model on
  H-14's label and candidates with inputs = the surviving group's features + `side` (≤ 40), the unchanged design
  gates (≥ 1,500 candidates, ≥ 60 per complete test fold, positive in ≥ 3 years incl. 2021 or 2022, DSR ≥ 0.95 on
  ≥ 200 model-filtered trades with the real count), tagged `from_discovery`, so promotion also needs one holdout
  scoring (from the free reserve).
- **Events owed from data before the freeze:** the number of candidate bars after the cost gate and blackouts
  (calendar upper bound ≈ 4,000 days × ~52 bars over 13 hours; the cost gate is expected to remove a larger share
  of 2012–2019, when cost in R was 2–3×
  higher, SoA A7), and the number per year.
  **Run count-owed-events and paste the counts before the freeze** (candidate bars, labelled rows = 2x, blackout and
  cost-gate drops, per year). Spec-derived until E-2 is merged (the spread is the bar's Dukascopy mean spread, not a
  `costs-v1` session spread); recount with E-2's own candidate code before the freeze.
- **Feature version:** `f-079deacc61`.

### H-18 Post-blackout event continuation (one trial, conditional)

- **Specialist:** `event_continuation` (E-3), 15m, rule-only screening family.
- **Events (E-1):** scheduled US NFP, CPI, PCE and FOMC statement times, 2010-01 .. 2025-09, at whatever scheduled
  time each had (FOMC was 12:30 / 14:15 ET before 2013, 14:00 ET after). One trade a day at most: the first event of
  the day; later same-day events are skipped and counted.
- **Entry:** P0 = open of the 15m bar starting at the scheduled time; the entry bar is the 15m bar closing at release
  + 30 min (the end of the gate's blackout, **[−15, +30) min around the scheduled release, on the bar close**, so the
  bar closing at +30 is the first allowed; the allocator's live [−30, +30] min zero-weight window is a sizing rule,
  not part of this event definition). M = entry close − P0. Enter if |M| > 1.0 × ATR_pre, where ATR_pre is the
  Wilder ATR(14) of 15m mid bars at the close of the bar before the release bar; side = sign(M).
- **Stop:** 1.5 × ATR(14) at the entry bar's close (the system's stop rule; it includes the release bars).
- **Exit:** V1 as H-17, flat at 16:30 New York.
- **Event floor:** 1,000 unless the owner rules otherwise (O-1). The calendar count is ≤ 44 a year, ≈ 690 in the
  window **before** the |M| filter, so under today's floor H-18 can only be inconclusive. **Its row is written only if
  O-1 is ruled or tier-2 pooling exists; otherwise it is withdrawn before the freeze and the slot goes to reserve.**
- **Reading rule:** **continue** if gross t ≥ 2.0 on at least the floor's events, net mean R > 0, and ≥ 3 positive
  net years incl. 2021 or 2022. **Stop** (event continuation retired) if net mean R ≤ 0 or the screen fails on at least
  the floor's events. Otherwise inconclusive.
- **Events owed from data before the freeze:** events with the entry bar present, and how many pass |M| > 1.0 ATR
  (events only: the sign and size of M at +30 min are inputs, not outcomes; nothing after the entry close is read).
  **Run count-owed-events and paste the counts before the freeze** (spec-derived until `event_continuation` is
  merged; recount with its own code before the freeze).
- **Distinct from:** H-11 (needs a consensus surprise), R-08 pre-FOMC drift (before the release).
- **Feature version:** `f-079deacc61`.

## Settled decisions and pins owed before any count is seen (research director, 2026-10-11)

Settled by the quant review and written into the rules above: (1) H-17's blackout reading is literal: if the day's
first breach falls in a blackout, the day is skipped; (2) the blackout is [release − 15 min, release + 30 min) on the
bar close; (3) H-14's cost prior is the Dukascopy bar spread plus the settings extra cost. The draft was checked
against all three: H-17's entry and H-14's candidates now state them; H-18 already enters at the bar closing at
release + 30 min, which is outside its own release's half-open window.

Still ambiguous. Each must be pinned here **before** count-owed-events is first run on `main`, so that no rule is
chosen after a count has been seen. Counts are events only, never outcomes, but the floor-based withdrawals (H-17
< 1,000, H-18's floor) make them decision inputs. Owner of every pin: strategy researcher drafts it, quant reviewer
rules, research director records it here. Date: **2026-10-25**.

| Pin | Question | Recommendation |
|---|---|---|
| P-1 (**resolved 2026-10-11**: the literal count `high_events_strict` is in `count_owed_events.py`) | The count script in review (`scripts/count_owed_events.py`, commit `9343f2f`, not on this branch) takes H-17's entry as the first breach *outside* the blackout. That is the non-literal reading. It reports `*_first_breach_in_blackout` beside it but not the literal count | Fix before merge: `high_events` / `low_events` = days whose first eligible breach is outside the blackout (literal). Keep the non-literal figure only as a labelled diagnostic. The withdrawal test reads the literal count |
| P-2 (**adopted 2026-10-11**, written into H-17's entry; applied in count-owed-events on 2026-10-11) | H-17: what if the first breach bar is in a closed session or has no ATR(14) yet? The script folds both into "eligible" and moves on to the next breach | Literal, the same as the blackout: the first breach bar (B defined, inside the window) is the only candidate. If it cannot be traded, the day is skipped |
| P-3 | Which tier-1 rows black out? The live gate blacks out `[CPI, NFP, FOMC, PCE]` (`config/settings.yaml` `risk.blackout.events`). E-1 also carries the FOMC press conference as a tier-1 row, and the script blacks out every tier-1 row | Research blacks out exactly what the live gate blacks out, so labels, shadow and live agree. Engineering confirms whether the live gate's `FOMC` covers the press conference, and the research set is pinned to match |
| P-4 | H-18: on FOMC days with a press conference (statement + 30 min), the entry bar's close (statement + 30) falls inside the press conference's window [+15, +60). Two tier-1 releases can also share a day | Gate parity: H-18 never enters inside any tier-1 blackout. If P-3 pins the press conference as blacked out, the FOMC event is the statement-plus-press-conference cluster: entry at the bar closing at the end of the union of their windows (press conference + 30 min), with M still measured from P0 at the statement. Counted and reported by type |
| P-5 (**adopted 2026-10-11**, written into "Fixed for every trial" and H-14) | Cost source between the freeze and the run. "Fixed for every trial" uses `costs-v1` if it is published by run time; the H-14 gate now never reads it | Pin the cost source **at the freeze** for every Q2 config (priors, or a named `costs-v1` version) and never switch it before the run. H-14's labels charge the same costs as its gate (bar spread in the ask/bid labels + the frozen extra cost). Any `costs-v1` result is a reported sensitivity only |
| P-6 | H-14: "net mean R > 0 on ≥ 1,000 events". Events, or candidates? | Trades taken by the pre-set rule (top-tercile p, one position at a time, both sides), not candidate rows |
| P-7 | H-14 survives at stability 0.7. Q1's H-02 slot rule uses 0.6 | Keep 0.7 (stricter at 15m, where costs bind). List it as a stated difference from H-02 at the freeze, as the draft promises |
| P-8 | Day mapping: the London date of the bar close vs the `day_type` day (22:00 UTC rollover) whose baseline forecast labels the day high or low | The `day_type` day D that contains 08:00 London on London date D. Its forecast is the one emitted at the previous stub-free day's close (`har_forecast`). H-17's whole window (08:00 London .. 16:30 New York) lies inside D in both DST regimes |
| P-9 | Does H-17's noise boundary B include days whose bar at τ fell in a blackout? | Yes. B measures noise, not trades: it uses every one of the previous 14 days that has a bar at τ |
| P-10 | Year and day attribution | Positive years by entry time in UTC (as H-01). H-18's "first event of the day" by New York date |

## Dependencies

| Kind | Item | Trials |
|---|---|---|
| Owner | **O-1** event floor for scheduled-event families (options in `research-plan.md` §9; recommendation: pool with tier-2 once it exists; until then H-18 waits) | H-18 |
| Owner | Raw-spread account confirmed (otherwise no 15m result can trade at £1,000) | H-17, H-14, H-18 use, not runs |
| Quant | **Q-1** H-17's overlap with retired R-02 / R-05 (ruled 2026-10-10: Welch t ≥ 2.0 on weekly blocks, V1 after V0, fail final) | H-17 |
| Quant | **Q-2** H-16's expanding monthly refit instead of the default 1d window (approved 2026-10-10, first OOS month and refit instant frozen) | H-16, H-17 |
| Quant | **Q-3** H-14's survivor unit = group (K_eff) (ruled 2026-10-10: groups ≤ 39 columns, frozen group-map hash, refuse rather than truncate) | H-14 |
| Engineering | BACKLOG **30a** `day_type` opt-in features, with a fold-fit hook for the HAR coefficients and the truncation tests | H-16, H-17, overlays |
| Engineering | BACKLOG **31** `flat_at_local` (and parity across labels, shadow and live) | H-17, H-14, H-18 |
| Engineering | BACKLOG **32** gate EV from the measured mean winner | H-17 V1, H-18 before any shadow or live use |
| Engineering | BACKLOG **30b** day-score allocator input | only after H-16 passes; not needed for any run |
| Engineering | BACKLOG 33 (opportunity-day report), on 20 and 26 | reports only |
| Data (new) | **E-1** historical tier-1 schedule 2010-01 .. 2025-09 (scheduled times from BLS, BEA and Fed calendars; no outcomes), loadable as research `ctx["events"]`. The live archive is forward-only and research passes no events today | H-16, H-17, H-14, H-18 |
| Engineering (new) | **E-2** 15m two-sided discovery candidate spec with the cost gate | H-14 |
| Engineering (new) | **E-3** `trend_day` and `event_continuation` screening specialists. `trend_day` implements H-17's literal blackout rule: only the day's first breaching bar is a candidate, and if its close is inside the tier-1 blackout [−15, +30) min (on the bar close) the day has no entry | H-17, H-18 |
| Engineering (new) | **E-4** an evaluation path for H-16 that records one registry trial and prints the reading rule. **Built 2026-10-11** (`goldbot/research/h16.py`, `research_pass.py --h16 --first-oos-month YYYY-MM`; quant review pending) | H-16 |
| Engineering (new) | **E-5** protect in-quarter allocations (survivors, Q1 follow-ups) from automated spending of the unreserved remainder | budget integrity |

## Not in Q2, and why

| ID | Reason |
|---|---|
| H-01, H-06, H-04, H-02 | Q1 (frozen). Follow-ups only, from the Q1 reserve above |
| H-03, H-05, H-07, H-08, H-10 | Survivor rows of Q1's H-02 at 4h; at 15m, H-05/H-08/H-10 are screened inside H-14 |
| H-15 | Daily, few events; needs playbook D-8 and ruling A |
| H-11 | Blocked on a consensus feed (owner decision C) |
| H-09, H-12, H-22 | Features only (0 trials) |
| H-20, H-21 | Inside H-16 / H-17 (0 trials) |
| H-23 | Blocked on intraday USD data (D+, owner) |
| V2 / V3 runner exits | Diagnostics in H-17's report; using them costs a trial each (Q3 if V1 beats V0) |

## Freeze checklist (by 2027-03-31)

- [ ] Q1 2027 verdicts recorded (or the follow-up reserve kept as written)
- [x] Quant rulings Q-1, Q-2, Q-3 recorded here (2026-10-10)
- [ ] Owner ruling O-1 recorded, or H-18 withdrawn
- [ ] BACKLOG 30a, 31, E-1, E-2, E-3, E-4 merged and tested; E-5 decided (E-4 built 2026-10-11, quant review pending)
- [ ] Pins P-1 .. P-10 above recorded **before** the first count-owed-events run on `main` that has `calendar-v1`
  (target 2026-10-25; accountable: strategy-researcher). **Deviation, recorded 2026-10-16 by the research director:**
  the first run on `main` (38931951849, 2026-10-16, for the Q1 counts) came before P-3, P-4 and P-6 .. P-10 were
  recorded. It had no `calendar-v1` and E-2/E-3 are not merged, so every H-17, H-18 and H-14 row and H-16's
  `expected_eval_days` came back "not counted" (n/a; per-year rows all 0): no count that an open pin governs was
  seen. The only Q2 value it printed is H-16's `first_oos_month` = **2012-03** (placebo prices; the script's expected
  value is 2012-04), which no open pin touches; the difference is explained by the strategy-researcher before the
  Q2 freeze. The item is therefore restated to the first run that can count the pinned rows.
- [ ] `calendar-v1` release published (accountable: owner sets the `FRED_API_KEY` repo secret, chaser:
  program-director, by 2026-10-18; then data-engineer dispatches `data-us-calendar`), otherwise the
  H-16/H-17/H-18/H-14 counts come back "not counted". **Blocked on the owner** (no secret on 2026-10-16;
  `data-us-calendar` has never run).
- [x] `macro-v1` release published (2026-10-13, scheduled `data-macro` run 38394945106; checked 2026-10-16)
- [ ] Event counts owed above recorded (events only, no labels or outcomes): run count-owed-events and paste the
  counts before the freeze, recounted with E-2/E-3's own code once merged; H-17 withdrawn if < 1,000
- [ ] `costs-v1` published (or the priors accepted and stated); `macro-v1` present for H-16
- [ ] `research.reserved_trials_quarter` for Q2 set to the number of queued rows
- [ ] Each queued row written with `queue=True, target_quarter="2027Q2"` and this file's freeze commit

## Not verified / left out

- No data was read. Every event count is a calendar estimate or quoted from `opportunity-days.md`; the exact counts
  are owed as listed.
- The H-14 discovery config is aligned with H-02's description in `preregistration-2027Q1.md`, not with H-02's
  registry row, which does not exist yet.
- Whether the research director's automated pool or the analyst would spend Q2's unreserved remainder was read from
  the code's budget logic (`goldbot/research/registry.py` `check_budget_reserved`, `goldbot/research/director.py`
  `reserved_trials`), not tested.
- No code, settings, registry rows, budget, holdout or the Q1 pre-registration were touched.
