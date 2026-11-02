# Sprint-200 lane TREND: pre-registration (34 strategies, S200-TREND-01..34)

Strategy researcher (principal), 2026-11-02. Written and committed BEFORE any bar of 2010-2025 is read for this lane
(no counts, no outcomes). Brief: orchestrator `sprint200-brief.md` (owner directive 2026-11-02), lane key `trend`.
Script (committed after this file, before any run): `scripts/sprint200/trend_screen.py`.

## 0. Honest prior

This family is the most mined corner of the programme: R-02 session_open 15m, R-03 trend pullback 1h, R-04 range
breakout 1h, R-05 intraday momentum, R-06/R-10 tsmom 1h/4h (gross +0.06 R, t 2.6, net negative), 15m ORB (24
variants), TTM squeeze, GS-C2 pyramided session breakout, GS-C4 NR7 4R break, OS-SD Donchian-20 1h in high RV, R-17
trend-day continuation, discovery v1/v2 (K 2,568). Expected passers at the confirm bar (DSR at K about 3,233 needs a
per-trade t of about 4): 0-1. Every strategy below differs from a retired one in timeframe, trigger, gate or exit, as
stated in its row; none re-runs a retired spec. The long CFD swap (-$0.60/oz/night) is charged, so multi-day longs
need a large gross edge; shorts pay none.

## 1. Conventions (all 34)

- **Data.** Dukascopy 1m bid/ask (`raw/xauusd_1m_dukascopy_<year>.parquet`), mid OHLC = (bid + ask) / 2, sorted,
  duplicate minutes dropped. Explore: data 2010-01-01..2017-12-31 (no earlier data exists), decisions in that window.
  Confirm: data 2016-07-01..2025-09-30 (2016-07..2017-12 is warm-up only, no decision before 2018-01-01). No row at or
  after 2025-10-01 is read: the loader never opens the 2026 file, cuts 2025 at 2025-10-01 and asserts it. Every open
  position is closed at the last minute of its window (time exit).
- **Bars.** 15m/1h/4h floor on the UTC clock (as `goldbot.data.resample.resample_bars`); a bar's decision time is its
  close (start + tf). 1d: the trading day ends 17:00 America/New_York (trading-day date = date of NY time + 7 h); its
  decision time is 17:00 NY. Week = Monday-start week of trading-day dates. A bar with no minute does not exist.
- **Indicators** use completed bars only (the decision bar included). ATR(n) = simple mean of the true range of the
  last n bars. EMA alpha 2/(n+1) (no adjust); RSI and ADX Wilder; MACD 12/26/9; Supertrend(10, 3) on Wilder ATR(10);
  Bollinger(20, 2) population sd. Shorthand: ATR15 = ATR(15m,14), ATRh = ATR(1h,14), ATR4 = ATR(4h,14),
  ATRd = ATR(1d,20). Channel(n) = highest high / lowest low of the n bars BEFORE the decision bar. "Fresh" = the
  previous bar did not meet the trigger. A higher-timeframe value is read as of the last bar of that timeframe whose
  decision time is <= this decision time.
- **D1UP / D1DN** (the 1d trend gate): the last completed daily close > EMA50(1d) and EMA50(1d) above its value 10
  daily bars earlier (D1DN: both reversed).
- **Sessions** are `goldbot.data.calendar.DEFAULT_SESSIONS` (DST-aware): asia Tokyo 08:00 -> London 08:00, london
  London 08:00 -> NY 08:30, newyork NY 08:30 -> NY 17:00. A session instance = one window on one trading day.
  **FLAT** = 16:30 America/New_York of the trading day (before the 17:00 NY rollover): intraday rules carry no swap.
- **Entry** at the OPEN of the first 1m bar at or after the decision time (mid). A rule with a deadline skips a
  signal whose fill is later than the deadline minus 15 minutes. **One position at a time** per strategy: a signal
  whose decision time is before the open trade's exit is dropped.
- **Exits** on the 1m mid path from the fill minute: stop at entry -/+ R (R = the rule's stop distance, USD/oz); a
  minute that gaps through the stop fills at its open; target (if any) at entry +/- target x R (level fill); stop
  first when both fall in one minute. Chandelier trail (if any): for a long the stop is max(initial stop, highest
  mid high of the minutes from the fill to the PREVIOUS minute - trail distance) (short mirrored), the trail distance
  fixed at entry. Indicator exit (if any): at the first decision bar close strictly after the fill where the rule's
  exit condition holds, filled at the next minute's open. Time cap: H decision bars after the decision time (or the
  stated deadline), exit at the last minute's close before it.
- **Costs** (cost of record `cor-2026-10-31.2`, pinned; ICM): per trade, session label of the fill minute
  (`DEFAULT_SESSIONS.session_label`) -> `ArmCosts.screen_charges("icm", ...)`: round trip = max(frozen $0.23, ICM
  cell round_trip_ci90 upper) and stop-exit overshoot = max($0.12 floor, cell overshoot_ci90 upper), charged on every
  stop or trailed-stop exit. Swap `SwapSpec.shipped()` (long -$60/lot/night = -$0.60/oz, short 0, triple Wednesday,
  server Europe/Athens) x `rollover_nights(fill, exit)`. Net R = (side x (exit - entry) + swap - round trip -
  overshoot x stop_exit) / R. A $0.70 overshoot stress line is reported, not used by the rule.
- **Matched random-entry control.** For each real trade, 4 twins with the same side, the same R (USD), target
  multiple, trail distance, indicator exit, the same hold length measured from the twin's own fill, and the same
  deadline rule on the twin's trading day. Twin fill minute: uniform (sha256 of strategy id, trade number, twin
  number) over the minutes of the SAME session instance (same trading day, same session) from the real fill minute to
  the instance's end; if that holds fewer than 30 minutes, the 60 minutes after the real fill; never later than a
  deadline minus 15 minutes. Twins only use minutes at or after the real fill, so the control never sees less than
  the signal saw (a pre-signal twin given the signal's side would carry the move that triggered it). Twins are not
  limited to one at a time. Control excess = mean over real trades with >= 1 twin of (real net R - mean twin net R).

## 2. Pass rules (frozen; same as the shared brief rule 4)

- **Explore** (2010-2017): n >= 100 trades AND mean net R > 0 AND t = mean / (sd / sqrt(n)) >= 2.0 AND control
  excess >= +0.05 R. Anything else is FAIL (n < 100 is FAIL-COUNT).
- **Confirm** (2018-01-01..2025-09-30), run ONCE and only for explore passers (the script refuses any other id):
  mean net R > 0 AND t >= 2.0 AND deflated Sharpe on per-trade net R (`goldbot.research.metrics.DsrInputs.of(r)
  .dsr(K)`, the Bailey and Lopez de Prado probability) >= 0.5 at K = 3,233 (programme 3,029 after A27 + 204 = six
  lanes x 34, the sprint's upper bound; "DSR > 0" read as the observed Sharpe exceeding the expected maximum of K
  null trials) and again at the final sprint K when the orchestrator reports it; DSR is not computed below 200 trades,
  which is FAIL; AND mean net R > 0 in >= 6 of the 8 confirm years 2018..2025 (2025 = Jan-Sep; a year without a trade
  is not positive). Confirm also reports the control excess (not a gate).
- **No tuning.** One spec per id, no variant beyond the 34 below. A bug found after a look is fixed for all 34,
  re-run in full and disclosed as a dated amendment here. K added by this lane: 34 (plus any amendment re-run, 0
  planned).
- **Deploy** only confirm passers (brief rule 7).

## 3. The 34 strategies

Columns: id | tf | mechanism (why gold pays it) | entry rule | stop / exit | differs from the retired form.

| id | tf | mechanism | entry | stop / exit | differs from |
|---|---|---|---|---|---|
| 01 H4-DON30-TR | 4h | trend persistence of multi-day flows (CTA/TSMOM, Moskowitz-Ooi-Pedersen 2012 JFE) | fresh 4h close beyond Channel(30) | 2 ATR4 stop, 3 ATR4 chandelier, cap 60 bars | R-04 was 1h fixed bracket; OS-SD 1h Donchian-20 RV-gated |
| 02 H4-DON20-D1 | 4h | breakout with the daily trend: flows aligned across horizons | fresh 4h close beyond Channel(20), long only in D1UP, short only in D1DN | 2 ATR4, target 3 R, cap 30 | adds D1 gate, 4h |
| 03 H1-DON48-D1 | 1h | as 02 at 1h, two-day channel | fresh 1h close beyond Channel(48), D1 aligned | 2 ATRh, 3 ATRh trail, cap 48 | R-04 no gate, fixed bracket; OS-SD 20 bars |
| 04 H1-DON24-ATRX | 1h | breakout on range expansion = informed order flow | fresh 1h close beyond Channel(24) AND bar TR >= 1.5 x ATR(1h,20) of the 20 bars before it | 1.5 ATRh, 2.5 ATRh trail, cap 24 | expansion condition, trail |
| 05 H4-KELT | 4h | volatility-scaled breakout (Keltner) | fresh 4h close above EMA20(4h) + 2 ATR4 (below EMA20 - 2 ATR4 short) | 1.5 ATR4, 2.5 ATR4 trail, cap 30 | band breaks were unconditional 15m-1h fixed brackets |
| 06 H4-POSTBREAK-PB | 4h | first pullback after a fresh daily breakout (late followers) | a daily close > prior 20-day high within the last 5 completed days and no daily close < prior 20-day low since (mirror short); 4h close crosses back above EMA10(4h) (previous close below it) | 1.5 ATR4, target 2 R, cap 18 | R-03 was 1h pullback without a daily breakout state |
| 07 ASIA-LON-1H | 1h | London price discovery breaks the thin Asia range | Asia range = asia instance ending at London open (>= 300 minutes); first 1h close in [London open, +4 h] beyond Asia high/low by >= 0.1 range; one trade a day | stop Asia mid, skip if R < 0.5 or > 4 ATRh; exit FLAT | GS-C2 15m, 1.5 ATR15 stop, pyramided; R-02 15m session open |
| 08 LON-OR60-1H | 1h | London first hour sets the day's auction range | OR = first 60 min of london; first 1h close in (open+1 h, open+5 h] beyond OR; one a day | stop opposite OR side, skip if R outside [0.5, 4] ATRh; exit FLAT | 15m ORB retired (24 variants): here 1h trigger, structural stop, day hold |
| 09 NY-OR60-D1 | 15m | US data/COMEX open range broken with the daily trend | OR = first 60 min of newyork; first 15m close in (OR end, +150 min] beyond OR in the D1 direction (other side ignored) | stop opposite OR side, skip if R outside [0.5, 4] ATRh; target 2 R; exit FLAT | 15m ORB had no D1 gate |
| 10 SGE-OR | 15m | Shanghai Gold Exchange day open (01:00 UTC) demand flow | OR = 01:00-02:00 UTC; first 15m close in (02:00, 05:00] UTC beyond OR; one a day | stop opposite OR side, skip if R outside [0.5, 4] ATRh; exit 06:45 UTC | no SGE-anchored rule tested (anchor reactions were fades at LBMA/COMEX/Tokyo) |
| 11 PDHL-1H-D1 | 1h | prior-day extreme break with the daily trend (stop clusters) | 1h close in [London open + 1 h, NY 15:00] beyond the previous trading day's high (D1UP) / low (D1DN); once per side a day | 1.5 ATRh; exit FLAT | key-level retests/fades retired; H-71 Asia-hours level break |
| 12 PWHL-4H | 4h | prior-week extreme break (weekly stop clusters, CTA weekly triggers) | fresh 4h close beyond previous trading week's high/low | 2 ATR4, 3 ATR4 trail, cap 30 | not tested |
| 13 ASIA-SQZ-15M | 15m | compressed Asia = stored order imbalance released at London | Asia range <= 0.6 x median of prior 20 Asia ranges; first 15m close in (London open, +3 h] beyond the Asia range | stop Asia mid, skip if R outside [0.5, 4] ATRh; exit FLAT | compression gate on Asia range (not an opening range) |
| 14 LON-HL-NY | 1h | NY extends a directional London session | first 1h close in (NY open, NY 12:00] beyond the London instance high (london return > 0) / low (< 0) | stop London mid, skip if R outside [0.5, 4] ATRh; exit FLAT | not tested |
| 15 H4-BBW | 4h | volatility compression -> expansion (Crabel 1990, practitioner) | previous bar's BB(20,2) width <= 10th pct of its last 250 bars; this 4h close outside the band | 1.5 ATR4, 2.5 ATR4 trail, cap 30 | TTM squeeze was BB-in-Keltner at 15m-1h with fixed bracket |
| 16 NR4ID-1H | 1h | narrow or inside day precedes range expansion | completed day d is NR4 or an inside day; during day d+1 first 1h close (Asia open + 1 h .. NY 15:00) beyond day-d range | stop opposite side of day-d range, skip if R outside [0.3, 2.5] ATRd; exit FLAT | GS-C4 NR7 only, 4 R target; v2 NR4 used as a gate on r4h side |
| 17 H1-ATRCOMP | 1h | intraday compression -> breakout | previous bar ATR(1h,5)/ATR(1h,50) < 0.6; this 1h close beyond Channel(12) | 1.5 x ATR(1h,50), target 3 R, cap 24 | not tested |
| 18 H4-INSIDE-D1 | 4h | inside-bar breakout with the daily trend | mother bar m, inside bar m+1; first 4h close in m+2..m+4 beyond the mother range in the D1 direction | stop other side of mother bar, skip if R outside [1, 3] ATR4; target 2 R; cap 18 | not tested |
| 19 H1-SHOCK-CONT | 1h | news-shock continuation (slow digestion; Gao et al. 2018 JFE adjacent) | 1h range >= 2.5 x ATR(1h,50) of the prior 50 bars, close in the top (bottom) 25% of its range | stop at the bar midpoint, skip if R outside [0.5, 3] x ATR(1h,50); target 2 R; cap 8 | MT-VAC faded 3-min moves at 1m |
| 20 ITS-AL2NY | 1m decision | session TSMOM: Asia+London direction carried into NY | at NY open: ret = mid(NY open) - mid(asia instance open); trade sign if abs(ret) >= 0.5 ATRd | 0.5 ATRd; exit FLAT | R-05 was first half-hour -> last half-hour |
| 21 ITS-A2L | 1m decision | Asia direction carried into London | at London open: ret since asia open, abs >= 0.25 ATRd | 0.4 ATRd; exit at NY open | H-75 is persistence within Asia |
| 22 BIGDAY-NEXT | 1d | large-day continuation next day (under-reaction) | daily abs(close_d - close_d-1) >= 1.5 x ATRd as of d-1; side = its sign; fill at the reopen | 0.75 ATRd; exit FLAT of day d+1 (no rollover held) | R-17 is same-day 15m continuation |
| 23 ITS-NOON-D1 | 1m decision | London-to-noon NY move continues into the afternoon with the trend | at 12:00 NY: ret = mid(12:00 NY) - mid(London open), abs >= 0.3 ATRd, sign = D1 direction | 0.4 ATRd; exit FLAT | R-05 NY half-hours, no gate |
| 24 MTF-EMA-1H | 1h | three-timeframe agreement | 1h EMA9 crosses EMA21 with D1UP and last 4h close > EMA50(4h) (mirror) | 1.5 ATRh, target 3 R, cap 24 | not tested as a rule |
| 25 MTF-RSI-4H | 4h | pullback end inside the daily trend | D1UP and RSI14(4h) crosses up through 40 (D1DN, down through 60) | 1.5 ATR4, target 2.5 R, cap 18 | oscillator fades at 15m-1h were unconditional |
| 26 MTF-15M | 15m | 15m trend resumption aligned with 1h and 4h | 15m close crosses EMA50(15m) with last 1h close > EMA20(1h) and last 4h close > EMA50(4h) (mirror); closes London open .. NY 15:00 | 1.0 ATRh, target 2 R, cap 32, exit FLAT | H-34 SMC MTF used FVG/CHoCH |
| 27 MACD-4H-D1 | 4h | MACD momentum turn with the daily trend | MACD histogram crosses zero in the D1 direction | 2 ATR4, 3 ATR4 trail, cap 30 | not tested |
| 28 ADX-ONSET-4H | 4h | trend onset (ADX as trigger, not a gate) | ADX14 crosses up through 20; side = sign(+DI - -DI) | 2 ATR4, 3 ATR4 trail, cap 30 | retired "ADX gate" filtered other rules |
| 29 ATREXP-1D | 1d | volatility expansion onset carries the 5-day direction | ATR(1d,5)/ATR(1d,20) crosses up through 1.2; side = sign(close_d - close_d-5); fill at reopen | 1.5 ATRd, 2.5 ATRd trail, cap 10 days | not tested |
| 30 PB-D1EMA20-1H | 1h | retest of the daily EMA20 in a daily uptrend (owner's example) | last daily EMA20 > EMA50 and daily close > EMA20 (mirror); previous 1h close above E20d, this 1h low <= E20d and close > E20d | stop E20d - 0.75 ATRd (mirror), target 2 R, cap 72 | R-03 used 1h EMAs only |
| 31 PB-4H-EMA20 | 4h | shallow pullback in a 4h trend | 4h close > EMA50(4h), EMA20 > EMA50, low <= EMA20(4h) < close (mirror) | 1.5 ATR4, target 2 R, cap 18 | R-03 was 1h |
| 32 RSI2-1H | 1h | short-term exhaustion inside a long trend (Connors) | close > SMA200(1h) and RSI(2) < 10 -> long; close < SMA200 and RSI(2) > 90 -> short | 2 ATRh; exit at a close beyond SMA5(1h) (long: close > SMA5), cap 10 | R-01 15m fades had no trend filter |
| 33 SUPERTREND-1H | 1h | stop-and-reverse trend tool | Supertrend(10,3) flips; trade the new direction | stop = distance to the Supertrend line, skip if outside [1, 4] ATRh; exit on the opposite flip, cap 72 | not tested |
| 34 LINREG-1H | 1h | statistically significant trend (regression t, not return sign) | OLS slope t-stat of the last 48 1h closes crosses up through +3 (down through -3) with R^2 >= 0.6 | 2 ATRh, 3 ATRh trail, cap 24 | R-06 tsmom used the return sign |

## 4. What is reported

Per id: n, gross and net mean R, t, control excess, exit mix, cost per trade in R, the $0.70 stress line, per-year
net means; confirm adds DSR at K 3,233 and the years-positive count. Losers are reported beside winners in
`docs/research/sprint200/trend-results.md`. Trade rows and market data are never committed.

## Amendment 1 (2026-11-02, before any look)

Coordinator control fix for all lanes: the pass rule uses a leak-free control drawn at or after the signal, matched
on day, session and side. Section 1's control already does exactly that (post-fill minutes of the same session
instance), so the pass rule is unchanged. As the coordinator asks, the brief's literal same-day control (4 twins
uniform over the WHOLE session instance, pre-signal minutes included, same side and exits) is also computed and
reported as `control_excess_full_lookahead`; it is not used by any rule.
