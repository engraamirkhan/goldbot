# D-12: the sr-v3 independence inputs, measured on learn 2010-17 (rho_x, rho_u,day, rho_u,inst, tau_a)

Owner of the lane: ml-researcher (principal), for the research director. Date 2026-11-25. Base: origin/main 88f34459.
Rulings measured: ADR 0085 amendment 2026-11-25 (quant Q1, R13), and the quant's re-specification of D-12 on PR #460
rev 3 (rulings Q1 and Q2, sr-v3 card rev 4, section 2.3; data lanes D-12a and D-12).
Script: `scripts/research/measure_rho.py`. Machine-readable output: `docs/research/rho-measurement-2026-11.json`
(summary, schema `rho_day_measurement/v2`, with the script sha256, every input file's sha256 and the tier file's
sha256) and `docs/research/rho-measurement-2026-11-cells.csv.gz` (every cell; its sha256 is in the summary).
K: 0. This is a measurement on a control family and on label-free features. No strategy, arm or card outcome is
judged, and no sr-v3 or H-102 candidate or label is read.

## 1. Results in one table (binding values; full tables in section 4)

| Input (ruling) | Class | <= 60 min | 1-4 h | > 4 h |
|---|---|---|---|---|
| rho_u,day upper 95 % (Q1, rev 4) | gold | 0.006 -> **0.05** (floor) | 0.040 | 0.110 |
| | FX10 | 0.010 -> **0.05** | 0.009 | 0.047 |
| | metals (gold + XAG) | 0.006 -> **0.05** | 0.041 | 0.120 |
| | pooled (gold + XAG + FX10) | 0.009 -> **0.05** | 0.010 | 0.047 |
| rho_u,inst upper 95 %, day-mean level (R13) | FX10 within class | 0.44 | 0.30 | 0.34 |
| | pooled body | 0.40 | 0.14 | 0.17 |
| rho_u,inst vs gold, day-mean level (D-13 test <= 0.2) | XAG vs gold | 0.89 | 1.00 | 0.89 |
| | FX10 vs gold | 0.25 | 0.28 | 0.26 |
| tau_a upper 95 %, floored 0.01 R (Q2) | pooled body | 0.010 (lambda_a 13,500) | 0.020 (3,416) | 0.032 (1,324) |
| | gold + FX10 | 0.010 (13,500) | 0.016 (5,280) | 0.021 (2,963) |
| rho_x (D-12a) | pooled, fold 2010-11 | see 4.5: 1m columns 0.01-0.18 within-day; 4h 0.7-0.98 | | |

The per-candidate ("pair") cross-instrument ICCs are an order of magnitude smaller than the day-mean ones (FX10
<= 60 min: 0.013; vs gold 0.012). Which level enters 1 + (k - 1) rho_inst is a quant call; section 3.2 gives my
recommendation and the reason.

## 2. Method (fixed before the run)

**Candidates for rho_u and tau_a: a control family, not any judged family.**
- A fixed grid: every hold_bars-th tf-bar close of each New York 17:00 session day, counted from the day's first bar.
  So a day's candidates never overlap: Q3 prices overlap separately, through uniqueness.
- The same decision times on every instrument. This is the worst case for the cross-instrument ICC.
- No rule, no filter, no sr-v3 / H-102 arm or output chooses a candidate. Retired families' replays were not used:
  the ten retired arms were sr-v3 rev 1's own signals, and the H-102 arms are judged.
- Geometry, the sr-v3 template form (SYNTH-tr R9): stop = k x ATR14_tf x sqrt(hold_bars / h_ref), h_ref = 1 bar, ATR14
  on tf bars closed at the decision.
  - Heads: T2 (stop 1, target 2, time exit at H) and T6 (stop 2, time exit at H).
  - Entry at the open of the first 1m bar after the decision. Barriers are read on 1m bars; when one minute touches
    both, the stop counts first.
  - The 1.5 x session-median-TR stop floor is not applied (stated deviation).
- Geometries (tf x hold bars):
  - <= 60 min: 1m x 15, 1m x 30, 5m x 12, 15m x 4;
  - 1-4 h: 15m x 8, 1h x 2, 1h x 4;
  - > 4 h: 4h x 2, 4h x 3.
  - Each geometry is run with both heads.
- Outcomes in R:
  - gross;
  - net of a constant bps cost: a round trip plus an overshoot on stop exits. Gold uses the W2 cost of record (0.575
    and 1.75 bp). XAG (2.5 / 3.0 bp) and FX (0.8 / 0.5 bp) are stated assumptions.
  - Net matters because a bps cost in R grows on quiet days, so it is day-clustered itself.
- Sides: long, short and random (seeded per candidate).

**Estimators.** All are pairwise moments on per (instrument, day) sufficient statistics. Each instrument is centred
on its own mean, which is "after the unit intercept and instrument level" (the rev-4 residual).
- **rho_u,day:** the correlation of two same-instrument candidates on one day.
- **rho_u,inst, pair:** the correlation of two candidates of different instruments on the same day.
- **rho_u,inst, day-mean:** the correlation of two instruments' day-mean residuals on the same day.
- **vs gold:** the same quantities, with pairs restricted to (gold, member).
- **tau_a:** the SD across instruments of the mean gross T2 R. Both sides are averaged (the level is shared by both
  sides); long-only is also read.
- **rho_x:** the same within-day and cross-instrument statistics, on each core-12 column (less `side`).
  - It is computed on every closed tf bar, z-scored per instrument (dense rows, as sr-v3's rows).
  - It reads no label or outcome.
  - Folds: the smallest training fold 2010-11 (the one the rev-4 ladder is computed on), and the whole learn window.

**Uncertainty.** A week-block bootstrap: ISO weeks of the session day, all instruments of a week resampled together,
B = 2,000, fixed seeds. "Upper 95 % CI" = the 97.5th percentile of the two-sided interval.

**Binding value of a cell.** The largest upper bound over every geometry, head, side, cost and orientation variant in
that cell (24-96 variants a cell). It is a max of upper bounds, so it is conservative by construction.
- Directional series are also read PC1-oriented: each instrument's side is signed by its loading on the first
  principal component of learn daily returns. That is the orientation that maximises co-movement, for example short
  USD on every pair.

**Data, learn only.**
- Dukascopy XAUUSD 1m via `windows.read_window_bars(raw, "learn")`, with no warm-up.
- HistData 1m 2010..2017 files only, rows of r11-v2.1 tier A / A-consistent months only.
  - HistData is bid-only and has no volume, so its VWAP is equal-weight.
  - XAG has 22 A months in 2010-17; FX10 has 65-92 each.
- A candidate whose horizon ends at or after 2018-01-01 is dropped.
- No 2009 (test_old), 2018+ (confirm) or holdout file is opened. The loader raises if a non-learn row leaves it.
- Index, energy, crosses and XAUEUR have no A / A-consistent learn month, so they are **not measurable**. Their
  priors stand.

## 3. Reading

### 3.1 rho_day at <= 60 min: the Q1 value is the 0.05 floor for every class
- The residual same-day ICC has a point estimate of -0.002..+0.008 and an upper 95 % bound of at most 0.010, over 96
  variants and 2,066-2,076 days.
- So max(0.05, upper CI) = **0.05**. This agrees with the earlier null-bracket figure of 0.000-0.016.
- Longer holds are more clustered: gold reaches 0.040 at 1-4 h and 0.110 at > 4 h. Those bands keep 0.25 until Q3 lands
  in full (ADR 0085), and the measurement says 0.25 is conservative there too.

### 3.2 rho_inst: the level matters more than the number. Quant call needed
- **Pair level.** Per candidate, cross-instrument correlation is tiny: FX10 0.013, pooled 0.008 at <= 60 min.
- **Day-mean level.** Per instrument-day, it is large: FX10 0.44, pooled 0.40, XAG vs gold 0.89.
- They are the same fact. For aligned days, corr(day means) = m x r_pair / (1 + (m - 1) rho_day). With m = 89
  candidates a day, 0.013 becomes about 0.45.
- The pooled design effect 1 + (k - 1) rho_inst treats each instrument's estimate as one unit. The correlation of
  two instruments' estimates is the day-mean figure, not the pair figure.
- **My recommendation:** the day-mean value enters 1 + (k - 1) rho_inst, and in rev 4 also
  rho_x,j,inst x rho_u,inst. Using the pair value would overstate pooled n_eff about 4-5 times on FX10.
- **Consequence:** FX10 pooled at <= 60 min gives 1 + 9 x 0.44 = 5.0, so ten pairs carry about two instruments' worth
  of evidence. The rev-3 ladder rows that assumed rho_inst <= 0.1 for 1m L1 / 5m L3 are **not** supported.
- **D-13 (new instruments only where rho_u,inst vs gold <= 0.2).**
  - XAG fails at every horizon (0.89-1.00).
  - FX10 fails narrowly at the day-mean level (0.25-0.28) and passes at the pair level (0.012-0.056).
  - So the D-13 verdict depends on the same call.
- The vs-gold orientation is the class PC1, not a per-pair worst case. A pair-specific sign could read somewhat higher
  (stated limitation).

### 3.3 tau_a: instrument levels barely differ, so lambda_a is large
- The between-instrument SD of mean gross T2 R has an upper bound of 0.006-0.032 R.
- At <= 60 min it hits the 0.01 floor for FX10, the pooled body and gold + FX10, which gives lambda_a = 13,500.
- At > 4 h the pooled body gets lambda_a = 1,324. That is still above the rev-3 placeholder of 540.
- The instrument levels will be shrunk hard towards the unit intercept. This matches the card's warning that
  "instrument levels cost more df than the pooling adds n_eff".

### 3.4 rho_x (D-12a, label-blind): fast columns are nearly independent within a day; slow ones and the clock are not
Fold 2010-11, pooled body:
- **Within-day.**
  - At 1m: 0.01-0.07 for every column except `tsmom_score` (0.18).
  - At 15m: 0.10-0.33 for the fast columns, and 0.66-0.71 for `tsmom_score` / `ret_96`.
  - At 4h: 0.71-0.98.
  - So rho_x lifts n_j a lot only at 1m-15m.
- **Cross-instrument, day-mean.**
  - `min_since_london_open` is 0.84-0.99: the same clock on every instrument. Pooling gives that column nothing.
  - `atr_ratio_14_100` is 0.5-0.77 (a shared volatility regime). `compression_8_96` is 0.35-0.45.
  - The directional columns are 0.15-0.30.
- **Vs gold.** The directional columns read at most 0.21 on 2010-11 and at most 0.09 on the full learn window.
- **Gold-only within-day values** are within 0.05 of the pooled ones, except 1m `adx14` (gold 0.12 against pooled
  0.05 on 2010-11).

### 3.5 What I would not claim
- The candidates are a control grid. A rule or filter that keeps candidates on particular days could have a higher
  within-day ICC among the KEPT rows than the dense population measured here.
  - That is why every read stays on the day-block bootstrap (Q1, last bullet). No p-value comes from this n_eff.
- Features are built causally on the whole learn series. The engine's 800-bar window can move EWM-type columns
  slightly; an ICC is insensitive to that.
- 2h is not a timeframe on main (`config.tf_seconds`), so there is no 2h rho_x.
- The cost levels for XAG and FX are assumptions. Only their day-clustering enters, and it is visible: FX10 at
  <= 60 min reads 0.001 gross and 0.010 net. Cost-in-R on quiet days is the main within-day common factor at short
  holds. Both are far under the 0.05 floor.

## 4. Full tables (generated from the JSON; every number is in the cells file)

### 4.1 rho_u,day (residual within-day ICC; max upper 95 % over variants)

| class | hold | upper 95 % | point at max | point range | days | variants | binding |
|---|---|---|---|---|---|---|---|
| gold | le60m | 0.006 | 0.003 | -0.002..+0.003 | 2066-2067 | 48 | 0.050 |
| metals | le60m | 0.006 | 0.003 | -0.002..+0.003 | 2066-2067 | 96 | 0.050 |
| fx10 | le60m | 0.010 | 0.008 | -0.002..+0.008 | 2076-2076 | 96 | 0.050 |
| pooled | le60m | 0.009 | 0.008 | -0.002..+0.008 | 2076-2076 | 96 | 0.050 |
| gold+fx10 | le60m | 0.009 | 0.008 | -0.002..+0.008 | 2076-2076 | 96 | 0.050 |
| gold | 1h-4h | 0.040 | 0.023 | -0.003..+0.024 | 2065-2065 | 36 | - |
| metals | 1h-4h | 0.041 | 0.025 | -0.003..+0.025 | 2065-2065 | 72 | - |
| fx10 | 1h-4h | 0.009 | 0.003 | -0.003..+0.004 | 2076-2076 | 72 | - |
| pooled | 1h-4h | 0.010 | 0.005 | -0.002..+0.005 | 2076-2076 | 72 | - |
| gold+fx10 | 1h-4h | 0.010 | 0.005 | -0.002..+0.005 | 2076-2076 | 72 | - |
| gold | gt4h | 0.110 | 0.059 | +0.008..+0.060 | 2036-2061 | 24 | - |
| metals | gt4h | 0.120 | 0.067 | +0.009..+0.067 | 2036-2061 | 48 | - |
| fx10 | gt4h | 0.047 | 0.015 | -0.003..+0.016 | 2072-2072 | 48 | - |
| pooled | gt4h | 0.047 | 0.018 | -0.000..+0.021 | 2073-2073 | 48 | - |
| gold+fx10 | gt4h | 0.046 | 0.020 | +0.000..+0.020 | 2073-2073 | 48 | - |

### 4.2 rho_u,inst (residual cross-instrument same-day ICC)

| class | hold | day-mean upper 95 % | day-mean point range | pair upper 95 % | pair point range | days |
|---|---|---|---|---|---|---|
| metals | le60m | 0.886 | -0.043..+0.786 | 0.0450 | -0.002..+0.040 | 2066-2067 |
| fx10 | le60m | 0.442 | -0.004..+0.404 | 0.0134 | -0.000..+0.012 | 2076-2076 |
| pooled | le60m | 0.397 | +0.000..+0.362 | 0.0075 | -0.000..+0.006 | 2076-2076 |
| gold+fx10 | le60m | 0.398 | -0.008..+0.363 | 0.0121 | -0.000..+0.011 | 2076-2076 |
| metals | 1h-4h | 0.999 | -0.018..+0.882 | 0.2695 | -0.003..+0.235 | 2065-2065 |
| fx10 | 1h-4h | 0.297 | +0.002..+0.274 | 0.0538 | +0.000..+0.049 | 2076-2076 |
| pooled | 1h-4h | 0.144 | +0.002..+0.130 | 0.0277 | +0.000..+0.025 | 2076-2076 |
| gold+fx10 | 1h-4h | 0.262 | -0.002..+0.243 | 0.0520 | -0.000..+0.048 | 2076-2076 |
| metals | gt4h | 0.890 | +0.041..+0.784 | 0.6110 | -0.009..+0.488 | 2036-2061 |
| fx10 | gt4h | 0.338 | -0.006..+0.312 | 0.1790 | -0.001..+0.163 | 2072-2072 |
| pooled | gt4h | 0.169 | -0.005..+0.151 | 0.0936 | -0.001..+0.083 | 2073-2073 |
| gold+fx10 | gt4h | 0.288 | -0.002..+0.263 | 0.1606 | +0.001..+0.148 | 2073-2073 |

### 4.3 rho_u,inst vs gold

| class | hold | day-mean upper 95 % | day-mean point range | pair upper 95 % | pair point range |
|---|---|---|---|---|---|
| metals | le60m | 0.886 | -0.043..+0.786 | 0.0450 | -0.002..+0.040 |
| pooled | le60m | 0.196 | -0.158..+0.166 | 0.0040 | -0.005..+0.003 |
| gold+fx10 | le60m | 0.249 | -0.013..+0.218 | 0.0116 | -0.001..+0.010 |
| metals | 1h-4h | 0.999 | -0.018..+0.882 | 0.2695 | -0.003..+0.235 |
| pooled | 1h-4h | 0.073 | -0.109..+0.046 | 0.0126 | -0.019..+0.007 |
| gold+fx10 | 1h-4h | 0.281 | -0.007..+0.245 | 0.0560 | -0.001..+0.049 |
| metals | gt4h | 0.890 | +0.041..+0.784 | 0.6110 | -0.009..+0.488 |
| pooled | gt4h | 0.065 | -0.096..+0.037 | 0.0245 | -0.039..+0.012 |
| gold+fx10 | gt4h | 0.255 | -0.009..+0.223 | 0.1365 | -0.003..+0.117 |

### 4.4 tau_a (between-instrument SD of mean gross T2 R)

| class | hold | SD point at max | upper 95 % | tau_a (floor 0.01) | lambda_a = 1.35/tau_a^2 |
|---|---|---|---|---|---|
| metals | le60m | 0.0083 | 0.0152 | 0.0152 | 5842 |
| fx10 | le60m | 0.0024 | 0.0066 | 0.0100 | 13500 |
| pooled | le60m | 0.0025 | 0.0074 | 0.0100 | 13500 |
| gold+fx10 | le60m | 0.0023 | 0.0062 | 0.0100 | 13500 |
| metals | 1h-4h | 0.0008 | 0.0286 | 0.0286 | 1654 |
| fx10 | 1h-4h | 0.0059 | 0.0153 | 0.0153 | 5757 |
| pooled | 1h-4h | 0.0084 | 0.0199 | 0.0199 | 3416 |
| gold+fx10 | 1h-4h | 0.0074 | 0.0160 | 0.0160 | 5280 |
| metals | gt4h | 0.0130 | 0.0524 | 0.0524 | 492 |
| fx10 | gt4h | 0.0054 | 0.0199 | 0.0199 | 3390 |
| pooled | gt4h | 0.0129 | 0.0319 | 0.0319 | 1324 |
| gold+fx10 | gt4h | 0.0078 | 0.0214 | 0.0214 | 2963 |

### 4.5 rho_x per core column, fold fold_2010_11 (upper 95 %; day = within-day, inst = cross-instrument day-mean, pooled body; vsG = vs gold, pooled)

| column | 1m day / gold day / inst / vsG | 5m day / gold day / inst / vsG | 15m day / gold day / inst / vsG | 30m day / gold day / inst / vsG | 1h day / gold day / inst / vsG | 4h day / gold day / inst / vsG |
|---|---|---|---|---|---|---|
| `tsmom_score` | 0.18 / 0.18 / 0.27 / 0.10 | 0.46 / 0.48 / 0.25 / 0.19 | 0.66 / 0.67 / 0.24 / 0.20 | 0.76 / 0.78 / 0.24 / 0.20 | 0.86 / 0.87 / 0.26 / 0.18 | 0.95 / 0.96 / 0.29 / 0.18 |
| `dist_vwap48_atr` | 0.02 / 0.02 / 0.21 / 0.03 | 0.12 / 0.12 / 0.21 / 0.18 | 0.33 / 0.32 / 0.25 / 0.21 | 0.55 / 0.57 / 0.26 / 0.18 | 0.75 / 0.76 / 0.26 / 0.17 | 0.93 / 0.92 / 0.25 / 0.20 |
| `atr_ratio_14_100` | 0.01 / 0.01 / 0.51 / 0.01 | 0.06 / 0.08 / 0.74 / 0.91 | 0.21 / 0.23 / 0.69 / 0.54 | 0.44 / 0.41 / 0.72 / 0.53 | 0.72 / 0.71 / 0.74 / 0.56 | 0.96 / 0.97 / 0.77 / 0.68 |
| `er_30` | 0.02 / 0.03 / 0.17 / 0.01 | 0.06 / 0.06 / 0.23 / 0.14 | 0.18 / 0.17 / 0.28 / 0.19 | 0.29 / 0.25 / 0.28 / 0.17 | 0.52 / 0.55 / 0.28 / 0.15 | 0.81 / 0.78 / 0.23 / 0.14 |
| `bb_z_20` | 0.01 / 0.01 / 0.15 / 0.02 | 0.03 / 0.03 / 0.17 / 0.07 | 0.10 / 0.11 / 0.21 / 0.15 | 0.20 / 0.22 / 0.25 / 0.18 | 0.36 / 0.38 / 0.26 / 0.18 | 0.71 / 0.72 / 0.28 / 0.19 |
| `ret_16` | 0.01 / 0.01 / 0.21 / 0.02 | 0.05 / 0.06 / 0.30 / 0.10 | 0.16 / 0.18 / 0.27 / 0.17 | 0.30 / 0.30 / 0.29 / 0.20 | 0.52 / 0.51 / 0.28 / 0.16 | 0.86 / 0.88 / 0.30 / 0.19 |
| `ret_96` | 0.07 / 0.08 / 0.29 / 0.08 | 0.31 / 0.32 / 0.30 / 0.20 | 0.71 / 0.69 / 0.30 / 0.16 | 0.85 / 0.86 / 0.30 / 0.17 | 0.92 / 0.94 / 0.29 / 0.19 | 0.98 / 0.98 / 0.28 / 0.18 |
| `donchian_pos_20` | 0.01 / 0.01 / 0.16 / 0.02 | 0.04 / 0.04 / 0.17 / 0.11 | 0.13 / 0.14 / 0.22 / 0.16 | 0.24 / 0.25 / 0.25 / 0.19 | 0.42 / 0.46 / 0.25 / 0.19 | 0.77 / 0.80 / 0.26 / 0.20 |
| `adx14` | 0.05 / 0.12 / 0.20 / 0.08 | 0.13 / 0.13 / 0.28 / 0.25 | 0.33 / 0.31 / 0.29 / 0.20 | 0.55 / 0.54 / 0.28 / 0.18 | 0.79 / 0.80 / 0.29 / 0.19 | 0.97 / 0.97 / 0.22 / 0.17 |
| `compression_8_96` | 0.03 / 0.07 / 0.39 / -0.00 | 0.05 / 0.07 / 0.46 / 0.53 | 0.13 / 0.15 / 0.44 / 0.39 | 0.24 / 0.22 / 0.40 / 0.30 | 0.36 / 0.32 / 0.42 / 0.42 | 0.79 / 0.78 / 0.35 / 0.30 |
| `min_since_london_open` | 0.00 / -0.00 / 0.84 / 0.04 | -0.00 / -0.00 / 0.97 / 0.95 | -0.01 / -0.01 / 0.97 / 0.97 | -0.02 / -0.02 / 0.97 / 0.96 | -0.04 / -0.05 / 0.96 / 0.94 | -0.20 / -0.20 / 0.99 / 0.99 |

### 4.6 rho_x per core column, fold learn (upper 95 %; day = within-day, inst = cross-instrument day-mean, pooled body; vsG = vs gold, pooled)

| column | 1m day / gold day / inst / vsG | 5m day / gold day / inst / vsG | 15m day / gold day / inst / vsG | 30m day / gold day / inst / vsG | 1h day / gold day / inst / vsG | 4h day / gold day / inst / vsG |
|---|---|---|---|---|---|---|
| `tsmom_score` | 0.17 / 0.18 / 0.17 / 0.03 | 0.46 / 0.48 / 0.16 / 0.05 | 0.66 / 0.68 / 0.17 / 0.05 | 0.76 / 0.78 / 0.18 / 0.04 | 0.86 / 0.87 / 0.19 / 0.02 | 0.96 / 0.96 / 0.17 / 0.02 |
| `dist_vwap48_atr` | 0.02 / 0.02 / 0.20 / 0.03 | 0.11 / 0.12 / 0.15 / 0.05 | 0.31 / 0.31 / 0.15 / 0.08 | 0.52 / 0.55 / 0.16 / 0.06 | 0.74 / 0.76 / 0.16 / 0.05 | 0.92 / 0.92 / 0.19 / 0.04 |
| `atr_ratio_14_100` | 0.01 / 0.01 / 0.60 / 0.01 | 0.05 / 0.05 / 0.71 / 0.82 | 0.18 / 0.18 / 0.63 / 0.52 | 0.40 / 0.38 / 0.62 / 0.52 | 0.66 / 0.67 / 0.61 / 0.52 | 0.93 / 0.94 / 0.58 / 0.48 |
| `er_30` | 0.02 / 0.02 / 0.14 / 0.02 | 0.06 / 0.06 / 0.18 / 0.11 | 0.17 / 0.18 / 0.23 / 0.15 | 0.28 / 0.26 / 0.22 / 0.17 | 0.53 / 0.56 / 0.21 / 0.16 | 0.80 / 0.79 / 0.18 / 0.14 |
| `bb_z_20` | 0.01 / 0.01 / 0.20 / 0.02 | 0.03 / 0.03 / 0.19 / 0.04 | 0.10 / 0.11 / 0.17 / 0.04 | 0.20 / 0.22 / 0.16 / 0.06 | 0.34 / 0.36 / 0.15 / 0.06 | 0.70 / 0.71 / 0.16 / 0.06 |
| `ret_16` | 0.01 / 0.01 / 0.16 / 0.01 | 0.05 / 0.06 / 0.23 / 0.09 | 0.15 / 0.18 / 0.16 / 0.08 | 0.29 / 0.31 / 0.18 / 0.08 | 0.50 / 0.48 / 0.18 / 0.06 | 0.85 / 0.86 / 0.19 / 0.05 |
| `ret_96` | 0.07 / 0.08 / 0.19 / 0.03 | 0.30 / 0.33 / 0.18 / 0.08 | 0.69 / 0.68 / 0.18 / 0.06 | 0.84 / 0.85 / 0.19 / 0.05 | 0.92 / 0.93 / 0.20 / 0.05 | 0.98 / 0.98 / 0.22 / 0.06 |
| `donchian_pos_20` | 0.01 / 0.01 / 0.18 / 0.02 | 0.04 / 0.04 / 0.16 / 0.04 | 0.12 / 0.14 / 0.15 / 0.06 | 0.23 / 0.24 / 0.15 / 0.07 | 0.41 / 0.43 / 0.15 / 0.06 | 0.76 / 0.79 / 0.16 / 0.06 |
| `adx14` | 0.04 / 0.06 / 0.17 / 0.03 | 0.12 / 0.13 / 0.21 / 0.14 | 0.32 / 0.33 / 0.23 / 0.18 | 0.55 / 0.54 / 0.23 / 0.18 | 0.79 / 0.80 / 0.22 / 0.17 | 0.97 / 0.97 / 0.20 / 0.17 |
| `compression_8_96` | 0.02 / 0.03 / 0.51 / 0.02 | 0.05 / 0.05 / 0.46 / 0.53 | 0.12 / 0.13 / 0.46 / 0.41 | 0.22 / 0.21 / 0.39 / 0.31 | 0.35 / 0.32 / 0.40 / 0.47 | 0.78 / 0.78 / 0.29 / 0.27 |
| `min_since_london_open` | 0.00 / -0.00 / 0.85 / 0.03 | -0.00 / -0.00 / 0.96 / 0.93 | -0.01 / -0.01 / 0.96 / 0.93 | -0.02 / -0.02 / 0.96 / 0.93 | -0.04 / -0.05 / 0.95 / 0.91 | -0.20 / -0.20 / 0.98 / 0.96 |

## 5. Run log and honesty notes
- Run 1 had two defects; its numbers were not used.
  - The > 4 h grid never fired: 4h closes are UTC-aligned, while the grid was phased to the NY roll.
  - FX10's vs-gold set had no gold in it.
  - Both were fixed (grid = bar rank within the session day; a `gold+fx10` set).
  - Run 1's <= 60 min upper bounds were within 0.002 of run 2's. Its 1-4 h bounds were up to 0.017 lower (metals 0.024
    against 0.041), because the grid phase differed.
- Run 2 gave the tables above.
- Run 3 split the output into the summary and the cells file (repo cap of 800 kB). Same seeds; equal to run 2 within
  5e-6 (rounding).
- Run 4 is the committed output. It adds behaviour-preserving type fixes for mypy, so the recorded script sha256 is
  the committed script's. It was checked equal to run 3.
- None of the four runs read anything outside learn 2010-17. They read control outcomes and features only, so K
  stays 0.
- Workers: 4.

## 6. Follow-ups (not done in this lane)
1. **Validator gap.** `PowerFirstSettings.rho_day_le_60m` accepts 0.05 today with no file (`ge=0.05`). ADR 0085 says
   the validator accepts rho_day < 0.25 "only with `rho_day_measurement` naming that file".
   - Implementer: add `research.power_first.rho_day_measurement: str | None`.
   - Refuse `rho_day_le_60m < 0.25` unless it names this JSON, the file's `script.sha256` matches the committed script,
     and the value is at least `rho_day_le_60m[class]`.
   - Routing: quant review and code review. The setting stays 0.25 until the quant signs.
2. **Quant ruling needed** on the rho_inst level (3.2). It decides the rev-4 ladder and D-13.
3. **rho_x for X-block columns** (X1-X5) once they are frozen. The same script reads any column list.
