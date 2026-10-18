# Chart-indicator replica: match report and pre-registration (2026-10-18)

Strategy researcher, 2026-10-18. Owner request the same day: "backtest the TradingView indicators and reverse
engineer them to reproduce the code". Three invite-only indicators on the owner's XAUUSD chart are rebuilt clean-room
from their outputs. Their source is protected and was never seen. This file has two parts. Part A is the match
report. Part B pre-registers the signal trials, before any outcome of them is computed.

Names used here are generic: **A** is the structure overlay (40 plots), **B** the support/resistance boxes (19
plots), **C** the money-flow oscillator (7 plots). No vendor text and no vendor output is in the repo. The pulled
values and settings stay in `~/tmp/tv/`, local only. The code is `goldbot/features/smc_replica.py` (opt-in family
`smc_replica`) and the tests are `tests/test_smc_replica.py`, which use synthetic bars only.

**Outcome-blind attestation.** No return, R, hit rate or label of any configuration in Part B has been computed.
The match work in Part A compares indicator values with indicator values on the chart's own bars, and reads no
return. Some of the pulled bars (1h from 2025-01, 15m from 2026-04) fall inside the holdout (2025-10-01..2026-09-30).
They were used only to match plot values. No outcome on them was computed, and the trials below exclude them.

## A. Match report

### A1. Data and method

- **Data.** Every plot value of the three studies for every closed bar of OANDA:XAUUSD: 11,080 15m bars from
  2026-04-30, 10,614 1h bars from 2025-01-01, 10,509 4h bars from 2020-01-01, and 14,810 daily bars from 1921. The
  daily series is computed from its first bar in 1833, because a running threshold uses the bar index. Comparison
  starts in 1921.
- **Settings.** The owner's input values were pulled with the plots and used to pin each parameter: swing length 50,
  equal-level confirmation 3 with threshold 0.1, FVG auto threshold on the chart timeframe, "SMA/EMA" MA scheme on
  A, EMA scheme on B, box width 1%, 15 boxes, lookback 300, money-flow length 12. Order blocks are switched off in
  the owner's A settings, and so are A's MA-cross shapes and EMA5.
- **Fit.** Each plot was matched bar by bar on the chart's own bars. Lines were compared by maximum absolute and
  relative error, events by precision and recall at ±0 bars, and states (packed colour indices) by exact equality.
- **Reproduce.** Run `python scripts/replica_parity.py [--from D] [--to D] [--tf 60]` (reads `~/tmp/tv` locally, prints aggregates and OK / WARN / SKIP per timeframe; `goldbot/research/replica_parity.py`, an optional health check, never an input). Every tunable is the typed block `ReplicaParams` (defaults = the owner's settings).
- **Repaint.** Every pulled value is tagged `history_recomputed`, so no bar in the pull was live. A repaint check
  (live versus recomputed) needs a forward capture. That is data request D-1 in A4.

### A2. Per-plot match (all four timeframes)

"Exact" means every bar agrees. For lines that is within 1e-6 relative error (the observed maximum is about 1e-14).
For events it means the same bar and the same side, with precision = recall = 1.

| Plot | Replica (generic name, rule) | 15m | 1h | 4h | 1D |
|---|---|---|---|---|---|
| A 0-3 candle OHLC | the bar itself | exact | exact | exact | exact |
| A MA10, MA20, MA200 | SMA | exact | exact | exact | exact |
| A MA50 | seeded EMA (SMA seed) | exact | exact | exact | exact |
| B MA5/10/20/50/200 | seeded EMA | exact | exact | exact | exact |
| A range lines (2) | 50-bar hh − 0.5 / 0.618 × range | exact | exact | exact | exact |
| A internal BOS/CHoCH, bull and bear (4) | `structure_breaks`, leg pivots of 5 + candle filter | exact | exact | exact | exact |
| A swing BOS/CHoCH, bull and bear (4) | leg pivots of 50 | exact | exact | exact | exact |
| A equal highs / lows | pivots(3,3) within 0.1 × ATR(200) | exact | exact | exact | exact |
| A FVG bull / bear | 3-bar gap, body % > 2 × running mean | exact | exact | exact | exact |
| A bar colour (4 states) | `candle_class`: engulf, high/low volume vs EMA20 | exact | exact | exact | exact |
| B bar colour (5 states) | the same plus MA traverse | exact | exact | exact | exact |
| B EMA10×EMA20 cross up / down | crossover / crossunder | exact | exact | exact | exact |
| C money-flow line | MFI(12), first bar adds to neither side | exact | exact | exact | exact |
| C oversold / overbought / either alerts | cross under 20 / over 80 | exact | exact | exact | exact |
| C cross-marker line | MFI value on alert bars | exact | exact | exact | exact |
| A order-block breakouts (4), MA-cross shapes (2); B label shapes (2) | off in the owner's settings; constant 0 in every pull | trivially equal; **not identifiable** | | | |
| B resistance break (triangle up) | `sr_channel_breaks` up | 1,801/1,801 exact | P 0.9988 R 1.0000 (2 extra of 1,695) | P 1.0000 R 0.9976 (4 missed of 1,671) | none on 1D (B draws no boxes on daily) |
| B support break (triangle down) | `sr_channel_breaks` dn | P 1.0000 R 0.9994 (1 missed of 1,801) | P 0.9981 R 0.9994 (3 extra, 1 missed of 1,584) | P 0.9993 R 0.9987 (1 extra, 2 missed of 1,519) | none |
| Colour indices of lines and shapes | constant in every pull | trivially equal | | | |

**Result.** Every observable plot is exact on every timeframe, with one exception: B's two break plots on 15m, 1h
and 4h, where 14 of about 9,800 events differ (99.86%).

### A3. What was learned, and what is unresolved

The fitted rules are recorded in the module docstring. These are the non-obvious ones:

1. **Leg pivots, not symmetric pivots.** Structure uses a pivot that is confirmed when the bar `length` back
   out-reaches every bar after it. The first leg seen only sets the state; it records no pivot. Without that rule one
   early 15m event was wrong.
2. **Internal-structure candle filter is on and asymmetric.** A bullish internal break needs upper wick >
   min(close, open − low); a bearish one needs upper wick < min(close, open − low). Without the filter, internal
   events matched only 33-97%. With it they are exact. The rule is reproduced as observed. Ties are compared at 1e-9:
   one 1993 daily bar ties to 1e-14.
3. **Equal highs/lows use classic pivots(3,3)**, with the left side allowed to tie, compared with the previous pivot
   within 0.1 × seeded ATR(200).
4. **FVG auto threshold** is 2 × the running mean of |middle-bar body %| from the first bar of the series. Values
   therefore depend on where the series starts. They never change when bars are appended.
5. **Money flow** gives the first bar of the series to neither side. Pine's built-in would give it to both.
6. **B's channels** come from pivots(5,5) inside 300 bars, at 1% of the 300-bar range, with up to 15 channels and
   ties going to the newest pivot. They are recomputed only on pivot bars. The fitted channel sets are **identical to
   B's final drawn boxes on 15m, 1h and 4h** (15 of 15 each, checked on the box drawings in the pull). The support-break
   test is empirical: a close reaching down through the top of the highest channel, or through the bottom of any
   channel except the lowest. The resistance test is the plain one.
7. **Unresolved:** 14 break events in five short episodes: 15m 2026-06-17 (1 event); 1h 2025-12-11 (3),
   2025-12-19 (2) and 2026-10-09 (1); 4h 2024-01-30..02-07 (7). In each episode the vendor's channel grouping differs for a few days. Alternatives tested and
   rejected: pivot-array order, tie-breaking, pruning at ≥, touch-window ±1 bar, recompute every bar, width from closes,
   and an "inside a channel" gate. The likely cause is internal bookkeeping in B (the box draw order differs between
   timeframes: strength order on 15m, price order on 1h and 4h). It cannot be pinned from plot values.

### A4. Data requests (for the TV-pull lane; the owner's chart is not to be changed)

- **D-1 (repaint).** A forward capture of live bars for 5 or more trading days on 15m and 1h. Each bar's plot values
  are recorded at its close and compared with the later `history_recomputed` values. This tells us whether any plot
  repaints.
- **D-2 (B episodes).** B's box drawings (bar-open times, UTC) from the last pivot bar before each episode to its last
  mismatch: 15m 2026-06-17 19:00-19:15; 1h 2025-12-11 01:00-10:00, 2025-12-18 21:00-2025-12-19 05:00 and
  2026-10-08 23:00-2026-10-09 00:00; 4h 2024-01-26 06:00-2024-02-07 22:00. Through bar replay if the pull supports
  it. This would settle item 7.
- **D-3 (order blocks).** Optional. A's order-block alerts are off in the owner's settings, so they cannot be fitted.
  A pull of the same study with order blocks on, from a separate layout (never the owner's chart), would let the OB
  rules be replicated. This is not needed for Part B.

## B. Pre-registration (written 2026-10-18, before any outcome)

### B1. Evidence and prior

R-09 in `hypotheses.md` retired SMC rules (BOS/CHoCH, FVG, order blocks and the like) as standalone rules. The
retirement came from the literature: no peer-reviewed support net of costs. No trial was ever run. The owner's
directive is to test the specific indicators he trades from, and these are now replicated exactly. That is a new
object to test, not new evidence of an edge. **The prior is low.** The mechanism behind a structure break is weak:
order flow continues after a level breaks, or stops clustered beyond an extreme get exhausted. Osler 2003 and 2005,
J. Finance and J. Int. Money Finance, FX, gross, are evidence for stop clusters at round numbers and extremes, not for
these rules. The closest families we have tested failed on gross edge: R-02 session-open breakout (gross +0.042 R,
t 1.31), R-04 range breakout (+0.015 R, t 0.38) and R-03 trend pullback.

So the trials below are few and use pre-set exits that fit the account. A failure confirms R-09 for this exact
configuration and **closes the topic**: no SMC variant is tested afterwards without new evidence.

### B2. Trials (2 trials, plus 1 conditional)

All rows use 15m decision bars, so a 1.0 × ATR15 stop fits 0.01 lot at £1,000. ATR15 is about $8 (2024-26).
Sample: Dukascopy 1m → 15m, 2010-01-01..2025-09-30, with the holdout excluded (`to_year=2025`; `score_holdout`
never set). Costs: bar spread + 0.37 $/oz round trip, swap per rollover held. Both sides are traded. The replica runs
on our bars with `tick_count` as volume. Volume enters only `candle_class` and money flow, which no trial below uses.

| Row | Configuration (exact) | Mechanism claimed | Expected gross vs cost | Events (estimate) | Trial cost |
|---|---|---|---|---|---|
| **TVR-1** | Long at the close of bar t when `choch_bull_internal[t] = 1`, `trend_swing[t-1] = +1` and `fvg_bull` = 1 on any of bars t-2..t. Short mirrored (`choch_bear_internal`, swing trend −1, `fvg_bear`). Stop 1.0 ATR(14, 15m), target 2.0 ATR, max 32 bars (8 h). One position at a time per side; a new signal while in a trade is skipped | The pullback inside a swing trend ends when internal structure flips back in the trend direction, with displacement (a gap) | Cost about 0.2-0.4 R at a 1-ATR stop, so the rule needs gross ≥ +0.3 R to net > 0. Prior: gross about 0 (R-02/R-03/R-04) | about 1,500-2,500 (internal CHoCH runs about 2.2% of 15m bars on the pulled sample; the trend and FVG filters cut it about 4×; *estimate*) | 1 |
| **TVR-2** | Long at the close of bar t when `bos_bull_swing[t] = 1` (a swing-scale break in the direction of the existing swing trend). Short when `bos_bear_swing[t] = 1`. Stop 1.0 ATR(14, 15m), target 2.0 ATR, max 32 bars | Continuation after a 50-bar swing high or low breaks (stop orders beyond the extreme) | As TVR-1. Breakout prior is about 0 (R-02/R-04) | about 1,200-2,000 (swing breaks about 0.6% of 15m bars, about 60% BOS; *estimate*) | 1 |
| TVR-3 (conditional) | TVR-1 on 1h decision bars, stop 1.0 ATR(1h), target 2.0 ATR, max 24 bars. **Runs only if TVR-1 reads PASS or LEAD.** It needs M2 sizing (£1,650+) to place, which is flagged | The same mechanism on the timeframe the owner reads | Cost is lower in R at a 1h stop | about 600-1,000 (*estimate*); fewer than 1,000 is inconclusive | 1 if triggered |

The estimates above are scaled from the replica's event rates on the pulled bars. They count events only, not
outcomes. Before dispatch the exact counts on 2010-2025 are computed (count only) and recorded here. A row whose count
is below 1,000 is withdrawn before it runs and costs nothing.

**Not trialled, by decision:**
- **C's money-flow crosses as a rule.** Fading them is short-horizon reversion (R-01, no gross edge, SoA A4).
  Following them has no mechanism.
- **B's channel breaks as a rule.** They are a range breakout (R-04) with a different level set.
- **Every column instead goes to discovery:** proposal **D-TVR**. It amends the H-02 feature-discovery trial before
  that trial runs, adding the opt-in `smc_replica` columns as one group (`family` grouping). That costs no new trial
  and raises K_eff by about 1. It needs the research director's and the quant reviewer's sign-off, because H-02's
  inputs are pinned in `preregistration-2027Q1.md`. This file does not edit them.

### B3. Reading rule (fixed now)

Each row is read alone by the evaluator, word for word by `batch-2026-10-18.md` §2 (rules 1-6: P4 screen, net
walk-forward with day-clustered t ≥ 2, DSR ≥ 0.95, ≥ 300 OOF trades with ≥ 30 in 2024-01..2025-09, year stability
including 2024 and 2025, cost stress at 0.71 $/oz). There is one change. **K for rule 3 is the registry's
`n_trials_effective` at the verdict date, never below 55** (the batch's 53 plus these 2).

- **PASS:** all six hold. The configuration becomes a shadow founder. Its neighbours (at most 3: target 1.5/3.0 ATR,
  max 16/64 bars) are pre-registered before any of their data is read.
- **LEAD:** rules 1, 2 and 5 hold, but 3, 4 or 6 fails. Shadow only, no orders. TVR-3 is unlocked.
- **FAIL:** the screen fails at or above 1,000 events, or rule-only net mean R ≤ 0. R-09 is reconfirmed for this
  configuration with the trial number. **If both TVR-1 and TVR-2 fail, the SMC topic closes.** No further BOS/CHoCH/FVG
  rule trial is written without new evidence, whatever variant is proposed.
- **INCONCLUSIVE:** fewer than 1,000 events. This should not happen, because the count is checked before dispatch.

### B4. Budget, blockers and owner decisions

- **Budget (owner decision, flagged).** Q4 2026's hand-dispatch budget is 53 (`trial_budget_overrides`), and all 53
  are allocated: 20 used plus the 33-trial batch. Q1 2027's open budget is 12, allocated in
  `preregistration-2027Q1.md` as 9 conditional rows plus 3 free reserve. These 2 trials need one of two options:
  (a) the owner raises 2026Q4 from 53 to 55; or (b) the research director funds them from Q1's free reserve, 2 of 3.
  **Recommendation: (b).** The prior is low and the owner's "backtest now" directive is already served by the
  33-trial batch. The continuous loop (ADR 0029) generates its own configurations, so it is not a route for a
  hand-written rule.
- **Blocker (implementation).** No specialist family produces these candidates yet. An implementer lane must add one
  before dispatch: `smc_structure`, `screening = True`, 15m, with presets `tvr1` and `tvr2` exactly as in B2. Its
  candidates come from `smc_replica.structure_breaks` and `fair_value_gaps` computed on the decision bars, with a
  test that the candidate bars equal B2's definitions. That needs quant and code review. Dispatch waits for it.
- **Engine path (shadow and live; quant review 2026-10-18).** Several replica pieces remember the whole history: the
  running FVG threshold, the seeded averages, the structure trend and its levels, and the channel book. A fixed
  800-bar tail therefore does not reproduce them. `smc_replica.replica_run` returns a `ReplicaState` (JSON). Given
  that state, every column on a tail of at least `context_bars()` = 312 bars is bit-identical to the full-history
  value. This is tested on every column at the engine's N = 800, on synthetic and on the pulled chart bars (0 of 120
  sampled bars differ). The engine must bootstrap the state once from research history, carry it bar to bar and
  persist it. That integration is an implementer task, and no shadow or live use comes before it.
- **Dispatch (after both are cleared; not run by this lane):**

```bash
# TVR-1 and TVR-2: 2 trials, 15m, smc_structure (needs the specialist above and the budget decision)
gh workflow run research.yml --ref main -f specialist=smc_structure -f from_year=2010 -f to_year=2025 -f variants='["tvr1","tvr2"]' -f lot_fit=true -f rule_ref='prereg-tv-replica-2026-10-18.md#b3-reading-rule-fixed-now' -f rationale='TVR owner-indicator replica: internal CHoCH + FVG with swing trend (TVR-1), swing BOS continuation (TVR-2); 15m, 1.0/2.0 ATR15, 32 bars'
```

### B4a. Research-director ruling (2026-10-18, before any count or outcome)

Signed by the research director under the PD + PO + research-director rule for routine research decisions. These
items supersede B4's recommendation where they differ. Nothing in B2's configurations or B3's thresholds changes.

1. **Funding: option (a), under the owner's standing directive of 2026-10-18.** `research.trial_budget_overrides`
   goes from `{"2026Q4": 53}` to `{"2026Q4": 55}` (implementer lane, `chore:` with a handoff fragment). The owner's
   directive ("backtest as much as needed; no quarterly waiting") already covers raising the Q4 override, so no new
   owner decision is needed. Q1's free reserve (3) is **not** used: it is held for H-02/H-14 survivor follow-ups,
   and spending next quarter's slots now would be spending reserved budget early. K is counted in full either way.
   TVR-3, if TVR-1 unlocks it, is funded by a written note raising the override to 56 at that time; it is not
   pre-funded.
2. **D-TVR: adopted into both O-4 (H-14, 15m) and H-02 (4h), one group `smc_replica` each, 0 registry trials,
   K_eff +1 per discovery run (+2 total).** Allowed now: neither group map nor `DiscoveryConfig` is pinned and
   `preregistration-2027Q1.md` is not frozen (`freeze_commit: null`). Conditions: (i) the group is written into
   both maps **before TVR-1/TVR-2 are dispatched**, so no TVR outcome can influence inclusion; (ii) the group holds
   only `smc_replica` columns, never columns already in `structure` (`bos_*`, `sweep_*`), so a survival measures
   what the replica adds; (iii) its size is at most `max_selected` (39) or it is split into two pre-named groups,
   each counted in K_eff; (iv) a TVR FAIL does not remove the group (features are not rules; R-09 stands for
   rules only). The amendment lines go into `preregistration-2027Q1.md` under O-4 and H-02 by the strategy
   researcher, with quant-reviewer sign-off, by 2026-10-25.
3. **Fixes before dispatch (each is a dispatch precondition, owned by the `smc_structure` implementer lane unless
   stated):**
   - **Causality.** A prefix-invariance test: every replica column at bar t is identical when computed on bars
     ..t and on bars ..t+n (n ≥ 200), on Dukascopy 15m and 1h samples. Leg pivots confirm `length` bars late; the
     break event must fire only on the bar where it is knowable. This replaces D-1 for the backtest; D-1 is still
     needed before any shadow or live use.
   - **Series start pinned.** The FVG running-mean threshold and the seeded ATR/EMAs start at the first bar of the
     2010-01-01 Dukascopy series for each timeframe. Recorded in the report.
   - **One position total, not per side.** At 0.01 lot on £1,000 the account holds one trade; an opposite signal
     while in a trade is skipped. (This tightens B2's "per side" wording before any data; the event count rule is
     unchanged.)
   - **Bars are ours, not OANDA's.** The replica is exact on OANDA bars; on Dukascopy bars event timing can
     differ. The trial tests the rule on our data; a PASS is not evidence that the owner's chart signals carry it.
     D-1 must also compare replica events on our live 15m bars against the chart before shadow.
   - **Count before dispatch** (B2) is pasted here by the strategy researcher, with TVR-1/TVR-2 overlap (share of
     TVR-2 events within 4 bars of a TVR-1 event). Overlap above 50% is reported, not acted on.
   - **Intrabar order.** If stop and target both fall inside one bar, the stop is taken first (the engine's
     pessimistic convention, as in `batch-2026-10-18.md`).
4. **Dispatch** follows `batch-2026-10-18.md` §0.2 (one run at a time) once the override, the specialist and the
   fixes above are merged. Target: dispatched by 2026-10-22; verdict by the evaluator before any neighbour or
   TVR-3 is written.

### B4b. Counts before dispatch (2026-10-18, counts only, no outcome)

Counted by `python scripts/smc_counts.py --bars raw` on the `smc_structure` code (presets `tvr1`, `tvr2`, through
`candidates_in_context`, the call research makes). Dukascopy 1m (release data-v1) → 15m mid bars 2010-01-01 ..
2025-09-30, 369,786 bars. The 2025 file is read whole, so its holdout rows (from `research.holdout_from`,
2025-10-01) are read from disk (`scripts/smc_counts.py`, `read_bar_files`) and then cut before anything is resampled
or computed (`count`, `cut_before_holdout`): no holdout bar reaches a count. Series start pinned to the first 2010
bar (B4a). No label, return or outcome was computed.

| Row | Events | Long | Short |
|---|---:|---:|---:|
| TVR-1 | 441 | 246 | 195 |
| TVR-2 | 1,196 | 630 | 566 |

TVR-2 events within 4 bars of a TVR-1 event: 3.5% (below the 50% reporting line).

| Year | 2010 | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TVR-1 | 19 | 54 | 16 | 41 | 22 | 32 | 27 | 10 | 8 | 14 | 47 | 25 | 29 | 24 | 34 | 39 |
| TVR-2 | 84 | 81 | 72 | 74 | 71 | 85 | 65 | 75 | 74 | 82 | 78 | 77 | 70 | 83 | 72 | 53 |

Funnel for TVR-1 (counts only): 8,385 internal CHoCHs (2.27% of bars, as the pulled sample's 2.2%), 3,616 with the
swing trend at t-1, 441 with a gap on t-2..t. B2 estimated a 4× cut from the two filters; the gap filter alone cuts
about 8×. Under B2 ("a row whose count is below 1,000 is withdrawn before it runs and costs nothing"), TVR-1 is
withdrawn as written, and TVR-3, which depends on it, is moot. TVR-2 (1,196) clears B2's 1,000 floor but not the
walk-forward gate's event minimum (1,500 candidates, `research/gates.py` MIN_CANDIDATES), so it would spend a trial on
a known gate failure.

**Decision (program director + research director, under the owner's delegation, 2026-10-18).** TVR-2 is **withdrawn
before dispatch: below the gate-1 event minimum**. TVR-1 is withdrawn under B2's floor. **0 trials are used.** No
reworded TVR-1 or TVR-2 is written from these counts (that would be a configuration chosen after seeing them). The
SMC topic continues only through the replica feature group (D-TVR) inside O-4 and H-02, not as standalone rules.

### B5. Ledger

| Item | Count |
|---|---|
| Registry trials before this file (Q4 batch allocated) | 53 |
| New pre-registered trials here | 2 written (TVR-1, TVR-2) + 1 conditional (TVR-3); **0 dispatched**: TVR-1 withdrawn at 0 cost (441 < 1,000), TVR-2 withdrawn before dispatch (1,196 < the gate-1 minimum 1,500), TVR-3 moot (B4b) |
| K for DSR at verdict | no verdict is read. The floor of 55 (B3) stands for any later SMC rule trial; no budget override is needed, because 0 trials were used |
| Feature-discovery K_eff (D-TVR adopted, B4a) | +1 group inside O-4, +1 inside H-02 |
| Holdout touched | no (match work read no returns; trials end 2025-09-30) |
