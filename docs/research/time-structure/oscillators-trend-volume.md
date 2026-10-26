# Slice D: oscillators, trend and volume on XAUUSD (15m / 1h / 4h / 1d)

Staff research lead for the research director, 2026-10-26. Follows `README.md` (the programme frame; it sits in the
director's worktree, not this one). Descriptive, **0 trials, 0 backtest jobs**. Script:
`scripts/research/time_structure_osc_trend_volume.py` (reuses `goldbot.features.technical` `atr`, `adx`, `rsi`, `mfi`,
`ema`, `sma`; `resample_bars`, `mid`). Data: Dukascopy 1m mid, local Parquet, no network.

## 0. Method, and one disclosure

- **Discovery half only for outcomes:** 2010-01-01 .. 2017-12-31. Activity tables (tick proxy, tick scale by year) use
  2010 .. 2025-09; the holdout is never read.
- **Disclosure (README 4.1).** My first run, before the director's frame reached me, measured outcomes pooled over
  2010 .. 2025-09 and printed an early/late split. I saw only the tail of that terminal output (five 1d rows: TSMOM
  120d, OBV cross, OBV divergence, CMF cross and CMF re-entry, pooled, all |t| < 3.3 except CMF cross). I discarded the
  file, rewrote the script to load 2010-2017 for outcomes, and report only the rerun. Anything touching **1d TSMOM, 1d
  OBV and 1d CMF** is therefore charged as a full trial with no confirmation credit; nothing else was read on 2018+.
- **Signals** are stamped at the closed bar, enter at the next bar's open and exit at the close H bars later (H = 8 bars
  on 15m and 1h, 6 on 4h, 10 on 1d), mid prices, long and short, greedy non-overlap (one event per H bars).
  Divergence pivots (k = 3) are stamped at the confirming bar (i + 3). The anchored-VWAP anchor is the rolling N-day
  low/high known at the time. Volume profile uses the completed prior 22:00-22:00 UTC day only.
- **Control (4.4, pattern events):** 500 draws of the same number of random bars, same UTC hour, same long/short mix.
  `p_ctrl` = share of draws with a mean at least the signal's. It removes hour-of-day and the drift of the mix, **not**
  the 2010-17 regime (gold peaked in 2011 and fell to 2015, so short-biased signals can inherit that bear market).
- **Statistic:** mean $/oz per event; **day-clustered t** (CR1). Costs: broker today $0.144/oz round trip
  (`cost-model-1m-2026-10-25.md`); research prior about $0.71/oz (2010-17 spread plus 0.37, as in
  `tight_stop_entry_study.py`). Median ATR in 2010-17 is $1.5 (15m), $3.3 (1h), $6.5 (4h), $16 (1d), so $0.71 is about
  0.09 ATR15 / 0.21 ATR1h / 0.11 ATR4h / 0.04 ATR1d.
- **Looks:** 173 signal rows (4 timeframes) plus 5 session-trend rows and 4 range-forecast fits. Bonferroni 5% at 173
  looks is |t| about 3.6; nothing reaches it. Raw: `~/tmp/ts_ostv/results.csv` (reproduce with the script).

## 1. Already tried (do not repeat)

| Topic | Result | Source |
|---|---|---|
| RSI/Bollinger/VWAP fades 15m | no gross edge, **R-01** (#3, #5, #10, #12) | `hypotheses.md` |
| MFI signals (extreme, double circle, 50-cross, pullback hold, divergence) and box/price-action flags | H-40 discovery FAIL, OOF AUC 0.503; retired as filters | `box-mfi-families-2026-10-18.md` |
| Owner box break + MFI 15m (H-50, #89) | gross +0.030 R, t 1.61, net −0.297 R, **R-15** | `hypotheses.md` |
| tsmom 1h/4h (#16, #17) | gross +0.061 R, net −0.079 R, **R-06/R-10** | `hypotheses.md` |
| Slow TSMOM 1d (#48, H-01) | inconclusive, 294 events, gross +0.166 R, t 2.04 | `hypotheses.md` |
| Volume profile, Supertrend/SAR, Stoch/Williams/CCI standalone | retired as rules (**R-09**), features only | `indicator-survey.md` s.3 |
| Oscillator IC method warning | within-day IC −0.135 on a random walk; pooled IC gold −0.039 | `README.md` 6.10 |
| Session-VWAP 2σ fade, EMA pullback arms | exploration rows on the learning hosts (H-60/H-61) | `arms-2026-10-25.md` |
| Not in the store | no COMEX GC volume, no futures data of any kind (`goldbot/data` has CFTC COT and GLD holdings only; Databento deferred as a paid owner decision, `knowledge-base/money-flow-and-volume.md` M10) | repo grep |

New here: the full indicator set at one fixed H on all four timeframes with a randomised control; MACD, Stochastic,
CCI, Williams %R, CMF, OBV, anchored VWAP, volume-profile levels, open volume spikes (none measured before); and the
volume-forecast-of-range test. R-06 and R-01 are not re-tested, only re-seen as members of the table.

## 2. Tick volume as a proxy for real volume

We have **no real volume**. The bar field is the Dukascopy `tick_count` (fractional volume units in the bar files, and
the 1m sum). Measured on 2010 .. 2025-09 (activity, no outcomes):

| TF | n bars | corr(log tick, log range) | same after removing the hour-of-week median | corr(log tick, log abs return) | corr(log tick, log spread) | share of log-tick variance explained by hour-of-week |
|---|---:|---:|---:|---:|---:|---:|
| 15m | 369,786 | 0.51 | 0.35 | 0.29 | −0.20 | 37% |
| 1h | 92,500 | 0.51 | 0.34 | 0.29 | −0.22 | 39% |
| 4h | 25,077 | 0.57 | 0.32 | 0.33 | −0.28 | 58% |
| 1d | 4,880 | 0.68 | 0.36 | 0.39 | −0.17 | 64% |

Median 1m tick-volume unit by year (non-stationary, not comparable across years): 2010 0.013, 2012 0.020, 2015 0.031,
2016 **0.049**, 2018 0.027, 2020 0.039, 2023 **0.012**, 2025 0.025. A 4x swing with no change in the market means any
absolute volume level (OBV slope, volume thresholds, volume-profile weights across years) is a feed artefact; only
ratios and hour-of-week z-scores are usable (`indicator-survey.md` trap table agrees).

**Reading.** Tick volume is mostly a clock and a volatility proxy: more than a third of its variance is the weekly
time-of-day shape, and after removing it a correlation of about 0.33-0.36 with the bar's range remains. That is the
well-known volume-volatility link, not evidence of traded size. It cannot be compared with COMEX volume because we hold
none; whether the proxy tracks real volume is therefore **unmeasured here**; the literature is mixed:

- Secondary report of about 0.9 correlation between FX tick volume and traded volume (Marney 2011, summarised at
  https://c.mql5.com/forextsd/forum/145/fxvolume_tick_volume_vs_real_volume_study_1.pdf; paper not retrieved, figure
  unverified).
- Reuters tick counts correlate quite strongly with monthly volumes but the relation is unstable and imprecise at high
  frequency: FMG discussion paper 265, https://www.fmg.ac.uk/sites/default/files/2020-10/dp265.pdf (authors not
  confirmed this session).
- Broker tick volume largely uncorrelated with CME futures volume, and differs by broker feed:
  https://www.earnforex.com/blog/do-you-believe-in-forex-tick-volume/ (informal).
- Mechanism: a tick is a quote update, so one large print counts once; the number of trades, not their size, drives
  the volume-volatility relation (Jones, Kaul, Lipson 1994, *RFS* 7(4); Ané & Geman 2000, *JF* 55(5); Karpoff 1987,
  *JFQA* 22(1), survey; none re-fetched, unverified).

Verdict: use tick volume as an **activity/volatility feature after hour-of-week normalisation**, never as traded
volume. Real COMEX GC volume is the only way to test volume as order flow; the programme decision on buying it
(Databento) stays the owner's and is not supported by anything below.

### 2.1 Does volume or ADX beat "range is what is predictable"?

Target = log(next-H-bar high-low range / ATR14), 1h (H = 8) and 4h (H = 6). Baseline = log(ATR14/ATR100) plus the
hour-of-week median of the target. Extra = tick-volume z (hour-of-week normalised), ADX14, |recent move|. Fit 2010-2014,
score 2015-2017 (both discovery).

| TF | OOS R² baseline | OOS R² + tick z, ADX, move | gain |
|---|---:|---:|---:|
| 1h | 0.3172 | 0.3174 | +0.0003 |
| 4h | 0.0862 | 0.0894 | +0.0032 |

The volatility term and the clock carry the forecast (R² 0.32 at 1h); tick volume and ADX add nothing at 1h and 0.3
points at 4h. **Close: volume/ADX are not a better opportunity-day selector than the HAR/ATR route (H-16).**

## 3. Oscillators (2010-2017; gross $/oz per event; t = day-clustered; `+yrs` = years positive of 8)

Direction convention is the textbook one named in the row. A negative gross means the opposite direction paid; I do not
claim the opposite (that would be a new look). Full table: results.csv. Net at broker cost = gross − 0.144; at the
research prior = gross − 0.71.

| Signal | TF | n | /month | gross | t | +yrs | vs control (p) |
|---|---|---:|---:|---:|---:|---:|---:|
| RSI14 re-entry from 30/70 (fade) | 15m / 1h / 4h | 3,974 / 1,211 / 387 | 41 / 13 / 4 | +0.03 / −0.29 / +0.18 | 0.4 / −1.2 / 0.3 | 3 / 4 / 4 | 0.37 / 0.90 / 0.38 |
| RSI14 cross of 70/30 (momentum) | 15m / 1h / 4h | 4,052 / 1,220 / 399 | 42 / 13 / 4 | +0.08 / +0.14 / **+1.43** | 1.0 / 0.6 / 1.9 | 5 / 6 / 4 | 0.13 / 0.27 / **0.02** |
| Stochastic(14,3,3) K/D cross in 20/80 | 15m / 1h / 4h | 7,757 / 2,021 / 680 | 81 / 21 / 7 | +0.04 / −0.36 / +0.17 | 0.9 / −2.0 / 0.3 | 5 / 2 / 3 | 0.17 / 0.99 / 0.34 |
| Williams %R14 re-entry −80/−20 | 15m / 1h / 4h | 14,556 / 3,532 / 1,103 | 152 / 37 / 12 | −0.03 / **−0.44** / −0.28 | −0.9 / **−3.0** / −0.7 | 3 / 2 / 2 | 0.82 / 1.00 / 0.75 |
| CCI20 re-entry ±100 | 15m / 1h / 4h | 12,473 / 3,101 / 895 | 130 / 32 / 9 | +0.03 / −0.32 / +0.41 | 0.9 / −2.1 / 1.0 | 3 / 2 / 6 | 0.21 / 0.99 / 0.19 |
| MFI12 (tick) re-entry 20/80 | 15m / 1h / 4h | 3,905 / 1,230 / 355 | 41 / 13 / 4 | −0.09 / **−0.66** / −0.11 | −1.5 / **−2.9** / −0.1 | 2 / 0 / 5 | 0.90 / 1.00 / 0.58 |
| MFI12 (tick) 50-cross follow | 15m / 1h / 4h | 11,643 / 2,874 / 799 | 121 / 30 / 8 | −0.05 / 0.00 / −0.37 | −1.2 / 0.0 / −0.7 | 1 / 4 / 2 | 0.89 / 0.45 / 0.79 |
| MACD(12,26,9) zero-line cross | 15m / 1h / 4h | 5,155 / 1,223 / 334 | 54 / 13 / 3 | **−0.15** / **+0.49** / −0.29 | **−2.5** / **2.3** / −0.4 | 2 / 6 / 3 | 1.00 / **0.01** / 0.63 |
| MACD signal-line cross | 15m / 1h / 4h | 10,084 / 2,438 / 754 | 105 / 25 / 8 | −0.04 / +0.07 / +0.45 | −0.9 / 0.4 / 0.9 | 2 / 5 / 5 | 0.80 / 0.34 / 0.16 |
| RSI14 regular divergence (pivot 3, confirmed) | 15m / 1h / 4h | 2,456 / 720 / 220 | 26 / 7.5 / 2.3 | **−0.23** / **+0.64** / −1.27 | **−2.6** / 2.1 / −1.5 | 1 / 6 / 3 | 0.99 / **0.01** / 0.92 |
| MFI12 (tick) regular divergence | 15m / 1h / 4h | 4,140 / 1,124 / 305 | 43 / 12 / 3 | −0.11 / +0.01 / −0.92 | −1.7 / 0.0 / −1.3 | 1 / 3 / 2 | 0.95 / 0.39 / 0.87 |

Reading.
1. **Oscillator re-entries have no gross edge at 15m, and at 1h the sign is negative** (Williams t −3.0, MFI −2.9,
   Stochastic −2.0, CCI −2.1): after a stretched 1h bar, price tends to continue for the next 8 hours, so the
   fade ("oversold = buy") is the wrong sign. This is consistent with the director's 6.10 result (R-01 stands) and with
   the 1h trend-follow rows in section 4, not a new edge: it is the same gold 1h continuation seen from the other side,
   and 6.7 found no variance-ratio momentum, so the effect is conditional on a stretch, small (0.1-0.2 ATR1h) and
   does not repeat at 15m or 4h.
2. **Signs flip across timeframes for MACD zero-line (15m −0.15 t −2.5; 1h +0.49 t 2.3) and RSI divergence (15m −0.23;
   1h +0.64; 4h −1.27).** A real mechanism would not alternate; these are the classic multiple-testing pattern.
   Divergence has no incremental value over the plain RSI on any timeframe that is also stable.
3. **MFI (the owner's indicator) does no better than the rest**, and its tick-volume weighting adds nothing over RSI: the
   MFI fade is the most negative row at 1h. Consistent with H-40 and H-50.
4. The only 4h oscillator outlier is RSI cross of 70/30 as momentum (+1.43 $ = 0.22 ATR4h, n = 399, 4 of 8 years
   positive, t 1.9): a 4h "momentum ignition" read, below the 1,000-event floor.

### 3.1 Session-conditioned oscillator signals (signal bar's UTC session)

Asia 00-07, London 07-12, NY 12-17, Late 17-21. Gross $/oz, day-clustered t in brackets.

| Signal | TF | Asia | London | NY | Late |
|---|---|---|---|---|---|
| RSI14 re-entry (fade) | 15m | −0.07 (−0.5) | −0.11 (−0.8) | +0.08 (0.5) | −0.13 (−0.8) |
| RSI14 re-entry (fade) | 1h | +0.17 (0.5) | −0.60 (−1.0) | +0.18 (0.4) | −0.32 (−0.9) |
| Stochastic cross in 20/80 | 15m | +0.05 (0.8) | +0.08 (0.8) | −0.15 (−1.0) | +0.13 (1.5) |
| Stochastic cross in 20/80 | 1h | +0.01 (0.0) | **−1.13 (−2.6)** | −0.36 (−0.9) | −0.46 (−1.9) |
| MFI12 re-entry | 15m | −0.12 (−1.4) | −0.07 (−0.4) | −0.33 (−1.8) | −0.08 (−0.6) |
| MFI12 re-entry | 1h | −0.53 (−1.7) | **−1.32 (−2.6)** | −0.89 (−1.8) | −0.76 (−1.6) |

"Oversold at the London open vs Asia": the question has the same answer as the pooled one. No session shows a positive
fade; London is the **most negative** at 1h (the London 1h stretch continues), which is the opposite of an oversold
bounce. Cells have n of 150-750 at 1h and 560-2,600 at 15m; none clears t 3 in the fade direction, and the only |t| > 2
cells are the continuation sign. Close as a fade; the continuation direction is a feature candidate (section 7).

## 4. Trend (2010-2017)

| Signal | TF | n | /month | gross $ | t | +yrs | p_ctrl | net @0.144 | net @0.71 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| MA regime state (close > EMA50 > SMA200, sampled every H) | 15m | 15,640 | 163 | +0.06 | 1.8 | 6 | 0.03 | −0.08 | −0.65 |
| | 1h | 3,955 | 41 | **+0.30** | 2.3 | 7 | 0.01 | +0.16 | −0.41 |
| | 4h | 1,358 | 14 | +0.20 | 0.5 | 5 | 0.31 | +0.06 | −0.51 |
| | 1d | 149 | 1.6 | +0.34 | 0.1 | 4 | 0.48 | +0.20 | −0.37 |
| Supertrend(10,3) flip | 15m / 1h / 4h / 1d | 4,111 / 1,061 / 304 / 63 | 43 / 11 / 3 / 0.7 | −0.10 / +0.43 / −0.63 / +0.95 | −1.3 / 1.6 / −0.8 / 0.2 | 3 / 5 / 3 / 5 | 0.93 / 0.04 / 0.78 / 0.41 | | |
| EMA20/50 cross | 15m / 1h / 4h / 1d | 3,008 / 750 / 197 / 44 | 31 / 7.8 / 2 / 0.5 | −0.13 / +0.18 / −1.65 / −4.1 | −1.6 / 0.7 / −1.8 / −0.8 | 2 / 4 / 3 / 2 | | | |
| Close vs SMA200 cross | 15m / 1h / 4h / 1d | 2,794 / 644 / 212 / 31 | 29 / 6.7 / 2 / 0.3 | −0.10 / −0.45 / +0.39 / +5.1 | −1.1 / −1.3 / 0.5 / 0.9 | 3 / 0 / 4 / 3 | | | |
| DI cross with ADX > 20 | 15m / 1h / 4h / 1d | 3,701 / 995 / 367 / 54 | 39 / 10 / 4 / 0.6 | **−0.21** / +0.36 / +0.24 / +4.6 | **−2.8** / 1.3 / 0.4 / 0.9 | 1 / 6 / 5 / 6 | | | |
| DMI direction when ADX > 25 (every H) | 15m / 1h / 4h / 1d | 10,293 / 3,020 / 1,174 / 105 | 107 / 31 / 12 / 1.1 | 0.00 / +0.04 / −0.27 / −5.4 | −0.1 / 0.2 / −0.7 / −1.4 | 4 / 4 / 4 / 1 | | | |
| DMI direction when ADX < 18 (chop) | 15m / 1h / 4h | 7,928 / 1,538 / 400 | 83 / 16 / 4 | +0.02 / +0.08 / −0.79 | 0.4 / 0.4 / −1.2 | | | | |
| TSMOM sign of 96/120-bar vol-scaled return | 15m / 1h / 4h / 1d | 14,631 / 3,842 / 1,341 / 156 | 153 / 40 / 14 / 1.6 | +0.06 / +0.07 / +0.34 / +0.12 | 1.7 / 0.5 / 0.9 / 0.0 | 5 / 5 / 5 / 4 | 0.04 / 0.27 / 0.20 / 0.54 | | |

Reading.
1. **Nothing in the trend set is net-positive at the research-prior cost, and nothing clears t 3.** The best are the 1h
   states (MA regime +0.30, Supertrend flip +0.43, MACD zero +0.49) at about 0.10-0.15 ATR1h gross against 0.21 ATR1h
   cost. They are the same 1h continuation as R-06 (gross +0.061 R, t 2.63 pooled 2010-2025) re-expressed; they are
   the same trade, so they cannot be counted as independent confirmation.
2. **ADX does not select trends on gold 1h**: ADX14 > 25 holds for 49-54% of 1h bars in every session (Wilder's 25 was
   set on daily commodity charts), and DMI-follow when ADX > 25 returns +0.04 $ at 1h (t 0.2) and −0.27 at 4h, no better
   than the chop bucket (ADX < 18: +0.08 at 1h). The DMI trend table the owner's indicator draws is a direction
   state, not a strength gate; the coverage note already found that an ADX ≥ 20 gate does not reproduce it
   (`indicator-coverage-2026-10-18.md`).
3. **Fresh-cross rules (EMA20/50, SMA200, DI cross, Supertrend) are worse than the persistent states** at 15m and 4h and
   are noisy at 1d (n 31-63).
4. **Daily rules are unpowered:** every 1d row has n of 31-160 over eight years, far below the 150-event floor for a
   read, and the 1d means (±$1-5) are inside their noise.

### 4.1 Trend strength by session (1h, 2010-2017)

| Session (UTC) | mean ADX14 | share of bars ADX > 25 | mean abs 8-bar move $ | DMI-follow 8-bar return when ADX > 25, $ | n |
|---|---:|---:|---:|---:|---:|
| Asia 00-07 | 27.5 | 51% | 5.1 | **+0.14** | 7,378 |
| London 07-12 | 27.1 | 50% | 7.3 | **+0.19** | 5,116 |
| NY 12-17 | 27.5 | 52% | 5.5 | −0.11 | 5,336 |
| Late 17-21 | 27.9 | 54% | 3.9 | −0.11 | 4,428 |
| Roll 21-24 | 27.9 | 53% | 4.1 | +0.08 | 2,127 |

ADX itself is flat by session; the session pattern is in activity (London 8-bar moves are about 1.9x Late's), which is
the range story the director already has. The only session-conditioned trend rows with a control p < 0.01 are
**Asia** (the signal bar in 00-07): TSMOM 96 bars on 15m **+0.156 $ (t 3.2, 7/8 years, p_ctrl 0.000)**, TSMOM 120 bars on
1h **+0.64 $ (t 2.7, 7/8, 0.002)** and DMI > 25 on 1h **+0.55 $ (t 2.0, 6/8, 0.002)**. These are one effect seen three
ways (trend persistence when entered in the Asian session and held 2 h or 8 h), and a London, NY or Late signal
shows no such persistence (NY 0.03 / −0.06, Late −0.01 / −0.38). Treat as a lead, not a result: it is one of about
12 session slices, it does not clear the 3.6 bar, the entry-hour control matches the hour but not the regime, and the
hold crosses into London so it may be the London-open drift in disguise (6.1 shows London AM at −2.5 bp the other way).

## 5. Volume, VWAP and profile (2010-2017)

| Signal | TF | n | /month | gross $ | t | +yrs | p_ctrl |
|---|---|---:|---:|---:|---:|---:|---:|
| OBV(tick) crosses EMA20 | 15m / 1h / 4h / 1d | 11,460 / 2,696 / 877 / 119 | 120 / 28 / 9 / 1.2 | −0.06 / −0.05 / −0.41 / −0.56 | −1.5 / −0.3 / −0.9 / −0.1 | 3 / 4 / 3 / 3 | 0.96 / 0.58 / 0.78 / 0.50 |
| OBV(tick) divergence at 40-bar price extreme (fade) | 15m / 1h / 4h / 1d | 6,208 / 1,553 / 486 / 69 | 65 / 16 / 5 / 0.7 | −0.06 / −0.19 / −0.92 / −11.9 | −1.1 / −0.8 / −1.3 / −2.3 | 2 / 3 / 2 / 0 | 0.85 / 0.82 / 0.92 / 0.99 |
| CMF20(tick) cross +0.1/−0.1, follow | 15m / 1h / 4h / 1d | 10,765 / 2,455 / 753 / 114 | 112 / 26 / 8 / 1.2 | −0.05 / **+0.59** / −0.34 / −7.1 | −1.1 / **3.2** / −0.7 / −1.6 | 3 / 6 / 3 / 2 | 0.87 / **0.000** / 0.76 / 0.98 |
| CMF20(tick) re-entry (fade) | 15m / 1h / 4h / 1d | 10,744 / 2,434 / 745 / 113 | 112 / 25 / 8 / 1.2 | +0.02 / **−0.37** / −0.11 / +6.9 | 0.4 / −2.0 / −0.2 / 1.5 | 5 / 3 / 3 / 7 | 0.36 / 0.97 / 0.57 / 0.03 |
| Day VWAP (22:00 UTC) stretch >1 ATR + turn candle, fade | 15m / 1h | 12,633 / 1,629 | 132 / 17 | −0.05 / +0.03 | −1.1 / 0.1 | 2 / 4 | 0.87 / 0.42 |
| London VWAP (07:00) stretch, fade | 15m / 1h | 13,451 / 1,860 | 140 / 19 | **−0.09** / −0.29 | **−2.7** / −1.7 | 0 / 1 | 1.00 / 0.97 |
| NY VWAP (12:00) stretch, fade | 15m / 1h | 13,504 / 1,713 | 141 / 18 | −0.07 / **−0.53** | −2.3 / **−2.5** | 1 / 2 | 0.99 / 0.99 |
| London VWAP stretch with candle, follow | 15m / 1h | 16,797 / 2,701 | 175 / 28 | −0.01 / +0.30 | −0.3 / 2.1 | 5 / 5 | 0.61 / **0.01** |
| NY VWAP stretch with candle, follow | 15m / 1h | 16,701 / 2,702 | 174 / 28 | +0.02 / +0.24 | 0.7 / 1.4 | 6 / 6 | 0.24 / 0.10 |
| Prior-day POC: turn toward POC from >1 ATR away | 15m | 18,746 | 195 | −0.06 | −2.1 | 2 | 0.98 |
| Prior-day value area: London-open outside, first close back inside, trade to POC | 15m | 1,188 | 12 | −0.10 | −0.7 | 4 | 0.77 |
| Prior-day VAH/VAL break by a 15m close, follow | 15m | 4,352 | 45 | +0.08 | 1.0 | 5 | 0.11 |

Anchored VWAP (touch of the VWAP anchored at the rolling N-day extreme, close back on the right side). Short signals use
the N-day **high** anchor, long signals the N-day **low** anchor:

| Anchor | 1h gross (t, n) | 4h gross (t, n, +yrs, p_ctrl) |
|---|---|---|
| 30d high, short | −0.16 (−0.5, 585) | **+1.60** (2.1, 248, 6, 0.02) |
| 60d high, short | −0.19 (−0.5, 429) | +1.28 (1.5, 210, 6, 0.06) |
| 90d high, short | −0.19 (−0.4, 360) | **+2.04** (2.1, 178, 5, 0.01) |
| 30d low, long | −0.23 (−0.5, 548) | +0.63 (0.7, 239, 5, 0.26) |
| 60d low, long | −0.22 (−0.5, 467) | −0.49 (−0.5, 198, 3, 0.74) |
| 90d low, long | −0.22 (−0.5, 398) | −0.78 (−0.7, 189, 2, 0.77) |

Open volume spikes (tick z > 1.5 against the previous eight same-hour-of-week observations), follow vs fade the bar:

| Open (UTC) | TF | n | follow gross $ (t) | fade gross $ (t) | fade p_ctrl |
|---|---|---:|---|---|---|
| London 07:00 | 15m | 265 | −0.32 (−0.8) | +0.32 (0.8) | 0.10 |
| London 07:00 | 1h | 267 | −1.14 (−1.6) | **+1.14 (1.6)** | 0.02 |
| NY 12:00 | 15m / 1h | 250 / 247 | −0.38 / +0.05 | +0.38 / −0.05 | 0.18 / 0.51 |
| NY cash 13:30 | 15m | 199 | **+0.76 (1.5)** | −0.76 | (follow p 0.05) |
| Asia 00:00 | 15m / 1h | 221 / 276 | +0.07 / +0.29 | −0.07 / −0.29 | 0.54 / 0.77 |

Reading.
1. **VWAP fades are dead, as R-01 and the S1 arms predicted.** Every session-anchored VWAP stretch fade has gross ≤ 0,
   London-anchored 15m −0.09 (t −2.7, 0 of 8 years positive). The follow side at 1h (London +0.30 t 2.1, NY +0.24)
   is again the 1h continuation.
2. **Volume profile levels (POC, value area) show no edge.** The "return to POC from 1 ATR" fade is negative (t −2.1), the
   80% value-area rule is flat (n 1,188), the VAH/VAL break follow is +0.08 (t 1.0). R-09 holds. The caveat is
   tick-weighted 1m typical prices, not trades at price.
3. **OBV and CMF:** OBV is nothing on any timeframe (the divergence fade is negative everywhere). **CMF20(tick) follow
   at 1h is the single strongest row of the slice** (+0.59 $ = 0.18 ATR1h, t 3.2, p_ctrl 0.000, 6 of 8 years) but it
   is not backed by 15m (−0.05) or 4h (−0.34), CMF is a close-location-in-range average (so mostly a momentum
   proxy that the MA-regime and MACD rows already carry), and at research-prior cost it is −0.12 net. It sits at
   t 3.2 against a bar of 3.6 for 173 looks.
4. **Anchored VWAP from the 30/60/90-day high, short, on 4h (t 2.1/1.5/2.1; 178-248 events, 5-6 of 8 years)** has the
   largest per-trade size in the set (1.3-2.0 $ = 0.2-0.3 ATR4h, net +0.6 to +1.3 at the research-prior cost) but the
   long mirror (anchor at the low) is flat or negative, and 2010-2017 includes the 2011-2015 bear market: this is
   most plausibly short-side drift that the hour/direction-matched control does not remove. It is the one result that
   the confirmation half must refute or support (gold rose in 2019-2025); n is far below 1,000.
5. **Open spikes:** at the London open a z > 1.5 first hour is **faded** (+1.14 $, t 1.6, p_ctrl 0.02, 6 of 8 years, n =
   267 = 2.8 a month) while at the NY cash open 13:30 the first 15m is **followed** (+0.76, t 1.5): opposite
   conclusions for the same statistic at two clock points, with n < 270 each. Noise until shown otherwise.

## 6. Verdicts per topic (README 7.2)

| Topic | Verdict |
|---|---|
| RSI, Stochastic, Williams %R, CCI fades; session-conditioned oversold | **close** (0 trials): no gross edge at 15m, wrong sign at 1h |
| MFI(12) tick signals, MFI divergence | **close as rules** (R-01, H-40, H-50 agree); keep as features |
| MACD, RSI divergence | **close** as rules (sign flips 15m/1h/4h); feature |
| ADX/DMI as a strength gate | **close**: ADX > 25 holds half the time, no selection value on 1h/4h |
| MA regime, Supertrend, DMI direction, TSMOM 15m-1d | **feature** (1h continuation = R-06, not independent) |
| Asia-session trend persistence (TSMOM / DMI) | **arm** (section 8, D-1) |
| Tick volume as real volume | **unmeasured** (no COMEX data); proxy for activity only |
| Tick volume / ADX as a range forecaster | **close**: +0.0003 R² (1h), +0.003 (4h) beyond ATR and the clock |
| OBV | **close** |
| CMF20 | **feature**; arm D-2 |
| Session VWAP fades; POC/value-area; AVWAP long side | **close** |
| AVWAP from 30/60/90d high (4h) | **feature + retire-only diagnostic** (D-3) |
| Open volume spikes | **feature** (hour-of-week tick z); drop as a rule |
| Keltner / Donchian / bands | not measured here; handed to slice F |

## 7. Good ML feature candidates (under the 40-cap; nothing here is a rule)

At most 16 columns, each causal and ratio/z form, for H-02's 4h map, O-4's 15m map and the H-62 meta-labeller; they
count as one group (`osc_trend_vol`, K_eff +1, fold-internal selection only):

1. `rsi14`, `stoch_k14`, `cci20` (one per timeframe, kept after |ρ| clustering; likely one survives).
2. `macd_hist_atr` (MACD histogram ÷ ATR) and `macd_zero_dist_atr`.
3. `adx14`, `di_diff` (+DI − −DI), `adx_gt25_share_48` (persistence of the state).
4. `supertrend_dir`, `bars_since_supertrend_flip`.
5. `ma_regime` (−1/0/+1: close/EMA50/SMA200 stack) on 1h and 4h.
6. `cmf20_tick` and `tick_z_how` (log tick minus its hour-of-week median, 8-week window).
7. `dist_vwap_london_atr`, `dist_vwap_day_atr`, `dist_avwap30_high_atr`, `dist_avwap90_high_atr`.
8. `dist_prior_poc_atr`, `in_prior_value_area` (completed prior day only).
9. `rsi_div_flag` (confirmed pivot divergence, stamped at the confirming bar).

Do **not** include: raw `tick_count`, OBV level or slope, volume thresholds (feed units vary 4x across years),
Keltner/Donchian (slice F). `mfi12` and `mfi_signals` already exist as the D-MFI group.

## 8. Candidate hypotheses (ranked hard)

**Slots.** 2026Q4 has 0 left; the 2027Q1 free reserve is 6. On this evidence **none of these is worth a trial slot**:
none has a gross edge several times the research-prior cost on at least 1,000 events, and none clears the
173-look bar. Two are worth an exploration arm (cost several times the broker cost), one is worth a free
retire-only diagnostic once the confirmation half is unlocked, the rest are features or drops. If the director must
fund one, D-1.

### D-1 Asia-session trend persistence (arm; the only slot candidate)
- Mechanism: Asian-hours flow (physical buying, ETF/CNY) is slow and one-directional, so a prior-day trend sign persists
  while the West sleeps; HFT front-running is weaker out of hours (SoA A1). Evidence grade: C (no gold paper; H-04
  already found a small Asian drift).
- Already tried?: related to H-04 (Asia long drift, #44: gross +0.043 R, net −0.093 R, stop) and R-06; new: the
  direction is the 96-120-bar trend sign, not a constant long.
- Timeframe/clock: signal on a closed 15m or 1h bar in 00:00-07:00 UTC (Tokyo/Shanghai daytime; use local clocks per 4.2);
  hold 8 bars.
- Entry: sign of the 120-bar (1h) / 96-bar (15m) vol-scaled return with |z| > 0.5, next open, one signal per 8 bars.
  Side: both.
- Exit: time exit at 8 bars (1h) or 2 h (15m); stop 1.5 ATR; flat before the 21:00-23:00 UTC rollover.
- One position at a time: yes.
- Control: random bars in 00-07 UTC, same direction mix, plus the same rule signalled in London/NY (done: no persistence).
- Discovery-half evidence: 15m +0.156 $ (t 3.2, n 4,491, 7/8 years, p_ctrl 0.000); 1h +0.641 $ (t 2.7, n 1,164, 7/8, 0.002);
  looks: 173 + 12 session slices.
- Cost check: 1h gross 0.64 $ = 0.21 ATR1h; **vs broker $0.144 = 4.4x, vs research prior $0.71 = 0.9x (net −0.07)**;
  15m 0.156 $ = 0.12 ATR15 vs broker 1.1x (net +0.01) and research prior 0.2x. Fails the trial test, passes only the
  arm test at 1h.
- Expected events: 1h about 12 a month (about 2,300 over 2010-2025-09); 15m about 47 a month (about 9,000).
- Kill rule: the confirmation-half read of the same rule gross ≤ 0 or day-clustered t < 2, or the effect disappears when
  the entry is delayed one London-open hour.
- Read rule (arm): continue if 60 fills show mean gross ≥ 2x the measured broker round trip; stop if ≤ 0.
- Route: exploration arm on `icm-probe`; feature `asia_trend_z`. Prior P(continue) 10%.

### D-2 CMF20 (tick) trend-follow on 1h (arm; do not spend a slot)
- Mechanism: close-location-weighted activity (accumulation) leads continuation; theory only (Blume, Easley, O'Hara 1994,
  unverified). Grade: D.
- Already tried?: new indicator; the information overlaps the MA-regime / MACD 1h continuation (R-06).
- Entry: CMF20 crosses above +0.1 (long) or below −0.1 (short) on a closed 1h bar; next open. Exit: 8 bars or 1.5 ATR stop.
- Control: random hour-matched bars (done p 0.000). Evidence: +0.586 $, t 3.2, n 2,455, 6/8 years; 15m −0.05, 4h −0.34.
- Cost check: 0.18 ATR1h gross vs broker 4x, research prior 0.8x (net −0.12). Expected 26 events a month.
- Kill rule: gross ≤ 0 on the confirmation half, or corr with the MA-regime signal > 0.7 (then redundant).
- Route: exploration arm; feature `cmf20_tick`. Prior P(continue) 7%.

### D-3 Anchored VWAP from the N-day high, short, 4h (retire-only diagnostic)
- Mechanism: holders since the N-day high sit underwater; their breakeven (the AVWAP) is supply when retested (practitioner;
  no peer-reviewed support found). Grade: D.
- Already tried?: new (R-09 retired volume profile; AVWAP not tested).
- Entry: 4h bar high ≥ AVWAP(30/60/90d high) and close below it; next open. Exit 6 bars. Side: short only.
- Control: hour-matched random shorts; add a **regime control** (shorts on random bars in the same month) because 2011-2015
  was a bear market.
- Evidence: 30d +1.60 $ (t 2.1, n 248), 90d +2.04 $ (t 2.1, n 178); long mirror ≤ 0.6 and flat.
- Cost check: 0.2-0.3 ATR4h vs research-prior 0.11 ATR4h = 2-3x; broker 5-10x. Events 2-3 a month (about 400-550 for 15.75
  years): below the 1,000 floor; best read is inconclusive.
- Kill rule: if the same rule on 2018-2025-09 (retire-only, pre-registered) has gross ≤ 0 or t < 1.5, drop; if the regime
  control explains it, drop. Route: 0-trial diagnostic, then feature. Prior P(continue) 5%.

### D-4 London-open tick spike fade, 1h (drop as a rule; feature only)
- Evidence: +1.14 $ (t 1.6, n 267, 6/8 years, p_ctrl 0.02) but the NY cash-open spike goes the other way and 15m is weak.
  n < 300. Route: feature (`tick_z_how` at the London-open bar). Prior < 5%.

### D-5 .. D-8 dropped (recorded so nobody re-derives them)
- D-5 RSI/MFI/Stochastic oversold-bounce by session: wrong sign at 1h, zero at 15m.
- D-6 MACD zero-line and RSI divergence: sign flips across 15m/1h/4h.
- D-7 Prior-day POC/value-area trades: negative or flat at 15m, n 1,188 for the 80% rule.
- D-8 Daily oscillator / trend rules: n 30-160 over 8 years; an unpowered read.

## 9. Looks taken, and what I would stop doing

- Looks: 173 (signal × TF) + 12 session slices + 5 session-trend rows + 4 range-forecast fits + 1 proxy table; two
  runs of the same code (the first on the wrong window, discarded, see 0). K for these topics should be recorded as
  the 173-row table, not one.
- Stop: reading indicator extremes as reversal signals on gold at 15m-1h; using ADX > 25 as a trend gate on intraday
  gold; treating tick volume level as volume.

## 10. References

Verified in the repo (earlier sessions): Park & Irwin 2007, *J. Economic Surveys* 21(4), https://ideas.repec.org/a/bla/jecsur/v21y2007i4p786-826.html;
Osler 2000/2003/2005 (levels and stops); Bajgrowicz & Scaillet 2012, *JFE* 106(3); Campbell, Grossman, Wang 1993, *QJE*
108(4), https://ideas.repec.org/p/hrv/faseco/3128710.html; Llorente et al. 2002, *RFS* 15(4),
https://www.johnhcochrane.com/s/llorente_michaely_saar_wang_volume_rfs.pdf; Batten, Lucey, McGroarty, Peat, Urquhart
2017, *PLoS ONE* 12(4) e0174232, https://pmc.ncbi.nlm.nih.gov/articles/PMC5407636/ (gold intraday volume shape);
Marek & Marková 2020 (MFI on S&P 500, tuned in-sample), https://otik.uk.zcu.cz/handle/11025/37043; Urquhart et al. (gold
and silver HFT technical analysis, treatment of cost unverified).
Seen this session by search only: Marney 2011 secondary summary; FMG DP265 (https://www.fmg.ac.uk/sites/default/files/2020-10/dp265.pdf);
EarnForex tick-volume note (https://www.earnforex.com/blog/do-you-believe-in-forex-tick-volume/).
Cited from memory, not re-fetched *(unverified)*: Sullivan, Timmermann, White 1999, *JF* 54(5); Moskowitz, Ooi, Pedersen 2012,
*JFE* 104(2); Hurst, Ooi, Pedersen 2017, *JPM*; Neely, Weller, Ulrich 2009, *JFQA* 44(2); Karpoff 1987; Jones, Kaul,
Lipson 1994; Ané & Geman 2000; Blume, Easley, O'Hara 1994, *JF* 49(1); Wilder 1978, *New Concepts in Technical Trading
Systems* (RSI, ADX/DMI, ATR); Appel (MACD); Lane (Stochastic); Lambert 1980 (CCI); Quong & Soudack 1989 (MFI).
No peer-reviewed test found for anchored VWAP, volume profile / value area, Supertrend, or divergence rules.
