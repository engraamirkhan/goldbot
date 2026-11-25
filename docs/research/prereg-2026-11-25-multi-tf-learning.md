# Pre-registration and design card: multi-timeframe learning, retraining and reinforcement (H-102, spec `mtf-v1`)

Principal ML researcher, 2026-11-25. Written and committed **before any data under this card is read**: no label, no
feature row and no candidate count under `mtf-v1`. The staff slices in section 13 used outcome-blind counts only.

**Status: proposed.** The quant-reviewer signs section 12 before any stage runs. This card dispatches no trial, books
no K and does not touch the holdout.

**Owner directives, 2026-11-25:**
- **D1.** Models learn, retrain and reinforce on 15m, 1h, 2h, 4h, 1d and 1w.
- **D2.** Keep every existing timeframe (1m, 5m, 30m). Nothing is dropped or retired by this card. Pooling serves
  analysis power only, never deletes a timeframe's arms or labels.
- **D3.** Every model is first trained on history, so it predicts from day one. Live labels then update it. The gate
  judges only out-of-sample or forward data.
  - **Owner, same day: "all the timeframes should also be trained on historical data, no exception."**
  - Section 4.0 states this as rule HP-0. HP-0 has no exceptions.
- **D4.** Weights are fitted to an explicit objective chosen by the experts.
- **D5.** "No limits for demo accounts". Demo caps and ADR 0004 §4 no longer bind demo arms on any timeframe.
  Statistical gates (power, Holm, sealed windows) are not limits in that sense and stay. RiskGate stays the only path
  to an order (CLAUDE.md non-negotiable). Real money stays owner-only.

**Builds on:**
- H-100 `sr-v2` (`prereg-2026-11-24-self-retrain.md`, `goldbot/research/self_retrain.py`);
- H-101 `zoo-v1` (`prereg-2026-11-25-model-zoo-fusion.md`, `history_replay.py`, `model_zoo.py`);
- ADR 0085 (power-first), ADR 0067 + `amendment-w3.md` (windows), ADR 0061 (learning class), ADR 0095 (cohort).

## 0. The plan in one table (gold; estimates until Stage 0 counts replace them)

Abbreviations: "live" = brain `exploration_obs`, last 30 days, annualised (x 12.2). "Hist." = outcome-blind history
counts from the staff slices (section 13). U = label-uniqueness n_eff (section 4.4).

| tf | Live cand./yr | Hist. learnable flow | Learning source | Judged unit (Stage L, history) | Daily weights | Refit (free) | Judged read | Power at confirm, delta 0.10 R | K |
|---|---|---|---|---|---|---|---|---|---|
| 1m | ~56,000 metal (rest: 0 labels) | very large | history base + live | **U-SCALP**: 1m + 5m signal arms pooled, p <= 10 | gate-passed only | weekly if >= max(500, 10 %) new labels | at most every 2nd week, >= 2,400 OOS rows | ~1.0 (history); live 0.5 at 2,400 rows | +1 (Q1) |
| 5m | ~39,000 | very large | history base + live | in U-SCALP | gate-passed only | as 1m | as 1m | as 1m | in U-SCALP |
| 15m | ~47,000 (gold strategy ~4,000) | RBR15 1,701/yr, F1A 104/yr | history base + live | sr-v2 per arm (live, grandfathered) -> **sr-v3 history-pretrained** (HP-0); H-101 (history) | existing | sr-v2 Sun 15:00 | sr-v2 | sr-v2 / H-101 | 0 new |
| 30m | ~12 (no running arm) | F1A30 ~152/yr (U 152) | history; forward replay confirms | **U-30m**: F1A30 (+ control F1C30) | gate-passed only | weekly | at most monthly, >= 100 new OOS rows | ~0.99 (C U ~1,180) | +1 (Q1) |
| 1h | ~7,100 (499/583 controls) | G1A ~113, PDC1 ~62, MB1H ~129 /yr | history base + live | **U-1h**: G1A, PDC1, MB1H pooled | existing for engine arms | weekly | at most monthly, >= 100 new | ~1.0 | +1 (Q1) |
| 2h | 0 (new tf) | ML2H ~139, MP2H ~82 /yr | history; forward replay confirms | **U-2h**: ML2H, MP2H pooled | gate-passed only | weekly | at most quarterly, >= 100 new | ~0.95 | +1 (Q1) |
| 4h | 69 rows in 24 d; 0 since 11-18 | DON4 ~70, PDC4 ~75, KSQ4 ~12, BXF4H ~38 /yr | history; forward replay monitors | **U-4h**: DON4, PDC4, KSQ4, BXF4H pooled | gate-passed only | weekly (drift report) | **quarterly slots only** | filter leg ~0.76 at T (z 1.28); **marginal**, may be NOT STARTED | +1 (Q1) |
| 1d | 0 | ~20/yr/arm, n_eff ~n/H | history | **CONTEXT-ONLY**: 1d state enters faster models as inputs; descriptive cell | n/a | monthly | **never judged** (test_old n_eff < 200 in every class) | < 0.25 | 0 |
| 1w | 0 | ~6/yr/arm | history | **CONTEXT-ONLY**, pooled with 1d as SWING for description only | n/a | quarterly | **never judged** | < 0.10 | 0 |

**K summary:**
- **2026Q4: 0 booked by this card.** The upper bound is +3, only if the quant rules that a new row costs K at
  registration (rows ML2H, MP2H, F1A30).
- **2027Q1: +5.** One Stage L candidate per unit (U-SCALP, U-30m, U-1h, U-2h, U-4h). Each is booked only if its
  Stage 0 power passes **and** H-101 is not null.
- **Live judged reads:** +1 per unit per read, only for units that passed history. At most 5 a quarter; about 0.25
  expected.
- **Optional 2027Q1: +2** for two demo learning-class swing arms (section 6).
- **Q4 cap for multi-tf: 20** (power-k slice). Q4 room: about 65 after H-101.

## 1. Prior evidence and known failures (not re-tested)

**What is new is not the entry rules.** It is three things:
- **(a)** a history-pretrained, live-updated filter on every timeframe;
- **(b)** a money objective (section 4.1);
- **(c)** higher-timeframe state as inputs.

"Retired arm + learned filter" is a new hypothesis by the PD + RD ruling of 2026-11-24 (the sr-v2 precedent). **A
timeframe split of the same inputs is not new information.** That is why every judged read here waits on H-101
(section 4.6).

| ID | Rule / tf | Sample, instrument | Net | Use here |
|---|---|---|---|---|
| R-03, R-04 | trend pullback, range breakout, 1h | XAUUSD 2010-25 | yes | none |
| R-06 / R-10 | tsmom 1h/4h (gross +0.061 R, t 2.63, net -0.079 R); 1d mutations | XAUUSD 2010-25 | yes | **no tsmom row at any tf.** W1A cannot be replayed (`KeyError hold_bars`) and is dropped label-blind |
| R-11, R-15, H-80 | box_break_close 15m-4h, box exits: net -0.20 to -0.30 R | XAUUSD 2010-25-09, n 5,355-5,684 | yes | existing rows only (MB1H, BXF4H); **no new box row** (no 2h box) |
| H-99 | redbox 1h/4h: net -0.16 R, Delta vs random -0.008 / -0.054 R | XAUUSD 2010-17 | yes (W2 bps) | **no red-box row** on 30m / 2h / 4h, not even as filter rows (sr-v2 also excluded RBR1H/RBR4H) |
| R-16 / H-84 | macro direction 4h/1d | XAUUSD 2010-17 | yes | macro levels only as inputs, in a later spec (section 11) |
| R-18 / H-85 | discovery v2 1h/4h/1d: 0 survivors | XAUUSD | yes | none |
| R-19 / H-86..88 | weekly COT / ETF rules | XAUUSD 2006-25 | yes | columns only, as context, in a later spec |
| H-01 | slow TSMOM (gross +0.166 R, t 2.04, net +0.002 R) | XAUUSD | yes | **1d Donchian / breakout count as the same family**, so 1d is context-only |
| POS1-A1, L1, TREND-XASSET | daily/weekly trend on XAU and cross-asset | 2000s-2025 | yes | **no 1w price-only trend read** |
| Sprint-200 ML-LR-4h / GBM-4h | OOF AUC 0.525 / 0.515 | XAUUSD 2010-17 | yes | no 4h algorithm search |
| H-82, A5, cost-audit fails | scalp S5/Z4/Z6/Z7, Z1P/Z2P/Z3D speed limit | XAUUSD 2010-17 and demo | yes | do-not-feed list (section 3) |
| R-07, R-20/A27, MLNET V3, MLEXEC, PROBTREND, H-62, specialists' LightGBM (AUC 0.44) | meta-models | XAUUSD | yes | the family risk this card carries |
| H-61 | Thompson within style = equal | demo, 24 broker-days | yes | allocator stays `equal` until R5 (section 6) |

**Live:** GD-1 A1 (H-98) is a schedule arm, not a filter target. It is untouched.

**Literature (methods only; none is a gold or net-of-cost edge claim):**
- Peduzzi et al. 1996, *J Clin Epidemiol* 49(12), and Riley et al. 2019, *Stat Med* 38(7) / 2020, *BMJ* 368:m441:
  sample size per input.
- López de Prado 2018, *Advances in Financial ML* (Wiley), ch. 3-4 and 7: meta-labelling, sample weights,
  uniqueness, purging.
- Hansen & Hodrick 1980, *JPE*, and Newey & West 1987, *Econometrica*: overlap.
- Holm 1979, *Scand J Statist* 6(2).
- Howard et al. 2021, *Ann Statist* 49(2): confidence sequences.
- Elkan 2001, IJCAI, "The foundations of cost-sensitive learning": weighted surrogate for example-dependent costs.
- Huber 1964, *Ann Math Statist* 35(1).
- Rockafellar & Uryasev 2000, *J Risk* 2(3): CVaR.
- Moody & Saffell 2001, *IEEE TNN* 12(4): differentiable Sharpe.
- Burges et al. 2005, ICML (RankNet).

## 2. Data sources

**Four label streams. They are never mixed inside one gate.**
1. **History replay.** `history_replay.py`, Dukascopy XAUUSD 1m, W2 bps cost of record plus swap in `move_usd`.
   **It carries all learning (D3).**
2. **Engine live.** `exploration_obs` + the r2 replay, measured broker legs: 1m / 5m / 15m / 1h. This is sr-v2's
   stream.
3. **Forward replay** (new, section 9). The history-replay code runs daily on the brain's stored broker 1m bars, with
   the measured cost of record. It makes point-in-time forward labels for research-only rows **without the engine or
   orders**. It covers 30m / 2h / 4h, plus 1d / 1w for description.
4. **Demo fills** (D5). Learning-class arms on any tf. They are used for cost, swap and parity measurement only, never
   as filter labels, so the allocation cannot select its own evidence.

**The day ceiling.** At the ADR 0085 prior rho_day 0.25, one instrument yields at most about 4 n_eff a day, however
many candidates fire. The scalp slice measured rho_day 0.000-0.016 on 15-60 min null brackets; the 15m-30m slice found
that rho 0.25 overstates n_eff about 2.9x for 24 h holds. Section 4.4 therefore replaces the prior with an overlap
design effect for every pooled read.

**Live is thin above 15m:**
- 4h: 0 rows since 11-18.
- 30m: 1 candidate in 30 days.
- 2h, 1d, 1w: 0.
- 1h: 499 of 583 are controls.

So for 30m-1w, live (forward replay) is a **monitor and slow confirm**, never the training set:
- 4h forward reaches confirm power after about 1.5 years on the filter leg;
- 1d and 1w never do.

## 3. Arms per timeframe

**Rules for adding rows:**
- No new rule body. No red-box, tsmom or box row on a new timeframe. No new 1m row (`ONE_MINUTE_ALLOWED` unchanged).
- Every new row is **research-replay only**, in a new `config/research/multi_tf_arms.yaml` that the engine never
  loads (a test pins this).
- Each row records every candidate, taken or not, and has a matched-stop control on its own tf (`self_retrain`
  STOP_MATCH, MIN_CONTROL 50).
- Hashes follow `arms.arm_hash`; an edit means a new id.

| tf | Rows (existing re-recorded via research replay; **new** in bold) | Control | Do not feed |
|---|---|---|---|
| 1m, 5m | U-SCALP signal arms: Z1P, Z2P (1m); Z3D, Z5P, Z9D, Z10D, S1A, S1B (5m). Engine rows unchanged; their sr-v2 forward rows stay unread | S0 / S0R, LC* (baseline only) | S5A/B, Z4*, Z6*, Z7*, Z8P, S2-S6 (H-82/A5); controls are never training rows |
| 15m | unchanged (sr-v2, H-101) | existing | |
| 30m | **F1A30** `hurst_band_break` 30m {hold_bars 2, dedup_bars 2, F1A's params}. It adds +118 % unique events over F1A 15m; redbox adds +4 % and session range +11 % | **F1C30** (F1A30's matched control), **IEX30** `random_bracket` {minutes [0, 30], window 07:00-20:30, hold_bars 8} | RBR30 (H-99), GS30 (duplicate of 15m, horizon correlation 0.94); MB30, BXR/BXT/BXF30, WFRI stay retired |
| 1h | G1A (`level_break`, live), PDC1 (`pdc_break`), MB1H | VEX1H / IEX1H | RB1H/RBR1H; specialists X*/VX* (not replayable) |
| 2h | **ML2H** `level_break` 2h (G1A's params); **MP2H** `pdc_break` 2h (PDC1's params) | **MC2H** `random_bracket` 2h {stop_atr 1.0, hold_bars 36} | tsmom 2h (whitelist refuses it; R-06), box 2h, clock_hold 2h (same trade), squeeze 2h (about 25 a year) |
| 4h | DON4, PDC4, KSQ4, BXF4H (= W2 candidates) | W0 | RB4H / RBR4H (H-99), W1A |
| 1d | DOND (`donchian_break`), recorded for context and description only | **MC1D** `random_bracket` 1d {stop_atr 1.5, hold_bars 10} | any judged 1d direction read |
| 1w | **MD1W** `donchian_break` 1w {hold_bars 8}, recorded for context and description only | **MC1W** `random_bracket` 1w {stop_atr 1.5, hold_bars 8} | any 1w judged read |

**Fixed before any label is read:**
- **Price-scaled stop clamps.** In history and forward replay every `min_stop_usd` / `max_stop_usd` is applied as bps
  of the decision price: cap_bps = cap_usd / 4,000 x 1e4. Without this, a dollar clamp makes history follow the gold
  price level. Example: F1A had 379 candidates in 2011, 1 in 2017, and runs at about 940 a year live. Engine rows keep
  their USD clamps, which are equal at $4,000.
- **One weekend rule for history and forward replay.** Every 4h / 1d / 1w label exits at the engine's Friday cut
  (`arm_rules.follows_friday_cut` applied to all rows), so the forward confirm tests the same label as history.
- **Bar coverage.** `bar_fill` = minutes / tf minutes is recorded on 2h and 4h. Decisions on bars below 80 % coverage
  are kept, and a stubs-excluded sensitivity is reported, never gated. 2h floors on UTC, nesting exactly into 4h; 9.3 %
  of 2h bars are stubs because the gold session is 23 h.
- **Warm-up:** 2h 120 days, 1d 400, 1w 1,100 (ADR 0067 warm-up rule). A row without full rule state is void.
- **Cost in R** (W2 0.575 bp round trip plus 1.75 bp overshoot, x P(stop) of about 0.6, at a 1 ATR_tf stop;
  2014-17):
  - 1m 0.07-0.09 R; 5m 0.05-0.06 R;
  - 1h 0.06-0.09 R; 2h 0.044-0.065 R; 4h 0.032-0.046 R;
  - 1d: spread about 0.01 R plus long swap about 0.007 R a night (about 0.07 R over 10 nights);
  - 1w: swap dominates.

  Fast timeframes die on cost. Slow timeframes die on power, and on swap for longs.

## 4. Models, objective, gates and reading rule (written before any data is seen)

### 4.0 CORE: history-pretrained base, live update, feature parity, out-of-sample judging (D3)

**Rule HP-0 (no exceptions).** Every model on every timeframe is first fitted on history: 1m, 5m, 15m, 30m, 1h, 2h,
4h, 1d and 1w. This includes U-SCALP, the 1d / 1w context and descriptive models, and the per-arm filters.
- **History source:** Dukascopy 1m replay, learn 2010-17.
- **Live rows only update the model** (2). No model anywhere is fitted on live rows alone.
- **Where history is missing** (non-gold intraday classes), the first work item is the free data pull (data-engineer
  lane, started 2026-11-25). Until it lands that cell reads `no_history`. There is **no live-only fallback**.
- **sr-v2 is the one model live today that was fitted on live rows only.** It is frozen as signed, and its
  constants are pinned by a test. Its successor, **sr-v3** (history-pretrained by 4.0 (2), objective 4.1), is **build
  item 1** of section 9.
  - sr-v2 is only grandfathered until sr-v3 is signed and live.
  - From that day on, sr-v2 stops fitting new models and its ACTIVE models are carried into sr-v3.

**The gap today.**
- sr-v2 trains only on live replay rows: icm on 2026-11-20 had 812 rows with features out of 7,821 labels; vantage
  had 0 with features.
- The daily learner uses live labels only.
- Nothing predicts on day one.

**(1) History-pretrained base, per unit x asset class.**
- **Data:** the unit's rows replayed by `history_replay.py`: same rule, label, objective and feature code.
- **Training window:** learn 2010-01..2017-12. After the unit's confirm has been read once, pass or fail, the
  base may refit on 2010-01..2025-09 (the confirm is spent).
- **Never read:** test_old (until `windows.allow_test_old_for` opens it), the embargo, and the holdout
  (2025-10-01..2026-09-30).
- **Stored:** `mtf_retrain_models` with `stage = base`, the window, the code hash and the data sha256.
- **Where:** built on the goldbot-ops VM (no Mac jobs). Shipped to the brain as JSON coefficients, never a pickle.
- **Asset class:** gold only today. A class with no history labels and no cost model has no base and reads
  `no_cost_model`.

**(2) Live update: prior-centred L2. History coefficients are the prior; the intercept is free.**
- **The loss.** The live refit minimises the section 4.1 objective on live rows (recency-weighted), plus
  (N0 / (2 C n_live)) x ||beta - beta_hist||^2.
  - The intercept is not penalised, so it re-estimates the base rate under the broker's measured costs.
  - At n_live = 0 the model *is* the history model.
- **Why not pool history and live rows.** History labels use W2 bps; live labels use r2 with measured broker legs.
  Pooled rows would anchor the intercept to history costs and keep the wrong share at the broker.
- **Why not a plain warm start.** A convex L2 fit forgets its starting point, so the warm start would carry nothing.
- **Disagreement ruled here.** The power-k slice said "history decides model class only, never coefficients". D3
  overrules that. The cost-level shift that motivated it is absorbed by the free intercept.
- **N0, fixed now:** min(0.5 x the base's training n_eff, 1,000) pseudo-rows.
- **Recency half-life on live rows:** 30 d (1m), 60 d (5m), 180 d (15m-2h), none (4h, 1d, 1w).
- **Bandit prior.** When an arm is gate-passed, its allocation prior is the history mean net R per candidate, as in
  H-60 `prior_from_history`: x 0.5, n_eff capped at 50, holdout refused.

**(3) Feature parity: history vs the engine.** It must pass before a base serves any live score.
- **Same code.** Both paths call `feature_snapshots.decision_features` with the same inputs and the same
  `ENGINE_MINUTES`. A test pins the import path.
- **`feature_parity.py`** (new, weekly, 0 K):
  - recompute the last 30 days of engine decisions from the brain's stored minutes through the history path, and
    compare them with `exploration_feature_snapshots`;
  - each column must be within tolerance (abs 1e-6 or rel 1e-4) on >= 99 % of rows, with equal NaN patterns.
  - A failing column puts every model that uses it into shadow, and health goes red.
- **Known breaks:**
  - **Backlog 271:** `d1_atr_ratio_14_100` and `d1_rv_ratio` are NaN in both paths, because 90,000 minutes is about
    80 daily bars.
  - The tick columns (`tick_count_z_48`, `tick_vol_ratio_20`) are not comparable between Dukascopy and the broker.
  - **None of these three enters `mtf-v1`'s input set (4.2).**
- **1d / 1w state comes from a closed-bar store** (cross-tf slice, option b): an append-only table of closed 1h+ bars
  with `visible_at` and `available_utc`, rebuilt from minutes, never from vendor D1 bars. Research uses it first;
  engine use is a later trading-safety item.
- 2h and 4h fit inside the engine memory (about 750 / 375 bars).

**(4) Judging stays out-of-sample.**
- History: Stage L OOF (2012-17) -> C -> T.
- Live: only rows decided after the fit cutoff plus embargo.
- A history row is never a forward row. N0, the objective and the prior are part of the frozen spec.
- sr-v2 stays frozen. History pretraining for its per-arm filters comes as a new spec `sr-v3` (quant review, K per
  judged pair as today) using exactly 4.0 (2) and 4.1.

### 4.1 Objective function (D4): cost-weighted logistic (meta-label), chosen over four alternatives

**Requirements:**
- costs inside the objective (net R: spread, commission, swap, slippage and stop overshoot);
- robust to fat tails;
- convex or stable at small n;
- the same objective in training, threshold choice and model selection;
- frozen hyperparameters;
- the gate still judges net R out of sample.

| Option | Costs inside | Fat tails | Small-n stability | Matches the keep/drop decision | Verdict |
|---|---|---|---|---|---|
| **(1) Cost-weighted logistic:** y = 1[net R > 0], weight w = u x min(abs(net R), 3), u = label uniqueness | yes (net R) | weights clipped at 3 R; logistic loss bounded in the score | **convex**; L2 and the prior-centred update apply directly | **yes.** Keeping a row earns its net R and dropping it earns 0, so maximising the kept sum is example-dependent cost-sensitive classification with cost abs(net R) (Elkan 2001). The weighted logistic is its convex surrogate | **CHOSEN** |
| (2) Huber regression on net R | yes | yes (Huber 1964) | convex | yes in principle (keep if E[R \| x] > 0) | **runner-up.** At AUC near 0.50 the regression target is mostly noise, its scale moves with costs per tf, and in-house regressors (R-20 LightGBM regressor) ranked mostly on cost. Kept as a diagnostic only |
| (3) Mean net R minus lambda x CVaR / semi-variance | yes | targets it | convex for linear scores (Rockafellar & Uryasev 2000) | partly | **rejected.** Bracketed exits already bound the downside near -1 R plus overshoot, so CVaR adds little. It needs a lambda we cannot set without tuning, and is unstable in the tail at small n |
| (4) Differentiable Sharpe / Sortino (Moody & Saffell 2001) | yes | no (a ratio is unstable when the denominator is small) | **non-convex**, path-dependent | no | **rejected.** It overfits at our n, is not reproducible as fixed JSON coefficients, and the program has no evidence it helps |
| (5) Pairwise ranking loss (RankNet) | only through the order | yes | convex surrogate, O(n^2) | matches "drop the bottom third" | **rejected.** It ignores magnitude, so a filter that drops many small losers and keeps one big loser scores well. It also cannot use the prior-centred update with a free intercept |

**The chosen objective, fixed now:**
- **Labels:** history W2 bps plus swap; live r2 measured legs.
- **Weight:** w_i = u_i x min(abs(net R_i), 3.0).
  - u_i = average uniqueness of row i's horizon among all rows of the unit (López de Prado 2018, ch. 4).
  - The weights are rescaled to mean 1 within each training fold.
- **Regularisation:** L2 at C = sr-v2 `C_REG`, intercept free.
- **Threshold, from the same objective.** Keep when the weighted model's p >= 0.5. That is where the model's
  cost-weighted expected sign of net R turns positive. The keep share is clamped to [1/3, 1] on the training fold:
  - never drop more than two-thirds;
  - keeping everything means "no filter", and that fails the gate by construction.
- **Selection by the same quantity.** Every comparison between models, folds or units uses OOS kept-mean net R
  (and kept-minus-all). That is the gate's statistic, so training, threshold and selection all optimise net R.
- **The gate is unchanged** (4.5): net R out of sample.

**K effect.**
- `mtf-v1` is registered with this objective, so there is no extra K.
- sr-v2 and H-101 keep their plain logistic, as signed. Moving them to this objective is a new candidate: sr-v3 /
  zoo-v2, K as their cards count.
- Changing the objective after any `mtf-v1` read is K +1 per unit.

### 4.2 Model per unit (fixed, no grid)

- **Learner:** pooled L2 logistic with the 4.1 objective, one model per unit x asset class, per-unit 4.0 prior.
- **Inputs: "core-8" plus `side`, plus `tf_is_1m` in U-SCALP only. p <= 10.** The core-8 inputs are:
  - `em_atr_cost`, `spread_atr`, `vol_tercile`, `session_id`, `hour_utc`;
  - `atr_ratio_14_100` (on the decision frame);
  - `tsmom_score` x side, `dist_vwap48_atr` x side.
- **Why not 40.** At AUC 0.55-0.57 a 40-input model needs about 23,000-46,000 rows for shrinkage >= 0.9 (Riley et al.
  2019, scalp slice). The 30m-4h units have about 1,500-2,500 learn rows.
  - **Even p = 8 is under-sampled there** (about 4,600 rows needed at AUC 0.57). So L2 will shrink toward the
    intercept, and a null is the expected result. That is stated, not hidden.
- **The 40-input cap** is respected with room.
- **The higher-timeframe "ladder"** (cross-tf slice: 1h/4h/d1/w1 state for each decision tf) is **not** in `mtf-v1`.
  It goes to zoo-v2 / sr-v3 as a new feature spec after a feature-discovery screen (section 11).
- **Purge and embargo.** Walk-forward is expanding, yearly blocks, full-horizon purge. Embargo = max(1 day, the
  unit's longest max hold): U-SCALP 1 d, U-30m 1 d, U-1h 2 d, U-2h 3 d, U-4h 7 d. For 1d / 1w description: max(H, 5
  trading days).

### 4.3 Stage 0 (0 K, outcome-blind)

Stage 0 runs once the build items in section 9 have landed:
- `history_replay` timeframes come from `config.tf_seconds` (30m / 2h / 1d / 1w); today they raise `KeyError`;
- the price-scaled clamps;
- the Friday cut;
- `sd_r_measured_by_tf` keys for 1m, 30m, 2h and 1w (today `sd_r()` raises for them).

**What it does:**
- Count candidates per row, window and tf.
- Compute n_eff by 4.4.
- Run `power_first.start_or_refuse`: SE of kept-minus-all = 1.2 x sqrt(0.5 / n_eff); delta 0.10 R; z at Holm rank 1
  of 5 (L and C 2.33; T 1.28).
- Start rule (ADR 0085): every power_g >= 0.5, joint >= 0.25, n_eff >= 200 at C and at T.
- **A unit failing the rule is NOT STARTED (power):** 0 K, it keeps recording, and it is re-counted monthly on counts
  only.
- **Expected:**
  - U-SCALP, U-30m, U-1h and U-2h start;
  - U-4h is marginal: T n_eff about 284 is an upper bound before the overlap correction;
  - 1d / 1w are refused in every class (test_old n_eff 48-90 for 1d, 10-18 for 1w).

### 4.4 Effective n

- **Pooled-tf, pooled-arm or held-overlap reads** use n_eff = sum of label uniqueness u_i (López de Prado 2018,
  ch. 4), further divided by the ADR 0085 day-cluster factor computed on the uniqueness-weighted counts.
  - This takes the overlap design effect from lane `fix/power-overlap-design-effect` when it merges. Until then the
    stricter of the two is used.
  - Raw counts and rho_day alone are never used across timeframes.
- **rho_day for horizons of 60 min or less:** measured at 0.000-0.016 on 2010-17 null brackets. That needs an ADR 0085
  follow-up (iv) ruling by the quant. Until then the 0.25 prior binds (settings only tighten).

### 4.5 Reading rule (history Stage L -> C -> T; Holm across the started units)

- **Learn, 2012-17 OOF. CONTINUE needs all of:**
  - edge vs no-model >= +0.05 R **and** vs the matched random control >= +0.05 R;
  - kept mean net > 0;
  - gross (mid) edge >= 0;
  - Holm max(p) <= 0.10 across the started units.

  Otherwise **STOP**: the unit is null for these inputs and this objective, and its rows keep recording for
  description.
- **Confirm, 2018-01..2025-09, read once:** edge vs no-model > 0, kept > 0, Holm p <= 0.10. 0 K.
- **Test_old, 2008-02..2009-12, passers only:** edge > 0, kept > 0, p <= 0.10. 0 K.
  - test_old is spent per family, in one pooled read.
  - Families already in the reads ledger (CONFIRM-SEEN: box confirm via H-80; learn SEEN for DON4 / PDC4 / KSQ4) are
    excluded label-blind before the read.
- **Detectable edge, stated up front** (80 % MDE at C): about 0.04 R (U-SCALP), 0.07 R (U-30m, U-1h, U-2h) and 0.10 R
  (U-4h). A true +0.05 R 4h filter would usually read null.
- **Prior:** about 3 % per unit (in-house meta-model AUC is about 0.50). About 14 % that at least one of five passes,
  by luck or by edge. The expected P4 result is null on every unit.
- **Disagreement ruled here.** The cross-tf slice put the prior at 10-15 %; I hold 3 %.

### 4.6 Dependency: H-101 gates every judged read here

- **If H-101's Stage L is null,** this card's Stage L is **CANCELLED at 0 K.** The meta-filter will then have failed
  where data is most plentiful, and splitting it by timeframe adds false positives, not information.
- **What continues either way:** labels, history bases, refits, parity, health and the descriptive tf x class tables
  (D1, D2).

### 4.7 Live / forward gates (sr-v2 shape, generalised to label time; cadence-drift slice)

These apply only after the unit passed history C. On 15m, sr-v2 applies as written.
- **Fit due:** time since the last fit >= FIT_SPACING[tf] **and** new labels >= max(N_FIT_ABS[tf], 10 % of training
  rows), **or** a drift trigger with half that.
  - A fit books **0 K**. It writes shadow scores and coefficients only.
- **Judge due:** time since the last judgement >= JUDGE_SPACING[tf] **and** >= 100 new OOS rows not in any earlier
  judged set (disjoint increment).
  - U-SCALP also needs >= 2,400 OOS rows and >= 2,400 control rows (50 % power on the binding control leg).
  - 4h judges only at quarterly ADR 0085 slots.
- **Pass:** both edges >= +0.05 R, gross >= 0, Holm across the run's judged units on max(p) <= 0.10.
- **ACTIVE:** passes in **two consecutive judged runs** of the unit.
  - A judge-due run that is missed breaks the chain.
  - The second run must add >= 100 OOS rows decided after the first run's cutoff.
  - The first passer's frozen model, scored on those new rows only, must show edge vs no-model >= 0, edge vs control
    >= 0, and kept > 0.
  - A first pass expires after 2 x JUDGE_SPACING.
  - Adoption requires the Monte Carlo null activation rate <= 1 % at every cadence (sr-v2's test, m = 1).
- **Demotion** (eager):
  - the sr-v2 rule (forward edge < `DEMOTE_EDGE_R` on >= `MIN_FORWARD` rows);
  - plus an anytime-valid confidence sequence (Howard et al. 2021): demote the day the upper bound of (arm - control)
    or of net falls below 0.
- **Drift triggers a fit, never a demotion:**
  - PSI > 0.25 on >= 2 of the 10 inputs;
  - keep rate outside [0.45, 0.85]. An ACTIVE model keeping < 0.25 or > 0.95 is forced to shadow;
  - CUSUM on the arm's no-model net R.

### 4.8 Leakage checks (all pass before any label is read; failure = no read, 0 K)

- T1, truncation invariance, for every (decision tf, context tf) pair including 30m / 2h / 1d / 1w.
- T2, future perturbation.
- T3, the HTF boundary: `visible_at` <= decision `ts_utc`, d1 at 13:30 New York on DST days, w1 at Friday / Sunday.
- T4, train/serve parity (4.0 (3)).
- T5, no structural NaN in the inputs.
- T6, shuffled-label AUC 0.50 +/- 0.02, pooled and per tf.
- T7a/b, oracle canaries (next-bar return; forming HTF bar). These show the detectors have power.
- T8, the purge property.
- T9, a cluster report (N rows, N clusters, n_eff by 4.4).
- Holdout and embargo rows raise.

## 5. Cadence (one daily scheduler with per-cell triggers; refits are free, reads cost K)

**Job slots.** These are set so no job overlaps `discovery_nightly` (01:00-04:05; peak RSS 2.76 GB against its
2.05 GB budget):
- `mtf_retrain`: daily 00:10 UTC, child process at nice 15, `job_budget_s` 900, RLIMIT_AS 1 GB, so it ends by 00:25;
- `mtf_forward_labels`: daily 22:30 UTC, budget 600 s;
- `feature_parity`: Saturday 10:00 UTC;
- sr-v2: Sunday 15:00, unchanged;
- history bases: on the goldbot-ops VM, the first Sunday of each month.

| tf | Daily weights | FIT_SPACING | N_FIT_ABS | JUDGE_SPACING | N_JUDGE_NEW (disjoint) | Cutoff lag | WARN if stale |
|---|---|---|---|---|---|---|---|
| 1m, 5m (U-SCALP) | frozen-weight scoring only; no daily refit (H-61: daily reweighting learned noise) | 7 d | max(500, 10 %) | 14 d | 100, and >= 2,400 total | 2 h | 9 d |
| 15m | sr-v2 | sr-v2 | sr-v2 | sr-v2 | sr-v2 | sr-v2 | 9 d |
| 30m | gate-passed only | 7 d | 100 | 28 d | 100 | 1 d | 16 d |
| 1h | existing engine weights | 7 d | 100 | 28 d | 100 | 2 d | 16 d |
| 2h | gate-passed only | 7 d | 100 (fit needs >= 300 purged training rows) | quarter | 100 | 3 d | 16 d |
| 4h | gate-passed only | 7 d (drift report: score PSI, top-10 coefficient sign flips) | 40 | quarterly ADR 0085 slot | 100 | 7 d | 31 d |
| 1d | n/a | 28 d | 30 | never | n/a | 10 d | 35 d |
| 1w | n/a | quarter | 20 | never | n/a | 6 w | 100 d |

## 6. Reinforcement = allocation among gate-passed arms only (reinforcement slice, adopted in part)

**Today there is nothing to reinforce.** All 61 of 61 weights are 0 ("excluded: retired") and the allocator is
`equal`.

**Adopted (K 0):**
- **R1.** Eligibility is the gate, not the bandit. Only gate-passed (ACTIVE) arm+filter pairs, taken through the ADR
  0061 learning-class route, get a result-driven weight. No arm earns weight from live fills alone.
- **R2.** The cell key becomes (tf, asset_class), replacing style. All 9 tf cells exist even when empty. An empty cell
  has budget 0 and never hands its budget on.
- **R6.** Gates, demotion and allocation read candidate-level r2 labels, not fills. Any fill-based read uses IPW on
  the logged propensities.
- **R7.** Demotion by confidence sequence (4.7).
- **R8.** RiskGate untouched and the only path to an order. Fail closed: unreadable weights mean equal for gated arms
  and **0 for ungated arms**.

**Deferred:** R3-R5, the risk-unit budget, hierarchical shrinkage and top-two Thompson. They form one allocator
hypothesis (K +1 when pre-registered), read only after >= 2 cells each hold >= 2 arms that beat their control out of
sample.

**D5 (no demo limits).**
- Demo caps no longer bind. Any arm, including 1d / 1w, *may* trade on demo in broker mode once built, as a learning
  arm with its control, through RiskGate.
- **My recommendation: at most two such swing arms in 2027Q1** (DOND 1d and MD1W 1w), each with its own learning K row
  (+1 each).
  - Their value is measured swap, gap and overshoot cost on multi-day holds. They have no gate power.
  - Their fills never become filter labels (R6).
- **Real money:** promotion stays **owner-only** (`unlock_live` + typed phrase).

## 7. Trial cost (K) and schedule

**Starts now (2026Q4, 0 K):**
- the section 9 build items;
- research-replay rows recording (forward weeks are lost permanently if they wait);
- Stage 0 counts and power;
- history bases (fits are not reads);
- feature parity;
- per-tf health;
- the descriptive tf x class tables;
- the rho_day estimate from other families' learn outcomes (ADR 0085 follow-up iv).

**Starts in January (2027Q1, under the 2027Q1 override; K still counted):**

| Item | K | Condition |
|---|---|---|
| Stage L: U-SCALP, U-30m, U-1h, U-2h, U-4h | **+5** | each only if Stage 0 starts it **and** H-101's Stage L is not null |
| Stage C, Stage T | 0 | passers only |
| Live judged reads | +1 per unit per read | only after a history pass; at most 5 a quarter, expected about 0.25 |
| Two demo swing learning arms (optional, 6) | +2 | RD allocation, trading-safety review |
| sr-v3 (history-pretrained sr, objective 4.1) | per its own card | quant signature |

**Not in this card, flagged to the RD:**
- **sr-v2's realistic Q4 K rises by 1-2.** Z3D reaches 200 OOS rows over 10 session days around 2026-12-07, so it
  enters the 12-13 run (scalp slice).
- The cadence slice's bound of 260 a quarter is **declined**. This card's own bound is <= 3 in Q4 and <= 12 in Q1.

**Compute (estimate, unmeasured):**
- history replay of about 15 rows at 30m-1w plus U-SCALP's 8 arms: about 1-3 h on the ops VM;
- the daily run: about 3-6 min and under 300 MB on the brain (sr-v2 measured 58 s);
- forward replay: minutes.

## 8. Asset class

- **Gold only today.** Demo is gold-only (ruling 2026-11-13). Non-gold r2 labels do not exist, because `ReplayCosts`
  prices gold only.
- **Every table and health line is split tf x `asset_class`.** Non-gold cells read `no_cost_model` or `no_candidates`.
- **Prerequisite work item (not a blocker for gold cells):** per-instrument cost models for non-gold labels
  (data-engineer + quant), and the free 1m history pull for fx / energy / bond / agri / crypto (data-engineer,
  free-data-first).
- Pooling metals buys at most about 1.35x n_eff (rho_inst 0.7).

## 9. Code changes for the implementer (none made by this card)

0. **sr-v3** (build item 1, HP-0). History-pretrained per-arm filters: 4.0 (2), objective 4.1, own card and quant
   signature. Also the **data-engineer free 1m pull** for non-gold classes; until it lands those cells read
   `no_history`.
1. **Timeframes.**
   - `goldbot/config.py`: `TF_SECONDS["2h"] = 7200`; the `Timeframe` literal; `INTRADAY_LIVE`.
   - `research/arms.py`: the `Timeframe` literal and `A2_SLOW_TIMEFRAMES`.
   - `research/exploration_data.TIMEFRAMES` (add 2h, 1w); `research/walkforward.WINDOWS` (2h: 42 / 6 / 6 months,
     purge 7 d, embargo 3 d).
   - Rule dicts: `arm_rules_channels.DON_HOLD` (1w), `catalogue/data.TF_NS`.
   - **`telegram/labs._TF_STYLE`: without "2h" it silently defaults to scalp. Add it, with a completeness test over
     `TF_SECONDS`.**
   - UI order lists: `api/strategy_board.TF_ORDER`, `api/strategy_ranking.TF_ORDER`, `api/trade_chart.LADDER`,
     `web/src/lib/trades.ts`.
2. **`research/history_replay.py`:**
   - timeframes from `config.tf_seconds`;
   - per-tf `WARMUP_DAYS`;
   - price-scaled clamps (3);
   - the Friday cut for every 4h / 1d / 1w row;
   - `bar_fill`;
   - void rows without warm-up.
3. **`config/research/multi_tf_arms.yaml` (new):** the section 3 rows. A test asserts that the engine never reads it.
4. **`research/forward_replay.py` (new):**
   - daily replay of the research rows on stored broker 1m bars from a cursor;
   - measured cost of record;
   - features via `decision_features` / the closed-bar store with `available_utc` = write time;
   - aux tables `mtf_obs`, `mtf_labels`, `mtf_features` (new aux migration);
   - idempotent per (row, decision).
5. **`research/closed_bars.py` + aux table (new):** the closed-bar store (4.0 (3)), with a parity test against a
   recompute from minutes.
6. **`research/multi_tf_retrain.py` (new):**
   - `UNITS` (rows, tf set, objective 4.1, core-8 inputs, N0, half-life, FIT/JUDGE spacings);
   - the prior-centred fit;
   - uniqueness weights;
   - reuses `self_retrain` walk-forward, `oos_metrics` and `holm`, with a per-unit embargo;
   - tables `mtf_retrain_runs`, `mtf_retrain_models`, `mtf_retrain_cells` (one row per broker x class x tf per day,
     statuses learning / waiting_labels / no_candidates / no_cost_model / failed / off / overdue);
   - K rows written before apply (fail closed).

   `self_retrain.py` is **not** edited.
7. **`research/mtf_history.py` (new):**
   - Stage 0 / L / C / T and the history bases;
   - `power_first.start_or_refuse` with the overlap design effect (lane `fix/power-overlap-design-effect`);
   - `windows.allow_test_old_for`.
8. **`research/feature_parity.py` (new):** see 4.0 (3).
9. **`goldbot/ops/jobs.py`:** `mtf_forward_labels`, `mtf_retrain` and `feature_parity` at the section 5 slots, with
   the child-process budget, behind `research.multi_tf.enabled` (default false until the signature).
10. **`config/settings.yaml` / `config.py`:**
    - the `research.multi_tf` block;
    - `sd_r_measured_by_tf` keys 1m, 30m, 2h, 1w and pooled;
    - **`snapshot_max_decisions` given a per-tf quota** (600 a night shared today, and it starves slow tfs when 1m
      volume returns).
11. **Health:** `ops/checks/loop.py` plus a `multi_tf_retrain.health`.
    - One line per tf x class, as counts: labels, new since fit, weights, active, next judge ETA.
    - **WARN** when an enabled cell is stale per section 5, a cell failed, or labels have not grown for 14 days.
    - **INFO** for `no_candidates` / `no_cost_model`.
    - Never report schedules alone.
12. **Reports:** `daily_review.py` / `weekly_review.py` split counts, net R, labels and weights by tf x asset_class.
    The `exploration_learn` output reports weights per tf.
13. **Tests:**
    - T1-T9;
    - 2h nesting into 4h;
    - `bar_fill`;
    - `_TF_STYLE` completeness;
    - clamp scaling;
    - Friday-cut parity;
    - K before apply;
    - cadence gating (no judgement under N_JUDGE_NEW);
    - chain expiry;
    - engine isolation;
    - the null activation Monte Carlo per cadence.

## 10. Flags

- **Owner-only:** none is needed. D5 removes the demo limits, and nothing here touches real money, RiskGate, sizing
  or instruments. Real-money promotion stays owner-only.
- **Open quant rulings** (the coordinator's list plus mine):
  1. rho_day for labels of 60 min or less. Measured at 0.000-0.016 against the prior of 0.25.
  2. Fit-only runs book 0 K.
  3. The overlap design effect, from lane `fix/power-overlap-design-effect` (running).
  4. The K convention for record-only rows.
  5. The W3.2 daily-P&L design effect.
  6. The cap reading for core-8 vs 40.
- **Quant rulings needed (detail):**
  - the K convention for record-only rows (7);
  - rho_day for short horizons (4.4);
  - fit-only = 0 K for `mtf-v1` (proposed here);
  - the W3.2 daily-P&L design effect (the only possible future 1d judged route).
- **RD + quant:** a "deep" 1975-2002 window and a rho_inst floor below 0.5 (weekly slice). Without both, no 1w card
  can ever start. This card does not assume them.
- **Data-engineer / SRE:**
  - the closed-bar store;
  - the snapshot quota;
  - non-gold cost models;
  - the free 1m pulls;
  - the 1999-2000 daily gap.
- **Risk I am raising:** this card spreads an unproven filter family across 9 timeframes. Three things keep the
  multiple-testing count honest: the H-101 dependency (4.6), history-first judging, and the refusal to add retired
  families (redbox, tsmom, box) on new timeframes. Without them I would not sign it.

## 11. Not in this card

- No new rule body. No red-box, tsmom or box row on a new tf.
- No macro, COT or ETF inputs. No higher-timeframe ladder. These go to zoo-v2 / sr-v3 after a feature-discovery
  screen (SW-0, Q2 reserve), with stability selection inside purged folds.
- Cross-tf partial pooling (A2, Daumé 2007) goes to zoo-v2 as one more base learner (K +1).
- No change to sr-v2, H-101, ADR 0095 or the engine.
- 1d and 1w are never judged as decision timeframes.

## 12. Quant-reviewer signature

Pending.

## 13. Staff findings integrated (ten slices, 2026-11-25, local `~/tmp/mtf/`, outcome-blind) and rulings

| Slice | Adopted | Disagreement -> ruling |
|---|---|---|
| 1m-5m | pooled U-SCALP with p <= 10 (Riley 2019); no daily refit; refit at >= max(500, 10 %); judge at >= 2,400; do-not-feed list; snapshot quota; Z3D enters sr-v2's 12-13 run | rho_day 0.015 for short labels -> quant ruling; the 0.25 prior binds until then |
| 15m-30m | F1A30 + F1C30, IEX30; price-scaled clamps; uniqueness n_eff; `history_replay` timeframes | RBR30 as a flow source -> **declined** (H-99) |
| 1h-2h | 2h on the UTC floor with `bar_fill`; clean set G1A-2h, PDC-2h; hold 36; `_TF_STYLE` trap; walk-forward windows | RBR-2h as filter rows -> **declined**; box 2h -> dropped (retired family, new row) |
| 4h | history-first; quarterly judging; one Friday-cut rule; `bar_fill`; replay-only recorders now; drop W1A | P5 with RB4H -> **declined** (H-99). U-4h = P4 and may be NOT STARTED; accepted |
| daily | 1d context-only; embargo max(H, 5 d); overlap n_eff; swap in R; Donchian = TSMOM family | engine `record_only` mode -> deferred; forward replay is off-engine. Deep window -> RD + quant |
| weekly | 1w never judged; quarterly refit; p of 5-8 for 1w description; SWING descriptive only | WK-1 deep-window card -> not here |
| cadence-drift | daily scheduler, per-cell triggers; disjoint increments; chain expiry; drift as fit trigger; e-process / CS demotion; `mtf_retrain_cells`; brain compute with budget; no overlap with discovery | (a) pool **instead of** history pretraining -> **overruled** by D3 (pooling only on top); MAX_JUDGED_RUN 20 / 260 a quarter -> **declined** (<= 3 Q4, <= 12 Q1) |
| reinforcement | R1, R2, R6, R7, R8; history bandit prior | R3-R5 -> deferred as one K +1 allocator card |
| cross-tf-models | closed-bar store; leakage tests T1-T9; drop structural NaN, do not impute; per-tf embargo; event clusters | A2 partial pooling as primary -> moved to zoo-v2 (the slice's own cheaper preference); prior 10-15 % -> held at 3 % |
| power-k-eval | the day ceiling; Holm across all judged units of a run; test_old once per family; NO-DATA / POWER-WAIT / CONTEXT-ONLY / FUTILE-STOP states; Q4 cap of 20; `sd_r` keys | one pooled-tf unit per class -> per-tf units kept: D1 wants per-tf learning, the families differ by tf, and same-family cross-tf pooling adds little uniqueness. "Never coefficients from history" -> **overruled** by D3 (free intercept) |

**FUTILE-STOP** (adopted): a unit stops consuming K when either holds:
- n_eff >= 400 and conditional power for 0.10 R < 10 %; or
- 4 consecutive judged runs pass nothing and the CI upper bound of the OOS edge vs no-model is < +0.05 R.
