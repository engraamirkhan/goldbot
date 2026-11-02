# ML+probability team, lane PROBTREND: pre-registration (before any look)

Principal Regime and Opportunity-Day Researcher, 2026-11-02. Team brief `mlprob-brief.md` (owner directive 2026-11-02),
shared rules = sprint-200 brief sections 1-6, 8, 9, plus windows amendment W1 (2026-11-02). This file is committed
before any data is loaded by this lane: no feature, label, probability or trade outcome has been computed. What was
seen before this commit: the sprint-200 TREND lane's published explore/confirm aggregates (`trend-results.md`,
commit `ccaf6b26`, branch `research/sprint200-trend`), which define the candidate pool (selection disclosed below), and
the earlier A2/A3/A24 failures listed in section 7.

## 1. Question and mechanism

1h-4h gold has a gross trend premium (11 of 34 sprint-200 trend rules gross t >= 2 in explore; S200-TREND-03 gross
+0.165 R out of sample) that ICM costs (round trip + stop overshoot + long swap, 0.10-0.15 R a trade) take. Trend rules
earn on days whose price path is directional and lose (stop, re-entry, stop) on rotational days. If the probability
that the rest of the trading day is a trend day can be forecast point in time, trading the rules only when that
probability is high should raise gross R per trade by more than it cuts n. A second, mechanical channel: trend days
cluster in higher-volatility regimes where ATR-based stops are wider in dollars, so the fixed-dollar cost is a smaller
share of R. Both act through the gate; neither changes a rule's entry, stop or exit.

Evidence: volatility persistence (Corsi 2009, J. Financial Econometrics 7(2), HAR-RV, equities/FX, replicated);
trend days and the noise-area boundary (Zarattini, Aziz, Barbon 2024, SSRN 4824172, SPY 2007-2024, net of costs, single
paper); trailing exits pay only with serial correlation (Kaminski and Lo 2014, J. Financial Markets 18, theory and
futures, single paper). Our own record: range is forecastable (H-16 HAR rho gain +0.20), direction from the open is
not (H-17, R-05). This lane forecasts the directional *efficiency* of the remaining day, not range and not direction.

## 2. Probability model (frozen here)

Trading day d = 17:00 New York (d-1) to 17:00 New York (d) (the engine's `tday`). Two snapshots per day:
- **A** = trading-day open (first minute of trading day d); **B** = London open of d (`lon_start`, first minute of the
  London session; the Asia session is then complete).
- Snapshot price P_s = open of the first minute at or after the snapshot time.

**Label** TREND_s(d) = 1 iff, over the minutes from P_s to the last minute of trading day d (remainder high H_r, low
L_r, last close C_r): ER = |C_r - P_s| / (H_r - L_r) >= 0.5 AND |C_r - P_s| >= 0.5 x ATR20d (20 completed trading days
before d). Days with fewer than 600 (A) / 300 (B) remainder minutes are not labelled (holidays, half days) and are not
used in training; a forecast is still made for them.

**Features** (11 at A, 15 at B; <= 40), standardised with the training mean and sd:

| # | feature | snapshot |
|---|---|---|
| f1 | log(ATR5d / ATR20d) | A, B |
| f2 | log(range(d-1) / ATR20d) | A, B |
| f3 | ER(d-1) = abs(C-O)/(H-L) of day d-1 | A, B |
| f4 | log(ATR20d / ATR100d) | A, B |
| f5 | abs(C(d-1) - C(d-6)) / (ATR20d sqrt 5) | A, B |
| f6 | abs(C(d-1) - C(d-21)) / (ATR20d sqrt 20) | A, B |
| f7 | mean TREND_A label of days d-10..d-1 (labelled days only) | A, B |
| f8 | NR4 or inside day at d-1 (0/1) | A, B |
| f9 | tier-1 US event (fomc_statement, nfp, cpi) with event_utc inside trading day d, from calendar rows with available_utc <= snapshot (keyless US calendar, scheduled dates) | A, B |
| f10, f11 | Monday, Friday (0/1) | A, B |
| f12 | log(Asia range / ATR20d) | B |
| f13 | Asia ER = abs(asia close - asia open) / asia range | B |
| f14 | log(Asia range / median Asia range of the 20 prior days) | B |
| f15 | abs(P_B - P_A) / ATR20d | B |

Daily quantities (ATR, ranges, closes) are on the engine's trading-day bars (17:00 NY close). A day with any feature
missing (warm-up) gets no forecast, so no gated trade.

**Model:** Bayesian logistic regression, MAP with an independent N(0, 1) prior on each standardised coefficient
(scikit-learn `LogisticRegression(C=1.0, solver="lbfgs")`), one model per snapshot. No hyperparameter is searched, ever.

**Training (no look-ahead):**
- Explore (read 2011-01-01..2017-12-31): annual walk-forward; for year Y the model is fitted on every labelled day of
  2010-01-01..(Y-1)-12-31 and predicts every day of Y. Training strictly inside the explore window. 2010 is burn-in
  (no forecast, no gated trade); the ungated baseline is read on the same 2011-2017 span.
- Confirm (2018-01-01..2025-09-30) and TEST-OLD (2004, 2006, 2008): one frozen model per snapshot fitted on all
  labelled explore days 2010-2017, applied unchanged. No confirm-window or old-window outcome is used in any fit.
  (Reading of the brief's "training strictly inside the window being read": it forbids fitting on the outcomes being
  read; a model frozen on the earlier explore window is the stricter out-of-sample form. Disclosed before any look.)
- Threshold tau_s = the 60th percentile of the fitted model's in-sample probabilities on its training days (about the
  top 40% of days pass). Gate: trade a signal iff P >= tau of the forecast in force.

**Forecast in force:** forecasts are a table (available_utc = snapshot time + 1 minute, p, tau, snapshot) joined to each
signal's decision time with `goldbot.data.store.asof_join` (tolerance 24 h): signals before London open use A, at or
after it B. No forecast in force -> no trade.

## 3. Calibration first (read before any P&L)

On the explore walk-forward forecasts 2011-2017, per snapshot: base rate, Brier score, climatology Brier (training base
rate as the forecast), Brier skill score BSS = 1 - Brier/Brier_clim, AUC, reliability table (10 equal-count bins: mean
forecast vs observed frequency), logistic calibration slope and intercept. Written to the results before the P&L run.

**Calibration gate (frozen):** P&L is read only if BOTH snapshots have BSS > 0 AND AUC >= 0.55. Otherwise the lane
stops: no P&L read, verdict FAIL for all 12 variants, K still +12.

## 4. Strategies (12 variants, K +12)

Candidate pool = the 11 sprint-200 trend rules with explore gross t >= 2 (team brief; selected on explore gross,
disclosed), engine and costs exactly as `scripts/sprint200/trend_screen.py` at `ccaf6b26`, vendored verbatim as
`scripts/mlprob/probtrend_engine.py`: mid prices, entry at the open of the first 1m bar at or after the decision close,
stop first in a shared minute, a gap through the stop fills at the minute's open, ICM cost of record cor-2026-10-31.2
upper bounds (round trip; overshoot floor 0.12 on stop/trail exits) plus the shipped swap per rollover, one position per
rule at a time, four leak-free twins per trade (at or after the real fill, same session instance, side and exits).

| id | gated rule | name |
|---|---|---|
| PT-01 | S200-TREND-01 | H4-DON30-TR |
| PT-02 | S200-TREND-03 | H1-DON48-D1 |
| PT-03 | S200-TREND-04 | H1-DON24-ATRX |
| PT-04 | S200-TREND-10 | SGE-OR |
| PT-05 | S200-TREND-15 | H4-BBW |
| PT-06 | S200-TREND-19 | H1-SHOCK-CONT |
| PT-07 | S200-TREND-20 | ITS-AL2NY |
| PT-08 | S200-TREND-24 | MTF-EMA-1H |
| PT-09 | S200-TREND-28 | ADX-ONSET-4H |
| PT-10 | S200-TREND-31 | PB-4H-EMA20 |
| PT-11 | S200-TREND-32 | RSI2-1H |
| PT-12 | all 11 above, gated, as one book | POOLED |

The gate removes signals before simulation (a skipped signal does not occupy the rule's position slot), as a live gated
rule would behave. Ungated baseline = the same rule, same engine, same span, no gate.

Timeframes: the rules' own (15m, 1h, 4h). `research.yml` inputs: none new (Dukascopy 1m, keyless US calendar).

## 5. Pass rules (frozen)

**Explore** (entries 2011-2017): n >= 100; net mean R > 0 with t >= 2.0; leak-free control excess >= +0.05 R (sprint-200
rule 4); AND gated net mean R minus ungated net mean R (same rule, same span) >= +0.03 R (the gate must add value). For
PT-12 the t is day-clustered (net R summed per entry day; t of the daily sums against 0), stricter than per trade.

**Confirm** (entries 2018-01-01..2025-09-30, run ONCE, explore passers only): n >= 100; net mean R > 0; t >= 2.0 (PT-12
day-clustered); deflated Sharpe >= 0.5 (the DSR probability, i.e. deflated SR above zero; the brief's "DSR > 0" read
strictly, as the trend lane did) at K = 3,300 (3,233 program + 12 here + allowance for the three sibling team lanes);
>= 6 of 8 confirm years (2018..2025, 2025 = Jan-Sep) with net mean R > 0. Gated-vs-ungated and control excess reported.

**TEST-OLD** (W1; 2004, 2006, 2008; once, confirm passers only, when the pre-2010 files are on disk; the preceding year
read for warm-up only): n >= 50 over the three years, net mean R > 0, t >= 1.5, net minus leak-free control >= 0. Until
then a confirm passer is "confirm-passed, old-test pending" and does not deploy.

**Qualified** = confirm pass + TEST-OLD pass. Survivors become exploration arms (`arm_rules_mlprob_probtrend.py` +
append-only `config/exploration_arms.yaml` rows with a paired random control).

## 6. Expectations, costs and trial cost

- Expectation: the gate needs to lift gross per trade by about 0.10 R to clear cost. Prior P(any explore pass) ~25%,
  P(any confirm pass) ~5-10%: OS-SD (1h Donchian in the top RV5 tercile) failed net -0.126 R and H-17 failed, so vol
  conditioning alone is known not to be enough.
- Costs in R: 0.02-0.14 R per trade (rule dependent, `trend-results.md` cost column); swap on longs held overnight.
- Trial cost: K +12; compute about 1-2 h on 2 workers. Owner decisions: none (no sizing, instrument, budget or live
  change). Holdout (>= 2025-10-01) never read: the 2026 file is never opened; the 2025 file is cut and asserted.

## 7. Known failures this does not re-test

H-16 (range score; GVZ, events, compression as range inputs), H-17 / R-17 (trend-day continuation from the open), R-05 /
GS-C2 (session direction), OS-SD (vol-tercile-gated Donchian), A-series and M1/M2 meta-labels (gross hit or direction
targets), sprint-200 stats lane (HMM/regime-gated entries, Kalman, variance ratio, ensembles). Difference: here the
target is the remaining day's directional efficiency (a trend-day probability, calibrated and checked before any P&L),
used only as a gate on rules that already carry a gross premium.

## 8. Reading rule (written before any data)

- Calibration gate fails -> report the calibration, retire "trend-day probability from daily structure + Asia session"
  as a gate input; no P&L read.
- Calibration passes, 0 explore passes -> the forecast exists but does not lift the trend rules net of cost: retire this
  gate family at today's costs.
- Explore pass(es), 0 confirm -> report, retire, no arm.
- Confirm pass -> "confirm-passed, old-test pending"; arm code written but not enabled for demo until TEST-OLD passes.

## Amendment 1 (2026-11-02, before any data is loaded; coordinator relay of W1/W2 and the statistics expert's memo)

The frozen model, gates and pass rules above are unchanged. Added, all reported only (not gating) unless stated:
1. **TEST-OLD window = W2**: entries 2006-02-01..2009-12-31 (warm-up may read 2005 and 2006-01). Qualification rule
   (W2, replaces W1's for this lane): n >= 100 (>= 60 for a 4h rule), net mean R > 0, t >= 1.28, net minus leak-free
   control >= 0. Run once, confirm passers only; until the files are on disk the status is "old-test pending". The
   keyless calendar starts 2010-01, so f9 (tier-1 event day) is 0 for every TEST-OLD day; disclosed as a known
   input gap, not repaired after any look.
2. **Excluding 2025**: every confirm statistic is also reported on entries 2018-01-01..2024-12-31.
3. **Drift control**: per real trade, four twins with the same side, same time of day, same stop/target/trail/hold and
   deadline kind, on a random other trading day within +/-20 trading days (hash-seeded); net uplift vs it reported.
4. **bps cost version**: the cor-2026-10-31.2 dollar round trip, overshoot and swap scaled by entry price / 4,000
   (W2 price-relative costs); net R and t reported beside the frozen dollar version.
5. **W2 pass rules reported beside ours**: explore t on day-summed R and control excess >= 0; confirm BH-FDR q 0.10 on
   the one-sided day-block-bootstrap p (m = explore passers, 5-day blocks, 2,000 resamples, seed 0), >= 5 of 8 years;
   DSR at K = 3,300 and K_eff = 12 (this lane).
