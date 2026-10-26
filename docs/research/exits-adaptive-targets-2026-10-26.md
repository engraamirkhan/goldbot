# Adaptive targets and exits by family, timeframe, lot size and running P&L (trade management researcher, 2026-10-26)

Owner directives (2026-10-26, verbatim): "a strategy is one strategy that can be applied to different time frames, so
break and close strategy to next box target for different time frames is still one strategy but train and tested on
multiple timeframes", then "yes the target should also be different based on the timeframe strategy lot size profit
loss etc."

Scope: a target/exit policy that adapts by family, timeframe, position size and running P&L; the evidence for each part;
pre-registered arms for the owner's next-box box break on 15m/30m/1h/4h as one family; a coding plan. Data: Dukascopy
1m mid, **2010-01-01 .. 2017-12-31 only** (the script opens only those eight files and asserts it). No trial, no
registration, no config change, no holdout, nothing from 2018 on. Script:
`scripts/research/exits_adaptive_discovery.py` (24 s, outputs in `~/tmp/exits_adaptive/`). Pre-registration:
`docs/research/prereg-exits-adaptive-2026-10-26.md` (H-80).

## 0. Answer in five lines

1. **"Next box" is not far on 4h and near on 15m once it is measured in the timeframe's own risk unit.** The next box
   beyond a box break sits at a median **0.39 R** (R = 1.5 x ATR14 of the decision timeframe) on 15m, 30m, 1h and 4h
   alike, and is closer than 2 R on **97-98%** of breaks. In dollars it scales with ATR ($4.7 on 15m, $21 on 4h at
   today's ATR); in R it is the same everywhere. The owner's ">= 2:1" rule therefore overrides the next box almost
   always: target = max(next box, 2 R) **is** the fixed 2 R target (paired difference -0.001 R pooled).
2. **The first box at or beyond 2 R is a worse target than 2 R in every timeframe** (E8: -0.017, -0.017, -0.012,
   -0.016 R on 15m/30m/1h/4h; pooled -0.016, t -1.73). Boxes are not magnets, as R-13 (EQH/EQL) and slice G (key
   levels: no magnet) already found.
3. **The course's structural geometry (stop at the box's far side, target the next box) loses before costs**: gross
   -0.157 R (t -7.9, 0 of 8 years positive) for a structural stop with a 2 R target. **That is the geometry the demo
   exploration arms MB15/MB30/MB1H/W2/I2A/I2B/S4 trade** (`research/arm_rules.py:290` passes the structural `risk_usd`
   as R), not the roster's 1.5 ATR stop. Flagged in section 6.
4. **Only one exit changes anything: a trail with no fixed target** (break-even at +1 R, 2.5 ATR chandelier armed at
   +1 R). Paired against the fixed 2 R on the same entries: **+0.039 R pooled (t 2.4)**, 8 of 8 years gross positive,
   but carried by 30m (+0.085, t 2.8); 15m +0.025, 1h +0.013, 4h +0.003. Partials halve it (+0.019); break-even adds
   nothing over the trail alone (-0.001).
5. **No exit makes the box rule pay.** Net of the measured cost with the $0.70 stop overshoot, at today's ATR: fixed
   2 R -0.045 R, runner -0.021 R; under the research cost ($1.07/oz) -0.39 / -0.35 R. R-15 stands. The adaptive policy
   below is worth building as **engine capability for every family**; on the box family it is learning, not income.

## 1. Target sources and how each scales with timeframe

### 1.1 The rule: one exit plan per (family, timeframe), resolved at entry, frozen for the trade
Every distance is a multiple of the **decision timeframe's own ATR(14) at the signal bar**, frozen at entry (never
refreshed). Timeframe scaling is then automatic and needs no per-timeframe constants except the hold. Point in time:
every level used is known at the signal bar's close (boxes in force at t, prior-period levels after their period
closed, as `box_course.next_box` and slice G build them).

| Source | Definition (resolved at entry) | Use |
|---|---|---|
| R floor (owner's 2:1) | target >= entry + side x 2 R; R = the stop distance actually placed | always binds; never relaxed |
| Volatility | k x ATR_tf (k = 3.0 with a 1.5 ATR stop = 2 R) | the default target (FIXED mode) |
| Structural | the **first** level in the trade direction at or beyond the 2 R floor (next box; next key level: PDH/PDL, PWH/PWL, Asia/London/NY session extremes, $10/$50/$100 round numbers). If none: the 2 R floor | STRUCT mode, only for a family whose levels beat a random-level control in the target role; **none does today** |
| Runner | no fixed target; break-even at +1 R; trail 2.5 ATR_tf behind the best exit-side price, armed at +1 R; optional cap (10 ATR in the 2027Q1 pins) | RUNNER mode, the only mode with a measured gain (section 4) |
| Placeable (engine) | target distance >= 2.5 x (bar spread + 0.37 $/oz) and stop >= $0.20 (`box_course.placeable`) | a refusal, never a resize |

"Nearest level" is never a target on its own: on the box rule it is inside 2 R 97-98% of the time, so a nearest-level
target would either break the owner's 2:1 rule or skip the trade (the course's K6: keeps 39% of breaks under the
structural stop and loses -0.248 R gross, S1 in section 4).

### 1.2 What the rule means per timeframe, at today's ATR (arms-2026-10-25.md ATR table; GBPUSD 1.33; 0.01 lot = 1 oz)

| TF | ATR $ | R = 1.5 ATR, $ (£ at 0.01) | 2 R target $ | median next box $ (0.39 R) | median first box >= 2 R $ (2.38 R) | trail 2.5 ATR $ | hold (bars = hours) | cost in R: research $1.07 / measured $0.23 / + $0.70 overshoot |
|---|---|---|---|---|---|---|---|---|
| 15m | 8.01 | 12.0 (£9.04) | 24.0 | 4.7 | 28.6 | 20.0 | 96 = 24 h | 0.089 / 0.019 / 0.077 |
| 30m | 11.6 | 17.4 (£13.09) | 34.8 | 6.8 | 41.4 | 29.0 | 72 = 36 h | 0.061 / 0.013 / 0.053 |
| 1h | 17.73 | 26.6 (£20.00) | 53.2 | 10.4 | 63.3 | 44.3 | 48 = 48 h | 0.040 / 0.009 / 0.035 |
| 4h | 36.61 | 54.9 (£41.30) | 109.8 | 21.4 | 130.7 | 91.5 | 24 = 96 h | 0.019 / 0.004 / 0.017 |

So the owner is right that the 4h target is far and the 15m near **in dollars** (x4.6), and the system already does
that by working in ATR_tf; in R, which is what pays, the geometry is identical. Cost in R falls with the timeframe, so
the same gross edge nets more on 4h, but the 4h rule fires about once a week (207 trades in 8 years).

## 2. Lot size: when partials exist, and what to do at the minimum lot

- **Partials need two minimum lots.** `runner._scale_out` (`goldbot/engine/runner.py:1692`) skips the partial when
  either part would be below `volume_min`, logging `scale_out_skipped`; at 0.01 lot every partial is skipped and the
  trail still runs. A 50% partial needs lots >= 0.02 with both parts on the volume step.
- **Equity at which 0.02 lot is allowed** (2 x the 0.01 loss at the stop, per-trade cap 1% hard / 0.5% normal, ADR
  0004/0042): 15m £1,808 / £3,616; 30m £2,618 / £5,236; 1h £4,000 / £8,000; 4h £8,260 / £16,520.
- **Small real account (live from £20).** ADR 0042 part 2: below £520 nothing is eligible (margin and notional rails),
  15m from £820 at 0.01 lot, 1h from £1,650, 4h from £3,800. So for the whole small-account path the position is
  **indivisible**, and the exit has to be all-or-nothing. Partials are an option only above the 0.02-lot equities above.
- **At the minimum lot, the trail replaces the partial.** Two forms only: FIXED (stop/2 R/time) or RUNNER without a
  partial (the V1u form already pinned in `prereg-exits-2027Q1.md` section 3). The evidence does not favour partials even
  where they are possible: on the same entries the 50% partial at the target with a trailed remainder (E5) earns half
  of the runner's gain (+0.019 vs +0.039 R pooled), and slice G's 50%-at-1R partial added nothing at 4h and +0.009 R at
  1h ("no asymmetric-exit rescue", key-levels.md section 4.1).
- **Demo must trade the live account's form.** icm-demo at £10k sizes a 15m trade at about 0.05 lot (divisible), the
  live path at 0.01 (indivisible). A demo that scales out learns an exit the small live account cannot run. Rule: every
  learning arm and every demo agent uses the indivisible form unless its pre-registration names a divisible account.
- **Size never changes after entry** except by a partial close; the policy never adds and never widens a stop.

## 3. Running P&L: break-even, trail, time, Friday, the day's P&L

All of these are automatic exits or tightenings: never gated, never confirmed, never loosening a stop (the
`ExitPolicy` contract, `goldbot/labels/exit_policy.py`: "A policy only ever tightens the stop or reduces size").

| Rule | Mechanics | Evidence (section 4) | Status |
|---|---|---|---|
| Break-even | at +1 R (best exit-side price), stop to entry + side x $0.23 (the measured round trip, so a BE exit is net flat) | E4 vs E3: -0.001 R. Moves 15-20% of trades from a possible -1 R to about 0; a risk shape, not an edge (folklore grade F, OD 2.8) | in RUNNER mode, owner's preference; never a trial on its own |
| Trail after 1 R | chandelier 2.5 ATR_tf behind the best exit-side price, armed at +1 R, effective from the next bar close (`policy_step`, live `modify` at the bar close) | the only positive paired exit (+0.039 R pooled), heterogeneous by TF | RUNNER mode, arms BXR (section 5) |
| Time exit | `HOLD_BARS` 96/72/48/24 (24/36/48/96 h) as the roster | 0.5-4% of fixed-target exits; 0.3-11% of runner exits (4h 10.6%) | unchanged |
| Friday | existing `weekend_cut` 21:30 server (Europe/Athens): losers closed, winners' stop to lock half the open profit (`runner._weekend_rule`) | 1-8% of exits (4h 5.8-7.7%), applied in every simulated exit here | unchanged; **parity gap**, see below |
| Day's P&L overlay | account level, two triggers: (a) the day's realised + open P&L <= -75% of the daily cap (min(2% x day start, £50), ADR 0004); (b) the day's P&L >= the north-star day (£50, ADR 0004). Action on every open winner: stop to lock half the open profit (the Friday mechanic). Losers keep their stop (the RiskGate already blocks new entries near the cap) | not testable per family (needs the book); expected cost a small loss of right tail, value is variance control | proposal; trading-safety-reviewer and CFO (risk appetite) rule before build |

Two cautions a quick reading misses:
- **Trails are stop orders; targets are limit orders.** 97% of runner exits are stops (initial, BE or trail) against
  66% for the fixed target. The probe's $0.70 stop overshoot (14 of 142 probes, possibly a polling artefact,
  `cost-model-1m-2026-10-25.md`) is charged on 31 points more of the trades. At 2010-2017 stop sizes this turns the
  runner's +0.04 R gross lead into a net deficit (-0.295 vs -0.250 R); at today's ATR it does not (-0.021 vs
  -0.045 R). The S0R resting-stop arm's overshoot reading therefore decides whether a runner is worth it at 15m.
- **The day overlay contaminates every arm read.** It ties one arm's exit to other arms' P&L. Arms are read on their
  own pre-registered exit, with the overlay logged as an override and the counterfactual exit recorded (the same
  1m path), so its cost in R is measured separately.

Parity gap (quant-reviewer, trading-safety-reviewer): labels (`triple_barrier*`) and the shadow book do not model the
Friday rule (no weekend handling in `goldbot/labels/` or `goldbot/engine/shadow.py` beyond swap), while the engine
applies it to every position. On 4h about 7% of trades exit by it. A 4h label and the 4h live trade differ on those.

## 4. Evidence

### 4.1 Already measured (no re-test)

| Source | What it says about exits | Use here |
|---|---|---|
| R-15 / H-50 (#89) | roster box rule 15m, stop 1.5 ATR, 2 R: gross +0.030 R (t 1.61, n 5,684, 2010-2025-09), net -0.297 R, 0 net-positive years | the entry has no edge; no exit was in the trial |
| R-03 / R-04 | trail 1.5 ATR armed at 1.25 ATR; half at 1.0 ATR + trail: failed, no gross edge | trails do not rescue an entry |
| `prereg-exits-2027Q1.md` | V0 fixed / V1 scale-and-run / V1u (indivisible) pinned for H-17; BE and exits-as-rescue on the known-failed list | E3 here is the V1u mechanics without the 10 ATR cap and clock flat |
| H-25 (precision stop) | pre-registered 2026-10-18, **never dispatched** (lapsed in Q4) | no result exists; the tight-stop study (2026-10-18) found tighter stops raise cost in R faster than gross: pooled net -0.11 R (1.5 ATR15) to -0.45 R (structural) |
| Slice G key levels (branch `research/slice-g-key-levels`, `docs/research/time-structure/key-levels.md`) | 1h Asia-hours level break, PT exit (50% at 1 R, BE, 1.5 ATR chandelier): gross +0.119 R, 8 of 8 years above control; but the same file finds **PT adds nothing at 4h and +0.009 R at 1h** over fixed 2 R ("no asymmetric-exit rescue", 4.1). The 8/8 table is the PT run; no fixed-exit yearly table is printed | correction to the brief: "held only with partial + trail" is not shown; the yearly stability belongs to the Asia-hours entry, not the exit. Next-level targets were not run there (its 9) |
| Kaminski & Lo (2014), "When do stop-loss rules stop losses?", *Journal of Financial Markets* 18, 234-254 (cited in hypotheses H-17) *(not re-checked)* | stops/trails add value only when returns are serially correlated | predicts a runner helps only where breaks trend; here only 30m shows it |

### 4.2 New discovery measurement (2010-2017, the roster entry fixed, every exit on the same entries)

Entries: the frozen roster rule (`decide_with_stop`, constants unchanged) on 15m/30m/1h/4h; one position at a time per
timeframe under the roster exit; n = 2,722 / 1,447 / 794 / 207, pooled 5,170. R = 1.5 ATR (E rows) or the structural
stop (S rows). Bars walked stop-first, gap through a stop filled at the open, Friday rule applied. Costs: **A** research
$1.07/oz flat; **M** $0.23 + $0.70 on the stopped share, at 2010-2017 stop sizes (median R $2.32 / $3.41 / $5.00 /
$9.27); **MT** the same at today's ATR. t is day-clustered.

| Exit | Pooled gross R (t) | Net A | Net M | Net MT | Years gross > 0 | P(R >= 2) | P(R >= 3) | Paired vs E0 (t) |
|---|---|---|---|---|---|---|---|---|
| E0 roster: 2 R target | +0.001 (0.03) | -0.389 | -0.250 | -0.045 | 4/8 | 0.317 | 0 | - |
| E1 next box, floor 2 R | -0.000 | -0.390 | -0.252 | -0.046 | 4/8 | 0.316 | 0.001 | -0.001 (-0.7) |
| E2 next box, floor 2 R, cap 4 R | 0.000 | -0.389 | -0.251 | -0.046 | 4/8 | 0.316 | 0.001 | -0.000 (-0.3) |
| E8 first box at >= 2 R | -0.016 (-0.71) | -0.405 | -0.277 | -0.063 | 4/8 | 0.272 | 0.022 | **-0.016 (-1.7)** |
| E3 runner: trail 2.5 ATR after 1 R | +0.041 (1.74) | -0.349 | -0.294 | -0.020 | 7/8 | 0.111 | 0.065 | **+0.040 (2.5)** |
| E4 runner + BE at 1 R | +0.040 (1.75) | -0.350 | -0.295 | -0.021 | 8/8 | 0.100 | 0.060 | **+0.039 (2.4)** |
| E5 50% at the E1 target, rest E4 (needs 0.02 lot) | +0.020 (1.04) | -0.370 | -0.284 | -0.036 | 5/8 | 0.101 | 0.031 | +0.019 (1.6) |
| E6 slice-G PT: 50% at 1 R, BE, 1.5 ATR trail | +0.008 (0.50) | -0.382 | -0.265 | -0.042 | 5/8 | 0.032 | 0.007 | +0.007 (0.6) |
| E7 E1 + BE at 1 R (0.01-lot form of E5) | -0.001 | -0.391 | -0.273 | -0.051 | 4/8 | 0.243 | 0.001 | -0.002 (-0.2) |
| S3 structural stop, 2 R (the demo arms' geometry) | **-0.157 (-7.9)** | -1.870 | -1.355 | -0.377 | 0/8 | 0.283 | 0 | n/a (other R) |
| S2 structural stop, max(next box, 2 R) | -0.156 (-6.1) | -1.869 | -1.414 | -0.387 | 0/8 | 0.243 | 0.027 | n/a |
| S1 structural stop, next box, skip if < 2 R (course K6; n 2,004) | -0.248 (-4.6) | -2.614 | -2.095 | -0.584 | 0/8 | 0.156 | 0.070 | n/a |

Per timeframe, paired runner (E4) minus fixed (E0): 15m +0.025 (t 1.1), **30m +0.085 (t 2.8)**, 1h +0.013 (0.3), 4h
+0.003 (0.1). E8 minus E0: -0.017, -0.017, -0.012, -0.016 (all four negative). Gross E0 by timeframe: 15m +0.037,
30m -0.056, 1h +0.008, 4h -0.103; no timeframe is net positive under any cost case for any exit. Yearly E4 - E0
(pooled): -0.033, +0.005, +0.023, +0.098, +0.046, +0.068, -0.003, +0.108 (2010..2017): positive in 6 of 8.

Geometry (exit-independent): next box exists for 97-98% of breaks, median 0.39 R (IQR 0.17-0.78), >= 2 R for 1.4-2.9%;
first box at >= 2 R exists for 78-84%, median 2.38 R; structural stop median 0.27 R of the 1.5 ATR stop (0.41 ATR);
uncapped MFE before the stop: P(>= 1 R) 0.47-0.52, P(>= 2 R) 0.26-0.34, P(>= 3 R) 0.12-0.25, P(>= 4 R) 0.04-0.19 (4h
lowest). Runner tail: max 16.1 R; the top 5% of trades carry 0.25 R per trade (fixed: 0.10). The best-day capture ratio
(D-4 of `prereg-exits-2027Q1.md`) was not computed here; it is in the confirmation read (H-80, R2).

Power: the SD of the paired E4 - E0 difference is 1.14 R, so a forward read at 300 trades detects only +0.13 R (t 2);
the discovery effect (+0.04) needs about 3,200 paired trades. Forward arms cannot settle the runner question in under
three years; the 2018-2025-09 half (about 5,000 trades at the same rate, detectable effect about 0.032 R) can.

### 4.3 Looks behind these numbers (for K)

| Pass | Added | Cells |
|---|---|---|
| 1 (pre-specified) | E0-E7 x 4 TF + pooled; next-box geometry; exit mix | 40 exit cells (gross, t, 3 nets, 2 year counts, 4 tail statistics each), 48 geometry statistics, 40 exit-mix rows |
| 2 (after reading pass 1) | S1, S2, uncapped MFE (MX), paired differences | +10 exit cells, 35 paired, +20 MFE statistics |
| 3 (after pass 2) | E8, the literal "first box beyond 2:1" target | +5 exit cells, +5 paired, +8 geometry statistics |
| 4 (after pass 3) | S3 (the arms' geometry, after reading `arm_rules.py`); today's-ATR cost fixed for S rows | +5 exit cells |
| post hoc | yearly E4 - E0, paired SDs, tail shares | 1 table, 3 statistics |

12 exit configurations x 4 timeframes, 60 exit cells, 40 paired differences, one data pass per run, no parameter
changed after results (every constant is the roster's or the 2027Q1 pins'). S1-S3, E8 and the yearly table were
chosen after seeing results: their t values are optimistic about their own selection. The choice of E4 (over E3/E5) as
the runner arm was made after seeing them: **K_eff +1 on H-80's row**. No registry trial.

## 5. Proposed arms (pre-registered in `prereg-exits-adaptive-2026-10-26.md`, H-80; nothing frozen is edited)

One family, four timeframes, one pooled read with a timeframe stratum; no best-timeframe pick. Entry for all: the frozen
roster rule (`decide_with_stop`, `smc.DEFAULT_PARAMS`, constants unchanged), the roster's **1.5 ATR_tf stop** (not the
structural stop), the arms' common filters (blackout, 20:45-22:15 UTC, Friday after 19:00 UTC), icm-probe, 0.01 lot.

| arm_id | kind | TF | exit | hold | £ at stop today (cap) |
|---|---|---|---|---|---|
| BXR15 | real orders | 15m | RUNNER (E4): BE at +1 R to entry + $0.23; trail 2.5 ATR15 after +1 R; no target; Friday rule | 96 | 9.04 (intraday £45) |
| BXR30 | real | 30m | as BXR15 on ATR30 | 72 | 13.09 (intraday) |
| BXR1H | real | 1h | as BXR15 on ATR1h | 48 | 20.00 (intraday) |
| BXR4H | real | 4h | as BXR15 on ATR4h | 24 | 41.30 (swing £40: refused and logged when over the cap, as W0/W2) |
| BXT15, BXT30, BXT1H, BXT4H | virtual (no orders), on each BXR fill | same | STRUCT (E8): target the first box at >= 2 R (boxes in force at t), else 2 R; stop 1.5 ATR | same | 0 |
| BXF15, BXF30, BXF1H, BXF4H | virtual, on each BXR fill | same | FIXED (E0): 2 R | same | 0 |

Virtual twins are computed on the BXR fill's own 1m path after entry (entry price = the real fill), so the comparison
is paired and costs no money; only BXR measures real trail mechanics (modify latency, trail-stop slippage). The
existing MB15/MB30/MB1H/W2 rows stay as they are (frozen); they are not comparators because their R is the structural
stop.

Prior: P(the runner is adopted at the confirmation read) about 35%; P(any BX arm net-positive at measured cost) about 3%;
P(STRUCT adopted) about 2%. Reading rules are in the pre-registration (forward kill at the H-61 cumulative-R stop;
adoption only from the 2018-2025-09 confirmation read R2, which needs the research director's funding).

## 6. Risks raised (unasked)

1. **The demo box arms do not mirror the roster's exit geometry.** `arm_rules.box_break_close_rule` sends `risk_usd`
   (the box's far side + 0.1 ATR) as the stop and 2 R of it as the target. On 2010-2017 that geometry is gross -0.157 R
   (t -7.9, 0/8 years); the roster's 1.5 ATR geometry on the same entries is +0.001. Under R-15 nothing is promoted, but
   reading MB*/I2*/W2/S4 fills as "the owner's box strategy on demo" misstates it. Owner of the fix: research director
   (a new arm id if the geometry is to change; the frozen rows are not edited).
2. **Friday rule parity** (section 3): labels and shadow do not model `weekend_cut`; about 7% of 4h trades differ.
3. **Divisible demo vs indivisible live** (section 2).
4. **Trail cost is stop slippage** (section 3); the S0R resting-stop reading decides the 15m runner.

## 7. Owner-only and reviewer flags

- Owner: none required. Sizing, per-trade caps and live stay where ADR 0004/0042/0043 put them; nothing here loosens a
  rail. The day-P&L overlay only tightens; its thresholds are a risk-appetite choice for the CFO with trading-safety.
- quant-reviewer: the exit-policy additions (break-even step, structural-target resolver), Friday-rule label parity.
- trading-safety-reviewer: runner modify path on exploration orders, the day-P&L overlay, virtual twin storage.

## 8. Delegation plan (coding; tests first; one writer per file)

Order: D1 and D2 in parallel, then D3/D4/D5 in parallel on top of them; D6 waits for the trading-safety and CFO ruling;
D7 runs only when the research director funds R2. Each implementer brief follows `docs/process/lane-checklist.md`.

| Brief | Scope (files) | Acceptance check | Mode |
|---|---|---|---|
| D1 implementer | `goldbot/labels/exit_policy.py`, `tests/test_exit_policies.py`: add a break-even step (`be_after_r`, `be_offset_usd`) to `ExitPolicy`/`policy_step`, applied after the trail update, never loosening | failing tests first: BE moves the stop once at +1 R, never back; BE + trail takes the tighter; stop-first order unchanged; existing policy tests pass unchanged | worktree |
| D2 implementer | new `goldbot/labels/targets.py` + `tests/test_targets.py`: `resolve_target(side, entry, risk_usd, atr, levels, floor_r=2.0, cap_r=None, mode)` for FIXED / STRUCT (first level at >= floor, else floor) / RUNNER (None) | tests first: floor binds; nearest level inside the floor is skipped; no level gives the floor; cap; prices x k and ATR x k give the same R (timeframe invariance); levels after t refused (lookahead fixture) | worktree |
| D3 implementer | `goldbot/research/arm_rules.py` (new rule `box_break_close_atr`: roster entry, R = STOP_ATR x ATR_tf, carries the exit mode), `config/exploration_arms.yaml` + `config/accounts.yaml` rows BXR15/30/1H/4H only after the director freezes H-80 | tests first: candidates equal `candidates_with_risk` entries; R = 1.5 ATR, not `risk_usd`; arm hashes load; existing MB/I2/W2/S4 hashes unchanged | worktree |
| D4 implementer | `goldbot/engine/exploration.py` (+ its tests): exploration positions accept an `ExitPolicy` (runner, BE) through the existing `_manage_policies` modify path; size fixed at 0.01, no scale-out | tests first: runner arm position gets trail/BE modifies at bar close; never loosens; exits never gated; kill switch and Friday cut still act | worktree |
| D5 implementer | `goldbot/engine/shadow.py` (+ tests): virtual exit twins (BXF*, BXT*) on each BXR fill from the fill price and the 1m path, stored with the arm id and `twin_exit`; and the Friday rule in the shadow walk | tests first: twin E0/E8 equal `triple_barrier_1m` on the same path; Friday-rule fixture (loser closed, winner locked at half) equals `runner._weekend_rule` | worktree |
| D6 implementer (after ruling) | `goldbot/engine/runner.py`: day-P&L lock-half overlay behind a config flag default off, with override journal and counterfactual exit | tests first: triggers at -75% of the daily cap and at +£50; only tightens; never gated; journal row per override | worktree |
| D7 exits-researcher-staff | `scripts/research/exits_adaptive_discovery.py` extended with `--confirm` (2018-01..2025-09 only, refuses without the director's ruling id), R2 statistics and the D-4 best-day capture ratio | dry run on 2010-2017 reproduces section 4.2 to 3 decimals; refuses 2018+ without the flag; never opens 2025-10+ | worktree |

Reviews: quant-reviewer on D1, D2, D5, D7; trading-safety-reviewer on D4, D5, D6; code-reviewer on all.
