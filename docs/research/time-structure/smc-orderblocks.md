# Slice E: order blocks, smart-money concepts and related methods on XAUUSD (15m / 1h / 4h)

Staff research lead, 2026-10-26, for the research director's time-structure programme (frame:
`time-structure/README.md`, whose rules 4.1-4.6 bind this file). Definitions-first slice: it states what each concept
is, to the precision needed to code it with no hindsight, shows the traps, ranks the candidates and closes most of them.

**Discovery half only (README 4.1).** Version 2 of this file (same day) measures on real gold, Dukascopy 1m mid bars,
**2010-01-01 .. 2017-12-31 only**: the script opens only the eight yearly files 2010-2017 and asserts the last bar is
before 2018; nothing from 2018 on, and no holdout bar, was read or computed. (Version 1 had measured nothing on gold;
the random-walk demo is kept.) Scripts: `scripts/research/smc_gold_discovery.py` (gold; run
`uv run python scripts/research/smc_gold_discovery.py --raw ~/tmp/ts/raw --out <json>`, about 90 s, seeded, a re-run
gives identical numbers) and `scripts/research/smc_hindsight_demo.py` (random walk and the control generators).
Parameters were pinned in section 2 before the run (k 5, displacement 1.5 ATR, expiry 32 bars, buffer 0.25 ATR, 2R
target, 32-bar exit). Looks taken are listed in section 10; none is charged to K.

## 1. What was already tried (do not repeat)

| Item | Result | Source |
|---|---|---|
| SMC as standalone rules (FVG, OB, BOS/CHoCH, premium/discount, Fibonacci) | retired as rules on the literature (R-09); kept as features | `hypotheses.md` R-09 |
| TVR-1 (internal CHoCH + swing trend + FVG) and TVR-2 (swing BOS), 15m | withdrawn before dispatch on counts: 441 and 1,196 events (floors 1,000 / 1,500); 0 trials. Order blocks were **never** replicated (off in the owner's settings, D-3) | `prereg-tv-replica-2026-10-18.md` B4b |
| H-34 / H-35 multi-timeframe CHoCH + discount + FVG; liquidity-sweep + CHoCH | withdrawn: 0-18 candidates; the structural stop is a median 3.3 ATR15, 34 of 8,385 CHoCHs fit the 1.5 ATR15 lot cap | `hypotheses.md` H-34/35 |
| H-46 `liquidity_sweep`, H-47 `sweep_displacement` | below 1,500 candidates; stop median $18.5 / $22, 4% / 0% fit 0.01 lot | `hypotheses.md` |
| H-48 `sweep_absorb` (prior-day/session extreme, shallow, fade at reclaim), trial #88 | gross -0.028 R, t -1.17, n 3,210; net -0.552 R; R-14 | `hypotheses.md` |
| H-49 equal highs/lows as magnets, stage 1 (0 trials) | lift -0.3 / +0.8 pts, CI contains 0; R-13 | `kb5-stage1.json` |
| Fibonacci levels vs random fractions of the same range (director 6.8) | not better than random (prior day -0.032 ATR, CI excludes 0 on the wrong side) | README 6.8 |
| Asia range taken in London (director 6.4) | 68% take the high, 68% the low, 39% both; first take continues (+0.06 range units, t 1.53), no reversal | README 6.4 |
| Opening range follow-through (6.3), momentum/reversal 15m-90d (6.7) | none | README 6.3, 6.7 |

Consequence: the only parts of this slice with no prior test are the **order block itself**, the **FVG as a
magnet/target**, **value-area (market profile) levels** and **Wyckoff spring/upthrust**. Everything about "sweep then
reverse" is closed by 6.4 and #88; premium/discount rests on the Fibonacci result.

## 2. Precise, hindsight-free definitions

Conventions. Bar i opens at `t_i`, closes at `a_i = t_i + tau` (`tau` = 15m / 1h / 4h). A feature "exists" only from
the **close of the bar that makes it knowable**; the decision happens at `a_i` and orders rest from bar i+1 (so a
fill needs a later bar). Joins to any external series via `asof_join` on `available_utc`; epoch times via
`goldbot.data.timeutil.epoch_ns`. ATR = Wilder ATR(14) **lagged one bar** (`ATR_{j-1}`), so a bar's own range never
sizes its own filter. Stop-first if stop and target sit in one bar (engine convention). Session clocks are local
(`America/New_York`, `Europe/London`), trading day ends 17:00 New York (README 4.2, 6.9: the NY clock rules in the UK/US gap weeks).

| # | Concept | Exact causal definition | Known at | Parameters (pin before data) | Hindsight trap | Verdict |
|---|---|---|---|---|---|---|
| 1 | **Swing high / low** (fractal) | `H_i` is a swing high when `high_i > max(high_{i-k..i-1})` and `high_i >= max(high_{i+1..i+k})` (ties on the right allowed, unique on the left; mirror for lows) | **`a_{i+k}`** (k bars late) | k = 3 internal, 5 on 15m, 10 on 1h/4h (replica: leg pivots 5 / 50, same lag) | marking the pivot at `a_i`: it is selected *because* price then fell | keep (substrate) |
| 2 | **BOS / CHoCH** | At close j: bullish break when `close_j > P` for the latest **unbroken confirmed** swing high P (and `close_{j-1} <= P`); one break per pivot. CHoCH if the trend state before j was bearish, else BOS; trend state flips only on a break. A wick through P without a close beyond it is a **sweep** (row 8), not a break | `a_j` | as row 1; close, not wick | using the pivot before it is confirmed; calling the move a BOS because it later kept going | keep (substrate; replica exists, exact) |
| 3 | **Displacement** | bar j with `(high_j - low_j) >= d * ATR_{j-1}`, body/range >= 0.6, close in the outer 25% of the bar (d = 1.5; 15m) | `a_j` | d, 0.6 | choosing d after seeing which moves paid | keep (OB filter) |
| 4 | **Fair value gap** | bull: `low_j > high_{j-2}`; zone `[high_{j-2}, low_j]`, size >= 0.25 `ATR_{j-1}`; bear mirrored. (The replica's body-% rule is the alternative, fitted to the owner's chart: running mean from series start.) Status at any time T: *open* until a later bar's low touches the top (partial), *mitigated* when any later **close** is beyond the far edge | `a_j` | size floor 0.25 ATR | **drawing only the gaps that "held"** (survivorship, section 3, T2) | keep as feature/target (E-2) |
| 5 | **Order block (bullish)** | Needs a bullish BOS at bar b (row 2) whose leg contains a displacement bar (row 3). Leg start s = the bar of the lowest low since the broken pivot's bar. OB = the **last bearish bar (close < open) with index in [s, b-1]**; zone = its full range `[low_o, high_o]` (body-only variant `[min(o,c), high_o]` is a pre-named alternative, not both). Created at `a_b` and not before. Active until: first **close** below `low_o` (invalidated), or a touch (consumed after one retest), or E bars (E = 32 on 15m). Entry: limit at `high_o`; stop `low_o - 0.25 ATR`; bearish mirrored | **`a_b`** | d, E, buffer, range vs body | "the last down candle before the move" is only identifiable once the move has finished; marking the candle at its own time, or only the zones that the chart shows after a clean rally | keep: one trial candidate (E-1) |
| 6 | **Supply / demand zone** | base = 1-3 bars with range <= 0.6 ATR followed by a displacement bar leaving the base; zone = the base range | departure close | as row 3 | same as OB | **drop** as separate (it is the OB with a different anchor; test once through row 5) |
| 7 | **Breaker / mitigation block** | breaker = an OB that was invalidated by a close through it, retraded from the other side; mitigation block = a failed-attempt variant | invalidation close | — | chains BOS -> OB -> failure -> flip; n collapses, extra discretion | **drop**: a support/resistance flip with 3 more conditions; mitigation block is not codable without discretion |
| 8 | **Liquidity sweep** | Level L from a *closed* set: prior-day H/L (17:00 NY roll), prior-session H/L, Asia range (Tokyo 08:00 -> London 08:00), confirmed swing H/L (row 1). Sweep at bar j: `high_j > L` and `close_j <= L` (first pierce since L formed), depth = `(high_j - L)/ATR_{j-1}`; deep/cascade = a close beyond L (that is a BOS) | `a_j` | depth bands 0.1-0.5 / >0.5 ATR | picking levels that the sweep "obviously" targeted after the reversal; anchoring on the day's eventual extreme | feature only (reversal closed by #88, 6.4) |
| 9 | **Equal highs / lows** | classic pivots within 0.1 ATR(200) | pivot confirm | replica | levels chosen after the reversal | **drop** (R-13) |
| 10 | **Premium / discount** | range = the last two **confirmed** opposite swings (row 1), `S_L, S_H`; `p = (close - S_L)/(S_H - S_L)`; premium p > 0.5, discount p < 0.5; OTE band 0.62-0.79 in ICT usage. The repo's `smc_levels.swing_zones` uses the running extreme since the last confirmed pivot (also causal) | `a_i` | k | anchoring on the leg's final high/low while the leg still runs; `p` outside [0,1] means the range is broken | **feature only**: it is a Fibonacci level (6.8: not better than random) |
| 11 | **Kill zones** | fixed **New York-local** windows: London 02:00-05:00, NY AM 07:00-10:00 (some sources 08:30-11:00), London close 10:00-12:00, Asia 20:00-24:00 NY. UTC shifts with US DST; use `America/New_York` per date | clock | the window edges | choosing the window that earned most | **gate/feature**: a volatility concentration (README 6.5: 08-11 NY is cheapest and most active), not a signal; S-GATE already failed as a filter |
| 12 | **Inducement** | the first minor (k = 1-2) pivot swept on the way into an OB | `a_j` | k | which minor pivot counts is chosen after seeing the reversal | **drop**: the sweep operator of row 8 at a smaller k, plus a free parameter |
| 13 | **Wyckoff range, spring, upthrust** | trading range TR (causal): over the last N bars `max(high) - min(low) <= w ATR`, each side touched >= 2 times at a confirmed pivot, N >= 20 (1h). Spring = sweep (row 8) of the TR low with a close back inside; upthrust mirrored. Phases A-E (SC, AR, ST, SOS, LPS) are **not** codable without discretion | `a_j` | N, w | the range "accumulation" is only recognised once the markup has begun | spring/upthrust: keep (E-5, feature/demo arm); phase labels: **drop** |
| 14 | **Market profile** | for each completed session on 1m bars: volume-at-price histogram (volume = `tick_count`), POC = max bin, value area = smallest contiguous set holding 70% of volume around the POC, VAH/VAL its edges; 80% rule: open outside the prior value area, then **two consecutive 30-min closes inside**: target the opposite edge | session close (profile), `a` of the 2nd close (rule) | bin = 0.1 ATR15, 70% | including the day's own completed profile before it ends | keep (E-4, levels via slice D too) |
| 15 | **Delta / footprint** | true delta needs signed executed volume. Spot XAUUSD CFD bars carry quote ticks (`tick_count`), not trades; there is no tape. Proxies: bar delta ~ `tick_count * (2C-H-L)/(H-L)` (CLV), tick-test on 1m close change. Both are activity-weighted price location, i.e. what MFI/CMF already hold | `a_i` | — | treating quote count as executed volume | footprint proper: **drop**; CLV/effort-vs-result: feature (E-6, via slice D) |

## 3. The hindsight traps, shown

Script output (driftless random walk, 60,000 hourly bars, seed 7, true edge zero by construction; all numbers should
be about zero or equal the cost convention if the rule is causal):

| Trap | Hindsight version | Causal version |
|---|---|---|
| **T1 swing, 10-bar short gain in ATR** | short at the close of the swing-high bar i (needs bars up to i+5 to know it is a swing): **+1.516 ATR** | short at the close of bar i+5, when it is known: **-0.016 ATR** |
| **T2 FVG, mean R** (limit at the near edge, stop at the far edge, target 2x width, fill from the next bar, stop-first) | only zones never closed through in the following 50 bars, "the chart you review later": **+1.142 R** (n 2,343) | every filled zone: **-0.279 R** (n 17,185; negative because of the stop-first convention and the fill-bar rule, the reference level for a zero-edge process) |

**Same test on real gold, 15m, 2010-2017** (188,356 bars):

| Trap | Hindsight version | Causal version |
|---|---|---|
| T1 swing high, 10-bar short gain, ATR (n 11,803) | **+1.101** (peeking 5 bars) | **-0.019** (at i+5) |
| T2 FVG mean R (size >= 0.25 ATR; limit at near edge, stop far edge, 2x width) | zones "respected" 50 bars: **+0.877** (n 3,872) | every filled zone: **-0.135** (n 18,434) |

Gold sits on the random-walk numbers: the hindsight versions pay +0.9 to +1.1 for nothing, so any statistic built from
"zones that held" must be assumed to carry this bias.

Reading (random-walk table). A 2.5-3% subsample of zero-edge gaps shows +1.14 R purely because the filter ("respected for 50 bars")
contains the stop condition of the trade it is then used to evaluate. Order blocks have the same two defects and a
third: **the OB candle is chosen after the move** ("the last down candle before the impulse"), so any statistic on OBs
selected from a chart is conditioned on the impulse that followed. The causal OB (row 5) exists only from `a_b`, after
the impulse is done, which removes the first-leg move from the trade; what remains is the retest, and a retest's edge
(if any) must be shown against a control. Three further traps to test for in any code: (i) a swing/OB read at the bar's
own timestamp instead of `a_{i+k}`; (ii) the ATR or FVG threshold including the bar itself or later bars (the replica's
running mean from the series start is causal but start-dependent; pin the start); (iii) joins on `bar_time` instead of
`available_utc`. A **prefix-invariance test** (feature at t identical on bars ..t and ..t+200) is a dispatch precondition
for every row, as in the TV-replica ruling B4a.

## 4. Controls (README 4.4) and the statistic

* **Randomised level (same distance):** for each real zone/level event at bar b with distance `d = |L - close_b|`, draw
  a control level at `close_b + s*d` with `s = +-1` at random. Same bar, same distance distribution, same count; the
  identity of the level is removed. (`control_random_level` in the script.)
* **Randomised time (same frequency):** same level offset in ATR, re-timed to a random bar with the **same
  New York hour and weekday**; keeps the frequency by hour and weekday. (`control_random_time`.) For sweeps and OBs
  also the director's matched random-bracket control (same session, ATR decile).
* **Statistic:** mean net R per event under the same bracket, day-block bootstrap CI (>= 1,000 draws) of
  `real - control`, plus the day-clustered t of the real rule (CR1). A candidate must beat the **control** (not zero):
  a zero-edge rule has a negative mean after costs and the stop-first convention (-0.28 R in T2), so the control is
  the reference, not zero.
* **Cost (net, two cases).** Research prior: bar spread + 0.37 $/oz (README 4.6; 0.15-0.25 ATR15 in 2019-25). Broker:
  $0.144 central / $0.23 conservative / $0.62 stress per oz (`cost-model-1m-2026-10-25.md`). Net R = gross R - cost/stop.

| Stop (15m rule) | Broker central $0.144 | Conservative $0.23 | Stress $0.62 | Research prior ($0.71 flat) |
|---|---|---|---|---|
| 1.0 ATR15 = $8 | 0.018 R | 0.029 R | 0.078 R | 0.089 R |
| 0.8 ATR15 = $6.4 (fits the $6.65 cap) | 0.023 R | 0.036 R | 0.097 R | 0.111 R |
| 1h structure stop $18.5 (H-46 median) | 0.008 R | 0.012 R | 0.034 R | 0.038 R |

This table is for 2024-26 prices (ATR15 about $8). On 2010-2017 prices the same rules have stops of $1-2 and pay 0.4-0.9 R (section 5.1, which also explains #88's recorded 0.52 R gap: a real cost). The earlier SMC families had no gross edge. A candidate needs gross >= 3x broker cost and a gate-1 t near 4.3 at K = 145.

## 5. Measured on gold, discovery half 2010-2017 (15m unless stated)

Cost basis. Research prior = bar spread (median $0.24-0.51 over 2010-2017) + $0.37/oz. Broker = $0.144/oz central.
Net R = gross R - cost / stop. All t are day-clustered (CR1). Controls are described in section 4.

**5.1 Why 2010-2017 stops are tiny and costs large (the #88 cost question).**

| Year | ATR15 median $ | Spread median $ | Research-prior round trip $ | Stop 0.6 ATR15 $ | Cost in R at 0.6 ATR15 | Stop 1.0 ATR15 $ | Cost in R at 1.0 ATR15 |
|---|---|---|---|---|---|---|---|
| 2010 | 1.63 | 0.51 | 0.88 | 0.98 | 0.90 | 1.63 | 0.54 |
| 2011 | 2.20 | 0.42 | 0.79 | 1.32 | 0.60 | 2.20 | 0.36 |
| 2012 | 1.81 | 0.36 | 0.73 | 1.09 | 0.67 | 1.81 | 0.40 |
| 2013 | 1.90 | 0.35 | 0.72 | 1.14 | 0.63 | 1.90 | 0.38 |
| 2014 | 1.35 | 0.29 | 0.66 | 0.81 | 0.82 | 1.35 | 0.49 |
| 2015 | 1.27 | 0.31 | 0.68 | 0.76 | 0.89 | 1.27 | 0.53 |
| 2016 | 1.51 | 0.30 | 0.67 | 0.90 | 0.75 | 1.51 | 0.45 |
| 2017 | 1.05 | 0.24 | 0.61 | 0.63 | 0.97 | 1.05 | 0.58 |

**#88 cost flag, resolved: a real cost, not a bug.** H-48's stop was 0.6-1.0 ATR15 (`prereg-kb` s.1) and the "$8" in
that document is today's ATR15. In 2010-2017 ATR15 was $1.05-2.20, so the stops were $0.6-2.2 and the research-prior
round trip ($0.61-0.88) cost 0.36-0.97 R per trade, about 0.5 R on average: the recorded net-minus-gross gap of 0.52 R
(-0.552 vs -0.028). The same mechanism explains E-1 below (median cost 0.39 R). Consequences: (1) every 15m trial read
on 2010-2017 prices faces a 0.4-0.9 R cost that the same rule would pay at 0.02-0.1 R at today's ATR15 of about $8, so
registry "net" for old years says little about today, and the era split matters; (2) on that data a rule needs a gross
edge of 1-3 R to net positive, not the 0.1 R that a gross t of 3-4 usually represents. The "$8 stop -> 0.09 R" figure in
section 4 applies to 2024-26 only. (This is a reading of the pre-registration and my own ATR table; I did not re-run
trial #88.)

**5.2 E-1 order-block retest (section 2 row 5; London + NY-morning window 02:00-12:00 NY on the break bar).**

| Item | Value |
|---|---|
| Causal OB events, all hours / in the window | 3,571 / 2,372 (8 years) |
| Filled before expiry or invalidation | 1,431 (60%; control fill rates 58-60%) |
| Median stop | $1.80 = 1.26 ATR (98.7% under the $6.65 cap, which is meaningless at 2010-17 ATR) |
| Outcomes | target 33.1%, stop 59.4% (a 2R target needs 33.3% to break even) |
| **Real gross** | **+0.112 R, t 2.84, n 1,431** |
| Real net, research prior (median cost 0.39 R) | **-0.313 R, t -7.8** |
| Real net, broker cost $0.144 on the same stops | +0.024 R, t 0.61 |
| Control A (zone identity removed: entry and stop distances shuffled within the year; same bar and side) gross / net prior | +0.054 / -0.384 |
| Real minus control A, gross | +0.043 R, t 1.21, 95% CI [-0.025, +0.116] |
| Real minus control A, net prior | +0.056 R, t 1.53, CI [-0.012, +0.134] |
| Control B (same side and ATR-scaled distances at a random bar of the same NY hour) gross / net prior | +0.032 / -0.372 |
| Real minus control B, gross | +0.082 R, t 1.89, CI [-0.006, +0.167] |
| Real minus control B, net prior | +0.061 R, t 1.40, CI [-0.029, +0.149] |

Reading. A small positive gross (t 2.84) but half to two thirds of it is earned by the same bracket on random levels
and times (control gross +0.03 to +0.05; a nonzero control is the geometry of a limit order with a 2R target under the
stop-first convention, not an edge). What the order block adds is +0.04 to +0.08 R gross, CI including zero against both
controls. Net of the research-prior cost the rule loses 0.31 R. **E-1 meets its pre-written kill rule (b): the CI of real
minus control includes 0. Closed at 0 trials.** The count (about 180 filled a year) would have cleared the 1,500 floor
over 2010-2025-09, so the kill rests on the control, not on counts. Residual: at today's stops (about 4x larger in
dollars; cost 0.02-0.1 R) the cost would not eat a +0.11 R gross, but the controls show it is not the block that earns it.

**5.3 E-2 fair value gaps (size >= 0.25 ATR).**

| Statistic | 15m (n 20,656) | 1h (n 4,794) |
|---|---|---|
| P(midpoint retraced within 8 bars): real / control / diff [95% CI] | 63.1% / 60.7% / +2.3 pts [+1.7, +3.0] | 59.6% / 58.7% / +0.9 pts [-0.1, +2.0] |
| within 32 bars | 80.1% / 78.3% / +1.9 pts [+1.4, +2.4] | 78.6% / 77.4% / +1.2 pts [+0.3, +2.1] |
| Next-16-bar range / ATR after a displacement bar **with** a gap | 5.78 (n 4,644) | 4.69 (n 1,057) |
| ... displacement bar **without** a gap | 5.83 (n 22,252) | 4.93 (n 6,144) |
| ... all bars | 4.66 | 4.70 |

Reading. A gap is slightly more likely than a random level at the same distance to be revisited (about +2 points on 15m,
nonzero, far below the 5-point bar H-49 set; 1h none at 8 bars). As a range predictor the gap adds nothing beyond the
displacement bar that makes it: that bar predicts about 24% more range than average with or without a gap. **Kill rule
met (retrace lift < 5 points, range lift 0): closed at 0 trials.** The displacement bar itself is volatility clustering,
already covered by the H-02/H-16 feature set (README 6.7: RV20 IC +0.40).

**5.4 E-4 value-area 80% rule** (prior trading day's profile from 1m typical price weighted by `tick_count`, 70% value
area; open at 08:30 NY outside it, two consecutive 30-min closes back inside; target the far edge; stop at the excursion
extreme -$0.50; flat 17:00 NY).

| | n | gross R | gross t | net R (prior: spread $0.30 + $0.37) | net t | median stop |
|---|---|---|---|---|---|---|
| Real value area | **83** (10 a year) | +0.190 | 0.88 | +0.020 | 0.09 | $4.45 |
| Random areas of the same width inside the prior day's range (10 per day) | 924 | +0.021 | 0.31 | -0.154 | -2.30 | $4.34 |

Reading. About one event a month (my version-1 estimate of 4-6 was 5x too high): 83 in eight years, below the 150
floor for daily rules. The point estimate is positive but t 0.88 on 83 events is no evidence. **Closed on the count rule;
POC/VAH/VAL stay as level features for slice D, 0 trials.**

**5.5 Other frequency facts.** London takes an Asia extreme 68% each, both 39% (README 6.4). TVR-1 / TVR-2 15m events
441 / 1,196 over 2010-2025-09 (`prereg-tv-replica` B4b).

## 6. Literature (peer-reviewed first; evidence grade by the README scale: A direct causal/OOS, B robust empirical, C weak, D practitioner only)

| Claim | Source | Grade | Note |
|---|---|---|---|
| Stop and take-profit orders cluster at round numbers and just beyond extremes, giving reversals/cascades in FX | Osler, "Currency orders and exchange rate dynamics: an explanation for the predictive success of technical analysis", J. Finance 58(5), 2003; Osler, "Stop-loss orders and price cascades in currency markets", J. Int. Money Finance 24, 2005; Osler, "Support for resistance", FRBNY Economic Policy Review 6(2), 2000 | B | Proprietary order data, 1-3 dealer banks, 1990s majors, **gross**. Supports a clustering mechanism, not these rules, not gold, not net of costs |
| Support/resistance coincide with limit-order depth | Kavajecz and Odders-White, "Technical analysis and liquidity provision", Rev. Financial Studies 17(4), 2004 | B | NYSE equities, not CFD gold |
| Technical rules profitable pre-1990, declining since | Park and Irwin, J. Economic Surveys 21(4), 2007; Neely, Weller and Ulrich, J. Financial and Quantitative Analysis 44, 2009; Menkhoff and Taylor, J. Economic Literature 45, 2007 | B | Trend rules, not SMC patterns; profits fell after 1990s; data-snooping caveat |
| Pattern recognition can add incremental information | Lo, Mamaysky and Wang, J. Finance 55(4), 2000 | C | US equities, kernel-based patterns, not FVG/OB |
| Fibonacci ratios not over-represented in turning points | Batchelor and Ramyar, "Magic numbers in the Dow", Cass Business School discussion paper, 2006 ([City Research Online](https://openaccess.city.ac.uk/16276/)) | C | Equity index; few significant ratios, about what chance gives with that many tests |
| Intraday volatility follows trading-centre hours | Dacorogna et al., J. Int. Money Finance 12, 1993; Andersen and Bollerslev, J. Empirical Finance 4, 1997 | B | Supports kill zones as **volatility** windows, not as directional signals |
| Order-flow toxicity (VPIN) predictive power disputed | Easley, Lopez de Prado and O'Hara, RFS 25(5), 2012; Andersen and Bondarenko, "VPIN and the flash crash", J. Financial Markets 17, 2014 ([Kellogg](https://www.kellogg.northwestern.edu/faculty/research/detail/2014/vpin-and-the-flash-crash/)) | C | Needs signed trades; we have quote ticks only |
| Trade classification by tick test | Lee and Ready, J. Finance 46(2), 1991 | B | Equities with a tape; not applicable to spot gold quotes |
| Multiple-testing deflation | Bailey and Lopez de Prado, "The deflated Sharpe ratio", J. Portfolio Management 40(5), 2014; Harvey, Liu and Zhu, Rev. Financial Studies 29(1), 2016 | A | The K = 145 gate |
| **Order blocks, FVG, breaker/mitigation blocks, inducement, kill zones, "ICT"** | Michael Huddleston (Inner Circle Trader) mentorship and video material; no paper, no data, no testable specification in print | **D** | **No evidence.** Definitions vary between teachers (this is why section 2 pins one) |
| Wyckoff method (accumulation/distribution, spring) | Wyckoff's early-20th-century courses; modern restatements (e.g. Pruden) | D | Practitioner only; phases are discretionary |
| Market profile, value area, 80% rule | Steidlmayer (CBOT); Dalton, Jones and Dalton, *Mind Over Markets*, 1990 | D | Futures-exchange practitioners; the 80% rule's hit rate is a claim without a published test (unverified) |
| Supply/demand zones | Seiden-type retail courses | D | No data |

Where claims lack evidence: all of the ICT vocabulary and Wyckoff phases (D). The only mechanism with outside support is
order clustering at levels (Osler), which predicts reactions at *prior extremes and round numbers*, not at candle zones.

## 7. Candidates, ranked (budget: 2026Q4 has 0 left, 2027Q1 has 6 free slots, K = 145, gate-1 t about 4.3)

After the discovery-half measurements **none is worth a Q1 slot. I propose 0 trials.** E-1, the only object with a
positive gross over its controls, adds +0.04 to +0.08 R with a CI that includes zero.

| Rank | ID | Discovery-half result | Verdict |
|---|---|---|---|
| 1 | E-1 OB retest 15m | gross +0.112 R t 2.84; over control +0.04..+0.08 R, CI includes 0; net prior -0.31 R | **closed at 0 trials** (kill rule b). May run only as a demo exploration arm, never as promotion evidence |
| 2 | E-2 FVG magnet / range | retrace +2 pts (< 5); range lift 0 beyond the displacement bar | **closed**; displacement stays a feature (H-16/H-62) |
| 3 | E-4 value-area 80% | n 83 in 8 years, gross t 0.88 | **closed on count**; levels go to slice D as features |
| 4 | E-5 Wyckoff spring/upthrust 1h | not measured (rare, wide stops) | feature / demo arm only |
| 5 | E-3 Asia sweep in London | README 6.4, #88 | drop as a rule; keep flags as features |
| 6 | E-6 effort-vs-result | not measured | feature (slice D) |
| - | E-7 premium/discount, Fibonacci OTE | README 6.8 | drop |
| - | Kill zones, inducement, breaker/mitigation, supply/demand, equal highs/lows, delta/footprint | section 2 | drop |

The blocks below keep the pre-measurement rationale and kill rules; the outcome of each is in section 5.

### E-1  Order-block retest (15m)
- Mechanism: an impulse that breaks structure leaves resting limit orders (and unfilled institutional inventory) at the origin candle, which the price revisits. Weak: Osler explains clusters at extremes and round numbers, not candle origins. Evidence grade D (concept), C (mechanism by analogy).
- Already tried?: new. TVR/H-34/H-35 tested CHoCH/FVG with structural stops that did not fit; the OB zone itself (stop = the candle, about 0.5-1.2 ATR15) was never replicated or run. R-09 retired it on the literature only, so this is a rule-level test of an untested object and needs the research director's waiver of R-09's "no further SMC rule trial without new evidence" clause (TVR closure applies only if TVR-1 and TVR-2 were run; they were withdrawn).
- Timeframe and clock: 15m decision; London 02:00-12:00 NY and NY 08:00-12:00 NY only (local clocks, DST per date); no entries within 30 min of a tier-1 release.
- Entry: section 2 row 5, bullish BOS with displacement at close b; limit at `high_o`, live from bar b+1 for E = 32 bars, first touch only; bearish mirrored. Higher-timeframe condition omitted (it removed every candidate in H-34).
- Side: both. One position at a time (yes).
- Exit: stop `low_o - 0.25 ATR` (skip if stop > $6.65, the 0.01-lot cap); target 2R; time exit 32 bars; stop-first.
- Control: random-level (same distance) and matched random-bracket (same session, ATR decile), section 4.
- Discovery-half evidence (2010-2017, section 5.2): n 1,431 filled; gross +0.112 R t 2.84; real minus control gross +0.043 (A) / +0.082 (B), CIs include 0; net prior -0.313 R.
- Cost check: stop $6.4-8: broker 0.018-0.023 R central, 0.078-0.097 stress; research prior 0.09-0.11 R. Gate-1 t of 4.3 at n = 1,500 and a per-trade SD of about 1.1 R needs a mean of about 0.12 R net, so gross of about 0.2 R under the research prior.
- Expected events: measured 1,431 filled in the discovery half (about 180 a year), so about 2,900 over 2010-2025-09; clears the 1,500 floor.
- Kill rule: (a) fewer than 1,500 candidates over 2010-2025-09 or fewer than 30% fitting the $6.65 cap; or (b) on 2010-2017, real minus control net R, 95% day-block CI includes 0 or is negative. Either closes it at 0 trials.
- Read rule (if a trial): continue only if gross t >= 4.3 on the confirmation half's pre-registered read and net R > 0 under the research prior; else stop.
- Route: closed after the 0-trial control. Posterior P(continue) under a trial: 1%.

### E-2  FVG as magnet and as range predictor (15m / 1h)
- Mechanism: a gap marks an imbalance; price tends to return to gap midpoints (mean-reverting microstructure) and a large displacement gap marks a volatility burst whose range persists (the predictable object). Grade D / C.
- Already tried?: new as a measurement; FVG as an entry was withdrawn (TVR-1). The replica FVG is a feature in D-TVR.
- Entry (diagnostic only): at `a_j` of each causal FVG (size >= 0.25 ATR): P(midpoint retraced within 8 / 32 bars) vs the random-level control at the same distance; and next-16-bar range / ATR vs bars matched on session and ATR decile.
- Exit/Side: none (descriptive; if used, as a target for the exits-researcher).
- Control: random level same distance (retrace); matched bars (range).
- Evidence: section 5.3 (closed). Cost check: not applicable to the diagnostic; as a target it saves round trips only if it beats the control by >= 5 points (the H-49 bar).
- Expected events: measured 20,656 gaps on 15m and 4,794 on 1h in 2010-2017 (descriptive).
- Kill rule: retrace lift CI contains 0 (as H-49) AND range lift < 5% over matched bars on 2010-2017.
- Route: 0-trial diagnostic; if only the range lift survives, a feature for H-16/H-62 (K_eff +1). Prior that it adds beyond ATR and session: 15%.

### E-3  Asia-range sweep in London (15m/1h) - dropped as a rule
Closed by 6.4 (continuation +0.06 range units, t 1.53; 30% close back inside) and by #88. Keep only the depth/close-back
flags as features for H-48's roster monitor. Prior P(continue) 2%.

### E-4  Value-area 80% rule (daily value, 15m execution)
- Mechanism: acceptance back inside the prior day's value area implies rotation to the opposite edge (auction theory). Grade D; the hit rate in the literature is a vendor claim (unverified).
- Already tried?: new. Volume profile/POC sits in R-09 as a feature; slice D covers POC as a level.
- Clock: profile of the prior trading day (17:00 NY roll) from 1m bars with `tick_count`; rule checked from the NY open (08:30 NY, 30-min closes).
- Entry: open outside prior VA, two consecutive 30-min closes back inside; enter at the second close toward the opposite edge. Both sides. One position.
- Exit: stop beyond the first close's extreme (skip if > $6.65 at 0.01 lot, else demo-only), target the far VA edge, flat 17:00 NY.
- Control: random value-area edges (the same width and distance, random side) and random-time re-entries.
- Cost: stop $7-15 -> 0.01-0.02 R central, 0.04-0.09 stress.
- Events: measured 83 in 2010-2017 (about 10 a year), **below the 150 floor** (section 5.4).
- Kill rule: 2010-2017 real minus control CI contains 0, or fewer than 150 events in the discovery half.
- Route: closed on count (5.4). POC/VAH/VAL pass to slice D. Prior 1%.

### E-5  Wyckoff spring / upthrust on 1h (causal range)
- Mechanism: a break below a multi-touch range that fails (sweep + close back inside) traps late sellers; Osler stop cascades after the stops are taken. Grade D / C.
- Already tried?: new in this form (a long range requirement and two touches); the generic sweep fade (#88) is closed.
- Entry: row 13 spring at `a_j`; market at the next open; stop below the spring low (median $10-18, does not fit 0.01 lot at £1,000, demo only); target the TR midpoint then the TR high; 48 bars; both sides.
- Control: sweeps of random levels inside the same TR (same depth), and the random-bracket control.
- Cost: stop $14 -> 0.010 R broker, 0.05 R prior. Events: about 2-4 a month on 1h, about 400-700 total (below floor).
- Kill rule: fewer than 150 events in the discovery half, or CI of real minus control contains 0.
- Route: feature / demo arm only; no slot. Prior 2%.

### E-6  Effort-versus-result absorption (tick count)
- Mechanism: a bar at a level with high tick count and small range shows absorption (Wyckoff/VSA). Tick count is quote activity, not signed volume, so the sign is only the CLV.
- Rule: `z = (tick_count - median_20)/MAD_20 >= 2` and range <= 0.6 ATR, at a prior-day/Asia level, as a filter on a sweep or OB event.
- Control: same bars with z drawn from the same session. Kill: lift of net R of the filtered over the unfiltered set < 0.05 R or CI contains 0.
- Route: feature only (slice D owns volume; counted in K_eff there). Prior 5%.

### E-7  Premium/discount and Fibonacci OTE
Fibonacci levels are no better than random fractions (6.8). Premium/discount is the 50% level of the same range. Drop
as a rule. `p` (row 10) remains one column in the existing D-TVR group (0 trials).

## 8. Verdicts

| Topic | Verdict |
|---|---|
| Order block (retest) | closed at 0 trials after the control (5.2); no slot |
| FVG | closed (5.3); rule closed (TVR-1) |
| BOS / CHoCH | substrate only; rule closed (TVR-2 withdrawn, R-09) |
| Liquidity sweeps (Asia, prior day) | close as reversal (6.4, #88); keep as feature |
| Premium/discount, Fibonacci OTE | close (6.8) |
| Kill zones | gate/feature only; volatility windows, not signals |
| Wyckoff | spring/upthrust: demo arm (E-5); phases: drop |
| Market profile | value-area 80% closed on count (5.4); POC/VAH/VAL levels via slice D |
| Delta/footprint | footprint: drop (no tape); CLV/effort: feature (E-6) |
| Supply/demand, breaker/mitigation, inducement | drop (duplicates or discretionary) |

## 9. Flags for the director

1. **#88 cost flag: resolved, a real cost** (5.1). The era split decides what "net" means; 2010-2017 R-costs are 4-10x
   today's. Worth stating in every verdict on a 15m rule: report net at the research prior and at the broker cost.
2. **Zero trials proposed.** Everything in this slice that could be measured on 2010-2017 closed against its control.
   What remains open is only features: displacement bar, POC/VAH/VAL, sweep flags, `p` position in range.
3. **Hindsight bias is large and general** (T1 +1.10 ATR, T2 +0.88 R on gold from zero-edge construction): treat any
   quoted OB/FVG win rate as selected zones until a prefix-invariance test passes.
4. **Not measured:** Wyckoff spring (rare), absorption (needs slice D's volume work), 1h/4h order blocks (counts too
   low at pinned parameters, cf. H-34).
5. **What I would stop doing:** sweep reversal, Fibonacci/premium-discount levels, kill-zone gating as a signal, equal
   highs/lows, FVG/OB entry rules on 15m.

## 10. Looks taken

On gold, 2010-2017 only: T1 (2 statistics), T2 (2), ATR/spread by year (1 table), E-1 (gross, net prior, net broker x
real, 2 controls = 7 headline statistics), E-2 (4 retrace statistics x 2 timeframes, 3 range means x 2), E-4 (2 rows x 2
cost cases). About 30 statistics; one pinned parameter set, no parameter varied. On the random walk: 4 statistics, one
seed. Nothing is charged to K; nothing was read on 2018+ or the holdout.
