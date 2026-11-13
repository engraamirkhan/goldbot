# Cost-budget audit of the running gold arms (2026-11-13)

Principal quant strategist, 2026-11-13. Plan line 2 of `algo-systems-review-2026-11-13.md` ("no arm runs with cost >
1/3 of its gross edge; audit live arms first"). Read-only: nothing in `config/`, the engine or the RiskGate changes
here. Every stop recommended below is a proposal. It is routed to the research director, and for the H-90 arms it is
an amendment to `prereg-2026-11-12-london-signal-arms.md` (section 4 below).

## 1. Rule and source

- **Speed limit.** Carver, R. (2015), *Systematic Trading*, Harriman House. Spend at most one third of the expected
  pre-cost Sharpe ratio on costs. The source is futures sub-systems, and the 1/3 rule is a practitioner's rule, not
  a peer-reviewed result. Taken via the survey `algo-systems-review-2026-11-13/3-practices.md` rows 88-89 (secondary
  sources: cxoadvisory, the7circles, qoppac.blogspot 2025-03) *(primary chapter not re-checked: unverified)*.
  Annual cost SR and annual gross SR scale by the same trade count, so per trade the rule reads:
  **cost per trade <= gross edge per trade / 3**, and the gross edge must be > 0.
- **Data.** Brain `aux.db` `exploration_trades`, read-only (`file:...aux.db?mode=ro`), pulled 2026-11-13 06:41 UTC.
  The sample is XAUUSD only, closed, with broker P&L, fill, requested price, spread, commission and FX all present.
  - 95 fills in `evidence_exclusions` are removed. 17 pyramid adds are left out (none are in running arms). 17 rows
    were still open or incomplete.
  - The window is 2026-10-26..2026-11-13: 14 opportunity days at most (22:00 UTC boundary).
- **Running** means a row in the brain's deployed `config/exploration_arms.yaml` with `status: run` and no
  `retired_day` on or before 2026-11-13. F1A, F1C, G1A, ZCD and IEX15 retired today, and
  `exploration_arm_status` is empty.

## 2. Definitions (per trade, in R = the gate's stop distance `stop_usd`, USD/oz)

| quantity | definition |
|---|---|
| net R | broker `pnl` (profit + swap - commission, account currency) / FX / oz / `stop_usd`. Measured, nothing modelled |
| cost R | the **expected** cost: cost-of-record round trip (`cor-2026-10-31.2`, `all` cell: ICM $0.166/oz, Vantage $0.213) + stop overshoot on stop exits (ICM $0.11, Vantage 0) - the trade's **measured** swap (broker sign), all / `stop_usd` |
| charged cost R | the same with the ADR 0064 charge, max($0.23, round-trip ci90 upper bound) |
| gross R | net R + cost R. The pnl before costs. Realised slippage noise stays in gross, not in cost. Fill-level slippage estimates (fill vs touch, overshoot vs a reconstructed stop) were tried and rejected: they are dominated by the 1 s send latency and by not knowing the stop reference, and they gave negative "costs" on some arms |
| ratio | cost R / gross R. **FAIL** when gross <= 0 or ratio > 1/3 |
| CI, t | 95 % interval and t of the mean, **clustered by opportunity day** |
| excess | gross R minus the matched random control's gross R, with a two-sample day-clustered SE. That is the signal's own edge net of drift: the arm and its control pay the same costs |

Measured swap on these fills (USD/oz/night, from the trades that crossed a rollover, Wednesday triple):

| broker | long | short |
|---|---|---|
| ICM | -0.53 (n 9, -4.8 %/yr at $4,000) | +0.37 (n 11, +3.3 %/yr) |
| Vantage | -0.75 (n 7) | +0.31 (n 11) |

These agree with `settings.yaml` `markup_pct_by_symbol` (W3.3, swap-icm-2026-11-02): -4.76 / +3.29 %/yr.

## 3. Results: running gold arms

### 3.1 Signal arms (the ones the rule applies to)

| arm | rule, tf | acct | n | days | gross R [95 % CI] | cost R (charged) | ratio | net R (t) | matched control | excess R (t) | speed limit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Z2P | run_fade 1m | probe | 74 | 12 | **-0.017** [-0.197, 0.164] | 0.074 (0.095) | n/a (gross <= 0) | -0.090 (-0.96) | LCZ2P | -0.281 (-1.60) | **FAIL** |
| Z3D | round_break 5m | demo | 193 | 14 | +0.067 [-0.097, 0.231] | 0.049 (0.063) | **0.73** | +0.018 (0.22) | LCZ3D | +0.027 (0.16) | **FAIL** |
| Z1P | tick_surge 1m | probe | 43 | 11 | +0.226 [-0.144, 0.596] | 0.074 (0.095) | 0.33 (charged **0.42**) | +0.152 (0.80) | LCZ1P | **-0.087** (-0.39) | **FAIL** (at the charged cost, and below its own control) |
| Z5P | extreme_break 5m | probe | 44 | 13 | +0.248 [-0.190, 0.687] | 0.049 (0.063) | 0.20 | +0.199 (0.88) | LCZ5P | +0.312 (1.18) | pass (point only) |
| Z9D | htf_reclaim 5m | demo | 39 | 12 | +0.333 [-0.058, 0.724] | 0.048 (0.062) | 0.15 | +0.285 (1.42) | LCZ9D | +0.388 (1.68) | pass (point only) |
| Z10D | three_horizon 5m | demo | 57 | 13 | +0.263 [0.115, 0.412] | 0.048 (0.062) | 0.18 | +0.216 (2.81) | LCZ10D | +0.398 (2.38) | pass |
| GS1 | session_range_break 15m | demo | 10 | 8 | +0.499 [-0.641, 1.639] | 0.017 (0.022) | 0.03 | +0.482 (0.83) | GS1C | **-0.353** (-0.38) | unreadable (n 10); below its control |
| S1A | session_vwap_fade 5m | probe | 26 | 5 | +0.256 [-0.585, 1.097] | 0.053 (0.069) | 0.21 | +0.203 (0.47) | none | - | stale: no entry since 10-30 (no qualification) |
| S1B | session_vwap_fade 5m | probe | 28 | 5 | +0.208 [-0.143, 0.560] | 0.050 (0.066) | 0.24 | +0.158 (0.88) | none | - | stale, as S1A |

Pooled:

| pool | n | gross R [95 % CI] | cost R (charged) | ratio | net R (t) |
|---|---|---|---|---|---|
| 1m signal (Z1P, Z2P) | 117 | +0.073 [-0.111, 0.256] | 0.074 (0.095) | **1.02** | -0.001 (-0.01) |
| 1m their controls | 152 | +0.290 [0.138, 0.442] | 0.069 | 0.24 | +0.221 (2.80) |
| 5m signal (Z3D, Z5P, Z9D, Z10D) | 333 | +0.156 [0.055, 0.257] | 0.048 (0.063) | 0.31 | +0.107 (2.06) |
| 5m their controls | 283 | -0.053 [-0.201, 0.094] | 0.051 | n/a | -0.104 (-1.37) |
| All 7 H-90 arms | 460 | +0.142 [0.052, 0.232] | 0.054 (0.070) | 0.38 | +0.088 (1.89) |
| Their 7 controls | 445 | +0.084 [-0.056, 0.224] | 0.056 | 0.67 | +0.028 (0.39) |

### 3.2 Controls and calibration arms (null by design: the rule does not apply)

| arm | tf | n | gross R | cost R | net R (t) |
|---|---|---|---|---|---|
| S0 / S0R (1m random) | 1m | 444 / 194 | +0.069 / +0.135 | 0.073 / 0.072 | -0.004 (-0.09) / +0.063 (0.92) |
| LCZ1P / LCZ2P | 1m | 80 / 72 | +0.313 / +0.265 | 0.070 / 0.068 | +0.243 (1.96) / +0.196 (1.29) |
| LCZ3D / LCZ5P / LCZ9D / LCZ10D | 5m | 76 / 64 / 66 / 77 | +0.040 / -0.063 / -0.055 / -0.135 | about 0.051 | -0.010 / -0.114 / -0.106 / -0.186 |
| GS1C | 15m | 10 | +0.852 | 0.021 | +0.831 (1.12) |
| VEX15 / VEX1H / IEX1H (execution calibration) | 15m / 1h / 1h | 36 / 9 / 10 | +0.281 / +0.215 / -0.051 | 0.030 / 0.002 / 0.005 | +0.251 / +0.214 / -0.055 |

Controls are nulls. Their cost is what any arm on that bracket pays. The 1m bracket costs **0.07 R** (charged
0.09 R) and the 5m bracket **0.05 R** (0.06 R). At 15m-1h it drops to **0.002-0.03 R**, swap included. Under the speed
limit a 1m arm needs a gross edge of **>= 0.22-0.28 R**, and a 5m arm **>= 0.15-0.19 R**. A 1h arm needs only
about 0.01-0.05 R.

## 4. Reading and recommendations

**Arms failing the cost budget (point estimates, 14 days):**

| arm | why | recommendation |
|---|---|---|
| **Z2P** run_fade 1m | gross <= 0; excess vs control -0.28 R (t -1.6) | stop |
| **Z3D** round_break 5m | ratio 0.73; the largest sample of any signal arm (193 fills, 14 days); excess +0.03 R (t 0.16) | stop |
| **Z1P** tick_surge 1m | ratio 0.42 at the ADR 0064 charge (0.33 at the point cost); gross is below its own random control | stop |
| GS1 (owner experiment to 11-27) | n 10, so the ratio cannot be read, and it is below GS1C | no action: it ends 11-27 under its own rule (owner experiment, not ours to stop) |
| S1A, S1B | configured "run" but send nothing (no qualification) since 10-30 | tidy-up only: add a `retired_day` so the board stops showing them as running (data-engineer/PD; no K effect) |

Z5P, Z9D and Z10D pass at the point estimate. Only Z10D's gross CI excludes 0. Z9D's and Z5P's include 0.

**Why the history prior should outweigh these point estimates (principal's reading):**

1. The forward window is 14 days of one regime, and the **random controls** show it. The 1m controls earn +0.29 R
   gross (t 3.75) and the 1m signal arms +0.07 R. The window rewards a 1 R / 1 R bracket by itself, so a positive
   gross on a 1m arm here is not evidence of signal.
2. H-82 replayed the same entry code on 2010-2017 and bounded gross at **±0.011 R per arm** (all hours pooled,
   `prereg-2026-11-12-london-signal-arms.md` section 0). Against that prior, every 1m and 5m arm **fails the speed
   limit by 4-9x** (cost 0.05-0.07 R vs a gross of at most 0.011 R). The forward +0.15-0.33 R grosses are 15-30x the
   history bound. Either the regime changed (a claim needing evidence), or they are noise plus drift. The second is
   much more likely.
3. So the budget rule's real verdict is structural, not per-arm. **No 1m or 5m bracket with a $3-4.50 stop can carry
   its cost unless it has a gross edge of 0.15-0.28 R.** No history screen in this program has found one (R-01, R-02,
   R-05, R-14, R-15, R-17, H-82, sprint-3000: "single intraday rules on gold retired as a search space"). The live
   1m/5m book should be treated as learning and cost measurement, never as an earner.

**Conflict with H-90 (must be resolved by the research director, not here).** Z1P, Z2P and Z3D are 3 of the 7 frozen
H-90 arms. H-90 section 2 lets an arm leave only "under its own rules". Stopping them on the cost budget is an
**amendment**. K is unchanged: the population shrinks, and the read uses the remaining arms' fills.

My recommendation:

- Stop Z2P now. It has a negative gross and adds nothing to H-90's London question.
- Stop Z3D and Z1P at the same amendment, unless the research director wants H-90's power more than the saved cost.
  Little money is at stake at 0.01 lot: Z2P loses about £0.20 a fill, and Z1P and Z3D are net positive in this
  window. But they hold daily admission slots that a cost-feasible line could use.
- If the director keeps them for H-90, they end on 2026-12-17 as frozen, and the gate applies at their read.

**Gate proposal (plan line 2, for PD + PO and quant sign-off).** From 2026-11-13, no new arm is admitted to demo
unless its pre-registration shows **expected cost <= 1/3 of the expected gross edge**. The cost is computed at
`cor-2026-10-31.2` plus the measured swap for its typical hold. The gross is taken from its history screen (not from
forward fills). A forward read applies the same test at its checkpoints.

Owner-only items: none. This changes demo admission only, inside the current caps.

## 5. Limits of this audit

- 14 days, one regime. Day-clustered CIs are wide: no running signal arm's ratio is significantly different from 1/3.
  The verdicts are point estimates, as Carver applies the rule (ex ante). The history prior (section 4, point 2) is
  the stronger evidence.
- Gross = net + expected cost. The realised slippage of a trade sits in its gross. That is unbiased for the mean but
  adds noise.
- Session cells are not split (the `all` cell). London is about 0.002 $/oz cheaper than the `all` cell, which is
  immaterial at these stops.
- Reproduction: the analysis scripts were scratch files (`audit.py`, `swap.py`) and are not in the repo. The inputs
  are the brain's `aux.db` and `config/exploration_arms.yaml` on 2026-11-13. A re-run on a later day will include
  more fills.
