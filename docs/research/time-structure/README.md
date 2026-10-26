# Time structure, levels and indicators: shared frame for the 2026-10-26 programme

Research director, 2026-10-26. This is the frame every slice file in this folder follows. The synthesis (cross-slice
ranking, the pre-registration fragment, demo exploration arms and the delegation plan) goes in
`../time-structure-2026-10-26.md` once the slices land.

Owner directives (2026-10-26, verbatim):
1. "there are three sessions in a day for trading, each open and close and their role in trading strategies, 30 day,
   60 days 90 days, and other time related strategies, the research team should look at this and devise strategies
   and also use state of the art techniques"
2. "add fibnocci series, wavelet thoery, oscillators indicators and related techniques and tools"
3. "volume indicators, trends, order blocks, smart money trading"
4. "and relevant methods"

North-star (ADR 0004): £50+ on days the market offers it, less or nothing otherwise. This programme is judged by
whether it finds (a) a net-positive entry edge, (b) a better opportunity-day selector, or (c) a cheaper execution
window. A descriptive pattern that pays none of these is recorded and closed.

## 1. Slices

| Slice | File | Scope | Measured here already (section 6) |
|---|---|---|---|
| A | `sessions.md` | Asia/London/NY, opens and closes, overlaps, LBMA fixes 10:30/15:00 London, SGE auctions, COMEX open 08:20 / settle 13:30 NY, NY close and rollover, DST (UK/US gap weeks) | 6.1-6.5, 6.9 |
| B | `cycles.md` | 30/60/90-day momentum, reversal, range position, vol regime; turn of month, day of week, OG option expiry, futures FND/roll, FOMC/NFP/CPI/PCE days and the FOMC cycle, month/quarter end, seasonality | 6.6, 6.7 |
| C | `fib-wavelets-cycles.md` | Fibonacci retracement/extension levels and time zones; wavelets (MODWT, CWT, coherence with DXY/real yields); Ehlers (roofing filter, Hilbert, sinewave); Hurst/DFA, fractal dimension; spectral analysis, Goertzel; EMD/HHT | 6.8 (Fibonacci levels and time), 6.7 (variance ratios, periodogram) |
| D | `oscillators-trend-volume.md` | RSI, Stochastic, Williams %R, MFI, CCI, MACD; ADX/DMI, MA regimes, Supertrend, TSMOM; tick volume as a proxy, OBV, CMF, VWAP and anchored VWAP, volume profile/POC/value area, open volume spikes (Keltner and Donchian moved to F) | 6.10 (oscillators, with the method warning) |
| F | `volatility-bands.md` | Keltner, Bollinger, TTM squeeze, Donchian, ATR/Chandelier, envelopes, regression and VWAP bands; breakout vs mean reversion by regime and session; bands as exits (coordinate with the exits-researcher's V1u runner) | 6.5 (cost by hour), 6.7 (variance ratios: no reversion or momentum at 15m-90d) |
| E | `smc-orderblocks.md` | Order blocks, breaker/mitigation blocks, FVG, BOS/CHoCH, liquidity sweeps (Asia range in London), premium/discount, ICT kill zones, inducement, Wyckoff, market profile, supply/demand zones, delta/footprint proxies | 6.4 (Asia range taken in London) |

| G | `key-levels.md` | Session OHLC, PDH/PDL/PDC, PWH/PWL, weekly/monthly open, PMH/PML, round numbers: break / reject / sweep / magnet, conditioned on the range forecast and the daily trend, tested with traders' exits; gross and net R reported separately | 6.4 (Asia range in London), 6.8 (prior-day Fibonacci vs random fractions) |
| — | `learning-loop.md` | The continuous learning loop (stages 1-5), multi-model rules, what runs with 2026Q4 full | — |

One slice per file; no slice edits another slice's file, this README, `hypotheses.md` or a pre-registration. Each
slice returns its candidate hypotheses in the template of section 5; the director ranks across slices.

## 2. Budget and K (checked 2026-10-26 against the live registry, release `research-v1`, last row 2026-10-26 12:17 UTC)

| Item | Value | Source |
|---|---|---|
| 2026Q4 trials used / cap | **69 / 69** (`quarter_trials`) | `registry.jsonl` via `goldbot.research.registry.quarter_trials`; `trial_budget_overrides: {"2026Q4": 69}` |
| 2026Q4 remaining | **0**. No time-structure trial can run in 2026Q4 | — |
| 2027Q1 | 20 = 6 reserved (H-02, C4H, 4 H-02 survivor slots) + 14 open: 7 conditional rows (O-1..O-6, O-10), H-53, **6 free reserve** | `research-plan.md` 11.5 |
| Where a trial from this programme can come from | the 2027Q1 free reserve only, each by a dated research-director note in `preregistration-2027Q1.md` before its pre-registration (E-5 cap) | `preregistration-2027Q1.md` "Q1 open-budget round" |
| Registry trial rows | 103 (trial numbers 1..103; 34 are loop trials, not charged to the quarter) | registry |
| **K = `n_trials_effective`** | **145** today (registry rows + recorded discovery `k_eff`; H-49 stage 1's +1 and any unrecorded look come on top) | `registry.n_trials_effective` |
| Gate-1 bar at K = 145 | model-filtered t of about **4.3** (E[max Z_K] about 2.66 + 1.645) | `preregistration-2027Q1.md` gate-1 power note |
| Holdout | 2025-10-01 .. 2026-09-30, never loaded (the scripts drop it at load) | — |

The honest implication: a rule-only trial from this programme can at best read "continue" (gate 1 reads only
model-filtered rows), and a 15m idea needs a per-trade t near 4.3 to pass. Most of what follows will be closed by
0-trial measurements, which is the cheapest outcome and the point of measuring first.

## 3. Already tried: do not re-test without new evidence

| Topic | What ran | Result | Where |
|---|---|---|---|
| Session-open breakout 15m (London, NY) | trials #1, #7, #11, #13; batch #21-#25 | gross +0.042 R, t 1.31; fails P4; **R-02** | `hypotheses.md` B |
| Intraday momentum (NY, London; Gao et al. form) | #18, #19; batch #26-#28 | NY −0.006 R, London −0.028 R gross; **R-05** | `hypotheses.md` B |
| Asia-session drift (long 00-07 UTC) | #44 (H-04) | gross +0.043 R, t 3.09 on 4,025; net −0.093 R; **stop** | H-04 |
| West-hours drift (short NY) | none | H-24 lapsed with H-04 | `research-plan.md` 11.4 |
| Pre-FOMC drift, London-fix front-running, weekday/weekend effects | none (literature) | **R-08** retired from the literature | SoA C3 |
| tsmom 1h/4h, 24-480 bars | #16, #17 + 46 tsmom rows incl. loop | gross +0.061 R, net −0.079 R; **R-06/R-10** | `hypotheses.md` B |
| Slow TSMOM 1d (1-12 months) | #48 (H-01) | inconclusive, held (294 events, gross +0.166 R t 2.04) | H-01 |
| Trend pullback 1h, range/Donchian breakout 1h | #2, #4, #9, #14; #6, #8, #15 | no gross edge; **R-03, R-04** | `hypotheses.md` B |
| RSI/Bollinger/VWAP fades 15m | #3, #5, #10, #12 | no gross edge; **R-01** | `hypotheses.md` B |
| MFI signals, box/price-action flags (incl. London/NY session gate S-GATE, regime R-GATE) | H-40 discovery | **FAIL**, OOF AUC 0.503, no group survived; flags retired as filters | `box-mfi-families-2026-10-18.md` |
| Owner box break+close+MFI 15m | #89 (H-50) | gross +0.030 R t 1.61; net −0.297 R; **R-15** | H-50 |
| Sweep-and-reclaim fade 15m (prior day / prior session extremes) | #88 (H-48) | gross −0.028 R; **R-14** | H-48 |
| EQH/EQL as magnets | H-49 stage 1 (0 trials) | lift −0.3 / +0.8 pts; **R-13** | `kb5-stage1.json` |
| SMC rules (FVG, OB, BOS/CHoCH, premium/discount, Fibonacci, pivots, Ichimoku, candlesticks, volume profile, Heikin-Ashi) | none as rules | **R-09** retired as rules, kept as features; TVR1/TVR2 and H-34/H-35 withdrawn on event counts (441 / 1,196 / 0-18) | `prereg-tv-replica-2026-10-18.md`, `owner-style-mtf-2026-10-18.md` |
| Variance ratio, Hurst, efficiency ratio (`regime_stats`), vol estimators, round numbers, jumps | built as H-02 features; H-02 not yet run | pending (Q1 queued) | `goldbot/features/survey.py` |
| Exploration arms already frozen (not trials): session-VWAP 2σ fade (S1A/B), opening-range break 15/30 min (S2A/B), EMA pullback (S5), tsmom, box | `arms-2026-10-25.md` | running or owed on `icm-probe`/`icm-demo`; exploration rows, never promotion evidence | H-60/H-61 |

A slice that proposes something in this table must say what is new (mechanism, data, cost era) or drop it.

**What each one died on (added 2026-10-26 for the owner's question "how are you ruling these out").** Wording rule
for every slice: say "this version, tested this way, did not beat random after cost". Never say "the idea doesn't
work". The research cost model itself is under quant audit (H-50: gross +0.030 R but net −0.297 R; #88: a net-gross
gap of about 0.52 R).

| Item | Gross (t) | Net | Died on | Could a range filter or an exit design rescue it? |
|---|---|---|---|---|
| R-02 session-open breakout 15m | +0.042 R (1.31) | negative | **signal**: gross t below 2, and costs 0.2-0.3 R on top | partly. H-17 is exactly the range-conditioned, runner-exit version (Q1 O-2/O-3) |
| R-04 range breakout 1h | +0.015 R (0.38) | negative | **signal** | only through H-17 / H-16 conditioning; the bare rule, no |
| R-05 intraday momentum | −0.006 / −0.028 R | negative | **signal** | no: gross ≤ 0 |
| R-03 trend pullback 1h | ≈ 0 | negative | **signal** | no |
| R-01 RSI/Bollinger/VWAP fades | ≈ 0 | negative | **signal** | no (README 6.10) |
| H-04 Asia drift | +0.043 R (3.09) | −0.093 R | **cost per R** | yes, by cost: the current broker round trip ($0.14-0.23/oz) is a fraction of the research prior; the cost-era question is the quant audit's |
| R-06 tsmom 1h/4h | +0.061 R (2.63) | −0.079 R | **cost per R** (and gross t below the DSR bar) | exit and holding-period designs were tried (R-10); a cheaper cost era is the only open route |
| H-01 slow TSMOM 1d | +0.166 R (2.04) | +0.002 R | **events** (294, below the floor): inconclusive | not a rescue question; needs more history |
| H-50 owner box break+close+MFI 15m | +0.030 R (1.61) | −0.297 R | **signal** (gross t below 2) **and cost** (net gap 0.33 R) | a range filter could lift gross; cost per R falls on high-ATR days; H-53 (1h) is the queued test |
| H-48 sweep-and-reclaim fade 15m | −0.028 R | −0.552 R | **signal** (gross ≤ 0); the 0.52 R gap is a cost-model question, not the verdict | no for this version |
| H-40 box/price-action flags | AUC 0.503 | — | **signal** | no |
| R-13 EQH/EQL magnets | lift −0.3 / +0.8 pts | — | **signal** (base rate) | no |
| TVR1/TVR2, H-34/H-35 SMC rules | not measured | — | **event count / stop fit at 0.01 lot**: never read on outcome | not a signal verdict: they were never tested on outcomes; slice E re-defines them testably |
| R-08 pre-FOMC, fix front-running, weekday | not run | — | **literature only** | slice B tests the calendar ones on the discovery half (README 6.6) |

## 4. Common methodology (binding on every slice)

**4.1 Two halves, written before any outcome was read.** Outcome measurements (returns, continuation, reversal,
level reactions, indicator ICs, cycles) use the **discovery half 2010-01-01 .. 2017-12-31** only. The
**confirmation half 2018-01-01 .. 2025-09-30** is reserved for the pre-registered reads; no slice reads an outcome
on it. Activity and cost tables (spread, ATR, tick counts, |move|) may use both halves. A slice that has already
looked at 2018+ outcomes says so, and its hypothesis is then charged as a full trial with no confirmation credit.

**4.2 Point in time.** A price at clock time T is the close of the last 1m bar with `visible_at` <= T. Every
external series joins through `goldbot.data.store.asof_join` on `available_utc`. Session clocks are local
(`zoneinfo`: Asia/Tokyo, Asia/Shanghai, Europe/London, America/New_York), so DST is handled per date. Trading day
= NY date after +7 h (ends 17:00 New York).

**4.3 No repainting.** A pivot, swing, order block, FVG, BOS/CHoCH, Fibonacci anchor or wavelet coefficient exists
only from the bar on which it is **confirmed** on closed bars (a w-bar pivot from bar t+w). Centred filters
(two-sided MAs, HP, centred wavelet/EMD, CWT at the edge) are banned as inputs; wavelets use the causal MODWT with
the boundary coefficients dropped (or recomputed on an expanding window ending at t), and the test shows the value
at t is identical when bars after t are deleted. Descriptive "historical structure" tables may use centred windows
only when labelled as such and never feed a rule.

**4.4 Randomised controls (levels and events).** A level or event claim is tested against a control drawn from the
same days and the same reference range:
- *Levels* (Fibonacci, OB, FVG edges, POC, VWAP bands, supply/demand zones): control levels at uniform random
  fractions of the same reference range (at least 40 fractions a day, fixed seed), same first-touch rule, same
  outcome window. Read the difference in mean reaction (ATR units) with a **day-block bootstrap** CI.
- *Clock events* (an open, a fix, a close): control = the same statistic at random clock times in the same session
  (or the same clock time on non-event days for calendar events).
- *Pattern events* (OB, FVG, sweeps, BOS): control = the same entry/exit rule at random bars matched on session and
  ATR decile (the `S0`/`I0` random-bracket idea of `arms-2026-10-25.md`).
A claim that does not beat its control in the discovery half is closed there at 0 trials.

**4.5 Statistic.** Mean per event with a **day-clustered** t (CR1, G − 1 df) whenever several events share a day; a
pooled IC with a day-block bootstrap, **never the mean of within-day ICs**: demeaning inside a day biases a level
indicator's IC strongly negative (section 6.10: −0.135 on a pure random walk). Daily signals at horizon h use
non-overlapping samples or Newey-West with h lags. Report the number of looks behind every table.

**4.6 Costs.** Report net in R at the stop the rule would use, under **two** cost cases:
- *Research prior* (what a trial would be read on): Dukascopy spread at the bar (ask in, bid out) + 0.37 $/oz +
  swap −60/0 USD per lot-night. On 15m this is about 0.25-0.45 ATR15 per round trip in 2010-2019 and about
  0.15-0.25 in 2025 (section 6.5).
- *Current broker* (exploration arms, sizing): `cost-model-1m-2026-10-25.md`, central $0.144/oz, conservative
  $0.23/oz round trip, about 0.02-0.05 ATR15 at today's ATR15 of about $8.
A hypothesis is worth a trial only if its plausible gross edge is several times the research-prior cost; it is worth
an exploration arm if it is several times the broker cost and fits the £ caps.

**4.7 Sizing facts (not research variables).** 0.01 lot = 1 oz; scalp per-trade cap £6.50, intraday £45, swing £40
on the learning hosts (ADR 0043); £1,000 live means 15m only. Max 40 features per model, fold-internal selection
only.

## 5. Hypothesis template (one block per candidate)

```
### <slice letter>-<n>  <short name>
- Mechanism: who trades, why, what forces the price (cite)        - Evidence grade: A/B/C/D (KB README)
- Already tried?: registry #/R-xx or "new: <what is new>"
- Timeframe and clock: decision TF, local-clock anchors, DST handling
- Entry: exact rule on closed bars, no repaint                    - Side: long / short / both
- Exit: stop, target, time exit, flat-by                          - One position at a time? (y/n)
- Control: the randomised control it must beat (4.4)
- Discovery-half evidence (2010-2017): table ref, n, effect, day-clustered t, looks behind it
- Cost check: gross edge in R vs research-prior cost and broker cost at the stop (4.6)
- Expected events: 2010-2025-09 count (counts only) and per year; vs the 1,000 (intraday) / 150 (daily) floors
- Kill rule: the 0-trial result that closes it before any trial
- Read rule (if a trial or arm): continue / stop / inconclusive, fixed now
- Route: 0-trial diagnostic | feature group (H-02/O-4/H-62) | exploration arm (icm-probe/vantage-demo) | Q1 free-reserve trial
- Prior P(continue): stated honestly
```

## 6. Measurements already taken (director, 2026-10-26; discovery half unless stated)

Script: `docs/research/candidates/time_structure_2026-10-26.py` (read-only; data-v1 1m mid; output JSON outside the
repo). Run: `uv run python docs/research/candidates/time_structure_2026-10-26.py --raw <data-v1 dir> --cal
<calendar-v1 us_calendar.parquet> --out <dir>`. About 25 tables, so about 150 individual statistics: at that many
looks a lone |t| of 2-3 is expected by chance (Bonferroni 5% at about t 3.4).

### 6.1 Session decomposition of the day (log return, bp per day, 2010-2017) [slice A]

| Segment (local clocks) | Mean bp | t | SD bp | Share of day variance | Positive years /8 |
|---|---|---|---|---|---|
| Overnight: NY 17:00 -> Tokyo 08:00 | +0.95 | 2.13 | 13.8 | 2% | 6 |
| Asia: Tokyo 08:00 -> London 08:00 | +1.20 | 0.92 | 45.3 | 20% | 4 |
| London AM: 08:00 -> NY 08:30 | **−2.53** | **−2.45** | 46.9 | 21% | 3 |
| NY pit: 08:30 -> COMEX settle 13:30 NY | −0.87 | −0.58 | 67.7 | **45%** | 3 |
| NY late: 13:30 -> 17:00 NY | +0.77 | 1.06 | 32.9 | 11% | 5 |
| Full day | +1.63 | 0.65 | 101.2 | 100% | 6 |

Reading: the NY pit window carries 45% of the day's variance (the opportunity window); no segment has a drift above
about 2.5 bp, against a research-prior round trip of about 4-8 bp at 2010-2017 prices. The London-AM sign is the
Western leg of the Asia/West thesis; H-04's Asian leg already failed on costs (#44). n differs by segment because a
price stale for 30 minutes (Monday's prior close, Dukascopy's 21-23 UTC gaps) is dropped.

### 6.2 Clock anchors: activity jump and pre/post 30-min correlation (ATR15 units, 2010-2017, n about 2,060) [A]

| Anchor (local) | |15m| after / before | corr(pre 30, post 30) | t |
|---|---|---|---|
| Tokyo open 08:00 | 1.14 | **−0.11** | −3.85 (n 1,200) |
| SGE AM auction 10:15 Shanghai | 0.84 | +0.08 | +3.54 |
| Tokyo close 15:00 | 1.16 | +0.09 | +4.31 |
| London open 08:00 | 1.26 | −0.03 | −1.41 |
| LBMA AM 10:30 London | 1.07 | −0.01 | −0.60 |
| **COMEX open 08:20 NY** | **1.81** | +0.02 | +0.84 |
| NY data slot 08:30 NY | 1.17 | +0.02 | +0.79 |
| LBMA PM 15:00 London | 1.15 | −0.01 | −0.31 |
| WM/R 4 pm London | 0.96 | +0.04 | +1.88 |
| London close 16:30 | 0.98 | −0.04 | −1.97 |
| COMEX settle 13:30 NY | 0.76 | **−0.07** | −3.34 |

Reading: four anchors show a 30-min serial correlation of |0.07-0.11| at |t| > 3 (Tokyo open reversal, SGE and Tokyo
close continuation, COMEX-settle reversal). The size is small: with a 30-min SD of about 0.5-0.7 ATR15 the
expected signed gain is about 0.04-0.07 ATR15 per event, below the research-prior round trip (0.23-0.30 ATR15 in
Asian hours, table 6.5) and near the broker cost today. Slice A owns the confirmation design.

### 6.3 Opening ranges (first 30/60 min; session = open .. next anchor; 2010-2017) [A]

| Session | P(session high or low inside OR) | P(break) | P(close beyond OR on break side) | P(full reversal) | Break -> session end, OR units | t |
|---|---|---|---|---|---|---|
| London 30 min (to NY 08:30) | 47% | 98% | 58% | 21% | +0.02 | 0.47 |
| NY 30 min (to 17:00 NY) | 48% | 97% | 58% | 19% | −0.05 | −0.86 |
| NY 60 min | 65% | 92% | 57% | 13% | −0.04 | −1.19 |

Reading: the opening range sets one session extreme half the time (activity), but the first break has no
directional follow-through: R-02 again. Do not re-propose ORB on 15m; the 5m arms S2A/S2B are the only open form.

### 6.4 Asia range in London (Tokyo 08:00 -> London 08:00 range; London to 12:00 NY; 2010-2017) [A, E]

| P(take Asia high) | P(take low) | P(both) | P(neither) | First sweep -> NY close, Asia-range units | t | P(close back inside) |
|---|---|---|---|---|---|---|
| 68% | 68% | 39% | 2.4% | +0.06 | 1.53 (n 1,981) | 30% |

Reading: London almost always takes an Asia extreme; the first take-out continues slightly (not a reversal), so the
"Asia sweep then reverse" SMC claim has no support here; H-48 (#88) already failed the reclaim fade.

### 6.5 Cost and activity by New York hour (15m bars, Dukascopy, median) [A, all slices' cost check]

| NY hour | Spread/ATR15 2010-17 | 2019-2025-09 | 2025-01..09 | |15m move|/ATR15 2019-25 |
|---|---|---|---|---|
| 00-02 (Asia) | 0.28-0.30 | 0.21-0.23 | 0.15-0.16 | 0.40-0.62 |
| 03-07 (London AM) | 0.22-0.25 | 0.17-0.18 | 0.13-0.14 | 0.46-0.62 |
| 08-11 (NY open, overlap) | **0.14-0.19** | **0.11-0.15** | **0.10-0.13** | 0.44-0.80 |
| 12-15 (NY afternoon) | 0.15-0.19 | 0.12-0.14 | 0.11-0.12 | 0.36-0.37 |
| 16-19 (NY close, rollover) | 0.25-0.31 | 0.19-0.23 | 0.15-0.19 | 0.27-0.37 |

The 08:00-11:00 NY window is both the cheapest in ATR and the most active. Add 0.37 $/oz for the research prior
(about 0.15 ATR15 in 2019-2025, about 0.08 in 2025).

### 6.6 Calendar effects on daily returns (NY close to NY close, 2010-2017; about 30 looks) [B]

| Effect | n | Mean bp | t | Range ÷ 20-day median |
|---|---|---|---|---|
| Friday | 409 | +14.1 | 2.70 | 1.18 |
| Other weekdays | 412-415 each | −5.7 .. +0.4 | −1.17 .. +0.09 | 1.01-1.15 |
| Turn of month, first 3 days | 288 | +8.7 | 1.41 | 1.16 |
| Last day of month / quarter | 96 / 32 | +6.9 / +19.9 | 0.60 / 1.15 | 1.18 |
| COMEX OG option expiry proxy (4th-last business day) | 96 | **+22.3** | **2.39** | 1.05 |
| FND proxy / GSCI roll window | 40 / 200 | +11.2 / −3.3 | 0.79 / −0.51 | 1.08 / 1.17 |
| FOMC statement day / next day | 64 / 64 | +10.6 / **−39.7** | 0.70 / **−2.19** | **1.46** / — |
| NFP day | 96 | +21.9 | 1.75 | **1.45** |
| CPI day / PCE day | 96 / 95 | −0.7 / +0.8 | −0.07 / 0.08 | 1.19 / 1.10 |
| Non-tier-1 days | — | — | — | 1.08 |
| FOMC cycle, even vs odd weeks (Cieslak et al. 2019) | 1,087 / 936 | +1.1 / +0.3 | 0.38 / 0.09 | — |
| Best month (August) | 178 days | +17.7 per day | 2.41 | — |

Reading: with about 30 looks, nothing clears Bonferroni (t about 3.1). The robust fact is range, not direction:
FOMC and NFP days are about 1.45 times a normal day's range, CPI 1.19, PCE 1.10 (feeds H-16). Directional
candidates for the confirmation half: Friday, OG expiry day, the post-FOMC day; each is under 1,000 events and
daily, so at most a retire-only read or a feature. The FOMC even-week cycle is absent in gold.

### 6.7 30/60/90-day signals, variance ratios, periodogram (daily, 2010-2017) [B, C]

| Signal -> forward | Spearman IC (non-overlapping) | t | n |
|---|---|---|---|
| mom30 / mom60 / mom90 -> 20 d | −0.00 / −0.09 / −0.06 | −0.01 / −0.91 / −0.62 | 99 |
| mom30 / 60 / 90 -> 5 d | −0.04 / −0.03 / −0.05 | −0.76 / −0.51 / −0.98 | 396 |
| range position 30/60/90 d -> 20 d | +0.02 / −0.02 / −0.05 | < 0.5 | 99 |
| RV20/RV90 -> 20 d return | −0.00 | 0.00 | 99 |
| RV20 -> next 5 d / 20 d RV | **+0.40 / +0.50** | — | 396 / 99 |

mom60 -> 20 d IC is negative in all 8 discovery years (−0.03 .. −0.52; 12 samples a year): weak, not significant
pooled, but the sign is the opposite of TSMOM at this horizon. Variance ratios (Lo-MacKinlay, robust z), daily
q = 2..90: 0.85-1.00, z −0.2 .. −0.6; 15m q = 2..96: 0.985-1.03, |z| < 1.2. **No significant momentum or mean
reversion at any scale from 15 minutes to 90 days** in 2010-2017. Periodogram of daily returns: no cycle in
20-120 days (top 20/28/24 d, p 0.64 against a 5-day block-shuffle null); of |returns|: a peak near **103 days**
(p 0.012), i.e. a slow volatility cycle, not a price cycle; power at 30/60/90 days is below average. Only volatility
is forecastable at these windows (as H-16 assumes).

### 6.8 Fibonacci (2010-2017) [C]

Levels at 23.6/38.2/50/61.8/78.6% of the prior day's range (retracement side by the prior day's direction) or of
the Asia range (London + NY sessions), first touch, signed move 8 bars (2 h) later in ATR15; control = 40 random
fractions in [0.15, 0.85] of the same range, same rule; 300 day-block bootstraps.

| Reference | Fib mean | Control mean | Diff [95% CI] | P(hold) fib / control | n fib / control |
|---|---|---|---|---|---|
| Prior day | +0.014 | +0.045 | **−0.032 [−0.065, −0.002]** | 53.1% / 54.3% | 6,450 / 52,516 |
| Asia range | −0.002 | −0.002 | −0.001 [−0.025, +0.024] | 51.5% / 51.5% | 8,196 / 65,994 |

Fibonacci retracements do **not** beat random fractions of the same range; on the prior day they are slightly worse
(the 61.8/78.6 levels sit deeper, where a touch means the prior day's move is already failing). Fibonacci time
(spacing of 10-day swing pivots, centred window, description only): 121 gaps, counts at 8/13/21/34/55 days of
4/8/6/2/0 against neighbour means of 7/5.5/4.5/1/0.5: no over-representation that 121 gaps could show. Slice C
extends this to 30/60/90-day swings and extensions, then closes or narrows it.

### 6.9 DST: which clock does activity follow? (mean |15m|/ATR15 by UTC slot, both halves) [A]

| Regime | Peak UTC slot | Second peak |
|---|---|---|
| Both on summer time | 12:30 | 13:30 |
| Both on winter time | 13:30 | 14:30 |
| UK/US gap weeks (US moved, UK not) | **12:30** | 13:30 |

The peak follows **New York's** clock (08:30 NY data, 09:30 equity open) in the gap weeks, not London's. Any rule
anchored on UTC or on London time mis-times the NY window for about 3 weeks a year; local NY clocks are correct
(`session.py` v2 already does this).

### 6.10 Oscillators: the method warning (15m, 2010-2017) [D]

- Mean of within-day Spearman ICs (RSI14 vs next 4 bars): −0.16 on gold, but **−0.135 on a simulated random walk**
  with the same procedure: almost all of it is the within-day demeaning artefact. Pooled IC: −0.039 on gold vs
  +0.003 on the random walk, a small real short-horizon reversal (about 0.04 ATR15 per bar at most).
- RSI 30/70 first-cross fades by session, net nothing: Asia +0.08 ATR over 4 bars (t 1.9, n 1,432), London −0.31 over
  16 bars (t −3.0), NY +0.13 over 16 bars (t 2.2). Signs flip by session and horizon; the cost at those hours is
  0.15-0.30 ATR15 plus 0.37 $/oz. R-01 stands; slice D tests oscillators only as features (O-4/H-62) or with a new
  mechanism.

## 7. What each slice hands back

1. Its file in this folder, following sections 4-5, with measured tables (discovery half), literature with URLs
   (peer-reviewed first; *(unverified)* marked), and candidate hypotheses in the template.
2. A one-line verdict per topic in its scope: *close* (0 trials, reason), *feature* (group and K_eff), *arm* (host,
   £ at stop, est. fills a day), or *trial* (events, prior).
3. The looks it took (tables, statistics), so the director can state K honestly.
4. What it would stop doing.
