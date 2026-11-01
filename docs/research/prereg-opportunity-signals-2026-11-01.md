# Pre-registration: opportunity signals screen (opportunity researcher, 2026-11-01)

Frozen by commit before any 2010-2017 outcome of these rules is computed. Brief (research director via the orchestrator,
2026-11-01): four unscreened lines, at most 4 rules, costs from `config/cost_of_record.yaml`, matched random control,
one look each on 2010-2017, K counted, pass bar per `growth-sprint-2026-11-01.md` section 2. Results:
`opportunity-signals-screen-2026-11.md`. Code: `candidates/opportunity_signals/opp_screen.py`.

## 0. Lines declined before any data (K +0)

The discipline is "never re-test a retired family without new evidence". Two of the brief's lines were already measured
on these years against a control, and the brief brings no new mechanism or data source for them.

| Line | Prior measurement | Decision |
|---|---|---|
| (c) day-of-month, month end, COMEX options expiry | `time-structure-2026-10-26.md` 2.2 (slice B, 2010-2017): turn of month, month and quarter end p 0.17-0.99; OG expiry, FND and LTD "noise-shaped (signs reverse next day), no range effect"; listed under "Retired by this programme" in `hypotheses.md` | **declined.** A pinning mechanism (Ni, Pearson, Poteshman 2005, *JFE* 78(1), US equity options, gross *(unverified)*) needs open interest by strike, which we do not hold (CME OI history is paid: owner-only spend). Re-open only with that data |
| (b-ii) London-open "gap" | spot gold trades through the night, so the London-open gap is the 21:00-07:00 UTC Asia return. Fading it in London is the Asia-up/West-down drift (H-04, registry #44, stopped on cost) and the overnight/intraday tug of war (slice A: overnight +0.88 bp, t 0.6, no reversal); the failed Asia-range fade A-1 is closed (H-76) | **declined** (re-test of H-04/H-76) |

Three rules remain: **K +3**. Fewer looks keep the Bonferroni bar honest for the ones that matter.

## 1. Common (frozen)

| Item | Frozen value |
|---|---|
| Window | 2010-01-01..2017-12-31, `raw/xauusd_1m_dukascopy_2010..2017.parquet` (local, not committed). The loader asserts no 2018+ bar. Confirm 2018-01..2025-09 and holdout 2025-10..2026-09 untouched |
| Prices | 1m mid = (bid + ask) / 2. Decisions on completed bars only; fill at the next 1m open; a stop gapped through fills at that minute's open (worse). A minute touching both stop and target is a stop |
| ATR15 | ATR(14), simple mean of true range of 15m mid bars, last completed 15m bar at or before the decision |
| Cost | `cor-2026-10-31.2`, ICM cell of the entry session (asia < 07:00, london 07:00-12:30, newyork >= 12:30 UTC): **1.5 x round_trip_usd**, plus **1.5 x overshoot_usd on a stop exit**; long swap prior $0.60/oz per 22:00 UTC weekday rollover held (Wednesday x3); shorts 0. SB adds a reopen surcharge (section 3) |
| R | net USD/oz over the initial stop distance |
| Random twin | 4 per real trade, seeded `sha256(rule, key, i)`; window and mechanics per rule below; side a fair coin |
| Pass (each rule) | all of: (a) net mean R, day-block bootstrap (10,000, seed 20261101), 90% lower bound > 0; (b) real minus mean-twin R per day, one-sided bootstrap p, **Bonferroni over 3** (3p < 0.10); (c) >= 5 of 8 calendar years with net mean R > 0. Else FAIL. n < 100 reads **COUNT** (no pass possible) |
| Reported | n, trades/week, gross and net mean R, sd, LB90, twin mean, real-minus-twin mean and 3p, years positive, per-year means; SD split by leg (descriptive, not extra looks) |
| Workers | `multiprocessing`, 3 processes (one per rule) |

## 2. SA: exhaustion reversion after an extreme 60-minute move outside news (line a)

Economic reason: a move far outside the clock-matched volatility, with no scheduled information, is more likely
inventory pressure than news; the liquidity provider who absorbs it earns a reversal (Grossman and Miller 1988, *JF*
43(3), theory; Nagel 2012, *RFS* 25(7):2005-2039, https://www.nber.org/papers/w17653, US equities 1998-2010, daily,
gross: reversal returns rise with VIX). Gold: overreaction followed by *inertia*, not reversal, on daily data (Caporale,
Gil-Alana, Plastun, "Short-term price overreactions: identification, testing, exploitation", *Computational Economics*,
2017/2018, https://bura.brunel.ac.uk/handle/2438/13887, FX and commodities incl. gold, daily, gross; *sample period
unverified*). Single-paper evidence, none intraday on gold, one paper with the opposite sign: the prior is weak.
Distinct from R-01 (unconditional 15m RSI/Bollinger/VWAP fades) and Z2 (1m, 3-bar run): it fires only on clock-adjusted
3-sigma hours with news excluded, and has a structure stop.

- Decision at every 15m close t, Monday 00:00 .. Friday 20:45 UTC; none at 20:45-23:59 UTC; none in the first 120
  minutes after a reopen (bar gap >= 6 h).
- r60 = mid close at t minus mid close at t - 60 min (last bar at or before each; >= 50 bars in the window).
- sigma60(t) = RMS of r60 at the same UTC clock time on the prior 20 weekdays that have it (>= 15 needed).
- Signal: |r60| >= 3 x sigma60, and no `calendar-v1` tier-1 or tier-2 scheduled event in [t - 75 min, t + 15 min].
- Side = -sign(r60). Entry at the next 1m open.
- Stop = the window extreme (max high for an up move, min low for a down move, over the 60 bars) beyond by
  0.25 x ATR15; R = |stop - entry|, floored at 0.5 x ATR15.
- Target = the window VWAP: tick-count-weighted (h + l + c) / 3 of the window's 1m bars (equal weights if all counts
  are 0). Skip if the target is less than 0.25 R on the profit side.
- Exit at stop, target or the last 1m close before entry + 240 min. One position at a time.
- Twin: a uniform allowed 15m close within +/- 180 min of t on the same UTC day, fair-coin side, the real trade's stop
  and target distances in USD, same 240-minute time exit.

## 3. SB: weekly-open gap fade (line b-i)

Economic reason: weekend information is priced first in the thinnest book of the week; forced liquidation into it
overshoots and depth arriving with Tokyo/Shanghai refills it (9 Aug 2021: about -$80 at the Asian open, mostly
recovered before Europe; `xauusd-trader-playbook.md` 2.5, practitioner). Peer-reviewed overnight/intraday evidence is
equity-only and cross-sectional (Lou, Polk, Skouras 2019, *JFE* 134(1)); no peer-reviewed test of gold weekend-gap
direction was found. Distinct from R-08 (weekday/weekend return effects, no conditioning on the gap).

- Reopen = the first 1m bar after a bar gap of >= 24 h whose UTC weekday is Sunday. F = the last mid close before it.
- Decision at reopen + 30 min: P = mid close of the last bar at or before it. g = P - F.
- ATR20d = mean true range of the prior 20 completed 22:00-22:00 UTC trading days.
- Signal: |g| >= 0.20 x ATR20d. Side = -sign(g). Entry at the next 1m open.
- Target = F (full gap fill). R = max(|F - entry|, 0.20 x ATR20d) (1:1 geometry). Skip if the entry has already
  crossed F. Time exit: last 1m close before Monday 07:00 UTC.
- **Reopen surcharge** (the cost of record has no reopen fills): + max(0, Dukascopy `spread_mean` at the entry minute -
  the median `spread_mean` of 00:00-07:00 UTC minutes of that calendar year), once, on entry.
- Twin: a uniform minute in [reopen + 30 min, Monday 05:00 UTC], fair-coin side, the real trade's R and target
  distances, same time exit and surcharge rule at its own minute.
- **Power flag:** about 415 weekends; expect 100-200 signals. Like C1, a pass here is informational: the confirm bar
  (>= 200 trades) is out of reach in 2018-2025, so a pass buys a forward-only demo arm proposal, not a confirm.

## 4. SD: volatility-regime switch, 1h (line d)

Economic reason: trend persistence in futures (Moskowitz, Ooi, Pedersen 2012, *JFE* 104(2), 58 futures 1985-2009,
monthly, gross; strongest in crises per Hurst, Ooi, Pedersen 2017, *JPM* 44(1) *(volume unverified)*); range trading in
quiet states. **The prior is against the reversion half**: Nagel 2012 finds reversal pays most when volatility is high,
the opposite assignment. In-house: F-1/H-70, a Hurst-switched 15m band rule (t 5.2, discovery only), and slice H
("correlation structure stable across vol regimes, only the Sharpe level moves"). The rule is screened as the brief
states it; no swapped-regime look is taken (that would be K +1).

- Trading day = 22:00-22:00 UTC. RV_d = sum of squared 5m mid log returns in the day. RV5 = mean of the last 5
  completed days. rank = share of the prior 250 completed days' RV5 values <= today's (>= 200 needed). Known at the
  day's 22:00 start, held for the whole day.
- 1h mid bars on UTC hours (>= 30 1m bars, else skipped); decisions at hour closes, none at the 21:00 and 22:00 UTC
  closes.
- **High (rank >= 2/3), trend:** close > max high of the prior 20 1h bars -> long; close < min low -> short. Stop
  1.5 x ATR(14) 1h; no target; time exit after 24 1h bars.
- **Low (rank <= 1/3), reversion:** close > SMA20 + 2 x sd20 (closes) -> short; close < SMA20 - 2 x sd20 -> long.
  Target = SMA20 at the decision; stop 1.5 x ATR(14) 1h; time exit after 12 1h bars. Skip if the target is < 0.25 R
  away.
- Middle tercile: flat. One position at a time. Entry at the next 1m open.
- Twin: a uniform allowed 1h close on the same trading day, fair-coin side, the real leg's mechanics (trend: same R,
  24 bars; reversion: same R and target distance, 12 bars).

## 5. Reading rule (written before data)

A rule passes only on (a), (b) and (c) with n >= 100. A pass earns a request to the research director for one confirm
on 2018-01..2025-09 (SA, SD; DSR bar at t about 4.0 at the registry K, >= 200 trades) or a forward-only demo arm
proposal with its control (SB), nothing else; no parameter is tuned on this read. A FAIL retires the rule as specified
(listed in `hypotheses.md`); neighbouring thresholds are not re-tried without a new mechanism. Nothing here changes
sizing, RiskGate or the holdout.

## 6. Multiple testing and trial cost

K base: ~3,020 (orchestrator brief, 2026-11-01; 3,009 after the growth screens per `prereg-growth-2026-11-01.md`, plus
the GS1/GS1C arms and lanes running in parallel). This screen: **K +3** (SA, SB, SD). Declined lines: K +0. Registry
trials: 0 (explore screen). Gates touched: none.
