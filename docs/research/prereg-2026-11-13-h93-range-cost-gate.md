# Pre-registration 2026-11-13: H-93, range-to-cost gate on the 11 pooled trend rules

Status: **registered before any outcome under this gate is computed** (research director, 2026-11-13). No data was
read to write this card. No runner exists yet (section 8), so nothing runs in this change; nothing in the engine,
RiskGate, caps, sizing or `config/` changes. Family `range-cost-gate:2026-11`, unit H-93. Source: improvement A of
`ml-review-2026-11-13.md` (branch `research/ml-review-2026-11-13`). Owner: ml-researcher. Kill date: **2026-11-27**.

## 0. Disclosure: what is already seen, and the prior

| seen | window | result | status for this card |
|---|---|---|---|
| Sprint-200 trend lane, per-rule aggregates (`sprint200/trend-results.md`) | explore 2010-17; rule 03 confirm | 11 rules selected on explore **gross** t >= 2; rule 03 confirm net +0.070 R, t 0.99 | 2010-17 seen as aggregates per rule; the gate split is not |
| MLNET (`mlprob/mlnet-results.md`) | explore OOS 2012-17; confirm 2018-01..2025-09 | pool net +0.014 R (n 6,563) explore, **-0.037 R (n 8,723) confirm**; V3 confirm gross +0.071 R vs cost 0.083 R | **2018-25.09 is CONFIRM-SEEN for this exact pool**: contaminated |
| H-16 (`opportunity-days-screen-2026-11.md`) | 2010-17 | range is forecastable; the +0.20 rho gain came from GVZ and tier-1 counts on an ATR-relative target | GVZ/events retired as range inputs; not used here |
| R-17 (H-17 on HAR-high days) | 2010-17 | net -0.35 R | evidence **against** "wide days pay trend" |
| TEST-OLD 2008-09 for these 11 rules | none | MLNET "TEST-OLD not run"; trend lane never read it; H-92's 2008-09 leg was declined, not run | **clean**: the one confirm window |

**Prior: 10-15 %.** The cost channel alone cannot reach +0.05 R: at bps-of-price costs (W2) and ATR stops, cost is
about 0.02-0.05 R a trade, so even a gate that halved it saves about 0.01-0.025 R. A pass needs gross trend R to be
higher on forecast-wide days, which R-17 argues against. The value is a decisive close on the ML review's last idea.

## 1. Hypothesis and mechanism

**H-93:** trades of the 11 pooled trend rules taken only when the day's forecast range divided by the round-trip
cost of the decision's session is in the top half of its trailing year earn at least +0.05 R a trade more, net of
cost, than the same rules ungated.

Mechanism. Volatility clusters and is forecastable (Corsi 2009, *J. Financial Econometrics* 7(2), HAR, widely
replicated). Cost is fixed per oz per session while the move a trend trade can capture scales with range, so cost as
a share of the move falls on wide days (Carver's speed limit, practitioner). The gate does not predict direction;
the rules do. Gold-specific evidence that trend gross rises with forecast range: **none** (R-17 is against).

## 2. The pool (unchanged rule code: `scripts/mlprob/mlnet_engine.py`, the trend lane's signals, stops and exits)

| id | rule | bars | stop (all volatility-scaled; none in fixed dollars) | exit |
|---|---|---|---|---|
| 01 | H4-DON30-TR | 4h | 2 ATR4 | 3 ATR4 chandelier, cap 60 bars |
| 03 | H1-DON48-D1 | 1h | 2 ATRh | 3 ATRh trail, cap 48 |
| 04 | H1-DON24-ATRX | 1h | 1.5 ATRh | 2.5 ATRh trail, cap 24 |
| 10 | SGE-OR | 15m | opposite OR side, R in [0.5, 4] ATRh | 06:45 UTC |
| 15 | H4-BBW | 4h | 1.5 ATR4 | 2.5 ATR4 trail, cap 30 |
| 19 | H1-SHOCK-CONT | 1h | bar midpoint, R in [0.5, 3] ATR(1h,50) | 2 R target, cap 8 |
| 20 | ITS-AL2NY | 1m decision | 0.5 ATRd | flat at session end |
| 24 | MTF-EMA-1H | 1h | 1.5 ATRh | 3 R target, cap 24 |
| 28 | ADX-ONSET-4H | 4h | 2 ATR4 | 3 ATR4 trail, cap 30 |
| 31 | PB-4H-EMA20 | 4h | 1.5 ATR4 | 2 R target, cap 18 |
| 32 | RSI2-1H | 1h | 2 ATRh | close beyond SMA5(1h), cap 10 |

Deviation from the brief, disclosed: holds are hours for 03, 04, 10, 19, 20, 24, 32 but up to days for the 4h rules
(01, 15, 28, 31). Caps are **not** shortened: changed caps would be new rules with no parity to the seen pool.
Overnight financing is charged (section 3).

Two conditions apply to **both** gated and ungated arms, so the gate is the only difference:
- **Tier-1 flat:** tier-1 = `goldbot/data/econ_calendar.py` TIER1_PATTERNS (CPI, NFP, FOMC statement/presser, PCE).
  No entry T-60..T+30 min; an open position closes at market at T-5 min and pays the full round trip.
- **Cost floor:** skip a trade whose 1 R stop is < 3 x (session round trip + overshoot floor), i.e. cost > 0.33 R
  (target >= 3x cost). Expected to bind on about 0 % of 2010-17 trades; the bind rate is reported.

## 3. Costs (cost of record `cor-2026-10-31.2`, per broker by session)

Gate denominator and trade charge: the **worse broker's** session cell, re-derived from `config/cost_of_record.yaml`:
asia 0.204 (ICM; Vantage 0.158), london **0.293** (Vantage; ICM 0.164), newyork 0.263 USD/oz round trip. Hours from
the NY close to the Tokyo open are asia (`gold_session`, BACKLOG 146). History converts each cell to bps of price at
a $4,000 reference (W2). Overshoot max($0.12, ci90 upper) on stop and trail exits, bps-scaled. Financing: W3.3
point-in-time swap model (`amendment-w3.md`). Stress: overshoot $0.70 (reported). The cost is per session, not per
hour: hourly cells do not exist at a usable n.

## 4. The gate (frozen)

- **Forecast:** HAR on the daily high-low range in bps of price: OLS of ln range(d) on ln range(d-1), mean ln range
  over d-5..d-1 and d-22..d-1. Range in bps, not ATR-relative: the level of volatility is what sets range-to-cost.
  Inputs are Dukascopy 1m mid bars aggregated to UTC days; joined to each trade with `asof_join` on `available_utc`
  = the previous day's close; F(d) is fixed before day d's first decision.
- **Fit:** annual, expanding. Prediction year Y uses coefficients fit on LEARN-OLD 2003-01..2005-12 plus explore
  days 2010-01-01..(Y-1)-12-31. 2006-09 days are never fit rows (TEST-OLD); their ranges serve only as lagged inputs
  for early 2010. TEST-OLD uses coefficients fit once on 2003-05 + 2010-17, frozen.
- **Ratio and cut:** ratio(t) = F(d) / RT_bps(session of t). Gate open iff F(d) >= M(d) x RT(session) / RT_mean,
  where M(d) is the median of F over the previous 250 trading days and RT_mean the mean of the three session cells.
  London therefore needs a forecast about 1.16x the median, New York 1.04x, Asia 0.81x. No other cut is computed.

## 5. Reading rule, explore 2010-01-01..2017-12-31 (one run)

Uplift U = gated net mean R - ungated net mean R (ungated = all pool trades under section 2). t = day-clustered t
of (gate-open mean - gate-closed mean). **Continue to TEST-OLD only if all hold:**
1. U >= +0.05 R and t >= 2.0;
2. U > 0 in >= 5 of the 8 years 2010-2017;
3. gated net mean R > 0 and gated gross >= 3 x gated mean cost R (speed limit);
4. U beats the matched random gate: 1,000 placebo gates, each opening random whole days within every
   year x session x side cell to match the real gate's trade count there; real U above the placebo 95th percentile;
5. gated excess over the leak-free random twins (4 per trade, same session instance, same side, same exits) >= 0;
6. short-side U >= 0.

Any leg fails: **H-93 retired.** Reported, not gating: U per rule, per session, per side, per year; the cost-floor
and tier-1 bind rates; stress net; gate-open share (expected about 0.5).

## 6. One look at TEST-OLD 2008-01-01..2009-12-31 (only if section 5 passes)

2018-25.09 is contaminated (MLNET read the whole pool there), so the single confirm is 2008-09. Warm-up from
2007-01. Pass: gated n >= 300; gated net > 0; U >= +0.025 R with one-sided day-block bootstrap p <= 0.10; gated
excess over twins >= 0; U > 0 in both years. DSR at program K reported, not gating. The minimum detectable U at 80 %
power is computed from the explore SD and written into the run log **before** the read. 2008 H2 is a crisis regime;
the trailing-median cut keeps the gate open about half the time anyway.

## 7. What justifies a demo arm

Sections 5 and 6 both pass. Then one demo learning arm, RCG1 (the 11 triggers, entries only when the live gate is
open, live cost = the arm's own broker's session cell), with its matched random control by session and side, at the
existing minimum-lot sizing inside RiskGate and caps. Kill: at 100 fills, net excess over control < -0.10 R or live
cost > 1.5x the cell; at 300 fills, excess t < 1 -> off that day. Never real-money evidence on its own; sizing and
going live stay owner decisions.

## 8. Card, budget and kill (rules 2-4)

| item | value |
|---|---|
| expected gross edge, gated | +0.08 to +0.15 R a trade if H-93 holds (pool gross about +0.05-0.10 R plus U) |
| measured cost a trade | 0.02-0.05 R: round trip $0.20-0.29/oz on a ~$32 stop (2 ATRh today) = 0.006-0.009 R, overshoot ~0.004 R, long swap ~0.019 R a night |
| gross / cost | >= 2x on the expected gross: meets rule 2 |
| trades a day | pool about 4.3 (6,563 / 6 explore years); gated about 2.2 |
| net £/day at demo size | min lot (1 R ~ £25): 2.2 x 0.05 R x £25 ~ **£3/day**; not the £50/day route |
| K | **+1** at explore; the TEST-OLD look adds +1 only if reached (program convention, H-91) |
| budget | runner build + self-test (shuffled-gate U ~ 0; parity: ungated pool before sections 2-3 reproduces the trend lane's per-rule n and net exactly) about 1 agent-day; one explore run; one TEST-OLD run |
| runner | **none exists**: needs HAR-in-bps, session cost join, tier-1 flat and placebo gates over the `mlnet_pool.py` pool; committed with its self-test before any run |
| kill | any explore leg fails, or no explore read by 2026-11-27 -> closed. Not re-run with another cut (tercile, quintile, absolute threshold), forecast (GVZ, H-16 score), pool or cost basis |

## 9. Direction

Stop: new entry rules on 1m-4h price bars, generators, sprints and learned selectors over them. Five weeks of
honest screens put gross edge within about 0.03 R of zero, with cost as the whole result; more volume repeats it.
Stop the bandit and the dormant retrains. Keep: the per-broker session cost of record, matched controls, kill rules,
the sealed holdout, the demo as cost truth. H-93 is a cheap, low-prior close, not the bet.

The single bet: **the owner's own trades**, the only record here of money made on gold. Get his fills (entry, exit,
size, timeframe) and score them net of his costs against a random control matched by day, session and side. If
his excess is real, encode what explains it (box break+close + MFI, his exits, his days) and test it on TEST-OLD.
If not, tell him plainly it is within luck. Lower cost (venue, swap-free) is the other lever: owner-only.
