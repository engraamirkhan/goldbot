# H-62 online meta-labeller: specification v1 (ml-researcher, 2026-10-25)

Parent: `docs/research/high-volume-learning-2026-10-25.md` section 3 (research director). This file freezes the H-62
protocol and feature list v1 **before any exploration fill or candidate outcome exists**. It dispatches no trial,
reads no label, no holdout (2025-10-01..2026-09-30) and no shadow outcome, and touches no code or config. The research
director signs it; the L3 data lane (`goldbot/research/exploration_data.py`, `training_table`) implements the data
contract in section 7.

**Trial cost: 0 registry trials, K +0.** H-62 never yields a verdict. A model reaches `icm-demo` only through its own
promotion pre-registration (H-63+), at full K.

## 0. What the meta-labeller can and cannot add (read this first)

- **Known failure, not to be repeated (R-07).** Meta-models over primaries with no gross edge gave OOF AUC about 0.50
  (registry #20, report #53; per-family fits). H-40 (box/price-action flags, K_eff 21) gave pooled OOF AUC 0.503.
  Meta-labelling filters an edge; it cannot create one (López de Prado 2018, *Advances in Financial Machine Learning*,
  Wiley, ch. 3; single-source methodology, not an empirical result on gold; SoA A5 in `state-of-the-art.md`).
- **What information H-62 adds that R-07 lacked.** Three things, in order of expected size:
  1. **Cost state.** Labels are net R and pay the spread. At a $2 1m stop the round trip is about 0.1 R
     (program s.1). `em_atr_cost`, `spread_atr`, `spread_rel_median_48` and `hour_utc` vary that cost by a factor of
     about 2 across the day *(design estimate; H-60 measures spread/ATR by hour before any fit)*. A model that skips
     the costliest tercile saves about 0.03 R a scalp trade, about £9 a day of drift at 170 trades and £1.8 per R.
     **That is a cost filter, not directional edge**, and it is reported as such (section 6, the mid-label flag).
  2. **Cross-arm conditioning.** About 24 arms share one model per style with `arm_id` as a feature, so regime x arm
     interactions that no single frozen rule expresses can be learned.
  3. **Unbiased rows.** Random brackets S0/I0/W0 plus a bar-replayed label for every untaken candidate (section 3) give
     a label set with no selection by the policy. R-07's models trained only on what a primary chose.
- **Expected outcome, written now.** Net-label strictly-forward AUC 0.52-0.56, mostly from cost columns; mid-label
  (gross) AUC 0.50-0.52. A model that only matches the per-arm base rate is the likely result for intraday and certain
  for swing in this quarter (section 5, volumes).
- **Graduation bar (R-07 kept).** S6 and I3 (the meta-labelled streams) **cannot graduate** while no member arm's
  primary has passed P4 on its own. Until then the meta-labeller is an exploration ranker and cost filter only.
  Untaken-candidate outcomes are never promotion evidence (architect ruling 2026-10-25, `prereg-2026-10-25-learning-forward.md`).

## 1. Feature list v1 (frozen per quarter, chosen on no outcome)

**How chosen.** No feature-to-label statistic was computed on any data, history or live: the lists are chosen a priori
from mechanism and from what earlier history work already settled. That is stricter than "on history". Sources:
`feature-groups-s1-s2-2026-10-18.md` (cost and hour columns already in `f-079deacc61`; monotone transforms rejected),
`indicator-survey.md`, and the retired list (box/price-action flags out after H-40; SMC objects FVG/order blocks and
EQH/EQL out after R-09/R-13; mean-reversion columns kept only as bulk features, R-09's rule). A history screen to pick
columns (stability selection on H-60 candidates) would be a discovery with its own K_eff; it is **not** part of v1 and
is the research director's call.

**Served-today rule.** v1 uses only columns the live engine already builds: `DEFAULT_FEATURE_NAMES` on the decision
frame and `CONTEXT_FEATURES` (`atr`, `trend_strength`, `moving_averages`, `realised_vol`) on h1/h4/d1 context frames
(`goldbot/engine/runner.py` `_frame`, `_context`). Hence **no `macro_drivers` and no `calendar_events`** in v1: both
are opt-in and not served live, so they would be NaN at serve time (train/serve skew). Swing v2 should add the five
signed macro driver columns once E6 serves them.

**Count.** 38 market columns + `side` + `arm_id` = **40 per style** (`side` counts toward the cap, `model.py`
`MAX_FEATURES`). Signed columns are side-aligned by `side_align` (registry `SIGNED` neutrals). `arm_id` is a LightGBM
categorical. Feature version of the frame: **`f-079deacc61`** (the default set the engine stamps; checked on this branch).

**available_utc rules (every column is bar-derived; no external table is joined in v1).**
- **B (decision bar):** stamped on the decision bar whose open is `ts_utc`; uses that bar's close and earlier bars only;
  `available_utc = ts_utc + TF` (= its `visible_at`) = the candidate's `decision_utc`.
- **C (context):** the h1/h4/d1 bar merged by `merge_higher_tf` with `visible_at <= ts_utc` of the decision bar (its
  open), so context is at least one decision bar stale; `available_utc = visible_at` of that context bar (open + TF;
  d1 at the 13:30 America/New_York feature-day settlement).
- **T (calendar fact):** a function of the decision bar's `ts_utc` alone (DST-aware session clocks); `available_utc =
  ts_utc`.
- `side`, `arm_id`: known at the decision; `available_utc = decision_utc`.
- **Row invariant:** max over a row's columns of `available_utc` <= `decision_utc`; the training table stores that max
  (section 7) and a row breaking it fails the build.

**Tail parity.** The engine computes features on the last `feature_window = 800` decision bars (`runner.py:194`) while
research builds on the whole series. `session_zones` and `market_structure` declare `lookback=1000` (> 800), and at 1m
800 bars is 13 h, so previous-day and previous-week levels can differ between the two. Rule: every v1 column must pass
the tail-parity test L-3 (section 8: effect-size tolerance) at its style's TF; a failing column is replaced by its pre-registered substitute
below, and nothing else. pdh/pdl are therefore not in the scalp list and pwh/pwl not in the intraday list.

### 1a. Scalp (`icm-probe`, scalp pot; decision frame 1m from E1; context h1/h4/d1)

| # | Column | Registry feature (version) | Window at 1m | Signed | Rule | Why |
|---|---|---|---|---|---|---|
| 1 | `em_atr_cost` | expected_move v1 | 14 m ATR / 96 m spread median | | B | cost per unit of move: the main net-R driver at 0.1 R cost |
| 2 | `spread_atr` | microstructure v1 | bar | | B | spread share of the stop |
| 3 | `spread_rel_median_48` | microstructure v1 | 48 m | | B | spread spike vs normal (news, rollover) |
| 4 | `tick_count_z_48` | microstructure v1 | 48 m | | B | activity |
| 5 | `atr_ratio_14_100` | atr v1 | 100 m | | B | short vs medium vol regime |
| 6 | `rv_ratio` | realised_vol v1 | 100 m | | B | realised vol regime |
| 7 | `vol_ts_yz_20_60` | vol_estimators v1 | 60 m | | B | vol term structure (expanding/contracting) |
| 8 | `vol_of_vol_60` | vol_estimators v1 | 60 m | | B | instability |
| 9 | `jump_z` | jumps v1 | 600 m | yes | B | recent jump and its side |
| 10 | `bars_since_jump` | jumps v1 | 600 m | | B | post-jump decay |
| 11 | `hour_utc` | session v2 | | | T | hour cost and liquidity pattern (S-2) |
| 12 | `min_since_london_open` | session v2 | | | T | London open dynamics |
| 13 | `min_since_newyork_open` | session v2 | | | T | NY open dynamics |
| 14 | `dow` | session v2 | | | T | weekday liquidity |
| 15 | `ret_1` | returns v1 | 1 m | yes | B | last-bar move vs side |
| 16 | `ret_4` | returns v1 | 4 m | yes | B | short continuation/reversal |
| 17 | `ret_16` | returns v1 | 16 m | yes | B | as above |
| 18 | `bb_z_20` | mean_reversion v1 | 20 m | yes | B | stretch (S1 fade context) |
| 19 | `rsi14` | mean_reversion v1 | 14 m | yes | B | stretch |
| 20 | `dist_vwap48_atr` | mean_reversion v1 | 48 m | yes | B | distance to rolling VWAP (S1) |
| 21 | `mfi12` | money_flow v2 | 12 m | yes | B | owner's indicator (S4) |
| 22 | `body_pct` | candles v1 | bar | yes | B | decision-bar conviction |
| 23 | `er_10` | regime_stats v1 | 10 m | | B | trend efficiency |
| 24 | `vr_4` | regime_stats v1 | 300 m | | B | variance ratio: trending vs mean-reverting tape |
| 25 | `dist_ema20_atr` | moving_averages v1 | EMA 20 m | yes | B | pullback depth (S5) |
| 26 | `slope_ema50` | moving_averages v1 | EMA 50 m | yes | B | local trend vs side |
| 27 | `dist_high_24_atr` | breakout v1 | 24 m | | B | room to the recent high |
| 28 | `dist_low_24_atr` | breakout v1 | 24 m | | B | room to the recent low |
| 29 | `compression_8_96` | breakout v1 | 96 m | | B | squeeze before ORB (S2) |
| 30 | `sess_high_dist_atr` | session_zones v2 | current session | | B | position in session range |
| 31 | `sess_low_dist_atr` | session_zones v2 | current session | | B | as above |
| 32 | `prev_sess_high_dist_atr` | session_zones v2 | previous session | | B | sweep target (S3) |
| 33 | `prev_sess_low_dist_atr` | session_zones v2 | previous session | | B | as above |
| 34 | `sweep_dir` | market_structure v2 | | yes | B | sweep just happened, and its side |
| 35 | `round_10_dist_atr` | round_numbers v1 | 100 m | | B | $10 level nearby |
| 36 | `h1_dist_ema50_atr` | moving_averages v1 on 1h | 50 h | yes | C | higher-TF trend vs side |
| 37 | `h1_adx14` | trend_strength v1 on 1h | 14 h | | C | higher-TF trend strength |
| 38 | `h4_slope_ema50` | moving_averages v1 on 4h | 200 h | yes | C | swing trend vs side |

Substitutes (L-3 failures only): 32/33 -> `dist_high_96_atr` / `dist_low_96_atr`; 34 -> `bars_since_bos`;
24 -> `er_30`.

### 1b. Intraday (`icm-probe`, intraday pot; decision frame 15m; context h1/h4/d1)

| # | Column | Registry feature (version) | Window at 15m | Signed | Rule | Why |
|---|---|---|---|---|---|---|
| 1 | `em_atr_cost` | expected_move v1 | 3.5 h / 24 h | | B | cost per unit of move |
| 2 | `spread_atr` | microstructure v1 | bar | | B | spread share |
| 3 | `tick_count_z_48` | microstructure v1 | 12 h | | B | activity |
| 4 | `atr_ratio_14_100` | atr v1 | 25 h | | B | vol regime |
| 5 | `rv_ratio` | realised_vol v1 | 25 h | | B | vol regime |
| 6 | `vol_ts_yz_20_60` | vol_estimators v1 | 15 h | | B | vol term structure |
| 7 | `jump_z` | jumps v1 | 6 d | yes | B | recent jump |
| 8 | `em_yz_16_cost` | expected_move v1 | 4 h horizon | | B | expected 4 h move per cost (I0 hold) |
| 9 | `hour_utc` | session v2 | | | T | hour pattern |
| 10 | `session_id` | session v2 | | | T | session |
| 11 | `dow` | session v2 | | | T | weekday |
| 12 | `min_since_newyork_open` | session v2 | | | T | NY open |
| 13 | `ret_4` | returns v1 | 1 h | yes | B | short move vs side |
| 14 | `ret_16` | returns v1 | 4 h | yes | B | as above |
| 15 | `ret_96` | returns v1 | 1 d | yes | B | daily move vs side |
| 16 | `tsmom_score` | tsmom v1 | 6 h-5 d | yes | B | multi-horizon momentum (I1 context) |
| 17 | `im_ldn_ret_atr` | intraday_session v1 | London so far | yes | B | London move vs side |
| 18 | `dist_ema50_atr` | moving_averages v1 | 12.5 h | yes | B | trend location |
| 19 | `slope_ema50` | moving_averages v1 | 12.5 h | yes | B | trend direction |
| 20 | `dist_ema200_atr` | moving_averages v1 | 2 d | yes | B | longer trend location |
| 21 | `adx14` | trend_strength v1 | 3.5 h | | B | trend strength |
| 22 | `ribbon_state` | moving_averages v1 | | yes | B | EMA stack |
| 23 | `bb_z_20` | mean_reversion v1 | 5 h | yes | B | stretch |
| 24 | `dist_vwap48_atr` | mean_reversion v1 | 12 h | yes | B | VWAP distance |
| 25 | `mfi12` | money_flow v2 | 3 h | yes | B | owner's indicator (I2) |
| 26 | `er_30` | regime_stats v1 | 7.5 h | | B | trend efficiency |
| 27 | `donchian_pos_20` | trend_strength v1 | 5 h | yes | B | position in recent range |
| 28 | `compression_8_96` | breakout v1 | 1 d | | B | squeeze (I2 box context) |
| 29 | `sess_high_dist_atr` | session_zones v2 | session | | B | session range position |
| 30 | `sess_low_dist_atr` | session_zones v2 | session | | B | as above |
| 31 | `pdh_dist_atr` | session_zones v2 | previous day | | B | previous-day high |
| 32 | `pdl_dist_atr` | session_zones v2 | previous day | | B | previous-day low |
| 33 | `prev_sess_pos` | session_zones v2 | previous session | yes | B | above/inside/below last session |
| 34 | `sweep_dir` | market_structure v2 | | yes | B | sweep and side |
| 35 | `h1_adx14` | trend_strength v1 on 1h | 14 h | | C | higher-TF trend strength |
| 36 | `h4_dist_ema50_atr` | moving_averages v1 on 4h | 8 d | yes | C | swing trend vs side |
| 37 | `d1_slope_ema20` | moving_averages v1 on 1d | 20 d | yes | C | daily trend vs side |
| 38 | `d1_atr_ratio_14_100` | atr v1 on 1d | 100 d | | C | daily vol regime |

Substitutes: 31/32 -> `dist_high_96_atr` / `dist_low_96_atr`; 34 -> `bars_since_bos`.

### 1c. Swing (`icm-probe`, swing pot; decision frame 4h; context d1)

| # | Column | Registry feature (version) | Window at 4h | Signed | Rule | Why |
|---|---|---|---|---|---|---|
| 1 | `em_atr_cost` | expected_move v1 | 2.3 d / 16 d | | B | cost per unit of move (small at 4h) |
| 2 | `spread_atr` | microstructure v1 | bar | | B | spread share |
| 3 | `atr_ratio_14_100` | atr v1 | 17 d | | B | vol regime |
| 4 | `rv_ratio` | realised_vol v1 | 17 d | | B | vol regime |
| 5 | `vol_ts_yz_20_60` | vol_estimators v1 | 10 d | | B | vol term structure |
| 6 | `vol_of_vol_60` | vol_estimators v1 | 10 d | | B | instability |
| 7 | `jump_z` | jumps v1 | 100 d | yes | B | recent jump |
| 8 | `atr14_pct` | atr v1 | 2.3 d | | B | vol level relative to price |
| 9 | `dow` | session v2 | | | T | weekday (swap on Wednesday) |
| 10 | `hour_utc` | session v2 | | | T | which 4h block |
| 11 | `ret_4` | returns v1 | 16 h | yes | B | short move vs side |
| 12 | `ret_16` | returns v1 | 2.7 d | yes | B | as above |
| 13 | `ret_96` | returns v1 | 16 d | yes | B | as above |
| 14 | `tsmom_z_24` | tsmom v1 | 4 d | yes | B | momentum (W1; Moskowitz, Ooi & Pedersen 2012, *J. Financial Economics* 104(2) 228-250; 58 futures incl. gold, 1985-2009, gross of costs; replicated across asset classes) |
| 15 | `tsmom_z_120` | tsmom v1 | 20 d | yes | B | as above |
| 16 | `tsmom_z_480` | tsmom v1 | 80 d | yes | B | as above (the H-01 slow horizon) |
| 17 | `tsmom_agree` | tsmom v1 | | yes | B | horizon agreement |
| 18 | `dist_ema50_atr` | moving_averages v1 | 8 d | yes | B | trend location |
| 19 | `dist_ema200_atr` | moving_averages v1 | 33 d | yes | B | long trend location |
| 20 | `slope_ema50` | moving_averages v1 | 8 d | yes | B | trend direction |
| 21 | `slope_ema200` | moving_averages v1 | 33 d | yes | B | long trend direction |
| 22 | `adx14` | trend_strength v1 | 2.3 d | | B | trend strength |
| 23 | `ribbon_state` | moving_averages v1 | | yes | B | EMA stack |
| 24 | `rsi14` | mean_reversion v1 | 2.3 d | yes | B | stretch |
| 25 | `bb_z_20` | mean_reversion v1 | 3.3 d | yes | B | stretch |
| 26 | `mfi12` | money_flow v2 | 2 d | yes | B | owner's indicator (W2) |
| 27 | `er_30` | regime_stats v1 | 5 d | | B | trend efficiency |
| 28 | `hurst_128` | regime_stats v1 | 21 d | | B | persistence |
| 29 | `donchian_pos_20` | trend_strength v1 | 3.3 d | yes | B | range position |
| 30 | `dist_high_96_atr` | breakout v1 | 16 d | | B | room to the 16-day high |
| 31 | `dist_low_96_atr` | breakout v1 | 16 d | | B | room to the 16-day low |
| 32 | `pwh_dist_atr` | session_zones v2 | previous week | | B | previous-week high |
| 33 | `pwl_dist_atr` | session_zones v2 | previous week | | B | previous-week low |
| 34 | `structure_state` | swings v1 | | yes | B | HH/HL vs LH/LL |
| 35 | `bos_dir` | market_structure v2 | | yes | B | last break of structure |
| 36 | `d1_dist_ema50_atr` | moving_averages v1 on 1d | 50 d | yes | C | daily trend vs side |
| 37 | `d1_adx14` | trend_strength v1 on 1d | 14 d | | C | daily trend strength |
| 38 | `d1_rv_20` | realised_vol v1 on 1d | 20 d | | C | daily realised vol |

Substitutes: 28 -> `vr_8`; 35 -> `bars_since_bos`. **v2 candidates (next freeze, needs E6 serving):** the
`macro_drivers` v5 signed columns `macro_real_yield_chg20`, `macro_dollar_chg20`, `macro_vix_chg5`, `macro_spx_dd20`
and `macro_gvz` (FRED `available_utc` publication rule, `goldbot/data/macro.py`), replacing 6, 8, 27, 28, 37.

Column names verified on this branch against the registry output on 2026-10-25 (38 distinct per style, all present).

## 2. Labels

- **Target:** `y = 1[label_sim_r > 0]`, net R on the arm's own barriers (stop, target, time limit), from the decision.
- **`label_sim_r`, every candidate (taken or not):** bar replay with the research label code (`triple_barrier_1m`,
  entry at the next 1m open at ask/bid, exit on 1m barriers, stop first when both barriers are inside one 1m bar), the
  arm's swap charged per rollover, and the **cost model version frozen at the model's cutoff** (spread from the bar;
  slippage and commission from the measured probe table *as of* the cutoff, never a later table: the cost table itself
  is point in time). Favourable slippage is never credited.
- **`label_fill_r`, taken candidates only:** the broker-reconciled net R (E7). Used for parity and OPE (section 6),
  never as the training target, so training does not depend on the policy.
- **`label_mid_r`:** the same replay on mid prices with no costs: the gross diagnostic (section 6).
- **Admissible:** label resolved (`label_exit_utc` <= the fit cutoff), not quarantined by H-52 (history gap, parity),
  `exploration = True`. Unresolved labels at a cutoff are excluded, never censored to the time limit.
- **Weights:** `uniqueness_weights` (average inverse concurrency over the label life), computed within the style.

## 3. Cross-fitting (calibration and diagnostics only)

- **Unit:** `session_day` = the date of `decision_utc` + 7 h in America/New_York (the day rolls at 17:00 NY, the
  metal's daily break). All arms' candidates on one session day share a fold.
- **Folds:** 5 **contiguous** blocks of session days (not random days: contiguous blocks are conservative against
  regime autocorrelation).
- **Purge:** drop a training row whose [`decision_utc`, `label_exit_utc`] overlaps the test block widened by
  `max_hold(style)` on both sides; `max_hold(style)` = the longest time limit of any arm in the style in the E2
  registry (at design: scalp 30 1m bars = 30 min for S0, larger for 5m arms; intraday 96 x 15m = 24 h; swing 24 x 4h =
  4 d; the registry value wins).
- **Embargo:** drop training rows that start within 1 session day (scalp, intraday) or 5 (swing) after a test block.
  In k-fold cross-fitting, training rows after the test block exist, so this is not the no-op it is in walk-forward
  (audit F9).
- **Model per fold:** fit on the other 4 blocks (purged, embargoed), score the held-out block: these out-of-fold (OOF)
  scores feed the calibrator and the diagnostics only.

## 4. Model, calibration, strictly forward scoring

- **Model class:** `MetaLabelModel` (LightGBM binary, side-aligned) with H-62 parameters, more regularised than
  `DEFAULT_PARAMS` for 3k-20k rows: `num_leaves=7, min_child_samples=100, learning_rate=0.03, n_estimators=200,
  feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10`, `arm_id` categorical. Fixed; no tuning
  inside H-62 (a tuning loop is a search with no K).
- **Served score:** the mean of the 5 fold models' raw scores, then the calibrator. No refit on all rows: the
  calibrator is fitted on single-model OOF scores, and a full refit is more confident than any of them (a known
  calibration mismatch). The 5-model mean narrows that gap; the forward ECE check (section 5) measures what is left.
- **Calibration:** isotonic on the pooled OOF scores of **all admissible candidates, taken and untaken**, against
  `label_sim_r > 0`, weights as in section 2; Platt below 500 rows (`MIN_ISOTONIC_ROWS`). Each OOF row's calibrator set
  is purged by label exit as in audit F2 (`fix/crossfit-calibration-purge`). Calibrated per style, with `arm_id` inside
  the model; per-arm calibration is not fitted (too few rows per arm).
- **Strictly forward:** model version `m` has a data cutoff `T_m` (Friday 21:00 UTC market close) and activation
  `A_m` (Sunday 22:00 UTC open), `A_m > T_m`. A candidate at `decision_utc = t` is scored only by the newest version
  with `A_m <= t`, and its row logs `meta_model_version`. Any logged row with `T_m >= t` fails test L-6. OOF scores are
  never served, and never used for allocation or OPE.
- **Champion zero:** the per-arm base rate (beta-binomial shrunk to the style rate, prior n 50). This is the model to
  beat; it adds nothing beyond `arm_id`.
- **Challenger H0 (history):** the same model class fitted once on H-60's historical candidates
  (2015-01..2025-09-30, label exits before 2025-10-01, era costs; no holdout row), frozen, entered in the first
  comparison. Its cost columns come from the Dukascopy spread, the live ones from IC Markets: if the population
  stability index (PSI) of `em_atr_cost` or `spread_atr` between its training set and the first 2 live weeks is
  above 0.25, H0 is scored and reported but cannot become champion.

## 5. Retrain cadence and champion/challenger

- **Retrain:** Sundays, per style, when the style has **>= 1,500 new admissible labelled candidates** since the last
  fit and **>= 3,000 in total**. Training window: expanding, all admissible live rows since the style's first arm went
  live. The feature list and parameters never change inside a quarter.
- **Expected cadence (design volumes, program s.1):** scalp about 170 trades a day plus untaken candidates, so about
  weekly; intraday about 105 a day, so about every 3 weeks; **swing 2-3 a day never reaches 1,500 in the quarter: the
  swing meta-labeller stays at champion zero (or H0, if it wins) all quarter**, and its 80/20 ranking runs on that.
- **Challenger evaluation:** a new fit scores in shadow (logged, never acting) for the **next 2 weeks after its
  cutoff, unseen in training**; the champion keeps acting. Metric: per-row log-loss on `label_sim_r > 0` over **all**
  candidates in the window (taken and untaken, so no selection bias and no weighting needed).
- **Replace the champion only if all hold:** (a) n >= 1,000 candidates in the window (if not, the window extends a
  week at a time, up to 4 weeks, then the challenger lapses); (b) mean log-loss improvement
  delta = LL(champion) - LL(challenger) >= **0.001 nats per row**; (c) the 95% lower bound of delta by session-day
  block bootstrap (10,000 draws) > 0; (d) challenger expected calibration error (ECE, 10 bins) <= 0.03.
  Why 0.001: a Gaussian score with AUC 0.53 (d' about 0.11) at a base rate near 0.4 is worth about
  d'^2 x p(1-p)/2, about 0.0013 nats per row, so the margin is set at the smallest edge worth serving. With 2 weeks of
  scalp (about 1,700 rows, per-row delta SD about 0.05 *(design estimate)*), the standard error is about 0.0012:
  most challengers will not replace, which is intended.
- **Multiple comparisons, stated:** about 26 scalp comparisons a quarter; at the 5% one-sided bootstrap level, one
  or two spurious replacements a quarter are expected. The cost is a slightly worse exploration ranker on demo, never
  a promotion. K stays 0.
- **At most 2 challengers in shadow per style.** A challenger never reaches `icm-demo`; S6/I3 start only once a
  champion other than champion zero exists in their style.

## 6. Off-policy evaluation with logged propensities (80/20 ranked/random)

- **Policy, per arriving candidate within its arm:** if the arm has a free slot and its caps allow a fill, draw
  `u ~ U(0, 1)` (logged). Ranked branch (`u < 0.8`): take iff calibrated `meta_p >= tau_arm`, where `tau_arm` is the
  arm's cut that fills its allocated share, frozen at the activation of the serving model from the previous OOF
  distribution. Random branch (`u >= 0.8`): take with probability `pi_rand_arm` (the arm's target take rate).
  **Propensity** `mu = 0.8 x 1[meta_p >= tau_arm] + 0.2 x pi_rand_arm`, logged with `branch`, `tau_arm`,
  `pi_rand_arm`, `meta_p`, the meta and bandit versions. Under champion zero all `meta_p` within an arm are equal,
  so `mu = pi_rand_arm` throughout (logged as `branch = uniform`). When the arm takes everything (under its cap),
  `mu = 1`.
- **Blocked candidates** (no slot, a £ cap, the cumulative-R stop, RiskGate refusal, stopped arm) are logged with
  `mu = 0` and the reason. **The off-policy estimate is conditional on the caps:** blocked candidates are excluded
  because slot availability depends on the policy (a different policy would have filled different slots), so V(pi') is the value
  of pi' under the same ADR 0043 pots and slot limits, not an unconditional value of the rule. They keep `label_sim_r` (training uses them) but are **outside OPE**: no target policy can be
  evaluated where the logging policy had no choice, so every target policy is evaluated under the same caps.
- **Estimator, two layers:**
  1. **Replay value on simulated labels:** V_sim(pi') = sum over unblocked candidates of pi'(take | x) x
     `label_sim_r` / n. Exact up to the label model, because every candidate has a replay label; no propensity needed.
  2. **Execution gap:** g = `label_fill_r` - `label_sim_r`, seen on taken rows only. Its mean under pi' is estimated
     by self-normalised IPW (SNIPS) with weights pi'(i) / mu(i), and by doubly robust (DR) with an outcome model
     g_hat(arm, hour) (cell means, fitted on the earlier weeks only). V(pi') = V_sim + gap estimate; reported as both.
  Uncertainty by session-day block bootstrap. Report the effective sample size (ESS) of the weights; ESS < 30% of n
  reads "OPE unreliable".
- **Logging checks:** realised take frequency by propensity decile within 2 SE of the logged mean; weights summing to
  about n; no taken row with `mu = 0`; `mu` >= 0.2 x `pi_rand_arm` for every unblocked candidate (positivity).
- **Cost-driven flag:** every reported AUC and OPE value is shown twice: on `label_sim_r` (net) and on `label_mid_r`
  (gross). Net AUC lower bound > 0.50 with gross AUC lower bound <= 0.50 is labelled **"cost filter"**: allowed for
  exploration ranking, never cited as directional edge.
- **OPE is not promotion evidence.** It decides exploration allocation and the H-62 reading only.

## 7. Data contract for L3 (`goldbot/research/exploration_data.py`, `training_table`)

One row per candidate (taken or not). Names below are the spec's; if L3 has already fixed different names, L3's names
stand and this table is amended to match (one source of truth, no aliases).

| Column | Meaning |
|---|---|
| `candidate_id` | unique id (arm, decision bar, side) |
| `decision_utc` | decision bar close; the as-of point of every feature |
| `session_day` | section 3 |
| `style`, `host`, `arm_id`, `arm_config_hash` | from the E2 arm registry |
| `side` | +1 long / -1 short |
| the 38 style columns | exactly the registry column names of section 1 (context columns with `h1_`/`h4_`/`d1_`) |
| `feature_version` | registry hash of the frame (`f-079deacc61` for v1) |
| `feature_list_id` | `h62-v1-scalp`, `h62-v1-intraday`, `h62-v1-swing` |
| `available_utc_max` | max `available_utc` over the row's columns; must be <= `decision_utc` |
| `taken`, `blocked_reason`, `branch`, `u`, `mu`, `tau_arm`, `pi_rand_arm` | section 6 |
| `meta_p`, `meta_model_version`, `bandit_version` | as served |
| `label_exit_utc`, `label_sim_r`, `label_mid_r`, `label_fill_r`, `cost_model_version` | section 2 |
| `exploration` | always True; excluded from `population.py` and every trial |

- **Snapshot, not recompute:** live rows store the features as computed at `decision_utc` (E6); `training_table` reads
  the snapshot, never rebuilds live rows. Rebuilding is for history (H0) and for test L-3 only.
- **Every join** of a feature table to candidates uses `asof_join` on `available_utc` (`goldbot/data/store.py`); the
  `training_table` builder calls `check_feature_cap` on the column list + `side` + `arm_id`.
- **The lists live in code once** (a constant keyed by `feature_list_id`); a test asserts it equals section 1.

## 8. Leakage checklist (each one a test or a logged check; all must pass before a fit is served)

| # | Check | Pass |
|---|---|---|
| L-1 | `lookahead_check` (`pipeline.py`) on each style's decision TF with the v1 columns | 0 columns |
| L-2 | Row invariant `available_utc_max <= decision_utc` over the whole training table | 0 rows |
| L-3 | **Tail parity:** the 800-bar engine tail vs the full-history build on >= 2,000 sampled 2024-2025 decision rows per style | Effect-size tolerance, not bit equality. Per column, d = tail minus full-history value, scaled by the column's SD on the full history: flags and categorical columns equal; continuous columns need NaN pattern equal, >= 99.5% of rows with abs(d) <= 0.02 SD and no row above 0.25 SD. Model-level: a fitted model scores both frames and abs(delta p) <= 0.005 on >= 99% of rows. A failing column is replaced by its pre-registered substitute |
| L-4 | **Label null:** labels permuted in whole session-day blocks (never shifted by one bar: a one-bar shift leaves neighbouring labels nearly identical, so it is no null; TRACEABILITY F8 records a shifted-label AUC of about 0.7 without leakage, which is how R-01's flagged 0.72 arose), 200 permutations | the real-feature OOF AUC distribution under permutation centred on 0.50 (mean within 0.005) |
| L-5 | Purge/embargo audit: no training row's label interval overlaps a test block +- `max_hold`; no row starts inside an embargo | 0 rows |
| L-6 | Strictly forward: every scored row's model has `T_m < decision_utc` and `A_m <= decision_utc` | 0 rows |
| L-7 | Calibrator fitted on OOF scores only, its prior set purged by label exit (audit F2) | test |
| L-8 | Cost model point in time: `cost_model_version` effective date <= the fit cutoff | 0 rows |
| L-9 | Unresolved labels at the cutoff excluded (not censored) | 0 rows |
| L-10 | Holdout: no row with `decision_utc` in 2025-10-01..2026-09-30 in any fit, H0 included | 0 rows |
| L-11 | Feed parity: PSI of each column, history (Dukascopy) vs live (IC Markets), first 2 weeks; > 0.25 flagged (spread, tick-count, `mfi12` expected) | reported; blocks H0 per section 4 |
| L-12 | Serving parity: the served frame's `feature_version` equals the model's (the engine refuses otherwise) | engine refusal count 0 |
| L-13 | Exploration isolation: no exploration row in `population.py` evidence or any trial input | 0 rows |
| L-14 | Cross-arm duplicates (the same bar and side on two arms) sit in one fold | by construction (session-day folds), asserted |

## 9. Reading rule (written before any data) and stop rule

- **H-62 v1 reads at the end of 2027Q1 or after 12 live weeks in scalp, whichever is first.** "The meta-labeller adds
  information" in a style iff its champion at that date is not champion zero **and** its strictly forward AUC on
  `label_sim_r > 0` over >= 8 weeks is >= 0.53 with a session-day block-bootstrap lower bound > 0.50. The gross AUC is
  reported next to it with the section 6 cost-filter flag.
- **Stop:** if no challenger has displaced champion zero in scalp by then, S6 and I3 do not launch, the ranker stays
  at base rates, and feature list v2 is not proposed without information v1 lacked (for example live tick or
  order-book state, or the macro columns for swing), never as a re-shuffle of the same registry columns.
- None of this is a trial verdict; K is unchanged either way.

## 10. Risks for the research director (raised, not decided here)

1. **Retired-family neighbours among the arms (arms-2026-10-25.md ids).** S3A/S3B (sweep-and-reclaim at 5m) are a neighbour of R-14, whose
   rule says "no neighbour tests". S4 (box break+close+MFI at 5m) and I2A/I2B neighbour R-15 ("no further trial without
   a new mechanism or measured costs below half the prior"). I1 and W1 tsmom 1h/4h sit on R-06/R-10 ("re-scoring
   tsmom 1h/4h under cheaper costs" retired). Exploring them is a 0-trial act, but their **graduation** is void unless the evaluator records,
   before the read, that the retirement row's re-entry condition is met (prereg 2026-10-25-high-volume, graduation section).
2. **Engine tail is shorter than the registry lookback**; engine follow-up item: the engine tail (`runner.py:194`, 800 bars) must be >= the 1,000-bar lookback (`feature_window = 800` < `lookback = 1000` for
   `session_zones`, `market_structure`, `order_blocks`). This affects any live model using those columns, not only
   H-62. L-3 measures it; the architect owns the fix (raise the tail or the lookback rule).
3. **Swing has no live learning this quarter** (2-3 labels a day). Its meta-labeller is base rate or H0 only.
4. **Per-row delta noise** (section 5) is a design estimate; the first scalp fortnight measures it. If the SD is above
   0.1, champion/challenger cannot separate 0.001 nats in 2 weeks, and the margin rule stands anyway (the champion
   simply stays).

Owner-only decisions: none is taken here. Hosts, accounts and £ caps are H-61's and the program's; nothing in this
spec changes sizing, limits or live trading.

## Delegation (none needed)

The spec is one judgement piece; the implementation is L3's (data contract, L-1..L-14 tests) and E6/E8's (snapshot,
serving slot).
