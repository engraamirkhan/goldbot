# Sprint-200 lane `meanrev`: mean reversion and levels, 33 strategies (pre-registration)

Principal market microstructure and execution researcher, 2026-11-02. Frozen by this commit **before any return,
trade or outcome of any strategy below is computed** on any window. Shared brief: owner directive 2026-11-02
("find 200 signal strategies, backtest on historical data and deploy the successful ones"); six lanes, this one owns
fades at VWAP bands, prior-day/week high-low rejections, round numbers, pivots, overextension, gap fills,
sweep-and-reclaim and N-bar exhaustion. Code: `scripts/sprint200/meanrev_screen.py` (committed after this file, before
the explore run; the evaluator re-runs it end to end).

## 0. Prior evidence and what this lane must not re-test

This lane sits in the most-retired territory of the program. Read before anything below:
- **R-01** mean_reversion 15m (RSI / Bollinger / VWAP fades): no gross edge. **R-09** pivots, Fibonacci, chart tools as
  standalone rules: never trialled, "no peer-reviewed support net of costs". **R-14** 15m shallow sweep-and-reclaim
  fade (#88): gross -0.028 R. **R-13** EQH/EQL magnets. **A17 Z2/Z4** 1m/5m run-exhaustion fade and round-number touch
  rejection: H-82 bounds the gross at +/-0.011 R. **MT-SRR** 1m prior-day pierce fade: net -0.29 R. **OS-SA** 3-sigma
  60-min move fade to window VWAP: net -0.197 R, its random twin did better. **OS-SB** Sunday-reopen gap fade at +30 min:
  n 90, net -0.016 R. **OS-SD** 1h Bollinger(20,2) fade in the low-RV tercile: reversion leg gross -0.079 R.
  **RS-1..3** ICT / supply-demand / IFVG; **GS-C4** NR7 break; **R-02/R-04/R-17** breakouts (the opposite side of a
  failed-break fade).
- What made all of them lose is the same thing: **a stop of $1-4 at a measured round trip of $0.23-0.27 plus a
  $0.12-0.20 stop overshoot costs 0.1-0.4 R a trade**, more than any gross edge ever measured at 1m-15m (H-82:
  |gross| <= 0.011 R on up to 168k entries).
- **How this lane differs, as a whole (the new part):** every strategy decides on a closed bar of 15m with a $2.50 stop
  floor or, mostly, 1h / 4h / 1d, so the median stop is several dollars to tens of dollars and cost falls to roughly
  0.02-0.10 R; the reversal is required to be *confirmed* by a close back inside (sweep-and-reclaim) or a reversal bar,
  never a blind touch fade; and every strategy faces the same matched random-entry control. Each row in section 3 says
  how it differs from its nearest retired neighbour. Three rows (01, 17, 28) are the closest neighbours of retired rules
  and are expected to fail; they are run because the owner's brief names the mechanism and because a FAIL at a wider
  stop closes the "it was only the cost" objection.

**Priors (honest):** 0-2 of 33 explore passers expected and fewer confirm passers. A lane result of 0 is the most likely
single outcome. The pass rule is fixed below and is not relaxed after the look.

## 1. Data, windows and point-in-time rules (common to all 33)

- Bars: `raw/xauusd_1m_dukascopy_<year>.parquet` read with `goldbot.data.release.read_bar_files` (error-flagged bars
  dropped, warnings kept); price = mid (bid + ask) / 2 on every OHLC field (the cost of record contains the spread, so
  it is never charged twice). Tick weight = the file's `tick_count`.
- **Explore 2010-01-01..2017-12-31** (no 2009 file: indicators warm up in early 2010). A trade is admitted only if its
  latest possible exit is before 2018-01-01 00:00 UTC, so explore never reads a 2018 price.
- **Confirm 2018-01-01..2025-09-30, once, explore-passers only** (the script refuses any id not recorded as a passer in
  the explore result file). Warm-up reads 2017. Bars are cut at 2025-10-01 00:00 UTC and the cut is asserted
  (`goldbot.research.arms.cut_at_holdout` / `assert_no_holdout_rows`); a trade whose latest possible exit is at or after
  that instant is not admitted. Nothing at or after 2025-10-01 is opened.
- **Server day** = New York + 7 h wall clock (`NY+7`, day boundary 17:00 New York). Daily bars are server days (Sunday's
  reopen minutes belong to Monday). Intraday bars 15m / 1h / 4h are bins of the server wall clock, labelled by their
  start, decided at their close.
- ATR_tf(14) = simple mean of the true range of the last 14 *completed* bars of that timeframe; ATRd = the same on
  server days. SMA / RSI(2, Wilder) / Bollinger on closes of completed bars only.
- **Decision and fill:** a signal is computed on a closed bar; entry at the OPEN of the first 1m bar at or after the
  bar's close. Daily-bar signals enter at the first 1m bar at or after **server 01:05** of the next server day (skips the
  reopen hour's spread). **No entry in the rollover window**: a signal whose entry minute falls in server
  [23:45, 01:05) is skipped.
- **Exits** on the 1m mid path from the entry minute: stop and target are checked per minute; if both are touched in one
  minute the stop wins; a stop is filled at the stop price or at that minute's open if the open is already beyond it
  (gap); a target is filled at the target price; otherwise the time exit at the close of the last 1m bar before the
  deadline. **Day-end** = server 23:45 of the entry's server day. "Cap N days" = server 23:45 of the N-th server day
  after the entry day (weekdays). **No position is held over a weekend**: every deadline is clipped to Friday server
  23:45. A "cap H h" exit is the close of the last 1m bar before entry + H hours, then clipped by any day-end / weekend
  rule the row states.
- **Stop distance floor $2.50**: stop distance = max(rule distance, $2.50) (the stop moves out, never in); never capped.
  R = that distance.
- **Level targets** (VWAP, SMA, pivot, prior close, midpoint) are frozen as a price at the decision; the signal is
  skipped if the target is on the wrong side or closer than 0.5 R.
- **One position at a time per strategy**: a signal whose entry minute is at or before the open trade's exit minute is
  dropped. Both sides (long and mirrored short) unless a row says otherwise.

## 2. Costs, control and the reading rule (common to all 33)

- **Costs** (ADR 0064, `config/cost_of_record.yaml` version **cor-2026-10-31.2**, pinned via
  `ArmCosts.require_version`): per trade, `goldbot.research.arms.screen_net_r(broker="icm", sessions=<entry minute
  label>)`: the session's charged round trip (Asia 0.272, London 0.230, New York 0.230; outside every session the
  dearest) and, on a stop exit, the session's overshoot charge (ICM clustered upper bound floored at $0.12: Asia 0.136,
  London 0.120, New York 0.196). The label follows the cost-of-record rule (`DEFAULT_SESSIONS.session_label`, "" when
  the market is closed). **Swap** for every rollover crossed: `rollover_nights` with `SwapSpec.shipped()` (long
  -$60/lot/night = -$0.60/oz, short 0, triple Wednesday). Net R = (side x (exit - entry) + swap - round trip -
  overshoot x stop_exit) / R. Stress line (reported, not a gate): overshoot $0.70.
- **Matched random-entry control:** for every admitted trade, 4 twins; each twin's entry minute is drawn uniformly
  (numpy PCG64, seed 20261102 + strategy number) from the 1m bars of the **same server day** whose cost session label
  equals the trade's, outside the rollover window, with the **same side, the same stop distance in $, the same target
  distance in $ (none if none), the same cap and the same day-end / weekend clipping** applied from the twin's own
  entry; costed exactly like the trade. Twins are not subject to one-at-a-time. Control mean = mean net R over all
  twins.
- **Explore PASS (all four):** n >= 100 trades; net mean R > 0; t >= 2.0 (t = mean / (sd / sqrt n) of per-trade net R,
  one position at a time); net mean R - control mean R >= +0.05 R.
- **Confirm PASS (all four), one look, 2018-01-01..2025-09-30:** net mean R > 0; t >= 2.0; deflated Sharpe > 0, read as
  the Bailey & Lopez de Prado (2014, Journal of Portfolio Management 40(5)) DSR probability > 0.5, i.e. the observed
  per-trade SR above the expected maximum SR of K unskilled trials (`goldbot.research.metrics.DsrInputs.of(net_r)
  .dsr(K)`), at **K = 3,229** (program ~3,029 + the sprint's 200) **+ every extra variant this lane runs (planned:
  0)**; the DSR is also reported against the program's usual 0.95 bar and at K = 4,000 so a reader can apply a stricter
  reading; same sign (net mean R > 0) in **>= 6 of the 8 confirm years** 2018..2025 (2025 = Jan-Sep; a year with no
  trade counts as not positive). Confirm-vs-control is reported, not gated.
- **No tuning after the look.** No threshold, stop, target, cap, window or filter in section 3 changes after any number
  is seen. A bug fix that changes trades is disclosed with both readings and the stricter one governs.
- **K accounting:** 33 explore trials (one per row) are this lane's share of the sprint's 200. A confirm run on a
  passer is that trial's second window (one registry trial per confirm). Any variant beyond section 3 is +1 each and
  named in the report.
- **Survivor (deploy-ready):** confirm PASS on all four legs. Survivors become exploration arms on demo (min lot,
  RiskGate, 1% cap, paired random control) per the brief; no survivor -> no code beyond the screen script.

## 3. The 33 strategies

Notation: one side is shown; the mirror (highs/lows swapped) is traded too. "x ATR" = ATR(14) of the row's timeframe
unless written ATRd. PDH/PDL = prior server day high/low; PWH/PWL = prior server week high/low; PMH/PML = prior
calendar month (by server day) high/low. "Reclaim" = the bar trades beyond the level and closes back on the near
side. VWAP and its sigma are tick-weighted over 1m mids since the anchor (sigma = sqrt of the tick-weighted variance
of 1m closes around VWAP); a VWAP band is used only once >= 60 minutes have accumulated since the anchor.

| id | tf | mechanism (why gold should pay it) | entry rule | stop | exit | differs from |
|---|---|---|---|---|---|---|
| S200-MEANREV-01 | 15m | Inventory dealers lean against intraday overshoots of the day's average price | Daily VWAP (anchor server 00:00): previous 15m close > VWAP + 2.5 sigma and this close back < VWAP + 2.5 sigma -> S | max high of the two bars + 0.1 ATR | target VWAP at decision; cap 4 h; day-end | R-01 (15m VWAP): tick-weighted sigma bands, re-entry confirmation, $2.50 floor. Closest neighbour, expected FAIL |
| S200-MEANREV-02 | 1h | Weekly value: multi-day extension from the week's VWAP reverts as positioning squares | Weekly VWAP (anchor first bar of the server week): prev 1h close > VWAP + 2 sigma, this close back inside -> S | max high two bars + 0.1 ATR | target weekly VWAP; cap 48 h; weekend clip | R-01 is a 15m daily anchor; OS-SA is a 60-min move to window VWAP |
| S200-MEANREV-03 | 15m | NY overshoots of the London price discovery revert once London liquidity re-engages | NY session only: London-anchored VWAP (anchor London open); prev 15m close > VWAP + 3 sigma, this close back inside -> S | max high two bars + 0.1 ATR | target London VWAP; cap 3 h; day-end | session anchor, cross-session; not tested before |
| S200-MEANREV-04 | 1h | The daily maintenance-break gap is noise from thin re-quoting and fills | At server 01:05: gap = mid at the 01:05 bar open - last close before server 23:45 of the prior server day; gap >= 0.3 ATR1h -> S (fade to that close) | 1.0 ATR1h beyond entry | target that close; cap 4 h | OS-SB was the weekend gap; this is the weekday daily break |
| S200-MEANREV-05 | 1h | Stops resting beyond yesterday's extreme are harvested, then price returns (stop-run) | First 1h bar of the day with high > PDH and close < PDH -> S | bar high + 0.1 ATR | 2 R; day-end | R-14 15m shallow sweep; MT-SRR 1m; this is 1h with a bar-scale stop |
| S200-MEANREV-06 | 4h | Same stop-run at a slower clock where the reclaim is more informative | 4h bar high > PDH and close < PDH (first per day per side) -> S | bar high + 0.1 ATR | 2 R; cap 2 days | as 05, 4h |
| S200-MEANREV-07 | 1h | Weekly extremes hold larger stop clusters; a reclaim traps breakout traders | First 1h reclaim of PWH in the week -> S | bar high + 0.1 ATR | 2 R; cap 3 days | weekly level, untested |
| S200-MEANREV-08 | 4h | As 07 at 4h | First 4h reclaim of PWH in the week -> S | bar high + 0.1 ATR | 3 R; cap 5 days | as 07 |
| S200-MEANREV-09 | 1h | Yesterday's extreme acts as resistance when approached but not broken | 1h high within 0.15 ATR below PDH without breaching it, close <= bar high - 0.5 ATR -> S (first per day per side) | PDH + 0.2 ATR | 1.5 R; day-end | touch-reject without a break (R-14 needed a sweep) |
| S200-MEANREV-10 | 1d | Turtle soup (Raschke & Connors 1995): failed 20-day breakouts trap trend followers | Day low < prior 20-day low (that low >= 4 days old) and day close > it -> L next day | day low - 0.1 ATRd | 2 R; cap 5 days | daily Donchian failure; R-04 tested the breakout side at 1h |
| S200-MEANREV-11 | 4h | Turtle soup at 4h | 4h bar low below the prior 20-bar low, close back above it -> L | bar low - 0.1 ATR | 2 R; cap 3 days | 4h |
| S200-MEANREV-12 | 1h | Turtle soup at 1h | 1h bar low below the prior 20-bar low, close back above it -> L | bar low - 0.1 ATR | 1.5 R; cap 8 h; day-end | 1h |
| S200-MEANREV-13 | 15m | NY sweeps of the London session extreme fail and revert toward the London midpoint | NY session: 15m bar high > London session high, close back below it -> S (first per day per side) | bar high + 0.1 ATR | target London midpoint; day-end | Asia-into-London sweep (#88) retired; London-into-NY is new |
| S200-MEANREV-14 | 4h | Monthly extremes are the largest stop clusters on the chart | 4h reclaim of PMH (first per month per side) -> S | bar high + 0.1 ATR | 2 R; cap 5 days | monthly level, untested |
| S200-MEANREV-15 | 1h | Limit orders and take-profits cluster at $50 handles (Osler 2003, FX); first rejection holds | 1h open < L, high > L, close < L - 0.1 ATR for L a multiple of $50 -> S | bar high + 0.1 ATR | 1.5 R; cap 8 h; day-end | Z4 was $1-scale stops at 1m/5m; G-7 magnet is the opposite claim |
| S200-MEANREV-16 | 4h | As 15 at $100 handles | 4h open < L, high > L, close < L - 0.1 ATR, L a multiple of $100 -> S | bar high + 0.1 ATR | 2 R; cap 3 days | as 15 |
| S200-MEANREV-17 | 15m | $10 handles at 15m | 15m open < L, high > L by <= 0.5 ATR, close < L, L a multiple of $10 -> S | bar high + 0.1 ATR | 1 R; cap 2 h | Z4 at 15m with a $2.50 floor; neighbour, expected FAIL |
| S200-MEANREV-18 | 1d | Daily rejection of a $50 handle | Day open < L, high > L ($50 multiple), close < L - 0.2 ATRd -> S next day | day high + 0.1 ATRd | 2 R; cap 3 days | daily |
| S200-MEANREV-19 | 1h | Classic floor-trader pivots are self-fulfilling order levels (R2/S2 extremes) | Daily classic pivots from the prior server day; 1h high >= R2, close < R2 -> S (first per day per side) | bar high + 0.1 ATR | target P; day-end | R-09 (never trialled) |
| S200-MEANREV-20 | 1h | As 19 at R1/S1 | 1h high >= R1, close < R1 -> S (first per day per side) | bar high + 0.1 ATR | target P; day-end | R-09 |
| S200-MEANREV-21 | 4h | Weekly pivot R1/S1 rejection | weekly classic pivots from the prior server week; 4h high >= R1w, close < R1w -> S (first per week per side) | bar high + 0.1 ATR | target weekly P; cap 5 days | R-09, weekly |
| S200-MEANREV-22 | 15m | Camarilla H3/L3 reversal (practitioner) | prior-day Camarilla (H3 = C + 1.1 (H - L)/4, H4 = C + 1.1 (H - L)/2); 15m high in (H3, H4), close < H3 -> S (first per day per side) | H4 + 0.1 ATR | target prior-day close C; day-end | R-09 |
| S200-MEANREV-23 | 1h | Overextension from the mean in volatility units reverts once momentum stalls | (prior close - SMA20)/ATR >= 3 on the prior bar and this close < prior bar low -> S | max high of last 3 bars + 0.2 ATR | target SMA20 at decision; cap 12 h | OS-SA used a 60-min 3-sigma move without a reversal bar |
| S200-MEANREV-24 | 4h | as 23 at 4h | same at 4h | same | target SMA20; cap 3 days | 4h |
| S200-MEANREV-25 | 1d | Multi-day overextension with a reversal day | (prior close - SMA10)/ATRd >= 2.5 on the prior day and today close < prior day low -> S next day | max high of 3 days + 0.2 ATRd | target SMA10; cap 5 days | daily |
| S200-MEANREV-26 | 1d | Short-term daily reversal inside the long trend (Connors RSI2) | RSI(2) < 5 and close > SMA200 -> L; RSI(2) > 95 and close < SMA200 -> S | 2.0 ATRd | 1 R; cap 5 days | daily |
| S200-MEANREV-27 | 4h | as 26 at 4h | RSI(2) < 3 and close > SMA50 -> L; > 97 and < SMA50 -> S | 1.5 ATR | 1 R; cap 2 days | 4h |
| S200-MEANREV-28 | 1h | Band excursion with a close back inside reverts to the middle | Bollinger(20, 2.5): prev close > upper, this close back inside -> S | max high two bars + 0.1 ATR | target SMA20; cap 12 h | OS-SD BB(20,2) in low-RV tercile; R-01. Neighbour, expected FAIL |
| S200-MEANREV-29 | 1d | Internal bar strength: closes at the day's extreme overshoot (Pagonidis 2014, equity indices) | IBS = (C - L)/(H - L) <= 0.10 -> L, >= 0.90 -> S, next day | 1.0 ATRd | day-end (1-day hold) | daily, untested |
| S200-MEANREV-30 | 15m | Daily range is mean-reverting; a day already 1.2 ATRd above its open is exhausted | After a 15m high >= day open + 1.2 ATRd, the first 15m close <= day high - 0.3 ATR15 -> S (once per day per side) | day high + 0.25 ATR1h | 2 R; day-end | range exhaustion, untested |
| S200-MEANREV-31 | 15m | Runs of 8 same-direction 15m closes over >= 2.5 ATR exhaust | 8 consecutive higher closes, cumulative >= 2.5 ATR -> S | run high + 0.25 ATR | 1 R; cap 3 h | Z2 was 1m/5m with a $3 stop |
| S200-MEANREV-32 | 1h | 6 consecutive higher 1h closes exhaust | 6 consecutive higher closes -> S | last bar high + 0.5 ATR | 1 R; cap 6 h | 1h |
| S200-MEANREV-33 | 1d | 4 consecutive down days revert | 4 consecutive lower closes -> L (mirror S) | 1.5 ATRd | 1 R; cap 3 days | daily |

"First per day/week/month per side" applies where written; elsewhere every signal is eligible subject to one position
at a time. Rows without a cap or day-end exit only at stop/target or the weekend clip; every row above has one.

## 3a. Literature (claims the rows lean on; none is net of our costs)

- Raschke & Connors (1995), *Street Smarts*, practitioner book, US futures and equities, no sample statistics:
  turtle soup. *(unverified)*, single source.
- Connors & Alvarez (2008), *Short Term Trading Strategies That Work*, practitioner book, US equities, gross: RSI(2).
  *(unverified)*, single source.
- Pagonidis (2014), "The IBS Effect: Mean Reversion in Equity ETFs", NAAIM Wagner award paper, equity index ETFs,
  gross. *(unverified)*, single source.
- Osler (2003), "Currency orders and exchange-rate dynamics", Journal of Finance 58(5), one dealer's FX order book
  1999-2000, gross: take-profit orders cluster at round numbers (reversal), stop-losses just beyond (continuation).
  Replicated in FX by Osler (2005, JIMF); not shown for gold.
- The program's own evidence (section 0) is the strongest prior, and it is negative at 1m-15m.

## 4. P4 screen expectation and trial cost

- Expected explore n per row ~50-2,500; the daily rows (10, 18, 25, 26, 29, 33) and 08 / 14 may fall below 100 and
  FAIL on count. Typical cost in R: 15m rows 0.08-0.15 R, 1h 0.04-0.08 R, 4h / 1d 0.01-0.04 R plus the long swap
  ($0.60/oz a night, ~0.02-0.05 R a night at daily stops).
- Trial cost: one local run, at most 2 worker processes, < 1 agent-hour of compute; K +33.

## 5. Owner-only items (flagged, not assumed)

None needed for the screen. A survivor goes to demo only as an exploration arm at the minimum lot inside RiskGate and
the existing caps; live trading, sizing and budget stay owner decisions.
