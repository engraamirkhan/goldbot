# Slice F: volatility bands and channels on XAUUSD, 15m / 1h / 4h / 1d (2026-10-26)

Staff research lead for the research director's time-structure programme (frame: `README.md` in this folder, sections
4-5). Descriptive measurement only: **no trial registered, no strategy backtest, no job dispatched**. Scripts:
`docs/research/candidates/time_structure/volatility_bands_diag.py` (the measurements; read-only, about 3 min) and
`docs/research/candidates/time_structure/vb_compact.py` (table formatting). Run: `uv run python -I docs/research/candidates/time_structure/volatility_bands_diag.py
--raw <data-v1 dir> --out <dir>`.

## 0. Integrity statement (read first)

- **Data and halves.** Dukascopy 1m bid/ask mid, aggregated to 15m/1h/4h (UTC-aligned) and a trading-day 1d bar
  (NY date after +7 h). The loader reads **only the 2010-2017 files**. The confirmation half (2018-01-01 ..
  2025-09-30) and the holdout (2025-10-01 ..) were **never loaded**; no outcome of this note was measured on them.
- **Point in time.** Every bar carries `available_utc` = last constituent minute + 1 min. A signal on bar i enters at
  the open of bar i+1 and is scored to the close of bar i+4 (4 bars on every timeframe: 1 h, 4 h, 16 h, 4 d). Higher
  timeframe context joins through `goldbot.data.store.asof_join` on `available_utc`. Donchian levels use bars up to
  i-1; no band uses a centred filter. Timestamps in ns go through `goldbot.data.timeutil.epoch_ns`.
- **Unit and cost.** The outcome unit is the signal bar's ATR(14) (Wilder, `goldbot.features.technical.atr`), so eras
  of different gold volatility are comparable. Costs are in dollars, so they are converted at today's median ATR
  (`atr-measured-2026-10-18.md`: 15m $8.0, 1h $17.7, 4h $36.6, 1d $87.6). Broker cost = `cost-model-1m-2026-10-25.md`
  (central $0.144, conservative $0.23 per oz round trip). The README research-prior cost (0.25-0.45 ATR15 in 2010-2017)
  is used for the trial-worthiness test, which is the harsher one.
- **Control.** Every event set is compared with 300 random draws of the same count, the same long/short mix, the same
  forward rule, from the same eligible bars (the same slice, or the same higher-timeframe state). The z in the tables
  is the event mean against that control. t(day) is a day-clustered t (CR1), since events share days. Events are
  de-duplicated to one per 4 bars.
- **Looks (multiple-testing honesty).** **336 cells** are counted (51 base band x use x timeframe cells; 231
  regime/session/open-window cells for five event sets; 3 squeeze direction cells (the 1d squeeze had too few releases);
  45 higher-timeframe-filter cells; 6 composite cells added after seeing the rest, section 6). Also 4 squeeze range/predictor tests, 2 exit diagnostics,
  3 redundancy matrices and 3 null runs (not counted as looks on gold). By chance alone 15 of 336 cells would have
  |z| > 2; **46 do**. Benjamini-Hochberg at q = 0.10 over all 336 keeps 12. Those 12 are **not 12 findings**: the
  three canonical bands (Keltner, Bollinger, Donchian) have a position correlation of 0.93-0.95 (section 7), so they
  re-measure the same events. The independent findings are two (Hurst/ADX regime, section 4; stretched higher
  timeframe, section 5). For K, charge this note about **+15 effective looks** (about 14 independent band geometries x
  regime axes after collapsing the 0.93+ correlated bands), not 336.
- **Post-hoc composites.** F-1 and F-2 below were formed after sections 1-5 were read. Their discovery numbers carry
  that selection and must be read as hypotheses, not results.
- **Cost-era caveat.** All outcomes are in ATR units of 2010-2017. Gold's ATR15 is now about 4x its 2021-2023 level
  and the spread-to-ATR ratio is about 5-10x smaller; whether a 2010-2017 ATR-unit edge exists today is exactly what the
  confirmation half is for.

## 1. What was already tried (do not redo)

| Item | Where | Reading for this slice |
|---|---|---|
| R-01 RSI/Bollinger/VWAP fade, 15m | trials #3, #5, #10, #12 | no gross edge. Section 3 agrees on the unconditional fade (BB fade 15m -0.001 ATR) |
| R-04 range (Donchian-style) breakout, 1h | #6, #8, #15 | gross +0.015 R, t 0.38. Section 3 agrees at 1h (DC20 +0.022 ATR, t 0.7) |
| R-11 box rules 1h/4h; R-15 owner box break + MFI 15m (#89) | H-50 | gross +0.030 R t 1.61, net -0.297 R. The box break is a range-breakout cousin, so it inherits section 3's null |
| R-06 / R-10 tsmom 1h/4h | #16, #17 | 4h band breakouts below are the same family (continuation after a 20-bar extreme) |
| S1A/B session-VWAP 2 sigma fade arms; EMA pullback S5 | `arms-2026-10-25.md` | exploration arms running; section 3's VWAP row is the discovery-half read of the same thing |
| H-20 compression -> expansion (NR4/NR7, ATR percentile) as a range feature inside H-16 | `hypotheses.md` | section 2 tests whether Bollinger bandwidth adds to it |
| `bb_z_20`, `bb_pctb_20`, `donchian_pos_20`, `compression_8_96`, `adx14`, `hurst_128` | `goldbot/features/` | exist already and sit in the H-62 40-feature spec. No Keltner, no squeeze flag, no regression channel, no higher-timeframe band position exists |
| Chandelier 2.5-3 ATR trail | `ExitPolicy.trail_stop` (frozen ATR), H-17 V1, `prereg-2026-10-25-high-volume.md` I2 | already specified; section 8 adds only a diagnostic |

Not covered: nothing in the repo tests Keltner, the TTM squeeze, STARC, regression channels, or a band breakout
conditioned on regime or on a higher-timeframe band. Those are what is new here.

## 2. Definitions and literature (compact)

| Tool | Definition used | Source and standing |
|---|---|---|
| Keltner channel | EMA(20) +/- k x ATR(14); k = 2.0 for breakout (Raschke's EMA20/2 ATR form), 1.5 for the squeeze. The 1960 original uses a 10-day mean of the typical price and the 10-day mean high-low range | C. Keltner, *How to Make Money in Commodities* (1960); Raschke and Connors, *Street Smarts* (1995) (the modern EMA/ATR form) (not re-fetched). Practitioner, grade D |
| Bollinger Bands, %B, bandwidth | SMA(20) +/- 2 population SD; %B = (close - lower)/(upper - lower); bandwidth = (upper - lower)/mid | J. Bollinger, *Bollinger on Bollinger Bands* (2001). Bandwidth percentile as a volatility-compression gauge is practitioner doctrine; no peer-reviewed gold test found. Grade D |
| TTM squeeze | squeeze on when Bollinger(20, 2.0) lies inside Keltner(20, 1.5 ATR); release = first bar off after at least 6 bars on; direction = sign of close minus the mean of the Donchian-20 midpoint and SMA(20) (the sign of Carter's linear-regression momentum histogram, here without the regression) | J. Carter, *Mastering the Trade* (2005/2012) (not re-fetched). Practitioner, grade D |
| Donchian / turtle channel | prior 20- (or 55-) bar high and low; entry on a close beyond | R. Donchian (1960s); C. Faith, *Way of the Turtle* (2007). Trend-following on diversified futures has long out-of-sample support (Hurst, Ooi, Pedersen 2017, "A Century of Evidence on Trend-Following Investing", *JPM* 44(1)) (not re-fetched); intraday on small-tick contracts no longer pays (Kurth, Eisler, Rej, Bouchaud 2026, arXiv 2607.01550, cited in `scalping-2026-10-17.md` C-8). Grade B at weekly-monthly, D at 15m-1h |
| Trading-range breakout rules (the academic anchor) | Brock, Lakonishok, LeBaron, *J. Finance* 47(5), 1992: 50- to 200-day channel breakouts on the DJIA 1897-1986 beat the null; Sullivan, Timmermann, White, *J. Finance* 54(5), 1999: the same rules fail after data-snooping correction on out-of-sample data; Park and Irwin, *J. Econ. Surveys* 21(4), 2007: profits of technical rules fade after about 1990 in equities and drop in FX; Neely, Weller, Ulrich, *JFQA* 44(2), 2009: FX rule profits disappear after the 1990s; Lo, Mamaysky, Wang, *J. Finance* 55(4), 2000: some pattern information after conditioning (not re-fetched, page numbers from memory) | Peer-reviewed. Net reading: breakout rules had a historical edge that did not survive data-snooping and publication; grade B for existence, D for today's gold at 15m |
| ATR channel / Chandelier exit | highest high since entry minus 3 x ATR(14) (long) | C. Le Beau (1990s), via Elder, *Come Into My Trading Room* (2002) (not re-fetched). Kaminski and Lo (2014), *J. Financial Markets* 18: a trailing stop adds value only with serial correlation (cited in `scalping-2026-10-17.md` C-8). Grade D/B |
| Moving-average envelope | SMA(n) +/- p percent | Same geometry as Keltner with a fixed percentage in place of ATR. Not tested separately: with a fixed percent it is a volatility-blind Keltner and reads as an ATR band when volatility is stable. Closed on redundancy |
| Standard-error / linear-regression channel | OLS line of the last 50 closes, band = fitted end value +/- 2 residual SD (closed form, causal) | Raff regression channel / Standard Error Bands (Jon Andersen, *Technical Analysis of Stocks & Commodities* (1996)) (unverified). Grade D |
| VWAP standard-deviation bands | session (trading-day) VWAP using tick count as volume, +/- 2 volume-weighted SD, only after 6 (15m) or 3 (1h) bars of the day | Desk benchmark practice. Tick volume is broker-specific (survey #16). Grade D. Slice D owns the VWAP levels themselves; here it is a band only |
| STARC bands | SMA(6) +/- 2 ATR(15) | M. Stoller, STARC bands (1980s) (unverified). Grade D |
| Hurst exponent / R/S as a regime filter | `goldbot.features.survey.rolling_hurst` (R/S, 128 returns, chunks 8-64), tercile of its trailing 250-bar rank | Hurst (1951); Lo, *Econometrica* 59(5), 1991: the classical R/S is biased by short memory and heteroscedasticity (not re-fetched). Here it is a state variable, not an estimate of memory |

Mechanism, stated once. A band is a volatility-scaled distance from a moving centre. A close outside it is an event
with a known base rate (about 4-5% of bars for a 2-sigma band) and no information beyond "the move was large for
recent volatility". Whether it **continues** (stop-run momentum, order flow chasing, forced covering) or **reverts**
(liquidity provision, exhausted move) is a statement about serial dependence of returns after large moves, which
the README section 6.7 variance ratios put at nil on average from 15m to 90 days. So the live question is not "which
use is right" but "is there a state in which the sign differs", and the only states with a published mechanism are
trendiness (ADX/Hurst), compression (squeeze), the session clock and the higher-timeframe location.

## 3. Breakout vs mean reversion, all bands, all timeframes (2010-2017, 4-bar forward, ATR units)

Mean = signed forward return in ATR of the signal bar (positive = the use paid). Breakout = first close outside the
band, trade with it. Fade = wick beyond the band that closes back inside (Donchian: beyond the prior extreme), trade
toward the middle. n = after de-duplication; per month is the raw event rate. t(day) is day-clustered. Controls
(random bars, same mix) have means of -0.01 to +0.01 everywhere; cost break-evens are below.

| TF | band | brk n (per month) | brk mean ATR | brk t(day) | brk yrs+ | fade n (per month) | fade mean ATR | fade t(day) | fade yrs+ |
|---|---|---|---|---|---|---|---|---|---|
| 15m | KC20x2.0 | 6778 (81) | -0.021 | -0.9 | 3/8 | 11526 (193) | +0.017 | +1.1 | 5/8 |
| 15m | BB20x2 | 10122 (123) | -0.010 | -0.5 | 2/8 | 16938 (303) | -0.001 | -0.0 | 5/8 |
| 15m | DC20 | 10888 (133) | -0.013 | -0.7 | 2/8 | 12683 (192) | +0.021 | +1.4 | 5/8 |
| 15m | DC55 | 6145 (74) | +0.004 | +0.2 | 4/8 | 7090 (104) | +0.011 | +0.6 | 6/8 |
| 15m | STARC6x2 | 2964 (33) | +0.043 | +1.1 | 4/8 | 6127 (87) | -0.003 | -0.1 | 4/8 |
| 15m | LR50x2 | 7887 (95) | -0.002 | -0.1 | 3/8 | 12821 (224) | -0.000 | -0.0 | 5/8 |
| 15m | VWAPx2 | 8186 (100) | +0.019 | +1.0 | 6/8 | 13998 (256) | -0.006 | -0.4 | 3/8 |
| 1h | KC20x2.0 | 1826 (21) | +0.021 | +0.5 | 6/8 | 3085 (52) | -0.012 | -0.4 | 2/8 |
| 1h | BB20x2 | 2549 (31) | +0.064 | +1.7 | 6/8 | 4173 (76) | -0.021 | -0.8 | 4/8 |
| 1h | DC20 | 2804 (34) | +0.022 | +0.7 | 6/8 | 3110 (47) | -0.061 | -1.9 | 1/8 |
| 1h | DC55 | 1610 (20) | +0.027 | +0.7 | 4/8 | 1723 (25) | -0.008 | -0.2 | 5/8 |
| 1h | STARC6x2 | 791 (9) | +0.046 | +0.7 | 4/8 | 1566 (22) | -0.038 | -0.8 | 2/8 |
| 1h | LR50x2 | 1880 (22) | +0.093 | +2.3 | 8/8 | 3099 (53) | -0.042 | -1.4 | 3/8 |
| 1h | VWAPx2 | 3348 (42) | +0.021 | +0.7 | 6/8 | 6763 (151) | -0.012 | -0.6 | 4/8 |
| 4h | KC20x2.0 | 567 (7) | +0.107 | +1.8 | 6/8 | 974 (16) | -0.027 | -0.6 | 3/8 |
| 4h | BB20x2 | 691 (8) | +0.136 | +2.7 | 6/8 | 1104 (18) | -0.078 | -1.7 | 1/8 |
| 4h | DC20 | 810 (10) | +0.090 | +1.8 | 5/8 | 907 (13) | -0.065 | -1.4 | 3/8 |
| 4h | DC55 | 512 (6) | +0.142 | +2.2 | 5/8 | 559 (8) | -0.074 | -1.1 | 2/8 |
| 4h | STARC6x2 | 221 (2) | +0.118 | +1.2 | 5/8 | 459 (6) | -0.065 | -1.0 | 2/8 |
| 4h | LR50x2 | 510 (6) | +0.149 | +2.5 | 6/8 | 849 (14) | -0.067 | -1.4 | 2/8 |
| 1d | KC20x2.0 | 90 (1) | +0.180 | +1.3 | 6/8 | 131 (2) | +0.065 | +0.5 | 6/8 |
| 1d | BB20x2 | 109 (1) | +0.143 | +1.0 | 7/8 | 173 (3) | +0.028 | +0.3 | 3/8 |
| 1d | DC20 | 147 (2) | +0.140 | +1.3 | 6/8 | 159 (2) | -0.070 | -0.6 | 3/8 |
| 1d | DC55 | 77 (1) | +0.250 | +1.6 | 7/8 | 94 (1) | -0.200 | -1.2 | 2/8 |
| 1d | LR50x2 | 82 (1) | +0.273 | +1.9 | 6/8 | 126 (2) | -0.221 | -1.9 | 1/8 |

Cost break-even in ATR units (gross edge must exceed it): at today's ATR, broker central $0.144 = 0.018 ATR15 / 0.008
ATR1h / 0.004 ATR4h / 0.002 ATR1d; conservative $0.23 = 0.029 / 0.013 / 0.006 / 0.003. Research prior (README 4.6) at
15m 0.25-0.45 ATR15.

Reading:
1. **15m: no unconditional use of any band beats random.** Every 15m cell has |t(day)| below 1.5. The largest
   (Donchian-20 fade, +0.021 ATR) is a tenth of the research-prior cost and equals the broker cost: it is R-01/R-04
   again, now over six more band geometries.
2. **The sign moves with timeframe: fades are non-positive from 1h up, breakouts positive from 1h up.** 4h breakouts
   are +0.09 to +0.15 ATR4h (six geometries, 5/8 to 6/8 positive years, t(day) 1.8-2.7), 1d +0.14 to +0.27 ATR1d
   (t(day) 1.0-1.9, 77-147 events). This is the shape of slow trend-following (Hurst, Ooi, Pedersen 2017) and of the
   tsmom rows already run (R-06: gross t 2.6, net negative; H-01 inconclusive), and the geometries are 0.9+
   correlated, so it is one observation, not six. The per-event gain in dollars is real (0.13 ATR4h = about $4.9/oz
   today) and cost is not the constraint (0.004-0.006 ATR4h); the binding limits are count (6-10 events a month a band) and the M3/M4
   account-size gates (README 4.7), not cost.
3. **VWAP 2 sigma and regression-channel 15m reads are null**, in line with the running S1A/B arms.

## 4. Regime and session: does the sign switch? (the part that is new)

Slices of the same events by the signal bar's prior ADX(14) bucket, its 128-bar R/S Hurst tercile (trailing 250-bar
rank), its volatility tercile (ATR/close rank), UTC session (Asia 22-07, London 07-12, NY 12-17, Late 17-22) and the
first 90 minutes after the DST-aware London (08:00 London) and New York (08:30 NY) opens. 231 cells; the table keeps
the ones that decide something. All means in ATR units of the slice's timeframe.

| Event set | Slice | n | mean | t(day) | z vs control | yrs+ |
|---|---|---|---|---|---|---|
| 15m BB20 breakout | Hurst high | 3724 | **-0.108** | -3.8 | -4.2 | 1/8 |
| 15m BB20 breakout | Hurst low | 3742 | **+0.098** | +3.1 | +3.7 | 7/8 |
| 15m KC20 breakout | Hurst high | 2668 | -0.113 | -3.3 | -3.5 | 1/8 |
| 15m KC20 breakout | Hurst low | 2264 | +0.034 | +0.9 | +1.0 | 5/8 |
| 15m DC20 breakout | Hurst high | 4319 | -0.091 | -3.5 | -3.5 | 1/8 |
| 15m DC20 breakout | Hurst low | 3737 | +0.065 | +2.2 | +2.4 | 7/8 |
| 15m DC20 breakout | ADX < 18 | 2973 | +0.092 | +2.7 | +3.0 | 7/8 |
| 15m DC20 breakout | ADX 18-25 | 3598 | **-0.100** | -3.4 | -3.9 | 1/8 |
| 15m DC20 breakout | ADX > 25 | 4784 | -0.029 | -1.1 | -1.3 | 3/8 |
| 15m BB20 breakout | ADX 18-25 | 3439 | -0.086 | -2.9 | -3.3 | 1/8 |
| 15m KC20 fade | Hurst high | 4700 | +0.066 | +2.7 | +3.0 | 6/8 |
| 15m KC20 fade | NY session 12-17 UTC | 3782 | +0.091 | +2.6 | +2.7 | 7/8 |
| 15m KC20 fade | ADX 18-25 | 3531 | +0.076 | +2.7 | +2.9 | 6/8 |
| 15m KC/BB/DC breakouts | London or Asia session | 1963-3912 | -0.013 to -0.064 | -0.4 to -1.7 | | 1/8-3/8 |
| 15m KC/BB/DC breakouts | Late 17-22 UTC | 757-1250 | +0.055 to +0.083 | +1.4 to +1.7 | +1.4 to +2.0 | 5/8-7/8 |
| 15m KC/BB/DC | first 90 min after London or NY open | 736-1231 | -0.07 to +0.01 | |t| < 1.2 | | 2/8-4/8 |
| 1h BB20 breakout | ADX < 18 / Hurst low | 655 / 934 | +0.134 / +0.110 | +1.8 / +1.7 | +1.8 / +2.0 | 7/8 / 6/8 |
| 1h BB20 breakout | ADX > 25 | 1148 | -0.004 | -0.1 | -0.1 | 5/8 |
| 4h KC20 breakout | Hurst low | 180 | **+0.329** | +2.9 | +2.7 | 8/8 |
| 4h BB20 breakout | ADX < 18 / vol low | 116 / 251 | +0.288 / +0.278 | +2.5 / +2.9 | +1.9 / +2.8 | 8/8 / 6/8 |
| 4h KC/BB breakout | Hurst high / ADX > 25 / vol high | 217-343 | -0.01 to +0.12 | |t| < 1.6 | | 3/8-7/8 |

Findings.
- **The breakout continues when it comes from a non-trending base and is exhausted when it comes from a persistent run.**
  Low ADX, low Hurst and (at 4h) low volatility all point the same way on three timeframes: break out of a quiet,
  low-persistence base and the move continues (+0.07 to +0.33 ATR depending on timeframe); break out after a
  persistent run (Hurst high at 15m) and it reverses (-0.09 to -0.11 ATR15, one positive year in eight, three
  geometries). The ADX 18-25 band is also negative at 15m, which is not monotone in ADX and is unexplained; I do not
  hold it as a mechanism.
- **This is the opposite of the textbook "high ADX / high Hurst = trend, take the breakout" rule** at 15m, and the
  "mean-revert when Hurst is low" rule fails too. The first-order reading: the R/S Hurst of the last 128 bars is high
  after a large directional run, so a band breakout in that state is a late entry into an extended move, and a break
  from a flat range is an early one. That is a compression-expansion story (practitioner), not a statement that
  15m returns trend.
- **Sessions are weak.** Breakouts in the Late window (17-22 UTC) are positive (+0.055 to +0.083, 5/8 to 7/8 years,
  z up to 2.0); London and Asia breakouts are negative or zero; the first 90 minutes after either open show nothing
  (consistent with README 6.3: no follow-through from opening ranges). The one session-conditional fade is NY (KC fade
  +0.091 ATR15, 7/8 years), which is the same finding as "Hurst high fade" seen through the clock. I would not build a
  session rule on it.
- **Random-walk null (guard against a construction artefact).** I rebuilt the 15m series three times with the bars
  shuffled (shape relative to the previous close kept, order randomised: no serial dependence, no volatility
  clustering) and ran the same three-band, Hurst-switch composite (section 6, X1): mean -0.006, -0.002, -0.011 ATR15,
  z -0.4, -0.0, -0.6, 3/8, 4/8, 2/8 positive years. The effect is not made by the tercile split or the Hurst code. It
  needs real gold's temporal structure (the cause is untested).

## 5. Multi-timeframe: 4h and 1d band position as a filter for 15m and 1h entries

Context from 4h and 1d Keltner(20, 2 ATR) position, pos = (close - EMA20)/(k ATR), joined to the signal bar by
`asof_join` on `available_utc`. "Agrees" = the event direction equals the sign of pos; "opposes" = it does not;
"stretched against" = pos beyond +/-1 on the other side (the higher timeframe is outside its own band, against the
15m event).

| 15m event | Higher-TF state | n (per month) | mean ATR15 | t(day) | z | yrs+ |
|---|---|---|---|---|---|---|
| BB20 breakout | 4h agrees | 5503 (66) | +0.030 | +1.2 | +1.2 | 5/8 |
| BB20 breakout | 4h opposes | 4787 (57) | -0.057 | -2.1 | -2.1 | 1/8 |
| BB20 breakout | **4h stretched against** | 633 (7.5) | **-0.195** | -3.1 | -3.0 | 1/8 |
| KC20 breakout | 4h stretched against | 261 (3.1) | -0.255 | -2.8 | -2.7 | 1/8 |
| DC20 breakout | 4h stretched against | 668 (7.9) | -0.159 | -2.7 | -2.7 | 1/8 |
| KC / DC / BB breakout | 1d stretched against | 492 / 821 / 750 | -0.21 / -0.16 / -0.14 | -2.7 / -2.7 / -2.2 | -2.8 / -2.4 / -1.9 | 1-2/8 |
| BB20 fade | 1d agrees / opposes | 8746 / 8790 | +0.035 / -0.046 | +1.9 / -2.3 | +1.8 / -2.2 | 6/8 / 2/8 |
| 1h BB20 breakout | 1d agrees / opposes | 1377 / 1207 | +0.084 / +0.022 | +1.6 / +0.4 | +1.9 / +0.6 | 5/8 / 4/8 |
| 1h KC20 breakout | 4h agrees / opposes | 1565 / 275 | +0.046 / -0.071 | +1.1 / -0.6 | | 7/8 / 3/8 |

Findings.
- **A 15m breakout into a stretched higher timeframe fails**: -0.14 to -0.26 ATR15, one year in eight positive, in all
  three band families and both 4h and 1d. Equivalently, the 4h/1d stretch resumes.
- Agree/oppose is a mild filter (about +0.03 vs -0.05): the same sign as the owner's "intraday must agree with the daily
  chart" rule, but each side alone is |t| 1-2.
- At 1h the filter does little (|t| < 1.9 everywhere); the higher-timeframe effect is a 15m phenomenon.

## 6. Squeeze, bandwidth, and range prediction (does the slice beat "range is predictable"?)

TTM squeeze = Bollinger(20, 2) inside Keltner(20, 1.5 ATR); release after at least 6 bars on. Range test: the next 4
bars' high-low range over ATR, release bars vs 300 random draws of the same count. Predictor test: Spearman rho of the
trailing-250 bandwidth percentile with the next 4-bar range in dollars, against the same for the trailing ATR percentile,
and the partial correlation of bandwidth given the ATR percentile.

| TF | squeeze on (share of bars) | releases (per month) | next-4 range, release | control | control SD | direction mean ATR (t) | rho bandwidth pct | rho ATR pct | partial rho, bandwidth given ATR |
|---|---|---|---|---|---|---|---|---|---|
| 15m | 20.4% | 2382 (25) | 2.28 | 2.14 | 0.03 | -0.012 (-0.4) | 0.253 | 0.339 | **+0.012** |
| 1h | 18.5% | 566 (5.9) | 2.22 | 2.16 | 0.06 | +0.107 (+1.6) | 0.001 | 0.092 | -0.061 |
| 4h | 11.0% | 94 (1.0) | 2.17 | 2.22 | 0.13 | +0.274 (+1.8) | 0.068 | 0.119 | +0.012 |
| 1d | 10.2% | 13 (0.14) | 2.09 | 2.11 | 0.27 | n/a | 0.260 | 0.356 | +0.094 |

- **Bandwidth carries no range information beyond the ATR percentile** (partial rho +0.01 at 15m, -0.06 at 1h, +0.01 at
  4h; only 13 daily releases). The squeeze does flag a larger next-4-bar range at 15m (2.28 vs 2.14 ATR, about 4 control
  SD), but that is partly mechanical (ATR is low after a squeeze, which inflates ATR-normalised range) and the ATR
  percentile predicts the dollar range better (rho 0.34 vs 0.25). H-16's HAR forecast is not improved by adding a
  bandwidth or squeeze term on this evidence.
- **Squeeze release direction**: no 15m edge; 1h +0.107 ATR (t 1.6), 4h +0.27 ATR (t 1.8, 94 events): the same sign as
  the generic 1h/4h band breakout (section 3) and no stronger. The "squeeze release" is a branding of "breakout from
  low volatility", which section 4 already measures with ADX/Hurst/volatility terciles.
- Squeeze is on 10-20% of bars; at 15m that is a state flag, not an event.

**Composites formed after reading sections 3-5 (post-hoc, 6 extra cells):**

| Rule (frozen after the fact) | TF | n (per month raw) | mean ATR | t(day) | z vs control | yrs+ | hit |
|---|---|---|---|---|---|---|---|
| X1: union of KC20 / BB20 / DC20 breakouts; trade **with** it when Hurst128 is in its low tercile, **against** it when in its high tercile; skip the middle | 15m | 10,425 (146) | **+0.087** | **+5.2** | +5.4 | **8/8** | 52.4% |
| same | 1h | 2,692 (37) | +0.070 | +2.1 | +2.5 | 7/8 | 52.5% |
| X2: same union; when the 4h KC position is stretched (>= 1) on the other side, trade **against** the breakout (with the 4h stretch) | 15m | 832 (11.8) | +0.146 | +2.8 | +3.0 (control = random bars in the same 4h-stretched state) | 7/8 | 57.8% |
| X2 with the 1d KC position | 15m | 1,036 (15.1) | +0.148 | +2.9 | +3.1 | 6/8 | 57.1% |
| X2 with 4h / 1d | 1h | 34 / 211 | +0.154 / +0.187 | +0.6 / +1.9 | | 6/8 / 5/8 | |

Overlap: half of X2's 4h events (0.50) and 38% of its 1d events fall in Hurst-high, so X1 and X2 are not independent.
X1 is the strongest discovery number in this slice and also the most selected (the Hurst tercile cut is read off three
bands' slices); the permuted-bar null (section 4) reads zero, which is the guard that matters.

## 7. Redundancy (for the 40-feature cap)

Spearman correlation of band positions, (close - mid)/(half-width), 1h (4h is within 0.03):

| | KC | BB | DC20 | DC55 | STARC | LR50 | VWAP |
|---|---|---|---|---|---|---|---|
| KC20x2 | 1 | 0.94 | 0.93 | 0.84 | 0.69 | 0.69 | 0.74 |
| BB20x2 | 0.94 | 1 | 0.93 | 0.69 | 0.75 | 0.79 | 0.81 |
| DC20 | 0.93 | 0.93 | 1 | 0.75 | 0.67 | 0.73 | 0.71 |
| DC55 | 0.84 | 0.69 | 0.75 | 1 | 0.45 | 0.30 | 0.49 |
| STARC | 0.69 | 0.75 | 0.67 | 0.45 | 1 | 0.64 | 0.87 |
| LR50 | 0.69 | 0.79 | 0.73 | 0.30 | 0.64 | 1 | 0.67 |
| VWAP | 0.74 | 0.81 | 0.71 | 0.49 | 0.87 | 0.67 | 1 |

KC, Bollinger and Donchian-20 are one feature with three names (0.93-0.94). Keep one of them (the existing
`bb_pctb_20` or `donchian_pos_20` already covers it), plus the least-correlated additions: DC55 (0.30-0.84), LR50 (0.64-0.79)
and the session VWAP position (0.67-0.87). Moving-average envelope and STARC add nothing beyond KC.

## 8. Bands as exits and stops (note for the exits researcher)

Diagnostic only, not a rule test. Entries = Donchian-20 breakouts on 1h and 4h, one per 24 bars, compared with
random entries of the same count and direction mix (40 draws). Exits: fixed 4 bars, or a 3 ATR chandelier from the
highest high since entry (frozen entry ATR, close-through trigger, 24-bar cap). MFE/MAE over the next 24 bars.

| TF | n | fixed-4 event / control | chandelier-3 event / control (SD) | MFE24 event / control | MAE24 event / control |
|---|---|---|---|---|---|
| 1h | 1293 | +0.046 / -0.008 | +0.176 / +0.036 (0.095) | 3.10 / 2.91 | 2.91 / 2.92 |
| 4h | 368 | +0.192 / +0.016 | +0.271 / +0.078 (0.165) | 2.95 / 2.91 | 2.55 / 2.91 |

- The breakout entry adds about +0.14 ATR over random entries at 1h (chandelier, 1.5 control SD) and +0.19 at 4h (1.2
  SD): not significant at these n, and the same entry effect appears with a fixed exit. MFE barely differs from
  random (3.10 vs 2.91 ATR at 1h); the 4h event MAE is lower (2.55 vs 2.91).
- The chandelier mean (+0.18 / +0.27) is higher than the fixed-4 mean (+0.05 / +0.19) because it holds up to 24 bars, not
  4: the horizons differ, so this is **not** evidence that the trail beats a fixed exit. A fair test needs a
  fixed-horizon, matched-hold control (the exits researcher's V1u already specifies a trail vs fixed-2R comparison on
  the same entries: `prereg-2026-10-25-high-volume.md` I2).
- For the "Keltner middle trail" (EMA20 as stop on a 4h/1d trend): not run here. By construction it is the 20-EMA
  exit of the tsmom family; it is only worth an exploration arm when a 4h/1d entry exists (section 9 F-3).

## 9. Candidate hypotheses, ranked (two proposed for a Q1 free-reserve slot, the rest as features or dropped)

K = 145; gate 1 needs a model-filtered t of about 4.3; the six free reserve slots are shared across the whole
programme. My ranking by expected information per slot:

### F-1  Base breakout vs extended breakout (Hurst switch), 15m   *(slot candidate 1)*
- Mechanism: a band breakout from a quiet, low-persistence base is the start of an expansion (compression/expansion;
  Crabel 1990, Bollinger 2001: practitioner), while the same breakout after a persistent run is the late leg of an
  extended move that mean-reverts as its liquidity is exhausted. Evidence grade D (practitioner) for the mechanism, C for
  the discovery number. Not a trend/ORB/volatility claim, so it does not contradict README 6.3/6.7.
- Already tried?: new in this form. R-01 (unconditional BB fade) and R-04/R-15 (unconditional breakout) were null, as
  section 3 confirms; **new: regime-conditional sign switch**, three geometries, permuted-bar null clean.
- Timeframe and clock: 15m decision, bar-close signals, UTC-independent (no session clock). 1h as a sister timeframe.
- Entry: on the close of 15m bar i, if the close crosses outside any of KC(20, 2 ATR), BB(20, 2), Donchian-20 (prior 20
  bars) from inside, and `hurst_128` (existing feature, trailing 250-bar rank) is in its low tercile, trade **with** the
  break; high tercile, trade **against** it; middle, skip. Enter at the next bar open.
- Side: both. Exit: time exit after 4 bars (1 h) with a stop at 1.0 ATR15 (about $8, about £6, at the £6.50 scalp cap).
  The stop distance is **not tested** (no MAE-per-event table was built); one position at a time: yes.
- Control: random bars, same count and long/short mix, same slice; plus a permuted-bar null (done; clean).
- Discovery evidence: section 6 X1, n 10,425, +0.087 ATR15, t(day) 5.2, 8/8 years, hit 52.4%, 146 events a month; the
  three components alone: BB brk Hurst-high -0.108 / low +0.098, KC -0.113 / +0.034, DC -0.091 / +0.065. Looks behind
  it: 336 cells, composite post hoc, so deflate heavily; I would carry it as t of about 3-4 until confirmed.
- Cost check: gross 0.087 ATR15 = $0.70/oz at ATR15 $8, against the broker round trip $0.144 central (4.8x) and $0.23
  conservative (3.0x). **Against the README research-prior cost (0.25-0.45 ATR15 in 2010-2017) it is 0.2-0.35x and
  fails**, and only about 0.5x-1x in 2025 terms. So it is cost-viable only at the current broker, which is a demo
  learning argument and not a gate-1 argument.
- Expected events: 10,425 in the 8-year discovery half (about 1,300 a year); the full 2010-2025-09 count owed (counts
  only). Above the 1,000-event intraday floor.
- Kill rule (0-trial, on the confirmation half only when the director funds the read): X1 mean below 0.03 ATR15, or
  fewer than 5 of 8 confirmation years positive, or the two arms (low-Hurst continue, high-Hurst fade) not both
  positive.
- Read rule: **continue** if gross >= 0.05 ATR15 with day-clustered t >= 3 and the broker-cost net per trade > 0 in at
  least 5 of the confirmation years; **stop** otherwise; **inconclusive** in between. (Frozen now.)
- Route: exploration arm on `icm-probe` (146 signals/month is about 5-7 a day: the owner's volume directive), plus the
  three flag features below for H-62. Q1 free-reserve trial only if the director accepts a read that is not gate-1
  eligible (rule-only rows can read at most "continue").
- Prior P(continue): about 10-15%. It passed a permuted-bar null and 8/8 years, but it is a post-hoc cut, the 2010-2017
  cost era is 3-5x the edge, and Kurth et al. 2026 say short-term trend/reversal on small-tick contracts has been gone
  since about 2009 (the 15m Hurst-high fade is a reversal).

### F-2  Break into a stretched higher timeframe fails (HTF resume), 15m   *(slot candidate 2, weaker, overlaps F-1)*
- Mechanism: a 15m close outside its band while the 4h (or 1d) is already outside its own band on the other side is a
  counter-trend bounce inside a stretched trend; the trend's holders and the "daily chart first" participants re-assert
  (owner note 6, `box-mfi-families-2026-10-18.md`; Elder's triple screen: practitioner). Grade D.
- Already tried?: new. The owner's strategies use the daily chart as a filter but no run tested a stretch-against rule.
- Timeframe: 15m trigger, 4h or 1d Keltner position (`asof_join`, `available_utc`).
- Entry: 15m close outside KC/BB/DC20 against a 4h KC position beyond 1 (or 1d), trade **with** the 4h stretch. Exit: 4
  bars, stop 1.0 ATR15 (untested). Side: both. One position: yes.
- Control: random bars drawn from the same 4h-stretched state, same direction mix (done).
- Discovery evidence: section 6 X2, 832 events (4h) / 1,036 (1d), +0.146 / +0.148 ATR15, t(day) 2.8 / 2.9, 7/8 / 6/8
  years, hit 58%. 12-15 a month (cell counted among 336).
- Cost check: 0.146 ATR15 = $1.17/oz today, 8x the broker central cost; research prior 0.5-0.6x (fails).
- Expected events: about 830-1,040 in 8 years, **at or below the 1,000 floor**; roughly 100-125 a year per version.
- Kill rule: mean below 0.05 ATR15 or under 5/8 positive confirmation years. Read rule: as F-1 with |t| >= 2.5 (n is
  smaller). Route: exploration arm and a **filter feature** for F-1 and for every 15m breakout arm (S2A/B, box); its
  half overlap with F-1 means I would not spend a slot on both: the director should pick one.
- Prior P(continue): about 8%.

### F-3  4h / 1d band-breakout continuation (Donchian-55, Bollinger, regression channel)   *(feature or retire-only read)*
- Section 3: +0.09 to +0.15 ATR4h, t(day) 1.8-2.7, 6-10 events a month, 4h Hurst-low KC breakout +0.33 ATR4h (180 events,
  8/8 years). It is the R-06/H-01 family (tsmom) with a band as the trigger. Not a new edge; it needs the M3/M4
  account size and falls under the README's "at most a retire-only read or a feature". **Drop as a trial; keep the 4h/1d
  band state as H-02/H-62 features.** P(continue) about 5%.

### Dropped or demoted
- **F-4 TTM squeeze as a rule: drop.** Bandwidth adds nothing to the ATR percentile (partial rho +0.01), release
  direction is the generic 1h/4h breakout; 15m no edge. Feature only (`squeeze_on`).
- **F-5 Band mean-reversion fades (Bollinger, Keltner, VWAP, STARC): drop.** Non-positive from 1h, zero at 15m, BB
  fade -0.001. The one positive pocket (KC fade, Hurst high or NY session) is F-1's other arm.
- **F-6 Chandelier / Keltner-middle trail as an entry-agnostic improvement: no evidence here.** Hand to the exits
  researcher with the matched-hold control requirement (section 8).
- **F-7 MA envelope, STARC, regression channel, VWAP bands as separate hypotheses: drop.** Correlated with KC/BB (0.64-0.87),
  nothing unique. The 1h regression-channel breakout (+0.093, 8/8 years, t 2.3) is the 1h member of F-3.
- **F-8 Session open as the discriminator for band use: close.** London/NY-open windows read nothing (|t| < 1.2), Asia
  and London breakouts weakly negative, Late weakly positive; no session rule is warranted.

## 10. ML feature candidates (40-feature cap; fold-internal selection only)

Existing (do not re-add): `bb_z_20`, `bb_pctb_20`, `donchian_pos_20`, `compression_8_96`, `range_width_*_atr`,
`adx14`, `hurst_128`, `er_30`, `dist_vwap48_atr`. New, all causal on closed bars, at most 8, ordered by value:

| # | Feature | Definition | Why | Pairwise redundancy |
|---|---|---|---|---|
| 1 | `htf4h_kc_pos`, `htf1d_kc_pos` | (close - EMA20)/(2 ATR) of the last closed 4h/1d bar, via `asof_join` on `available_utc`, signed | the F-2 state and the owner's daily-chart rule (2 features) | low vs 15m features |
| 2 | `hurst_base_flag` | sign: +1 Hurst128 low tercile, -1 high tercile (trailing 250 rank), else 0 | the F-1 switch as a flag | built on `hurst_128` |
| 3 | `brk_event_dir` | +1 / -1 / 0: first close outside KC/BB/DC20 (the union) | the event itself | collinear with `bb_pctb_20`; keep as the event flag only |
| 4 | `kc_pos_20_2` | (close - EMA20)/(2 ATR14) | Keltner distance, the only band not already present (ATR-based, unlike BB's SD) | 0.94 vs `bb_pctb_20`; add only if not both |
| 5 | `squeeze_on`, `bars_since_squeeze_release` | BB inside KC(1.5) flag; run length | compression state; bandwidth itself is redundant with `compression_8_96` and ATR percentile | low |
| 6 | `lr50_z` | (close - fitted)/SE of a 50-bar OLS line | least-correlated band position (0.64-0.79) and the 1h regression breakout read | 0.69-0.79 |
| 7 | `vwap_sess_sd_pos` | (close - session VWAP)/(2 weighted SD), after 6 bars into the day | the S1A/B arm's state | 0.74-0.87 |

Net adds: 8 columns if all are taken; a defensible minimal set is `htf4h_kc_pos`, `htf1d_kc_pos`, `hurst_base_flag`, `brk_event_dir`,
`squeeze_on` (5). Everything else is redundant at 0.9+. Do not add the moving-average envelope or STARC.

## 11. Verdict per topic

| Topic | Verdict |
|---|---|
| Keltner, Bollinger, Donchian, regression channel, STARC, VWAP bands: unconditional use at 15m | **close** (0 trials): null on both uses |
| The same at 1h | **close**; breakouts +0.02 to +0.09 ATR, t < 2.3, R-04 stands |
| The same at 4h / 1d | **feature** (4h/1d band state, F-3); retire-only read at most |
| TTM squeeze / bandwidth | **close as a rule; feature** (`squeeze_on`); no range information beyond ATR percentile |
| Band fades / mean reversion | **close** (R-01 stands); its one pocket is F-1's second arm |
| Regime-switched band breakout (Hurst, ADX) | **arm + possible slot** (F-1) |
| Higher-timeframe stretch filter | **feature + arm; one slot between F-1 and F-2** |
| Sessions as discriminator | **close** |
| Chandelier / Keltner middle trail | **hand to exits-researcher** with a matched-hold control; no evidence from this slice |
| MA envelope, STARC | **close** (redundant) |

## 12. Looks, and what I would stop doing

- Looks: 336 counted cells on gold (51 base, 231 slices, 3 squeeze, 45 filter, 6 composites; 46 with |z| > 2
  against 15 expected, 12 survive BH at q 0.10, all but two from one finding), 3 permuted-bar nulls, 2 exit rows, 3
  correlation matrices. No confirmation-half or holdout bar was loaded.
- Suggested K charge: +15 effective (collapse correlated bands and slices to independent axes), or +1 if only the
  confirmation read of F-1 is funded and counted as one trial.
- I would stop: testing any band as an unconditional 15m rule, TTM-squeeze branding of low-volatility breakouts, and
  stand-alone moving-average envelope or STARC work.
- Open items for the director: (a) the full 2010-2025-09 event counts of F-1/F-2 (counts only), (b) an MAE-per-event table
  for the 1.0 ATR15 stop before sizing (the £6.50 scalp cap means a $8 stop is the ceiling at today's ATR), (c) whether
  F-1's regime flag also helps the existing box and ORB arms (zero trials, an arm-side split), (d) whether a read that
  can say at most "continue" (rule-only) is worth one of six free slots at 10-15% prior.
