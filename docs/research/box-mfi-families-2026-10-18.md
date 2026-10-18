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
count (2010-01-01..2025-09-30, both sides, after one-at-a-time and the placeable rule) is **≥ 1,500** (gate 1,
`MIN_CANDIDATES`; above P4's 1,000). If no rung reaches 1,500 the family is **withdrawn, 0 trials**. Rungs relax
only in the order fixed here: first pooling the owner's own timeframes, then the owner's own documented looser
threshold, then dropping the condition with the weakest mechanism. Counts select the rung; nothing else does.

### 4.1 H-37 `box_break` (owner's break trade)

Box timeframe 1h, confirmation and entry on 15m (pair P1). Long (short mirrored):

| Code | Condition |
|---|---|
| K1 | 1h break up of box k at 1h bar T (d = 0.2) |
| K2 | 1h MFI long-aligned at T |
| K3 | a 15m break up (15m boxes, d = 0.2) on a 15m bar closing in (T − 1h, T + 4h], with the 15m MFI long-aligned there |
| K4 | armed at A = the later of T and the K3 bar. Pullback: the first 15m bar t within 16 bars after A with `low[t] ≤ P` and `close[t] > P`, where P = min(T_k, EMA20 of 15m) ("whichever comes last": the farther of the broken box and the slowest of the owner's MAs). Entry at close[t] |
| K5 | stop = `B_k − 0.1 × ATR15`; skip if entry ≤ stop |
| K6 | target = next 1h box; reward floor 2 |
| K7 | entry not in the 1h golden zone |
| — | placeable; calendar; one candidate per 1h break (first pullback only) |

Pair P2 (the owner's "4h = bigger levels"): box timeframe 4h, confirmation and entry on 1h, golden zone on 4h,
pullback window 8 1h bars, EMA20 of 1h, ATR1h buffer.

| Rung | Content |
|---|---|
| L0 | P1, K1-K7 |
| L1 | P1 ∪ P2 pooled, K1-K7 |
| L2 | L1 with MFI extremes 70/30 (owner: "~70-80 / ~20-30") |
| L3 | L2 without K7 (golden zone: no mechanism) |
| L4 | L3 without K3 (15m confirmation) |
| L5 | L4 without K2 (MFI): bare box break with pullback entry and box exits |

Mechanism: stops beyond a level are triggered by the break and push price on (Osler 2003); a pullback entry buys
the retest with a stop beyond the box, where a failed break is proven. Money flow coming from the opposite extreme
marks the side that has just been exhausted.

### 4.2 H-38 `box_rip` (owner's rejection trade)

Box timeframe 1h, candles 1h, entry on the 1h close (pair Q1). Short (long mirrored):

| Code | Condition |
|---|---|
| J1 | bar t-1 is bullish, `close[t-2] < B_k`, `high[t-1] ≥ B_k`, `close[t-1] < T_k` (touch from below, no close through) |
| J2 | bar t is bearish and `close[t] < B_k` (the opposite candle closes back outside) |
| J3 | 1h MFI ≥ 80 on any of t-2..t, or the last visible 4h MFI ≥ 80 |
| J4 | stop = max(high[t-1], high[t]) + 0.1 × ATR1h |
| J5 | target = next 1h box below; reward floor 2 |
| J6 | entry not in the 1h golden zone |
| — | placeable; calendar |

Pair Q2: 4h boxes, 4h candles, 4h MFI (and the daily MFI for the "or"), 4h golden zone.

| Rung | Content |
|---|---|
| L0 | Q1, J1-J6 |
| L1 | Q1 ∪ Q2 pooled |
| L2 | L1 with 70/30 |
| L3 | L2 without J6 |
| L4 | L3 without J3 (a plain box fade with wick stop and box target) |

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
| L0 | 1h boxes |
| L1 | 1h ∪ 4h boxes pooled (4h: 12-bar window on 4h bars) |
| L2 | L1, window doubled (48 1h / 24 4h bars) |

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

## 5. Variations (small, ranked by prior) and how they are tested

Variations that **select a subset** of a family's candidates are tested together in **one** pre-registered
filter-discovery trial, H-40, not one trial each. Variations that are **supersets** are not tests: they appear
only as ladder rungs above (pooling, 70/30) or are not run.

| Rank | Flag (point in time at entry) | Applies to | Prior / reason |
|---|---|---|---|
| 1 | `trend_with`: 1h structure trend agrees with the side. Trend = up when the last two confirmed 1h pivot highs (pivot length 5) and the last two pivot lows are both higher (HH+HL), down when both lower, else range | BX, RT; RP as "with-trend RIP" | note 5 "continuation default"; the only filter tied to our one gross edge (R-06 trend) |
| 2 | `ctr_major`: a counter-trend RIP is kept only at a major level: the box overlaps a 4h box, or lies within 0.25 ATR1h of the daily pivot R1/S1 (previous feature day, classic floor pivots) or of the 50-bar 1h extreme; in a range, only within the outer 20% of the 50-bar 1h range | RP | note 5; Osler: longer-horizon levels hold more orders |
| 3 | `vol_veto` (RP): none of the 3 approach bars before t-1 nor t-1 itself is a high-volume candle in the approach direction (tick count > 1.5 × EMA20 of tick count, the box indicator's own rule) / `vol_with` (BX): the break bar is a high-volume candle in the break direction | RP / BX | note 5 volume veto; the replica's candle code |
| 4 | `depth_30`: break depth ≥ 0.3 | BX | owner's upper bound |
| 5 | `rr_3`: reward floor 3 | BX, RP, RT | larger target per cost |
| 6 | `day_bias`: long only above the daily pivot PP, short only below | all | owner's structure-overlay rule; folklore |
| 7 | `premium_discount`: long only below the 1h 50-bar midpoint, short only above | RP, RT | folklore; opposes continuation for BX, so not applied to BX |
| 8 | `engulf`: the signal candle engulfs the previous body (replica candle code 4) | RP, RT | candlestick confirmation; folklore |
| 9 | `anchor_box`: BX pullback anchor = the broken box only (not EMA20) | BX | owner's alternative |

Not run, with reason: reward floor 1.5 (lowers target per cost, the one thing costs punish); `trend table`
(the owner's trend/ranging table has no replicated definition; `trend_with` is our stated rule for the same idea);
order blocks (the owner has them off); the price-action course's channel trading and partial exit at the midpoint
(a new exit family; candidate for the next generation after these verdicts); EMA claims of note 5 (touch rates)
are diagnostics, section 7.

### 5.1 H-40 `box_filter_discovery` (one trial)

- **Candidates:** the union of the rungs that run for H-37, H-38 and H-39 (their own labels, same exits). If a
  family is withdrawn, its candidates are absent; if all three are withdrawn, H-40 is withdrawn.
- **Features:** the nine flags of section 5 plus the `mfi_signals` group (4.4), each a group.
- **Method:** `research.yml -f discover=true`, `DiscoveryConfig` defaults as pre-registered for O-4
  (`survivor_unit: group`, fold-internal stability selection on training folds only). K_eff = groups screened (10).
- **Survivor rule:** a flag survives when it is selected in ≥ 60% of training folds **and** its kept-vs-rejected gross
  difference has Welch t ≥ 2 inside the training folds. At most **two** survivors may go forward, each as its own
  pre-registered trial (the family's rung + the flag), read by section 6 at the K of that date. No survivor: the
  variations are recorded as failed in `hypotheses.md` and not re-tested without new evidence.

## 6. Reading rule (fixed now)

Each trial is read alone, by the evaluator, word for word by `batch-2026-10-18.md` section 2, rules 1-6 (P4 screen:
gross mean R > 0, t ≥ 2 on ≥ 1,000 events; rule-only net mean R > 0 with day-clustered t ≥ 2; DSR ≥ 0.95; ≥ 300
out-of-fold trades with ≥ 30 in 2024-01..2025-09; year stability incl. 2024 and 2025; cost stress at 0.71 $/oz).
Changes:

- **K** for rule 3 is the registry's `n_trials_effective` at the verdict date, **never below 80** (the highest floor
  already pre-registered, 77, plus H-37..H-39). Gate-1 per-trade t about 4.1.
- **Baseline lift (can only downgrade).** For H-37 and H-38 the evaluator also reports the gross mean R of rung L5 /
  L4 (the bare level rule, same exits). If the run rung does not beat it with Welch t ≥ 1, the verdict carries
  "owner's confirmations add nothing" and a PASS becomes a LEAD.
- **Rung and sides** are named in the verdict; long and short reported separately.
- **Placeability** is reported at £1,000 / 0.01 lot with today's ATR scaling (section 9.3 method). It does not
  change the verdict; a PASS that is not placeable at £1,000 is shadow-only until equity allows (owner decision).

Verdicts: **PASS** all rules + lift: shadow founder; H-41 (for H-37) becomes fundable. **LEAD** rules 1, 2, 5 hold:
shadow only. **FAIL** screen fails at ≥ 1,000 events or net ≤ 0: the family is retired as a rule (R-10 box break,
R-11 box rejection, R-12 retest), with the features kept. If H-37, H-38 and H-39 all FAIL, the box/money-flow rule
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
| H-37, H-38, H-39 (one rung each, if ≥ 1,500) | up to 3 |
| H-40 filter discovery | 1 (K_eff 10) |
| H-40 survivors | up to 2, conditional |
| H-41 | 1, conditional on H-37 PASS/LEAD |
| `mfi_signals` group in O-4 | 0 (K_eff +1) |

- **Budget.** 2026Q4's override is 53 in settings and fully allocated (B4a's raise to 55 for TVR lapsed when TVR was
  withdrawn). These need up to 4 unconditional trials. Options: (a) the owner raises the 2026Q4 override (the
  research director read the owner's "backtest as much as needed" as covering this in B4a); (b) 2027Q1's free
  reserve (3), which B4a held for H-02/H-14 survivors; (c) the continuous loop budget (ADR 0029) is **not** suitable:
  loop trials are generator-grid trials with `rule_ref=loop`, not hand pre-registrations. **Raising the budget is an
  owner decision under my brief**; recommendation in section 9.4 once the counts are known.
- **K.** Every trial is charged in full; H-40 adds K_eff 10. Floor 80 at any verdict.
- **Sizing.** Nothing here changes risk per trade, the daily limit or the minimum-lot rule.
- **Holdout.** Untouched; counts end 2025-09-30.
