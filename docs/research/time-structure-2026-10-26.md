# Time structure, levels and indicators in XAUUSD: synthesis and ranked programme (2026-10-26)

Research director. Synthesis of the frame and the eight slices in `time-structure/`. No trial was run, no budget was
spent and no registry row was written. The pre-registration this file proposes is
`prereg-2026-10-26-time-structure.md`.

**Owner directives (2026-10-26, verbatim; all answered here):**
- "there are three sessions in a day for trading, each open and close and their role in trading strategies, 30 day,
  60 days 90 days, and other time related strategies, the research team should look at this and devise strategies and
  also use state of the art techniques"
- "add fibnocci series, wavelet thoery, oscillators indicators and related techniques and tools"
- "volume indicators, trends, order blocks, smart money trading"
- "and relevant methods"
- "Keltner band and related ideas"
- "traders trade on the strategies i shared such as range breakers but not limited to, so how are you ruling these out"
- "opening closing sessions, previous day low, hig, week high low, monthely etc"
- "strategies developed should be backtested on historical data to analyse learning, improve it and once confidence is
  high then it will be able to predict so the model is in contious learning mode"
- "if required multimodels caould be used"

| Slice | File | Commit |
|---|---|---|
| Frame, director measurements | `time-structure/README.md` | this branch |
| A sessions, opens, closes, fixes, DST | `time-structure/sessions.md` | c2f76569 |
| B 30/60/90 d, calendar, events, seasonality | `time-structure/cycles.md` | 295a98fd |
| C Fibonacci, wavelets, Ehlers, Hurst, spectral, EMD | `time-structure/fib-wavelets-cycles.md` | cfc4ce07 |
| D oscillators, trend, tick volume, VWAP, profile | `time-structure/oscillators-trend-volume.md` | f8d8dcff |
| E order blocks, FVG, SMC, Wyckoff, market profile | `time-structure/smc-orderblocks.md` | 1c51f247 |
| F Keltner, Bollinger, Donchian, squeeze, bands as exits | `time-structure/volatility-bands.md` | 5ebd2b83 |
| G key levels (session, day, week, month, round numbers) | `time-structure/key-levels.md` | 371e7b9a |
| H combinations, portfolio, allocator, meta-labelling | `time-structure/combinations.md` | 6c13c0ae |
| Learning loop and multi-model rules | `time-structure/learning-loop.md` | this branch |

## 1. The answer in one screen

1. **What is predictable in gold's clock and calendar is range, not direction.** All of these forecast the size of
   the move, on top of RV20:
   - the NY 08:30-13:30 window (45% of the day's variance; README 6.1). This is a variance fact; the frozen
     `ny_pit` stratum of H-70 is 08:00-13:30 New York, as slice H measured it;
   - FOMC and NFP days (+0.37 to +0.40 log points);
   - volatility persistence (RV20 IC 0.40-0.50);
   - wavelet scale energy (t 5.6-6.3);
   - a slow cycle of about 103 days in |returns|.
   This feeds priority 1, opportunity-day detection. It costs 0 trials and goes into H-16, H-54 and the H-62
   features (section 5, H-73).
2. **Direction.** Almost every directional idea in the owner's list was measured on 2010-2017 against a randomised
   control, net of cost. In the form tested, each one either did not beat random or did not pay the research cost
   (section 4 lists them one by one). The wording is deliberate: *this version, tested this way, did not beat random
   after cost*. That is not a claim that the idea can never work.
3. **One 15m candidate survived every 0-trial check: F-1.** It is a band breakout switched by the 128-bar Hurst
   tercile: trade with the break from a low-persistence base, against it after a persistent run.
   - Evidence: +0.087 ATR15, day-clustered t 5.2, 8/8 years, n 10,425, a permuted-bar null of about 0.
   - It reproduced in an independent build (H) and is not a volatility or trend-length proxy (README 6.11).
   - The New York pit window concentrates it: +0.180 ATR15, t 4.6.
   - At 4.8 times the broker cost it pays; at the research prior (0.2-0.35 times cost) it does not.
   - It is the only candidate that can trade at £1,000 (15m, a stop of about $8 ≈ £6 at 0.01 lot).
4. **The cost model decides most verdicts, and it is not wrong** (quant audit). On 2010-2017 prices a 15m stop of
   $1-2 pays 0.4-0.9 R per round trip. Today's broker pays 0.02-0.05 R at an ATR15 of $8.
   - So the deciding unknown for F-1, G-1 and D-1/D-2 is **the cost at their own signal times**, which only demo
     fills can measure (`learning-loop.md` stage 3).
   - Every retired family died on gross t below 2, so none flips under cheaper costs. R-06 died on cost and still
     cannot clear the DSR bar.
5. **Honest K.**
   - Registry K is **145** (2026Q4: 69/69 used).
   - This programme took about **2,460** raw looks. I charge about **187 effective** (section 7; G at its 51
     axes, quant condition 4).
   - With the conservative default (owner-confirmed: discovery looks are added to every later trial), gate 1 then
     needs a model-filtered t of about **4.6** (K ≈ 332, or 337 with the 5 arms).
   - Slices A, B and D read data they should not have (section 7.2). Those candidates lose their confirmation
     credit; A's session-clock family loses the holdout too.

## 2. Findings from our data (discovery half 2010-2017 unless stated)

### 2.1 Sessions, opens and closes: what each does (A, README 6.1-6.5, 6.9)

| Event (local clock) | Role measured | Direction usable? |
|---|---|---|
| Tokyo open 08:00/09:00 | Asia range formation; no activity jump (0.95x control) | no: reversal −0.49 bp (t −1.1) |
| SGE AM 10:15 Shanghai | quietest anchor | no (+0.43 bp, t 1.9) |
| London open 08:00 | mild expansion (1.18x); takes an Asia extreme 68%/68% (both 39%, neither 2.4%) | no: first take-out continues +0.06 Asia ranges (t 1.5); the break itself is no better than random |
| LBMA AM 10:30 / PM 15:00 London | no spike of their own (the 15:00 spike is 10:00 New York) | no: AM reversal −0.6 bp (t −2.3), below any cost |
| COMEX open 08:20 / NY data 08:30 | **the activity event of the day** (2.2x control; 1.8x jump at 08:20) | no: persistence 0.00 bp |
| US equity open 09:30 | second burst (1.9x) | no |
| COMEX settle 13:30 NY | volatility drops (0.76x); reversal −0.83 bp (t −2.9) | no: about 0.1 R or less |
| London close 16:30 / NY close 17:00 | decay; quotes stop 17:00-18:00 NY; spread 1.4x for 5-30 min after the reopen | cost rule only: no tight stops 16:55-17:00 and 18:00-18:30 NY |
| Opening ranges, 24 ORB variants (London/NY, 15-60 min, two stops, two exits) | the range holds one session extreme 47-65% of the time | no: net −0.12 to +0.03 R, about random-start ORB (R-02 again) |
| Session drift: Asia +2.4 bp/day (t 2.5), London AM −2.5 (t −2.4), NY +0.0 | Asia-up / West-down, faint, inside the random-window spread at the 86th/11th percentile | no: H-04 (#44) stopped on cost |
| DST | activity follows **each city's own clock**; in the UK/US gap weeks the NY peak stays on New York time | rules must use local clocks (`session.py` v2 does; UTC arm windows do not) |

Cost by New York hour (Dukascopy spread / ATR15): 08-11 NY is the cheapest and most active window (0.10-0.15 in
2019-2025; 0.10-0.13 in 2025). Asia and the NY close are 1.5-2 times dearer in ATR (README 6.5). At the broker
($0.144 central), cost is 3-4% of a 15m range in every session: volatility chooses the session, not cost.

### 2.2 Multi-day windows and the calendar (B, README 6.6-6.7)

| Window / effect | Result |
|---|---|
| 30/60/90-day TSMOM, reversal, range position, new highs | no edge: all abs(t) < 1.2 (non-overlapping or Newey-West); variance ratios 0.85-1.00, abs(z) < 0.6; no price cycle at 20-120 days (periodogram p 0.64) |
| Volatility by window | predictable: RV tercile -> next-5-day abs(ret) 149-154 / 157-171 / 210-214 bp; abs(returns) cycle about 103 days (p 0.012) |
| Friday | +14.1 bp/day (t 2.7; within-year permutation p 0.003; 7/8 years); not significant after about 200 looks (Bonferroni p about 0.13); **B saw it persist on 2018-25** (no confirmation credit) |
| Turn of month, month and quarter end, first day of year | nothing (p 0.17-0.99) |
| OG expiry, FND, LTD (rule-derived dates) | noise-shaped (signs reverse next day); no range effect |
| Pre-FOMC drift; FOMC even weeks | absent (p 0.56; R-08 confirmed on our data) |
| Post-FOMC day | −28 to −40 bp in 2010-17; **B saw it fail on 2016-25** (dropped) |
| FOMC / NFP **range** | +0.40 / +0.37 log points over RV20 (t 3.0 / 2.7); CPI and PCE none |
| Seasonality: August, Jan-Feb, Diwali, Chinese New Year | August t 2.4 among 12 months; festivals have 8 events each (cannot be powered) |

### 2.3 Levels, bands, structure (C, E, F, G)

| Object | Control | Result |
|---|---|---|
| Fibonacci retracements and extensions (Asia, prior day, 30/60/90 d; 15m-1d) | distance-matched random levels | 0 of 26 cells survive BH (min q 0.52); prior-day Fibonacci levels **worse** than random fractions (−0.032 ATR, CI below 0) |
| Fibonacci time zones | neighbouring offsets | p 0.07-0.33, no excess |
| Order-block retest 15m (causal, no repaint) | zone identity shuffled; random bracket at the same hour | gross +0.112 R (t 2.8), but random brackets earn +0.03 to +0.05; OB adds +0.04 to +0.08, CI includes 0; net prior −0.31 R |
| FVG as magnet / range predictor | random level at the same distance; matched displacement bars | +2 pts revisit (below the 5-pt bar); no range beyond the displacement bar |
| Key-level breaks (PDH/PDL/PDC, PWH/PWL, session extremes, opens, $10/50/100) | random level, same distance and window | break share +3 to +8 pts over random (stops cascade, Osler); no magnet; **resting-limit retests worse than random** (−0.03 to −0.06 R) |
| Band breakouts / fades 15m (KC, BB, DC, STARC, LR, VWAP) | random bars, same mix | null (abs(t) < 1.5); from 1h up breakouts +, fades − (the tsmom shape) |
| Hurst-switched band breakout 15m (F-1) | random bars; permuted-bar null | **+0.087 ATR15, t 5.2, 8/8**; NY pit +0.180 (t 4.6) |
| First touch of a 30-day low, daily horizon (C-1) | random-time entries; random levels | +0.277 R (t 3.85, 310 events, 15/yr non-overlapping), carried by 2013 and 2015 |
| Value area 80% rule | random areas | 83 events in 8 years (below the floor) |
| Sweep of Asia/prior-day extremes, reversal | random levels | sweep equals control; #88 gross −0.028 R |

### 2.4 Oscillators, trend, volume (D)

- **Oscillator fades (RSI, Stochastic, Williams %R, CCI, MFI) by session:** no gross edge at 15m and the wrong sign
  at 1h. The within-day IC is an artefact: −0.135 on a random walk (README 6.10).
- **MACD / RSI divergences:** the sign flips across 15m, 1h and 4h.
- **ADX > 25:** no selection value.
- **OBV, session-VWAP fades, POC/value area:** closed.
- **Tick volume:** about 37-64% hour-of-week shape and ρ 0.33-0.36 with range after that. It is an activity and
  volatility proxy, not traded size. Its feed units drift 4 times across years. Whether it tracks real COMEX volume
  is **unmeasured**: we hold no COMEX volume (the data survey decides).
- **Tick volume and ADX as range forecasters:** add +0.0003 R² at 1h and +0.003 at 4h beyond ATR and the clock.
- **Two weak arm candidates.** Neither reproduced in H's build:
  - D-1, Asia trend persistence at 1h: +$0.64, t 2.7, 4.4 times the broker cost.
  - D-2, CMF20 at 1h: +$0.59, t 3.2.

### 2.5 Combinations and the allocator (H)

- **Streams:** 12 streams have mean pairwise ρ −0.000 (max 0.19). An equal-risk book beats the average member, not
  the best one (F1, Sharpe 1.83 gross / 1.45 measured).
- **The pre-specified, unbiased book** (E1, V1, O1, TS) reads 0.82 gross, against a null p95 of 0.52. That is 0.37
  at measured cost and negative at the research prior.
- **The rule-table allocator has never been tested against a control.** H's stylised test (0.47 against 0.94 for
  shuffled weights) is **undetermined** (architect): day-level regime, no calendar, a single-day shuffle, and F1
  selected in-sample. The proper test is backlog item 111.
- **Meta-labelling F-1:** OOF AUC 0.557 against a null of 0.502, but the top-half lift is within the null. It is a
  ranker only (R-07, H-62).
- **Reproduction flags:** H's rebuilds of A-1 (sign flipped), D-1 (null) and C-1 (a daily proxy) did not reproduce.
  H used $0.75/oz, not the audit's $1.07. Reconciliation is briefed (section 9).

## 3. State of the art (survey; knowledge base extended in `knowledge-base/gold-specific.md` section 7)

Sources fetched by the slices are marked as they did. *(unverified)* means cited from memory or a secondary source.

| Method | Key sources | What it implies here | Tested here |
|---|---|---|---|
| Intraday momentum after the first half-hour | Gao, Han, Li, Zhou 2018, *JFE* 129(2) (checked by A); Baltussen, Da, Lammers, Martens 2021, *JFE* 142(1) (SoA) | needs a close auction and gamma hedging; gold's analogue is the COMEX settle | A: one pair of six at t 2.7 (NY 08:30 -> 13:30), +0.73 bp; not reproduced by H; feature only |
| Overnight vs intraday decomposition | Lou, Polk, Skouras 2019, *JFE* 134(1) (checked by A) | a cross-sectional effect; it does not transfer to one asset | A: overnight +0.88 bp (t 0.6); no tug of war |
| Noise-area intraday breakout with trailing exits | Zarattini, Aziz, Barbon 2024, SFI 24-97 (checked by A) | H-17's basis | not re-run; H-17 is Q1 O-2/O-3 |
| Time-series momentum across lookbacks | Moskowitz, Ooi, Pedersen 2012, *JFE* 104(2); Hurst, Ooi, Pedersen 2017, *JPM* 44(1) *(volume unverified)*; Kurth et al. 2026 arXiv 2607.01550 (short-term trend gone on small-tick contracts) | it works diversified and slow; a single gold series at 30-90 days shows none | B, C, F: 30/60/90 null; 4h/1d band breakouts positive but few (the R-06/H-01 lane) |
| Variance ratio, Hurst, fractal regime | Lo and MacKinlay 1988, *RFS* 1(1); Lo 1991, *Econometrica* 59(5) (R/S bias) | the R/S level is a biased memory estimate; as a state it can still sort events | C: Hurst level equals its shuffled null; F/README 6.11: the tercile sorts breakouts and is a short-scale autocorrelation state |
| Regime models (HMM, Markov switching) | Hamilton 1989, *Econometrica* 57(2); Nystrup, Madsen, Lindström 2017, *J. Forecasting* *(unverified)* | a regime gate must be fitted inside the folds; regimes forecast volatility far better than direction | H: correlation structure is stable across vol/ADX regimes, only the Sharpe level moves, so size by regime rather than switch families; HMM not fitted (brief M-2) |
| Wavelets (MODWT, coherence) | Percival and Walden 2000 (MODWT) *(unverified)*; causal boundary handling as in C 4.1 | centred or boundary coefficients look ahead; causal MODWT is honest | C: no directional IC; scale energy predicts volatility beyond RV20 (feature) |
| Ehlers cycles (Hilbert, roofing, sinewave), Goertzel, EMD/HHT | Ehlers 2013 *Cycle Analytics* (practitioner); Huang et al. 1998, *Proc. R. Soc. A* 454 *(unverified)* | EMD end effects are large; the dominant cycle is scale-free | C: closed; roofing amplitude is a feature |
| Stop-loss clustering at levels and round numbers | Osler 2003, *JF* 58(5); Osler 2005, *JIMF* 24(2); Kavajecz and Odders-White 2004, *RFS* *(unverified)*; Aggarwal and Lucey 2007 (gold barriers) | levels break more than random; take-profit clusters cause bounces | G: break share +3 to +8 pts; no bounce; retests worse than random |
| Technical-rule data snooping | Sullivan, Timmermann, White 1999, *JF* 54(5); Bajgrowicz and Scaillet 2012, *JFE* 106(3) *(unverified)*; Harvey, Liu, Zhu 2016, *RFS* 29(1); McLean and Pontiff 2016, *JF* 71(1) | discount a lone t of 2-3 heavily; t > 3 for new factors; about 50% decay after publication | applied: every slice reports its looks; K charged in section 7 |
| Pre-FOMC drift; FOMC cycle | Lucca and Moench 2015, *JF* 70(1); Kurov, Wolfe, Gilbert 2021, *FRL* 40; Cieslak, Morse, Vissing-Jorgensen 2019, *JF* 74(5) | equities only, decayed after 2015 | B: absent in gold |
| Announcement volatility | Andersen, Bollerslev, Diebold, Vega 2003, *AER* 93(1); Cai, Cheung, Wong 2001, *JFM* 21(3) | range, not direction | B: FOMC/NFP add to RV20 |
| Meta-labelling, conformal filters | López de Prado 2018, *Advances in Financial ML*, ch. 3; Angelopoulos and Bates 2021, arXiv 2107.07511 (conformal) *(unverified)* | a filter cannot create an edge; conformal gives coverage, not edge | H: F-1 meta AUC 0.557, no lift; conformal is a sizing/abstention tool for H-62 once a primary passes (ML brief) |
| Time-of-day-aware ML with fold-internal selection | López de Prado 2018 ch. 7 (purged CV); our `discovery.py` | session and clock features enter as columns, selected inside the folds | features offered by A-H (section 5, H-73), 40-cap per model |
| Volume / tick volume | Jones, Kaul, Lipson 1994, *RFS* 7(4) *(unverified)*; FMG DP 265 (tick vs traded volume, D) | number of trades, not size, drives the volume-volatility link | D: proxy for activity only; COMEX volume needed (data survey) |
| Wyckoff, market profile, SMC (OB, FVG, BOS/CHoCH, kill zones) | practitioner only; no peer-reviewed test found (E, KB README finding 4) | testable only with no-repaint definitions and random controls | E: OB/FVG/value area closed against controls; hindsight inflates OB/FVG win rates by +0.9-1.1 R on a random walk |

## 4. How each idea was ruled in or out (the owner's question)

Columns:
- **Died on** is *signal* (no gross edge over its randomised control), *cost* (gross over control, net negative at
  the research prior), *count* (too few events to read) or *seen* (data contaminated for that rule).
- **Rescue** says whether a range filter (H-16/H-73), an exit design (V1u runner, partial + trail) or cheaper
  measured costs could change the verdict.

| Idea (version tested) | Gross vs control | Died on | Rescue? |
|---|---|---|---|
| Opening-range breakout, London/NY, 24 variants (A) | about random-start ORB; net −0.12..+0.03 R | signal | range filter + runner: that is H-17 (Q1 O-2/O-3), not a new row |
| Asia-range break chased in London (A, E) | +0.06 Asia ranges, no better than random | signal | no |
| Failed Asia-range break fade (A-1) | −2.8 bp, 2.2 control sd, n 384; **H's rebuild flipped sign** | count (48/yr) + not reproduced + holdout seen | **closed** (evaluator condition 8) |
| Fix / COMEX-settle / Tokyo-open reactions (A, README 6.2) | 0.04-0.07 ATR15 | cost | no: below even broker cost after overshoot |
| Session drifts, Asia/West (A; H-04 #44) | +0.043 R (t 3.1) | cost | cheaper fills could; H-04's verdict stands (no re-test without a pre-registered cost-era argument) |
| 30/60/90-day momentum, reversal, range position (B, C) | null | signal | no |
| Friday long (B-1) | +14 bp, t 2.7, about 200 looks | seen (2018+) | forward-only arm |
| Turn of month, expiry, FOMC/NFP direction, seasonality (B) | null or reversing | signal / count | no |
| Fibonacci levels, extensions, confluence, time zones (C, README 6.8) | not better than random (prior-day worse) | signal | no |
| Wavelet / Ehlers / Goertzel / EMD direction (C) | IC about 0; EMD end effects | signal | no (energy kept as a range feature) |
| Oscillator fades and divergences, any session (D, README 6.10) | 0 at 15m, wrong sign at 1h | signal | no |
| ADX gate, OBV, VWAP fades, POC/value area (D, E) | null / 83 events | signal / count | no |
| Order-block retest 15m (E-1) | +0.04..+0.08 R over random brackets, CI includes 0 | signal (over control) | no as a rule |
| FVG, BOS/CHoCH, premium/discount, kill zones, inducement, breaker/mitigation, supply/demand, EQH/EQL (E; R-13) | revisit +2 pts; rest discretionary or duplicates | signal | features only |
| Key-level retest limits and fades (G-6; #88) | worse than random levels | signal | no |
| Key-level break-close, all families (G) | +0.02-0.03 R attributable to the level; at 15m and 4h the real-minus-control CIs include 0 | **signal** (evaluator condition 6) | the one cell whose CI clears 0, 1h Asia hours, goes to an arm (G-1); 4h Asia is a pre-specified secondary in O-12 |
| Band breakouts and fades, unconditional 15m-1h (F) | null at 15m; 1h breakouts ≤ +0.09 ATR | signal | regime switch: **F-1** |
| 4h/1d band and 30-day breakouts (F-3, C-1) | +0.09-0.28 | count (and the M3/M4 size gate) | merged into the slow-trend lane (H-74) |
| TTM squeeze (F-4) | no information beyond the ATR percentile | signal | feature |
| Owner box break+close+MFI 15m (H-50, #89) | +0.030 R, t 1.61 | signal (and cost) | 1h re-read is H-53 (Q1) |
| Stacking filters, meta-labelling F-1, inverse-variance books (H) | no lift beyond the null | signal | no |

## 5. Ranked hypotheses (north-star rank; each fully specified in the pre-registration where it has a rule)

Ranking criteria, in order:
1. KPI reach at £1,000 (15m first).
2. Evidence after controls.
3. Gross edge against both costs.
4. Information per slot.
5. K cost.

| Rank | ID | Hypothesis | TF | Evidence (discovery) | Cost check | Events | Route | Prior |
|---|---|---|---|---|---|---|---|---|
| 1 | **H-73** | Time-structure range features: FOMC/NFP day flags, pit-window and hour-of-week activity, wavelet scale energy (j3 1d, j5/j7 1h), rv30/rv90, range-used vs forecast (G-4 veto), displacement bar, tick z (hour-of-week); ≤ 12 columns as one group into H-16 refreshes, H-54's sealed log and H-62 v2 | daily / 15m | FOMC/NFP +0.40/+0.37 over RV20 (t 3.0/2.7); wavelet t 5.6/6.3; range-used veto −0.16 R (n 159) | n/a (selector) | daily | feature group (K_eff +1); serves priority 1 | 0.5 as a feature |
| 2 | **H-70** | **F-1 Hurst-switched band breakout, 15m** (with the NY-pit stratum H-1 and the F-2 stretch flag recorded, not traded separately) | 15m | +0.087 ATR15, t 5.2, 8/8; NY pit +0.180 (t 4.6); permuted null 0; reproduced by H; not a vol/trend proxy (6.11) | broker 4.8x (central), 3.0x (conservative); research prior 0.2-0.35x | about 1,300/yr; about 5-7 a day | **arm F1A + control F1C now**; Q1 confirmation read O-11 (1 slot) after signal-time cost exists | 0.10-0.15 |
| 3 | **H-71** | **G-1 Asia-hours break of a prior-period level, 1h**, stop 1.0 ATR1h back through the level, fixed 2R, 32-bar cap (a 0.01 lot cannot take G's partial + trail) | 1h | gross +0.119 R (t 3.05), +0.067 over random levels [+0.017, +0.105] (fixed 2R); **the 8/8 years belongs to the partial + trail exit, not the armed fixed 2R**; about 900 cells looked | net −0.047 at the research prior; +0.082 at the broker; about 0 with the $0.70 overshoot | 15-20 a month (one a day) | **arm G1A + random-level control G1C now**; Q1 confirmation O-12 (conditional) | 0.05 |
| 4 | H-72 | Friday long (Thu 19:00 -> Fri 16:30 New York, frozen; B measured Thu 17:00 -> Fri 17:00), 1.0 ATR4h stop | 1d / 4h | +14 bp/day, t 2.7, 7/8; **2018+ seen** | gross 2.4x the research prior, 12x the broker | 4 a month | **forward-only arm WFRI** (swing pot); 26 Fridays is a safety check only, the decisive read needs about 170 Fridays | 0.10 |
| 5 | H-74 | Slow-trend lane merge: 30-day-extreme first touch (C-1, short), 4h/1d band breakouts (F-3), 30/60/90 windows (B), as one swing feature group in SW-0 | 1d / 4h | C-1 +0.277 R (t 3.85, carried by 2013 and 2015); F-3 +0.09-0.15 ATR4h | cost tiny per R; swap on longs | 15/yr (C-1) | feature group in SW-0 (Q2); no arm (the swing pot has one slot) | 0.10 |
| 6 | H-75 | D-1 Asia trend persistence, 1h | 1h | +$0.64, t 2.7, 7/8; **H's rebuild null** | broker 4.4x; prior 0.9x | 12 a month | reconcile first (brief MS-2); arm only if reconciled | 0.05 |
| 7 | H-76 | A-1 failed Asia-range fade | 15m | −2.8 bp, 2.2 control sd, n 384; **H's rebuild flipped; holdout seen** | broker 7x; prior 0.6x | 4 a month | **closed** (sign flipped on rebuild, holdout seen, 48 a year) | 0 |
| 8 | H-77 | D-2 CMF20 1h trend-follow | 1h | +$0.59, t 3.2 | broker 4x; prior 0.8x | 26 a month | redundancy check against the MA regime (ρ > 0.7 closes it); feature `cmf20_tick` | 0.05 |
| 9 | H-78 | Pre-specified book read on arm fills (E1/V1/O1/TS shapes = S5/S1/S2/W1 arms) against sign-randomised nulls | all | book 0.82 gross vs null p95 0.52 | — | — | daily_review addition, 0 trials | n/a |
| 10 | H-79 | Allocator against controls: 4 pre-fixed streams at $1.07 vs a block-shuffled null, and a decision-journal replay with the allocator's weights vs all weights = 1 | — | H's stylised test undetermined | — | — | 0-trial diagnostic (backlog 111) | n/a |

Not ranked: everything in section 4 whose "died on" column reads signal, count or seen without a rescue. Those are
closed in the form tested, at 0 trials.

## 6. What runs now, and what gets a slot

### 6.1 Demo exploration arms (2026Q4; within the RiskGate, ADR 0043 pots and caps; rules frozen in the pre-registration)

| Arm | Host / pot | Rule | £ at stop (0.01 lot) | Est. fills/day | Read |
|---|---|---|---|---|---|
| F1A | `icm-probe` scalp (£6.50 cap) | H-70, 15m, next open, 4-bar time exit, stop 1.0 ATR15 (skip outside $3-8.6) | about £6.0 | 3-6 | **cost at signal times** at ≥ 200 fills (0 slots); H-61 read at 300 (underpowered), decisive at 1,000 / 2,000 paired; stops in 6.3 |
| F1C | `icm-probe` scalp | random control: same bars' hour, same side mix (seeded hash), same exits | same | 3-6 | never graduates; the null for F1A |
| G1A | `icm-demo` intraday (£17 per-trade cap in `accounts.yaml`) | H-71, 1h; stop $3.0-22.6, so about half are skipped at today's ATR1h | about £15-17 | 0.3-0.5 | cost at ≥ 100 fills; a cost/parity/safety arm (power needs about 2,300); stops in 6.3 |
| G1C | `icm-demo` intraday | random level at the same distance, same window and exits | same | 0.5-0.7 | null for G1A |
| WFRI | `icm-demo` swing (£40 cap; 1 slot) | H-72 | about £27 | 0.8 (Fridays) | safety check at 26 Fridays; the only read at about 170 Fridays (1 slot then) |

- **Budget.** The precedent (the 11 Vantage twins) charged each registered arm one 2026Q4 slot. 2026Q4 is at 69/69,
  so these 5 arms need **69 -> 74**. **Approved 2026-10-26** by the program director and product owner, under the
  owner's no-quarterly-wait directive and the Vantage precedent. The quant's conditions apply:
  - this pre-registration is on `main` before the first fill;
  - the settings comment cites the directive;
  - the owner is told on #34 the same day;
  - K +5;
  - the fills are never used as evidence.
  - The alternative, starting on 2027-01-01 from the Q1 free reserve, would spend 5 of its 6 slots on arm
    registrations and delay the cost measurement by two months.
  - K moves 145 -> 150, which is negligible against the programme's own charge.
- **Local-clock twins of the frozen UTC arms** (S0L, S4L, S5AL; quant ruling: each one a trial, read as a paired net-R
  difference against its UTC twin, 95% CI > 0): **not funded**. The new arms above use local clocks by construction,
  and README 6.9 already settles which clock activity follows. A paired test buys a scheduling answer for 3 slots.
  Deferred to the Q2 draft with this reason.
- **Not armed:**
  - E-1 order block: controls explain it.
  - C-1 swing: the swing pot's one slot is taken, at about 1 trade a month.
  - D-1: not reproduced. A-1: closed.
  - D-2: redundancy unchecked.

### 6.2 2027Q1 free reserve (6 slots; dated note in `preregistration-2027Q1.md`)

| Row | Use | Condition (by date) | Else |
|---|---|---|---|
| O-11 | H-70 confirmation read on 2018-01-01..2025-09-30 (window never loaded by F, H or the director) | F1A has ≥ 200 admissible fills with its signal-time cost read, and L-2 (confirmation read type) is merged, by 2027-02-26 | Q2 |
| O-12 | H-71 confirmation read on the same window | G1A has ≥ 100 fills with cost read, by 2027-03-12 | Q2 |
| — | 4 slots stay free | — | unspent unless a dated note names them |

The confirmation half is clean for H-70 and H-71: slices F, G and H never loaded it. It is **not** clean for any of
A's, B's or D's 1d items.

## 6.3 Power and stop rules (evaluator condition 10)

| Item | Power statement | Stop rule |
|---|---|---|
| F1A alone | effect about +0.087 R against a per-trade SD of about 1 R needs **n ≈ 1,000** for 80% power (one-sided 5%). H-61's 300-trade read has about 35-40% power for this effect: a pass there is evidence, a miss is not a refutation | H-61 scalp stop (running net ≤ −30 R within the first 300); **futility** at 300 and 1,000: the one-sided 95% upper bound of the mean net R at measured cost below +0.03 R |
| F1A − F1C paired | **n ≈ 2,000** pairs | as F1A; F1C stops with F1A |
| G1A | per-trade SD about 1.3 R at 2R targets; an effect of +0.067 needs about 2,300 for 80% power: **the arm is a cost and parity measurement**, not an edge read, until then | H-61 intraday stop (≤ −15 R within 100); futility at 300 as F1A; G1C stops with G1A |
| WFRI | 14 bp against a Friday SD of about 100 bp needs about **170 Fridays** (≈ 3.3 years) | safety only at 26 Fridays: stop if the mean ≤ −20 bp or cumulative net ≤ −10 R |
| O-11 / O-12 | net conditions need a **minimum n of 1,000 events** in the placeable band, else inconclusive | — |

## 7. K, looks and contamination (honest accounting)

### 7.1 Looks charged

| Source | Raw looks (statistics) | Effective charge | Basis |
|---|---|---|---|
| Director (README 6.1-6.11) | about 210 | 20 | independent topic axes |
| A sessions | about 70 + 70 peeked | 15 | anchors, Gao pairs, ORB collapsed |
| B cycles | about 200 | 25 | calendar families |
| C Fibonacci/wavelets | about 185 | 20 | as C grouped them |
| D oscillators/trend/volume | about 190 | 30 | 173 rows over about 30 indicator x TF axes |
| E SMC | about 34 | 5 | one parameter set |
| F bands | 336 cells | 15 | F's own collapse (bands correlate 0.93+) |
| G key levels | about 1,100 | 51 | 17 families x 3 TF (quant condition 4; L-1 replaces this grouping with an eigenvalue-based K_eff) |
| H combinations | 64 | 6 | H's own collapse |
| **Total** | **about 2,460** | **about 187** | |

- **Registry K stays 145.** No registry row was written: 2026Q4 has no slot, and L-1 (the looks ledger) does not exist
  yet. Until it does, this table is the record. The evaluator adds **187** to `n_trials_effective` when reading any
  later trial (the owner-confirmed conservative default).
- So the gate-1 bar is about **t 4.6** at K ≈ 332 (4.3 at 145). With the 5 arms, K ≈ 337.
- Confirmation reads O-11 and O-12 are deflated by K_conf (2 reads), not by the discovery looks. That is the point of
  an untouched half. Both still require a day-clustered t ≥ 3.0, which is stricter than the K_conf bar.

### 7.2 Contamination (protocol breaches, reported to the evaluator)

| Slice | What was read outside the frame | Consequence |
|---|---|---|
| A | outcomes on 2020-01-01..2026-10-23, **including the holdout** 2025-10-01..2026-09-30 | every **session-clock configuration** (A-1, A-2, LBMA/COMEX/Tokyo anchors, Asia/West drift, overnight/intraday, 15m ORB) loses confirmation credit **and** can never be holdout-scored; forward data only. The holdout stays valid for configurations A did not compute (H-70, H-71, H-72) |
| B | outcomes on 2018-01..2025-09 (no holdout) | B-1 Friday and every B row: no confirmation credit; forward only (H-72 is forward-only by design) |
| D | the tail of a pooled 2010-2025-09 run (1d TSMOM, OBV, CMF) | those 1d rows: no confirmation credit; D-1/D-2 (1h) unaffected |
| All | no slice wrote to the registry or touched a trading path | — |

The cause was a process gap: the slices launched before the frame reached them. **Fix:** L-1's loader refuses bars
from 2018 on, which makes this impossible rather than forbidden.

## 8. Several models, and where they help (owner: "if required multimodels")

The rules are in `learning-loop.md` section 3:
- 40 features per member, fold-internal selection, every member in K;
- the combined model is judged as one strategy, with no best-member pick afterwards.

Where the evidence points:
1. **Range model under the specialists** (H-16 + H-73 -> M-3). This is the strongest use. A range/volatility model
   decides stand-aside and runner arming, while the direction comes from the specialist rule. At £1,000 it cannot
   size up: 0.01 lot is the floor and the 1% cap binds.
2. **Regime as a feature inside one rule (F-1), not an external family switch.** H finds the cross-stream
   correlation unchanged by regime, and the rule-table allocator is unvalidated (item 111).
3. **Per-session experts:** not justified yet. Session splits of oscillators and anchors flip sign (noise at these
   look counts). The NY-pit stratum of F-1 is recorded inside the one arm and read as a pre-specified secondary, not
   as a separate model.
4. **Stacking / meta-labelling:** ranker only until a primary passes P4 (H: AUC 0.557, no lift).

What exists and what is missing:
- **Exists:** specialists with their own meta-models, `pooled_<tf>`, `loop_filters` (session/regime filters),
  `RuleAllocator` (rule table x population fitness), H-62 per (broker, style) models, `h16.py`.
- **Missing:** a decision-time range selector (M-3), a learned or validated gate (M-2, after item 111), and stacking
  code (M-4). None of these is built before its trigger.

## 9. Delegation plan

Briefs for the specialist principals, each with a `-staff` bench. One line per brief; no two write the same file.

- opportunity-researcher | H-73 feature group spec: ≤ 12 causal columns (FOMC/NFP flags via `asof_join` on `available_utc`, wavelet j3/j5/j7 energy with prefix-invariance tests, rv30/rv90, range-used vs forecast, displacement, tick z hour-of-week), incremental R² over HAR(1,5,22) on 2010-2017 only, and the H-54 sealed-log amendment | spec + discovery-half table with the drop rule applied; K_eff +1 stated | worktree (docs/research/h73-range-features-2026-10.md)
- opportunity-researcher-staff | count-only: H-73 columns' coverage and stub days 2010-2025-09 (no outcomes) | count table pasted into the spec | read-only
- microstructure-researcher | Signal-time cost protocol for F1A/G1A/WFRI: buckets (signal hour x stop exit vs time exit), the 95% upper bound, overshoot charge, the 200/100-fill read dates, and the cost condition O-11/O-12 use | protocol amendment to `prereg-cost-measurement-2026-10.md` before the first fill | worktree
- microstructure-researcher-staff (MS-2) | Reconcile D-1: slice D's build vs H's (entry stamp, unit, cost, session clock) on 2010-2017 only (A-1 is closed) | a reconciliation note; D-1 either reproduced in both builds or closed | worktree (docs/research/time-structure/reconcile-d1.md)
- macro-researcher | H-74 merge: one swing feature group (30-day-extreme first touch from 1m, F-3 4h/1d band state, 30/60/90 windows, wavelet-coherence macro context) for SW-0, plus the data-survey dependencies (COMEX volume, COT, ETF, OG expiry dates, pre-2010 history) with what each unblocks | group spec ≤ 10 columns; the dependency table | worktree (docs/research/h74-swing-group-2026-10.md)
- macro-researcher-staff | freeze the Diwali / Chinese New Year / CME OG-FND-LTD date tables with source and sha256 (B's are typed from memory) | data file + checksum + test | worktree (config/)
- ml-researcher | H-62 v2 feature list under the 40-cap with H-73, F (hurst_base_flag, brk_event_dir, htf4h/1d_kc_pos), G-3 level distances, D's osc_trend_vol: |ρ| clustering, which columns replace which, leakage tests (prefix invariance, asof); plus M-2/M-4 trigger criteria | spec with the column budget per style and a leakage test list | worktree (docs/research/h62-v2-features-2026-10.md)
- ml-researcher-staff | the allocator control test design (item 111): 4 pre-fixed streams at $1.07, block-shuffled null (week blocks), journal replay with weights = 1 | design note + code plan, quant-reviewer sign-off owed | worktree
- exits-researcher | G1A's partial + trail vs fixed 2R on matched holds; F section 8 chandelier with a matched-hold control; whether V1u applies to F1A (4-bar exit) | note; no new trial | read-only
- strategy-researcher | write the F1A/F1C/G1A/G1C/WFRI rule generators' spec text into `arms-2026-10-25.md` by amendment (pointer done) and the hypotheses.md A15 rows' upkeep | amendment merged before the first fill | worktree
- evaluator | record the contamination in 7.2 as findings, confirm the 187 effective-look charge or set another, and confirm O-11/O-12 reading rules before the freeze | verdict note | read-only
- performance-analyst | from the first F1A/G1A fills: signal-time cost by bucket and the H-78 book table on arm fills | weekly rows in the scorecard | read-only

## 10. What I would stop doing, and what changed in the plan

**Stop:**
- 15m rules on fixed levels, sessions or oscillators without a randomised control.
- Calendar direction studies.
- Fibonacci, wavelet-direction and cycle-extrapolation work.
- Launching slices before their frame is committed. That is what caused the 7.2 breaches.

**Plan changes** (`research-plan.md` 11.7 and its feedback log rows 63-80):
- H-73 joins ranks 1-2 beside H-16/H-19 as a priority-1 enabler.
- H-70 becomes the 15m lane's first new candidate since H-17.
- H-71 and H-72 are arms.
- H-74 moves into SW-0.
- H-75 is held for reconciliation; H-76 (A-1) is closed.
- Two Q1 free-reserve slots are conditionally used; 4 stay free.
- The 2026Q4 override 69 -> 74 is approved under the quant's conditions (6.1).
