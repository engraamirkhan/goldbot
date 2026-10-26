# Slice G: key levels (session, day, week, month, round numbers), first-touch behaviour and level-based trades

Staff research lead for the research-director, 2026-10-26. Follows `README.md` (time-structure frame) sections 4-5.
Owner directives (2026-10-26, verbatim): "opening closing sessions , previous day low, hig, week high low, monthely etc"
and "traders trade on the strategies i shared such as range breakers but not limited to, so how are you ruling these out".
Scripts: `scripts/research/key_levels_discovery.py` (events, trades, controls; 8 s) and
`scripts/research/key_levels_tables.py` (tables). Data: Dukascopy 1m mid, **2010-2017 only** (the loader opens only
those eight files; nothing from 2018 on was read). No trial, no registration, no secrets.

## 0. Answer to the owner and the three headlines

Range breakers and level strategies are **not ruled out wholesale**; they are measured against randomised levels, which
is the only fair test. Result in one line: a close through a key level carries a small gross edge on 1h/4h
(gross +0.04 to +0.08 R, t 2-3) but **a random level at the same distance earns about two thirds of it**; the part that
belongs to the level is +0.02 to +0.03 R (CI touching 0), and at 15m it is nil. Costs decide the rest.

1. **Levels break more often than random levels, they do not reverse and they do not attract price.** First touch of
   prev-day high/low closes beyond the level 51.5% of the time on 15m vs 44.6% for random levels at the same distance
   (1h 47.6 vs 43.0, 4h 44.5 vs 41.6); previous-week high/low 53.5 vs 48.5 (15m), 55.0 vs 47.2 (1h). P(touch within
   the window) is the same as for random levels (e.g. PDH/PDL 46.0% vs 47.0% at 15m): **no magnet**. This is the
   Osler stop-cascade direction; the size is a few points.
2. **A resting-limit retest of a key level is worse than a random level**, the baseline slice E found positive for
   order blocks: real minus control at 1.0 ATR stop, 2R target is **-0.030 (15m), -0.052 (1h), -0.056 (4h)**, all CIs
   below zero (section 4.3). The generic-retest edge in E's order-block result is therefore not a property of
   levels; key levels do not beat it, they lose to it.
3. **Best cell: break of a prior-period level during Asia hours, 1h** (stop 1.0 ATR back through the level, 2R target):
   gross +0.119 R (t 3.05, n 3,738) vs control +0.051, difference +0.067 [+0.017, +0.105]; 8 of 8 years positive and
   above control. Net: **-0.047 R under the research prior, +0.082 R under measured-fill cost** (+0.112 at today's
   ATR scaling), and about 0 once the measured $0.70 stop overshoot is charged on stopped trades (-0.013). One of about
   900 table cells, so a t of 3 is what chance produces at this look count (section 8): an exploration arm, not a
   trial.

## 1. What was already tried (not re-run)

| Topic | Source | Status here |
|---|---|---|
| Range/Donchian breakout 1h (R-04), session-open breakout 15m (R-02) | `hypotheses.md` B | Not re-run. The level-break rule here differs (the level is a prior-period value, not the last N bars; exits include partial + trail) |
| Sweep-and-reclaim fade 15m of prior day / prior session extremes (#88, R-14, gross -0.028 R) | H-48 | Re-measured only as a by-product (FADE rows); the 15m FADE result below (gross +0.034, control +0.028) agrees: nothing |
| EQH/EQL magnets (R-13), Fibonacci vs random fractions (README 6.8) | kb5-stage1, README | Magnet test here is the same design on key levels: negative |
| Box break + MFI (H-50, R-15: gross +0.030 R, net -0.297 R) | `prereg-2026-10-19-h50-followup.md` | Cost-per-R lesson applied: stop-size and cost tables in section 5 |
| `arms-2026-10-25.md` S2A/S2B opening-range arms, S3A sweep_absorb | arms doc | Not duplicated; G-1 below is a different arm |

## 2. Method (all fixed before any outcome was read; one pass of the script; no parameter was changed after seeing results)

* **Bars.** 15m, 1h, 4h from the 1m mid; spread = bar mean of ask minus bid. ATR = Wilder 14 on the same bars,
  value at the bar before the touch. Trading day = NY 17:00 to 17:00 (README 4.2). 4h bins are UTC-aligned (a bin
  straddles the NY day start; the first touch is always on a bin that starts after the level exists).
* **Levels, all causal.** Daily open (DO); previous-day high/low/close (PDH/PDL/PDC); previous NY-session high/low/close
  (PNH/PNL/PNC: identical to the previous-day figures because the NY session ends at 17:00, kept as a check); Asia
  (Tokyo 08:00 to London 08:00) high/low/close, available at London 08:00; London (08:00 to NY 08:30) high/low/close,
  available at NY 08:30; session opens (Asia, London, NY; available 1 h after the open); weekly open and
  previous-week high/low (fixed at Friday 17:00 NY, tradable from the next Sunday open); monthly, quarterly, yearly open
  and previous-period high/low; round numbers $10 (not multiples of 50), $50 (not multiples of 100), $100: the nearest
  one above and one below the day's first price. Window = the level's trading day (the first 5 trading days for week,
  month, quarter, year levels). 30 level groups, 17 reporting families.
* **First touch** = first bar of the window whose range contains the level. Classification of that bar by its close:
  **break** (closes beyond by >= 0.1 ATR), **sweep** (wick through by >= 0.1 ATR, closes back on the original side),
  **reject** (touches, pierce < 0.1 ATR, closes on the original side), **stall** (closes beyond by < 0.1 ATR).
  Fixed-horizon drift: close 8 bars later, signed in the break direction, in ATR.
* **Rules traded (first touch only, one trade per level and window).** BRK: enter at the close of a break bar, stop S ATR
  back on the original side of the level (S = 0.5 and 1.0). FADE: enter at the close of a reject or sweep bar against
  the break, stop 0.1 ATR beyond the touch extreme, skipped if the stop is over 3 ATR. RET: resting limit at the level,
  stop S ATR beyond (S = 0.5, 1.0), the generic retest of slice E. **Exits (two, both reported):** *fixed* = stop or 2R
  target; *PT* = 50% off at 1R, the rest with the stop at entry and then a 1.5 ATR chandelier trail, both capped at 32
  bars. Stop-first on every bar; for a limit the target counts only after the fill bar (E's convention). Next-level
  targets were **not** run (see section 9).
* **Control (README 4.4, E's design generalised).** For every real level, 6 random levels at `ref +/- d` (random sign),
  where ref is the last close before the window opens and d the real level's distance: same window, same distance
  distribution, same first-touch rule, same trade rule. Read the **difference real minus control** with a day-block
  bootstrap (400 draws) of the pooled means; t is the day-clustered CR1 t of the real mean. A nonzero control is the
  geometry of the bracket, not an edge.
* **Conditioning (causal at the touch bar).** Session of the touch bar; range used = (day high - low so far at the bar
  before) / forecast, where the forecast is the mean daily range of the previous 20 days (the causal RV20/ATR-style
  forecast; range expansion is the one predictable thing, README 6.6); distance travelled = |close before touch - day
  open| / forecast; daily trend = sign of previous close minus its EMA20, direction agreement with the trade.
* **Costs, three nets, labelled.** *Prior* = bar spread + 0.37 $/oz (README 4.6). *Measured* = $0.16/oz round trip
  (spread 0.10 + commission 0.06, `cost-model-1m-2026-10-25.md`: spread mean 0.079, commission 0.06) on the **2010-2017
  stop sizes** (stops of $1-10, so the measured cost is large in R here). *Measured at today's ATR* = the same $0.16 on
  the same R multiples with ATR15 $8, 1h $16, 4h $32 (an assumption: ATR scales with the square root of time). The
  cost audit's $0.70 mean stop overshoot at signals is not in the three nets; it is applied as a stress in 6.2.
  Entry cost counts once per trade (the partial exit adds no second spread in this model).
* **Counts.** About 900 table cells, listed in section 8.

## 3. First-touch behaviour (descriptive; tables are on the full discovery half, real vs control)

### 3.1 Touch classification and magnet test (share of first touches; P(touch) = share of levels touched in the window)

| TF | Level family | Break % real / ctrl | Sweep % | Reject % | P(touch) real / ctrl | Drift 8 bars, break dir, ATR real / ctrl | n touches |
|---|---|---|---|---|---|---|---|
| 15m | prev-day high/low | **51.5 / 44.6** | 6.2 / 6.8 | 35.3 / 37.8 | 46.0 / 47.0 | +0.057 / +0.058 | 1,895 |
| 15m | prev-week high/low | 53.5 / 48.5 | 6.1 / 5.2 | 35.5 / 39.4 | 49.5 / 47.8 | +0.221 / +0.005 | 411 |
| 15m | round $100 | 51.8 / 46.8 | 5.8 / 5.9 | 27.9 / 33.6 | 8.8 / 9.0 | +0.604 / +0.353 | 365 |
| 15m | prev-day close | 39.0 / 39.6 | 11.6 / 11.5 | 29.0 / 28.6 | 92.3 / 91.5 | -0.073 / -0.072 | 1,717 |
| 15m | session opens | 40.0 / 41.2 | 8.2 / 7.7 | 36.0 / 36.6 | 80.9 / 80.4 | -0.017 / -0.020 | 4,851 |
| 1h | prev-day high/low | 47.6 / 43.0 | 8.5 / 8.3 | 33.9 / 36.4 | 45.5 / 46.5 | +0.039 / +0.070 | 1,871 |
| 1h | prev-week high/low | **55.0 / 47.2** | 5.1 / 6.5 | 33.5 / 37.2 | 49.3 / 48.5 | +0.063 / +0.130 | 409 |
| 1h | round $10 | 42.2 / 40.5 | 8.6 / 9.2 | 34.3 / 34.6 | 61.6 / 60.9 | +0.094 / +0.071 | 2,032 |
| 4h | prev-day high/low | 44.5 / 41.6 | 8.5 / 8.2 | 35.3 / 34.9 | 45.1 / 45.7 | +0.088 / +0.036 | 1,841 |
| 4h | prev-week high/low | 48.8 / 47.1 | 8.4 / 7.3 | 33.7 / 33.7 | 49.1 / 47.2 | +0.019 / +0.110 | 406 |
| 4h | Asia high/low | 40.5 / 40.1 | 8.5 / 8.7 | 37.4 / 36.3 | 65.5 / 65.6 | -0.032 / +0.023 | 2,603 |

Reading. The level-vs-control gap is in the **break share** (+3 to +8 points for previous-day and previous-week
extremes), not in sweeps (equal) or in attraction (equal): stops sit beyond extremes and a touch that closes through
tends to go on, a small effect and not a reversal. The 8-bar drift after a break is no larger than for random levels
at the same distance (PDH/PDL 15m +0.057 vs +0.058). Session opens, previous close and the Asia levels behave like
random levels. Previous-month/quarter/year levels have 12-60 touches: too few to read (listed in the CSV only).
Verdict: **no magnet** (all families within 1.5 points of control), **sweep-and-reverse is not special**, **reject
bounce is rarer than random** at the prior-day and prior-week extremes.

## 4. Trades, all level families pooled (2010-2017, first touch, real vs control)

Net columns: P = research prior, M = measured $0.16 on 2010-17 stops, T = measured at today's ATR. Diff CI is the 95%
day-block bootstrap of real minus control gross.

### 4.1 Break-close (BRK), stop 1.0 ATR back through the level

| TF | Exit | n real | Gross R (t) | Control gross | Diff [CI] | Net P | Net M | Net T | Median stop $ (ATR) |
|---|---|---|---|---|---|---|---|---|---|
| 15m | fixed 2R | 10,168 | +0.013 (0.67) | -0.007 | +0.020 [-0.001, +0.040] | -0.305 | -0.058 | +0.001 | 2.50 (1.57) |
| 15m | PT | 10,168 | +0.025 (1.57) | +0.011 | +0.013 [-0.003, +0.029] | -0.294 | -0.046 | +0.013 | |
| 1h | fixed 2R | 9,781 | +0.044 (1.98) | +0.018 | +0.026 [+0.004, +0.051] | -0.105 | +0.011 | +0.038 | 5.08 (1.48) |
| 1h | PT | 9,781 | +0.053 (2.85) | +0.020 | +0.033 [+0.015, +0.052] | -0.097 | +0.019 | +0.046 | |
| 4h | fixed 2R | 8,671 | +0.081 (2.89) | +0.055 | +0.025 [-0.004, +0.057] | +0.005 | +0.063 | +0.077 | 9.66 (1.42) |
| 4h | PT | 8,671 | +0.074 (3.43) | +0.056 | +0.018 [-0.003, +0.039] | -0.002 | +0.057 | +0.071 | |

With stop 0.5 ATR: 15m gross +0.004 / net P -0.485; 1h +0.012 (PT +0.044); 4h +0.068 (PT +0.062): tighter stops cost
more per R and gain nothing. The PT exit adds nothing at 4h and +0.009 at 1h: **no asymmetric-exit rescue**.

### 4.2 Fade of reject/sweep (FADE), stop beyond the touch extreme

| TF | Exit | n | Gross R (t) | Control | Diff [CI] | Net P | Net M | Net T | Median stop $ (ATR) |
|---|---|---|---|---|---|---|---|---|---|
| 15m | fixed | 11,086 | +0.034 (2.01) | +0.028 | +0.006 [-0.021, +0.028] | **-0.817** | -0.153 | +0.001 | 1.17 (0.79) |
| 1h | fixed | 11,536 | -0.020 (-1.16) | -0.003 | -0.016 [-0.038, +0.007] | -0.422 | -0.110 | -0.037 | 2.19 (0.68) |
| 4h | fixed | 10,547 | -0.079 (-3.98) | -0.039 | -0.039 [-0.062, -0.015] | -0.289 | -0.126 | -0.088 | 4.07 (0.62) |

The 15m fade has the H-50 profile: stops of $1.17 (0.79 ATR), research-prior cost 0.62 R per trade, gross +0.034. Fades
lose to the control at 4h with a CI below zero.

### 4.3 Resting-limit retest (RET), stop 1.0 ATR beyond, 2R: the slice E generic-retest baseline

| TF | n | Real gross | Control gross | Diff [CI] | Net P | Net M |
|---|---|---|---|---|---|---|
| 15m | 23,313 | -0.101 | -0.071 | **-0.030 [-0.047, -0.013]** | -0.621 | -0.217 |
| 1h | 23,526 | -0.118 | -0.066 | **-0.052 [-0.072, -0.033]** | -0.351 | -0.170 |
| 4h | 21,252 | -0.102 | -0.047 | **-0.056 [-0.078, -0.032]** | -0.216 | -0.128 |

Both are negative because a limit order filled on the first touch is stopped before the target in my convention
(stop-first on the fill bar, 2R, stops of 1 ATR): the control sets the floor (-0.05 to -0.07), real levels are 0.03 to
0.06 R below it. E's order-block control was positive (+0.03 to +0.05) because its stop sits 1.26 ATR beyond a zone,
not at a fixed ATR; the comparison to take is **real minus control, which is negative for key levels**. Key levels do
not carry the "generic retest" edge slice E saw; if anything price goes through them. With stop 0.5 ATR the same
pattern holds (diff -0.028 to -0.055).

### 4.4 By level family, 1h and 4h break-close (stop 1.0 ATR, fixed 2R; families with n >= 400)

| TF | Family | n | Gross (t) | Control | Diff [CI] | Net P | Net M | Net T |
|---|---|---|---|---|---|---|---|---|
| 1h | prev-day high/low | 890 | +0.080 (1.74) | +0.082 | -0.003 [-0.067, +0.057] | -0.068 | +0.046 | +0.073 |
| 1h | prev NY session high/low | 1,014 | +0.088 (2.06) | +0.096 | -0.008 [-0.064, +0.047] | -0.064 | +0.054 | +0.082 |
| 1h | prev-day close | 592 | +0.047 (0.83) | -0.032 | +0.080 [-0.008, +0.164] | -0.122 | +0.011 | +0.040 |
| 1h | daily open | 668 | +0.097 (1.79) | +0.072 | +0.025 [-0.052, +0.103] | -0.066 | +0.061 | +0.090 |
| 1h | Asia high/low | 1,172 | -0.053 (-1.35) | -0.080 | +0.027 [-0.023, +0.079] | -0.206 | -0.088 | -0.059 |
| 1h | London high/low | 923 | +0.016 (0.38) | -0.030 | +0.046 [-0.014, +0.099] | -0.112 | -0.013 | +0.011 |
| 1h | session opens | 1,700 | -0.020 (-0.61) | -0.001 | -0.019 [-0.064, +0.026] | -0.173 | -0.055 | -0.027 |
| 1h | round $10 | 858 | +0.144 (3.05) | +0.085 | +0.059 [-0.006, +0.129] | -0.002 | +0.112 | +0.138 |
| 4h | prev-day high/low | 820 | +0.047 (0.97) | +0.033 | +0.014 [-0.049, +0.067] | -0.029 | +0.029 | +0.043 |
| 4h | prev-day close | 600 | **+0.147 (2.51)** | +0.043 | **+0.104 [+0.017, +0.180]** | +0.065 | +0.128 | +0.143 |
| 4h | Asia high/low | 1,053 | +0.038 (0.88) | +0.021 | +0.017 [-0.028, +0.076] | -0.035 | +0.021 | +0.035 |
| 4h | session opens | 1,428 | +0.098 (2.56) | +0.075 | +0.024 [-0.028, +0.080] | +0.023 | +0.081 | +0.095 |
| 4h | round $10 | 792 | +0.067 (1.33) | +0.064 | +0.004 [-0.065, +0.070] | -0.007 | +0.051 | +0.064 |

Small families (previous week/month/quarter/year, round $50/$100, week/month opens: n 30-230) give point estimates
of +0.08 to +0.5 R with no CI possible at this n; round $50 is positive for **both** break and fade (+0.17 / +0.20 at 1h,
+0.17 / +0.22 at 4h), which cannot be an effect of the level and is read as noise. Previous-day close (= previous NY
session close, one level counted twice in the pooled rows: PDC and PNC, 600 events each) is the only family whose CI
excludes 0 against the control (4h +0.104), on 75 events a year.

## 5. Stop-size distribution and cost per R (the H-50 lesson)

2010-2017 median ATR15 is $1.05-2.20 (slice E 5.1), so stops are small and cost per R is large.

| TF, rule | Stop $ p10 / median / p90 | Stop in ATR (median) | Median cost, prior R | Median cost, measured $0.16, R |
|---|---|---|---|---|
| 15m BRK 1.0 | 1.31 / 2.50 / 5.99 | 1.57 | 0.30 | 0.06 |
| 15m FADE | 0.43 / 1.17 / 2.99 | 0.79 | 0.62 | 0.14 |
| 15m RET 1.0 | 0.90 / 1.46 / 2.63 | 1.00 | 0.50 | 0.11 |
| 1h BRK 1.0 | 2.98 / 5.08 / 10.36 | 1.48 | 0.14 | 0.03 |
| 1h FADE | 0.92 / 2.19 / 5.37 | 0.68 | 0.33 | 0.07 |
| 4h BRK 1.0 | 6.04 / 9.66 / 18.89 | 1.42 | 0.07 | 0.02 |
| 4h FADE | 1.80 / 4.07 / 9.81 | 0.62 | 0.18 | 0.04 |

Reading. At 4h the research-prior cost is 0.07-0.11 R for a break (a gross of +0.08 nets about 0), at 15m it is 0.30-0.62
R and no level rule at 15m has a gross above +0.04. A rule needs gross above about 0.15 R at 1h and 0.35 R at 15m to
pay under the research prior; the best gross seen in a family of n >= 800 is +0.15 R. Under the measured-fill cost
the 1h and 4h break rules are positive (+0.01 to +0.06) but that is the generic geometry (controls +0.02 to +0.06).

## 6. Conditioning (break-close, stop 1.0 ATR, fixed 2R; real vs control; cells with n < 200 have no CI)

### 6.1 Session at the touch, range used, distance travelled, daily trend

| TF | Condition | n | Gross | Control | Diff [CI] | Net P | Net M |
|---|---|---|---|---|---|---|---|
| 15m | Asia | 4,356 | +0.005 | -0.040 | +0.045 [+0.015, +0.081] | -0.374 | -0.076 |
| 15m | London | 2,587 | -0.002 | -0.005 | +0.003 [-0.038, +0.041] | -0.336 | -0.079 |
| 15m | NY | 3,225 | +0.036 | +0.038 | -0.002 [-0.038, +0.026] | -0.189 | -0.016 |
| **1h** | **Asia** | **3,738** | **+0.119 (t 3.05)** | +0.051 | **+0.067 [+0.017, +0.105]** | -0.047 | **+0.082** |
| 1h | London | 2,851 | -0.013 | +0.009 | -0.022 [-0.063, +0.014] | -0.167 | -0.048 |
| 1h | NY | 3,192 | +0.009 | -0.011 | +0.020 [-0.019, +0.054] | -0.118 | -0.020 |
| **4h** | **Asia** | **3,410** | **+0.125 (t 2.76)** | +0.065 | **+0.059 [+0.011, +0.106]** | **+0.043** | +0.107 |
| 4h | London | 3,756 | +0.043 | +0.045 | -0.002 [-0.039, +0.036] | -0.030 | +0.026 |
| 4h | NY | 1,505 | +0.074 | +0.058 | +0.015 [-0.042, +0.076] | +0.003 | +0.058 |
| 1h | range used < 0.4 / 0.4-0.8 / 0.8-1.2 / > 1.2 | 5,258 / 3,369 / 833 / 211 | +0.050 / +0.057 / -0.047 / +0.032 | +0.035 / +0.022 / -0.074 / -0.050 | +0.015 / +0.035 / +0.027 / +0.082 | | |
| 4h | range used < 0.4 / 0.4-0.8 / 0.8-1.2 / > 1.2 | 2,950 / 2,572 / 588 / 159 | +0.028 / +0.111 / +0.090 / -0.164 | +0.032 / +0.056 / +0.096 / -0.019 | -0.004 / +0.055 / -0.006 / -0.145 (n < 200: no CI) | | |
| 4h | distance travelled < 0.3 / 0.3-0.6 / 0.6-1.0 / > 1.0 | 4,852 / 1,092 / 268 / 57 | +0.050 / +0.110 / +0.131 / -0.128 | +0.038 / +0.072 / +0.075 / +0.085 | +0.012 / +0.038 / +0.056 / -0.213 | | |
| 1h | with daily trend / against | 5,132 / 4,640 | +0.076 (t 2.35) / +0.007 | +0.058 / -0.026 | +0.018 [-0.013, +0.048] / +0.033 [+0.001, +0.068] | -0.074 / -0.142 | +0.042 / -0.027 |
| 4h | with daily trend / against | 4,529 / 4,142 | +0.092 (t 2.28) / +0.068 | +0.087 / +0.023 | +0.006 [-0.029, +0.046] / +0.045 [+0.005, +0.080] | +0.016 / -0.008 | +0.075 / +0.051 |

Reading, against the owner's and the director's priors:
* **Session.** The only sessions where a level break beats a random level are Asia-hours touches (1h +0.067, 4h +0.059,
  15m +0.045, all CIs above 0, same sign in the three timeframes): thin, wide-spread hours where a through-close is a
  genuine move rather than noise; London and NY touches add nothing over random levels. Half of this is the touch
  hour 17:00-24:00 NY (rollover and early Asia), where cost is highest (README 6.5): the research-prior net is
  -0.047 at 1h, positive only at 4h (+0.043).
* **Range used vs the forecast.** A day that has already used more than 1.2 of its forecast range is a worse place to
  take a break at 4h (-0.164 gross, n 159) and 15m (-0.10, n 187), neutral at 1h; a small range so far (< 0.4) is **not**
  better than a medium one (0.4-0.8, +0.111, diff +0.055 [+0.007, +0.103] at 4h). So "range still to expand" selects
  weakly at best, and "range already spent" is a plausible veto. n of the veto cells is small.
* **Distance travelled** shows no monotone pattern (the > 1.0 cell has 57-96 events).
* **Daily trend (the owner's rule).** Breaks **with** the daily trend have higher gross (1h +0.076 vs +0.007 against),
  but the control moves by the same amount (+0.058 vs -0.026): trend agreement is generic momentum, and the extra
  that the level adds is, if anything, larger **against** the trend (+0.033 and +0.045, CI above 0 at the lower end).
  The owner's rule keeps its value as a selector for any entry; it does not make levels better.

### 6.2 Cost stress: the measured $0.70 stop overshoot (cost audit)

Charging $0.70 on every stopped trade (47% of the BRK trades are stopped) on top of the measured cost:

| Cell (BRK 1.0 ATR, PT exit) | n | Net M | Net M with overshoot | Net T with overshoot |
|---|---|---|---|---|
| 1h, Asia touches | 3,738 | +0.062 | **-0.013** | +0.077 |
| 4h, all | 8,671 | +0.057 | +0.022 | +0.064 |
| 4h, Asia touches | 3,410 | +0.089 | +0.053 | +0.096 |

At the 2010-17 stop sizes the overshoot is 0.04-0.08 R per trade and removes the 1h Asia net; at today's ATR it is
smaller than the margin (the "T" column), so the sign of these rules depends on the era and on a stop-market fill
that the cost audit has measured at signals only.

### 6.3 Stability of the two leading cells (PT exit; real, control, nets by year)

| Year | 1h Asia: n / gross / control / net P / net M | 4h prev-day close: n / gross / control / net M |
|---|---|---|
| 2010 | 419 / +0.132 / +0.065 / -0.089 / +0.094 | 68 / +0.221 / +0.062 / +0.203 |
| 2011 | 499 / +0.161 / +0.056 / +0.033 / +0.136 | 82 / +0.175 / +0.083 / +0.162 |
| 2012 | 425 / +0.090 / +0.042 / -0.052 / +0.061 | 74 / +0.032 / -0.134 / +0.016 |
| 2013 | 504 / +0.002 / -0.038 / -0.127 / -0.025 | 75 / +0.299 / +0.135 / +0.284 |
| 2014 | 507 / +0.230 / +0.190 / +0.061 / +0.190 | 75 / +0.200 / +0.057 / +0.180 |
| 2015 | 423 / +0.057 / -0.110 / -0.128 / +0.014 | 71 / +0.071 / +0.051 / +0.050 |
| 2016 | 479 / +0.028 / +0.000 / -0.131 / -0.009 | 69 / -0.009 / -0.049 / -0.026 |
| 2017 | 482 / +0.082 / +0.043 / -0.116 / +0.031 | 86 / +0.031 / +0.044 / +0.005 |

1h Asia: gross positive and above control in 8 of 8 years, net P positive in 1 of 8, net M in 6 of 8. 4h previous-day
close: gross positive 7 of 8, above control 7 of 8, but 6 trades a month and 600 events in the half.

## 7. Candidates (template of README 5; at most one deserves a Q1 slot, and only after an arm)

### G-1  Asia-hours break of a prior-period level, 1h (the best cell; not a trial yet)
- Mechanism: stops cluster just beyond prior-period extremes and round numbers; a through-close in thin hours triggers
  them (Osler 2003, 2005; Kavajecz & Odders-White 2004: support/resistance coincide with order-book depth). Evidence
  grade C (FX order books, gross; gold only by this table).
- Already tried?: new in form (prior-period levels, all families pooled, partial + trail exit); overlaps R-02/R-04
  breakouts in kind, not in level definition. R-04 is gross +0.015 R t 0.38 on rolling range breaks.
- Timeframe and clock: 1h bars; touch bar opens between 17:00 NY and London 08:00 (the session label of this file);
  local NY clocks, DST via zoneinfo.
- Entry: first 1h bar of the trading day that touches a level in {PDH, PDL, PDC, Asia/London/NY session extremes,
  weekly and monthly extremes, $10/$50/$100 round numbers} and closes >= 0.1 ATR1h beyond it; enter at that close.
- Side: both. Exit: stop 1.0 ATR1h back through the level; 50% at 1R, remainder trailing 1.5 ATR1h from entry-stop at
  breakeven, cap 32 bars. One position at a time: yes (the pooled 39 events a month overlap; a rule would take the first
  per day).
- Control: random level at the same distance and window (done): real minus control gross +0.067 [+0.017, +0.105]
  (fixed 2R; PT version in 6.3 table, 8/8 years above control).
- Discovery-half evidence: section 6.1/6.3, n 3,738, gross +0.119 (t 3.05, day-clustered), about 900 cells looked at.
- Cost check: median stop $5.08 (1.48 ATR1h). Net P -0.047 (fails), net M +0.082, net T +0.112, with the $0.70
  overshoot -0.013 / +0.077. Gate-1 needs t near 4.3: **not reachable on this effect** (diff +0.067 R, SD about 1 R).
- Expected events: about 39 a month in 2010-2017 (3,738 / 96), correlated within days; the one-per-day rule gives
  roughly 15-20 a month, about 3,500-4,500 in 2010-2025-09; floor 1,000 met. Needs a recount on rule form before use.
- Kill rule: on the 2018-2025-09 half, real minus control gross CI includes 0, or net M (stop-overshoot included) below 0.
- Read rule (arm): continue only if after 300 admissible trades the mean net R > 0 at the broker cost with the
  overshoot and the control arm does not match it; otherwise stop.
- Route: **exploration arm** on `icm-probe` (intraday pot: the median stop of about $24 at today's ATR1h of about $16 is
  about £18, inside the £45 cap) with a random-level control arm on the same bars; Q1 free-reserve trial only if the
  arm reads continue. Prior P(continue) 5%.

### G-2  4h break-close of the previous day's close
- Mechanism: the daily close is the settlement reference the market re-prices against (COMEX settle 13:30 NY is
  earlier; the 17:00 close is the next-day open). Evidence grade D.
- Evidence: diff +0.104 [+0.017, +0.180], t 2.51, n 600 (the family counted twice as PNC), 7/8 years, 75 events a year.
- Cost: stop $9, net P +0.065, net M +0.128 at 2010-17 stops. Expected events 1,200 over 2010-2025-09, below the
  2,000 intraday floor in practice; kill rule: any of the confirmation-half read fails.
- Route: **feature** (`dist_to_prev_close_atr`, `broke_prev_close`) for H-02/O-4; arm only after G-1. P(continue) 3%.

### G-3  Level-break probability and distance features
- Mechanism and evidence: break share +3 to +8 points over random levels for PDH/PDL and previous-week levels (3.1);
  distance to the nearest of {PDH, PDL, PWH, PWL, session extremes, round numbers} in ATR, class of that level, session
  of the touch, range used vs the 20-day forecast.
- Cost: none (features). Route: **feature group** (H-62 meta-labeller / O-4), about 12 columns, K_eff +1 as a group;
  already partly built (`pdh_dist_atr`, `pdl_dist_atr`, `session_zones` v2 in h62 spec). New: range-used and the level
  class interaction. P(continue) not applicable.

### G-4  Range-used veto for level breaks
- Evidence: after the day has used more than 1.2 of its forecast range, 4h breaks lose (-0.164 gross, n 159; 15m -0.10,
  n 187; 1h neutral); a break at < 0.8 of the range is not worse. Weak, small n.
- Route: **filter inside G-1 / the exploration arms** (log the value; read at the 300th trade). P(continue) 10% as a
  veto, no stand-alone use.

### G-5  Previous-week and previous-month break/continue
- Evidence: break share 53.5 / 48.5 and 55.0 / 47.2 (15m / 1h), gross +0.08-0.12 R, 200-230 events per timeframe over
  eight years (25-30 a year). Too few for any trial (floor 1,000 intraday / 150 daily is met only as pooled events across
  families, not as a family).
- Route: **drop as a rule**, keep in the feature group (G-3). P 0.

### G-6  Fade of rejected levels / retest limit (closed)
- Evidence: RET real minus control -0.030 / -0.052 / -0.056 (CI below 0), FADE 4h diff -0.039 [-0.062, -0.015], 15m FADE
  cost 0.62 R at a gross of +0.034 (H-50 profile). **Closed, 0 trials**: key levels are worse resting points than random
  levels, and a fade does not pay through any exit tried. Re-confirms R-14 and R-13.

### G-7  Round numbers $10/$50/$100 as stand-alone levels
- Evidence: $100 levels are reached on 8.8% of days (the nearest are far), $50 on 16%, both with break shares within
  3-6 points of random; $10 breaks (1h) +0.144 gross vs control +0.085 (diff +0.059 [-0.006, +0.129], net M +0.112, net
  P -0.002). $50 positive for both break and fade: noise.
- Route: **feature** (already in `survey.py`; add the $100 step) and a component of G-1 only. Drop stand-alone.

### G-8  Session OHLC, daily/weekly/monthly opens as reaction levels
- Evidence: session opens and the daily open behave like random levels on break share (40.0 / 41.2, 39.6 / 38.1); 1h
  daily open break +0.097 (diff +0.025, CI includes 0); weekly and monthly opens (n 160-220) +0.08 to +0.14 with no
  difference from control. Route: **drop as rules**; the open price is in the features already. P 0.

Slots for 2027Q1: **none is justified by the discovery half today.** If the director wants one, G-1 after its arm reads
continue; the stated prior is 5%, and under the research-prior cost its net is negative, so a trial could at most read
"continue" at gate 1 only if the level-attributable edge were about three times what was measured.

## 8. Looks behind every number (for K)

| Look | Count |
|---|---|
| Family table: 17 families (+ pooled) x 3 timeframes x 5 rules x 2 exits, cells with n >= 30 real and control | 482 cells |
| Conditioning: 4 rules x 4 conditions (session, range used, distance travelled, daily trend) x 3 TF x 2 exits | 312 cells |
| Touch classification and magnet test: 17 families x 3 TF x (break, sweep, reject, stall, P(touch), drift) | about 300 statistics |
| Yearly stability and the stress test (6.2, 6.3), the ones I chose after seeing section 6 | 6 tables, post hoc |

Structural variants of the strategy: 3 timeframes x 5 rule parameterisations x 2 exits = 30; 30 level groups pooled into
17 families; 6 random controls per level; one data pass, no re-parameterisation. Of the 362 family cells with a
bootstrap CI, **19 have a CI above 0 for real minus control** (about 9 are expected by chance at 2.5% one-sided) and
84 have a CI below 0 (mostly the resting-limit retests). The 19 are not independent: 14 are the same few events seen
through two stops and two exits, and they concentrate in previous-close/daily-open break-close cells (4h previous-day
close, 1h previous NY close, 15m daily open: diff +0.07 to +0.12 [+0.02, +0.18]; the 15m daily-open cell has n 711 and
net M -0.016 / -0.064, so it fails on cost). The best |t| of a break-close cell is 3.05 (1h round $10, gross +0.144) and
3.43 (pooled 4h, PT). Treat every positive cell as a hypothesis, not a result.
The post-hoc choice of G-1 and G-2 (picked after reading section 6) means their CIs are optimistic.

## 9. Limits and what this file did not do

* **Next-level targets** (target = the next key level up/down) were not simulated: the pooled 2R target is what the
  brief calls "at least 2:1"; PT is the second exit. A next-level target needs the levels sorted at each entry; it would
  be a G-1 variant for the arm, not for this file.
* Levels used the first touch inside the window only; second touches (break-and-retest), failed-break reversals after
  entry and trade-through multi-level days are not measured. Sweep subclass of FADE (pierce >= 0.1 ATR) is in the CSV
  only.
* 4h bins are UTC-aligned, not NY-day aligned; the previous-day-close finding at 4h uses the first 4h bin starting after
  17:00 NY.
* Costs on 2010-2017 data over-weight spread; the "T" column rescales only the commission/spread constant, not any change
  in stop-overshoot or in the stops of today's regime. Measured costs come from about a week of probe fills.
* Discovery half only. No 2018+ outcome, no holdout, nothing about the broker beyond `cost-model-1m-2026-10-25.md`.

## 10. Verdicts per topic (README 7.2)

| Topic | Verdict |
|---|---|
| Session OHLC and opens as reaction levels | close (0 trials): like random levels |
| Previous-day high/low, previous-week high/low break/continue | feature (G-3); break share higher, no stand-alone edge over control |
| Previous-day close, 4h | feature (G-2) |
| Previous-month, quarter, year levels | close: 12-60 touches |
| Round numbers $10/$50/$100 | feature; $100 step to add |
| Magnet / sweep-and-reverse / reject bounce | close: no magnet, sweep equal to control, rejects below control |
| Retest-limit of a level | close: worse than random levels (-0.03 to -0.06 R) |
| Level break with partial + trail exit | arm (G-1): 1h Asia hours; 4h generic break is the control |
| Range-used / forecast conditioning | feature and veto (G-4) |
| Daily-trend agreement at levels | already a selector for any entry; adds nothing at the level |

What I would stop doing: 15m level fades (cost 0.3-0.8 R per trade), resting-limit retests of fixed levels, and any
level family under 1,000 events a half.

## 11. Literature (peer-reviewed first)

* Osler, C. (2003), "Currency orders and exchange rate dynamics: an explanation for the predictive success of technical
  analysis", *Journal of Finance* 58(5), 1791-1819. Stop-loss and take-profit orders cluster at round numbers; FX bank
  order data, gross. Cited in the repo (`box-mfi-families-2026-10-18.md`).
* Osler, C. (2005), "Stop-loss orders and price cascades in currency markets", *Journal of International Money and
  Finance* 24(2), 219-241. Trends accelerate when rates reach levels where stop-loss orders cluster. Working-paper
  version verified: https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr150.html ; the earlier report on
  clustering at round numbers: https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr125.html (journal
  citation from the repo documents, not re-checked).
* Osler, C. (2000), "Support for resistance: technical analysis and intraday exchange rates", *FRBNY Economic Policy
  Review* 6(2), 53-68. Published support/resistance levels predict intraday trend interruptions in FX (gross).
* Kavajecz, K. and Odders-White, E. (2004), "Technical analysis and liquidity provision", *Review of Financial Studies*
  17(4), 1043-1071. Support and resistance coincide with peaks in limit-order-book depth. Verified:
  https://ideas.repec.org/a/oup/rfinst/v17y2004i4p1043-1071.html
* Aggarwal, R. and Lucey, B. (2007), "Psychological barriers in gold prices?", *Review of Financial Economics* 16(2),
  217-230. Gold: round numbers ($100 levels) change the conditional mean and variance; used in
  `xauusd-trader-playbook.md` 1.18 (link there).
* Kaminski, K. and Lo, A. (2014), "When do stop-loss rules stop losses?", *Journal of Financial Markets* 18, 234-254:
  stops and trailing exits pay only with serial correlation (cited in `hypotheses.md` row H-17).
* Practitioner (unverified, not relied on): ICT/SMC "previous day high/low, weekly open, Asia range" level teaching and
  floor-pivot conventions; used here only as the list of levels to test.

## 12. Delegation notes for the director

* Nothing in this file needs a trial slot. G-1 needs an arm design (intraday pot, random-level control arm) and a recount
  of the one-trade-per-day form; G-2/G-3 need a feature column each.
* Artefacts outside the repo: `~/tmp/key_levels/*.parquet` and the CSVs (`cells_family.csv`, `cells_cond.csv`,
  `touch_class.csv`, `stop_dist.csv`); both scripts regenerate them in under a minute.
