# Box and money-flow families: baselines, variations, pre-registration (2026-10-18)

Strategy researcher, 2026-10-18. Owner, the same day, after pasting his course notes on the box (support/resistance
channel) indicator, the money-flow oscillator, the structure overlay, the box strategy and a price-action course:
"build on these, don't strictly obey these, these are baselines"; "no input from me"; "backtest as far back as
possible". The notes stay local to the owner's machine and are not quoted here; this file uses our own names
(`sr_channels` = the box indicator, `mfi` = the money-flow oscillator, `smc` = the structure overlay), all three
replicated in `goldbot/features/smc_replica.py`.

**Outcome-blind attestation.** Sections 0-8 were written and committed **before** any event count of these rules
was computed; the commit that adds them holds no counts. Section 9 adds event counts, stop distributions and cost
ratios only, in a later commit. No return, R outcome, hit rate or label of any rule here has been computed or read.
Nothing reads a holdout bar (2025-10-01..2026-09-30).

## 0. Relation to earlier rulings (read first)

- **A7 / R-04 / R-01.** `hypotheses.md` A7 says box-break rules (R-04 breakout) and money-flow cross rules (R-01
  reversion) are "not trialled, by decision". This file reopens them as **rules** only because the owner's notes are
  new information about the *procedure*: a complete step list (level, oscillator state, confirmation timeframe,
  pullback entry, stop beyond the level, target at the next level, a reward floor, a no-trade zone). R-04 (#6, #8,
  #15) was a Donchian-style range breakout with ATR exits; R-01 (#3, #5, #10, #12) faded RSI/Bollinger/VWAP extremes
  with ATR exits. Neither used pivot-cluster levels, level-based stops and targets, or a two-timeframe confirmation.
  The prior is still low (section 2).
- **B4b (TVR) and H-34/H-35.** No TVR or H-34/H-35 rule is reworded here. Those were structure (BOS/CHoCH/FVG) rules
  with ATR or structure stops. The box families use box edges for stops and targets. What I already knew when
  writing this, and disclose: the TVR funnel (gap filter is sparse) and H-34's finding that structure stops on 15m
  are a median 3.3 ATR15 (not placeable at £1,000). The second is why the stop here is the owner's box-edge stop and
  why section 9 reports the stop distribution before anything is dispatched.
- **Ruling needed.** The research director (with the quant reviewer) rules whether this section meets A7/B4b before
  dispatch. Until then every row is *pre-registered, not funded*.

## 1. The owner's baselines, as procedures (our wording)

| Family | Owner's steps (paraphrased) |
|---|---|
| Box BREAK | 1h candle body closes through a box, ideally 20-30% beyond it; 1h money flow came from the opposite extreme (overbought before a break down, oversold before a break up); 15m breaks the same way with the same money-flow alignment; wait for a pullback to a box or to EMA5/10/20, whichever comes last; stop just beyond the broken box; target the next box; reward:risk ≥ 2; not inside the golden zone (50-61.8% retracement) |
| Box RIP (reaction in price) | 1h and/or 4h money flow at an extreme; a candle reaches a box without closing through it, then an opposite candle closes; stop beyond the rejection wick; target the next box; ≥ 2:1; not in the golden zone |
| Money-flow uses | divergence; double circle (back inside 80/20 after an extreme); 50-cross in the line's direction; entering an extreme as an exit; 40/60 pullback hold in a trend; "box = where, money flow = which side carries the risk; neither alone is a signal" |
| Price action (note 5) | continuation is the default; trade with HH/HL or LH/LL; in a range only at the extremes; reversal only at a major level; broken level flips and its retest is an entry; no fade into large-volume approach (trade with it instead); 1h entries on the close; avoid FOMC/NFP/CPI |
| Strategies (note 6) | the intraday trade must agree with the **daily** chart ("the most important rule"); daily/4h are the map (levels, trend), 1h is the trigger (on the close), 15m refines the entry only and is never where setups are found; never enter between key levels; take 2:1 or 3:1, partials, an occasional runner; 50% retracement confluence with an old swing level; trendline touches and EMA10/20 pullbacks with a candle signal |

## 2. Evidence and prior

| Claim | Source | Sample, instrument | Net of costs? | Grade |
|---|---|---|---|---|
| Published S/R levels predict intraday trend interruptions, strength varying by firm and currency | Osler 2000, FRBNY Economic Policy Review 6(2), 53-68 | DEM, JPY, GBP vs USD, 1-min quotes, 1996-03/1998 | gross (prediction, not a strategy) | single study (mechanism) |
| Stop-loss orders cluster just beyond round numbers and S/R; crossing them accelerates the move (breaks continue); take-profits cluster at the level (bounces) | Osler 2003, J. Finance 58(5); Osler 2005, J. Int. Money and Finance 24(2) | one bank's FX order book, 1999-2000 | gross (mechanism) | robust as an FX mechanism; no gold evidence |
| Prices bounce off detected S/R more often than chance; bounce probability rises with prior bounces | Garzarelli, Cristelli, Pompa, Zaccaria & Pietronero 2014, Scientific Reports 4, 4487 | LSE stocks, tick data 2002 *(period unverified)* | gross | single study |
| Algorithmic S/R levels reverse intraday trends at a significant rate; bounce probability decays with level age | Chung & Bellotti 2021, arXiv 2101.07410 (preprint) | intraday equity/FX data *(instrument unverified)* | gross | preprint |
| Technical rules show no out-of-sample edge after snooping control and costs | Bajgrowicz & Scaillet 2012, J. Financial Economics 106(3) | DJIA daily 1897-2011 | net | robust |
| Money-flow index (volume-weighted RSI) as a rule | no peer-reviewed study found (`indicator-survey.md`) | — | — | folklore grade |
| Our own: 1h range breakout gross +0.015 R (t 0.38); 15m fades no gross edge; 15m session-open breakout +0.042 R (t 1.31) | R-04 #6/#8/#15; R-01 #3/#5/#10/#12; R-02 | XAUUSD 2010-2025 | both | own evidence |

**Prior.** S/R levels have a gross mechanism (order clustering) but no documented net edge, and our own breakout and
fade families had no gross edge. P(screen pass) per family about 10-15%; P(full PASS by the batch bar) about 1-2%.
The value of the trials is that they answer the owner's baselines definitively; a FAIL closes the box topic as
rules (features stay).

## 3. Shared definitions (fixed now)

- **Bars.** Dukascopy 1m (release `data-v1`) cut at `research.holdout_from` **before** resampling, then
  `resample_bars` to 15m, 1h, 4h and the feature day (NY anchor); `mid`. Sample: first available bar to
  2025-09-30. Today that is 2010-01-03; if the 2003-2009 extension is in `data-v1` and has passed data QA before
  dispatch, the series starts at its first bar, the counts in section 9 are recomputed and the ladders re-applied,
  all before dispatch. MFI years with tick counts missing on > 5% of bars are excluded from MFI rungs only.
- **Boxes** on timeframe X: the replica's channel set in force at bar t with the owner's settings (pivot length 5,
  width 1% of the 300-bar range, 15 boxes, lookback 300), recomputed only on pivot-confirming bars, exposed per bar
  (the indicator lane's `trace`). Box k has top `T_k`, bottom `B_k`, height `h_k = T_k − B_k` (a single-pivot box is
  a line, h = 0), and `h_eff = max(h_k, 0.1 × ATR_X)`.
- **ATR_X.** Wilder ATR(14), seeded (`atr_seeded`), of timeframe X at t.
- **Money flow.** `money_flow(n=12)` on tick counts (owner's period); extremes 80/20.
- **MFI alignment** (break, "against the prior extreme"). Long-aligned at bar t of X when, over bars t-9..t, the most
  recent bar with MFI ≥ 80 or ≤ 20 had MFI ≤ 20; short mirrored. No extreme in the window = neutral = no trade.
- **Break** of box k on X at bar t, up: `close[t-1] ≤ T_k`, `close[t] ≥ T_k + d × h_eff`, `close[t] > open[t]`
  (body through, wicks do not count); down mirrored on `B_k`. Several boxes: the farthest one crossed. Baseline
  d = 0.2 (the lower end of the owner's 20-30%, read as *beyond the far edge*; see 3.1).
- **Golden zone** (1h): the entry close lies between the replica's 0.5 and 0.618 retracement of the last 50 1h bars.
  Inside = no trade.
- **Next box** (target). From the box set of the box timeframe visible at entry: long, the lowest `B_k` above the
  entry; short, the highest `T_k` below it. None = no trade.
- **Reward floor.** `(target − entry) / (entry − stop) ≥ 2` (long; short mirrored).
- **Placeable.** The engine's own entry gate: target distance ≥ `risk.min_target_over_cost` (2.5) × the cost prior
  (entry-bar spread + 0.37 $/oz); stop distance ≥ $0.20 (stops level 0 + 20-point spread prior). Not placeable = no
  candidate (it could never be ordered).
- **Daily trend (core, note 6).** From the feature-day bars: classic pivots of length 3 confirmed by the visible day
  (`pivot_points`, stamped 3 days after the extreme). Up when the last two confirmed daily pivot highs **and** the
  last two pivot lows are both higher (HH+HL); down when both lower; else range. **D0:** a long needs the daily trend
  up, a short down. In a daily range, only H-38 (fade) may trade, and only within the outer 20% of the last 20
  feature days' high-low range (note 5: "in a range, trade only the extremes"). D0 is in every rung and is dropped
  only by the last rung of each ladder; a verdict on that rung is labelled "without the daily rule".
- **Hierarchy (note 6).** Map timeframe (daily/4h: trend and levels) → trigger timeframe (1h, on the close) →
  refinement (15m entry only). Each family's L0 uses 1h boxes with the 1h trigger (15m refines H-37's entry); the
  first pooling rung adds the **4h map with the 1h trigger**. Setups found on 15m alone (H-41) rank lowest.
- **Between levels.** Every entry here is at a box by construction (break-retest, touch, retest); "no level, no
  trade" holds without an extra filter.
- **Calendar.** Entries inside the engine's blackout (15 min before to 30 min after CPI, NFP, FOMC, PCE) are not
  candidates where `calendar-v1` covers the date; uncovered years are reported, not imputed.
- **Exits.** Stop and target as each family states; time exit 96 bars (15m entry) / 48 bars (1h entry) / 24 bars
  (4h entry). Stop first when both fall in one bar. One position at a time in total (all members of a pooled rung
  share it). Costs: bar spread + 0.37 $/oz + swap prior (−60 USD/lot/night long, 0 short); stress 0.71 $/oz.
- **Label plumbing (dispatch blocker K-T).** R = the candidate's stop distance (`barrier_atr` = stop / ATR) and the
  target is a per-candidate multiple of R (next box), which `BarrierSpec` cannot express today (fixed `target_atr`).
  Blocker: a per-candidate target hook (`barrier_target_r`), its lookahead test and quant review. **Fallback fixed
  now:** if the hook is not merged by dispatch, the target is exactly 2R (the floor) and the verdict is labelled
  "2R proxy for next-box target"; no second trial is run for the other form.
- **Point in time.** At a 15m decision bar, 1h/4h values are those of the last bar with `visible_at` ≤ the 15m bar's
  close; boxes, pivots and MFI are stamped on the bar whose close makes them known. A prefix-invariance test on the
  specialist is a dispatch precondition.

### 3.1 The break-depth reading (flag for the indicator lane)

The indicator lane's draft `sr_break_depth` measures the close's penetration **into** the box from the near edge
(1.0 = at the far edge) and calls ≥ 0.2 a break. That is a close *inside* the box, which the owner's notes call a
touch without a close through (a RIP condition), and it disagrees with the replica's own break arrow (close at or
beyond the far edge). The baseline here reads "20-30% through" as the close at least 0.2-0.3 box heights **beyond
the far edge**. In lane units that is `depth ≥ 1.2`. Feedback to the lane: expose the depth from the far edge (or
keep the column and document 1.0 as the arrow threshold). No variant uses the inside reading.

## 4. Families and their pre-registered count ladders

Each family is **one trial**, running **exactly one rung**: the first rung, in the order below, whose candidate
count (2010-01-01..2025-09-30, both sides, after the placeable rule and a timestamp-only one-at-a-time: a candidate
is dropped when it falls within the time exit of the previous kept candidate, which is the fewest trades the real
one-at-a-time can leave and reads no price after entry) is **≥ 1,500** (gate 1,
`MIN_CANDIDATES`; above P4's 1,000). If no rung reaches 1,500 the family is **withdrawn, 0 trials**. Rungs relax
only in the order fixed here: first pooling the owner's own timeframes, then the owner's own documented looser
threshold, then dropping the condition with the weakest mechanism. Counts select the rung; nothing else does.

### 4.0 H-45 `tls` — the master family: Trend + Level + Signal (note 6, the course's entry checklists)

The course's core framework requires a trend, a level and a 1h-close signal together; its "Long A / Short A"
checklists are the exact with-trend entries. H-45 encodes them as **one configuration**; the box, pin-bar and
inside-bar/fakey families (4.1-4.8) are its sub-signals and are folded into it by the overlap rule below.

Trigger: the close of 1h bar t. 4h and daily values are the last visible bars. Long A (Short A mirrored where the
course gives a mirror; its asymmetric rules are kept as written):

| Code | Long A | Short A |
|---|---|---|
| T1 | 4h trend up: the last two confirmed 4h pivot highs and the last two pivot lows (`pivot_points`, length 3) both higher; and the 4h close above 4h EMA10 and EMA20 | 4h LH+LL; close below EMA10 and EMA20 |
| T2 | D0 (daily trend up; "intraday must agree with daily") | D0 down |
| L1 | the bar touches a key level L from above and closes above it: `l[t] ≤ L + 0.1 ATR1h`, `c[t] ≥ L`. Levels: the top of a 1h or 4h box below the prior close, 1h EMA20, the 50% level of the last 50 1h bars, or the top of a box broken upward in the last 48 1h bars (broken resistance) | bottom of a box above, EMA20, 50% level, bottom of a box broken downward (broken support); trendlines are not built (no feature), noted |
| S1 | a bullish 1h signal candle: hammer (lower wick ≥ 0.6 range, body ≤ 0.35 range), bullish engulfing (bar t−1 bearish, `o[t] ≤ c[t−1]`, `c[t] ≥ o[t−1]`), piercing (t−1 bearish, `o[t] ≤ c[t−1]`, `c[t]` above the midpoint of t−1's body, below its open), morning star (t−2 bearish with body ≥ 0.5 range, t−1 body ≤ 0.3 range, t bullish closing above the midpoint of t−2's body), inverted hammer (upper wick ≥ 0.6 range, body ≤ 0.35 range, lower wick ≤ 0.1 range) | shooting star, bearish engulfing, dark cloud, evening star (mirrors), a false break (high above L + 0.1 ATR1h, close back below L), "wicked off the line" (upper wick ≥ 0.5 range crossing L, close below L) |
| M1 | money flow: the 4h or 1h MFI was < 40 within the last 5 bars of its timeframe and is rising now (`mfi[t] > mfi[t−1]`), **or** the 1h MFI crosses above 50 at t | the 1h MFI was ≥ 60 within the last 5 bars and is falling now |
| X (invalid) | mid-range (close between 0.4 and 0.6 of the last 50 1h bars' range); 1h MFI > 60; no candle; reward < 2 | 4h MFI < 30; mid-range; no candle; reward < 2 |
| SL | below the wick and the zone: `min(l[t], zone bottom) − 0.1 ATR1h` (zone bottom = the box bottom when L is a box top, else L) | mirrored |
| TP | TP1 = next 1h box edge above; reward to TP1 ≥ 2; TP2 = the next box edge after TP1 (else TP1) | mirrored |
| E | entry at the **next** 1h bar's open (the course's pre-trade checklist); reward to TP1 is computed before entry on the signal close, and the candidate is kept even if the open gaps (the fill price sets R) | same |
| Exit | 80% at TP1, stop to breakeven, 20% runner to TP2; the stop is never widened; time exit 48 1h bars; a missed entry is not chased (no re-entry on the same level) | same |

Plus placeable and calendar (section 3). One position at a time.

**Exit plumbing (dispatch blocker K-X).** `labels.exit_policy.ExitPolicy` supports one scale-out at a fixed
multiple of the barrier ATR (`scale_atr`, `scale_fraction` 0.8 is allowed) and an ATR trail, but not (a) a
per-candidate scale level (TP1 = next box, variable in R), (b) moving the stop to breakeven after the scale-out,
or (c) a per-candidate final target (TP2). Needed: `scale_r` per candidate, `breakeven_after_scale: bool`, and the
per-candidate target hook of K-T, implemented identically in labels, shadow book and engine
(`engine/runner.py` modify after the partial) with a lookahead test and the trading-safety review. **Fallback fixed
now:** if K-X is not merged at dispatch, the exit is the plain barrier at TP1 (100% at TP1, no runner), labelled
"TP1-only proxy"; no second trial is run for the other exit.

**Course "B" setups (rung L1, pooled with A).** Long B (counter-trend): 4h trend down, 4h MFI < 30 with two
crossings under 20 within the last 12 4h bars (double circles), L a **major** level (`major_level`), a hammer that
closes off the line (S1 hammer with `l < L < c`), the 1h **and** 4h MFI < 30, reward ≥ 2, exit 100% at TP1 ("take
profit early"). Short B (false break at major resistance, with the 4h downtrend): high above the top of a major box,
upper wick ≥ 0.5 range, close back below the box bottom; stop above the wick high + 0.1 ATR1h; 100% at TP1
("fast"). Long B has no course mirror for shorts; it is encoded as written (long only); Short B as written (short
only).

| Rung | Content |
|---|---|
| L0 | Long A + Short A exactly (T1 T2 L1 S1 M1 X) |
| L1 | L0 ∪ Long B ∪ Short B |
| L2 | L1 without M1 (money flow) |
| L3 | L2 without T2 (the daily rule; T1's 4h trend stays) |

**Confluence count** (the course's 12 factors, at each H-45 candidate): 4h trend aligned; level is a box; level is a
4h box or within 0.25 ATR1h of the previous day's pivot R1/S1/PP; EMA20 within 0.25 ATR1h of L; the 50% level within
0.25 ATR1h of L; the signal is a pin or engulfing; 1h or 4h MFI at ≤ 20 / ≥ 80 in the last 5 bars; L is a broken-level
retest; event area (*not built*: origin zones of impulse moves, counted 0 until a feature exists); volume declining
(mean tick count of t−3..t−1 below that of t−13..t−4); closes off the line (`l < L < c` long); trendline touch (*not
built*, 0). The thresholds ≥ 3, ≥ 4 and ≥ 6 are **subsets** of the run rung, so they are not separate trials: the
count enters H-40 as one ordinal group `tls_count` (K_eff +1), and section 9 reports the candidates at each
threshold. A threshold whose subset has ≥ 1,500 candidates and survives H-40 may go forward as one of H-40's two
survivor trials.

**Overlap rule (sub-families fold into H-45).** For each of H-37, H-38, H-39, H-42, H-44, H-46 and H-47: if more than 50% of
its run-rung candidates are also H-45 run-rung candidates (same side, H-45 bar within ±2 1h bars of the
sub-family's entry), the sub-family is **withdrawn** (0 trials) and survives only as an H-40 flag on H-45's
candidates. Below 50%, it runs on its own.

### 4.1 H-37 `box_break` (owner's break trade)

Box timeframe 1h, confirmation and entry on 15m (pair P1). Long (short mirrored):

| Code | Condition |
|---|---|
| K0 | D0: daily trend up |
| K1 | 1h break up of box k at 1h bar T (d = 0.2) |
| K2 | 1h MFI long-aligned at T |
| K3 | a 15m break up (15m boxes, d = 0.2) on a 15m bar closing in (T − 1h, T + 4h], with the 15m MFI long-aligned there |
| K4 | armed at A = the later of T and the K3 bar. Pullback: the first 15m bar t within 16 bars after A with `low[t] ≤ P` and `close[t] > P`, where P = min(T_k, EMA20 of 15m) ("whichever comes last": the farther of the broken box and the slowest of the owner's MAs). Entry at close[t] |
| K5 | stop = `B_k − 0.1 × ATR15`; skip if entry ≤ stop |
| K6 | target = next 1h box; reward floor 2 |
| K7 | entry not in the 1h golden zone |
| — | placeable; calendar; one candidate per 1h break (first pullback only) |

Pair P2 (map 4h, trigger 1h): box timeframe 4h, confirmation and entry on 1h, golden zone on 4h,
pullback window 8 1h bars, EMA20 of 1h, ATR1h buffer.

| Rung | Content |
|---|---|
| L0 | P1, K0-K7 |
| L1 | P1 ∪ P2 pooled, K0-K7 |
| L2 | L1 with MFI extremes 70/30 (owner: "~70-80 / ~20-30") |
| L3 | L2 without K7 (golden zone: no mechanism) |
| L4 | L3 without K3 (15m confirmation) |
| L5 | L4 without K2 (MFI): bare box break with pullback entry and box exits |
| L6 | L5 without K0 (daily rule) |

Mechanism: stops beyond a level are triggered by the break and push price on (Osler 2003); a pullback entry buys
the retest with a stop beyond the box, where a failed break is proven. Money flow coming from the opposite extreme
marks the side that has just been exhausted.

### 4.2 H-38 `box_rip` (owner's rejection trade)

Box timeframe 1h, candles 1h, entry on the 1h close (pair Q1). Short (long mirrored):

| Code | Condition |
|---|---|
| J0 | D0 for a with-trend fade (short only in a daily downtrend), or the daily range-extreme rule |
| J1 | bar t-1 is bullish, `close[t-2] < B_k`, `high[t-1] ≥ B_k`, `close[t-1] < T_k` (touch from below, no close through) |
| J2 | bar t is bearish and `close[t] < B_k` (the opposite candle closes back outside) |
| J3 | 1h MFI ≥ 80 on any of t-2..t, or the last visible 4h MFI ≥ 80 |
| J4 | stop = max(high[t-1], high[t]) + 0.1 × ATR1h |
| J5 | target = next 1h box below; reward floor 2 |
| J6 | entry not in the 1h golden zone |
| — | placeable; calendar |

Pair Q2 (map 4h, trigger 1h): 4h boxes, 1h candles, the 4h MFI or the daily MFI for J3, 4h golden zone, ATR1h buffer.

| Rung | Content |
|---|---|
| L0 | Q1, J1-J6 |
| L1 | Q1 ∪ Q2 pooled |
| L2 | L1 plus counter-trend fades at a **major** level (note 5: the box overlaps a 4h box, or lies within 0.25 ATR1h of the previous day's floor pivot R1/S1 or of the 50-bar 1h extreme) |
| L3 | L2 with 70/30 |
| L4 | L3 without J6 |
| L5 | L4 without J3 (a plain box fade with wick stop and box target) |
| L6 | L5 without J0 (daily rule) |

Mechanism: take-profit orders cluster at levels and halt moves there (Osler 2003); S/R bounces exceed chance in
equities (Garzarelli et al. 2014). Reversal component: R-01's prior applies.

### 4.3 H-39 `box_retest` (note 5: a broken level flips; the retest is the entry)

Box timeframe 1h, 1h candles and entry. Long (short mirrored): a 1h close beyond the top of box k
(`close[s-1] ≤ T_k < close[s]`, any depth) at bar s; within the next 24 1h bars, the first bar t with
`low[t] ≤ T_k`, `close[t] > T_k` and bullish (rejection from the old resistance), with no close below `B_k` between
s and t. Entry at close[t]; stop = `low[t] − 0.1 × ATR1h`; target = next 1h box; reward floor 2; placeable;
calendar; one candidate per break.

| Rung | Content |
|---|---|
| L0 | D0, 1h boxes, 1h candles |
| L1 | D0, 1h ∪ 4h boxes pooled, 1h candles throughout (4h map, 1h trigger) |
| L2 | L1, window doubled (48 1h bars) |
| L3 | L2 without D0 |

No money-flow condition: note 5 uses price and volume only. Mechanism: the role reversal of S/R (Osler 2000 tests
interruptions at levels; the flip itself is practitioner evidence only).

### 4.4 Money-flow uses (no trial; feature group)

The owner says the oscillator is never a signal alone, and as standalone rules its uses are reversion (R-01) or
have no mechanism. Its six signals (`mfi_regime`, `mfi_extreme_circle`, `mfi_double_circle`, `mfi_cross50`,
`mfi_pullback_hold`, `mfi_divergence`, lane `goldbot/features/mfi_signals.py`) enter **as one feature group
`mfi_signals` on 15m, 1h and 4h** in O-4 (H-14, 15m discovery) and in H-40 below, K_eff +1 each. Section 9 records
their event counts so the tested universe is honest. No standalone money-flow rule is pre-registered.

### 4.5 H-41 `box_break_15m` (owner: "15m boxes = scalping"; conditional, not funded)

H-37's K1-K7 with 15m boxes, 15m MFI, entry on the 15m bar after a 15m break, pullback window 8 15m bars, 15m
golden zone; no confirmation timeframe. Counted in section 9; funded only if H-37 is LEAD or PASS (it is H-37's
neighbour at the scalping horizon, where costs per R are highest).

### 4.6 H-42 `pin_bar` (note 6: the course's first-ranked reversal signal)

Trigger 1h, entry on the 1h close (pooled rung: 4h). **Key levels** at bar t: the edges of the boxes in force (1h
boxes; 4h boxes on the 4h rung), EMA20 of the trigger timeframe, and the 50% level of the last 50 trigger bars
(`range_retracement` 0.5). Long (short mirrored):

| Code | Condition |
|---|---|
| N0 | D0: daily trend up |
| N1 | pin: range `h−l > 0`; lower wick `min(o,c) − l ≥ 0.65 (h−l)`; body `|c−o| ≤ 0.25 (h−l)` |
| N2 | the wick penetrates a key level L and the bar closes back above it: `l < L < c` (choose the highest such L) |
| N3 | after a retrace: the 20-bar high before t minus `l[t]` ≥ 1.5 ATR (a pullback happened; excludes pins in the trend direction without one) |
| N4 | not extended: `c[t]` is more than 0.5 ATR below the 50-bar high (no long pin at a fresh extreme) |
| N5 | stop = `l[t] − 0.1 ATR`; target = next box edge above (the lowest bottom above c); reward floor 2; placeable; calendar |

| Rung | Content |
|---|---|
| L0 | 1h, N0-N5 |
| L1 | 1h ∪ 4h pooled |
| L2 | L1 plus counter-trend pins (against D0) only when the pin's range ≥ 1.5 ATR ("large, obvious") and L is a major level (`major_level`, section 5); in a daily range, pins only within the outer 20% of the 20-day range |
| L3 | L2 with body ≤ 0.33 (h−l) |
| L4 | L3 without N0 |

**Overlap with H-38.** A pin at a box edge is a one-bar RIP. If more than 50% of H-42's run-rung candidates are also
H-38's run-rung candidates (same bar and side, or H-38 one bar later), H-38 is withdrawn in favour of H-42 (the
owner ranks the pin first). Below 50%, both run.

Variant flags (into H-40): `pin_ema20` (L is EMA20 in a trend: the course's "EMA20 pullback"), `pin_retest` (L is
a box broken in the trade direction within the last 24 trigger bars: "first retrace after an initial burst", the
course's top variant).

### 4.7 H-43 `inside_bar` (note 6: continuation on the 4h/daily, stop-entry)

Signal timeframe 4h (pooled rung: the feature day). Long (short mirrored):

| Code | Condition |
|---|---|
| I0 | D0: daily trend up |
| I1 | inside bar t: `h[t] ≤ h[t−1]` and `l[t] ≥ l[t−1]` (mother bar t−1) |
| I2 | core level: the mother bar's range contains a 4h box edge or the 4h EMA20, and `c[t]` > EMA20 (respecting the EMA) |
| I3 | entry: a buy-stop at `h[t−1] + $0.10`, live for the next 3 bars, filled on the first bar whose high reaches it, at max(stop price, that bar's open); cancelled if a bar trades below `l[t−1]` first (same bar: cancelled, the pessimistic order) |
| I4 | stop = `l[t−1] − $0.10`, or beyond a pin bar (N1) at t−2 if one preceded the mother bar and its wick is lower; target = next box edge above the stop price; reward floor 2 on the stop price; placeable |

| Rung | Content |
|---|---|
| L0 | 4h, I0-I4 |
| L1 | 4h ∪ 1d pooled (daily inside bars, 4h boxes and the daily EMA20; the order lives 2 days) |
| L2 | L1 without I2 |
| L3 | L2 without I0 |

Dispatch blocker **K-S**: research, the shadow book and the engine fill only at a bar close today. A stop-entry
fill model (fill price, cancel rule, the owner's 90 s click at placement rather than at fill) needs a design and the
trading-safety review. Until it exists H-43 cannot be dispatched.

Variant flags (into H-40): `pin_inside` (a pin bar at a key level at t−2, the course's "low-risk reversal setup"),
`ema8_21` (EMA8 above EMA21 for a long, the course image's pair).

### 4.8 H-44 `fakey` (false break of the inside bar; note 6)

Signal timeframe 4h, entry at the close of the false-break bar (no stop order needed). Long (short mirrored):

| Code | Condition |
|---|---|
| F0 | D0: daily trend up, and 4h EMA8 > EMA21 (4h momentum aligned) |
| F1 | an inside bar at t−k (k = 1..3) with mother bar m = t−k−1 |
| F2 | bar t false-breaks **against** the trend and closes back inside: `l[t] < l[m]` and `c[t] > l[m]` (and no bar between t−k and t closed outside the mother range) |
| F3 | stop = `l[t] − 0.1 ATR4h`; target = next 4h box edge above; reward floor 2; placeable; calendar |

| Rung | Content |
|---|---|
| L0 | 4h, F0-F3 |
| L1 | 4h ∪ 1h pooled (1h fakeys need the daily trend and 1h EMA8 > EMA21) |
| L2 | L1 plus counter-trend fakeys only when bar t's range ≥ 1.5 ATR and the mother range holds a major level, and **not** when the inside bar sits on EMA20 support/resistance for the trend side |
| L3 | L2 without the EMA8/21 condition |
| L4 | L3 without D0 |

### 4.10 H-46 `liquidity_sweep` (note 7: false breakout of tested resistance, structure break, retest)

Trigger and entry on the 1h close; boxes 1h (pooled rung: 4h boxes, 1h candles). Short (long mirrored):

| Code | Condition |
|---|---|
| W1 | tested resistance: a box above the prior close whose range was touched by the high of ≥ 2 of the last 100 1h bars |
| W2 | sweep: bar t trades above the box top (`c[t−1] ≤ T`, `h[t] > T`) |
| W3 | failure: a close back below the box bottom within 4 bars (bar f); sweep high SH = max high t..f |
| W4 | structure break: within 12 bars of f, a close below the last confirmed 1h swing low before t (`pivot_points`, length 3), with no high above SH first |
| W5 | retest: within 12 bars of the break, the first bar with `high ≥ level − 0.1 ATR1h`, close below the level and bearish (rejection); no close above SH first. Entry at its close |
| W6 | stop = SH + max(0.25 ATR1h, 2 × bar spread) ("not exactly on the level; allow for overshoot") |
| W7 | TP1 = next 1h box edge below; TP2 = the new low of the bearish move (if beyond TP1, else TP1); reward to TP1 ≥ 2; placeable; calendar |
| Exit | the owner's risk rules (note 6): 80% at TP1, stop to breakeven, runner to TP2 (K-X; fallback TP1 only) |

| Rung | Content |
|---|---|
| L0 | 1h boxes, W1-W7 |
| L1 | 1h ∪ 4h boxes pooled |
| L2 | L1 without W1 (any box) |
| L3 | L2 with the windows doubled (8 / 24 / 24 bars) |

The note gives no daily-trend rule for this setup, so D0 is not in the ladder; it enters H-40 as the flag
`daily_with` (K_eff +1). **Disclosure:** H-35 (withdrawn) was a 15m CHoCH after a 1h box break with a structure
stop; H-46 differs in timeframe, entry (retest of the broken swing, not the CHoCH) and stop (above the sweep high).
I knew H-35's counts when writing this. Rank: 2, after H-45 (gold-specific mechanism: stop runs above obvious
levels, Osler 2005 cascades; no gold study).

### 4.11 H-47 `sweep_displacement` (note 11: session liquidity, sweep, displacement)

Detection and entry on the 15m close (pooled rung: 1h). M5/M1 execution, which the note mentions, is **out of
scope**: research, the shadow book and the engine act on 15m/1h closes only. Short (long mirrored):

| Code | Condition |
|---|---|
| Y0 | HTF bias: daily trend down (D0) and the 4h trend (4.0 T1 structure) not up |
| Y1 | session gate: the entry bar closes inside the London or New York window (`DEFAULT_SESSIONS`, DST-aware, the same windows as `features/trader.py`) |
| Y2 | liquidity L above the prior close: the high of each of the last completed Asia, London and New York session instances, or one of the last three 1h equal-high levels (replica `eqh`, its pivot value) |
| Y3 | sweep: bar s trades above L and closes back at or below it (same bar), or the next bar closes back below L |
| Y4 | displacement: within 4 bars after s, a bearish bar with body ≥ 1.5 ATR15 closing below the sweep bar's low; entry at its close |
| Y5 | stop = sweep high + max(0.25 ATR15, 2 × bar spread); target = the nearest opposite liquidity below (a previous session low or an equal low) or else the next 1h box edge; reward ≥ 2; placeable; calendar |

| Rung | Content |
|---|---|
| L0 | 15m, Y0-Y5 |
| L1 | 15m ∪ 1h pooled (1h: ATR1h, 4-bar windows) |
| L2 | L1 with displacement ≥ 1.0 ATR |
| L3 | L2 without Y0 |

The displacement stop sits above the sweep high, at least 1.5 ATR from the entry; H-34 found such structure stops
unplaceable at £1,000 on 15m, so section 9 reports the stop distribution before anything is funded. Rank 3 (after
H-46: the same mechanism, H-46 has the narrower stop at the retest).

### 4.12 Session hard gate (note 11; a filter on every family, 0 trials)

`session_ln_ny`: the entry bar closes inside the London or New York window. It is built into H-47 (Y1). For every
other family it is (a) an H-40 flag (K_eff +1) and (b) a pre-registered contrast reported by the evaluator with each
verdict: the gross mean R of the candidates inside vs outside the gate (Welch t), read-only and selecting nothing.
A family's rung is never re-run with the gate unless the gate survives H-40.

### 4.13 Regime filters (note 13, "no-trade is valid"; every family, 0 trials)

Each is an H-40 flag (K_eff +1 each) **and** a read-only contrast the evaluator reports with every verdict: the share
of candidates removed and the gross mean R kept vs removed (Welch t). Fixed now:

| Flag | Rule at entry (removes the candidate when true) |
|---|---|
| `chop` | in the last 24 1h bars there was both an up-sweep (high above the prior 50-bar 1h high, close back below it) and a down-sweep (mirrored) |
| `stretch` | the entry is in the direction of a stretch: `(close − EMA50_1h) / ATR1h > 3` for a long (< −3 for a short) |
| `atr_expansion` | 4h ATR(14) > 1.3 × the mean of its last 14 values. The note sizes ×2/3 here; at our 0.01-lot minimum a smaller size does not exist, so the rule becomes **skip**. Flagged to the CFO / adaptive-limits owner as a candidate tighten rule (ADR 0031): it would reduce, never raise, exposure |
| `news` | the engine's calendar blackout, already applied to every family (section 3) |

### 4.9 Ranking (expected information per trial)

| Rank | Row | Why |
|---|---|---|
| 0 | H-45 TLS (Long A / Short A) | the course's master framework and exact checklists; trend + level + signal combines the one mechanism with own evidence (trend) and the S/R order-clustering mechanism; sub-families fold into it |
| 1 | H-46 liquidity sweep | the owner's gold-specific setup; stop-cascade mechanism (Osler 2005); fully specified entry, stop and two targets |
| 1a | H-47 sweep + displacement | owner's pre-entry checklist (session liquidity, displacement); same mechanism as H-46 with a wider stop |
| 1b | H-37 box break | owner's primary trade; continuation (the owner's default) with a stop-cluster mechanism; the only family with a link to our one gross edge (trend) |
| 2 | H-42 pin bar | owner's top-ranked signal, at levels (S/R mechanism); the wick stop is small, so cost per R decides it (section 9) |
| 3 | H-44 fakey (with trend) | with-trend entry at a failed counter-move; closes on a bar (no new infrastructure); 4h events are few |
| 4 | H-39 retest | S/R role reversal; practitioner evidence only |
| 5 | H-38 RIP | reversal (R-01 prior); partly duplicated by H-42 (overlap rule) |
| 6 | H-43 inside bar | needs the stop-entry model (K-S); 4h/daily events are few |
| 7 | H-41 15m box break | 15m-only setups rank lowest (note 6); highest cost per R |
| — | H-40 | one trial over whatever runs; its value grows with the number of families that run |

## 5. Variations (small, ranked by prior) and how they are tested

Variations that **select a subset** of a family's candidates are tested together in **one** pre-registered
filter-discovery trial, H-40, not one trial each. Variations that are **supersets** are not tests: they appear
only as ladder rungs above (pooling, 70/30) or are not run.

| Rank | Flag (point in time at entry) | Applies to | Prior / reason |
|---|---|---|---|
| 1 | `trend_with`: the **1h** structure trend also agrees with the side (D0 already holds the daily). Trend = up when the last two confirmed 1h pivot highs (pivot length 5) and the last two pivot lows are both higher (HH+HL), down when both lower, else range | BX, RT; RP as "with-trend RIP" | note 5 "continuation default"; the only filter tied to our one gross edge (R-06 trend) |
| 2 | `major_level`: the box overlaps a 4h box, or lies within 0.25 ATR1h of the previous day's floor pivot R1/S1 or of the 50-bar 1h extreme | all | note 5 "trade significant levels from longer timeframes"; Osler: longer-horizon levels hold more orders |
| 3 | `vol_veto` (RP): none of the 3 approach bars before t-1 nor t-1 itself is a high-volume candle in the approach direction (tick count > 1.5 × EMA20 of tick count, the box indicator's own rule) / `vol_with` (BX): the break bar is a high-volume candle in the break direction | RP / BX | note 5 volume veto; the replica's candle code |
| 4 | `depth_30`: break depth ≥ 0.3 | BX | owner's upper bound |
| 5 | `rr_3`: reward floor 3 | BX, RP, RT | larger target per cost |
| 6 | `day_bias`: long only above the daily pivot PP, short only below | all | owner's structure-overlay rule; folklore |
| 7 | `premium_discount`: long only below the 1h 50-bar midpoint, short only above | RP, RT | folklore; opposes continuation for BX, so not applied to BX |
| 8 | `engulf`: the signal candle engulfs the previous body (replica candle code 4) | RP, RT | candlestick confirmation; folklore |
| 9 | `anchor_box`: BX pullback anchor = the broken box only (not EMA20) | BX | owner's alternative |
| 10 | `fib50_confluence`: the entry lies within 0.25 ATR1h of the 50% retracement of the last confirmed 1h swing **and** of a previous 1h swing level broken in the trade direction | all | note 6 (ABCD); folklore |

Not run, with reason: partial exits at 2R/3R with a runner (note 6) and the channel midpoint partial (note 5) are exit variants, pre-registered later as one neighbour of a PASS/LEAD only (an exit cannot rescue a rule without a gross edge); trendline touches, EMA10/20 slope, gap and pullback-with-candle (note 6) go to O-4 as features, not rules here; reward floor 1.5 (lowers target per cost, the one thing costs punish); `trend table`
(the owner's trend/ranging table has no replicated definition; `trend_with` is our stated rule for the same idea);
order blocks (the owner has them off); the price-action course's channel trading and partial exit at the midpoint
(a new exit family; candidate for the next generation after these verdicts); EMA claims of note 5 (touch rates)
are diagnostics, section 7.

### 5.1 H-40 `box_filter_discovery` (one trial)

- **Candidates:** the union of the rungs that run for H-45, H-37, H-38, H-39, H-42, H-43, H-44, H-46 and H-47 (their own labels, same exits). If a
  family is withdrawn, its candidates are absent; if all are withdrawn, H-40 is withdrawn.
- **Features:** the ten flags of section 5, the four family flags of 4.6-4.7 (`pin_ema20`, `pin_retest`, `pin_inside`, `ema8_21`), the ordinal `tls_count`, `daily_with` (H-46), `session_ln_ny`, `chop`, `stretch`, `atr_expansion`, plus the `mfi_signals` group (4.4), each a group.
- **Method:** `research.yml -f discover=true`, `DiscoveryConfig` defaults as pre-registered for O-4
  (`survivor_unit: group`, fold-internal stability selection on training folds only). K_eff = groups screened (21).
- **Survivor rule:** a flag survives when it is selected in ≥ 60% of training folds **and** its kept-vs-rejected gross
  difference has Welch t ≥ 2 inside the training folds. At most **two** survivors may go forward, each as its own
  pre-registered trial (the family's rung + the flag), read by section 6 at the K of that date. No survivor: the
  variations are recorded as failed in `hypotheses.md` and not re-tested without new evidence.

## 6. Reading rule (fixed now)

Each trial is read alone, by the evaluator, word for word by `batch-2026-10-18.md` section 2, rules 1-6 (P4 screen:
gross mean R > 0, t ≥ 2 on ≥ 1,000 events; rule-only net mean R > 0 with day-clustered t ≥ 2; DSR ≥ 0.95; ≥ 300
out-of-fold trades with ≥ 30 in 2024-01..2025-09; year stability incl. 2024 and 2025; cost stress at 0.71 $/oz).
Changes:

- **K** for rule 3 is the registry's `n_trials_effective` at the verdict date, **never below 86** (the highest floor
  already pre-registered, 77, plus H-37..H-39 and H-42..H-47). Gate-1 per-trade t about 4.1.
- **Baseline lift (can only downgrade).** For H-37 and H-38 the evaluator also reports the gross mean R of rung L5 /
  L4 (the bare level rule, same exits). If the run rung does not beat it with Welch t ≥ 1, the verdict carries
  "owner's confirmations add nothing" and a PASS becomes a LEAD.
- **Rung and sides** are named in the verdict; long and short reported separately.
- **Placeability** is reported at £1,000 / 0.01 lot with today's ATR scaling (section 9.3 method). It does not
  change the verdict; a PASS that is not placeable at £1,000 is shadow-only until equity allows (owner decision).

Verdicts: **PASS** all rules + lift: shadow founder; H-41 (for H-37) becomes fundable. **LEAD** rules 1, 2, 5 hold:
shadow only. **FAIL** screen fails at ≥ 1,000 events or net ≤ 0: the family is retired as a rule (R-10 box break,
R-11 box rejection, R-12 retest), with the features kept. If H-45 and every sub-family that ran FAIL, the box/money-flow/price-action rule
topic is closed; only the owner's own trades (H-36) may reopen it.

## 7. Zero-trial diagnostics (descriptive, not rules)

- **"70% of reversals fail"** (note 5): share of confirmed 1h swing pivots after which the prior trend resumes
  (next pivot beyond the previous extreme). A property of price, not of any rule here; reported once, no rule is
  chosen from it.
- **"EMA20 on 1h touched on 98% of days"**, "price above 1h EMA10 tends to continue": touch frequency only; the
  continuation claim is a trend rule (R-06 territory) and is not trialled.
- **Box geometry**: box heights in ATR by timeframe and year; the share of line-boxes (h = 0).

## 8. Budget, K and owner decisions (flagged, not taken)

| Item | Trials |
|---|---|
| H-45 (one rung, after K-X or with the TP1 proxy) | 1 |
| H-46, H-47, H-37, H-38, H-39, H-42, H-44 (one rung each, if ≥ 1,500 and not folded into H-45) | up to 7 |
| H-43 (after K-S) | 1 |
| H-40 filter discovery | 1 (K_eff 21) |
| H-40 survivors | up to 2, conditional |
| H-41 | 1, conditional on H-37 PASS/LEAD |
| `mfi_signals` group in O-4 | 0 (K_eff +1) |

- **Budget.** 2026Q4's override is 53 in settings and fully allocated (B4a's raise to 55 for TVR lapsed when TVR was
  withdrawn). These need up to 9 unconditional trials (H-45, seven sub-families unless folded, H-40); H-43 after K-S. Options: (a) the owner raises the 2026Q4 override (the
  research director read the owner's "backtest as much as needed" as covering this in B4a); (b) 2027Q1's free
  reserve (3), which B4a held for H-02/H-14 survivors; (c) the continuous loop budget (ADR 0029) is **not** suitable:
  loop trials are generator-grid trials with `rule_ref=loop`, not hand pre-registrations. **Raising the budget is an
  owner decision under my brief**; recommendation in section 9.4 once the counts are known.
- **K.** Every trial is charged in full; H-40 adds K_eff 21. Floor 80 at any verdict.
- **Sizing.** Nothing here changes risk per trade, the daily limit or the minimum-lot rule.
- **Holdout.** Untouched; counts end 2025-09-30.
