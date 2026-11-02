# ML+prob lane MLNET: pre-registration (learned NET-R selection of trend candidates, MLNET-V1..V4)

Principal ML researcher, 2026-11-02. Written and committed BEFORE any pool trade, feature value or model output of
this lane is computed or read. Brief: orchestrator `mlprob-brief.md` (owner directive 2026-11-02) with sections 1-6,
8, 9 of `sprint200-brief.md`, and windows amendment W1 (section 8). Scripts (committed after this file, before any
run): `scripts/mlprob/mlnet_engine.py` (verbatim copy of `scripts/sprint200/trend_screen.py` from branch
`research/sprint200-trend`, PR #272, for its signal code, fill engine, leak-free twins and cost path, plus a
`test_old` window entry), `scripts/mlprob/mlnet_pool.py` (candidate pool + features), `scripts/mlprob/mlnet_select.py`
(walk-forward models, selection, statistics).

## 0. What is already known, and the honest prior

- Seen before this file: the sprint-200 trend lane's *aggregate* explore table (PR #272: per rule n, gross/net mean
  R, cost R, controls) and its single confirm read of rule 03. No trade-level row, no feature value. That table is
  why the pool is these 11 rules (gross explore t >= 2): 01, 03, 04, 10, 15, 19, 20, 24, 28, 31, 32. This is a
  selection on explore *gross* outcome; disclosed. Confirm (2018-2025.09) is untouched by it except rule 03's
  confirm aggregate (n 497, net +0.070 R), one of eleven rules in the confirm pool.
- Prior ML failures here: meta-label M1/M2/M1c and the A-series (AUC about 0.50, target = direction or hit, price
  features). This lane changes the **target** (net R after the cost of record, so cost and swap are inside the
  label) and adds three information sources the earlier meta-models did not have (section 3): (a) the ex-ante cost
  of the trade in R, known at entry from stop distance and session; (b) cross-rule consensus (how many of the other
  10 trend rules fired the same or the opposite way in the last 24 h); (c) trend-strategy momentum (realised net R
  of the same rule's and the whole pool's recent closed trades, known at entry). Literature for (c): time-series
  momentum returns are regime dependent (Moskowitz, Ooi, Pedersen 2012, Journal of Financial Economics 104(2),
  1985-2009, 58 futures incl. gold, gross of costs; replicated over 1880-2016 by Hurst, Ooi, Pedersen 2017, Journal
  of Portfolio Management); serial correlation of *trend-strategy* returns at trade frequency in gold CFDs is not
  established in the literature *(no direct source; this lane tests it)*. For (a) none needed (cost R = cost USD /
  stop USD). Meta-labelling on rule entries: Lopez de Prado 2018, *Advances in Financial Machine Learning*, Wiley,
  ch. 3 (method; no out-of-sample gold result; single source).
- Prior: the pool's explore net mean is near +0.03 R; no rule passed confirm. A selector must lift the net mean by
  about 0.1 R on a subset that keeps n >= 100. Expected confirm passers: 0 (p about 0.85), 1 (p about 0.15).

## 1. Conventions (inherited verbatim from the trend lane prereg, `trend-prereg.md` section 1)

Mid prices from Dukascopy 1m; entry at the open of the first 1m bar at or after the decision close; stop first in a
shared minute; a gap through the stop fills at that minute's open; one position at a time PER RULE (rules are
independent arms; two rules may hold positions at once); ICM cost of record `cor-2026-10-31.2` through
`ArmCosts.screen_charges` (round trip upper bound, overshoot floor $0.12 on stop and trail exits) plus the shipped
swap per rollover night; 4 leak-free random twins per trade (same session instance, minutes at or after the real
fill, same side, stop distance, target, trail and time cap). Each rule's signal, stop, target, trail and cap are the
trend lane's code, unchanged. Windows: explore decisions 2010-01-01..2017-12-31; confirm decisions
2018-01-01..2025-09-30 (data from 2016-07-01 for warm-up); nothing at or after 2025-10-01 is read (loader
assertion; the 2026 file is never opened).

## 2. Candidate pool and label

- Pool = every simulated trade of the 11 rules above, per window. Label y = net R of that trade
  ((gross + swap - round trip - overshoot on stop/trail exits) / stop distance), exactly the trend lane's `R`.
- A trade is a TRAINING row for a model fitted at cutoff C only if its exit time < C (purging; features use only
  information at or before the trade's decision time, so no further embargo is needed).

## 3. Features (33 columns, cap 40), all point in time at the decision time d

Every bar-derived feature is computed on completed bars and joined to the trade with `goldbot.data.store.asof_join`
on `available_utc` = the bar's decision (close) time, so only bars closed at or before d are used.

| # | feature | information |
|---|---|---|
| 1-11 | rule one-hot (ridge) / rule index as categorical (boosting) | which trigger |
| 12 | side (+1 / -1) | swap and drift asymmetry |
| 13 | ex-ante cost R = (round trip + overshoot of the decision-time session) / stop USD | cost per trade (new) |
| 14 | log(stop USD / ATRd) | stop width relative to daily range |
| 15 | long swap exposure = (side == long) x min(cap - d, 10 days) in days | swap cost (new) |
| 16-17 | sin, cos of UTC hour of d | intraday seasonality |
| 18 | weekday of d (0-6) | |
| 19 | session code at d (-1, 0, 1, 2) | |
| 20 | side x D1 trend state (-1, 0, +1; the trend lane's D1UP/D1DN) | alignment with daily trend |
| 21 | side x (1d close - EMA50(1d)) / ATRd | extension |
| 22-24 | side x 1d, 5d, 20d close-to-close return / ATRd | multi-horizon momentum |
| 25 | percentile of ATRd within its trailing 250 daily values | vol regime |
| 26 | ATR(1h,5) / ATR(1h,50) | short-term vol expansion |
| 27 | ADX(4h,14) | trend strength |
| 28 | side x position of the 4h close in its 20-bar channel, mapped to [-1, 1] | location |
| 29 | side x (1h close - previous completed day's high if long / low if short) / ATRd | break of daily level |
| 30 | number of OTHER rules' signals with the same side, decision in (d - 24 h, d] | consensus (new) |
| 31 | number of other rules' signals with the opposite side in (d - 24 h, d] | conflict (new) |
| 32 | mean net R of the same rule's last 20 trades with exit < d (NaN if < 5) | rule momentum (new) |
| 33 | mean net R of the pool's last 50 trades with exit < d (NaN if < 10) | trend-regime momentum (new) |

Signals for 30-31 are the rules' raw signals (before the one-position-per-rule filter). Ridge imputes NaN with the
training median and standardises with training mean/sd; boosting takes NaN natively.

## 4. Models and the four selection variants (K += 4; nothing else is run on outcomes)

Fixed hyperparameters, no search anywhere (explore or confirm). Training target y winsorised to [-2, +5] (training
rows only; evaluation always uses raw y).

- **MLNET-V1 RIDGE-POS**: ridge regression, alpha = 10 on standardised features; take a trade if prediction > 0.
- **MLNET-V2 HGB-POS**: `sklearn.ensemble.HistGradientBoostingRegressor(max_depth=3, learning_rate=0.03,
  max_iter=200, min_samples_leaf=80, l2_regularization=1.0, random_state=0)`, rule as categorical; take if > 0.
- **MLNET-V3 HGB-TOP3**: the same model; take a trade if its prediction >= the 2/3 quantile of the model's own
  predictions on its training rows (threshold fixed at fit time, point in time).
- **MLNET-V4 RULEPICK** (non-learned baseline the ML must beat to matter): take a trade if its rule's mean net R
  over the training rows is > 0 (a rule with < 30 training rows: skip).

**Walk-forward.** Annual, expanding: for prediction year Y the model is fitted on all pool trades with exit time <
Y-01-01 (from 2010). Explore scores Y = 2012..2017 (2010-2011 are training only). Confirm scores Y = 2018..2025
(through 2025-09-30), each year refitted on everything with exit < Y-01-01 (explore pool + earlier confirm years):
exactly what a live arm retrained each January would see. No hyperparameter, feature or threshold change between
explore and confirm.

## 5. Pass rules (written before any data is seen)

Statistics are on the scored (out-of-sample) trades. `t` is the cluster-robust t of the mean net R with clusters =
trading day (trades from different rules on one day are not independent); the plain trade-level t is reported beside.

- **Explore (each variant)**: selected n >= 100; selected net mean R > 0 with clustered t >= 2.0; selected mean
  minus the unselected pool mean (same scored years) >= 0.05 R; selected mean minus its leak-free twins' mean
  (paired per trade) >= 0.05 R.
- **Confirm (explore passers only, run once)**: selected n >= 100; net mean R > 0; clustered t >= 2.0; DSR >= 0.5
  (the probability form of "deflated Sharpe > 0", `goldbot.research.metrics.DsrInputs`, needs n >= 200) at
  K = 3,237 (programme ~3,233 + this lane's 4); >= 6 of the 8 confirm years (2018-2025) with selected net mean > 0;
  selected mean minus twins' mean > 0.
- Reported, not gating: stress net R (overshoot $0.70), Spearman rank correlation of prediction with net R, selected
  minus unselected per year, a shuffled-label walk-forward (V2 with y permuted within each training set: leakage
  check, expected selected-minus-unselected about 0), long share, n per rule.

## 6. Reading rule

- A variant that passes confirm AND test-old (section 8) is a survivor: one exploration arm per variant (the 11 rule
  triggers, each entry taken only if the model, refitted each January on closed trades, selects it) with its paired
  random control; min lot, RiskGate, 1% cap.
- If V4 (rule picking) passes and the ML variants do not beat V4's selected mean, the learning added nothing:
  reported as rule selection, not ML.
- If no variant passes explore: all four reported, confirm not run, verdict FAIL; learned net-R selection of this
  pool is retired (no re-test without a new information source).
- Owner-only, not assumed: live trading, sizing, instruments, budget.

## 7. Trial cost

K += 4 (V1..V4). Compute: pool builds (explore, confirm) at <= 2 worker processes.

## 8. Windows amendment W1 (owner directive 2026-11-02, adopted before any run of this lane)

Learn/confirm above unchanged; LEARN-OLD years are NOT added to this lane. A confirm-passer is **qualified** only
after it also passes TEST-OLD (2004, 2006, 2008), read once: `mlnet_select.py --window test_old` refuses a variant
that did not pass confirm. Procedure: the pool is built per test year from data starting the preceding 1 January
(warm-up, including that year's trades for the momentum features 32-33); only trades entered in 2004, 2006, 2008 are
scored; the model is the variant fitted once on every explore + confirm pool trade with exit < 2025-10-01 (same
fixed hyperparameters), frozen. Costs: today's cost of record + overshoot + swap. Pass: selected n >= 50 over the
three years, net mean R > 0, clustered t >= 1.5, selected minus leak-free twins >= 0. If the pre-2010 data is not on
disk, a confirm-passer is "confirm-passed, old-test pending" and does not deploy.

## 9. Windows amendment W2 and the coordinator's reporting additions (2026-11-02, before any run of this lane)

W2 supersedes W1 for TEST-OLD. The frozen model, features, variants, thresholds and the explore/confirm pass rules
of sections 4-5 are NOT changed. Changes:

- **TEST-OLD = entries 2006-02-01..2009-12-31** (replaces section 8's 2004/2006/2008). The pool is built from
  2005-01-01 (warm-up for bars and for momentum features 32-33; 2005 trades are history only, never scored, no
  2006-01 entry is scored). Model and freezing as section 8. Qualification pass (W2): selected n >= 100, net mean
  R > 0, day-summed t >= 1.28, selected minus leak-free twins >= 0. Read once, confirm passers only.
- **Reported beside the frozen gates, not gating** (each window): (a) everything again EXCLUDING 2025 (in confirm,
  2025 entries are dropped from the scored set and all statistics recomputed); (b) a DRIFT control: per trade one
  twin with the same side, stop distance, target, trail and hold length, entered at the same UTC time of day on a
  random other trading day within +/-20 trading days (deterministic hash), net uplift = selected minus drift twin;
  (c) the bps cost version: round trip, overshoot and swap converted to price-relative terms (dollar figure /
  4,000 x entry price), net R recomputed; (d) W2 statistics: t on DAY-SUMMED net R, one-sided day-block bootstrap
  p of the confirm net mean (10,000 draws, seed 0), BH-FDR at q = 0.10 over m = number of explore passers (fixed
  before confirm), and >= 5 of 8 years; the W2 verdict is reported beside the frozen verdict. K_eff is not
  estimated by this lane (DSR at programme K only).
