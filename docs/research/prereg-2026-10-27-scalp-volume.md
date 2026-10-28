# Pre-registration H-81: 1m/5m scalp-volume families, Vantage S-twins and per-account allocation (2026-10-27)

Author: microstructure-researcher (principal), 2026-10-27. Plan: `volume-500-plan-2026-10-26.md` sections 2-3 and its
delegation line "new 1m/5m volume families". Targets: CFO ADR 0049 (branch `cfo/demo-volume-500-funding`, proposed):
icm-probe 250, icm-demo 150, vantage-demo 150 rising to 200 once 200 of its own fills measure its cost; budgets
unchanged (£198 / £198 / £180). The owner's correction: "500 plus overall, 200 per account".

**Frozen before any code, history run or fill of any arm below.** Every rule here was written before any return of it
was computed. The only data read was 2010-01..2017-12 Dukascopy 1m, for two outcome-blind things:
- **event counts** (fire rates), as in arms 8.14 "a count reads no return";
- **the per-trade SD in R of random-entry brackets**: the null, with no rule and no side, used only for the cap
  arithmetic.

No 2018+ bar was opened. The holdout (2025-10-01..2026-09-30) was not read, and no registry trial is consumed. Scripts
were kept outside the repo (scratchpad `fire_rates.py`, `fire2.py`, `null_sd.py`, `cap27c.py`); every input is in the
tables. Editing an arm after its first fill or history run needs a dated amendment and a new arm id.

## 0. Conventions (arms-2026-10-25.md sections 0 and 8.0 bind every arm here; additions below)

- **Size and brackets.** 0.01 lot = 1 oz, never resized. The decision is on the close of bar t, with market entry
  right after. Stop and target are **fixed prices set at entry**, searched on 1m bars with the stop checked first.
  The time exit is at the close of the hold-th decision bar. There is no trailing and no break-even.
- **Stops are fixed dollars per broker, not ATR multiples.** The stop decides cost per R, and cost is per broker:

| Broker geometry | 1m rules | 5m rules | Cost/R at the measured cost | Cost/R at the conservative cost |
|---|---|---|---|---|
| ICM (icm-probe, icm-demo) | stop $3.00 | stop $4.50 | $0.156: 0.052 / 0.035 | $0.23: 0.077 / 0.051 |
| Vantage (vantage-demo) | stop $7.00 | stop $7.00 | $0.52: 0.074 | none yet (5 fills) |

  - Every arm passes cost/R ≤ 0.08 at its broker's measured cost.
  - **Vantage's $7.00** is the smallest round stop with cost/R ≤ 0.08 at $0.52 ($0.52 / 0.08 = $6.50).
  - **The scalp per-trade cap holds:** £6.50 = $8.64, and $7.00 is £5.26.
  - **Volatility check:** at today's median ATR (1m $1.83, 5m $4.43, arms section 0), $3.00 is 1.6 ATR1m, $4.50 is
    1.0 ATR5m and $7.00 is 1.6 ATR5m. A fixed-dollar stop's width in ATR drifts with volatility. ADR 0047's ATR-regime
    factor already acts on `max_open`, and every read reports net R by ATR-regime tercile (descriptive).
- **Volume proxy.** v = the bar's tick volume (Dukascopy `tick_count` in history; MT5 `tick_volume` live). It is only
  ever compared with its own trailing median on the same feed, never across feeds or brokers.
- **Session window W (local clocks):** from 19:00 New York (about Tokyo 08:00) to 16:00 New York, Monday to Friday
  trading days (the NY 17:00 day of section 8.0). The common filters apply:
  - tier-1 blackout;
  - no entry 20:45-22:15 UTC, after 19:00 UTC on Friday, 16:55-17:00 New York or 18:00-18:30 New York;
  - no entry on CME holidays or early closes.
- **Spread gate (microstructure, new):** skip the candidate when the spread at decision is above 2 × the median spread
  of the account's own last 60 minutes. Each skip is journalled `spread_wide` with its propensity. History uses
  Dukascopy `spread_mean` the same way.
  - *Why:* the cost model's $0.156 comes from scheduled quiet-time probes. Several of these rules fire in fast
    markets, where spreads widen several-fold (cost model caveat 2). The gate keeps them inside the cost they were
    sized on.
- **Hedging or netting.** On a netting terminal, a signal opposite an open position of another arm is skipped and
  logged (`net_conflict`). It is never netted.
- **Stop overshoot coupling.** If S0R reads "real" (arms section 4: mean > $0.30, CI lower bound > $0.10), every
  $3.00 arm here (Z1P, Z2P, Z8P, Z4D) is suspended by the same amendment as S0, S5A and S5B.
- **Shared controls, one per account** (precedent: arms 8.13, borrowed controls):
  - `ZCD` (icm-demo) and `ZCV` (vantage-demo) are new.
  - icm-probe borrows **S0**.
  - For the day-paired family-minus-control read, each control fill is **virtually re-bracketed** on its own 1m path,
    from its real fill price, with the family's stop, target and hold.

## 1. The ten families (strata = arm ids per account)

Fire rates are events per trading day on 2010-2017, window W, before the blackout and spread gate. "Fills" = rate ×
0.85, an assumed admission share for blackout, the spread gate, slots and pot refusals; it is measured at step 1. The
round-number grid was scaled to the 2010-17 price level, so that handle/ATR5m equals today's $10 / $4.43.

| Family | TF | Entry rule (closed bars only; mid prices; ATR = Wilder 14 of the TF, read at t unless stated) | Side | Target / hold | Rate/day 2010-17 | Strata (account) |
|---|---|---|---|---|---|---|
| **Z1 TSC** tick-surge continuation | 1m | v_t ≥ 2.5 × median(v, t−60..t−1); \|c_t − o_t\| ≥ 0.8 ATR_{t−1}; close in the outer 30% of the bar's range in the body's direction; at most 1 per 10 bars | sign(c_t − o_t) | 1.5 R / 15 bars | 13.8 | Z1P (icm-probe) |
| **Z2 RXF** run-exhaustion fade | 1m | three consecutive closes in one direction (c_t − c_{t−1}, c_{t−1} − c_{t−2}, c_{t−2} − c_{t−3}, all nonzero and the same sign); \|c_t − c_{t−3}\| ≥ 2.5 ATR_{t−3}; v_t < v_{t−2} (participation fading); 1 per 10 | against the run | 1.0 R / 15 | 13.9 | Z2P (icm-probe) |
| **Z3 RNB** round-number break | 5m | handles = multiples of $10.00. Long when floor(c_t/10) > floor(c_{t−1}/10) and c_t − 10·floor(c_t/10) ≥ 0.15 ATR_t; short mirrored (c_t ≤ 10·floor(c_{t−1}/10) − 0.15 ATR_t); 1 per 3 | break side | 1.5 R / 6 | 30.9 | Z3D (icm-demo), Z3V (vantage, stage V2) |
| **Z4 RNR** round-number touch rejection | 1m | U = 10·ceil(c_{t−1}/10), L = 10·floor(c_{t−1}/10). Short when h_t ≥ U, c_{t−1} < U and c_t ≤ U − 0.25 ATR_{t−1}; long mirrored at L; 1 per 15 | rejection side | 1.0 R / 15 | 32.5 | Z4D (icm-demo) |
| **Z5 XBT** tick-confirmed prior-hour extreme break | 5m | HH_t = max high of bars t−12..t−1 (LL mirrored). Long when c_t > HH_t + 0.1 ATR_t and c_{t−1} ≤ HH_{t−1} and v_t ≥ 1.5 × median(v, t−48..t−1); 1 per 6 | break side | 1.5 R / 6 | 6.8 | Z5P (icm-probe), Z5V (vantage, V2) |
| **Z6 NRB** NR4 inside-bar break | 5m | range_{t−1} = min of the ranges of t−4..t−1, and bar t−1 is inside bar t−2; long when c_t > h_{t−1}, short when c_t < l_{t−1}; 1 per 3 | break side | 1.5 R / 6 | 13.5 | Z6D (icm-demo), Z6V (vantage, V1) |
| **Z7 HHP** same-half-hour periodicity | 5m | at each 5m close on a New York half-hour (:00, :30): S = sum over the previous 10 trading days of the mid return over the same New York half-hour [τ, τ+30 min); ≥ 8 of the 10 days present, else skip; S = 0 skips | sign(S) | 1.5 R / 6 (the half-hour itself) | 40.9 | Z7D (icm-demo), Z7V (vantage, V1) |
| **Z8 ABS** volume absorption fade | 1m | v_t ≥ 2.5 × median(v, t−60..t−1); \|c_t − o_t\| ≤ 0.2 ATR_{t−1}; P = c_{t−1} − c_{t−6} with \|P\| ≥ 1.5 ATR_{t−1}; 1 per 10. Disjoint from Z1 by the body condition | −sign(P) | 1.0 R / 15 | 4.2 | Z8P (icm-probe) |
| **Z9 HPB** 1h-trend 5m reclaim | 5m | 1h trend from the last **closed** 1h bar (`asof_join` on `available_utc`): up when EMA20 > EMA50 and close > EMA50, down mirrored. Long (trend up) when closes t−3..t−1 < EMA20(5m) and c_t > EMA20(5m)_t; short mirrored; 1 per 6 | trend side | 1.5 R / 12 | 7.4 | Z9D (icm-demo) |
| **Z10 THM** three-horizon momentum | 5m | \|c_t − c_{t−1}\| ≥ 1.0 ATR_{t−1}, and its sign equals the sign of the return of the last closed 1h bar and of the 15m bar before the last closed one (so the 15m vote excludes bar t); 1 per 6 | that sign | 1.5 R / 6 | 7.3 | Z10D (icm-demo) |
| **Controls** | 5m | ZCD / ZCV: at each 5m close in W with the common filters, enter when `hash_u(id, close) < p` (ZCD p 0.07, ZCV p 0.05); side `hash_side(id, close)`, the section-0 hash of arms-2026-10-25 | random | 1.5 R / 6 at the account's 5m stop; virtual re-brackets per family | 17 / 12 | ZCD, ZCV; S0 borrowed on icm-probe |

**Concurrency** (all ten at their time-exit upper bound, one account, 2010-17): mean 3.3 open, p99 8, maximum 12, and
0.1% of minutes at 10 or more open. The scalp `max_open` 8-12 binds only at the tail.

### 1.1 Mechanism, evidence, and the neighbours that must not be re-tested

None of these has a cost-surviving, replicated gold result at 1m/5m. **Every family is exploration only. Prior
P(continue) is in the last column.**

| Family | Mechanism and literature (instrument, sample, gross or net) | Retired neighbour, and what is new here | Prior |
|---|---|---|---|
| Z1 | Order-flow imbalance persists over minutes. Chordia & Subrahmanyam 2004, *JFE* 72(3): NYSE stocks 1988-1998, daily imbalance, gross *(unverified)*. Easley, López de Prado & O'Hara 2012, *RFS* 25(5), VPIN: E-mini futures, toxicity rather than direction *(unverified)*. Single-paper, equities | none retired. Tier-1 news minutes are excluded by the blackout, so this is the non-news surge | 0.05 |
| Z2 | Short-horizon reversal as the return to liquidity provision. Grossman & Miller 1988, *JF* 43(3), theory; Nagel 2012, *RFS* 25(7): US equities 1998-2010, daily, gross *(unverified)* | **R-01** (15m reversion, no gross edge); "oscillator fades 15m-1h" (time-structure). New: 1m, volume-fading runs only. Expect ≤ 0 | 0.02 |
| Z3 | Stop-loss orders cluster just beyond round numbers, so crossings accelerate. **Osler 2003, *JF* 58(5)**: one bank's FX order book, 1999-2000, gross; Osler 2005, *JIMF* 24, stop cascades *(unverified)*. Single-source, FX | G1 (H-71) uses round numbers on 1h in Asia; "key-level breaks" were not retired. New: the 5m break | 0.05 |
| Z4 | Take-profit orders cluster **at** round numbers, so the touch reverses (Osler 2003, as above). Kavajecz & Odders-White 2004, *RFS* 17(4): NYSE, levels as liquidity *(unverified)* | "key-level retests and fades" retired at 15m+ (time-structure; slice G). **New evidence:** slice C 3.3 found a level's edge sits in the touch minute, not after a 15m close (arms 8.4/8.5). Z4 trades that minute at 1m. The evaluator records the re-entry note before Z4's read | 0.03 |
| Z5 | Cascades beyond recent extremes (Osler 2005), conditioned on participation (v ≥ 1.5 × median) | "unconditional band breaks" (15m) and R-04 (1h breakout). New: 5m, and the tick-volume condition (the rule is not unconditional) | 0.03 |
| Z6 | Volatility clustering: a compressed bar precedes expansion. Crabel 1990, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (practitioner, grade D) *(unverified)* | TTM squeeze (15m) and R-04. New: the 5m bar-level NR4 | 0.02 |
| Z7 | **Same-half-hour return continuation across days. Heston, Korajczyk & Sadka 2010, *JF* 65(4): NYSE stocks 2001-2005, half-hour returns, gross; lasts about 40 days. Replicated in equities:** Bogousslavsky 2016, *JF* 71(6) (infrequent rebalancing) *(unverified)*. For gold or futures: no direct test found (search 2026-10-27) | R-08 (weekday effects) and the clock anchors (LBMA/COMEX/Tokyo) are different mechanisms. R-05 (Gao intraday momentum) is a within-day effect; this one is across days | 0.06 |
| Z8 | High volume with no price change means passive liquidity is absorbing the aggressive side, so the prior move reverses. Llorente, Michaely, Saar & Wang 2002, *RFS* 15(4): NYSE/AMEX 1993-1998, daily, gross *(unverified)* | R-14 (`sweep_absorb`) fades level sweeps at 15m; Z8 needs no level and is 1m | 0.03 |
| Z9 | Higher-timeframe trend continuation after a shallow pullback (Moskowitz, Ooi & Pedersen 2012, *JFE* 104(2), TSMOM, monthly; transferred here intraday, unsupported) | **R-03** (1h pullback); S5 (1m, 15m trend). New: 5m with a 1h trend. Expect ≤ 0 | 0.02 |
| Z10 | Horizon-aligned momentum (TSMOM as above) | **R-06/R-10** (tsmom 1h/4h). New: 5m impulse with 15m and 1h agreement | 0.02 |

**Declined and why** (so nobody spends a slot on them):
- **News-jump continuation.** It needs an exception to the tier-1 blackout, which is a safety filter, and it fires at
  the spread-widening moments the cost model never saw. Re-propose only after a signal-time cost read at news.
- **Session-VWAP fades, 1m/5m ORB, sweep reversals, Asia-range fades.** These are retired or closed (time-structure;
  H-76). Running them on 1m would only re-test them.
- **Limit-entry variants.** There is no limit-fill data (cost model caveat 4). The cost probe needs a limit arm first.

## 2. Scalp twins

### 2.1 Vantage S-twins (frozen, `status: blocked` until their cost gate)

| id | twin_of | Rule, TF, params, max_open | Gate to run |
|---|---|---|---|
| VS0 | S0 (control) | identical to the twin (the loader checks); own seed | as the strategy twins it controls |
| VS1A, VS1B | S1A, S1B | identical | Vantage 95% upper bound of the round trip ≤ **$0.24** on ≥ 200 own fills: S1's $3-6 stop then has cost/R ≤ 0.08 |
| VS2A, VS2B, VS3A, VS3B | S2A, S2B, S3A, S3B | identical | ≤ $0.24; at $0.24-0.56 they may run with a per-candidate skip, `cost_per_r`, of any stop below UB/0.08. Logged, not resized |
| VS5A, VS5B | S5A, S5B | identical | ≤ $0.24 ($3.00 / $3.75 stops) |

- **S4 has no twin.** R-15 has no promotion route, and a box twin adds no cost information the Z strata lack.
- **Why blocked today:** at $0.52, S0's $3 stop costs 0.17 R, twice the bar.
- Twins have no history run and flat priors (arms 7). Each (broker, arm) is its own id and K slot.

### 2.2 icm-demo S-twins: declined (ids DS0..DS5B reserved, not registered, not in K)

- The brief asked for them. I decline, for the plan's own reason (2.3 "Not done"): a same-rule twin on the **same
  broker** doubles correlated fills of one rule on the same days. It adds no cost information (same ICM cost basis)
  and almost no rule information.
- Six of the ten new families give icm-demo its own scalp volume instead, so all three accounts carry scalp.
- The research director may overrule by amendment. The ids are reserved so the names cannot collide.

## 3. Allocation per account (ADR 0049 targets; caps, % ceilings, per-trade caps and `budget_share` unchanged)

### 3.1 Inputs

| Item | ICM | Vantage |
|---|---|---|
| £ at the stop, 0.01 lot | 1m £2.26; 5m £3.38 | £5.26 |
| Round trip £ per trade | £0.117 measured ($0.156); £0.173 conservative ($0.23) | £0.391 ($0.52, 5 fills) |
| Per-trade SD in R of the **null** bracket, 2010-17 (random side and time, same stop in ATR, target, hold) | 1m $3 / 1R / 15 min: **0.95**; 5m $4.50 / 1.5R / 30 min: **1.08** (no target: 1.44, so every family has a target) | 5m $7 / 1.5R / 30 min: **0.88**; 1m-rule $7 / 1R / 15 min: **0.66** |

**Model:** the plan's first-passage formula (2.1), with the drift at zero gross edge. D, the within-day variance
inflation, is shown at 1 / 1.5 / 2: ADR 0049 uses 1.5 until research D4 measures it. "Bind" means the pot or budget
stops new entries for the day. Probes on icm-probe are ≤ 20% of its count, as the plan requires.

### 3.2 Fills per day per account (expected; stages are deterministic, so pot refusals do not choose the sample)

| Account | Now (step 1, 11-02) | Arm fills/day | Total | Later stage |
|---|---|---|---|---|
| **icm-probe** | S-arms today 129 + Z1P 11.7 + Z2P 11.8 + Z8P 3.6 + Z5P 5.8; probes 50; the plan's pot re-split (£138 → £168) admits about +40 of today's 44 `arm_budget` refusals | scalp ≈ 200, probes 50 | **≈ 250** | none needed |
| **icm-demo** | Z3D 26.3 + Z4D 27.6 + Z6D 11.5 + Z7D 34.8 + Z9D 6.3 + Z10D 6.2 + ZCD 15 = 128; intraday ≈ 9; swing ≈ 1 | scalp 128 | **≈ 138** (option A below); **≈ 120** under today's pot mechanism (option B) | 150 needs D ≤ 1.2 (ADR 0049) or option A |
| **vantage-demo** | **V1:** Z6V 11.5 + Z7V 34.8 + ZCV 12 = 58; probes 16-30; depth 5 | scalp 58 | **≈ 80-95** | **V2** (`vantage-swing` live, wave 3, 10-29..10-30; vantage-demo becomes scalp + probes): + Z3V 26.3 + Z5V 5.8, giving **≈ 120-135**. **V3** (95% UB ≤ $0.35 on ≥ 200 own fills): the 1m strata Z1V/Z2V/Z4V/Z8V at $7, registered by dated amendment with this text, + 55, giving **≈ 175-190**. At UB ≤ $0.24 the VS twins unblock: **200+** |
| **Total** | | | **≈ 470-480** now; ≈ 510 at V2; ≈ 560-600 at V3 | |

These are filled fills. The plan's admissibility test (not void, reconciled) is separate: on 10-26, 45% of fills were
void. **The 500 is only real after step 0's repair** (void ≤ 5%, `reconcile:*` green).

### 3.3 Cap arithmetic (pots inside the unchanged 0.9 budget)

| Host, mix | Fills | Drift £/day at zero edge | σ £/day (D1) | Open risk H | Pot / budget | P(bind) D1 / D1.5 / D2 | Weekly drift (6% cap ≈ £600-660) |
|---|---|---|---|---|---|---|---|
| icm-probe 250, measured cost | 250 | 29.3 | 40.0 | 8.2 | £198 | 0.0% / 0.1% / 0.4% | £147 (1.3%) |
| icm-probe 250, conservative | 250 | 43.2 | 40.0 | 8.2 | £198 | 0.0% / 0.2% / 0.8% | £216 |
| icm-probe scalp 200 in the re-split pot | 200 | 23.5 | 36.7 | 6.4 | £168 | 0.0% / 0.2% / 0.7% | £117 |
| **icm-demo, option A: one shared host budget** (scalp 128 + intraday 10 + swing 1) | 149 | 17.5 (25.8 conservative) | 68.1 | 37.4 | £198 | **3.3% / 8.0% / 12.7%** (4.2 / 9.5 / 14.5) | £87 (0.8%) |
| icm-demo, option B: separate pots; scalp pot £90 / 8 open at 110 fills | 110 | 12.9 | 35.7 | 5.5 | £90 | 4.0% / 9.2% / 14.2% | £65 |
| the same option B, intraday pot £68 (from £108) at 10 fills | 10 | 1.2 | 38.0 | 11.5 | £68 | **14.4% / 23.2% / 30.0%** (today's £108: 1.2 / 4.0 / 7.6) | — |
| **vantage V1** (depth still on the host): scalp pot £100 / 8 open, 58 fills, $0.52 | 58 | 22.7 | 35 | 5 | £100 | ≈ 3.5% / 8.5% / 13% | £114 + probes |
| vantage V1 if all 5m strata ran now (73 fills) | 73 | 28.5 | 39.6 | 6.1 | £100 | 8.0% / **14.5%** / 20.1% | rejected |
| **vantage V2**: scalp + probes on the whole £180, $0.52 | 135 | 52.8 | 51.7 | 10.6 | £180 | **1.9% / 5.2% / 8.9%** | £264 (2.6%); −8% in ≈ 15 trading days |
| vantage V3 at 195, $0.35 | 195 | 51.3 | 58.7 | 15.2 | £180 | 4.2% / **9.2%** / 14.0% | £257 |
| vantage V3 at 195, $0.52 | 195 | 76.3 | 58.7 | 15.2 | £180 | 9.5% / **16.1%** / 21.4% | £381: refused |

**What the arithmetic says:**
1. **icm-probe 250 is safe at any plausible cost.** The cap binds on under 1% of days. The constraint is supply and
   void rows, not £.
2. **icm-demo can carry about 140 inside the £198, but only as one budget (option A).**
   - Today the ADR 0043 pots split £198 into intraday £108 and swing £90, with no scalp pot.
   - To carve a scalp pot (option B), the intraday pot drops to £68. It would then bind on about 23% of days at
     D1.5, which starves the depth families.
   - ADR 0049's 8.3% figure for icm-demo 150 is a whole-budget figure. **It holds only if the style pots become
     shares of one host budget.**
   - Under option B, icm-demo carries about 120.
3. **Vantage: 200 is impossible inside its £180 at $0.52.**
   - The ≤ 0.08 cost/R bar forces $7 stops, which is £5.26 at risk per trade, 2.3× ICM's 1m.
   - At $0.52 the most that fits at ≤ 10% bind (D1.5) on the whole £180 is about **165 fills**. With today's
     intraday/swing pots still on the host, it is about **60 scalp fills** in a £100 pot.
   - **The fixes, in order:**
     1. Measure the cost: ≥ 200 own fills, the 95% UB. At ≤ $0.35, 195 fits (9.2%).
     2. Move the depth arms to `vantage-swing` (wave 3), so the whole £180 serves scalp.
     3. A demo top-up does not help the daily bind (the % caps scale with balance). It only resets the −8% clock,
        under the ADR 0049 roll. **CFO decision.**
   - ADR 0049's "vantage 150 now at 1.6%" assumes $3-4.5 stops. At $0.52 those carry cost/R 0.12-0.17, which **fails
     the 0.08 bar** this brief set. The CFO and research director should pick one bar.
4. **Drift is the real cost.** Zero-edge exploration costs about £29 (icm-probe) + £18 (icm-demo) + £23-53 (Vantage)
   = **£70-100 of demo £ a day**. Vantage reaches −8% in about 15 trading days at V2, which is ADR 0049's roll.

### 3.4 Pot and `max_open` changes requested (tighten-or-redistribute only; the pots still sum to ≤ the budget)

| Host | Today | Requested | Decision |
|---|---|---|---|
| icm-probe | scalp £138 / 12 | £168 / 12 (the plan's re-split; probe sleeve £30) | research director + CFO; trading-safety check |
| icm-demo | intraday £108 / 9, swing £90 / 2 | **Option A (recommended):** pots become soft shares under one £198 host budget (needs an ADR 0047 amendment). **Option B:** scalp £90 / 8, intraday £68 / 7, swing £40 / 2 | CFO + trading-safety (A is an engine change) |
| vantage-demo | intraday £98 / 9, swing £82 / 2 | V1: scalp £100 / 8, intraday £40 / 4, swing £40 / 1. V2: scalp £150 / 12, probes £30 | CFO; trading-safety |

`max_open_total` 17 and `max_positions` 18 are unchanged. Peak scalp concurrency is ≤ 8 at p99 (section 1).

## 4. Reading rule (fixed now, per family; the arms-2026-10-25.md 8.0 rule with these specifics)

- **Status.** Exploration evidence only, **never promotion evidence**. A read can say at most *continue*, and
  *continue* only earns a new pre-registration (gate 1 at that day's K, the holdout once, forward n ≥ 60). Every id is
  counted in K.
- **Unit.** The family, pooled across its strata. Broker strata get equal weights, so a family on two brokers is read
  once. The consistency check is arms-expansion 2.1. A family continues or stops as a whole, never by its best stratum.
- **Primary measure.**
  - Net R per admissible fill, at each fill's **signal-time cost** (`label_fill_r`), with an SE clustered by UTC day
    (CR1).
  - It is also reported at the conservative cost and at the S0R-measured overshoot.
  - Fills taken under a sleeve refusal regime are IPW-weighted, with the unweighted mean beside it.
  - `spread_wide` and `stop_*` skips are excluded (they are not untaken candidates).
- **One read, at the 300th admissible family fill on ≥ 20 session days.**
  - **continue** if P(mean > 0) > 1 − 0.05/m (m = the strategy arms on that broker eligible at the read; H-61) **and**
    the family minus its control, paired by day, is > 0.
  - **stop** at −30 R within 300 fills, or for futility at 300 (the one-sided 95% upper bound of the mean < +0.03 R).
  - otherwise **inconclusive**: collect on, with no outcome read before 1,000.
- **Cost stop per stratum** (independent of R). At ≥ 30 fills, if the 95% UB of signal-time cost per R is > 0.10, the
  stratum's share is halved (ADR 0047 ladder). Above 0.20 it is suspended until a dated amendment.
- **Controls** (ZCD, ZCV, borrowed S0) are never read for edge and never graduate.
- **Descriptive only, no decision:** net R by ATR-regime tercile, by session (Asia / London / NY), by spread state,
  per handle for Z3/Z4, and Z7 by half-hour.

## 5. Trial cost and K

- **Registry trials: 0.** These are exploration rows (H-61 class). The 2010-17 counts and the null-SD read add no
  look: no return of any rule was computed.
- **New arm ids now: 25.**
  - 16 strategy strata: Z1P, Z2P, Z5P, Z8P; Z3D, Z4D, Z6D, Z7D, Z9D, Z10D; Z3V, Z5V, Z6V, Z7V; plus ZCD and ZCV.
  - 9 Vantage S-twins, blocked but counted now (KSQ4 precedent).
- **Later, by amendment only:** Z1V, Z2V, Z4V and Z8V, at V3, +4. The DS ids are never registered.
- **Precedent count, one per id:** the 2026Q4 override goes 118 → **143**, and K goes from about 370 to about 395
  (gate-1 t ≈ 4.62). This is the program director's decision, on the waves' precedent. One registry row per id before
  its first fill.
- **Families count once in the registry,** each with its strata as declared variants. m_eff is measured on a 2010-17
  `run_arm_backtest` of the ICM strata. That run is a later history run, and it reads returns, so it needs the H-60
  look rules.

## 6. Flags (decisions not taken here)

1. **Bar conflict (CFO + research director).** ADR 0049 sizes Vantage 150 on $3-4.5 stops (cost/R 0.12-0.17 at
   $0.52). This file holds the brief's ≤ 0.08, which gives Vantage about 80-95 now. One bar has to be chosen.
2. **icm-demo pot structure (CFO + trading-safety).** Option A (shared budget) or B (separate pots, about 120/day,
   intraday starved).
3. **Vantage cost read first** (SRE/data). It is the cheapest lever: 200 fills at V1 take about 3 days. Top-ups do not
   fix the daily bind (CFO).
4. **S0R coupling.** The $3 strata stand or fall with S0R's overshoot read.
5. **Engine work** (implementer, failing tests first): rule functions with prefix-invariance tests; `tick_volume` on
   the 1m/5m frames; the spread gate; the `net_conflict` skip; virtual re-brackets for ZCD/ZCV/S0; the bridge burst
   at :00/:30 (Z7 plus probes); and, per the plan, probe minutes moved off the quarter hour.
6. **Owner-only:** none is assumed. No live account, sizing, spending or cap is touched.

## 7. Amendment 2026-10-27 (a): step-1 total of 520 or more on icm-probe; one stop bar for Vantage

Written on 2026-10-27 at the program director's decision, before any code, history run or fill of any arm in this
file. No return was read; the inputs are section 1's counts and section 3's null SDs. This amendment overrides
sections 1-5 where they differ.

### 7.1 icm-probe carries the gap

The loosening rule stands: no threshold of any rule is loosened. The added volume is **new decision points, disjoint
from icm-demo's**, so it is not a same-rule duplicate on one broker.

- **Z7P**, a declared stratum of Z7. It is Z7's rule at the **New York :15 and :45** half-hour starts: S sums the same
  New York half-hour [τ, τ+30 min) over the previous 10 days. Its stop is $4.50, 1.5 R target, 6 bars.
  - Z7D samples :00/:30, so the two strata never share a decision time or an interval.
  - Rate: 40.9 a day (2010-17 census at the offset grid), about **35 fills** a day.
- **Z4P**, a declared stratum of Z4. It is Z4's rule at the **$5-offset handles** (prices ≡ 5.00 mod 10.00), with a
  $3.00 stop, 1 R target and 15 bars.
  - Z4D trades the $10 handles, so the levels are disjoint.
  - Osler 2003 ranks roundness: a $5 handle is a weaker cluster, so the stratum is reported per handle class
    (descriptive).
  - About **28 fills** a day.
- **Probes** go from 50 to about 54 a day, every 10 minutes off the quarter hour (plan lever). That is 17% of the
  count, under the 20% rule.
- **Slots.** icm-probe's scalp pot `max_open` goes 12 → **14**, inside the unchanged `max_open_total` 17 (with 3 probe
  slots) and `max_positions` 18. The pot is re-split to £168 (section 3.4); the £198 and `budget_share` are
  unchanged.
- **Ids.** The two strata are new ids under one family each (+2). The precedent count gives 2026Q4 143 → **145**, and
  K about 397. This is the director's decision.

**icm-probe at about 323 a day** (scalp 269, probes 54):

| Case | Drift £/day | σ (D1) | P(bind) D1 / D1.5 / D2 | Weekly |
|---|---|---|---|---|
| Whole £198, $0.156 | 37.9 | 47.9 | 0.2% / **1.0%** / 2.5% | £189 (1.7%); −8% in about 23 trading days |
| Whole £198, $0.23 | 55.9 | 47.9 | 0.5% / **2.1%** / 4.4% | £279 |
| Scalp pot £168, $0.156 | 31.6 | 45.0 | 0.4% / **1.9%** / 4.1% | — |
| Scalp pot £168, $0.23 | 46.5 | 45.0 | 1.1% / **3.5%** / 6.5% | — |

- Below 5% at D1.5 in every case up to the conservative cost.
- **Tripwire:** if its 10-day realised bind rate is above 5%, or icm-probe's measured cost rises above $0.23, Z7P and
  Z4P stop first, as one rung.

### 7.2 One stop bar for Vantage, reconciled with ADR 0049

- **The bar: cost/R ≤ 0.08 at the broker's measured round trip,** using the 95% upper bound once there are ≥ 200 own
  fills. At $0.52 that means a **$7.00** stop on every Vantage scalp arm. At UB ≤ $0.24 it allows $3, and the
  S-twins unblock.
- **Why this bar.** At $3-4.5 stops and $0.52, cost alone is 0.12-0.17 R. The volume plan's 3× rule then needs a
  gross 0.35-0.5 R per trade, which no family here has a basis for. That would buy cost-dominated labels.
- **Conflict with ADR 0049 (proposed, CFO).** D1/D2 size vantage-demo at 150 now on $3-4.5 stops (1.6% bind at
  D1.5). Under the $7 bar each trade risks £5.26, so:
  - with its intraday and swing arms still on the host, the cost-safe level is a **£100 scalp pot ≈ 58 scalp fills
    (8.5% at D1.5) + probes + depth ≈ 80-95**;
  - with them moved to `vantage-swing` (V2), it is **≈ 120-135 (5.2%)**;
  - the whole-£180 ceiling at $0.52 is **≈ 165**. Vantage is held at or below it until its measured cost is in.
  - **Requested of the CFO:** amend ADR 0049 D1/D2 so that Vantage's target is "the cost-safe level at the $7 bar".
    That is about 90 now, about 130 at V2, ≤ 165 at $0.52 and 200 at UB ≤ $0.35. Its ≤ 10% bind rule is unchanged.

### 7.3 Step-1 numbers (11-02)

| Account | Fills/day | P(bind) at D1.5 |
|---|---|---|
| icm-probe | **≈ 323** | 1.0-3.5% |
| icm-demo | **≈ 138**, with one shared budget (option A, section 3.4) | 8.0% |
| vantage-demo | **≈ 90** at V1, **≈ 130** if `vantage-swing` is live before 11-02 | 8.5% / 5.2% |
| **Total** | **≈ 551 (V1) to 591 (V2)**, at least 520 either way | |

- If icm-demo stays on separate pots (option B, about 120), the total is ≈ 533 at V1.
- These are filled fills. Admissible fills still need step 0's void repair.

## 8. Amendment 2026-10-27 (b): quant conditions on 0439d7da (APPROVE WITH CONDITIONS), applied before any fill

Written before any code, history run or fill of any arm in this file. This amendment overrides the text above where
they differ.

1. **Z7 (Z7D, Z7V, Z7P): the timestamp convention.** τ is the 5m bar's **close** time. Dukascopy bars are labelled by
   their **open**, so history adds the bar length before any clock test. The live engine uses the same function.
   This is the convention the census used (bar close = `ts_utc` + 1 min on 1m).
2. **Controls.**
   - Borrowed S0 fills count as controls only when they fall **inside window W** and **pass the spread gate**
     (section 0).
   - Every control re-bracket (ZCD, ZCV, S0) is charged its **own signal-time cost**, the cost of the control's
     own fill, never the family's.
3. **K accounting: one registry row per id** (the precedent rule). Section 5's "families count once in the registry"
   is withdrawn. A family is the unit of the **read** only (section 4). Every arm id, controls and blocked twins
   included, is its own registry row and its own count in K.
4. **The m_eff run.** The 2010-17 `run_arm_backtest` that measures m_eff outputs the strata's **daily net-R
   correlation matrix only**. Means, t-statistics and any per-arm return summary are suppressed in its output and in
   its logs.
5. **The authority for the budget raise.** The 2026Q4 override goes **118 → 145**, for **27 ids**:
   - 25 from section 5;
   - Z7P and Z4P from amendment (a), c7c9ac8e.

   The authority is the owner's written directives:
   - 2026-10-18, "backtest as much as needed";
   - 2026-10-26, "tripple the numbers";
   - 2026-10-27, "make sure it trades 500 and plus trades a day";
   - 2026-10-27, "500 plus overall, 200 per account".

   The decision is the program director's (PD + PO), on the precedent of the waves. K is about 397. Each row is
   registered before its id's first fill.

## 9. Amendment 2026-10-27 (c): the control side draw uses its own key (implementation fix, before any fill)

Written before any code merged, any history run or any fill of ZCD or ZCV.

**Defect in the frozen text.** Section 1 gates a control entry with `hash_u(id, close) < p` (p 0.07 / 0.05) and draws
its side with `hash_side(id, close)`. `hash_side` is `+1 if u < 0.5`, on the **same** u. Every admitted entry has
u < p < 0.5, so as written every control entry would be **long**. That is a long-only null, not a random one.

**Fix.**
- The side is drawn from a separate hash with the key `f"{id}:side"`, i.e. `hash_side(f"{id}:side", close)`, using
  the section-0 hash of arms-2026-10-25.
- The entry gate keeps `hash_u(id, close)`.
- The two draws are therefore independent: the side is 50/50 whatever the gate admits.

**Unchanged:** ids, p, the bracket, W, the filters and the reading rule. This restores the stated intent ("random
side"), so it is an implementation fix, not a new hypothesis. K is unchanged.

**Test owed (implementer):** over a long synthetic run of admitted control entries, the long share lies within
0.5 ± 3 SE.

## 10. Amendment 2026-10-28 (d): quant conditions on the live volume fix and the room floor (before the 300-fill read)

Written on 2026-10-28, before any read of any family in this file. No return was read. It applies the quant
reviewer's conditions on the fix to Z1P's live volume and on ADR 0057.

### 10.1 A minute without its broker bar is not a volume decision (condition C1)

**What happened.** Live, the 1m/5m pass read the minute that had just closed from the engine's sampled quotes. That
broke section 0's "v = ... MT5 `tick_volume` live ... on the same feed", and Z1P made no candidate on 2026-10-27. The
fix: the pass reads the broker's M1 bar of that minute. It waits for it at most 3 s past close + 1.5 s, as a decision
close does.

**Rule.** When the bar misses that hold, the minute has no MT5 tick volume.
- A rule that reads tick volume is **not decided on the sampled count**. Such rules are `arm_rules.VOLUME_RULES`: Z1,
  Z2, Z5, Z8 here, plus S1's tick-weighted VWAP and S4's money flow. A test shows that every other shipped 1m/5m rule
  ignores `tick_count`.
- A candidate such a rule makes on that minute is journalled `exploration_skipped:broker_bar_late` and recorded
  blocked with its propensity, exactly as `spread_wide`. It is a refusal, not an untaken candidate. The history run
  has no such minute.
- Rules that do not read volume decide on the sampled minute, as before.
- **Reported:** the daily review gives the late rate per host (late minutes / the host's 1m bars) and the longest
  wait (`broker_bars` in the review record). Every waited or late minute is journalled
  `exploration_broker_bar_wait` with `hold_ms`.
- **Read.** The 300-fill read of Z1, Z2, Z5 and Z8 counts only candidates decided on a broker bar. Before this fix
  no Z1P/Z2P/Z5P/Z8P candidate was decided on the broker's volume. Their read therefore starts at the fix's deploy
  time on each host (taken from the release record). Earlier rows are reported as pre-fix and are not in the primary
  read.
