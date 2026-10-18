# Tight-stop entry study: can a deferred 1m entry with a structural stop fit £5 risk at 0.01 lot? (2026-10-18)

**Status: pre-trial exploratory study. Not a registry trial, no `research.yml` run, no holdout scoring.**
Author: strategy-researcher (independent second opinion; the other expert's study was not consulted).
Script: `scripts/research/tight_stop_entry_study.py` (no data committed; it downloads release `data-v1`).

## Owner question and constraint

No new capital and no higher risk per trade. On the £1,000 IC Markets Raw account, `risk_per_trade` 0.5 % = £5
≈ $6.65 at GBPUSD 1.33, and the minimum lot is 0.01 (1 oz). A trade is refused when its stop is wider than
$6.65 per oz. A 1.5 × ATR(15m) stop at today's ATR(15m) of about $8 is about $12, so it is refused. The question:
if each specialist signal's entry is deferred on 1m bars for a pullback or retest, and the stop goes at the structural
level, (1) what stop distances, fill rates and R outcomes result, net of 0.71 $/oz, (2) what fraction then fits
£5 at 0.01 lot, (3) how do ATR(15m) and the fit rate vary by hour, and (4) does the tighter stop raise or lower
expectancy per trade and per day?

## Answer in one paragraph

**No.** With tighter structural stops, many more trades fit: in the current regime the share of signals that fit
goes from **0.7 %** (1.5 × ATR15) to **64 %** (pullback-reclaim, 60-minute window). But each fitting trade loses more,
not less. Gross R per trade stays about where it was: the signals beat a random-side control by roughly
**+0.05 to +0.07 R whatever the entry**. Cost in R is 0.71 / stop, so it rises in proportion as the stop shrinks.
In the design sample (2024-01 to 2025-09) the pooled net R falls from **−0.11 R** (market entry, 1.5 × ATR15 stop)
to **−0.45 R** (pullback-reclaim) and **−0.46 R** (50 % retrace). About **40 %** of the tight-stop trades are stopped
within 15 minutes, against 7 % for the baseline. At sized £, the loss per day rises from **−£2.65** to **−£14**,
because more trades fit and each one loses more. Holding the stop in today's $3 to $6.65 band, net R is still about
**−0.10 to −0.15 R** (random control about the same). To break even, a signal needs a gross edge of **0.71 / stop**:
0.11 R at a $6.65 stop, 0.18 R at $4 and 0.36 R at $2. That is **two to six times** the edge any of our
signals shows. The binding constraint at £1,000 is **gross edge, not stop width**. A tighter stop changes which
trades pass the size check, but it does not change how much each trade can earn after costs.

## Method (stated simplifications)

| Item | Choice |
|---|---|
| Data | Dukascopy XAUUSD 1m, release `data-v1` 2022-2026 (2022 is warm-up only), loaded with `goldbot.data.release.read_bar_files`; mid prices via `goldbot.data.resample.mid`; higher timeframes via `resample_bars`; ATR via `goldbot.features.technical.atr` (Wilder, 14) |
| Signals | **The specialists' own code**: `spec.candidates_in_context` on `goldbot.research.pipeline.build_decision_frame` (default features plus h1/h4/d1 context; no macro or positioning ctx), then `complete_windows`. Families: session_open, mean_reversion, intraday_momentum and breakout on 15m; breakout, trend, tsmom and asia_drift on 1h. Signal time t0 = decision bar close (`visible_at`) |
| Baseline B | Market entry at the first 1m open at or after t0; stop 1.5 × ATR(signal timeframe); **B15** has stop 1.5 × ATR(15m), the brief's sizing case (B and B15 are identical on 15m families) |
| T1 pullback-reclaim | Within N minutes: price trades ≥ 0.25 × ATR15 beyond the signal close against the side, then a 1m bar closes beyond the far side of the bar that set the pullback extreme. Entry at the next 1m open; **stop = pullback extreme ∓ $0.30** |
| T2 50 % retrace | Limit at the signal bar's midpoint (or its close if better), filled at the first 1m touch within N minutes (at the open if it gapped through); **stop = signal bar's far extreme ∓ $0.30** |
| T3 structural at market | Market entry at t0; **stop = extreme of the last four completed 15m bars ∓ $0.30** (no deferral, fill 100 %) |
| Windows N | 15, 30, 60, 120 min (fixed in advance, all reported; 30 and 60 shown in the main tables) |
| Targets | `_rr`: target = family reward:risk (target_atr / stop_atr: 1.5 session_open, intraday_momentum; 0.67 mean_reversion; 2.0 breakout, trend, tsmom; none for asia_drift) × this trade's stop. `_thesis`: the baseline's target price (the signal's own level) kept, so R rises when the stop is tighter |
| Exits | Barrier only: stop, target, time limit = the family's (max_bars + 1) × timeframe from t0. The trail and scale-out policies of trend and breakout are **not** modelled. A bar that touches both stop and target counts as the stop (this happened in under 1 % of trades); on a limit-fill bar the stop counts and the target does not |
| Costs | **0.71 $/oz round trip** (brief) on every trade; long swap −0.60 $/oz per 22:00 UTC rollover Mon-Fri, Wednesday × 3; short swap 0 (settings priors) |
| Sizing | oz = floor($6.65 / stop) (0.01 lot = 1 oz); refused when the stop is above $6.65; £ = oz × net $ / 1.33 |
| Overlap | One position at a time per family and variant; an unfilled pending order blocks new signals until its window ends |
| Control | The same simulation with a **random side** (seed 0) on the same signal times: separates the entry mechanics from any signal edge |
| Samples | **Design 2024-01-01..2025-09-30** (440 trading days); **check 2023** (258 days). **Holdout 2025-10-01..2026-09-30: no outcomes (R, £) computed**, only fill rates, stop distances, fit fractions and ATR |

**Deviation from the brief, on purpose:** the brief asked for "last 12 months emphasis" and "2026 descriptive", with
parameter choice on data to 2025-12-31. The registered holdout is **2025-10-01..2026-09-30**
(`research.holdout_from/to`), so the last 12 months are almost all holdout. R and £ outcomes in that window would
be holdout scoring by another name, and they would contaminate the one-time holdout read of the same families later.
So the holdout gets outcome-free descriptives only, nothing was tuned on data after 2025-09-30, and the current
regime's economics are **projected** from design-sample trades whose $ stop matches today's (table 5).

## 1. Fill rate, stop distance and R per entry (pooled, eight families)

### Design sample 2024-01..2025-09, signal side

| Entry | Signals | Fill % | Stop $ median | Stop / ATR15 | Fit % of signals | R gross | R net | t (net) | Cost in R (median) | Stopped ≤ 15 min % | Fitting trades | £ per trade | £ per day | Trades per day |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B15 market, 1.5 × ATR15 | 3,086 | 99.7 | 5.13 | 1.50 | 70.5 | +0.06 | −0.11 | −4.3 | 0.14 | 7.1 | 2,174 | −0.54 | −2.65 | 4.9 |
| B market, 1.5 × ATR(sig tf) | 2,607 | 99.7 | 7.63 | 1.50 | 40.4 | +0.07 | −0.05 | −2.2 | 0.09 | 5.2 | 1,053 | −0.61 | −1.46 | 2.4 |
| T1 pullback-reclaim, N 30, rr | 3,771 | 72.0 | 1.60 | 0.50 | 71.0 | +0.03 | −0.46 | −15.0 | 0.44 | 39.6 | 2,678 | −2.13 | −12.97 | 6.1 |
| T1 pullback-reclaim, N 60, rr | 3,745 | 80.8 | 1.60 | 0.50 | 79.6 | +0.04 | −0.45 | −14.9 | 0.44 | 39.8 | 2,979 | −2.09 | −14.16 | 6.8 |
| T1 pullback-reclaim, N 60, thesis | 3,390 | 81.4 | 1.61 | 0.50 | 80.4 | +0.08 | −0.42 | −6.8 | 0.44 | 41.4 | 2,724 | −1.97 | −12.19 | 6.2 |
| T2 50 % retrace, N 30, rr | 3,721 | 75.1 | 1.92 | 0.61 | 72.3 | +0.03 | −0.47 | −12.8 | 0.37 | 34.8 | 2,690 | −2.22 | −13.54 | 6.1 |
| T2 50 % retrace, N 60, rr | 3,690 | 80.7 | 1.99 | 0.64 | 77.2 | +0.03 | −0.46 | −12.9 | 0.36 | 34.1 | 2,847 | −2.14 | −13.86 | 6.5 |
| T2 50 % retrace, N 60, thesis | 3,303 | 80.9 | 1.97 | 0.63 | 77.4 | +0.14 | −0.37 | −6.1 | 0.36 | 37.6 | 2,558 | −1.75 | −10.17 | 5.8 |
| T3 structural stop at market | 3,255 | 99.7 | 3.77 | 1.15 | 74.6 | +0.05 | −0.29 | −9.1 | 0.19 | 18.4 | 2,427 | −1.67 | −9.24 | 5.5 |

### Same, random-side control (design sample)

| Entry | R gross | R net | Stopped ≤ 15 min % | £ per day |
|---|---|---|---|---|
| B15 market, 1.5 × ATR15 | −0.01 | −0.16 | 7.5 | −3.66 |
| B market, 1.5 × ATR(sig tf) | +0.01 | −0.11 | 5.0 | −1.80 |
| T1 N 60 rr | −0.01 | −0.51 | 41.8 | −15.78 |
| T1 N 60 thesis | −0.06 | −0.57 | 43.1 | −16.48 |
| T2 N 60 rr | −0.01 | −0.56 | 36.1 | −17.59 |
| T2 N 60 thesis | +0.06 | −0.50 | 39.0 | −14.15 |
| T3 at market | −0.01 | −0.42 | 22.7 | −13.83 |

The random-side gross R is about zero for every entry rule, so the simulation itself adds no bias. Signal minus
random gross R: B15 +0.07, B +0.06, T1 rr +0.05, T2 rr +0.04, T3 +0.06, T2 thesis +0.08. **The entry rule does not
make the signal's edge bigger in R.** The edge stays at roughly 0.05 R, while the cost in R goes from 0.14 to
0.36-0.44.

### Check sample 2023 (signal side; nothing chosen here)

| Entry | Fill % | Stop $ median | Fit % | R gross | R net | Stopped ≤ 15 min % | £ per day |
|---|---|---|---|---|---|---|---|
| B15 market, 1.5 × ATR15 | 99.8 | 3.06 | 96.6 | +0.06 | −0.20 | 6.4 | −5.34 |
| T1 N 60 rr | 80.6 | 1.06 | 80.5 | +0.09 | −0.63 | 35.6 | −20.75 |
| T2 N 60 rr | 80.6 | 1.31 | 79.8 | +0.01 | −0.69 | 30.7 | −22.02 |
| T3 at market | 99.7 | 2.30 | 90.5 | +0.06 | −0.43 | 17.1 | −14.85 |

Same ordering, and worse, because 2023's ATR was lower: smaller $ stops mean a larger cost in R.

### Per family, design sample (N 60, rr targets)

| Family | Entry | Signals | Fill % | Stop $ median | Fit % | R gross | R net | t | £ per day |
|---|---|---|---|---|---|---|---|---|---|
| session_open/15m | B15 | 174 | 100 | 4.74 | 82 | +0.05 | −0.11 | −1.2 | −0.14 |
| session_open/15m | T1 | 174 | 89 | 1.93 | 88 | −0.06 | −0.47 | −4.7 | −0.70 |
| session_open/15m | T2 | 174 | 81 | 2.17 | 80 | +0.02 | −0.33 | −3.2 | −0.49 |
| mean_reversion/15m | B15 | 366 | 100 | 5.27 | 67 | +0.05 | −0.10 | −2.3 | −0.21 |
| mean_reversion/15m | T1 | 396 | 84 | 2.55 | 80 | −0.00 | −0.33 | −7.2 | −1.02 |
| mean_reversion/15m | T2 | 472 | 97 | 1.37 | 96 | −0.16 | −0.86 | −17.4 | −4.20 |
| intraday_momentum/15m | B15 | 437 | 100 | 6.84 | 47 | −0.07 | −0.18 | −4.3 | −0.48 |
| intraday_momentum/15m | T1 | 437 | 78 | 1.53 | 77 | +0.10 | −0.38 | −5.6 | −1.29 |
| intraday_momentum/15m | T2 | 437 | 88 | 1.52 | 88 | −0.03 | −0.63 | −9.2 | −2.61 |
| breakout/15m | B15 | 438 | 100 | 4.12 | 82 | +0.12 | −0.07 | −1.0 | −0.41 |
| breakout/15m | T1 | 458 | 85 | 1.55 | 84 | +0.08 | −0.43 | −5.7 | −1.82 |
| breakout/15m | T2 | 456 | 78 | 2.19 | 76 | +0.03 | −0.33 | −4.4 | −1.12 |
| breakout/1h | B (1.5 × ATR1h) | 117 | 100 | 9.70 | 12 | +0.08 | −0.01 | −0.1 | −0.03 |
| breakout/1h | T1 | 125 | 86 | 2.05 | 84 | −0.08 | −0.50 | −3.7 | −0.55 |
| trend/1h | B (1.5 × ATR1h) | 159 | 97 | 10.25 | 11 | +0.14 | +0.05 | +0.4 | −0.07 |
| trend/1h | T1 | 386 | 79 | 1.55 | 78 | +0.12 | −0.40 | −4.7 | −1.29 |
| tsmom/1h | B (1.5 × ATR1h) | 484 | 99 | 10.95 | 5 | +0.14 | +0.05 | +0.8 | −0.14 |
| tsmom/1h | T1 | 1,337 | 78 | 1.51 | 77 | −0.00 | −0.54 | −12.3 | −5.79 |
| tsmom/1h | T2 | 1,256 | 79 | 2.25 | 74 | +0.08 | −0.37 | −7.8 | −3.44 |
| asia_drift/1h | B15 | 432 | 100 | 3.82 | 86 | +0.16 | −0.03 | −0.4 | −0.14 |
| asia_drift/1h | T1 | 432 | 82 | 1.28 | 81 | +0.15 | −0.44 | −2.6 | −1.70 |

(T3 and the other windows are in the script's CSVs.) tsmom/1h at its own 1.5 × ATR1h stop shows the family's known small gross edge (+0.14 R here with plain
barriers; the registered result is +0.06 R gross, t 2.4-2.6, net negative). It has a $11
median stop, fits 5 % of signals in the design sample and 0 % today. On tight stops its gross edge **disappears**
(T1 −0.00 R). The tsmom edge is a multi-day drift, and a $1.50 stop is stopped out by noise before the drift can show.

## 2. Fit at £5 and 0.01 lot: design sample vs today (outcome-free holdout descriptives)

| Entry | Fit % of signals 2024-25Q3 | Holdout window: fill % | Holdout: stop $ p25 / median / p75 | Holdout: stop / ATR15 | **Holdout: fit % of signals** |
|---|---|---|---|---|---|
| B15 market, 1.5 × ATR15 | 70.5 | 99.1 | 11.37 / 14.50 / 18.92 | 1.50 | **0.7** |
| B market, 1.5 × ATR(sig tf) | 40.4 | 99.0 | 15.14 / 22.28 / 31.03 | 1.96 | **0.2** |
| T1 N 30 | 71.0 | 72.9 | 2.96 / 4.32 / 6.25 | 0.45 | **56.8** |
| T1 N 60 | 79.6 | 82.2 | 2.93 / 4.25 / 6.19 | 0.44 | **64.4** |
| T2 N 60 | 77.2 | 79.7 | 3.11 / 5.43 / 8.95 | 0.60 | **48.1** |
| T3 at market | 74.6 | 99.1 | 4.82 / 10.92 / 19.63 | 1.14 | **34.2** |

Per family in the holdout window, T1 N 60 fits between 49 % (mean_reversion) and 77 % (session_open). T2 fits 0 % on
breakout/1h and 16 % on trend/1h, because the 1h signal bar is too wide. As a multiple of ATR15, the structural
stops are stable across regimes (T1 is about 0.45 to 0.5 × ATR15 in every sample), so the fit rate is set by the
level of ATR. A stop of 0.45 × ATR15 is the best these rules achieve, and with today's ATR15 of about $8.5 to $11.5
that is still $4 to $5.

## 3. Hour-of-day map (UTC)

| UTC hour | ATR15 median 2024-25Q3 | Fit % at 1.5 × ATR15, 2024-25Q3 | ATR15 median, last 12 months* | ATR15 p25, last 12 months* | Fit % at 1.5 × ATR15, last 12 months* | Fit % at 1.0 × ATR15, last 12 months* | T1 N 60 fit %, 2024-25Q3 | T1 N 60 fit %, holdout | T2 N 60 fit %, holdout |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2.55 | 84 | 9.01 | 6.77 | 1.8 | 24 | 99 | 75 | 62 |
| 1 | 2.75 | 80 | 9.70 | 7.75 | 0.5 | 13 | 97 | 77 | 69 |
| 2 | 2.88 | 78 | 9.84 | 7.82 | 0.5 | 11 | 99 | 79 | 43 |
| 3 | 2.87 | 79 | 9.44 | 7.61 | 0.5 | 12 | 100 | 91 | 30 |
| 4 | 2.69 | 82 | 8.73 | 7.09 | 1.0 | 18 | 100 | 92 | 70 |
| 5 | 2.62 | 84 | 8.46 | 7.02 | 1.0 | 20 | 100 | 88 | 55 |
| 6 | 2.79 | 82 | 8.57 | 7.37 | 0.9 | 17 | 99 | 90 | 61 |
| 7 | 2.90 | 80 | 8.56 | 7.32 | 1.0 | 16 | 100 | 83 | 81 |
| 8 | 3.00 | 80 | 8.54 | 7.33 | 0.5 | 15 | 99 | 82 | 58 |
| 9 | 3.00 | 80 | 8.39 | 7.20 | 0.4 | 17 | 100 | 86 | 63 |
| 10 | 2.96 | 82 | 8.29 | 6.88 | 0.9 | 21 | 100 | 77 | 64 |
| 11 | 2.99 | 83 | 8.21 | 6.65 | 1.8 | 25 | 100 | 79 | 61 |
| 12 | 3.25 | 79 | 8.57 | 6.75 | 2.1 | 23 | 96 | 83 | 62 |
| 13 | 3.84 | 67 | 9.67 | 7.58 | 0.2 | 12 | 97 | 63 | 51 |
| 14 | 4.41 | 51 | 10.84 | 8.66 | 0.0 | 6 | 99 | 60 | 45 |
| 15 | 4.66 | 44 | 11.57 | 9.36 | 0.0 | 3 | 92 | 62 | 38 |
| 16 | 4.49 | 48 | 11.33 | 9.44 | 0.2 | 3 | 99 | 80 | 60 |
| 17 | 4.20 | 57 | 10.73 | 8.97 | 0.5 | 3 | 100 | 82 | 76 |
| 18 | 3.87 | 69 | 10.00 | 8.37 | 0.6 | 5 | 100 | 75 | 38 |
| 19 | 3.57 | 76 | 9.32 | 7.93 | 0.3 | 10 | 93 | 73 | 55 |
| 20 | 3.20 | 81 | 8.83 | 7.33 | 0.0 | 15 | 99 | 80 | 55 |
| 21 | 2.66 | 98 | 9.14 | 7.00 | 0.0 | 18 | 100 | 80 | 50 |
| 22 | 3.07 | 77 | 7.60 | 6.46 | 0.0 | 28 | 94 | 67 | 85 |
| 23 | 2.64 | 85 | 8.10 | 6.50 | 1.3 | 28 | 100 | 68 | 59 |

\* 2025-10-18..2026-10-05 (the end of the data); ATR only, no trade outcomes. Bars are labelled by their open time
in UTC. In the last 12 months, **no hour has a median ATR15 below $7.60**, so a 1.5 × ATR15 stop fits under 2.1 % of
bars at every hour. Even a 1.0 × ATR15 stop fits under 30 %. The calmest hours are 22:00 to 00:00 UTC and 10:00 to
12:00 UTC, and the most volatile are 14:00 to 17:00 UTC (the New York open and the London/New York overlap). The
rollover hours 21:00 and 22:00 also have the widest spreads, which the flat 0.71 $/oz cost does not capture. No
session makes the baseline fit. Hour of day explains less variation in fit than the overall ATR level, which has
risen about 3× since 2024-25.

## 4. Do tighter stops raise or lower expectancy?

**They lower it, per trade and per day, in both samples, for every family and entry rule except a few small, noisy
cells.**

Stop width, measured in ATR15, against R (design sample, all filled signal trades across entry rules and N 30/60;
the rows overlap between variants, so n overstates the independent count):

| Stop / ATR15 | n | Stop $ median | R gross (signal) | R net (signal) | R net (random) | Stopped ≤ 15 min (signal) |
|---|---|---|---|---|---|---|
| ≤ 0.25 | 2,284 | 0.71 | +0.09 | −1.05 | −1.19 | 62 % |
| 0.25-0.5 | 7,814 | 1.36 | +0.05 | −0.53 | −0.62 | 46 % |
| 0.5-0.75 | 6,910 | 1.87 | +0.12 | −0.30 | −0.43 | 36 % |
| 0.75-1.0 | 3,708 | 2.60 | +0.07 | −0.25 | −0.27 | 25 % |
| 1.0-1.5 | 4,991 | 4.43 | −0.01 | −0.20 | −0.24 | 13 % |
| 1.5-2.0 | 3,106 | 5.28 | +0.08 | −0.07 | −0.13 | 8 % |
| > 2.0 | 2,075 | 9.90 | +0.07 | −0.02 | −0.05 | 2 % |

Gross R is flat in stop width, at about +0.05 to +0.1 R in every bucket. Noise stop-outs therefore do not destroy R
in R units: a barrier on a random walk is fair at any width. The whole decline comes from cost, which is about
0.71 / stop$. Tight stops "hit by noise" lose because each noise hit costs the full 0.71 $/oz against a much
smaller R, not because noise lowers the hit rate disproportionately.

**Projection to today's regime (table 5).** Design-sample trades whose $ stop falls in the band today's tight stops
occupy (T1 median $4.25):

| Entry | Stop $ band | n | R gross (signal) | R net (signal) | R net (random) |
|---|---|---|---|---|---|
| B15 | 3.0-4.5 | 882 | +0.02 | −0.18 | −0.18 |
| B15 | 4.5-6.65 | 955 | +0.04 | −0.10 | −0.14 |
| T1 N 60 rr | 3.0-4.5 | 303 | +0.09 | −0.12 | −0.09 |
| T1 N 60 rr | 4.5-6.65 | 101 | +0.02 | −0.12 | −0.22 |
| T2 N 60 rr | 3.0-4.5 | 470 | +0.05 | −0.15 | −0.24 |
| T2 N 60 rr | 4.5-6.65 | 240 | +0.01 | −0.13 | −0.13 |
| T3 | 3.0-4.5 | 524 | +0.08 | −0.12 | −0.17 |
| T3 | 4.5-6.65 | 572 | −0.05 | −0.19 | −0.23 |

In the current regime a tight-stop book would therefore expect about **−0.12 R per fitting trade**, indistinguishable
from random entry. At 1 oz with a $4.25 stop (about £3.20 at risk), that is about **−£0.40 per trade**. With about
1,874 eligible signals in 218 days and 64 % fitting, that is about 5.5 trades and **−£2 a day**. Not trading costs
£0. This is a projection from the design sample, not a holdout measurement.

**Break-even edge needed, gross R = 0.71 / stop:**

| Stop $ | 6.65 (the £5 cap) | 5.00 | 4.25 (T1 median today) | 3.00 | 2.00 | 1.50 (T1 median 2024-25) |
|---|---|---|---|---|---|---|
| Gross R needed | 0.107 | 0.142 | 0.167 | 0.237 | 0.355 | 0.473 |

Observed signal edge over random: +0.04 to +0.08 R. Even at the largest stop the account allows ($6.65), a signal
needs about 0.11 R gross, roughly twice the best we have measured on any 15m or 1h family. This holds at any risk
level: with no gross edge, raising the risk per trade (an owner decision, which is not recommended here) would only
scale up the loss.

## Verdict and implications for the research agenda

1. **Do not adopt deferred or structural tight-stop entries to make signals fit £5.** They pass the size check
   without adding profit: per trade net R falls from about −0.1 to between −0.3 and −0.9, and the daily £ loss grows about 5×
   because more trades are taken. This applies to every specialist and to the random control alike.
2. **The binding constraint at £1,000 is gross edge per trade in $, not stop width.** A tradeable 15m or 1h
   hypothesis at this account needs gross ≥ 0.71 / stop$, about 0.11 R at the $6.65 cap and 0.17 R at a $4 stop,
   *before* the deflated-Sharpe bar. Write this into every 15m pre-registration as the expected-move-versus-cost gate
   (it agrees with the `min_target_over_cost` 2.5 rule: a target of 2.5 × $0.71 = $1.78 is the floor, and an edge of
   0.11 R or more on a $6.65 stop is the real bar).
3. **Correct reading of the shadow book:** with today's ATR, every 15m and 1h signal at its designed stop is
   refused (fit below 1 %). That is correct behaviour, not lost profit: in the design sample those trades were net
   negative too (B15 −0.11 R, B −0.05 R).
4. **What could change this:** (a) a signal with gross ≥ 0.2 R on ≥ 1,000 events (the H-14 15m discovery trial is
   the place to look; any survivor's entry mechanic can then be layered on, and should then be compared against
   this study's T1/T2/T3 as pre-registered variants); (b) a lower cost (a measured ICM round trip below 0.71 $/oz
   moves the break-even table in proportion: at 0.40 $/oz, a $4 stop needs 0.10 R); (c) a fall in gold volatility
   back to ATR15 of about $3, which brings the 1.5 × ATR15 baseline back to 70 % fit; even then it was −0.11 R net
   in 2024-25.
5. **Multiple-testing ledger:** this study looked at 19 entry variants (B, B15, T3, and T1 and T2 × rr/thesis ×
   4 windows) across 8 families on 2023-2025Q3 outcomes. It is not a registry trial and adds nothing to K now. If any
   entry mechanic from it is later pre-registered, count these looks as **K_eff +19** for that trial (recommendation
   to the research director for `hypotheses.md` §C). Nothing here was tuned: every variant and window was fixed
   before the run, and all are reported.
6. **Retire, with reason, so it is not re-tested without new evidence:** "Fit £5 risk with a tight structural stop
   on the existing specialists' signals (deferred pullback-reclaim, 50 % retrace limit, 15m swing stop)": gross R is
   unchanged against the random control, and the cost in R grows as 0.71 / stop. Proposed row for `hypotheses.md` §B
   (research-director to accept; not edited here to avoid a conflict with the parallel study).

## Limitations

- Dukascopy mid prices with a flat 0.71 $/oz cost. ICM spreads widen at the 21:00 to 22:00 UTC rollover and around
  news, which makes tight stops look *better* here than they would be. A stop $0.30 beyond a level on mid is about
  $0.20 to $0.25 beyond it on the bid or ask. Slippage on stop orders in fast markets is not modelled beyond the
  flat cost, also in tight stops' favour.
- Barrier exits only. The trend and breakout trails and the scale-out are not modelled. They change the baseline's
  absolute numbers but not the cost-in-R mechanism.
- 1m resolution: under 1 % of trades had a stop and a target inside one 1m bar (counted as the stop). Path inside a
  minute is unknown, and with $0.7 stops the 1m bar range is itself comparable to the stop.
- Signals were built without the macro and positioning ctx tables. This affects no default specialist trigger used
  here (tsmom's macro gate is an optional variant).
- Pooled rows mix families whose trades overlap in time, so the pooled t is illustrative. The per-family rows are
  one position at a time.
- £ per day is summed per family over trading days (Mon-Fri with data). Families can hold positions at the same
  time, which a single account's daily loss limit would cap.

## Reproduce

```
gh release download data-v1 -p 'xauusd_1m_dukascopy_202[2-6].parquet' -D data/dl_tight
uv run python scripts/research/tight_stop_entry_study.py --data data/dl_tight --out ~/tmp/tight_stop
```

The run writes `signals.parquet`, `trades_{signal,random}.parquet`, `summary_*_{check_2023,design_2024_25Q3,holdout_descriptive}.csv`
(no outcome columns for the holdout), `by_width_*.csv`, `fit_by_hour_*.csv` and `atr15_by_hour_{design,last12m}.csv`.
Runtime is about 10 minutes for the first run (feature frames) and under a minute with the cached signals.
