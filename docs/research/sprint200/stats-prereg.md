# Sprint-200 lane `stats`: statistical and ML signals, pre-registration (2026-11-02)

Owner: principal ML researcher. Frozen by this commit BEFORE any outcome (return, R, hit rate) of any rule below is
computed on any window. Brief: sprint-200 shared brief (owner directive 2026-11-02). Screen code:
`scripts/sprint200/stats_screen.py` (written after this commit; any later code fix is listed in the report with its
reason, never a parameter change).

## 0. What is new, and what is not re-tested

Every line already closed stays closed: meta-label M1/M1c/M2 (net-R regression on I0/I2B decisions, R-20), discovery
v1/v2 (R-18), positioning H-86..88, H-84 macro, H-89 FOMC, growth C1-C4, micro-tick VAC/TIM/SRR, cross-asset X1-X3,
retail setups, opportunity SA/SB/SD, TV T-1, and the retired list under A15 (Hurst as an external gate, oscillator
fades 15m-1h, ADX gate, wavelet/Ehlers direction, 30/60/90-day momentum, unconditional band fades and breaks, ORB, TTM).
How this lane differs:
- ML models here predict the **sign of the forward return over a fixed horizon** (classification), not the net R of a
  bracketed I0/I2B candidate (the M1/M2 target); features are price-derived statistics only (no positioning, no
  calendar columns, which M1c showed act as regime labels); models are frozen at the end of the explore window.
- Regime models (2-state Gaussian HMM, k-means) and the Kalman local-linear-trend filter have not been tested in the
  programme. The variance-ratio / autocorrelation rules (S11, S12, S15) are neighbours of the retired "Hurst as an
  external gate": they differ in that the statistic sets the **side** of the trade (trend vs fade) at the trade's own
  lag, not a gate on another family's entries. Disclosed as neighbours.
- Return-distribution signals (realised skew, BNS jump test, signed jump variation, MAX/lottery) are new to the
  programme.
- S32 (1d trend vote) is a neighbour of H-01 slow TSMOM (trial #48, seen 2010-2025-09 unconditioned): disclosed; the
  vote-transition entry and the 1d bracket are the new part. Expected to fail on the long swap.

Prior (stated before the look): the programme has ~3,029 trials and 0 survivors; costs are the whole result at 1m-1h.
Expected confirm passers in this lane: 0 to 1. This lane is run to falsify cheaply, not to promise a survivor.

## 1. Common mechanics (all 33 rules)

- **Data**: Dukascopy 1m bid/ask (`raw/xauusd_1m_dukascopy_<year>.parquet`), error-flagged rows dropped (the
  `read_bar_files` rule), priced at mid = (bid + ask) / 2. Rows at or after 2025-10-01 are never loaded (parquet row
  filter on read, asserted). Explore loads 2010-01-01..2017-12-31 only (asserted < 2018-01-01).
- **Bars**: 1h and 4h floor on the UTC clock, visible at their nominal close; 1d = the repo feature day
  (`resample_bars`, America/New_York, 13:30 anchor), visible at its 13:30 NY settlement. A decision is made at a bar's
  visible time from bars visible at or before it only (as-of semantics; lookahead test in s.3).
- **Entry**: mid open of the first 1m bar with `ts_utc >=` the decision time; no trade if that bar is more than 15
  minutes after the decision (weekend, gap).
- **Stop**: 1.5 x ATR14 (Wilder, of the decision timeframe, through the decision bar). R = the stop distance in USD.
  A stop is filled at the stop price, or at the 1m open when the open is already beyond it (gap). Stop first when
  stop and target fall in the same minute. Target filled at the target price.
- **Brackets** (by timeframe; per-rule exceptions are written on the rule):
  - B1h: target 2 R, time exit 24 h after entry;
  - B4h: target 2 R, time exit 72 h;
  - B1d: no target, time exit 7 calendar days (about 5 trading days);
  - ML rules: no target, time exit = the model's horizon (1h: 6 h; 4h: 24 h; 1d: 7 calendar days).
  Time exit = close of the last 1m bar before entry + hold. A path is cut at the window's end (explore: 2017-12-31
  23:59 UTC; confirm: 2025-09-30 23:59 UTC); a trade still open is closed there.
- **One position at a time** per rule: a decision while the rule's previous trade is open is skipped. Unless a rule
  says "on a cross" or "on a transition", a decision fires on every bar where its condition holds.
- **Costs** (ICM, `cor-2026-10-31.2`, ADR 0064 via `goldbot.research.arms.screen_net_r`): per trade the entry
  session's charged round trip (max of the measured upper 90% bound and the frozen $0.23) plus, on a stop exit, the
  ruled overshoot (max($0.12, cell upper 90%)); plus swap at the shipped settings (long -$60/lot/night = -$0.60/oz,
  short $0, triple Wednesday, server Europe/Athens, `rollover_nights`). Net R = (signed move - costs + swap) / R.
  A Vantage stress line (its round trip, overshoot stress $0.70) is reported, not used for the verdict.
- **Matched random control**: 4 twins per real trade, entry minute drawn uniformly (seed 20261102) from the 1m bars of
  the same UTC day and the same session label (`DEFAULT_SESSIONS.session_label`) as the real entry, same side, same
  USD stop distance, same target multiple, same hold, same costs. Control mean = the mean over all twins.
- **Statistics**: per-trade mean net R, t = mean / (sd / sqrt(n)); day-block bootstrap 90% interval reported.

## 2. Pass rules (brief rule 4)

- **Explore** (2010-01-01..2017-12-31; rules needing history trade from their first warm date): n >= 100, net mean
  R > 0 with t >= 2.0, AND real mean - twin mean >= +0.05 R.
- **Confirm** (2018-01-01..2025-09-30, ONCE, explore passers only, all parameters and fitted models frozen at the end
  of explore): net mean R > 0, t >= 2.0, deflated Sharpe > 0 at K, and the same sign (net mean > 0) in >= 6 of the 8
  confirm years (2018..2025, 2025 = Jan-Sep). "Deflated Sharpe > 0" = the per-trade Sharpe exceeds the expected
  maximum Sharpe of K unskilled trials (Bailey & Lopez de Prado 2014, `metrics.deflated_sharpe`, var(SR) = 1/n), i.e.
  DSR probability > 0.5; the probability is reported and a pass below 0.95 is labelled "weak".
- **K**: 3,029 (programme before this sprint) + 200 (all sprint trials) + this lane's variants beyond its 33 (none
  planned) = **3,229**; the report also reads DSR at 3,500.
- A rule failing any leg is FAILED and retired as specified. No neighbouring thresholds, horizons or features after
  the look.

## 3. Fitting discipline (leakage control)

- **Fitted rules** (S01-S03 HMM, S24-S29 ML, S30 k-means): explore = annual expanding walk-forward. For test year Y,
  fit on decisions with decision time < Jan 1 of Y whose outcome window (horizon) ends before Jan 1 of Y (purge), plus
  a 1-day embargo. First test year: 2011 (HMM, fit on 2010) or 2012 (ML and k-means, fit on 2010-2011).
  Confirm: one model per rule fitted on all explore decisions whose horizon ends before 2018-01-01, frozen, applied to
  2018-2025-09 (this is also the model a deployed arm would carry).
- **Features** are computed from bars visible at the decision only; standardisation statistics come from the fit set.
- **Leakage tests** run before any outcome read and are reported: (a) lookahead test: features at 200 random decision
  times recomputed on data truncated at the decision equal the full-data values; (b) shuffle test: each ML model
  trained on permuted labels gives out-of-fold AUC within 0.5 +/- 0.03 (else the screen stops).
- ML probability thresholds are fold-internal: long when p >= the 85th percentile of the fit set's in-sample p,
  short when p <= its 15th percentile.

## 4. Feature sets (fixed, <= 40)

F(tf), on the decision timeframe's bars: log returns over 1, 2, 4, 8, 16, 32, 64 bars divided by ATR14/close;
RV8/RV64 (sd of 1-bar log returns); ATR14/close; VR(4) over 64 bars; lag-1 autocorrelation over 64; skew and excess
kurtosis of 1-bar returns over 64; (close - SMA20), (close - SMA50), (close - SMA200) in ATR; position in the 20- and
55-bar Donchian range (0..1); Kalman slope z (s.5); last bar range / ATR; close location in the last bar (0..1); upper
and lower wick / range; for 1h and 4h: sin and cos of the UTC hour; sin and cos of the weekday. 27 columns intraday,
25 on 1d. All rules share the one set per timeframe (no feature search).

## 5. Shared estimators

- **Kalman local linear trend** on log close: state (level, slope), measurement variance R_t = variance of the last 100
  1-bar log returns (PIT), Q = diag(0.1 R_t, 0.001 R_t). Slope z = slope / sqrt(P_slope,slope). Innovation z =
  (y - predicted level) / sqrt(innovation variance).
- **HMM**: 2-state Gaussian HMM on log returns of the timeframe, Baum-Welch (200 iterations, init by the lower and
  upper halves of |r|), forward-filtered probabilities only (never smoothed). The low-variance state is the one with the
  smaller fitted variance.
- **Realised measures** on 5m mid log returns inside the feature day (1d) or the trailing 24 h (4h): RV, bipower BV,
  realised skew (Amaya et al. 2015 definition), positive/negative semivariances, BNS ratio jump z (tripower
  quarticity; Barndorff-Nielsen & Shephard 2006, Huang & Tauchen 2005 ratio statistic).
- "pct over trailing N" ranks the current value among the previous N values (exclusive), 0..100.

## 6. The 33 rules (id S200-STATS-NN; side +1 long, -1 short)

Regime models
- **01 HMM2-1d-LOWVOL-TREND** (1d, B1d). P(low-var state) >= 0.8 -> side = sign(20-day log return). Mechanism: in calm
  regimes gold's slow trends persist (Moskowitz, Ooi & Pedersen 2012 JFE, TSMOM, futures 1985-2009, gross).
- **02 HMM2-1d-HIGHVOL-REVERT** (1d, B1d). P(high-var) >= 0.8 and |z5| >= 1.5, z5 = 5-day return / (sd of daily
  returns over 60 days x sqrt 5) -> side = -sign(z5). Mechanism: liquidity-driven overshoots revert in stressed regimes
  (Nagel 2012 RFS, equities, gross).
- **03 HMM2-4h-LOWVOL-TREND** (4h, B4h). 4h HMM, P(low) >= 0.8 -> sign(24-bar return).
- **04 RVPCT-1d-LOW-DONCHIAN** (1d, B1d). RV20 (sd of daily returns, 20 days) pct over trailing 252 <= 33 and the close
  > max(high) of the prior 20 days (long) or < min(low) (short). Mechanism: breakouts from compressed volatility carry
  information (volatility clustering, Engle 1982).
- **05 RVPCT-1d-HIGH-FADE** (1d, B1d). RV20 pct >= 67 and |daily return / sd60| >= 2 -> fade.
- **06 RVRATIO-4h-COMPRESSION-BREAK** (4h, B4h). RV6/RV60 of 4h returns < 0.5 and the close breaks the prior 20-bar
  high/low -> with the break.

Kalman trend
- **07 KALMAN-4h-SLOPE-CROSS** (4h, B4h). Slope z crosses above +2 (long) / below -2 (short) on this bar.
- **08 KALMAN-1h-SLOPE-CROSS** (1h, B1h). As 07 on 1h.
- **09 KALMAN-1d-SLOPE-CROSS** (1d, B1d). As 07 on 1d.
- **10 KALMAN-1h-INNOV-FADE** (1h, B1h). |slope z| < 1 and |innovation z| >= 2.5 -> side = -sign(innovation).
  Mechanism: shocks off a flat filtered level are noise in a trendless state.

Autocorrelation / variance ratio
- **11 VR-1h-TREND-MOM** (1h, B1h). VR(4) of 1h returns over the last 256 bars > 1.15 and |z24| >= 1.5 (24-bar
  return / (sd1h x sqrt 24)) -> sign(z24). Mechanism: Lo & MacKinlay (1988 RFS) VR > 1 = positive serial dependence.
- **12 VR-1h-REVERT-FADE** (1h, B1h). VR(4) < 0.85 and |z24| >= 1.5 -> -sign(z24).
- **13 AC-1h-FOLLOW** (1h, B1h). AC1 of 1h returns over 128 bars > +0.10 and |last bar return| >= 1 ATR14 -> with it.
- **14 AC-1h-FADE** (1h, B1h). AC1 < -0.10 and |last bar return| >= 1 ATR14 -> against it.
- **15 VR-1d-MOM** (1d, B1d). VR(5) of daily returns over 250 days > 1.10 and |z20| >= 1 -> sign(20-day return).
- **16 AC-4h-FOLLOW** (4h, B4h). AC1 of 4h returns over 120 bars > +0.08 and |last bar return| >= 1 ATR14 -> with it.
- **17 INTRADAY-MOM-COMEX** (1m clock). Signal = sign(mid at 08:50 NY - mid at the prior day's 13:30 NY); enter at
  the first 1m open at or after 13:00 NY, exit at the close of the 13:29 NY minute; stop 1.5 x ATR14(1h). Mechanism:
  Gao, Han, Li & Zhou (2018 JFE): the first half hour predicts the last half hour (SPY 1993-2013, gross; reported on
  commodity futures by later work *(unverified)*). Twins: same day and session, 30-minute hold.

Return distribution
- **18 RSKEW-1d-CONTRA** (1d, B1d). Realised skew over the last 5 feature days of 5m returns; pct over trailing 252 >=
  80 -> short, <= 20 -> long. Mechanism: Amaya, Christoffersen, Jacobs & Vasquez (2015 JFE): high realised skew
  predicts low next-week returns (US equities 1993-2013, cross-section, gross).
- **19 JUMP-1d-CONT** (1d, B1d). BNS jump z >= 3.09 on the feature day -> sign of that day's return.
- **20 JUMP-1d-REV** (1d, B1d). Same test -> against it.
- **21 SJ-1d-CONTRA** (1d, B1d). SJ5 = sum over 5 feature days of (RS+ - RS-) / RV5; pct >= 80 -> short, <= 20 -> long.
  Mechanism: Patton & Sheppard (2015 REStat); Bollerslev, Li & Zhao (2020 JFQA): positive signed jumps precede lower
  returns (equities, gross).
- **22 MAX-1d-LOTTERY** (1d, B1d). MAX21 (largest daily return in 21 days) pct >= 80 -> short; MIN21 pct <= 20 (most
  negative) -> long; both -> none. Mechanism: Bali, Cakici & Whitelaw (2011 JFE) lottery demand (equities, gross);
  the single-asset adaptation is untested.
- **23 RSKEW-4h-CONTRA** (4h, B4h). Skew of 5m returns over the trailing 24 h; pct over trailing 180 4h bars >= 80 ->
  short, <= 20 -> long.

Fold-internal ML (target y = 1 if the close H bars ahead > the decision close; F(tf); thresholds s.3)
- **24 ML-LR-1h** (1h, H 6 bars, 6 h exit). Logistic regression, L2, C = 1.0, standardised features.
- **25 ML-GBM-1h** (1h, H 6). sklearn HistGradientBoostingClassifier(max_iter 200, learning_rate 0.05, max_leaf_nodes
  15, min_samples_leaf 200, l2_regularization 1.0, random_state 0).
- **26 ML-LR-4h** (4h, H 6 bars, 24 h exit).
- **27 ML-GBM-4h** (4h, H 6).
- **28 ML-LR-1d** (1d, H 5 bars, 7-day exit).
- **29 ML-GBM-1d** (1d, H 5).
- **30 KMEANS-1d-REGIME** (1d, B1d). Features (fit-set z-scored): log RV20, z20, AC1 over 60 days, VR(5) over 250,
  skew of daily returns over 60. k-means k = 4 (n_init 10, random_state 0). In the fit set each cluster's forward
  5-day log return mean and t; t >= 2 -> long in that cluster, t <= -2 -> short, else no trade.
  Mechanism: regime-conditional drift (Hamilton 1989 Econometrica; Ang & Bekaert 2002 RFS), learned in-fold.

Equal-weight ensembles of weak rules
- **31 ENS-4h-TREND** (4h, B4h). Votes sign(r6), sign(r24), sign(r60), sign(SMA10 - SMA40), sign(close - Donchian20
  mid); on the transition into all five equal -> that side.
- **32 ENS-1d-TREND** (1d, B1d). Votes sign(r20), sign(r60), sign(r120), sign(r250), sign(SMA50 - SMA200); on the
  transition into all five equal -> that side. (Neighbour of H-01, disclosed.)
- **33 ENS-4h-STAT** (4h, B4h). Votes: sign(Kalman slope z) if |z| >= 1 else 0; sign(AC1 over 120) x sign(last bar
  return); -sign(RSKEW-4h pct - 50) if the pct is outside 30..70 else 0. On the transition into all three equal and
  non-zero -> that side.

## 7. Trial cost and owner flags

33 explore screens (K +33, inside the sprint's 200), plus one confirm read per explore passer (no extra K: the
sprint's 200 already counts each strategy once). 0 registry trials until the evaluator re-runs a survivor. Mac load: at
most 2 worker processes. Owner-only: none here; deploying a survivor is a demo exploration arm at min lot under the
RiskGate, inside the ADR 0061 qualification gate. Live sizing and instruments stay owner decisions.

## Amendment 1 (2026-11-02, before any outcome was computed): leak-free control

Coordinator correction for all lanes: a same-day twin drawn BEFORE the signal takes its side from that day's later
move, which inflates the control for momentum rules and deflates it for fades. The explore pass leg "beats the matched
random control by >= 0.05 R" now uses the **forward control**: 4 twins per real trade drawn uniformly from the 1m bars
of the same UTC day and session at or after the real entry minute, same side, stop, target and hold; if no such minute
exists, the same UTC time of day (within 15 minutes) on another day of the loaded window. The brief's same-day control
is still computed and reported beside it. Nothing else changes.
