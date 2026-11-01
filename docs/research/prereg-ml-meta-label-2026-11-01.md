# Pre-registration: meta-label filter screen over two entry generators, 2010-2017 (ML researcher, 2026-11-01)

Frozen by commit **before** any label, model score or outcome on any window is computed. Results go to
`meta-label-screen-2026-11.md`; code `scripts/research/meta_label_screen.py` + `goldbot/research/meta_label_screen.py`
(committed after this file, before the single run). Rows ML-M1, ML-M2 in `hypotheses.md` section A24.
**Trial cost: K +2** (one per pre-registered model), 0 registry trials, holdout and 2018+ sealed.

## 0. Why this is not a re-test of R-07, and the prior

R-07 (and SoA A5): meta-models over primaries with no gross edge gave OOF AUC ~0.50; "do not retry until a primary
passes P4 on its own". No primary has passed. This screen is run on the owner's direction (relayed 2026-11-01) and
is admissible only because it adds information no R-07 model had:

1. **New PIT sources.** COT managed-money and GLD-flow columns (R-19 retired them as *rules* and kept them as model
   features, "counted there") and the tier-1 US calendar (`calendar-v1`). No R-07 model saw either.
2. **Unbiased rows.** M1's rows are every scheduled I0 decision taken **both** ways, so the label set has no selection
   by a primary (R-07 trained only on what a primary chose).
3. **The cost in the label.** Net R at the cost of record (`cor-2026-10-31.2`) with the long swap; R-07 used gross
   triple-barrier classes. A model can at least learn where costs in R are smallest.

Literature (separating replicated from single-source): López de Prado 2018, *Advances in Financial Machine Learning*,
Wiley, ch. 3 (meta-labelling filters a primary; methodology, no gold evidence, single source); Gu, Kelly & Xiu 2020,
*RFS* 33(5) (ML gains are cross-sectional; single-asset monthly OOS R^2 < 1%; replicated); Welch & Goyal 2008, *RFS*
21(4) (single-asset predictors fail OOS; replicated). None is net of costs on XAUUSD intraday.

**Prior, written now:** P(either model passes) about 5-8%. Most likely result: OOS rank IC 0.00-0.03, top-decile
gross near 0, net beats the random control only through side/swap and cost-in-R selection, which the side-matched
control (s.6) is built to remove. A pass would go to one pre-registered confirm on 2018-01..2025-09 (its own file);
nothing reaches demo or live from this screen.

## 1. Data and window

| Item | Frozen value |
|---|---|
| Bars | `raw/xauusd_1m_dukascopy_2010..2017.parquet` (local, not committed), read through `goldbot.data.release.read_bar_files`; loader asserts no bar at or after 2018-01-01. 2018+ and the holdout 2025-10..2026-09 untouched |
| Positioning | `raw/positioning/positioning.parquet` (PIT-stamped by `goldbot.data.positioning`), rows with `available_utc < 2018-01-01` only |
| Calendar | `us_calendar.parquet` (`calendar-v1`, vintage 2026-10-25), rows with `available_utc < 2018-01-01` only |
| Prices | 1m mid = (bid + ask)/2; decision frame 15m mid bars (`resample_bars(b1, "15m")`); context 1h, 4h, 1d |
| Decision time | the 15m bar's close (`visible_at`). Entry at the open of the first 1m bar at or after it |

## 2. Entry generators (existing code, unchanged)

| Model | Generator | Candidates | Barriers |
|---|---|---|---|
| **M1** | I0 `random_bracket` (`config/exploration_arms.yaml`, hash `6648e4d29f97155c`) via `arm_rules.candidates`: every :00 15m close 07:00-19:00 UTC, Mon-Fri | each scheduled decision **twice, long and short** (the hash side is ignored; the model chooses) | stop 1.5 x ATR14(15m), target 2 R, time limit 96 bars |
| **M2** | I2B `box_break_close` (hash `ccd052649a9dc2f9`) via `arm_rules.candidates` | the rule's own side | stop 1.0 x ATR14(15m), target 2 R, time limit 96 bars |

Common: no tier-1 blackout (no blackout arrays, as hb-exits); a candidate whose time barrier (`close[idx+96]`) is
more than 26 h of wall clock after the decision (weekend or holiday crossing) is dropped, as is one whose window does
not close before 2018-01-01. Overlapping positions are allowed (the screen measures selection, not a book); a
one-at-a-time book is reported as a diagnostic only.

## 3. Label (net R at measured costs)

Walk on 1m mid from the entry minute to the minute before the time barrier: stop first when one minute touches both
barriers; target fills at the target; time exit at the last minute's close. Gross R = side x (exit - entry) / risk.

`net_R = gross_R - (RT + 1[stop exit] x OS + 1[long] x nights x 0.60) / risk_usd`

- RT, OS: `ArmCosts.screen_charges("icm", session, "cor-2026-10-31.2")` (the charged round trip = measured 90% upper
  bound; the stop-overshoot charge), session of the entry minute (asia < 07:00, london 07:00-12:30, newyork >= 12:30
  UTC). Swap: $0.60/oz per 22:00 UTC weekday rollover held, triple Wednesday, shorts 0 (settings prior, as R-19).
- Training target: `net_R` (regression). `gross_R` is kept for the cost-filter diagnostic only.

## 4. Features (40 columns, chosen on no outcome)

Base: the frozen H-62 intraday list v1 (`h62-meta-labeller-spec-2026-10-25.md` s.1b, 38 columns), minus 7 redundant
columns (`tick_count_z_48`, `rv_ratio`, `em_yz_16_cost`, `session_id`, `ribbon_state`, `dist_vwap48_atr`,
`prev_sess_pos`) to make room for the 8 new-information columns. Signed columns side-aligned by
`registry.side_align`.

- **15m decision bar (27):** `em_atr_cost`, `spread_atr`, `atr_ratio_14_100`, `vol_ts_yz_20_60`, `jump_z`,
  `hour_utc`, `dow`, `min_since_newyork_open`, `ret_4`, `ret_16`, `ret_96`, `tsmom_score`, `im_ldn_ret_atr`,
  `dist_ema50_atr`, `slope_ema50`, `dist_ema200_atr`, `adx14`, `bb_z_20`, `mfi12`, `er_30`, `donchian_pos_20`,
  `compression_8_96`, `sess_high_dist_atr`, `sess_low_dist_atr`, `pdh_dist_atr`, `pdl_dist_atr`, `sweep_dir`.
- **Context (4):** `h1_adx14`, `h4_dist_ema50_atr`, `d1_slope_ema20`, `d1_atr_ratio_14_100`; each context frame
  stamped `available_utc = visible_at` of its bar and joined by `store.asof_join` at the decision time.
- **Positioning (5):** `pos_cot_mm_net_pct_oi`, `pos_cot_mm_net_pct_oi_z52`, `pos_cot_mm_net_pct_oi_chg4`,
  `pos_gld_tonnes_chg5_pct`, `pos_gld_tonnes_chg20_pct` (`goldbot.features.positioning`, `asof_join` on
  `available_utc`).
- **Calendar (3):** `min_to_next_tier1_pit`, `min_since_last_tier1_pit`, `tier1_count_today`
  (`goldbot.features.us_calendar`, `asof_join` on `available_utc`).
- **`side`** (1). Total **40** = the live cap (`model.check_feature_cap`).

Registry features are computed on the 15m bar whose open is `ts_utc` and known at its close; positioning and
calendar columns join at the bar's open (`ts_utc`), so they are at least 15 minutes stale at the decision.

## 5. Model and folds (fixed; no tuning, no feature selection)

- **Model:** `lightgbm.LGBMRegressor`, L2 objective, `n_estimators=300, learning_rate=0.03, num_leaves=7,
  min_child_samples=200, feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10, random_state=20261101,
  deterministic=True, n_jobs=1`, NaN handled natively. Unweighted. One model class per generator.
- **Primary folds (purged, embargoed, blocked by year):** 8 folds, fold y tests calendar year y (2010..2017). Train =
  every other row whose `[decision, label_exit]` does not overlap `[year start - 26 h, year end + 26 h]`, and that
  does not start within the 5 days after the test year (embargo). Out-of-fold (OOF) predictions for every row.
- **Forward leg (anchored walk-forward):** for y in 2012..2017, train on rows with `label_exit` < y start - 26 h,
  test year y. Strictly forward; no information from y or later.
- **Selection:** the **top decile** of predicted net R within each test year (threshold = that year's 90th percentile
  of predictions; label-free, but uses the year's prediction distribution: disclosed). Up to 4 worker processes.

## 6. Pass bar (per model; all must hold; one look)

| Leg | Rule |
|---|---|
| (a) power | >= 3,000 candidates and >= 300 top-decile trades; else the model reads UNDERPOWERED (counted in K, FAIL) |
| (b) edge | top-decile net mean R, week-block bootstrap (10,000, seed 20261101), **90% lower bound > 0** |
| (c) beats random selection | top-decile net mean > the **year- and side-matched** random-selection control (per year, draw the same number of longs and shorts from that year's candidates; 2,000 draws, seed 20261102): one-sided p <= **0.025** (Bonferroni over 2 models) |
| (d) years | >= 5 of 8 years with top-decile net mean R > 0 (a year with < 20 selected trades counts as not positive) |
| (e) forward | forward-leg top-decile net mean R pooled over 2012-2017 > 0 |

**Validity gate (decided before the read):** the same pipeline with labels permuted within each training fold
(seed 20261103). If the shuffled model's top decile beats the control at p <= 0.025, the run is VOID (leakage), not
read. A lookahead test runs first: features rebuilt on series truncated at 12 cut points must equal the full-series
values on the last 50 rows before each cut (|diff| <= 1e-9, NaN = NaN); any mismatch stops the run before labels.

**Reading rule.** PASS on M1 or M2: one confirm pre-registration on 2018-01..2025-09 (same model, retrained on
2010-2017 only, same bar), its own K +1; no demo arm before it. FAIL on both: R-07 is extended (R-20): meta-label
filters over random or box-break entries with this PIT set (price, vol, session, positioning, calendar) are retired;
reopening needs a new information source not in s.4 (order flow, options surface, author calls), not a new algorithm
or a new feature subset of these sources.

## 7. Reported (not in the verdict)

OOF rank IC (Spearman of prediction vs net R) and AUC of net R > 0 per year; decile table of net and gross mean R;
top-decile gross mean (cost-filter vs direction); side mix of the top decile; top-10 gain importances; net at 1.5 x
cost; one-at-a-time book of the top decile; GBP per trade at 0.01 lot.

## 8. Owner-only flags

None arise from a screen. Sizing, instruments, live trading and budget stay owner decisions; a pass changes none of them.

## Amendment 1 (2026-11-01, before any label statistic, model fit or read)

The dry run (`--dry`: counts, feature coverage and the lookahead test only; no label statistic printed, no model
fitted) found one description error in s.2 and fixes the counts:

- **M2's stop is the I2B rule's own structural stop** (`box_break_close.decide_with_stop` with the arm's
  `stop_atr: 1.0` parameter), not 1.0 x ATR14(15m). The generator is unchanged (hash `ccd052649a9dc2f9` governs);
  median risk $0.61/oz, so the charged cost is about 0.3-0.5 R a trade and M2 is a cost-dominated pool. Pass bar
  unchanged.
- Candidates after the s.2 filters: **M1 41,862** (20,931 decisions x 2 sides; 5,106-5,408 a year, median risk
  $2.52/oz), **M2 3,102** (346-465 a year). Both clear leg (a)'s 3,000; M2's top decile is about 310 (>= 300).
- Lookahead test: 12 cuts x 50 rows x 39 market columns, 0 mismatches.
- Feature NaN shares (LightGBM native): at most 4.1% (`d1_atr_ratio_14_100`, the 100-day warm-up); all others <= 1.3%.
