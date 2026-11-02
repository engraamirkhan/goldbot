# Sprint-200, lane `macro` (macro and events): pre-registration of 33 strategies (2026-11-02)

Principal gold macro and flows researcher. Written and committed **before any bar, event or macro value is read for
this lane** (inspected before writing: column schemas, the calendar's per-type row counts and the macro file's series
names only; no price and no macro value in any window). Shared rules: the sprint-200 brief (owner directive 2026-11-02, competition section included). Code that
implements this text exactly: `scripts/sprint200/macro_screen.py` (committed after this file; it may not change any
rule below, only implement it; a bug fix that changes a number is reported as such).

## 0. What already failed and must not be re-tested (how this lane differs)

| Retired line | Evidence | This lane's difference |
|---|---|---|
| H-89 post-FOMC continuation, sign(t+5m - t-1m), hold t+6m -> t+60m | confirm FAIL 2026-11-01 (`h89-fomc-confirm-2026-11.md`) | no FOMC-only 5m-sign rule; FOMC enters only inside pooled sets, as a range breakout with a structure stop and 2R target (S04/S05), next-day drift (S15) or pre-event drift (S17/S18) |
| Director's 14-type event screen, sign(t+5m - t-1m), hold 5 -> 60m (K 15, 2010-2017 seen) | `decision-2026-11-01-next-lines.md` | no 5 -> 60m sign rule is re-run on any type. Constructions here: pre-range breakout, 15m overshoot fade, 30m -> NY-afternoon drift, next-day drift, pre-event drift, Monday/day-after shock, 4h event bar. **Disclosure:** that screen's 2010-2017 per-type numbers (retail sales t 2.46, NFP t -0.89) are known to this lane; no strategy below isolates retail sales or NFP, and the director's 15 looks stay in K |
| H-84 / R-16: 20-day real-yield or broad-dollar sign as a 4h/1d direction rule; GVZ VRP | screen FAIL 2026-10-30 | real yields and VIX enter only as **gates** on event breakouts (S31, S32), never as the direction rule |
| H-86..88 positioning, cross-asset lead-lag X1-X3, OS-SA/SB/SD (incl. Sunday-gap fade SB), discovery v1/v2, growth C1-C4, microtick VAC, retail/ICT setups, meta-label M1/M2, TV T-1 | various 2026-10/11 | not used. The Monday rules S20/S21 act at the London open on Friday's whole-day move, not on the reopen gap |

**Not testable from local data (blocked, no trial spent):** ECB, BoJ and BoE decisions (no point-in-time central-bank
calendar outside the US is on disk; fetching one is a new data source for the data-engineer), and any data
*surprise* (actual minus consensus): no point-in-time consensus series is on disk. Rules below use only the scheduled
time and the price response.

**Power note, decided before the look:** a single monthly release gives at most 96 events in the 8 explore years,
below the n >= 100 rule. So every event rule is run on a **pool** of event types, never on one monthly type.

## 1. Inputs (research.yml-style)

```yaml
bars: raw/xauusd_1m_dukascopy_{2009..2025}.parquet   # Dukascopy 1m bid/ask, bar labelled by its open, visible at open+1m
calendar: ~/tmp/kcal_out/us_calendar.parquet           # keyless calendar-v1 build (scripts/us_calendar_data.py); sha256 recorded in the result
macro: ~/tmp/macro_dl/macro_fred.parquet               # macro-v1, sha256 47473d2c3f9c4b35ae4b20368cbcb1f19678e65f40e81ed755363a6c45eaa2ed (S31, S32 only)
cost_of_record: cor-2026-10-31.2                       # broker icm, ADR 0064 (charged RT = max(cell ci90 upper, 0.23); overshoot = max(cell ci90 upper, 0.12))
swap: SwapSpec.shipped()                               # long -60 USD/lot/night, short 0, triple Wednesday, server Europe/Athens
explore_window: 2010-01-01..2017-12-31                 # decision time in window
confirm_window: 2018-01-01..2025-09-30                 # explore-passers only, ONE look; a trade whose exit would reach 2025-10-01 is dropped
holdout: nothing at or after 2025-10-01 00:00 UTC is loaded (asserted in code); the 2026 file is never opened
workers: 2
```

## 2. Common definitions (apply to every strategy)

- **Prices.** mid = (bid + ask) / 2 for open, high, low, close of each 1m bar. P(x) = the mid close of the last 1m bar
  visible at or before wall time x (visible = open + 1m). pre = P(t) for event minute t (the last pre-release close).
- **Events.** `us_calendar` rows with `status == scheduled`, an `event_id` with no `cancelled` row available before t,
  and `available_utc <= t - 1 day` (the time was known in advance). Unique event minutes; a minute belongs to every pool
  of any type released at it. Pools: **T1** = {cpi, nfp, pce, retail_sales, ppi}; **CLM** = jobless_claims minutes with
  no T1 type at the same minute; **ISM** = {ism_manufacturing, ism_services}; **FED** = {fomc_statement, fomc_minutes};
  **ALL** = every type in the calendar except fomc_press_conference (same meeting as the statement).
- **Pre-range** of event t: high H and low L of mid over the 1m bars opening in [t - 60m, t - 1m]; at least 40 bars
  present, else no trade.
- **Volatility (point in time, completed bars only).** ATR15(x), ATR1h(x), ATR4h(x): mean true range of the last 14
  completed clock-aligned 15m, 1h, 4h mid bars (4h aligned to 00/04/.../20 UTC) closing at or before x. **D-bars**:
  trading days ending 17:00 America/New_York. ATRd(x): mean true range of the last 20 completed D-bars. r_D: log
  close-to-close return of D-bar D. sd60(x): sd of the last 60 completed r_D. SMA50d(x): mean of the last 50 completed
  D closes. **Trend gate**: side == sign(last completed D close - SMA50d). **RV regime**: RV20 = sd of the last 20
  completed r_D; top tercile = RV20 >= the 66.67th percentile of the last 252 completed RV20 values; bottom tercile =
  RV20 <= the 33.33rd percentile.
- **Execution (brief rule 3).** Decision at a bar close (wall time x); entry at the **open of the first 1m bar opening
  at or after x**, at mid. Stop and target are checked on 1m mid high/low from the entry bar on: a bar that opens
  through the stop (or target) fills at its open; otherwise **stop first** when both are inside one minute. Time exit at
  the open of the first bar opening at or after the exit time. Stop distance floored at **$1.50/oz**. R = the stop
  distance in $/oz.
- **Costs.** net R = (side x move + swap - charged round trip(entry session) - overshoot(entry session) x [stop exit]) / R,
  through `goldbot.research.arms.screen_net_r(broker="icm", version="cor-2026-10-31.2")`; entry session from
  `DEFAULT_SESSIONS` (outside every session = the dearest cell). Swap per `rollover_nights`. The 0.70 overshoot
  stress line is reported beside it, never gated on.
- **Overlap.** One open position per strategy: a candidate whose entry is before the previous kept trade's exit is
  skipped.
- **Random control (brief rule 4).** 10 twins per real trade: entry at the open of a uniformly drawn 1m bar on the same
  UTC calendar day with the same session label as the real entry, same side, same stop distance in $, same target
  distance in $ (or none), same planned hold (planned exit time - entry time), same cost and swap rules. Seed =
  sha256(strategy id, trade index). control R = mean of all twins' net R.
- **Statistics.** t = mean / (sd / sqrt(n)) of per-trade net R. DSR = Bailey & Lopez de Prado (2014) via
  `goldbot.research.metrics.DsrInputs.of(net_r).dsr(K)`; "deflated Sharpe > 0" is read as **DSR >= 0.5** (the per-trade
  Sharpe exceeds the expected maximum of K unskilled trials). Years: calendar years of the decision time.

## 3. Pass rules (frozen, the brief's rule 4)

- **Explore (2010-2017):** n >= 100 AND net mean R > 0 AND t >= 2.0 AND (net mean R - control R) >= 0.05.
- **Confirm (2018-01-01..2025-09-30, explore-passers only, one look):** net mean R > 0 AND t >= 2.0 AND DSR >= 0.5 at
  **K = 3,229** (3,029 before the sprint + 200 sprint trials; this lane adds 33 and no other variant; if the
  orchestrator's final K is larger the DSR is also reported at that K, and the larger K governs) AND mean net R > 0 in
  >= 6 of the 8 confirm years (2018..2025).
- No tuning after the look. A rule that fails is reported with its numbers and stays failed.

## 4. The 33 strategies

Mechanism key. **BRK**: scheduled news resolves a known uncertainty and reprices gold in jumps that persist for hours
(Andersen, Bollerslev, Diebold & Vega 2003, AER, FX 1992-1998, gross; Andersen et al. 2007, JIE, bonds/stocks/FX;
replicated across asset classes, no gold net-of-cost result known); a break of the pre-news range enters after the jump
direction is known, with the move large against our ~$0.4 all-in cost. **FADE**: release-minute books are thin, so the
first 15m overshoot partly reverses once liquidity returns (single-asset evidence mixed; *unverified* for gold).
**DRIFT**: slow digestion of news into the NY afternoon (Gao, Han, Li & Zhou 2018, JFE, intraday momentum stronger on
news days, SPY 1993-2013, gross; single paper for this construction). **NEXT**: next-day continuation of a large
event-day move (macro news drift; *unverified* for gold). **PRE**: announcement risk premium earned before scheduled
releases (Lucca & Moench 2015, JF, S&P 500 1994-2011, gross, pre-FOMC; Ai & Bansal 2018, Econometrica; the gold sign
is *unverified*), and its unwinding after the release ("buy the rumour, sell the fact"). **SHOCK**: overreaction to a
large day move reverses when liquidity returns (or continues as news is digested); gold's day-after behaviour is
*unverified*. **REGIME**: the same event edge is larger in the regime where its mechanism should bind (trend, high/low
realised vol, real-yield direction, risk-off VIX).

| id | mech | pool / tf | rule (decision -> side) | stop | target | time exit |
|---|---|---|---|---|---|---|
| S200-MACRO-01 | BRK | T1, 1m | first 1m bar opening in [t, t+30m) whose mid close > H (long) or < L (short); decision = its close | range midpoint (H+L)/2, distance floored at max($1.50, 0.5 ATR15(t)) | 2R | t+240m |
| S200-MACRO-02 | BRK | CLM | as 01 | as 01 | 2R | t+240m |
| S200-MACRO-03 | BRK | ISM | as 01 | as 01 | 2R | t+240m |
| S200-MACRO-04 | BRK | FED | as 01 | as 01 | 2R | t+240m |
| S200-MACRO-05 | BRK | ALL | as 01 | as 01 | 2R | t+240m |
| S200-MACRO-06 | FADE | T1, 15m | m = P(t+15m) - pre; if abs(m) >= 1.0 ATR15(t): side = -sign(m), decision t+15m | beyond the [t, t+15m) extreme against the trade + 0.25 ATR15(t) | 1R | t+120m |
| S200-MACRO-07 | FADE | CLM | as 06 | as 06 | 1R | t+120m |
| S200-MACRO-08 | FADE | ISM | as 06 | as 06 | 1R | t+120m |
| S200-MACRO-09 | FADE | ALL | as 06 | as 06 | 1R | t+120m |
| S200-MACRO-10 | DRIFT | T1, 30m | m = P(t+30m) - pre; if abs(m) >= 0.5 ATR15(t): side = sign(m), decision t+30m | 1.5 ATR1h(t) | none | 16:00 America/New_York of the event day (no trade if that is < 30m after the decision) |
| S200-MACRO-11 | DRIFT | CLM | as 10 | as 10 | none | as 10 |
| S200-MACRO-12 | DRIFT | ISM | as 10 | as 10 | none | as 10 |
| S200-MACRO-13 | DRIFT | ALL | as 10 | as 10 | none | as 10 |
| S200-MACRO-14 | NEXT | T1, 1d | decision 20:00 America/New_York of the event's NY calendar day; m = P(decision) - pre; if abs(m) >= 0.5 ATRd: side = sign(m) | 1.0 ATRd | none | 16:00 America/New_York on the next NY weekday |
| S200-MACRO-15 | NEXT | FED | as 14 (FOMC: a next-day construction, not H-89's 60m) | as 14 | none | as 14 |
| S200-MACRO-16 | NEXT | ALL | as 14 | as 14 | none | as 14 |
| S200-MACRO-17 | PRE | T1 + FED, 1d | decision t - 24h (needs a bar visible in [t-24h-5m, t-24h], else none): long | 1.0 ATRd | none | t - 5m |
| S200-MACRO-18 | PRE (unwind) | T1 + FED | d = pre - P(t-24h); if abs(d) >= 0.5 ATRd: side = -sign(d), decision t+5m | 1.5 ATR1h(t) | none | t+240m |
| S200-MACRO-19 | PRE (unwind) | CLM + ISM | as 18 | as 18 | none | t+240m |
| S200-MACRO-20 | SHOCK | Monday, 1d | first D-bar of a week (after a weekend gap > 24h) whose previous D-bar has abs(r) >= 1.0 sd60: side = -sign(r_prev), decision 08:00 Europe/London Monday | 1.0 ATRd | none | 16:00 America/New_York same day |
| S200-MACRO-21 | SHOCK | Monday | as 20 but side = +sign(r_prev) (continuation) | 1.0 ATRd | none | as 20 |
| S200-MACRO-22 | SHOCK | any day | any D-bar whose previous D-bar has abs(r) >= 1.5 sd60: fade, decision 08:00 Europe/London | 1.0 ATRd | none | 16:00 America/New_York same day |
| S200-MACRO-23 | SHOCK | event day | previous D-bar contains a T1 or FED event and abs(r) >= 1.0 sd60: fade, decision 08:00 Europe/London | 1.0 ATRd | none | as 22 |
| S200-MACRO-24 | REGIME | 01 + trend gate | 01's trades with side == sign(D close - SMA50d) | as 01 | 2R | as 01 |
| S200-MACRO-25 | REGIME | 05 + RV top tercile | 05's trades when RV20 is in the top tercile | as 05 | 2R | as 05 |
| S200-MACRO-26 | REGIME | 09 + RV bottom tercile | 09's trades when RV20 is in the bottom tercile | as 09 | 1R | as 09 |
| S200-MACRO-27 | REGIME | 13 + trend gate | 13's trades with the trend gate | as 13 | none | as 13 |
| S200-MACRO-28 | REGIME | 16 + trend gate | 16's trades with the trend gate | as 16 | none | as 16 |
| S200-MACRO-29 | BRK (4h) | T1 + FED, 4h | the UTC-aligned 4h bar containing t; at its close, body = C - O; if abs(body) >= 0.5 ATR4h (bars before it): side = sign(body) | 1.0 ATR4h | 2R | 8h after the decision |
| S200-MACRO-30 | REGIME | 29 + trend gate | 29's trades with the trend gate | as 29 | 2R | as 29 |
| S200-MACRO-31 | REGIME | 05 + real-yield gate | 05's trades with side == -sign(DFII10 last - DFII10 20 observations earlier), values with available_utc <= decision | as 05 | 2R | as 05 |
| S200-MACRO-32 | REGIME | 05 + VIX gate | 05's trades when VIXCLS (last available) >= the median of its last 252 available values | as 05 | 2R | as 05 |
| S200-MACRO-33 | BRK (hold) | T1 | 01's entries | the opposite side of the pre-range (L for a long, H for a short), floor as 01 | none | 16:00 America/New_York of the event day |

Gated strategies (24-28, 30-32) apply the gate to the parent's candidate list **before** the overlap rule, and draw
their own twins. Each of the 33 is one trial: **K +33** (no other variant is run).

## 5. Expectations and reading rule (written before the look)

- Prior per strategy of passing explore: 5-10% (BRK and DRIFT highest); of passing confirm given explore: ~25%.
  Expected confirm survivors: 0-1. Every gold-only price, macro and event line so far has failed net of cost
  (~3,029 looks, 0 survivors), so a 0 is the base case and is reported as such.
- Cost size: all-in ~$0.35-0.45/oz a trade (RT 0.19-0.27 + overshoot 0.12-0.20 on stops). Event stops of $3-8 put
  this at 0.05-0.15 R; the 1d rules (stop ~1 ATRd ~ $15-25) at ~0.02 R plus ~0.03 R a long night of swap.
- A strategy that beats its control by >= 0.05R but is net negative is a gross finding, not an edge.
- Survivors (confirm passers) become exploration arms with their own paired random control (brief rule 7); losers
  are reported beside winners in `docs/research/sprint200/macro-result.md`.

## 6. Owner-only flags

None is decided here: going live, sizing, instruments (a swap-free venue for the 1d rules), spending on ECB/BoJ/BoE or
consensus data. Demo deployment of any survivor is at the minimum lot inside the RiskGate and the 1% cap.
