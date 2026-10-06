# Design improvements after the first clean research pass (October 2026)

Decision document for the owner. Date: 2026-10-06. Status: proposed, nothing implemented.
Scope: research, modelling, learning loop and gates. Execution, risk limits and operations are unchanged.
The design (online copy, mirrored in `docs/DESIGN.md`) stays the source of truth: any proposal accepted here is
written into the online design first and then built through CI as usual. This document changes no code.

## 1. Summary

**What the evidence says.** On 16 years of clean Dukascopy bars (volume bug fixed, all 163 features passing the
lookahead check, one position per agent), none of the four rule families has an edge at default settings:

| Family | TF | Out-of-fold AUC | Rule alone after costs | Model-filtered result | Binding problem |
| --- | --- | ---: | --- | --- | --- |
| breakout | 1h | 0.49 | loses | none | no information in features |
| trend | 1h | 0.50 | loses | none (earlier: 2 trades in 13 years) | no information in features |
| session_open | 15m | 0.50 | loses | none | ~90 candidates a year: most 24-month windows have < 200 training rows |
| mean_reversion | 15m | 0.54 | loses | ~16 trades in 15 years, hit 0.94, DSR 0.991 | far below every trade-count gate |

**The honest reading.** An AUC of 0.50 means the features carry no usable information about whether these rules'
trades work, and meta-labelling can only filter an edge that the primary rule already has. The rules lose after costs
in every family, so there is nothing to filter. Mean-reversion's 0.54 is about two null standard errors (for 1,000
candidates split evenly the null SE of an AUC is about 0.018), after at least four families were tried, which is weak
evidence. Its 16-trade result is not evidence at all (section 2, finding F1). The edge may not exist at 15m and
1h horizons on gold at retail costs with price-derived features. That is a real outcome, and this document is
built so the next round of work can confirm or reject it quickly and cheaply, instead of searching until noise
passes a gate.

**Code review findings that change how to read the numbers.** Reading `goldbot/research`, the specialists and the
retrain job shows several defects that bias results in both directions (section 2). The most important: the model
sees the first 40 feature columns in registry order (no RSI, MFI, breakout, tick volume, support/resistance or any
higher-timeframe context, and no macro); the model is never told the trade's side; the isotonic calibrator is
fitted on the same out-of-fold predictions it is then used to select trades from; and the spread is charged twice
while commission and slippage are not charged at all. The first two can hide a real edge. The third can manufacture
one.

**Recommended first batch (at most three, all small):**

1. **P1: fix the evaluation before searching further.** Cross-fitted calibration and threshold, a correct cost
   hurdle, the design's trade-count gates enforced in code, and no deflated Sharpe on tiny samples.
2. **P2: protect the statistical budget.** Pause the monthly label-grid loop, lock a held-out final year, and
   pre-register a small trial budget for the quarter.
3. **P3: give the meta-model the right inputs.** Side-aligned features, a deliberate per-family feature list that
   includes higher-timeframe context, and a group ablation report.

Then re-run the four families once, as four pre-registered trials. If AUC stays near 0.50 with correct inputs,
that is strong evidence against these rules, and the second batch (P4 denser primary signals and P5 pooling) is
where to go next. If it also fails, the pivot is to longer horizons or more instruments (P6), not to a wider search.

**Ranking (expected value per unit of effort):**

| Rank | Proposal | Area | Effort | Expected value |
| ---: | --- | --- | --- | --- |
| 1 | P1 Fix the evaluation (calibration, costs, enforced gates) | e | S | High: every later result depends on it |
| 2 | P2 Pre-registered budget, held-out year, pause the label grid | d | S | High: keeps a real edge provable |
| 3 | P3 Side-aligned features and a deliberate 40-feature selection | b | S–M | Medium–high: cheapest real chance of AUC above 0.5 |
| 4 | P10 Re-specify shadow promotion and retirement tests | e | S–M | Medium now, high once anything trades |
| 5 | P4 Primary-signal screen and denser, literature-backed primary signals | a | M | High upside, uncertain |
| 6 | P5 Pooled meta-model across families and sessions | a | M | Medium: fixes session_open's window problem, more rows |
| 7 | P9 Live and shadow data into learning: recalibrate weekly, refit monthly | c | M | Medium: what "self-improvement" can safely mean |
| 8 | P7 Change the target: net R and move magnitude | b | M | Medium: attacks the cost drag directly |
| 9 | P6 More instruments as training data (silver first) | a | M–L | Medium: multiplies rows, but they are correlated |
| 10 | P8 Macro, calendar-event and news features | b | M | Low–medium at these horizons; news has no history yet |

## 2. Findings from the code (read 2026-10-06)

These are facts about the current implementation, with the file where each lives. They are the "mis-specified gates"
answer (area e) and they feed P1, P3 and P10.

- **F1. Calibration and threshold are fitted in-sample on the evaluation set.** `research/pipeline.py`
  `run_specialist` fits the isotonic calibrator on all out-of-fold predictions, then selects `taken` from those same
  rows by calibrated `p > threshold` (and `scripts/research_pass.py` does the same). Isotonic regression is a step
  function; with few rows in the top score range it can give a block of winners p close to 1 because they won. A
  16-trade, 94% hit subset is exactly what this produces. Even before that bias, 15 of 16 against a break-even hit
  rate near 0.62–0.65 has a one-trial binomial p-value of about 0.005–0.01, which does not survive the
  multiplicity of the registry, the threshold choice and the calibration fit.
- **F2. The model sees an arbitrary 40 of 85 base columns, and no context.** `select_features` takes
  `eligible[:40]`, and columns come in registry order (session, returns, ATR, realised vol, moving averages, trend
  strength, then `bb_z_20`, `bb_pctb_20`). Everything after that is never used: RSI, MFI, VWAP distance, every
  breakout/range feature, tick volume and spread, support/resistance, swings, gaps, candles, and all `h1_`, `h4_`,
  `d1_` context columns (merged last). Breakout's model never sees the volume or range features its own trigger is
  built on. `macro` and `calendar_events` are excluded from `DEFAULT_FEATURE_NAMES` altogether.
- **F3. The model is not told the trade's side, and signed features are not side-aligned.** Candidate side is not
  a column. `ret_4 = +0.3%` is good for a long and bad for a short; a tree must learn that interaction from a few
  hundred rows per fold. Standard meta-labelling practice multiplies signed features by side so that "positive"
  always means "in favour of the trade".
- **F4. Spread is charged twice, commission and slippage not at all.** Labels already enter at the ask and exit at
  the bid, so `p(target)` and the net payoff T already include the spread. The research threshold then adds
  `cost_atr = 2 × median(spread / ATR)` again; the engine adds the cost table's full round trip (spread included).
  Commission ($6–7 per lot), slippage and swap are not in the labels. The net effect pushes thresholds up, toward
  the "few near-certain trades" regime, while understating the costs that matter at execution.
- **F5. The design's research gates are not enforced.** The design requires at least 1,500 labelled candidates,
  60 per test fold, and positive expectancy in three separate calendar years including 2021–22. The code has
  `min_train=200` and no per-fold or per-year gate; the per-year table is printed but not checked.
- **F6. The deflated Sharpe is computed on any sample size.** `metrics.summarize` returns a DSR for 16 trades. The
  DSR is an asymptotic statistic; at n = 16 with a 94% hit rate the skew/kurtosis correction is unreliable and the
  number (0.991) is meaningless. It also uses the theoretical null variance `1/n` instead of the variance of Sharpe
  across the registry's trials, and the global trial count across unrelated families.
- **F7. The deployed model never trains on the most recent data.** The Saturday retrain deploys the last fold's
  model, trained on the window before the last test window: the latest 3 months (15m) or 6 months (1h) of data are
  never in a champion's training set. The calibrator is fitted on a mixture of all folds' models' scores and then
  applied to that one model.
- **F8. The shadow book only records trades above threshold.** `engine/runner.py` opens shadow trades only when
  `p` clears break-even + 0.02, so live outcomes exist only for the selected tail. Any recalibration from shadow
  data is therefore biased by selection.
- **F9. Promotion and retirement tests are dominated by noise** (detail in P10). A correctly specified challenger
  passes the hit-rate gate only about 68% of the time; the annualised Sharpe of 40 trades has a standard error of
  about 1.9; and the retirement rule retires a true 0.1R edge 61–72% of the time at 100 trades.
- **F10. The monthly loop spends the trial budget on families with no signal.** 12 label-grid trials per family per
  month (48 a month) plus up to two analyst trials per week: roughly 600 trials a year, mostly ±25% barrier
  perturbations of rules whose features have AUC 0.5. Each one raises the deflated-Sharpe bar for any future real
  discovery (section 3).
- **F11. Small items.** Sample weights are uniqueness × |return|; with one position at a time uniqueness is 1, so
  the weights are just |return|, which overweights high-volatility years. `f_macro` computes "5-day change" as a
  diff over 96 × 5 bars, which is 5 days only on 15m (20 days on 1h). The design still describes the shifted-label
  shuffle test, which the code correctly dropped; the online design should be updated.

## 3. What the statistics allow

This arithmetic sets every gate below. With per-trade expectancy μ (in R) and per-trade standard deviation σ, a
one-sided 5% test with 80% power needs about **n ≈ 6.2 (σ/μ)²** trades.

- 1:1 payoff, σ ≈ 1R, μ = 0.1R: about **620 trades**.
- 2:1 payoff (trend, breakout), σ ≈ 1.45R, μ = 0.1R: about **1,300 trades**. The design's 400–600 is optimistic for
  asymmetric barriers.

Multiple testing raises it further. With the deflated Sharpe at 0.95 and N trials, the expected best Sharpe of N
unskilled trials sets the bar. Trades needed for a true per-trade Sharpe of 0.10 or 0.15 to pass (skew and kurtosis
ignored):

| Trials counted (N) | Expected max of N null Sharpes (× 1/√n) | Trades needed, SR 0.10 | Trades needed, SR 0.15 |
| ---: | ---: | ---: | ---: |
| 5 | 1.19 | ~800 | ~360 |
| 20 | 1.90 | ~1,260 | ~560 |
| 100 | 2.53 | ~1,740 | ~780 |
| 600 (one year of the current loop) | 3.11 | ~2,260 | ~1,000 |

Two consequences drive the proposals:

1. **Trade frequency is the binding constraint, not model quality.** At ~90 candidates a year (session_open) or ~1
   trade a year (mean_reversion's model), no edge of realistic size can ever be shown. Area (a) is about getting
   pooled trade counts into the thousands across 3 years of history-plus-paper.
2. **Every trial run now makes later proof harder.** A year of the current loop multiplies the trades needed by
   about 2.8× relative to a 5-trial programme. The fastest route to an answer is fewer, better trials.

## 4. Proposals

Each proposal states the problem, the change, why it should help, cost and risk, how it is tested through the
existing gates, and exactly which files change. "Effort" is S (a day or two of Claude sessions plus a CI cycle),
M (about a week), L (several weeks or a long data pull).

### P1. Fix the evaluation before searching further (area e, effort S, rank 1)

**Problem.** Findings F1, F4, F5, F6. The only result that looked good (mean_reversion, hit 0.94, DSR 0.991 on 16
trades) is the shape that in-sample isotonic selection produces, and the gates that would have rejected it (1,500
candidates, 60 per fold, three positive years) are not in code. The cost hurdle double-counts spread and omits
commission and slippage.

**Change.**
- Cross-fitted calibration and threshold: fold k's predictions are calibrated by an isotonic (or Platt, when fewer
  than ~500 rows) map fitted only on the OOF predictions of folds before k; the threshold for fold k is chosen from
  folds before k. The first folds that have no history are excluded from the reported "model-filtered" result.
  Report both the rule-only result (every candidate) and the cross-fitted model-filtered result.
- Cost hurdle: in research and in the engine, `c` in `EV = pT − (1−p)S − c` becomes commission + slippage + expected
  swap only (spread is already in the labels). Research uses IC Markets' measured commission and the slippage
  prior ($0.15) until the cost table exists; the engine uses its own table minus the spread component.
- Enforce the design's gates in code and print them as pass/fail lines in the report: at least 1,500 labelled
  candidates; at least 60 candidates in every test fold; positive model-filtered expectancy in at least three
  non-overlapping calendar years including one of 2021 or 2022; at least 200 model-filtered trades before a DSR is
  reported at all (below that the report shows "n too small" and a bootstrap 90% interval on expectancy).
- DSR inputs: use the variance of per-trade Sharpe across the family's registry trials when at least 10 exist (the
  Bailey and López de Prado specification), else `1/n`; count the threshold choice and the calibration fit as part
  of the trial (they are, because they were selected on data).
- Report the rule-only gross (before cost) and net expectancy with a t-statistic, so the "is there anything to
  filter" question (P4) is answered in every report.

**Why it should help.** Meta-labelling results are only as honest as the probability that selects trades; fitting
the selector on the evaluation rows is selection on the outcome. Cross-fitting is the standard remedy
(Chernozhukov et al. 2018, double/debiased ML; López de Prado 2018, *Advances in Financial Machine Learning*,
ch. 7 on purged CV). Isotonic calibration is known to overfit below roughly a thousand calibration rows
(Niculescu-Mizil and Caruana 2005), so small folds should use Platt scaling. The DSR is an asymptotic statistic
(Bailey and López de Prado 2014); the design's own trade-count gates exist because small samples cannot be judged.
Fixing the cost hurdle stops the threshold drifting toward a handful of near-certain trades.

**Cost and risk.** Low effort; all results so far become non-comparable with new ones (record the change as a new
`feature_version`-like `eval_version` in the registry). Cross-fitting costs the first one or two folds of
model-filtered history. It may make every family look worse; that is the point.

**How to test.** Unit tests: on synthetic bars with no signal (`goldbot/data/synthetic.py`) the model-filtered
DSR must stay below 0.5 in at least 95 of 100 seeds, and the old in-sample path must be shown to exceed it more often
(a regression test that documents F1); with a planted signal, the cross-fitted path must recover it. Integration:
`pytest -m integration && python scripts/dry_run.py 1`. Then one `research.yml` run per family, each recorded as a
pre-registered trial (P2).

**Files.** `goldbot/research/pipeline.py` (cross-fitted calibration and threshold, rule-only gross/net stats, gate
evaluation), `goldbot/research/metrics.py` (DSR minimum n, cross-trial variance, bootstrap interval on expectancy),
`goldbot/research/walkforward.py` (per-fold minimum of 60 test candidates, configurable `min_train`),
`goldbot/research/model.py` (Platt option for small calibration sets), `goldbot/engine/runner.py` (cost hurdle
without spread), `goldbot/execution/costs.py` (a `round_trip_ex_spread_atr` accessor), `goldbot/ops/jobs.py`
(`backtest_stats` and `_walk_forward` pass the cost hurdle and gate results), `scripts/research_pass.py` (report
gate lines, rule-only table), `tests/test_research_pass_integration.py`, `tests/test_costs_promotion_registry.py`,
`tests/test_engine_costs.py`.

### P2. Protect the statistical budget: pre-registered trials, a held-out year, batch evaluation (area d, effort S, rank 2)

**Problem.** Finding F10 and section 3: the current loop spends about 600 trials a year on ±25% barrier variants of
rules with no information, which multiplies the trades a future real edge needs by about 2.8×. The design's risk
table promises "a held-out year the loop never scores", but nothing in the code holds a year out: every trial walks
forward to the present.

**Change.**
- Set `research.trial_budget_per_month` to 0 (pause the label grid) until a family passes P4's primary-signal screen.
  The analyst's `run_trial` refuses once the quarter's budget is spent.
- A quarterly pre-registered budget: for Q4 2026, **at most 20 trials in total across all families**, each written
  into the registry before it runs (`status: "preregistered"`) with hypothesis, expected effect, and the single
  metric that decides it. The DSR for the quarter uses at least the full budget as N, whether or not it is spent.
- Lock the last 12 months (2025-10-01 to 2026-09-30) as a held-out year. Research and the analyst never see it.
  A configuration that passes every walk-forward gate is scored on it exactly once, recorded with
  `status: "holdout"`, and the result is final for that configuration. Because 2024–26 was a strong one-way gold
  trend, the holdout is a check, not a regime sample; the three-year rule (including 2021–22) still applies.
- Variant batches: when a hypothesis has variants (for example six thresholds), they run as one batch with one
  `batch_id`; the report gives the probability of backtest overfitting (PBO) by combinatorially symmetric
  cross-validation across the batch, and the winner is chosen by a criterion declared before the run. All variants
  count as trials.
- Effective trial count: cluster the registry's trials by the correlation of their OOF trade-return series and use
  the number of clusters as N (López de Prado and Lewis 2019), but never fewer than the number of distinct
  pre-registered hypotheses.

**Why it should help.** Pre-registration and a fixed budget are how the multiple-testing penalty stays small enough
for a modest edge to be provable (Harvey, Liu and Zhu 2016 argue for t > 3 on searched factors; section 3 above gives
the trade counts). PBO/CSCV (Bailey, Borwein, López de Prado and Zhu 2017) measures how often the in-sample best
variant underperforms out of sample, which is the failure the label grid is prone to. A held-out final year is the
one test no amount of walk-forward reuse can contaminate.

**Cost and risk.** Slower apparent activity: the dashboard shows fewer trials. Clustering trials into an effective N
lowers the bar relative to raw counting; mitigated by the floor at distinct hypotheses. The holdout removes the most
recent year from research training, which matters for a drifting market; it is returned to the training set once a
configuration has been scored on it or at the end of the quarter, and a new holdout is never needed for the live
champion because shadow trading is itself out of sample.

**How to test.** Unit tests for budget refusal, holdout masking (no candidate with `ts_utc` in the holdout reaches
`run_specialist` unless the run is a holdout scoring), and a PBO function checked against a synthetic batch where the
true answer is known (pure noise gives PBO near 0.5). The registry sync must keep the new statuses.

**Files.** `config/settings.yaml` (`research.trial_budget_per_month: 0`, new `research.trial_budget_quarter`,
`research.holdout_from`), `goldbot/config.py` (Settings fields), `goldbot/ops/jobs.py` (`monthly_research`,
`make_trial_runner`: budget and holdout), `goldbot/research/registry.py` (statuses `preregistered` and `holdout`,
`batch_id`, budget accounting), `goldbot/research/registry_sync.py` (merge key keeps the new fields),
`goldbot/research/metrics.py` (PBO via CSCV; effective N from clustered trials), `scripts/research_pass.py`
(`--holdout-from`, `--batch-id`, `--score-holdout`), `.github/workflows/research.yml` (inputs for the above),
`goldbot/agents/tools.py` (`run_trial` budget check), `goldbot/agents/roles.py` (analyst instructions), new
`docs/research/preregistration-2026Q4.md` (the quarter's hypotheses, written before running them),
`tests/test_registry_sync.py`, `tests/test_jobs_integration.py`, `tests/test_agents.py`.

### P3. Give the meta-model the right inputs (area b, effort S–M, rank 3)

**Problem.** Findings F2 and F3: the model sees the first 40 columns by registry order, no higher-timeframe context,
none of the features its own trigger is built on, and not the trade's side. An AUC of 0.50 under these inputs says
less than it appears to.

**Change.**
- Add `side` as a feature and side-align every signed feature (returns, distances to averages and levels, slopes,
  z-scores, RSI and MFI centred at 50, Donchian position, structure state): the model sees `side × x`. Tag signed
  features in the registry (`tags=("signed",)`) so the alignment is mechanical and the engine applies the same
  transform at scoring time.
- Replace `eligible[:40]` with a per-family declared feature list (each specialist gets a `feature_groups`
  attribute with a written rationale): for example mean_reversion gets band/RSI/MFI/VWAP distance, realised-vol
  regime, session clock, `h1_`/`h4_` trend context and distance to support/resistance; breakout gets range,
  compression, tick-volume and spread features plus `h4_`/`d1_` trend. Within the declared groups, choose up to 40 by
  clustered importance computed inside each training fold only (López de Prado 2018, ch. 8), never on the full
  sample.
- Report a group ablation table (drop one group at a time, change in OOF log-loss and AUC, inside the same folds).

**Why it should help.** A meta-label model should get the information relevant to whether that setup works:
context (is the higher timeframe trending against the mean-reversion trade?) and the trigger's own strength (how
loud was the breakout's volume?). Side alignment halves the interactions a tree must discover, which matters when a
fold has a few hundred rows. Neither creates an edge where none exists, but both remove reasons the current 0.50
could be a false negative.

**Cost and risk.** Feature selection inside folds is extra compute (seconds per fold at these sizes). Declared lists
are a researcher choice, so they must be written before the run (P2) to avoid list-shopping; the ablation table is
reporting, not a selection step. Train/serve parity risk: the engine must apply the same side alignment, covered by a
test that scores one frame through both paths.

**How to test.** The lookahead check runs unchanged on the new frame. Unit test: on a synthetic series where the
outcome depends on `side × ret_4`, the aligned model's AUC exceeds the unaligned one's. Engine parity test. Then the
four families re-run once each as the pre-registered P1+P3 trials; the deciding metric is OOF AUC with a bootstrap
interval, and the rule-only expectancy from P1.

**Files.** `goldbot/research/pipeline.py` (side column, alignment, fold-internal selection, ablation),
`goldbot/specialists/base.py` (`feature_groups` attribute and its validation), `goldbot/specialists/breakout.py`,
`goldbot/specialists/mean_reversion.py`, `goldbot/specialists/session_open.py`, `goldbot/specialists/trend.py`
(declared groups), `goldbot/features/registry.py` (`signed` tag helper), `goldbot/features/technical.py`,
`goldbot/features/structure.py`, `goldbot/features/session.py` (tags), `goldbot/engine/runner.py` (same transform
when scoring and in `_top_features`), `scripts/research_pass.py` (ablation table), `tests/test_features_labels.py`,
`tests/test_engine.py`, `tests/test_specialists.py`.

### P4. A primary-signal screen and denser, literature-backed primary signals (area a, effort M, rank 5)

**Problem.** Meta-labelling assumes a primary signal with some edge and decent recall; it raises precision, it does
not create edge (López de Prado 2018, ch. 3; Joubert 2022, "Meta-labeling: theory and framework"). All four rules
lose after costs, and three of the four are classic chart rules whose standalone edge on a liquid major market is
expected to be near zero. They also fire rarely (session_open ~90 a year; trend and mean-reversion fewer once one
position at a time is enforced).

**Change.**
- A screen before any model is fitted: a rule enters model research only if its rule-only gross expectancy over the
  pre-holdout history is positive with t ≥ 2 on at least 1,000 events, and its net expectancy is not worse than
  −0.05R. A rule that fails is recorded and retired from model research (it can still be a feature for others).
- Two new rationale-backed families, chosen because they have published evidence and fire often:
  - **Time-series momentum** on 1h and 4h decisions: side = sign of the trailing return over several horizons
    (e.g. 1, 5 and 20 days, volatility-scaled), entries at most once per 4h bar, barrier exit. Time-series momentum
    is among the best-documented effects in futures including gold (Moskowitz, Ooi and Pedersen 2012; Hurst, Ooi and
    Pedersen 2017 over a century). Its horizon is days to weeks, so this specialist holds longer and pays swap, which
    the label must include.
  - **Session intraday momentum**: the return from the London (or New York) open to a fixed point predicts the
    direction of the rest of that session. Documented for equity index ETFs (Gao, Han, Li and Zhou 2018) and later
    reported for several futures markets; for gold it is a hypothesis to test, not a known effect. It fires on most
    trading days for each session: about 450–500 events a year.
- Keep the four existing families as shadow-only founders so the record has no survivorship bias.

**Why it should help.** Trade frequency is the binding constraint (section 3), and a primary signal with a small
positive gross edge and high frequency is the configuration meta-labelling is designed for. Both new families are
pre-specified from literature, so they cost one trial each, not a search.

**Cost and risk.** Momentum on 4h/daily trades fewer times per year than 15m rules, but per-trade edge in published
work is larger relative to cost; swap cost (around $12 per lot per night long) must be in the labels. Gold's 2024–26
trend flatters momentum: the three-year rule including 2021–22 is essential. The engine runs on a 15m clock and
evaluates each agent on its own timeframe; a 4h decision timeframe needs walk-forward windows and context rules for
4h.

**How to test.** Screen results printed by `research_pass.py`. Each new family is a specialist file with unit tests
for its trigger (no lookahead: candidates on a truncated history equal the prefix of candidates on the full history).
Then the P1 walk-forward, P1 gates and P2 holdout, as two pre-registered trials.

**Files.** New `goldbot/specialists/time_series_momentum.py` and `goldbot/specialists/intraday_momentum.py`,
`goldbot/specialists/__init__.py` (registration), `goldbot/labels/triple_barrier.py` (swap charged per rollover
crossed), `goldbot/research/walkforward.py` and `config/settings.yaml` (`walkforward: 4h` windows, e.g. train 48 /
test 6 / step 6 months), `goldbot/config.py` (`DecisionTimeframe` includes 4h), `goldbot/features/mtf.py` (context
for a 4h decision bar: `d1_`, weekly), `goldbot/research/pipeline.py` and `scripts/research_pass.py` (screen),
`goldbot/engine/runner.py` (4h agents on the 15m clock), `tests/test_specialists.py`, `tests/test_engine.py`.

### P5. A pooled meta-model across families and sessions (area a, effort M, rank 6)

**Problem.** Each family trains alone on a few hundred rows per window. Session_open has about 90 candidates a year,
so a 24-month window holds about 180, below the walk-forward's 200-row minimum, and a 3-month test fold holds about
22, below the design's 60.

**Change.**
- One pooled meta-model per decision timeframe trained on the union of every family's candidates, with `family`,
  `side`, session and the family's barrier ratio as features, and side-aligned features from P3. Each family is
  still evaluated, gated and traded separately; only the model is shared. A family can opt out if its own model wins
  by more than the pooled model's log-loss interval.
- Session_open specifically: an expanding training window with recency weighting (see P9) instead of the rolling
  24 months, and 6-month test folds, so every fold meets the 60-candidate gate.

**Why it should help.** Pooled ("global") models routinely beat per-series ("local") models when each series is
short, because shared structure (volatility regime, session effects, context) is learned once from more data
(Montero-Manso and Hyndman 2021). Statistical power comes from rows, and pooling roughly quadruples them.

**Cost and risk.** Negative transfer: one family's patterns can degrade another's predictions; mitigated by the
family feature and the opt-out. The pooled model's candidates overlap in time across families, so purging and
uniqueness weights must be computed across the pool. Model registry semantics change: one artefact serves several
agents.

**How to test.** Same walk-forward and gates per family; the deciding comparison is per-family OOF log-loss of
pooled versus local within identical folds, pre-registered as one trial per timeframe.

**Files.** `goldbot/research/pipeline.py` (`run_pool`: union of candidates, cross-family purge and weights),
`goldbot/research/walkforward.py` (expanding-window option, per-family `min_train`, `test_months` override),
`goldbot/labels/triple_barrier.py` (`uniqueness_weights` across a pooled frame), `goldbot/research/model.py`
(categorical `family`), `goldbot/research/model_registry.py` (an entry may serve several agents),
`goldbot/ops/jobs.py` (`saturday_retrain` for pooled models), `goldbot/engine/runner.py` (scoring with a shared
model), `scripts/research_pass.py` (`--pool`), `tests/test_jobs_integration.py`, `tests/test_engine.py`.

### P6. More instruments as training data, silver first (area a, effort M–L, rank 9)

**Problem.** One instrument over 16 years caps the number of independent events.

**Change.** Pull XAGUSD (and optionally XPTUSD) 1m bars from Dukascopy through the same workflow, and add them as
extra training rows for the pooled model (P5), with an `instrument` feature and instrument-specific cost and ATR
normalisation. Trading stays XAUUSD only; the design's expansion gate for trading silver is unchanged.

**Why it should help.** Cross-sectional pooling is how most published momentum and carry evidence reaches
significance. Silver shares gold's macro drivers, so learned context transfers.

**Cost and risk.** Gold and silver returns correlate strongly (often 0.7–0.8 daily), so the effective sample grows
by much less than 2×; the report should state an effective-n estimate. Silver's spreads and volatility are larger,
so cost-normalised labels are essential. The data pull is hours of Actions time per instrument.

**How to test.** Gold-only OOF log-loss with and without silver rows, same folds, one pre-registered trial. The
evaluation set remains gold candidates only.

**Files.** `scripts/dukascopy_year.py` (`--instrument`), `.github/workflows/data-dukascopy.yml` (instrument
matrix, file names), `scripts/fetch_data_release.py`, `goldbot/data/loaders.py` and `goldbot/data/release.py`
(instrument in file names and frames), `scripts/research_pass.py` (`--train-instruments`),
`goldbot/research/pipeline.py` (instrument column, gold-only evaluation), `tests/test_dukascopy_year.py`,
`tests/test_data_layer.py`.

### P7. Change the target: net R and move magnitude (area b, effort M, rank 8)

**Problem.** The model predicts P(target before stop). Time-outs are lumped with stops, yet `EV = pT − (1−p)S − c`
treats every non-target as a full stop; and the samples are weighted by |return| (F11). At 15m the cost is a large
fraction of the typical move, so the trades that fail are often the ones whose move was too small to clear cost,
which is a volatility question rather than a direction question.

**Change.**
- Train on the realised net R of each candidate (a regression with a robust loss, or a three-class model for target
  / stop / time with the time-out's mean R), and select trades on predicted net R above a cost hurdle.
- Add a magnitude model: predict the absolute move over the barrier horizon in ATR (realised-volatility forecasts
  are among the most reliable in finance, e.g. Corsi 2009 HAR-RV; intraday volatility follows strong session and
  announcement patterns, Andersen and Bollerslev 1998). Use it to skip candidates whose expected move is below a
  multiple of cost and to scale barriers, not to pick direction.
- Drop the |return| weighting in favour of uniform weights (uniqueness is already 1).

**Why it should help.** Direction at these horizons appears close to unpredictable (section 1). Magnitude is
predictable. A filter that avoids low-move setups directly attacks the cost drag that makes every rule lose. Net-R
targets also match what is actually paid.

**Cost and risk.** Regression targets in finance are heavy-tailed; use Huber loss and winsorise in-fold. A magnitude
filter can remove most candidates in quiet regimes and reduce trade count. Barrier scaling by predicted volatility is
a label change, so it needs owner approval under the current design rule.

**How to test.** Within the same folds, compare the cross-fitted model-filtered net expectancy of the three
selectors (current binary, net R, binary plus magnitude filter) as one pre-registered batch with PBO.

**Files.** `goldbot/labels/triple_barrier.py` (`r` column = net R including P1 costs, outcome class),
`goldbot/research/model.py` (regression and multiclass objectives; a `MagnitudeModel`),
`goldbot/research/pipeline.py` (selector by predicted net R; weights), `goldbot/research/metrics.py`
(three-outcome EV), `goldbot/engine/runner.py` and `goldbot/engine/shadow.py` (EV from the model's output),
`goldbot/specialists/base.py` (optional volatility-scaled barriers), `tests/test_features_labels.py`,
`tests/test_shadow.py`.

### P8. Macro, calendar-event and news features (area b, effort M, rank 10)

**Problem.** Macro loaders and features exist (`goldbot/data/macro.py`, `f_macro`) but no workflow publishes FRED
history and research excludes `macro` and `calendar_events`. News is collected and scored only from now on.

**Change.**
- A `data-macro.yml` workflow that pulls the design's FRED series and CFTC COT to a release with `available_utc`,
  loaded into `ctx["macro_wide"]` by research; fix `f_macro`'s change horizon to calendar days.
- Event-time features from a historical calendar: minutes to and since the last tier-1 release, and whether a
  candidate's holding window spans one. These enter as regime and magnitude inputs (P7), where announcements have
  strong documented effects on intraday volatility (Andersen, Bollerslev, Diebold and Vega 2003).
- News: no feature enters a model until 12 months of scored headlines exist. GDELT has history back to 2015 and
  could provide a backfilled event-intensity series; scoring it with a language model costs money and must be
  scored as-of `received_utc`, so it is a separate, later decision.

**Why it should help, and why it is ranked last.** Real yields and the dollar explain gold over months (Erb and
Harvey 2013), and that relationship itself broke down after 2022. Within a 1–48 hour trade, daily macro values are
nearly constant, so they act as regime labels, which the allocator already approximates. Their best use is the
event-time and magnitude angle, not direction.

**Cost and risk.** New data pipeline to maintain; FRED revisions require vintage handling (ALFRED) to avoid
lookahead. Adds features that compete for the 40-feature cap.

**How to test.** The lookahead check plus the existing seeded-release as-of test; one pre-registered ablation trial
(macro and event groups in or out) on the P5 pooled model.

**Files.** New `.github/workflows/data-macro.yml` and `scripts/fetch_macro.py`, `goldbot/data/macro.py` (vintages),
`goldbot/data/econ_calendar.py` (historical backfill), `goldbot/features/session.py` (`f_macro` horizon in days;
event-time features), `goldbot/research/pipeline.py` (`DEFAULT_FEATURE_NAMES` includes macro and events when
`ctx` provides them), `scripts/research_pass.py` (load macro into `ctx`), `tests/test_data_layer.py`,
`tests/test_econ_calendar.py`, `tests/test_features_labels.py`.

### P9. Live and shadow data into learning, safely (area c, effort M, rank 7)

**Problem.** The owner wants the model to keep improving from past and live data. Today: a weekly retrain on a
rolling window changes about 1% of the training data each week and creates a challenger that cannot be told apart
from the champion in four weeks (P10), so the weekly cadence mostly churns. The deployed model never contains the
latest 3–6 months (F7). Shadow outcomes exist only for selected trades (F8), so they cannot be used for calibration
without bias. Shadow trades replicate the labels exactly, so as training rows they add nothing that the bars do not
already give; the genuinely new information from live trading is costs, fills, the owner's vetoes and calibration
drift.

**Change.** Separate three things the current loop mixes:

- **Recalibrate weekly (cheap, low variance).** Keep the champion's trees fixed and update only the probability map
  from recent outcomes: a Platt-style intercept and slope fitted on the last N candidate outcomes, shrunk toward the
  backtest calibration by a prior worth about 200 trades, so 20 new trades move it a little and 400 move it a lot. A
  recalibration is a minor version, logged, bounded (probabilities can move by at most ±0.05 per week) and does not
  need a shadow period; it does need the CUSUM and ECE monitors to keep running.
- **Refit monthly, not weekly (higher variance).** At the end of the walk-forward, refit on all available data
  including the latest window, calibrated by cross-fitting (P1). Allow an off-cycle refit only when drift alarms fire
  (PSI above 0.25 on a top feature, ECE above 0.08). Recency weighting with a half-life (for example 12 months on an
  expanding window) is one pre-registered trial, judged by OOF log-loss; report the Kish effective sample size,
  because heavy decay throws data away.
- **Counterfactual shadow.** The shadow book records the barrier outcome of every candidate, including those below
  threshold and those the owner rejected, flagged by decision. That gives unbiased recalibration data, measures
  what the threshold and the veto are worth, and makes P10's paired comparison possible.
- **Live costs into labels.** Once the nightly cost tables have 50+ fills per session, research labels use
  per-session measured commission and slippage (P1) instead of the prior.

**Why it should help.** In drifting data, calibration drifts faster than ranking ability, and recalibration is the
low-variance response (Platt 1999; the concept-drift literature, e.g. Gama et al. 2014, separates detection from
adaptation for this reason). Retraining a flexible model on a 1% data change mostly adds variance. A prior-weighted
update stops one bad week from moving live sizing much, which is the "learn from present data without learning
noise" the design asks for.

**Cost and risk.** Recalibration that adapts too fast can chase noise and push sizing around; the prior weight and
the weekly cap bound it. Counterfactual shadow outcomes for rejected trades are still conditioned on the same
candidate rule, so they remain fine for calibration but not for discovering new rules. A monthly refit makes the
system feel slower to react; recalibration covers the fast part.

**How to test.** Replay: run the weekly recalibration over the last two years of OOF predictions in time order and
compare ECE and log-loss with a static calibration and with weekly refits (one pre-registered trial). Unit tests for
the bounds and the prior. The existing promotion gates apply unchanged to monthly refits.

**Files.** `goldbot/research/model.py` (`recalibrate(prior_weight, max_shift)`), `goldbot/research/model_registry.py`
(minor versions for recalibrations), `goldbot/research/pipeline.py` (final refit on all data; recency weights;
Kish ESS), `goldbot/ops/jobs.py` (new weekly `recalibrate` job; `saturday_retrain` monthly or on drift),
`config/settings.yaml` (scheduler entries, `research.recency_half_life_months`), `goldbot/engine/runner.py` and
`goldbot/engine/shadow.py` (record every candidate's outcome with its decision), `goldbot/execution/costs.py`
(per-session costs exported for labels), `tests/test_shadow.py`, `tests/test_jobs_integration.py`,
`tests/test_scheduler.py`.

### P10. Re-specify shadow promotion and retirement tests (area e, effort S–M, rank 4)

**Problem (finding F9).** The gates in `research/promotion.py` and `research/population.py` are dominated by noise at
the sample sizes they run on:

- **Hit-rate gate**: "within one binomial standard error of expectation", two-sided. A perfectly specified
  challenger passes about 68% of the time, and a challenger that does better than expected fails.
- **Sharpe gates**: the floor of 0.8 annualised and the "within 0.5 of backtest" rule are applied after 40 trades.
  The per-trade Sharpe of 40 trades has a standard error of about 0.16; annualised at about 150 trades a year, that
  is about 1.9 (Lo 2002). Both gates are close to coin flips.
- **Retirement**: retire when the lower 80% bound on expectancy is below zero after 100 trades. For a true 0.1R
  edge with σ = 1R, the estimate's standard error is 0.1R, so the rule retires it about 61% of the time (72% for
  σ = 1.45R). The design's honest edge is 0.05–0.15R, so this rule retires most real edges. The design promises a
  falsely retired agent can be reversed, but the code has no reinstatement path.
- **Challenger versus champion** are compared through separate summary statistics, although both shadow-trade the
  same candidate stream, which wastes the most powerful comparison available.

**Change.**
- Hit rate: fail only if one-sided below expectation by more than 2 standard errors (a binomial test at 2.5%).
- Replace the Sharpe floor and shortfall with a sequential probability ratio test on per-trade net R (Wald 1945):
  H0 μ = 0 against H1 μ = half the backtest expectancy, α = 0.05, β = 0.2, with a maximum duration after which the
  challenger is retired as "undecided". This decides as soon as the evidence is in, often earlier than four weeks
  for clear cases.
- Challenger versus champion: a paired test on the candidates where their decisions differ (one takes, the other
  does not), using the counterfactual outcomes from P9.
- Retirement: retire when the upper 90% bound on expectancy is below a small positive edge (for example 0.03R),
  i.e. retire on evidence of no edge, not on lack of evidence of an edge; keep the bottom-quartile rule only as a
  capital-allocation signal; add reinstatement when a retired agent's six-month shadow record passes the
  promotion test.
- Turnover gate: scale the tolerance by the Poisson standard error of the trade count instead of a flat 30%.

**Why it should help.** A gate that fails a correct model a third of the time or retires most real edges defeats
the purpose of a long shadow period. Sequential tests reach decisions with the fewest trades for given error rates,
and paired comparisons remove the market noise both models share.

**Cost and risk.** Looser hit-rate and retirement rules let more marginal agents survive longer in shadow; they still
need the DSR promotion gate and they never risk capital in shadow. SPRT needs a backtest expectancy that is itself
honest, which is P1.

**How to test.** Monte Carlo unit tests that pin the operating characteristics: with simulated trades at the
backtest's expectancy, promotion probability at least 80% within the maximum duration; with zero expectancy, at most
5%; retirement of a true 0.1R edge at most 10% at 100 trades. These are test fixtures, not tuning.

**Files.** `goldbot/research/promotion.py` (binomial one-sided test, SPRT, Poisson turnover tolerance),
`goldbot/research/population.py` (retirement on the upper bound, reinstatement), `goldbot/ops/jobs.py`
(`_decide_challengers` uses the paired test and SPRT state), `goldbot/engine/shadow.py` (`PerfStats` carries per-trade
R and the paired records), `tests/test_costs_promotion_registry.py`, `tests/test_population.py`,
`tests/test_shadow.py`.

## 5. Recommended plan

**Batch 1 (P1, P2, P3): about two weeks of sessions, then one research run per family.**

1. Write `docs/research/preregistration-2026Q4.md` first: four trials (the existing families with P1 evaluation and
   P3 inputs), the deciding metric for each (cross-fitted OOF AUC with a bootstrap interval, and rule-only gross
   expectancy t-statistic), and a quarterly budget of 20.
2. Pause the label-grid loop and lock the holdout year.
3. Implement P1 and P3, pass CI, then dispatch `research.yml` for the four families.

**How to read the outcome.**

- **Any family with OOF AUC ≥ 0.55 (lower bound above 0.52) and positive rule gross expectancy:** that family goes to
  P5 pooling and P7 targets in batch 2, then to the holdout and shadow.
- **AUC still near 0.50 everywhere:** these four rules have no learnable structure with these features. Batch 2 is
  P4 (two new primary signals, pre-registered) plus P5. Spend no more trials on barrier grids for the old rules.
- **Batch 2 also fails:** the evidence will then say that price-derived intraday edges on gold at retail cost are not
  there to be found by this system. The options at that point are longer horizons (daily/weekly momentum across
  several instruments, which needs P6), or stopping research spend under the design's stop rule. This should be
  decided before batch 2 starts, so the outcome cannot move the goalposts.

**Before any shadow trading on the VPS:** P10 and the counterfactual shadow part of P9, because without them the
shadow period cannot reach a correct decision in a reasonable time.

## 6. What "self-improvement" can honestly mean here

The system can reliably improve at: cost and slippage modelling (more fills every day), calibration (P9), knowing
which of its own trades it should not take (counterfactual shadow), and the owner-veto record. It can improve its
edge only as fast as evidence accumulates, which section 3 puts at hundreds to thousands of trades per decision. A
loop that retrains weekly and searches monthly does not learn faster; it learns noise faster and raises the bar for
everything that follows. Fast progress therefore means few, pre-registered, decisive experiments, each of which
either kills an idea or moves it to the next gate.

## 7. Decisions needed from the owner

1. Accept the first batch (P1, P2, P3) and the Q4 2026 budget of 20 pre-registered trials.
2. Approve pausing the monthly label-grid loop and holding out 2025-10-01 to 2026-09-30.
3. Approve the reading rules in section 5, including what happens if batch 2 also fails, before results arrive.
4. Approve updating the online design for: the shuffle test replaced by the lookahead check; trade-count targets
   restated from section 3 (about 600 trades for 1:1 payoffs, about 1,300 for 2:1); P10's gate definitions.

## References

- Andersen, T. G. and Bollerslev, T. (1998). Deutsche mark–dollar volatility: intraday activity patterns,
  macroeconomic announcements, and longer run dependencies. *Journal of Finance* 53(1).
- Andersen, T. G., Bollerslev, T., Diebold, F. X. and Vega, C. (2003). Micro effects of macro announcements.
  *American Economic Review* 93(1).
- Bailey, D. H. and López de Prado, M. (2014). The deflated Sharpe ratio. *Journal of Portfolio Management* 40(5).
- Bailey, D. H., Borwein, J., López de Prado, M. and Zhu, Q. J. (2017). The probability of backtest overfitting.
  *Journal of Computational Finance* 20(4).
- Chernozhukov, V. et al. (2018). Double/debiased machine learning for treatment and structural parameters.
  *Econometrics Journal* 21(1).
- Corsi, F. (2009). A simple approximate long-memory model of realized volatility. *Journal of Financial
  Econometrics* 7(2).
- Erb, C. B. and Harvey, C. R. (2013). The golden dilemma. *Financial Analysts Journal* 69(4).
- Gama, J. et al. (2014). A survey on concept drift adaptation. *ACM Computing Surveys* 46(4).
- Gao, L., Han, Y., Li, S. Z. and Zhou, G. (2018). Market intraday momentum. *Journal of Financial Economics* 129(2).
- Harvey, C. R., Liu, Y. and Zhu, H. (2016). ... and the cross-section of expected returns. *Review of Financial
  Studies* 29(1).
- Hurst, B., Ooi, Y. H. and Pedersen, L. H. (2017). A century of evidence on trend-following investing. *Journal of
  Portfolio Management* 44(1).
- Joubert, J. F. (2022). Meta-labeling: theory and framework. *Journal of Financial Data Science* 4(3).
- Lo, A. W. (2002). The statistics of Sharpe ratios. *Financial Analysts Journal* 58(4).
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley (ch. 3 meta-labelling, ch. 4 sample
  weights, ch. 7 purged cross-validation, ch. 8 feature importance, ch. 11–14 backtesting).
- López de Prado, M. and Lewis, M. J. (2019). Detection of false investment strategies using unsupervised learning
  methods. *Quantitative Finance* 19(9).
- Montero-Manso, P. and Hyndman, R. J. (2021). Principles and algorithms for forecasting groups of time series:
  locality and globality. *International Journal of Forecasting* 37(4).
- Moskowitz, T. J., Ooi, Y. H. and Pedersen, L. H. (2012). Time series momentum. *Journal of Financial Economics*
  104(2).
- Niculescu-Mizil, A. and Caruana, R. (2005). Predicting good probabilities with supervised learning. *ICML*.
- Platt, J. (1999). Probabilistic outputs for support vector machines and comparisons to regularized likelihood
  methods.
- Wald, A. (1945). Sequential tests of statistical hypotheses. *Annals of Mathematical Statistics* 16(2).
