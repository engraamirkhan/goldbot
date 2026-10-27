# Slice C: Fibonacci, wavelets, cycles and fractal regimes (time-structure programme, 2026-10-26)

Staff research lead for the research director. Follows `README.md` of this folder (sections 3-5). Descriptive screen:
**no trial registered, no backtest job, 0 trial budget used**. Scripts: `docs/research/candidates/time_structure/ts_common.py` (loaders),
`ts_fib.py` (levels), `ts_fib_breakout_check.py` (follow-up), `ts_cycles.py` (wavelets, Ehlers, Hurst, Goertzel, EMD,
Fibonacci time). Data: Dukascopy 1m mid via `goldbot.data.resample`, **2010-01-01 .. 2017-12-31 only** (the loader
never reads 2018+; the confirmation half was **not** looked at, and neither was the holdout). Macro: FRED
`real_yield_10y` and `broad_dollar` joined with `asof_join` on `available_utc`.

## 0. Verdicts in one screen

| Topic | Verdict | Why (discovery half) |
|---|---|---|
| Fibonacci retracement/extension levels (Asia, prior day, 30/60/90-day swings; 15m/1h/4h/1d) | **close** | 0 of 26 fib cells beat randomised levels after BH correction (min q 0.52); gross R about 0 to -0.1; net negative everywhere |
| Fibonacci confluence with session opens/prior close | **close** | confluent vs non-confluent difference vs control +0.003 .. +0.02 R, p > 0.2 |
| Fibonacci time zones | **close** | 13/21/34/55/89-day offsets p 0.07 (pivot +-10d) and 0.33 (+-20d); the 13-day cell sits at the pivot rule's own minimum gap |
| Wavelet denoise/trend as a signal | **close** | causal db4 band components have no directional IC (all \|t\| < 2 except one isolated j3 cell, 5d, t -1.9) |
| Wavelet scale energy | **feature** (volatility, not direction) | adds to the RV20 baseline: 1d j3 t +5.6, 1h j5 t +6.3; it is HAR-RV in other clothes, so expect modest, correlated gain |
| Wavelet coherence with real yield / dollar | **close as signal** | strong contemporaneous scale correlation (-0.43 at 16-64 d) but macro-at-t does not predict gold (\|t\| <= 1.7) |
| Ehlers roofing / Hilbert dominant cycle / sinewave | **close** (roofed amplitude: feature) | dominant cycle is about 21 bars at 1h, 4h and 1d alike (scale-free = noise); ICs 0.00-0.04 |
| Hurst / fractal regime | **close** | R/S H on 1h/4h equals its shuffled-returns null; trend/MR payoff by H tercile all \|t\| < 1.2 |
| Spectral / Goertzel 30/60/90 d | **close** | sinusoid-extrapolation IC grid: 30/60/90 rank 19/5/22 of 23, none significant |
| EMD / HHT | **close** | end effects: IMF1-3 at t move 0.4-0.7 daily SDs when 60 later bars are added; slope sign agrees only 63% |
| **New: first touch of a 30-day extreme, daily horizon** | **one candidate (C-1)** | continuation +0.20 R (block t 3.7), survives a 15-minute entry delay; short side only is robust; below the gate-1 bar |

## 1. What was already tried (checked first)

- `indicator-survey.md` rows 20 and 55 (rolling Hurst/DFA, floor pivots/Fibonacci: "self-fulfilling at most") and its
  banned list (centred filters, wavelet denoising, two-sided HP); `knowledge-base/support-resistance-and-zones.md`
  ("Pivots and Fibonacci ratios have no good evidence"); `hypotheses.md` R-09 (Fibonacci retired as a rule, kept as bulk
  feature); `goldbot/features/survey.py` (`regime_stats`: variance ratios, rolling R/S Hurst 128, efficiency ratio,
  built for H-02, not yet run); `indicator-coverage-2026-10-18.md`, `state-of-the-art.md` (no wavelet/Ehlers/EMD row).
- Director's measurements `README.md` 6.7 (periodogram: no price cycle in 20-120 d, a volatility cycle near 103 d;
  variance ratios flat) and 6.8 (Fibonacci 23.6-78.6% of prior-day and Asia range vs 40 random fractions in
  [0.15, 0.85]: diff -0.032 and -0.001 ATR15). I did **not** redo those. What is new here: 30/60/90-day swings,
  extensions (127.2, 161.8, -27.2, -61.8), four timeframes with ATR-scaled first-passage outcomes, distance-stratified
  controls, confluence, causal wavelets, Ehlers, Hurst calibration, Goertzel grid, EMD end effects, Fibonacci time zones.
- No trial in the registry covers any of these (no wavelet, Ehlers, Goertzel, EMD or Fibonacci trial).

## 2. Method and the looks behind it

- Levels: `level = low + f * (high - low)` for ranges known at the start of the active window (07:00-22:00 UTC; the
  swing families also 5 days for the daily horizon); f in {0.236, 0.382, 0.5, 0.618, 0.786} (retracement),
  {1.272, 1.618, -0.272, -0.618} (extension), {0, 1} (the range edges, as a sanity control). Control: 20 random f per
  day and range in [-0.8, 1.8], more than 0.02 from any Fibonacci/edge f (fixed seed), compared **inside bins of
  distance-to-price in ATR** so real and control levels have the same distance distribution; retracements against
  controls inside [0.1, 0.9], extensions against controls outside [-0.1, 1.1] (an unrestricted control mixes
  breakout-type touches into the baseline and flatters Fibonacci: my first pass did that, +0.03 to +0.05 R "wins" at
  1h that vanished once the control was matched).
- Outcome: first touch on 1m mid; bounce (+b with the approach side) before break (-b), b = 0.5 x ATR14 of the
  timeframe (15m $0.65, 1h $1.6, 4h $3.5, 1d $8.5 mean); timeouts 4 h / 12 h / 48 h / 120 h mark to market. Entry
  assumed **at the level** (a resting limit: optimistic); same-minute double hit counts as a loss. 600 day-block
  (10-day blocks) bootstraps. Net R subtracts $0.144 (central) and $0.23 (conservative) from
  `cost-model-1m-2026-10-25.md` divided by b.
- Looks behind the tables: ts_fib 42 level cells + 6 confluence cells + 16 edge/lag cells (64); ts_cycles about 120
  (scales 46, macro 14, Ehlers 10, Hurst 6, Goertzel 23, Fibonacci time 2, EMD 1, causality checks). About 185 looks in
  all, none corrected except where "BH q" is shown; read any lone |t| of 2-3 as noise (Bonferroni 5% near t 3.8).

## 3. Fibonacci levels

### 3.1 Table: Fibonacci retracement and extension vs distance-matched random levels (2010-2017, k = 0.5 ATR)

R is the bounce first-passage mean in units of b (0 = coin flip; negative = levels break more than they hold). diff =
real minus control (distance-stratified), se day-block bootstrap. Net columns use the **real** set.

| TF | Reference | Set | n real / ctl | R real | R ctl | diff | se | p | BH q | net R central | net R cons. |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 15m | Asia | retr | 8,183 / 8,885 | -0.030 | -0.045 | +0.016 | 0.014 | 0.21 | 0.64 | -0.28 | -0.44 |
| 15m | Asia | ext | 4,141 / 11,908 | -0.094 | -0.073 | -0.021 | 0.016 | 0.21 | 0.64 | -0.36 | -0.51 |
| 15m | prior day | retr | 5,364 / 5,798 | -0.069 | -0.052 | -0.018 | 0.021 | 0.42 | 0.64 | -0.33 | -0.48 |
| 15m | 30d swing | retr | 1,468 / 1,695 | -0.056 | -0.074 | +0.018 | 0.037 | 0.66 | 0.75 | -0.31 | -0.47 |
| 15m | 60d swing | retr | 1,077 / 1,145 | -0.140 | -0.101 | -0.040 | 0.044 | 0.40 | 0.64 | -0.39 | -0.54 |
| 1h | Asia | retr | 8,183 / 9,064 | +0.002 | -0.029 | +0.031 | 0.013 | 0.020 | 0.52 | -0.10 | -0.16 |
| 1h | Asia | ext | 4,141 / 11,861 | -0.021 | -0.037 | +0.016 | 0.014 | 0.26 | 0.64 | -0.12 | -0.19 |
| 1h | prior day | retr | 5,364 / 5,820 | +0.003 | -0.010 | +0.013 | 0.019 | 0.49 | 0.64 | -0.10 | -0.16 |
| 1h | 30d swing | retr | 1,468 / 1,690 | -0.008 | -0.048 | +0.040 | 0.035 | 0.21 | 0.64 | -0.11 | -0.17 |
| 1h | 60d swing | retr | 1,077 / 1,192 | -0.114 | -0.066 | -0.048 | 0.040 | 0.19 | 0.64 | -0.22 | -0.28 |
| 1h | 90d swing | retr | 918 / 944 | -0.078 | -0.034 | -0.045 | 0.049 | 0.36 | 0.64 | -0.18 | -0.24 |
| 4h | Asia | retr | 8,183 / 8,929 | -0.016 | -0.030 | +0.014 | 0.012 | 0.26 | 0.64 | -0.06 | -0.09 |
| 4h | prior day | retr | 5,364 / 5,743 | -0.006 | -0.002 | -0.003 | 0.016 | 0.86 | 0.91 | -0.05 | -0.08 |
| 4h | 30d swing | retr | 1,468 / 1,692 | +0.011 | -0.025 | +0.036 | 0.040 | 0.38 | 0.64 | -0.04 | -0.06 |
| 1d | prior day | retr | 7,180 / 7,785 | +0.003 | -0.013 | +0.016 | 0.014 | 0.23 | 0.64 | -0.02 | -0.03 |
| 1d | prior day | ext | 3,434 / 9,886 | -0.078 | -0.079 | +0.001 | 0.012 | 0.94 | 0.94 | -0.10 | -0.11 |
| 1d | 30d swing | retr | 2,778 / 3,088 | -0.027 | -0.032 | +0.004 | 0.028 | 0.87 | 0.91 | -0.05 | -0.06 |
| 1d | 60d swing | retr | 2,054 / 2,196 | +0.007 | +0.045 | -0.038 | 0.042 | 0.34 | 0.64 | -0.01 | -0.02 |
| 1d | 90d swing | retr | 1,742 / 1,816 | -0.051 | -0.019 | -0.032 | 0.044 | 0.51 | 0.64 | -0.07 | -0.08 |

(The other 8 cells, mostly 4h/1d extensions and the 4h 90d swing, are in `fib_results_k0.5.csv` written by `ts_fib.py`: same picture, 4h 90d swing diff -0.060, p 0.19.) Reading:
- 26 retracement/extension cells (15 with positive diff); the best is 1h Asia retracement diff +0.031 R (p 0.020, **BH q 0.52**). None survives.
  The sign of diff splits about evenly. The two halves (2010-13 vs 2014-17) disagree in sign in 11 of 26 cells.
- Gross R is near zero or negative **in absolute terms**: a Fibonacci level is not a place where gold bounces more than
  a random place at the same distance. Net of cost it is -0.04 to -0.5 R, the cost share rising as the timeframe falls
  (15m: $0.144 / $0.65 = 0.22 R; the research-prior cost $0.37 + spread is larger still). Fibonacci at 15m cannot pay.
- Range edges (prior high/low, 30/60/90-day extremes) show the opposite of support/resistance: they **break** more
  than controls (diff -0.15 to -0.30 R). That is the only structure found; section 3.3 below.

### 3.2 Confluence with session references

References: prior-day close, Asia open, London open (07:00 price), NY open price (13:00 UTC, only after it forms); a
level is confluent when within 0.25 ATR of one already formed. Retracements vs matched controls:

| TF | Confluent? | n real | R real | R ctl | diff | p |
|---|---|---|---|---|---|---|
| 15m | no / yes | 13,803 / 3,207 | -0.048 / -0.088 | +0.008 / -0.027 | 0.46 / 0.26 |
| 1h | no / yes | 10,287 / 6,723 | -0.014 / -0.005 | +0.014 / +0.020 | 0.28 / 0.20 |
| 4h | no / yes | 6,293 / 10,717 | +0.018 / -0.038 | +0.004 / +0.003 | 0.75 / 0.81 |

Confluence adds nothing. Close.

### 3.3 The one structural fact: extremes break, and it is an entry-fill artefact intraday

First touch of a prior-day or 30/60/90-day extreme: bounce R -0.14 to -0.31 (continuation +0.14 to +0.31 R), at every
timeframe (1h prior-day: positive in 8 of 8 years at limit fill; 1d 30-day: 7 of 8). A limit-at-level fill, however, is the wrong entry for a breakout. Re-entering a continuation
trade at the close of the 1m bar `lag` minutes after the touch (`ts_fib_breakout_check.py`, block-clustered t):

| Horizon, family | n | at level | +1 min | +5 min | +15 min |
|---|---|---|---|---|---|
| 1h, prior day | 1,581 | +0.171 (t 6.8) | -0.016 | -0.045 | -0.013 |
| 1h, 30d | 334 | +0.251 (5.1) | +0.060 | +0.018 | +0.042 |
| 1h, 60d / 90d | 231 / 166 | +0.264 / +0.289 | +0.108 / +0.024 | +0.065 / +0.036 | -0.022 / -0.024 (t < 1) |
| 1d, prior day | 2,428 | +0.086 (4.2) | +0.033 | +0.024 | +0.022 (t 1.0) |
| **1d, 30d** | 655 | +0.249 (5.0) | +0.151 (2.8) | +0.185 (3.5) | **+0.203 (3.7)** |
| 1d, 60d | 462 | +0.257 (4.3) | +0.196 (3.0) | +0.195 (2.9) | +0.173 (2.4) |
| 1d, 90d | 343 | +0.219 (3.0) | +0.137 (1.8) | +0.155 (2.0) | +0.149 (1.7) |

Lesson for every level study: the "edge" a level seems to give at 15m-1h is the touch minute itself (the bar that
tags the level has already moved through it; the scan starting at the next minute inherits that move). **Intraday it
vanishes with a one-minute delay; at the daily horizon (barrier $8) it survives.** This is candidate C-1 below. Controls:
random-time entries on the same days with the same barriers give short R -0.014, long +0.014 (se 0.016), so the effect
is not gold's 2010-2017 downward drift.

## 4. Wavelets (causal only)

### 4.1 The look-ahead trap, shown, and how it is avoided

Whole-sample wavelet denoising (SWT, db4, 4 levels, universal soft threshold, then reconstruct) uses bars after t for
the value at t. Measured on daily log price (n 2,490), IC of sign(s_t - s_{t-1}) vs the next-day return:

| Series | Whole-sample denoise (wrong) | Causal db4 smooth (a trous, backward filters) |
|---|---|---|
| Gold | **+0.342** | +0.005 |
| Random walk with gold's vol | **+0.392** | -0.006 |

A "0.34 IC" on a random walk is the whole story. The same denoiser recomputed on each prefix x[:t+1] (what live would
see) gives IC +0.028 (n 298), and its last-point value differs from the whole-sample value by **2.2 daily SDs** on
average: that is the boundary effect. How the study avoids it: `causal_modwt` applies the MODWT filters only to
x[t - l * 2^(j-1)], never wraps, and returns NaN for the first (L_j - 1) outputs (the boundary region is dropped, not
padded or reflected). `check_causal` shows value(T) from x[:T+1] equals value(T) from the full series: max |diff|
**0.0** for Haar and db4 (also for the roofing filter and the Hilbert cycle, section 5). Note the causal Haar
approximation is a plain moving average, so "wavelet trend" at one scale is an SMA with a name; the fancier wavelets
change the lag/shape trade-off, not the information.

### 4.2 Scale energy and direction (daily and hourly, db4 MODWT, levels 1-7/8)

Energy share of returns by scale j (period about 2^j to 2^(j+1) bars): 1d `1:1% 2:2% 3:3% 4:8% 5:16% 6:25% 7:45%`;
1h `1:1% 2:1% 3:2% 4:3% 5:6% 6:13% 7:25% 8:49%` (most variance is at the slowest scale, as for any random walk with
fat tails; a spike at 16-32 or 32-64 days would be a cycle, there is none).

IC of the causal band component W_j[t] with the forward return (daily: non-overlapping, t = IC sqrt(n-2)/...; hourly:
pooled with a day-block bootstrap):

| Horizon | j1 | j2 | j3 | j4 | j5 | j6 | j7 |
|---|---|---|---|---|---|---|---|
| 1d -> 5 d (n about 495) | -0.02 | +0.01 | -0.09 (t -1.9) | -0.05 | -0.05 | +0.01 | +0.08 (t 1.5) |
| 1d -> 20 d (n about 120) | -0.02 | -0.09 | -0.10 | +0.01 | +0.04 | 0.00 | +0.13 (t 1.1) |
| 1h -> 24 h | -0.001 | +0.002 | +0.006 | +0.005 | -0.002 | -0.003 | -0.006 (all \|t\| < 1.3) |

No scale carries direction. This agrees with the director's variance ratios and periodogram (README 6.7): there is no
price cycle at 30/60/90 days.

### 4.3 Scale energy and volatility, against the RV baseline the director found predictable

Regression log RV(next h) on log RV20 plus one wavelet feature (rolling-20d mean of W_j^2; HAC errors). Baseline R2:
daily 0.097 (h = 5), hourly 0.252 (h = 24).

| Series | Feature | Coef | t | R2 gain |
|---|---|---|---|---|
| 1d | log E_j3 (8-16 d) | +0.295 | **+5.6** | +0.039 |
| 1d | log E_j1 | +0.481 | +2.7 | +0.012 |
| 1d | log E_j7 | +0.056 | +2.1 | +0.009 |
| 1d | log E_j5, E_hi/E_lo | +0.03, +0.01 | 0.7, 0.1 | about 0 |
| 1h | log E_j5 (32-64 h) | +0.138 | **+6.3** | +0.013 |
| 1h | log E_j7 (128-256 h) | +0.074 | +4.8 | +0.009 |
| 1h | E_hi/E_lo | -0.074 | -4.2 | +0.006 |
| 1h | log E_j1 | -0.408 | -3.3 | +0.003 |

Wavelet energy by scale is a multi-horizon realised variance, i.e. the HAR-RV idea (Corsi 2009) restated; the gains
are real in t terms and small in R2. They are feature material for the volatility/range model (H-16, H-02), not a
signal. Only the daily j3 gain (+3.9 points of R2) is large enough to matter, and its overlap with RV5/RV20 features
already in `regime_stats`/vol groups must be checked in the group's fold-internal selection, not assumed.

### 4.4 Wavelet correlation with real yield and the dollar (daily, 2010-2017)

Causal db4 band components of gold, real-yield and log dollar series, the macro series joined by `asof_join` on
`available_utc` (FRED values arrive about one day late, so this is the tradable alignment).

| Macro | corr(W_gold, W_macro) by scale j1..j6 | IC of W_macro[t] -> gold next 5 d |
|---|---|---|
| Real yield 10y | +0.11, +0.04, -0.16, **-0.44, -0.43, -0.52** | +0.03, +0.02, +0.08 (t 1.7), 0.00, +0.04, -0.01 |
| Broad dollar | 0.00, -0.07, +0.03, -0.05, **-0.33, -0.42** | -0.01, -0.04, -0.04, -0.06, -0.04, -0.04 (all \|t\| <= 1.2) |

Gold moves against real yields and the dollar at scales above 16 days (the known relation, grade B), but the macro
level is not ahead of gold: corr(W_gold[t], W_macro[t - lag]) at j5 is -0.43/-0.42/-0.40/-0.31 for lags 0/1/2/5 (it
decays slowly because the band is slow, not because macro leads). At 5-day horizons the IC is nil. So coherence is a
description of regime, usable as a context feature at 1d/4h at most (the macro-researcher's lane), not a trigger. The
continuous wavelet transform (CWT/Morlet coherence of Torrence-Compo, Grinsted et al.) was **not** run: its
cone-of-influence edge region is exactly the last bars a live system needs, and the MODWT correlation above answers
the scale question without it.

## 5. Ehlers cycle tools, Hurst, Goertzel, EMD

### 5.1 Ehlers (Hilbert dominant cycle, roofing filter, sinewave)

Roofing = 2-pole high-pass (48 bars) then SuperSmoother (10); dominant cycle = Hilbert-transform homodyne
discriminator (Ehlers 2001); sinewave signal = sin(phase + 45 deg) - sin(phase). Both recursive and causal
(`check_causal` error 0.0).

| TF | Roofing sign IC (h) | Roofing level IC | Sinewave IC | Dominant cycle median (IQR) | lag-20 autocorr of cycle |
|---|---|---|---|---|---|
| 1h (n 47,300) | -0.007 (1 bar, t -1.9), +0.009 (4), +0.007 (24) | -0.015 (t -3.9), 0.000, +0.004 | -0.010, -0.012, +0.001 | 20.4 bars (17.6-24.4) | 0.05 |
| 4h (n 12,814) | -0.015, -0.022 (6) | -0.018, -0.027 | -0.030 (t -3.8), +0.013 | 20.7 (17.4-25.0) | 0.08 |
| 1d (n 2,490) | +0.014, +0.025 (5) | +0.022, +0.036 (t 2.0) | +0.009, -0.004 | 21.8 (17.7-26.3) | 0.09 |

The decisive row is the cycle: **the same 20-22-bar "dominant cycle" at 1 hour, 4 hours and 1 day**, with no memory
beyond a few bars. A real cycle would scale with the timeframe; this is the filter's own passband (Slutsky 1937;
a band-pass filter turns white noise into apparent cycles). The 1h/4h ICs of -0.01 to -0.03 are the same small
short-horizon reversal the director found for RSI (README 6.10, 0.04 ATR15), far below cost. Roofed-amplitude adds to
the RV baseline at 1h only (coef +0.14, t +3.2, R2 0.253 -> 0.256): a feature at best.

### 5.2 Hurst (goldbot rolling R/S, window 128 returns) and the regime idea

| TF | H gold (sd) | H after shuffling returns | Trend-following payoff next h bars by H tercile (causal 1000-bar percentile): low / mid / high (t) |
|---|---|---|---|
| 1h | 0.578 (0.067) | 0.580 (0.074) | +1.9 bp (0.5) / +1.7 (0.4) / -4.5 (-1.1) over 24 bars |
| 4h | 0.574 (0.070) | 0.578 (0.073) | +0.5 (0.1) / +4.2 (1.1) / +3.0 (0.9) over 6 bars |
| 1d | 0.601 (0.070) | 0.582 (0.066) | +15.6 (0.8) / +2.6 (0.1) / +6.4 (0.4) over 5 d, 20-d sign |

R/S Hurst has a large positive finite-window bias (about 0.58 on pure noise, Anis-Lloyd 1976); gold at 1h/4h **equals**
its shuffled null, and at 1d it is only +0.02 above it. H has no persistence (lag-128 autocorrelation -0.01 to -0.08),
so it cannot select a regime. Regimes defined by H do not separate trend-following from mean-reversion payoffs
(all |t| < 1.2). H as a volatility predictor is weak (1h coef +0.50, t +2.3; others |t| < 1.2). Verdict: keep the
existing `hurst_128` out of any rule; at most one feature inside H-02's existing group.

### 5.3 Spectral (Goertzel single-bin) at 30/60/90 days

Trailing 360-day window, linear detrend, DFT bin at period P, fitted sinusoid extrapolated P/4 days; IC with the
realised change, non-overlapping. Grid P = 10..120 step 5 (23 periods) so that 30/60/90 can be compared with
neighbours: IC at 30/60/90 = -0.020 / +0.063 / +0.009 (|t| < 1); grid mean -0.002, sd 0.056; **|IC| rank of the three
among 23: 19, 5, 22** (none stands out; the largest grid IC, P110 +0.15 with n 78, is noise at this grid size). There is
no 30/60/90-day price cycle to harvest, in line with README 6.7 (the only periodicity is in |returns| near 103 days).

### 5.4 EMD / Hilbert-Huang

Sifting (10 iterations, spline envelopes with end points pinned to data, IMF 1-3) on a 250-day window ending at t vs
the same window start extended 60 days past t (92 cuts): the IMF value at t changes by **0.38, 0.38, 0.70 daily SDs**
for IMF 1, 2, 3, and the slope sign of IMF3 agrees in only 63% of cuts (coin flip is 50%). Spline end effects mean the
newest IMF values are the least reliable; EMD with pinned or mirrored ends is **not** point-in-time safe, and the only
causal use is recomputing on the prefix at every bar (which is what the numbers above are), at which point the
signal is the unreliable edge. (IMF4 is the unconverged remainder and is not read.) Close.

### 5.5 Fibonacci time zones

Pivot = extreme of a centred +-w-day window of daily closes, **confirmed w days later**; for every pivot pair within
120 days I count offsets (+-1 day tolerance) and compare the mean count at {13, 21, 34, 55, 89} with 5000 random
5-offset sets from the offsets above the pivot rule's own minimum gap.

| Pivot window | Pivots | Fib offsets 13/21/34/55/89 | 30/60/90 | Fib mean vs random mean (sd) | p |
|---|---|---|---|---|---|
| +-10 d | 144 | 33 / 27 / 23 / 27 / 22 | 26 / 22 / 24 | 26.4 vs 23.7 (1.8) | 0.070 |
| +-20 d | 78 | 9 / 7 / 5 / 11 / 5 | 8 / 8 / 5 | 7.4 vs 6.8 (1.2) | 0.33 |

The only elevated cell, 13 days, lies against the pivot rule's minimum gap where counts are structurally high and
the random pool does not reach; without it the set is at the random mean. This extends README 6.8's gap table with the
30/60/90 comparison and the permutation. Close.

## 6. Literature

Peer-reviewed or primary first; *(unverified)* = cited from memory, not re-checked online in this session (no network).
- Lo, Mamaysky, Wang 2000, "Foundations of technical analysis", *J. Finance* 55(4). Brock, Lakonishok, LeBaron 1992,
  *J. Finance* 47(5). Park & Irwin 2007, "What do we know about the profitability of technical analysis?", *J. Econ.
  Surveys* 21(4). Evidence on Fibonacci specifically is almost entirely practitioner books (grade D) *(unverified: no
  peer-reviewed gold test found)*.
- Osler 2000, "Support for resistance: technical analysis and intraday exchange rates", *FRBNY Econ. Policy Rev.* 6(2);
  Osler 2003, "Currency orders and exchange rate dynamics", *J. Finance* 58(5) (stop-loss clusters beyond round numbers
  and extremes: the mechanism behind the breakout continuation in 3.3). Kavajecz & Odders-White 2004, "Technical
  analysis and liquidity provision", *Rev. Financial Studies* 17(4).
- Percival & Walden 2000, *Wavelet Methods for Time Series Analysis*, CUP (MODWT, boundary region). Donoho & Johnstone
  1994, *Biometrika* 81(3) (wavelet shrinkage; whole-sample). Gencay, Selcuk, Whitcher 2002, *An Introduction to
  Wavelets and Other Filtering Methods in Finance and Economics*, Academic Press. Whitcher, Guttorp, Percival 2000,
  *J. Geophys. Res.* 105 (wavelet variance/correlation) *(unverified volume)*. Torrence & Compo 1998, *Bull. AMS* 79(1)
  (CWT, cone of influence). Grinsted, Moore, Jevrejeva 2004, *Nonlin. Processes Geophys.* 11 (wavelet coherence).
  Aguiar-Conraria & Soares 2014, *J. Econ. Surveys* 28(2). Rua & Nunes 2009, *J. Empirical Finance* 16(4).
  Corsi 2009, "A simple approximate long-memory model of realized volatility", *J. Financial Econometrics* 7(2).
- Ehlers 2001, *Rocket Science for Traders*; Ehlers 2013, *Cycle Analytics for Traders*, Wiley (practitioner, grade D;
  no peer-reviewed validation found). Slutsky 1937, *Econometrica* 5(2) (filters create cycles). Cogley & Nason 1995,
  *J. Econ. Dyn. Control* 19 (filter-induced spurious cycles). Granger 1966, "The typical spectral shape of an economic
  variable", *Econometrica* 34(1). Goertzel 1958, *Am. Math. Monthly* 65(1).
- Hurst 1951, *Trans. ASCE* 116; Mandelbrot & Wallis 1969, *Water Resources Res.* 5(5); Anis & Lloyd 1976,
  *Biometrika* 63(1) (R/S small-sample bias); Lo 1991, "Long-term memory in stock market prices", *Econometrica*
  59(5); Lo & MacKinlay 1988, *Rev. Financial Studies* 1(1) (variance ratio); Peng et al. 1994, *Phys. Rev. E* 49 (DFA);
  Weron 2002, *Physica A* 312 (finite-sample Hurst estimators).
- Huang et al. 1998, *Proc. R. Soc. A* 454; Wu & Huang 2009, *Adv. Adapt. Data Anal.* 1(1) (EEMD); Flandrin, Rilling,
  Goncalves 2004, *IEEE Signal Process. Lett.* 11(2) (EMD as a filter bank). End effects: Huang's own papers note them
  *(unverified cite)*.
- Moskowitz, Ooi, Pedersen 2012, *J. Financial Econ.* 104(2); Hurst, Ooi, Pedersen 2017, *J. Portfolio Mgmt* 44(1)
  (time-series momentum; slow, diversified).
- Bailey & Lopez de Prado 2014, "The deflated Sharpe ratio", *J. Portfolio Mgmt* 40(5); Lopez de Prado 2018, *Advances
  in Financial Machine Learning*, Wiley (look-ahead, purging).

## 7. Candidate hypotheses (ranked; at most one worth a Q1 free-reserve slot)

Common: 2010-2017 evidence only; the 2018-2025-09 half is untouched (honest: the whole table above is on discovery).

### C-1  Daily continuation after the first touch of a 30-day extreme (short side)
- Mechanism: stops and entries cluster just beyond N-day extremes (Osler 2003; Kavajecz & Odders-White 2004), so the
  first trade-through triggers a cascade of orders in the direction of the break. Evidence grade: C (order-flow
  logic, thin gold evidence).
- Already tried?: new in this form. Not R-04 (1h Donchian/range breakout on bars, no gross edge) and not H-01 (monthly
  TSMOM): this is a first-passage continuation after a touch of a prior-days extreme at the daily horizon. **Slices B
  and F overlap here (Donchian, 30/60/90-day windows): the director must merge, not duplicate.**
- Timeframe and clock: decision on 1d context, execution from the 07:00 UTC window; extreme = trailing 30 opportunity
  days (22:00-22:00 UTC) ended before the day's start (no repaint: known at 22:00 the day before).
- Entry: market order 15 minutes after the first 1m touch of the 30-day low; short only. Side: short.
- Exit: stop and target +-0.5 ATR14(1d) (about $8), time exit 5 days. One position at a time: y.
- Control: random-time entries same days, same barriers (short R -0.014, se 0.016); matched random levels (diff -0.20
  R, p < 0.001, section 3).
- Discovery-half evidence: all 30-day touches (both sides) +0.203 R, block t 3.65 (n 655 events, 171 blocks);
  non-overlapping 5-day events +0.228 R, t 3.15 (n 189); **break-low +0.277 R, t 3.85 (n 310)**, break-high +0.136 R,
  t 1.7 (n 345); by year 0.00/+0.24/+0.04/+0.56/+0.02/+0.28/+0.36/+0.19 (2010-2017; 2013 and 2015, the gold bear
  legs, carry it). Looks: about 64 in ts_fib plus this follow-up.
- Cost check: gross 0.277 R x $7.9 = about $2.2/oz on break-low; research-prior cost about $0.6 (spread $0.22 + $0.37)
  = 0.075 R, no swap on shorts: net about +0.20 R. Broker cost $0.144 = 0.018 R. Edge/cost about 3-4x research-prior.
  Longs pay -$0.60/oz a night and break-high is weaker (+0.136 R, t 1.7): excluded.
- Expected events: break-low 310 raw, about 118 non-overlapping over 2010-2017 = **about 15 a year, 1.2 a month**.
  Below the 150 floor for a daily rule: retire-only.
- Kill rule (0 trials): close if the break-low non-overlapping mean on a pre-registered extension of the sample (a
  shifted window or 60/90-day variants, same rule) has block t < 2, or the effect is explained by the 2013/2015 legs
  (drop those two years: if the 6-year mean R < 0.10). Not yet run: it would need the confirmation half.
- Read rule (if a trial): continue if net R > 0.10 and t > 2 on 2018-2025-09 on the pre-registered event set; stop
  otherwise; inconclusive below 100 events.
- Route: **Q1 free-reserve trial only if the director merges it with slice B/F's 30-day breakout and the combined set
  clears 150 events; otherwise a 5m/daily exploration arm** (cap per ADR 0043 swing £40, stop $8 = £6 at 0.01 lot).
- Prior P(continue): 0.15. Gate-1's t of 4.3 at K 145 is not met even on discovery (3.2-3.9); regime dependence is the
  main risk. It is the most promising result in this slice, which says how thin the slice is.

### C-2  Wavelet-scale energy as volatility/range features (H-02, H-16)
- Mechanism: variance at 8-16 d (daily) and 32-64 h (hourly) scales carries information beyond RV20 about the next
  week's/day's realised range (HAR logic).
- Already tried?: partly (RV20 -> next RV, README 6.7); new: causal MODWT scale split.
- Timeframe: 1d and 1h features; no entry or exit (feature). Control: RV20-only baseline (table 4.3).
- Evidence: daily j3 t +5.6 (R2 0.097 -> 0.136); hourly j5 t +6.3 (0.252 -> 0.265); looks 10.
- Cost check: n/a (feature). Expected trades: n/a. Kill rule: drop from the group if fold-internal selection never
  keeps it, or if its permutation importance is below the RV5/RV20 columns it correlates with.
- Route: feature group (H-02/H-16), at most **5 columns** (below). Prior P(continue): 0.4 as a feature, low as an edge.

### C-3  Roofed-filter amplitude as an opportunity/volatility feature (1h)
- Mechanism: band-pass amplitude of the roofed price = local oscillation size. Evidence: 1h coef +0.14 t +3.2, R2
  gain 0.003 (tiny); 4h and 1d nothing. Route: feature at most (1 column), otherwise drop. P(continue) 0.2.

### Dropped (kill rule already hit at 0 trials)
- C-4 Fibonacci levels/extensions/confluence (best BH q 0.52; net negative): **drop**, feature value at most as the
  "distance to nearest 61.8%" bulk column already planned in R-09, expected importance zero.
- C-5 Fibonacci time zones, C-6 Goertzel/30-60-90 cycle extrapolation, C-7 Ehlers dominant cycle/sinewave,
  C-8 Hurst/fractal regime gate, C-9 EMD, C-10 wavelet-coherence macro trigger: **drop**. Real yield/dollar remain a
  slice-macro context feature at 1d.

## 8. Features for ML under the 40-feature cap

Fold-internal selection only. Ranked by evidence; the cap is 40 per model, so this slice offers **5 columns**, not a
group:

| Rank | Feature (timeframe) | Evidence |
|---|---|---|
| 1 | `wav_logE_j3_1d`: log rolling-20d mean of squared db4 level-3 MODWT coefficient | next-5d RV, t +5.6 beyond RV20 |
| 2 | `wav_logE_j5_1h` (32-64 h) | next-24h RV, t +6.3 |
| 3 | `wav_logE_j7_1h` (128-256 h) | next-24h RV, t +4.8 |
| 4 | `wav_ehi_elo_1h` (E_j1+j2 over E_j7+j8) | t -4.2 |
| 5 | `roof_amp_1h` (rolling mean square of the roofing filter) | t +3.2 |

Not offered: any directional wavelet/Ehlers/Hurst/Goertzel column (zero IC), Fibonacci distance columns (zero
information beyond R-09's existing bulk features). `hurst_128` exists in `regime_stats`; I would not add `1d` Hurst
(+0.02 over its shuffled null). All five are causal (value at t identical with later bars deleted); remember to drop
the first 400-1,000 bars (boundary region) and verify the prefix-equality check in the feature's test.

## 9. What this slice would stop doing

- Stop any Fibonacci, Elliott, Gann, harmonic, time-zone and Ehlers/Hilbert-cycle rule at every timeframe.
- Stop wavelet denoising as a price signal; stop CWT/EMD as inputs (end effects).
- Stop Hurst as a regime gate; keep only vol-scale features.
- Process lesson for every level/OB/FVG/sweep slice (E, F): a limit-at-level fill after a first touch bakes in the touch
  minute; re-test every level "edge" with a market entry a minute or more after the touch, and match the control to the
  level's distance distribution and to whether the level is inside or outside the reference range.
- Not run (stated limits): CWT/Morlet coherence, DFA, fractal dimension (Higuchi/Katz) and EEMD were not measured; the
  R/S Hurst test shows the estimator family has no regime content here, and DFA/FD tests would be added looks with
  little prior; wavelet packets and nonlinear (thresholded) causal denoising are untested.
- Data caveats: Dukascopy mid, 2010-2017 prices ($1,000-1,900), spreads wider than today; the 15-minute entry fills
  at mid (add ask/bid at the research prior); no 2010-2017 swap model beyond the stated -$0.60/oz long night.
