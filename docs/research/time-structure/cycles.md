# Slice B: cycles, calendar and event time (30/60/90-day windows, turn of month, weekday, expiry, FOMC/NFP/CPI, seasonality)

Staff research lead, 2026-10-26, for the research director. Frame: `README.md` in this folder. No trial registered,
no backtest job, no registry row. Script (reusable, read-only): `docs/research/candidates/time_structure/time_structure_screen.py`
(`uv run python docs/research/candidates/time_structure/time_structure_screen.py --bars-dir <data-v1 dir> --calendar <us_calendar.parquet>`,
default window = discovery half). Data: release `data-v1` 1m mid (2010-2017 read for outcomes), `calendar-v1`
`us_calendar.parquet` (scheduled rows only; `event_utc` is the announcement, windows are measured from it).

## 0. Disclosure (README 4.1) and what this file is worth

**I looked at 2018-2025-09 outcomes before the frame reached me.** My first run read the whole 2010-01..2025-09-30
window (the 2025-10+ holdout was never read). After the frame arrived I re-ran on 2010-2017 only; every table below is
the discovery half. But I saw the full-sample numbers, so every hypothesis here is **charged as a full trial with no
confirmation credit**, and the confirmation half cannot be used as an untouched read for these effects. What I saw,
stated plainly so the director can weigh it: Friday stayed positive (+7.6 bp/day in 2018-25, +11.0 over all 16 years,
11 of 16 years); the post-FOMC next-day fall did **not** persist (2016-2025-09 +10 bp, wrong sign); NFP pre-24h
weakness vanished after 2015; pre-FOMC drift was absent in both halves; the 90-day-window momentum t of 1.9 in the
full sample is 0.4 in the discovery half. Nothing below was chosen because of the confirmation half, but I cannot
prove it did not shape which rows I wrote up.

Looks behind the tables: about 200 statistics (A ~80, B ~45, C 9 cells, D ~75 event windows, E 8, F 24). A lone
|t| of 2-3 is expected by chance; Bonferroni 5% is about t 3.5. Only the hour-of-day rows for 02 and 05 UTC (t 4.2)
clear it, and they are the H-04 Asia drift that already failed on net (#44).

## 1. Already tried (checked first)

`hypotheses.md` R-08 (pre-FOMC drift, weekday/weekend retired from the literature, never trialled), R-06/R-10 (tsmom
1h/4h and re-scoring), H-01 (#48 slow TSMOM 1d, inconclusive, gross +0.166 R, 294 events), H-04 (#44 Asia drift,
net -0.093 R), SW-6 in `candidates/swing-2026-10-17.md` (turn of month, month, Lunar New Year and Indian festival
windows: proposed, nothing run, festival table missing), `opportunity-days.md` 1.1/1.4 (event days widen range;
weekday direction "fragile"), `macro-pit-audit-2026-10.md` (event counts; pre-FOMC retired). README 6.6 and 6.7 (the
director's own calendar and 30/60/90 tables) are **not redone**; my numbers below are an independent re-measurement
with randomised controls and agree with them. New relative to those: randomised-date controls, event-clock windows on
1m bars, the incremental-over-volatility test (B-2), and the festival table (Diwali, Chinese New Year: dates typed
from the published calendars, **unverified, the data engineer should freeze and checksum them**).

## 2. Measured tables (discovery half 2010-01..2017-12, daily close = 17:00 New York roll)

Costs for reading: price 1,300 in this half, so 10 bp = $1.3/oz. Research-prior round trip about $0.75/oz
(spread ~$0.34 + $0.37, about 6 bp); current broker $0.144/oz (central), 0.4 bp at today's price near 3,860.

### 2.1 Rolling windows: 30/60/90 days (forward return from the next close; Newey-West lag 2h; eff n = N/h)

| L d | h | eff n | TSMOM sign(L-ret) x fwd, bp | NW t | fwd after up / down, bp | after z<-1 (n) |
|---|---|---|---|---|---|---|
| 30 | 5 | 407 | +3.5 | 0.37 | +7.3 / +0.3 | +18.6 (87) |
| 60 | 5 | 401 | +0.9 | 0.09 | +4.4 / +3.0 | +27.8 (62) |
| 90 | 5 | 395 | +3.8 | 0.38 | +4.8 / -2.7 | +69.0 (56) |
| 30 | 20 | 101 | +21.8 | 0.66 | +34.5 / -9.2 | +158.5 (87) |
| 60 | 20 | 99 | -4.0 | -0.11 | +6.6 / +15.7 | +227.3 (62) |
| 90 | 20 | 98 | +4.8 | 0.13 | +8.6 / -0.4 | +251.7 (56) |
| 120 | 20 | 96 | +42.0 | 1.20 | +42.9 / -40.9 | +329.0 (53) |

Unconditional drift 20 d: +12 bp. Reading: **no momentum or reversal at 30/60/90 days** (all |t| < 1.2). The "after
oversold" column looks large but its 56-93 days collapse to 6 (60/90 d) and 16 (30 d) independent 20-day episodes
(t 0.15-1.46; the 60/90 d means flip sign between the two halves of the window, +357/-162 and +256/-198 bp): it is
the 2010-2017 trend, not a cycle. Range position (quintiles, 20 d forward): Q4-Q0 -15, -11, +8 bp, t < 0.5. A new
30/60/90-day closing high: forward 20 d +71/+33/+66 bp on 28/20/14 episodes, NW t 0.9/0.3/0.5. Long-if-up on the
120-day sign earns +1.35 bp/day vs +0.83 always long, 10 flips a year; the 30/60/90 versions earn less than always
long. Consistent with README 6.7 (variance ratio 0.85-1.00) and with #48.

Volatility regime (expanding point-in-time percentile, 250-day burn-in), next-5-day |return| by terciles of realised
volatility over the window: 30 d 154/157/213 bp; 60 d 149/171/210; 90 d 149/165/214 (low/mid/high). rv30/rv90 > 1.25
(n 133): 224 bp; < 0.8 (n 295): 199 bp; all: 169 bp. **Volatility is the only thing the windows predict** (the director's
early read holds). Direction after high volatility: 5-day mean -25..+7 bp, |t| < 1.

### 2.2 Weekday, month, turn of month, month and quarter end

| Effect | n | Mean bp/day | Control | Result | Years positive |
|---|---|---|---|---|---|
| Friday (Thu 17:00 -> Fri 17:00 NY) | 410 | +14.1 (t 2.72) | Fri minus other days +16.6; within-year label permutation p 0.003 | not NFP-only: ex-NFP Fridays +11.8 (n 316), NFP Fridays +21.8 (n 94) | 7/8 years Fri > others; both halves +14.2 / +14.1 |
| Mon / Tue / Wed / Thu | ~413 | -2.7 / +0.4 / -5.7 / -1.9 | | all |t| < 1.2 | |
| August | 178 d | +17.7 (t 2.41) | | 7/8 years > 0 share 0.88; second half +5 | |
| Jan / Feb | 168 / 162 d | +14.0 / +9.6 (t 1.9 / 1.2) | | first 4 years +2, last 4 years +25 | |
| Q4 (Oct-Dec) | | -5.7 vs +3.0 rest | | Baur's autumn effect not present 2010-17 | |
| Turn of month -1..+3 | 95 windows | +7.0 vs random 4-day blocks +0.5 | p 0.165 | eff n 95 | 0.50 |
| Last 3 days of month / quarter end -3..+1 | 95 / 31 | +0.4 / -6.2 | p 0.99 / 0.41 | nothing | 0.50 / 0.25 |
| First trading day of year | 7 | +94 | | n = 7, noise | |
| First trading day of month | 96 | +16.6 (t 1.4); ex-January +8.1, p 0.52 | | nothing | |

Friday by hour (UTC blocks, mean bp per hour): the Friday excess is spread over the session, largest at 17-20 UTC
(+2.1 vs about -0.3 on other days) and Monday 21-23 UTC (+2.2). It is not a single clock event, and no
single-event decomposition explains it.

### 2.3 COMEX expiry dates (rule-derived; approximation)

Dates from the CME rule set and `cme_metals_holidays`: first notice day = last business day before the delivery month
(Feb/Apr/Jun/Aug/Oct/Dec), last trading day = 3rd last business day of the delivery month, standard monthly option
expiry = 4th last business day (README 6.6 uses the same proxy). **Not checked against the CME calendar; expiry weeks
are not in the store.**

| Date | d-1 | d | d+1 | n |
|---|---|---|---|---|
| FND | -8.9 | +4.4 | +21.0 (p 0.16) | 48 |
| Last trading day | **+32.7 (p 0.028)** | -20.1 | +6.3 | 48 |
| Monthly option expiry | **+23.3 (p 0.028)** | +13.1 | -16.2 (p 0.09) | 96 |

Mean |return| on those days is 57-70 bp against 72 for all days (p of higher |return| > 0.5 everywhere): **expiry
does not raise range** in this half. The directional rows are 3 of 9 cells at p < 0.05 with the sign reversing on the
next day (+23 then -16): the profile of noise, not a pinning or roll effect. README 6.6 found OG expiry +22.3 (t 2.39).
Closed here as a candidate (section 5, B-5: feature only).

### 2.4 Scheduled US events on 1m bars (announcement-relative windows, bp; control = same New York clock time and weekday on days with no tier-1 event within a day, 3,000 draws)

| Event (n) | Window | Mean bp | t | Control mean | p | Mean abs bp vs control abs |
|---|---|---|---|---|---|---|
| FOMC statement (64) | 24 h before | +4.0 | 0.41 | -0.3 | 0.56 | 60 vs 72 |
| | 60 min before | +2.9 | 1.20 | -1.9 | 0.13 | 12 vs 14 |
| | 30 min after | +10.8 | 1.26 | -0.1 | 0.000 | 52 vs 10 |
| | 24 h after | -28.4 | -1.11 | -0.6 | 0.008 | 160 vs 71 |
| NFP (97) | 24 h before | -20.7 | -2.11 | +2.9 | 0.035 | 69 vs 68 |
| | 30 min / 4 h after | +9.9 / +17.1 | 1.33 / 1.61 | -1.7 / -0.6 | 0.000 / 0.006 | 56 vs 17 / 83 vs 44 |
| CPI (96) | 24 h before / 30 min after / 4 h after | +1.4 / -3.1 / -3.1 | 0.14 / -1.00 / -0.58 | | 0.96 / 0.43 / 0.87 | |
| PCE (95), ISM mfg (96) | all windows | | |t| < 1.7 | | |

(The small control p on "30 min after" and the event-day absolute moves only restate that announcements raise the
move.) **Pre-FOMC drift: absent** (24 h before +4.0 bp vs control -0.3, p 0.56; 60 min before +2.9, p 0.13; both
sub-periods insignificant). This confirms R-08 on our own data; Lucca and Moench's effect was for equities and
decayed (section 4). The post-FOMC-day -28 bp (control p 0.008, but the control for the 24 h window is a noisy
random-day mean, and the effect is about 0.4 sd of that day's move) matches README 6.6's -39.7 (t -2.19) and fell
in both halves of 2010-2017 (-35, -22). I have seen it fail out of sample (section 0). The FOMC-cycle even/odd week
effect (Cieslak et al.) is absent (README 6.6).

**Range, not direction (incremental over volatility).** Regression of log |next-day return| on log RV20 (known
before the day) in 2010-2017, 2,047 days, HC1 t: RV20 coefficient 0.65 (t 9.0, R-squared 0.0347). Adding RV5 changes
nothing (R-squared 0.0347). Adding event-day dummies: NFP +0.37 (t 2.7), FOMC statement +0.40 (t 3.0), CPI +0.07 (0.6),
PCE 0.00, Friday -0.07, Monday -0.13 (t -1.9), R-squared 0.0443. NFP and FOMC days have about 1.45-1.49 times the
size of move, on top of what volatility persistence already predicts. This is the only piece of time structure here
that adds a selector the vol-persistence feature does not have, and it is knowable days in advance (`calendar-v1`
`available_utc` is the schedule vintage, so it is point in time).

### 2.5 Festival windows (trading-day windows; control = same-length window at random start, one per festival)

| Festival (eff n = 8 each) | Window | Mean bp | t | Random-window mean | p |
|---|---|---|---|---|---|
| Diwali | d-15..d-1 | +65 | 0.52 | +9 | 0.69 |
| Diwali | d..d+10 | -205 | -1.72 | +8 | 0.068 |
| Chinese New Year | d-15..d-1 | +92 | 0.58 | +8 | 0.54 |
| Chinese New Year | d..d+10 | +188 | 1.76 | +4 | 0.13 |

Eight festivals per window: the standard error is about 110 bp, so any effect under about 250 bp is undetectable
(eff n and power, not a negative finding). Nothing is significant; the signs are the opposite of the "stocking before
the festival" story for Diwali. Closed for trading; not worth a trial.

### 2.6 Hour of day (UTC hour of bar close, 1h returns, 2010-2017)

Positive: 02 UTC +1.39 bp (t 4.2, 7/8 years), 05 UTC +1.38 (t 4.2, 7/8), 22 UTC +1.07 (t 2.4). Negative: 07-10 UTC
-0.7..-1.3 (09 UTC t -3.5). This is the Asia-up, London-down pattern of README 6.1 (slice A); it is the H-04 thesis
that stopped on net at -0.093 R; at 1 bp an hour the research-prior cost (6 bp) kills a one-hour trade, and the
London-AM leg is shorter than the cost. Not repeated as a candidate here.

## 3. What the numbers say, per topic (one line each)

| Topic | Verdict |
|---|---|
| 30/60/90-day momentum | close: no edge (all |t| < 1.2, eff n 98-407), agrees with #48 and README 6.7 |
| 30/60/90-day mean reversion / oversold | close: 6-16 independent episodes, sign flips by half |
| Position in the 30/60/90-day range, new highs | close at 0 trials (t < 1) |
| Volatility regime by window | feature (B-3): the only predictable thing |
| Turn of month, month end, quarter end | feature flag only (p 0.17-0.99) |
| Day of week | Friday: the one slot candidate (B-1); other days close |
| COMEX expiry (OG, FND, LTD) | feature/drop (B-5): no range effect, directional rows are reversing noise |
| FOMC / NFP / CPI / PCE direction | close: pre-FOMC drift absent; NFP and post-FOMC day failed out of sample (seen) |
| FOMC / NFP range | feature (B-2): +0.37..0.40 log points over RV20, t 2.7-3.0 |
| Seasonality, Diwali, Chinese New Year, Q4/Jan | close: eff n 8, no detectable effect, Q4 negative not positive |
| Hour of day | defer to slice A |

## 4. Literature (peer-reviewed first; *(unverified)* = cited from memory or a secondary source, check before relying)

- Moskowitz, Ooi, Pedersen 2012, "Time series momentum", *Journal of Financial Economics* 104(2) 228-250, 58 futures
  incl. gold, 1-12 month look-backs, gross. Hurst, Ooi, Pedersen 2017, "A century of evidence on trend-following
  investing", *Journal of Portfolio Management* 44(1) *(unverified volume)*. Support is a diversified portfolio, not a
  single instrument; our single-gold read is #48.
- Lo and MacKinlay 1988, "Stock market prices do not follow random walks", *Review of Financial Studies* 1(1) 41-66
  (variance ratios, as in README 6.7).
- Lucca and Moench 2015, "The pre-FOMC announcement drift", *Journal of Finance* 70(1) 329-371 (S&P 500 equities, about
  49 bp in the 24 h before FOMC, 1994-2011). Kurov, Sancetta, Strasser, Wolfe 2019, "Price drift before U.S.
  macroeconomic news: private information about public announcements?", *Journal of Financial and Quantitative
  Analysis* 54(1) *(unverified)*. Cieslak, Morse, Vissing-Jorgensen 2019, "Stock returns over the FOMC cycle",
  *Journal of Finance* 74(5) 2201-2248 (even-week effect, equities).
- Andersen, Bollerslev, Diebold, Vega 2003, "Micro effects of macro announcements", *American Economic Review* 93(1)
  38-62 (announcements raise volatility and jumps in FX). Corsi 2009, "A simple approximate long-memory model of
  realized volatility", *Journal of Financial Econometrics* 7(2) 174-196 (HAR, for the volatility baseline).
- Ariel 1987, "A monthly effect in stock returns", *Journal of Financial Economics* 18(1) 161-174; Lakonishok and
  Smidt 1988, "Are seasonal anomalies real? A ninety-year perspective", *Review of Financial Studies* 1(4) 403-425
  (turn of month, equities). Etula, Rinne, Suominen, Vaittinen 2020, "Dash for cash: month-end liquidity needs and the
  predictability of stock returns", *Review of Asset Pricing Studies* 10(1) *(unverified)* (equities).
- Gold calendar and seasonality: Baur 2013, "The autumn effect of gold", *Research in International Business and
  Finance* 27(1) 1-11 (1980-2010 gross; reversed to a winter effect in Potrykus and Augustynowicz 2024, *(unverified)*,
  cited in `candidates/swing-2026-10-17.md`); Lee et al. 2016, *Banks and Bank Systems* (Friday positive, Tuesday
  negative across Tokyo/London/New York) and Yu and Shih 2011, *IMFI* (Thursday), both in `opportunity-days.md` 1.4;
  Rosch, Schmidbauer, Jiang 2013, EcoMod working paper (Lunar New Year, preliminary, gross). World Gold Council data
  on Indian wedding-season and Diwali demand: descriptive, no return test *(unverified)*.
- Multiple testing and decay: Harvey, Liu, Zhu 2016, "... and the cross-section of expected returns", *Review of
  Financial Studies* 29(1) 5-68 (t above 3 for new factors); McLean and Pontiff 2016, "Does academic research destroy
  stock return predictability?", *Journal of Finance* 71(1) 5-32 (about half of the effect lost after publication).
  Both support reading a lone t of 2.7 with a 50% haircut.

The only gold-specific papers support a Friday effect (Lee et al.) and a decaying autumn/winter month effect; all the
event-drift and turn-of-month work is for equities, so its transfer to gold is a hypothesis, not a result.

## 5. Candidate hypotheses (README template; ranked)

**Ranking.** At most one candidate is worth a 2027Q1 free-reserve slot (B-1), and it only as a conditional,
retire-only daily read. B-2 and B-3 are features for the opportunity-day work and cost 0 trials. B-4 and B-5 are
recommended "drop"; they are written down so the director can see what was closed and why.

### B-1  Friday-session long (Thursday 17:00 -> Friday 17:00 New York)
- Mechanism: no clean mechanism; candidates are short covering into the weekend and pre-weekend risk-off bid for gold
  (Lee et al. 2016 find Friday positive in Tokyo, London and New York gold). *Evidence grade: C/D.*
- Already tried?: R-08 retired "weekday/weekend effects" from the literature; never measured on our data. New: measured
  here with a within-year label permutation; replaces a literature retirement with a data result that did not fail.
- Timeframe and clock: 1d decision, 4h/1h execution. Entry at the first liquid bar after the 17:00 New York rollover
  (spread is worst at 22 UTC, cost model: use 23:00 UTC). Exit at 16:30 New York Friday. DST handled by New York clock.
  Check the broker rollover really is 17:00 New York (server midnight, Athens) so that no swap is charged.
- Entry: market buy at the entry bar close if the day is a normal Thursday-Friday (not a CME holiday, per
  `cme_holidays`); no filter. Side: long only (shorts have no support: "after down" rows are flat).
- Exit: stop 1.5 ATR(1d), time exit Friday 16:30 New York, no target. One position at a time: yes.
- Control: weekday-label permutation within year (done: p 0.003) plus a random-day long of the same length at the same clock
  (to run).
- Discovery-half evidence: 410 days, +14.1 bp/day, t 2.72 (ex-NFP +11.8, n 316), 7 of 8 years, looks about 45
  (B) so Bonferroni-adjusted p is about 0.13: **not significant after multiple looks**. Table 2.2.
- Cost check: gross 14 bp = $1.8/oz at 1,300 (0.06 R at a 1.5 ATR(1d) stop of about $30); research-prior cost 6 bp, so
  gross is 2.4x cost and net about +8 bp = +0.035 R; at the current broker $0.144/oz the cost is under 4% of the gross.
  Cost is not the issue; the t statistic is.
- Expected events: 52 a year, about 800 for 2010-2025-09, about 4.3 a month; below the 1,000 intraday floor, above the
  150 daily floor.
- Kill rule: close at 0 trials if the day-clustered t on the confirmation read is under 2.0 or Friday-minus-other-days
  is under +6 bp (the cost-covering size), or if 2025 and later Fridays are negative. **I have seen the confirmation half
  (section 0), so it cannot serve as that read: use a forward shadow of 26 Fridays and the holdout as the untouched data.**
- Read rule: continue if forward 26-Friday mean is above +6 bp with t above 1.3 and a 4-year sub-period never below
  -5 bp; stop below 0; inconclusive otherwise.
- Route: exploration arm on `icm-probe` at 0.01 lot first (a 1d stop of ~$130 at today's ATR breaches the swing per-trade
  cap of £40, ADR 0043: use a 4h-ATR stop and accept a wider overnight risk, or skip the arm); Q1 free-reserve trial
  only as a retire-only read. Prior P(continue): 0.10 (a lone t of 2.7, 50% post-publication haircut, a ~0.14 sd/day
  effect that a £50 day cannot use alone).

### B-2  Event-day range selector: NFP and FOMC days (opportunity-day feature)
- Mechanism: scheduled releases move gold about 1.45 times a normal day and the date is known weeks ahead
  (Andersen et al. 2003). *Evidence grade: B for range, none for direction.*
- Already tried?: `opportunity-days.md` 1.1 and README 6.6 (ratio 1.45). New: incremental over RV20 (NFP +0.37, t 2.7;
  FOMC +0.40, t 3.0; R-squared 0.0347 -> 0.0443 on 2,047 days).
- Timeframe and clock: daily flag from `calendar-v1`; join with `asof_join` on `available_utc` (schedule vintage); 15m/1h
  entries on that day.
- Entry/exit: none; it is a risk-concentration input (size or admit more attempts on those days, never loosen RiskGate).
- Control: flag on randomly drawn weekdays of the same weekday mix; incremental R-squared with day-block bootstrap.
- Discovery evidence: Section 2.4, 2,047 days, 96 NFP and 64 FOMC days, 4 looks.
- Cost check: n/a (not an entry); the value is in lifting opportunity-day capture, README objective (b).
- Expected events: 12 NFP + 8 FOMC a year, about 1.7 a month (flagged days, not trades).
- Kill rule: drop if the incremental R-squared in the discovery half of the H-16 forecast (per-day range, not |return|) is
  under +0.005 or the dummies lose significance against a HAR(1,5,22) baseline.
- Route: feature group inside H-16 / O-4 (0 trials, K_eff +1); Prior P(continue): 0.5 as a feature, ~0 as a standalone
  rule. Note the director's own 6.6 reading already supports H-16: this adds the vol-adjusted test.

### B-3  Volatility-regime ratio rv30/rv90 and tercile of rv60 as range features
- Mechanism: volatility clustering (HAR, Corsi 2009) and the slow 103-day cycle in |returns| (README 6.7). *Grade B.*
- Already tried?: new in the form "windowed ratio"; H-16/HAR already forecast range from RV. Likely **redundant with
  RV20/HAR**: RV5 added nothing over RV20 (coefficient 0.01, t 0.2); I did not test the 30/90 ratio's increment. Report
  honestly as untested for increment.
- Entry/exit: none. Control: shuffle the ratio within month; compare increment over HAR.
- Discovery evidence: rv30/rv90 > 1.25 (n 133) next-5-day |ret| 224 bp vs 199 (n 295 below 0.8); terciles 154/157/213.
- Cost check: n/a. Expected events: daily feature. Kill rule: no increment over HAR(1,5,22) in the discovery half, drop.
- Route: feature (H-02 candidate `atr14_rank_60d` already exists in `feature-groups-s1-s2-2026-10-18.md`: check overlap
  first; add `rv30_rv90` only if it is not collinear). Prior: 0.2.

### B-4  Post-FOMC-day fade (short the close after the statement, hold one day)
- Mechanism: gold over-reacts to the statement and the next session retraces (README 6.6: -39.7 bp, t -2.19; mine: -28 bp).
  Evidence grade D.
- Already tried?: R-08 (literature); new measurement only. Discovery: 64 events, both halves negative (-35, -22),
  t -1.1; **but I saw 2016-2025 go +10 bp (wrong sign)**, so I recommend **drop**.
- Cost/events: 8 a year, 0.7 a month; far below the 150 daily floor over any window (about 125 events, retire-only).
- Kill rule: dropped now; revive only on a new mechanism (positioning or yields reaction) and a forward read.
- Route: drop (no slot). Prior: 0.05.

### B-5  Option/futures expiry day profile (OG expiry d-1/d, LTD d-1)
- Mechanism: pinning and gamma hedging around the large open interest strike, roll flows around FND (CME OG series).
  Evidence grade D.
- Already tried?: new as a date study with control. Discovery: 96 expiries, d-1 +23.3 bp (p 0.028), d+1 -16.2; |return|
  not higher (57-70 vs 72 bp), 3 of 9 cells at p < 0.05 reversing next day: **noise-shaped**. Dates are an
  approximation not checked against CME's calendar.
- Cost/events: 12 a year, daily, below the 150 floor over 12 years (~190 events including FND/LTD only if split).
- Kill rule: close unless exact CME expiry dates show the same sign in a forward read of 12 expiries.
- Route: feature flag (`days_to_opt_expiry`) in H-02 only; no slot. Prior: 0.05.

## 6. What I would stop doing

1. Stop treating the 30/60/90-day windows as direction signals; keep them only as volatility features.
2. Stop the Diwali / Lunar New Year line: 8 festivals per window cannot be powered; it needs 30+ years.
3. Stop weekday and month-of-year directional studies except Friday, and Friday only as the forward shadow in B-1.
4. Do not register more calendar-effect trials: K is 145 and the gate-1 bar is about t 4.3; a daily effect with
   t 2.7 and about 800 events cannot reach it.

## 7. Open items for others

- Data engineer: freeze Diwali and Chinese New Year dates (`DIWALI`, `CNY` in the script are typed from memory) with
  a source and sha256; confirm CME gold FND/LTD/OG expiry dates from the CME calendar (the script derives them by rule).
- Slice A owns the hour-of-day and session decomposition (my 2.6 repeats its 6.1 pattern); slice F can use B-3.
- The earlier full-sample numbers are in my working output only; the file reports the discovery half.
