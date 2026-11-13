# Pre-registration 2026-11-13: H-92 (line HD-1), pooled gold trend held for hours to days

Status: **registered before any outcome under this rule is computed** (principal quant strategist, 2026-11-13). No
trial runs in this change, and nothing in the engine, RiskGate, caps, sizing or `config/` changes. Family
`trend-hd:2026-11`, unit H-92, **K +4** (section 9). Plan line 3 of `algo-systems-review-2026-11-13.md`.

## 0. Disclosure: what is already seen, and the prior

This is **one** pooled line, not a set of arms. Its signal family has already been read on every window except
TEST-OLD and the future. This card exists for three reasons:

1. To give the survey's top recommendation (slow, pooled, cost-budgeted trend at hours-to-days holds, flat through
   tier-1 news, regime-tested) one honest, final test on gold alone.
2. To apply the new cost-budget gate (`cost-budget-audit-2026-11-13.md`) to it.
3. To stop it from being re-proposed if it fails.

What has been seen:

| source | window | result | status of the window for this signal |
|---|---|---|---|
| R-06, registry #16 (tsmom 1h, these exact defaults) | 2010-01..2025-09 | gross **+0.061 R**, t 2.63, n 3,764, SD 1.42 R; net -0.079 R at the 10-18 cost priors (0.37 $/oz + bar spread + -60 $/lot/night long swap) | LEARN and CONFIRM both **seen** |
| R-06, #17 (tsmom 4h) | 2010-01..2025-09 | gross +0.062 R, t 2.37, n 2,275 | seen |
| #36 (tsmom 1h, min_score 1.0) | same | gross +0.084 R, t 2.43, n 1,691 | seen. **Not chosen**: picking the better neighbour after the fact would be selection |
| sprint-3000 momentum / trend families, 1h-4h (`sprint3000/results-2026-11-02.md`) | explore 2010-17; confirm 2018-01..2025-09 | explore median net +0.134 R (C1 cost 0.014 R); confirm median net **+0.020 R**, 72 % of 318 singles positive, momentum ensemble t 1.18 (p 0.12); 0/328 pass BH-FDR | 2018-25 is **CONFIRM-SEEN** program-wide |
| forward demo, 2026-10-26..11-13 (`cost-budget-audit-2026-11-13.md`) | 14 days | the only running gold arms whose gross clears the speed limit are trend-agreement rules at 5m (Z10D three-horizon, Z9D 1h-trend reclaim); post hoc, n < 60 | not evidence; disclosed as motivation only |

Why this is not a re-test of a retired idea without new evidence (R-06, R-10): both stated reasons have changed.

- R-06's net sign came from cost priors 3-5x today's measured cost of record. W2 (2026-11-02) moved history costs to
  bps of price with measured swap: dollar costs at $400-1,300 gold were 4-5x too harsh.
- R-10's reason was the DSR bar at t about 4.1. W2 and eval2 replaced it with a fresh-window test plus a forward read.
- New since both: the cost-budget gate itself, the tier-1 flat rule and the regime and short-side tests. They make
  this a different, stricter line than R-06.

What has *not* changed is the evidence that the edge is small.

**Prior: low.**

- In 2018-25 the premium shrank to about +0.02 R net a trade (sprint-3000).
- At that size the speed limit is likely to bind (section 4).
- Expected outcome: **retired at leg C on the speed limit or the 2019-21 regime test**, with a probability of about
  0.6. P(passes every leg) is about 0.05-0.10.
- The value of the card is a decisive, recorded closure or a surprise, at a cost of K +4 and no money.

## 1. Hypothesis and economic reason

**H-92:** a single pooled trend forecast on gold, read on 1h bars and held for hours to two days, has these properties:

- a gross edge at least 3x its full cost (round trip, stop overshoot, financing), net of cost-of-record costs and
  measured-model swap;
- an excess over its matched random-side control of at least 0;
- a positive net in 2019-2021 and on the short side;
- a positive net on the one fresh history window (TEST-OLD 2008-09).

Mechanism:

- **Robust, multi-market.** Time-series momentum.
  - Moskowitz, Ooi & Pedersen 2012, *JFE* 104(2): 58 futures 1965-2009, 1-12-month lookbacks, gross of costs.
  - Hurst, Ooi & Pedersen 2017, *J. Portfolio Mgmt* 44(1): 67 markets 1880-2016, with estimated costs.
- **Mechanism:** slow diffusion of information and hedging and flow pressure. Short-horizon (1-20 day) trend in
  metals is weaker and less replicated.
- **Against, single-paper:** Kurth et al. 2026, as cited in R-03 *(unverified here)*: small-tick contracts lost short-term trend after 2009.
- **Gold-specific support:** none peer-reviewed at 1-48 h holds net of CFD costs (`state-of-the-art.md` B-section).
- This card tests the practitioner claim (Carver 2015, *Systematic Trading*; AQR managed futures). A pooled, slow,
  cost-budgeted forecast survives where single fast rules do not. That claim is single-source for gold
  *(practitioner, unverified for XAUUSD CFDs)*.

## 2. The line (frozen at this commit)

| item | value |
|---|---|
| instrument | XAUUSD (ICM demo for F; Dukascopy 1m mid bars for history). No other instrument (owner decision) |
| signal | the existing `tsmom` specialist (`goldbot/specialists/time_series_momentum.py`), **defaults unchanged** (registry #16): score = mean of the vol-scaled trailing log returns over 24, 120 and 480 h, each / (96 h SD of hourly returns x sqrt(horizon)). Long if score >= +0.5, short if <= -0.5. One combined forecast: the "pooling" is inside the score, not across arms |
| decision bars | 1h, firing at most once per 4 h (`schedule_h` 4, closes on multiples of 4 UTC hours). One position at a time, long and short. **No 15m or 4h variant**: 4h is the same forecast sampled more coarsely (no new information, K +0), and the specialist has no 15m form |
| stop | 1.5 x ATR14(1h) at the signal bar, fixed for the trade = **1 R** |
| target | 3.0 x ATR14(1h) = **2 R** |
| time exit | 48 1h bars (2 trading days). No weekend hold: the engine's `crosses_friday` refusal applies forward, and the history walk exits at the Friday close |
| tier-1 flat | tier-1 = `risk.blackout.events` (CPI, NFP, FOMC statement and press conference, PCE; `goldbot/data/econ_calendar.py` TIER1_PATTERNS). **No entry from T-60 min to T+30 min.** An open position is **closed at market at T-5 min**. The exit pays the full round trip like any exit |
| sizing | fixed risk per trade: 1 R = the demo's fixed risk unit at 0.01-lot granularity. Never increased after losses. No pyramiding |
| target vs cost | at today's ATR14(1h) of about $16 (VEX1H/IEX1H stops $24 = 1.5 ATR), the target is about $48, which is **about 200x** the $0.23 round trip. In 2010-17 (ATR(1h) about $4-6 at $1,250) it is about 170x the bps-scaled round trip. The 3-5x target-vs-cost requirement holds by a wide margin. **The binding constraint is gross edge vs cost** (section 4) |

Code status:

- **The forward leg needs one engine change.** A pre-release hard-flat exit for this arm (implementer, then
  trading-safety review). It also needs two `config/exploration_arms.yaml` rows: HD1 (rule `tsmom`, timeframe 1h,
  #16 config) and HD1C (rule `random_bracket`, 1h, `minutes: [0]`, on the 4-hourly schedule, `stop_atr 1.5`,
  `target_r 2.0`, `hold_bars 48`, random side, the same tier-1 flat).
- **The history legs need new code** (answer to the brief):
  - `scripts/run_arm_backtest.py` can replay rule `tsmom` 1h with the #16 config from a config row today. It writes
    per-trade `gross_r`, side, entry and exit. But it has **no tier-1 hard-flat exit**, **no random-side twin**, and
    charges the frozen $0.23 plus the shipped swap prior, not bps plus point-in-time swap.
  - The sprint-3000 engine has the C1 bps costs, the random-side twin and the one-read registry, but it has no `tsmom`
    forecast and no tier-1 flat.
  - **So the LEARN leg of this line cannot run without new code.**
  - The no-news form *could* run today through `run_arm_backtest.py`. It must **not** be run. That would be an
    unregistered look at a different rule.
  - Smallest build: the tier-1 hard-flat in the 1m barrier walk; the sprint-3000 `c1` cost function with point-in-time
    DTB3 swap; the sprint-3000 random-side twin. One lane, lookahead test included.

## 3. Windows and legs (research windows W2; ADR research-windows-w2)

| leg | window (entry time, UTC) | role | read |
|---|---|---|---|
| **L** | 2010-01-01 .. 2018-01-01 | **kill-only** (seen by R-06 and sprint-3000) | once, under this rule |
| **C** | 2018-01-01 .. 2025-10-01 | **kill-only regime and cost test** (CONFIRM-SEEN). Reported with and without 2025 | once, after L passes |
| **T** | TEST-OLD intraday: 2008-01-01 .. 2010-01-01 (2006-07 fail the intraday data-quality check, 2026-11-10 note) | **the decisive history leg**: the only window the signal has not been read on | once, only after L and C pass (W2: TEST-OLD only for confirm-passed rules; quant to rule that passing C's kill gates counts) |
| **F** | demo fills from the day after the engine change, both registry rows and the quant sign-off all exist | cost verification, futility and the demo read | fixed checkpoints (section 6) |

- **Not read:** learn_old 2003-05 (intraday bars fail data quality; daily trend there was read by POS-1) and the
  embargo.
- **The holdout 2025-10-01..2026-09-30 is sealed.** No history leg reads an entry on or after 2025-10-01. Warm-up may
  read earlier bars only. Exits of trades entered before 2025-10-01 may not cross it: the trade is cut at the last bar
  before it and flagged.
- **Calendar dependency:** T needs tier-1 times for 2008-09. Raw-calendar coverage before 2010 is not verified
  (data-engineer). If it is missing, **T waits for the backfill**. It is never read without the tier-1 rule.

## 4. Costs and the speed limit

Pinned: `cor-2026-10-31.2`, ICM, charged per ADR 0064, in bps of the entry price at the $4,000 reference.

| component | charge |
|---|---|
| round trip | $0.23 = **0.575 bp** of entry price, every trade, including tier-1 flat exits |
| stop overshoot | $0.11 = **0.275 bp** on stop exits only |
| swap (primary) | per rollover held, Wednesday triple, W3.3 model: long pays (DTB3 + 0.74) %/yr, short receives (DTB3 - 0.74) %/yr (a negative value is paid). DTB3 is point-in-time (`asof_join` on `available_utc`). The markups were measured 2026-11-02 and match this audit's fills (long -4.8, short +3.3 %/yr at DTB3 4.0) |
| swap (short-side gate) | the short credit is capped at 0, so the short-side test cannot be passed on carry alone |
| stress (reported, not gated) | C1 dollar line: $0.23 + $0.70 overshoot, shipped swap -60 $/lot/night long |

**Speed limit (gate in L, C, T and F):** mean cost R per trade <= mean gross R per trade / 3, with gross > 0.

Ex-ante arithmetic (not a result). A 1.5 ATR(1h) stop is about 37 bp of price. The round trip costs about 0.015 R and
overshoot about 0.003 R. Swap costs about 0.006 R in 2010-17 (rates near 0) and about 0.010 R net of the side mix in
2018-25. That gives a total cost of about 0.024-0.028 R, so the **gross must be >= about 0.075-0.085 R**. R-06's
2010-25 gross was +0.061 R, so the gate is likely to bind in C.

## 5. Statistics

- Per trade: gross R (mid-to-mid on 1m bars, entry at the next 1m open after the decision close, stop first within a
  minute), cost R, net R.
- **t is on day-summed net R** (W2), one-sided, opportunity-day clusters at the 22:00 UTC boundary.
- **Matched random control:**
  - **History:** the sprint-3000 C1 random-side twin. Same decision minute, same stop, target, time exit and tier-1
    flat, the opposite side at its exact expectation. Excess = (real - opposite) / 2.
  - Also reported: the drift-adjusted gross (side x in-window drift x hold subtracted).
  - **Forward:** HD1C, scored the same way per checkpoint.
- **Years:** calendar years by entry. 2025 is truncated at 2025-09-30.

## 6. Reading rule (decided now; the evaluator applies it, the research director signs)

Run in order. Any FAIL **retires H-92** (section 8). There is no re-run with other parameters.

**L (kill-only), all required:**

- net > 0;
- the speed limit holds;
- random-side excess >= 0;
- drift-adjusted gross > 0;
- >= 5 of 8 years net > 0;
- short-side net > 0 with the short credit capped at 0.

**C (kill-only: regime and cost), all required:**

- net > 0, both with and without 2025;
- the speed limit holds;
- **2019-01-01..2021-12-31 net > 0** (the regime test: a two-way market, one bull and one bear year);
- **short-side net > 0** with the credit capped at 0;
- random-side excess >= 0;
- >= 4 of 8 calendar years net > 0.

Passing C is not evidence of an edge: the window is seen.

**T (decisive history), all required for history PASS:**

- n >= 100;
- net > 0;
- day-t >= 1.28 (W2 TEST-OLD rule);
- the speed limit holds;
- random-side excess >= 0.

The short side is reported only: 2008-09 is too short to require it.

**F (forward demo, HD1 vs HD1C), at fixed checkpoints and never between them:**

| checkpoint | estimated date at about 0.9 fills a trading day | rule |
|---|---|---|
| 100 HD1 fills | about 5 months after start | **Cost verification.** If measured cost R per trade (broker-net vs mid-to-mid) is above 1.5x the pinned cost, the line is **stopped** as cost-infeasible. **Futility:** the one-sided 90 % upper bound of the excess over HD1C below 0 -> stopped |
| 300 HD1 fills | about 15 months | excess t >= 1.0 and net > 0 and the speed limit holds -> **CONTINUE**. Otherwise **FAIL** |
| 1,000 HD1 fills | about 4 years | excess t >= 2.0, net > 0, the speed limit holds -> **PASS** (demo volume only) |

Kill date for F: the 300-fill read, or 2027-12-31, whichever comes first. After that, F continues only by amendment.

**Verdicts:**

- **PASS** = L, C and T pass, and F reaches PASS. It is a demo-volume and qualification proposal only, never
  real-money evidence. The live gate (DSR >= 0.95 at full registry K) and any live use stay **owner decisions**.
- **LEAD** = L, C and T pass and F is at CONTINUE. The line keeps running on demo inside today's caps.
- **FAIL** = any leg's gate fails, or F futility. Retired.

## 7. Power (honest)

Assumptions: SD 1.42 R a trade (#16), about 235 trades a year, about one a day, so the day-cluster design effect is
about 1.

| leg | n | SE of mean R | net needed for 80 % power at the leg's bar | power if true net = +0.04 R | power if +0.10 R |
|---|---|---|---|---|---|
| T (2008-09) | about 470 | 0.065 | 0.14 R (t 1.28) | about 25 % | about 60 % |
| F, 300 fills | 300 | 0.082 | 0.15 R (t 1.0) | about 30 % | about 60 % |
| F, 1,000 fills | 1,000 | 0.045 | 0.13 R (t 2.0) | about 13 % | about 59 % |

**At the edge the evidence suggests (0.02-0.06 R net), no fresh window can confirm this line.** The card decides on
three things: the kill gates in L and C (the regime and the speed limit), a sign-consistent T, and forward cost
verification. A CONTINUE at 300 fills means "not refuted, cost-feasible". It does not mean "proven".

## 8. What FAIL retires (recorded so it is not re-proposed)

A FAIL retires **gold-only trend at 1 h-2 day holds as a stand-alone line**, for:

- every tsmom 1h/4h configuration;
- every sprint-3000 momentum and trend single at 1h-4h on XAUUSD;
- any re-scoring of them under cheaper costs.

It extends R-06 and R-10. Re-opening needs new evidence: a different instrument set (an owner decision), a cost
change of at least 2x, or a new mechanism.

A FAIL at C on the speed limit also closes the survey's hours-to-days recommendation **for gold alone**. The only
open trend route is then a diversified multi-instrument book, which POS-1 A1 already found financing-bound at daily
holds.

## 9. K, budget, registry rows, ownership

- **K +4:** L +1, C +1, T +1, F +1 (a read is a read, whatever its weight). The diagnostics below add 0 because they
  cannot change the verdict.
- **Registry rows:**
  - H-92-L, H-92-C, H-92-T, H-92-F;
  - plus HD1C's arm row before its first fill (HD1's row is H-92-F).
  - **5 rows**, written by the research director at the quant sign-off and before any read.
- **Budget:** 2026Q4 `trial_budget_overrides` 3500, about 177 used on the local snapshot. The research director runs
  `check_budget(5)` on the brain registry. If the reads fall in 2027Q1 (budget 20, 8 reserved) and there is no
  headroom, **raising the budget is an owner decision**: flag it, never assume it.
- **Diagnostics (reported, K +0, never gating):**
  - the same line without the tier-1 flat (the cost of the flat rule);
  - the C1 dollar stress line;
  - long-only and short-only legs;
  - hold-time distribution and the share of tier-1 flat exits.
- **Owners:**
  - implementer: tier-1 hard flat (engine and history walk), C1 and point-in-time swap cost and twin wiring in the
    replay;
  - data-engineer: tier-1 calendar for 2008-09;
  - quant-reviewer: sign-off, plus the C-gate-as-confirm ruling (section 3);
  - evaluator: applies section 6;
  - research director: K rows and budget check.
- **Not in scope (K +0):** 15m or 4h variants, min_score, stop or target changes, macro gates (R-16), session filters
  (H-90), and other instruments. Each needs its own card.

## 10. Sign-off

- quant-reviewer: pending. The frozen text is this commit.
- trading-safety-reviewer: required before the engine change (pre-release hard flat) and before HD1/HD1C send a demo
  order.
- evaluator: applies section 6 at each leg.
