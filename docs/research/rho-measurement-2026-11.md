# D-12: the sr-v3 independence inputs, measured on learn 2010-17 (rho_x, rho_u,day, rho_u,inst, tau_a)

- **Lane and date.** Owned by the ml-researcher (principal) for the research director. Written 2026-11-25, families
  run 2026-11-26.
- **Rulings measured.**
  - ADR 0085 amendment 2026-11-25 (quant Q1, R13).
  - The quant's D-12 specification on PR #460 rev 3 (Q1, Q2).
  - The quant rulings on PR #466 (D-12 a-d; ADR 0085 amendment "quant rulings D-12 a-d").
- **Script.** `scripts/research/measure_rho.py`.
- **Output.**
  - `docs/research/rho-measurement-2026-11.json`: schema `rho_day_measurement/v2`. It holds the script sha256, every
    input file's sha256, the `candidates` list, the stated deviations and the bindings. The `rho_day_measurement`
    validator reads it.
  - `docs/research/rho-measurement-2026-11-cells.csv.gz`: every cell. Its sha256 is in the JSON.
- **K: 0.** The inputs are control-grid and replayed retired-family candidates, plus label-free features. No sr-v3 or
  H-102 arm, candidate or label is read. Nothing outside learn 2010-17 is opened.

## 1. Binding values (max(0.05, grid, families) for rho_u,day <= 60 min; max(grid, families) otherwise)

| Input | Class | <= 60 min | 1-4 h | > 4 h |
|---|---|---|---|---|
| rho_u,day (upper 95 %) | gold | **0.148** (grid 0.006, families 0.148) | 0.155 | 0.169 |
| | FX10 | **0.062** (grid 0.010) | 0.060 | 0.097 |
| | metals | **0.137** | 0.124 | 0.160 |
| | pooled body | **0.054** (grid 0.009) | 0.053 | 0.070 |
| rho_u,inst day-mean (upper 95 %) | FX10 | 0.443 (grid) | 0.297 | 0.338 |
| | pooled body | 0.397 (grid) | 0.151 | 0.185 |
| rho_u,inst vs gold, day-mean (D-13; max over raw, PC1 and per-instrument sign) | XAG | 0.885 | 1.00 (capped) | 1.00 (capped) |
| | FX10 | 0.256 | 0.304 | 0.350 |
| tau_a (floor 0.01 R; both-sides and long-only) | pooled body | **0.041** (lambda_a 820) | 0.055 (441) | 0.063 (341) |
| | gold + FX10 | 0.038 (955) | 0.042 (772) | 0.042 (757) |

- **The validator file value `rho_day_le_60m`** is gold 0.148, metals 0.137, FX10 0.062, pooled 0.054, gold + FX10
  0.056.
  - The validator refuses any setting below the largest of these, 0.148, until a re-measurement or a quant ruling
    changes it.
  - The shipped setting stays 0.25.
- **rho_x** (label-blind, fold 2010-11) is unchanged from the first delivery; see 4.6.

## 2. What the families changed, and why I flag it

- **rho_u,day.** The families raise the <= 60 min binding from the 0.05 floor to 0.054-0.148.
  - Each family on its own is sparse. Non-overlapping candidates per instrument-day: box_break_close 2.6-3.7,
    sweep_absorb 1.8-1.9, session_range_break 1.7 (grid 22-90).
  - Their within-day ICC points span -0.05..+0.08, and their 95 % intervals are wide.
  - The binding comes from **sweep_absorb (0.148) and session_range_break (0.142) on gold**.
  - The three families pooled ("all", 4-5 a day) read **0.022 at most**; the grid reads 0.006.
  - A max over 4 sources x 24 variants of 97.5 % upper bounds is biased upward by multiplicity. The bias is
    conservative for n_eff, which is the direction of the ruling.
  - **For the quant:** whether the families' bound should be the pooled-families estimate (better powered) rather
    than the max over each sparse family. The JSON carries both (`families`, `families_pooled`).
- **tau_a.** It rises from the floor (0.01) to 0.036-0.135.
  - Part of this is sampling noise. Each family has a few thousand candidates per instrument, so the SE of each
    instrument mean is 0.02-0.03 R, and an SD of means is biased up by about that much.
  - Part is real: the families' gross T2 means differ by instrument.
  - Under ruling (d), lambda_a uses the pooled-body row: **820 / 441 / 341** (<= 60 min / 1-4 h / > 4 h), replacing
    the provisional 13,500 / 3,416 / 1,324. Instrument levels are shrunk much less than the grid alone implied.
- **rho_u,inst and D-13.**
  - The families do not move the day-mean binding at <= 60 min; the grid stays the max.
  - At > 4 h they raise pooled vs gold to 0.325, and gold + FX10 to 0.350.
  - Under ruling (b) no new instrument is admitted against gold at any bucket from these classes. XAG fails at
    0.885-1.0 (capped), and FX10 fails at 0.256-0.350 (FX10 and XAG stay under R13 pricing).
  - Correlation bounds above 1 (metals > 4 h, families: 3.28) are sampling noise in sparse cells and are capped at
    1.0.

## 3. Method

**Candidates.**
- **Control grid.** Every hold_bars-th tf bar of each New York 17:00 session day, the same times on every
  instrument. No rule.
- **Replayed families.** Outcome-blind candidates from the established replay (`history_replay._horizons`: the
  arm's rule, window, common filters and stop band; every candidate, taken or not; tier-1 calendar blackout).
  - box_break_close: MB15, W2.
  - sweep_absorb: MSA15.
  - session_range_break: GS1.
  - All are retired arms, outside sr-v3 (`self_retrain.PRENAMED`) and H-102. None has a USD stop band, so the rules
    run on FX prices.
  - `config.RHO_EXCLUDED_ARMS` is the single source of the exclusion list, and the script refuses to start if a
    family uses one of those arms.
  - Each family decision is labelled with every hold-bucket geometry below; the family's own exits are not used.
    Candidates are thinned greedily so holds never overlap per instrument.
  - Family side, long and short variants are all read.
  - "all" pools the three families, with same-minute duplicates removed.

**Geometry.** stop = k x ATR14_tf x sqrt(hold_bars); T2 (stop 1, target 2, time exit at H) and T6 (stop 2, time exit).
- <= 60 min: 1m x 15, 1m x 30, 5m x 12, 15m x 4.
- 1-4 h: 15m x 8, 1h x 2, 1h x 4.
- > 4 h: 4h x 2, 4h x 3.
- Entry at the next minute's open; barriers on 1m bars, stop first on a shared minute.
- Outcomes gross and net of bps costs: gold W2 0.575 / 1.75 bp; XAG 2.5 / 3.0 and FX 0.8 / 0.5 are assumptions.

**Estimators.** Pairwise residual moments on per (instrument, day) sufficient statistics. Each instrument is centred on
its own mean, which is the unit intercept plus the instrument level.
- rho_u,day: same-instrument same-day pairs.
- rho_u,inst: the day-mean level, ruling (a). The pair level is reported, never used.
- vs gold: pairs with gold only.
- tau_a: SD across instruments of the mean gross T2 R.
- rho_x: the same statistics on z-scored core-12 columns, dense tf rows, no labels.

**Uncertainty and orientation.**
- ISO-week block bootstrap, all instruments jointly, B = 2,000, fixed seeds; upper = the 97.5th percentile.
- Directional series are read raw, PC1-oriented, and signed per instrument by their learn daily-return correlation
  with gold. The binding is the max over orientations, ruling (b).

**Data.**
- Dukascopy XAUUSD 1m (`windows.read_window_bars(raw, "learn")`).
- HistData 1m, 2010..2017 files only, r11-v2.1 tier A / A-consistent months only.
- Candidates with a horizon reaching 2018-01-01 are dropped. No test_old, confirm or holdout file is opened.
- Index, energy, crosses and XAUEUR have no graded learn month, so their priors stand.

**Stated deviations** (also in the JSON):
- the 1.5 x session-median TR stop floor is not applied;
- HistData has no volume, so VWAP is equal-weight;
- XAG and FX costs are assumed;
- there is no 2h rho_x (2h is not a repo timeframe).

## 4. Full tables (generated from the JSON)

### 4.1 rho_u,day (residual within-day ICC, upper 95 %)

| class | hold | grid | families (max of each + pooled) | families pooled only | binding |
|---|---|---|---|---|---|
| gold | le60m | 0.006 | 0.148 | 0.022 | **0.148** |
| metals | le60m | 0.006 | 0.137 | 0.020 | **0.137** |
| fx10 | le60m | 0.010 | 0.062 | 0.014 | **0.062** |
| pooled | le60m | 0.009 | 0.054 | 0.013 | **0.054** |
| gold+fx10 | le60m | 0.009 | 0.056 | 0.013 | **0.056** |
| gold | 1h-4h | 0.040 | 0.155 | 0.036 | **0.155** |
| metals | 1h-4h | 0.042 | 0.124 | 0.034 | **0.124** |
| fx10 | 1h-4h | 0.009 | 0.060 | 0.012 | **0.060** |
| pooled | 1h-4h | 0.011 | 0.053 | 0.010 | **0.053** |
| gold+fx10 | 1h-4h | 0.010 | 0.055 | 0.010 | **0.055** |
| gold | gt4h | 0.110 | 0.169 | 0.114 | **0.169** |
| metals | gt4h | 0.119 | 0.160 | 0.109 | **0.160** |
| fx10 | gt4h | 0.045 | 0.097 | 0.050 | **0.097** |
| pooled | gt4h | 0.047 | 0.070 | 0.047 | **0.070** |
| gold+fx10 | gt4h | 0.047 | 0.067 | 0.048 | **0.067** |

### 4.2 rho_u,inst, day-mean level (upper 95 %; capped at 1)

| class | hold | grid | families | families pooled only | binding |
|---|---|---|---|---|---|
| metals | le60m | 0.885 | 0.604 | 0.511 | **0.885** |
| fx10 | le60m | 0.443 | 0.159 | 0.122 | **0.443** |
| pooled | le60m | 0.397 | 0.128 | 0.063 | **0.397** |
| gold+fx10 | le60m | 0.399 | 0.132 | 0.101 | **0.399** |
| metals | 1h-4h | 1.002 | 1.075 | 0.635 | **1.000** |
| fx10 | 1h-4h | 0.297 | 0.206 | 0.182 | **0.297** |
| pooled | 1h-4h | 0.143 | 0.151 | 0.081 | **0.151** |
| gold+fx10 | 1h-4h | 0.262 | 0.173 | 0.160 | **0.262** |
| metals | gt4h | 0.891 | 3.278 | 1.036 | **1.000** |
| fx10 | gt4h | 0.338 | 0.304 | 0.259 | **0.338** |
| pooled | gt4h | 0.169 | 0.185 | 0.104 | **0.185** |
| gold+fx10 | gt4h | 0.287 | 0.317 | 0.261 | **0.317** |

### 4.3 rho_u,inst vs gold, day-mean level (upper 95 %; max over raw, PC1 and per-instrument-sign orientations; capped at 1)

| class | hold | grid | families | families pooled only | binding |
|---|---|---|---|---|---|
| metals | le60m | 0.885 | 0.604 | 0.511 | **0.885** |
| pooled | le60m | 0.215 | 0.090 | 0.059 | **0.215** |
| gold+fx10 | le60m | 0.256 | 0.100 | 0.075 | **0.256** |
| metals | 1h-4h | 1.002 | 1.075 | 0.635 | **1.000** |
| pooled | 1h-4h | 0.255 | 0.135 | 0.103 | **0.255** |
| gold+fx10 | 1h-4h | 0.304 | 0.156 | 0.132 | **0.304** |
| metals | gt4h | 0.891 | 3.278 | 1.036 | **1.000** |
| pooled | gt4h | 0.234 | 0.325 | 0.197 | **0.325** |
| gold+fx10 | gt4h | 0.268 | 0.350 | 0.235 | **0.350** |

### 4.4 tau_a (upper 95 % of the between-instrument SD of mean gross T2 R; both-sides and long-only)

| class | hold | grid | families | families pooled only | tau_a (floor 0.01) | lambda_a |
|---|---|---|---|---|---|---|
| metals | le60m | 0.0149 | 0.0961 | 0.0697 | **0.0961** | 146 |
| fx10 | le60m | 0.0065 | 0.0361 | 0.0189 | **0.0361** | 1038 |
| pooled | le60m | 0.0074 | 0.0406 | 0.0268 | **0.0406** | 820 |
| gold+fx10 | le60m | 0.0066 | 0.0376 | 0.0193 | **0.0376** | 955 |
| metals | 1h-4h | 0.0289 | 0.1353 | 0.0699 | **0.1353** | 74 |
| fx10 | 1h-4h | 0.0149 | 0.0432 | 0.0244 | **0.0432** | 723 |
| pooled | 1h-4h | 0.0195 | 0.0553 | 0.0307 | **0.0553** | 441 |
| gold+fx10 | 1h-4h | 0.0159 | 0.0418 | 0.0239 | **0.0418** | 772 |
| metals | gt4h | 0.0517 | 0.1285 | 0.0853 | **0.1285** | 82 |
| fx10 | gt4h | 0.0214 | 0.0402 | 0.0290 | **0.0402** | 835 |
| pooled | gt4h | 0.0318 | 0.0629 | 0.0362 | **0.0629** | 341 |
| gold+fx10 | gt4h | 0.0219 | 0.0422 | 0.0303 | **0.0422** | 757 |

### 4.5 rho_u,day <= 60 min by family (upper 95 %, max over variants)

| class | grid | box_break_close | sweep_absorb | session_range_break | all |
|---|---|---|---|---|---|
| gold | 0.006 | 0.053 | 0.148 | 0.142 | 0.022 |
| metals | 0.006 | 0.047 | 0.105 | 0.137 | 0.020 |
| fx10 | 0.010 | 0.016 | 0.062 | 0.043 | 0.014 |
| pooled | 0.009 | 0.014 | 0.054 | 0.043 | 0.013 |
| gold+fx10 | 0.009 | 0.015 | 0.056 | 0.038 | 0.013 |

### 4.6 rho_x per core column, fold fold_2010_11 (upper 95 %: pooled within-day / gold within-day / pooled cross-instrument day-mean / vs gold day-mean)

| column | 1m | 5m | 15m | 30m | 1h | 4h |
|---|---|---|---|---|---|---|
| `tsmom_score` | 0.18 / 0.18 / 0.28 / 0.10 | 0.46 / 0.48 / 0.25 / 0.19 | 0.66 / 0.67 / 0.24 / 0.20 | 0.76 / 0.78 / 0.24 / 0.20 | 0.86 / 0.87 / 0.26 / 0.17 | 0.95 / 0.96 / 0.29 / 0.18 |
| `dist_vwap48_atr` | 0.02 / 0.02 / 0.21 / 0.03 | 0.12 / 0.12 / 0.21 / 0.19 | 0.32 / 0.32 / 0.25 / 0.20 | 0.55 / 0.57 / 0.26 / 0.18 | 0.75 / 0.76 / 0.26 / 0.17 | 0.93 / 0.92 / 0.25 / 0.20 |
| `atr_ratio_14_100` | 0.01 / 0.01 / 0.51 / 0.01 | 0.06 / 0.08 / 0.74 / 0.91 | 0.20 / 0.24 / 0.69 / 0.54 | 0.44 / 0.41 / 0.71 / 0.53 | 0.72 / 0.71 / 0.74 / 0.57 | 0.96 / 0.97 / 0.77 / 0.68 |
| `er_30` | 0.02 / 0.03 / 0.17 / 0.01 | 0.06 / 0.06 / 0.23 / 0.14 | 0.18 / 0.17 / 0.28 / 0.20 | 0.29 / 0.25 / 0.28 / 0.17 | 0.52 / 0.55 / 0.27 / 0.15 | 0.81 / 0.78 / 0.23 / 0.14 |
| `bb_z_20` | 0.01 / 0.01 / 0.15 / 0.02 | 0.03 / 0.03 / 0.17 / 0.10 | 0.10 / 0.11 / 0.21 / 0.15 | 0.20 / 0.22 / 0.25 / 0.18 | 0.36 / 0.38 / 0.26 / 0.18 | 0.71 / 0.72 / 0.28 / 0.19 |
| `ret_16` | 0.01 / 0.01 / 0.21 / 0.02 | 0.05 / 0.06 / 0.30 / 0.11 | 0.16 / 0.18 / 0.27 / 0.17 | 0.30 / 0.30 / 0.30 / 0.20 | 0.52 / 0.51 / 0.28 / 0.16 | 0.86 / 0.88 / 0.30 / 0.19 |
| `ret_96` | 0.07 / 0.08 / 0.29 / 0.08 | 0.31 / 0.32 / 0.30 / 0.20 | 0.71 / 0.69 / 0.29 / 0.16 | 0.86 / 0.86 / 0.30 / 0.18 | 0.92 / 0.94 / 0.29 / 0.19 | 0.98 / 0.98 / 0.27 / 0.19 |
| `donchian_pos_20` | 0.01 / 0.01 / 0.16 / 0.02 | 0.04 / 0.04 / 0.17 / 0.13 | 0.13 / 0.14 / 0.22 / 0.17 | 0.24 / 0.25 / 0.25 / 0.19 | 0.42 / 0.46 / 0.25 / 0.19 | 0.77 / 0.80 / 0.26 / 0.20 |
| `adx14` | 0.05 / 0.12 / 0.20 / 0.08 | 0.13 / 0.13 / 0.28 / 0.25 | 0.32 / 0.31 / 0.29 / 0.20 | 0.54 / 0.54 / 0.28 / 0.17 | 0.79 / 0.80 / 0.29 / 0.19 | 0.97 / 0.97 / 0.22 / 0.16 |
| `compression_8_96` | 0.03 / 0.07 / 0.39 / -0.00 | 0.05 / 0.07 / 0.46 / 0.52 | 0.13 / 0.15 / 0.44 / 0.40 | 0.24 / 0.22 / 0.40 / 0.29 | 0.36 / 0.32 / 0.42 / 0.42 | 0.79 / 0.78 / 0.35 / 0.31 |
| `min_since_london_open` | 0.00 / -0.00 / 0.84 / 0.04 | -0.00 / -0.00 / 0.97 / 0.95 | -0.01 / -0.01 / 0.97 / 0.97 | -0.02 / -0.02 / 0.97 / 0.96 | -0.04 / -0.05 / 0.96 / 0.94 | -0.20 / -0.20 / 0.99 / 0.99 |

### 4.7 rho_x per core column, fold learn (upper 95 %: pooled within-day / gold within-day / pooled cross-instrument day-mean / vs gold day-mean)

| column | 1m | 5m | 15m | 30m | 1h | 4h |
|---|---|---|---|---|---|---|
| `tsmom_score` | 0.17 / 0.18 / 0.17 / 0.11 | 0.46 / 0.48 / 0.16 / 0.23 | 0.66 / 0.68 / 0.17 / 0.24 | 0.76 / 0.78 / 0.18 / 0.25 | 0.86 / 0.87 / 0.19 / 0.25 | 0.96 / 0.96 / 0.17 / 0.29 |
| `dist_vwap48_atr` | 0.02 / 0.02 / 0.20 / 0.03 | 0.11 / 0.12 / 0.15 / 0.17 | 0.31 / 0.31 / 0.15 / 0.23 | 0.52 / 0.55 / 0.16 / 0.23 | 0.74 / 0.76 / 0.16 / 0.23 | 0.92 / 0.92 / 0.18 / 0.25 |
| `atr_ratio_14_100` | 0.01 / 0.01 / 0.60 / 0.01 | 0.05 / 0.05 / 0.71 / 0.82 | 0.19 / 0.17 / 0.63 / 0.52 | 0.40 / 0.38 / 0.62 / 0.53 | 0.66 / 0.67 / 0.60 / 0.52 | 0.93 / 0.94 / 0.58 / 0.48 |
| `er_30` | 0.02 / 0.02 / 0.14 / 0.02 | 0.06 / 0.06 / 0.18 / 0.12 | 0.17 / 0.18 / 0.22 / 0.15 | 0.28 / 0.26 / 0.22 / 0.17 | 0.53 / 0.55 / 0.21 / 0.16 | 0.80 / 0.79 / 0.18 / 0.14 |
| `bb_z_20` | 0.01 / 0.01 / 0.20 / 0.02 | 0.03 / 0.03 / 0.19 / 0.07 | 0.10 / 0.11 / 0.17 / 0.13 | 0.20 / 0.22 / 0.16 / 0.19 | 0.34 / 0.36 / 0.15 / 0.22 | 0.70 / 0.71 / 0.16 / 0.22 |
| `ret_16` | 0.01 / 0.01 / 0.16 / 0.02 | 0.05 / 0.06 / 0.23 / 0.13 | 0.15 / 0.18 / 0.17 / 0.15 | 0.29 / 0.31 / 0.18 / 0.19 | 0.50 / 0.48 / 0.18 / 0.19 | 0.85 / 0.86 / 0.19 / 0.18 |
| `ret_96` | 0.07 / 0.08 / 0.19 / 0.13 | 0.30 / 0.33 / 0.18 / 0.19 | 0.69 / 0.68 / 0.18 / 0.18 | 0.84 / 0.85 / 0.19 / 0.18 | 0.92 / 0.93 / 0.20 / 0.19 | 0.98 / 0.98 / 0.22 / 0.23 |
| `donchian_pos_20` | 0.01 / 0.01 / 0.18 / 0.02 | 0.04 / 0.04 / 0.16 / 0.10 | 0.12 / 0.14 / 0.15 / 0.16 | 0.23 / 0.24 / 0.15 / 0.21 | 0.41 / 0.43 / 0.15 / 0.22 | 0.76 / 0.79 / 0.16 / 0.22 |
| `adx14` | 0.04 / 0.06 / 0.17 / 0.03 | 0.12 / 0.14 / 0.21 / 0.14 | 0.32 / 0.33 / 0.23 / 0.18 | 0.55 / 0.54 / 0.23 / 0.18 | 0.79 / 0.80 / 0.22 / 0.17 | 0.97 / 0.97 / 0.20 / 0.17 |
| `compression_8_96` | 0.02 / 0.03 / 0.51 / 0.02 | 0.05 / 0.05 / 0.46 / 0.53 | 0.12 / 0.13 / 0.46 / 0.41 | 0.22 / 0.21 / 0.39 / 0.32 | 0.35 / 0.32 / 0.40 / 0.47 | 0.78 / 0.78 / 0.29 / 0.27 |
| `min_since_london_open` | 0.00 / -0.00 / 0.85 / 0.03 | -0.00 / -0.00 / 0.96 / 0.93 | -0.01 / -0.01 / 0.96 / 0.93 | -0.02 / -0.02 / 0.96 / 0.93 | -0.04 / -0.05 / 0.95 / 0.91 | -0.20 / -0.20 / 0.98 / 0.96 |

## 5. Run log
- Runs 1-4 (2026-11-25) used the control grid only.
  - Run 1 had two defects, fixed before any number was used: the > 4 h grid phase, and the missing gold in the
    FX10 vs-gold set.
  - Runs 3 and 4 were identical.
- Run 5 (families added) was killed by the session interruption.
- Run 6 (2026-11-26) added the families, on main 553d1d70 merged into the branch. Grid values were unchanged from
  run 4 to the table's precision.
- **Run 7 (2026-11-26) is the committed output.** It is the same code with only the `--raw` default changed to
  `ROOT / "raw"` (code review of #466). Its results are identical to run 6 (maximum difference 0), and the recorded
  script sha256 equals the committed script.
- 4 workers in every run. Every run read learn 2010-17 only, so K stays 0.

## 6. What is wired, and what is open
- **Validator (ruling (c), code review of #466).** `research.power_first.rho_day_measurement` and the pinned
  `rho_day_measurement_sha256` are in place (`config.check_rho_measurement`; tests `test_d12_*`).
  - At load, the validator checks the pinned file sha, the cells-file sha, the window (2010-01-01..2018-01-01), the
    two families, and that every class is finite and present.
  - It also checks that each <= 60 min binding equals the value recomputed from the cells.
  - It never hashes the live script. A CI test ties the committed script to the JSON's `script.sha256`.
  - The setting stays 0.25 and both fields stay null.
- **Open for the quant.**
  1. Should the families' rho_u,day bound be the pooled-families estimate rather than the max of sparse single
     families? Section 2: 0.022 against 0.148 for gold at <= 60 min.
  2. Should tau_a carry a sampling-noise correction (method of moments)? Its upper bound is partly the SE of sparse
     family means.
- **Open for D-12a follow-up.** rho_x for the X1-X5 block columns, once frozen. The same script reads any column
  list.
