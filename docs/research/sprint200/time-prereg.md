# Sprint-200, lane TIME (time and calendar): pre-registration of 33 strategies

Principal regime and opportunity-day researcher, 2026-11-02. Written and committed **before any market data is read for
this lane** (no bar, return or calendar outcome has been computed). Brief: sprint-200 shared brief (owner directive
2026-11-02). Cost of record pinned: **`cor-2026-10-31.2`**, broker **icm** (ADR 0064). Screen code (committed after this
file, built and unit-tested on synthetic bars only before the run): `scripts/sprint200/time_screen.py`.

## 0. What is already known (not re-tested as such)

The time-structure programme (`time-structure-2026-10-26.md`, `time-structure/sessions.md`) already retired, in the
form tested on 2010-2017: unconditional LBMA/COMEX/Tokyo anchor reactions, session opens as levels, 15m ORB (24
variants), turn of month, expiry, FOMC/NFP/CPI direction, pre-FOMC drift, festival/month seasonality; H-04 (Asia long
drift) and H-24 (West short drift) cannot pass; H-72 (Friday long) is forward-only; H-76 (A-1 Asia-range fade) closed;
H-83 when-map (timing-only at the S0 clock, 24 cells) retired; growth C2a/C2b (session-range pyramid breakouts) failed;
H-89 FOMC continuation is its own line. Every strategy below is **conditional** (a move-size, range or flow-sign
condition, a specific calendar subset, or a different hold/exit) and says how it differs. Strategy 07 sits on an
anchor whose unconditional 30-minute persistence was already looked at on 2010-2017 (COMEX settle, t -2.94,
`sessions.md` section 2): its explore read is therefore not clean and only its confirm read is evidence (disclosed now).

Prior: low. The program is at K ~3,029 with 0 survivors; I expect 0-2 of 33 to pass confirm.

## 1. Common mechanics (all 33)

- **Data:** Dukascopy XAUUSD 1m, `raw/xauusd_1m_dukascopy_<year>.parquet`, mid = (bid + ask) / 2. Explore
  2010-01-01..2017-12-31; confirm 2018-01-01..2025-09-30, once, explore-passers only. Bars at or after 2025-10-01 are
  never loaded (`goldbot.research.arms.cut_at_holdout` + `assert_no_holdout_rows`; the 2025 file is cut on load and the
  2026 file is never opened). A trade whose exit would fall at or after 2025-10-01 is dropped.
- **Clocks:** `NY` America/New_York, `LDN` Europe/London, `TKY` Asia/Tokyo, `SH` Asia/Shanghai; each day's local time
  is converted to UTC per date (DST-safe). Days are Monday-Friday local dates of the decision clock.
- **P(T)** = close (mid) of the last 1m bar with `ts_utc < T`, taken only if that bar started within 10 minutes of T
  (else the day is skipped for that strategy). This is what is known at the decision close T.
- **Entry** at T: the open (mid) of the first 1m bar with `ts_utc >= T`, only if it starts within 10 minutes of T.
- **Exit:** the stop, the target (strategy 20 only) or the time exit E, whichever first. The path is searched minute by
  minute from the entry minute (its own minute included) to the last minute before E; **stop first** when stop and
  target fall in one minute; a minute that opens beyond the stop fills at its open (gap), else at the stop. Time exit
  at the open (mid) of the first bar with `ts_utc >= E`.
- **ATRh(T)** = mean over the 24 most recent complete UTC clock hours ending at or before T that hold >= 30 bars, of
  (max mid high - min mid low) in the hour. **ATRd(T)** = mean over the 20 most recent complete NY days (17:00 NY to
  17:00 NY) ending at or before T, of the mid high - low. Both point in time.
- **Rolling percentiles** (04, 08, 12): over the strategy's own values on the 250 prior qualifying days (strictly
  before the decision day), at least 100 of them, else no trade.
- **Business days:** Monday-Friday that are not a CME metals full closure (`goldbot.data.cme_holidays`). **US holiday
  days:** every date in `cme_metals_holidays(year)` (full and early halt). **UK bank holidays:** the England rules
  (New Year observed, Good Friday, Easter Monday, first Monday of May, last Monday of May, last Monday of August,
  Christmas and Boxing Day observed) with the published exceptions 2011-04-29, 2012-06-04/05 (no 2012-05-28),
  2020-05-08 (no 2020-05-04), 2022-06-02/03 (no 2022-05-30), 2022-09-19, 2023-05-08. All known in advance.
- **COMEX gold options (OG) expiry:** the 4th last business day of the month; if that is a Friday or the business day
  before a CME holiday, the business day before it. **GC first notice day (FND):** the last business day of Jan, Mar,
  May, Jul, Sep and Nov (the month before each active month Feb/Apr/Jun/Aug/Oct/Dec).
- **Costs (ADR 0064, `cor-2026-10-31.2`, icm):** net R = (move - charged round trip of the entry's session - overshoot
  on stop exits - swap) / stop distance, via `goldbot.research.arms.screen_net_r` (`charged_round_trip_usd`: max(0.23,
  cell upper 90%); overshoot: the cell's clustered upper bound floored at 0.12). Swap: `SwapSpec.shipped()` (long
  -60 USD/lot/night, short 0, triple Wednesday) per rollover held (`rollover_nights`). The 0.70 overshoot stress line
  is reported beside every result, not a gate.
- **One position per strategy at a time;** at most one trade per strategy per day by construction.
- **Random-entry control (matched, per trade):** 5 draws (seed 20261102, numpy default_rng, draws in trade order). Each
  draw: entry minute uniform over the 1m bars of the same session instance (same day, same `DEFAULT_SESSIONS` session
  label as the strategy's entry minute; the unlabeled 17:00 NY-Tokyo gap is its own instance), same side, same stop
  distance in USD, same target in R (if any), same hold length in minutes (exit = draw entry + hold), same cost rules.
  A trade's control R = the mean of its 5 draws.

## 2. Pass rules (written before any look; identical for all 33)

- **t** = the t of the day-summed net R: mean / (sd / sqrt(days)). With at most one trade a day this equals the
  per-trade t. (`DsrInputs.t()`, the non-normality-adjusted t, is reported beside it.)
- **Explore PASS:** n >= 100 trades AND net mean R > 0 AND t >= 2.0 AND (net mean R - control mean R) >= 0.05 R.
- **Confirm PASS (explore-passers only, one look):** net mean R > 0 AND t >= 2.0 AND deflated Sharpe > 0, read as
  the day-summed DSR probability `day_summed_dsr(...).dsr_day_summed` > 0.5 (the observed SR exceeds the expected
  maximum SR of K null trials; fails closed below 200 trading days with a trade) at **K = 3,029 + 200 + every extra
  variant this lane runs** (planned: none, so K = 3,229; also reported at K = 5,000) AND the yearly mean net R is > 0
  in >= 6 of the 8 confirm years (2018..2025, 2025 = Jan-Sep).
- No parameter is changed after a look. A failed strategy is reported, never re-tuned in this sprint.
- **Deploy:** each confirm-passer becomes an exploration arm (min lot, RiskGate, 1% cap) with its own paired random
  control, per the brief rule 7.

## 3. The 33 strategies

Notation: `r[a->b]` = P(b) - P(a); side +1 long, -1 short; "stop k" = k x ATRh(T) (the R unit); exit = time exit E.
Mechanism first, then how it differs from what failed.

| ID | Mechanism (why gold pays it) | Days | Condition at decision T | Side | Stop | Exit E | Differs from retired |
|---|---|---|---|---|---|---|---|
| S200-TIME-01 | US-open news digestion continues (intraday momentum on news, Gao, Han, Li & Zhou 2018 JFE) | Mon-Fri | T = 09:00 NY; r = r[08:30->09:00 NY]; abs(r) >= 0.75 ATRh | sign(r) | 1.5 | 11:30 NY | anchor read was unconditional and 15m; this is big-move-only, 2.5h hold |
| S200-TIME-02 | 10:00 NY data slot (ISM, confidence, sentiment) burst continues | Mon-Fri | T = 10:30 NY; r = r[10:00->10:30 NY]; abs(r) >= 0.75 ATRh | sign(r) | 1.5 | 13:30 NY | never tested as a conditional |
| S200-TIME-03 | Intraday momentum, settle-to-settle: the day's first 23h predict the last COMEX hour (Elaut, Froemmel & Lampaert 2018 JFM, FX) | Mon-Fri | T = 12:30 NY; r = P(12:30 NY) - P(13:30 NY previous business day); abs(r) >= 1.0 ATRh | sign(r) | 1.5 | 13:30 NY | the Gao pairs looked at were within-session; this uses the full settle-to-settle return |
| S200-TIME-04 | Gao first-half-hour predicts last half-hour, only on large opens | Mon-Fri | T = 13:00 NY; r = r[08:20->08:50 NY]; abs(r) >= rolling 67th pct of abs(r) | sign(r) | 1.5 | 13:30 NY | top-tercile filter (unconditional pairs were null) |
| S200-TIME-05 | PM fix hedging/front-running overshoot reverts after the fix (Caminschi & Heaney 2014 JFM) | Mon-Fri | T = 15:05 LDN; r = r[14:35->15:05 LDN]; abs(r) >= 0.75 ATRh | -sign(r) | 1.5 | 16:00 LDN | large pre-fix moves only; the retired read was unconditional persistence |
| S200-TIME-06 | AM fix overshoot reverts | Mon-Fri | T = 10:35 LDN; r = r[10:05->10:35 LDN]; abs(r) >= 0.75 ATRh | -sign(r) | 1.5 | 12:00 LDN | as 05 |
| S200-TIME-07 | COMEX settlement pressure reverses after the settle | Mon-Fri | T = 13:30 NY; r = r[13:00->13:30 NY]; abs(r) >= 0.5 ATRh | -sign(r) | 1.5 | 14:30 NY | conditional, 1h hold; explore not clean (section 0) |
| S200-TIME-08 | Compressed Asia releases into London (volatility compression -> expansion) | Mon-Fri | T = 08:30 LDN; Asia range (08:00 TKY -> 08:00 LDN high-low) <= rolling 33rd pct; P(T) above the Asia high (long) or below the low (short) | break side | 1.5 | 12:00 LDN | C2a had no compression filter and pyramided; ORB used the London opening range |
| S200-TIME-09 | Large Asia moves on thin liquidity are faded by London | Mon-Fri | T = 08:00 LDN; r = r[08:00 TKY->08:00 LDN]; abs(r) >= 2.0 ATRh | -sign(r) | 1.5 | 12:00 LDN | H-04 was unconditional long; A-1 faded range extremes |
| S200-TIME-10 | A London trend closing at its extreme hands over to NY | Mon-Fri | T = 08:25 NY; r = r[08:00 LDN->08:25 NY]; abs(r) >= 2.0 ATRh; P(T) in the top 20% (r > 0) or bottom 20% (r < 0) of the 08:00 LDN->T range | sign(r) | 1.5 | 11:00 NY | C2b was a range-break pyramid; this is close-at-extreme continuation |
| S200-TIME-11 | NY rejects a big London move (handoff reversal) | Mon-Fri | T = 09:00 NY; L = r[08:00 LDN->08:25 NY], abs(L) >= 1.5 ATRh; N = r[08:25->09:00 NY], sign(N) = -sign(L), abs(N) >= 0.3 abs(L) | -sign(L) | 1.5 | 12:00 NY | new conditional |
| S200-TIME-12 | Extreme overnight moves overshoot and revert in the NY session | Mon-Fri | T = 09:00 NY; O = P(09:00 NY) - P(16:59 NY previous business day); abs(O) >= rolling 90th pct of abs(O) | -sign(O) | 1.5 | 12:00 NY | the unconditional overnight fade was null; this is top decile only |
| S200-TIME-13 | A strong US session continues in the next London morning (Asia/Europe follow US price action) | Mon-Fri | T = 08:00 LDN; Y = P(16:59 NY) - P(08:30 NY), previous business day; abs(Y) >= 2.0 ATRh | sign(Y) | 1.5 | 12:00 LDN | new |
| S200-TIME-14 | Weekend gaps on thin Sunday liquidity are filled in Asia | Monday (TKY date) | T = 08:00 TKY Monday; G = P(T) - last Friday close; abs(G) >= 0.75 ATRh | -sign(G) | 2.0 | 08:00 LDN Monday | weekday effects retired were unconditional; this is a gap-size rule |
| S200-TIME-15 | Friday afternoon position squaring fades a strong week | Friday | T = 12:00 NY; W = P(T) - first P of the week (Sunday/Monday open); abs(W) >= 1.0 ATRd | -sign(W) | 1.5 | 16:30 NY | H-72 is unconditional Friday long |
| S200-TIME-16 | Information in a large weekend gap continues once Europe trades | Monday | T = 08:00 LDN; G = P(T) - last Friday close; abs(G) >= 1.0 ATRh | sign(G) | 1.5 | 12:00 NY | new (opposite read to 14, at a later clock; both counted) |
| S200-TIME-17 | Month-end rebalancing sells the month's winner into the 16:00 London (WM/R) fix | last 2 business days of the month | T = 14:00 LDN; MTD = P(T) - last close of the previous month | -sign(MTD) | 1.5 | 16:00 LDN | turn-of-month was retired as an unconditional long; this is flow-signed and fix-timed |
| S200-TIME-18 | After the month-end fix, the rebalancing pressure lifts and price rebounds | last 2 business days of the month | T = 16:05 LDN; MTD as 17 at T | sign(MTD) | 1.5 | 13:30 NY | as 17 |
| S200-TIME-19 | Quarter-turn inflows (window dressing, new-quarter allocations to gold ETFs), intraday only so no swap | last 2 and first 2 business days of each quarter | T = 08:00 LDN | long | 2.0 | 13:30 NY | quarter subset, intraday hold |
| S200-TIME-20 | Options pinning toward the nearest $25 strike in expiry week | OG expiry day and the 2 business days before | T = 10:00 NY; K = nearest multiple of 25 to P(T); d = K - P(T); 0.5 ATRh <= abs(d) <= 2.0 ATRh | sign(d) | abs(d) (R = abs(d)); target at K (1 R) | 13:30 NY | expiry retired as direction; this is a pin (toward strike) rule |
| S200-TIME-21 | Expiry-week dealer hedging dampens the morning move (mean reversion) | as 20 | T = 11:30 NY; r = r[08:30->11:30 NY]; abs(r) >= 1.0 ATRh | -sign(r) | 1.5 | 13:30 NY | new |
| S200-TIME-22 | Pre-FND liquidation: longs not taking delivery sell the front month | the 5 business days before FND | T = 08:35 NY | short | 1.5 | 13:30 NY | futures roll never tested |
| S200-TIME-23 | Post-FND rebound once the liquidation is done | FND and the 2 business days after | T = 08:35 NY | long | 1.5 | 13:30 NY | new |
| S200-TIME-24 | Pre-holiday effect (Ariel 1990 JF, equities): short-covering before US holidays | the 2 business days before each US holiday day | T = 08:00 LDN | long | 2.0 | 12:00 NY | holiday rules so far were risk rules, not alpha |
| S200-TIME-25 | On thin (one-centre holiday) days the London morning move overshoots and reverts | US holiday days that are business days, and UK bank holidays that are business days | T = 11:00 LDN; r = r[08:00->11:00 LDN] | -sign(r) | 1.5 | 14:00 LDN | new |
| S200-TIME-26 | A strong US day closing at its extreme continues into Asia | Mon-Thu (Friday excluded: no weekend hold) | T = 16:00 NY; D = r[08:30->16:00 NY]; abs(D) >= 2.5 ATRh; P(T) in the top 15% (D > 0) / bottom 15% (D < 0) of the 08:30 NY->T range | sign(D) | 2.0 | 08:00 LDN next business day (swap charged) | new (overnight hold) |
| S200-TIME-27 | The first Tokyo hour sets the Asia direction | Mon-Fri (TKY date) | T = 09:00 TKY; r = r[08:00->09:00 TKY]; abs(r) >= 0.75 ATRh | sign(r) | 1.5 | 12:00 TKY | Tokyo-open anchor was unconditional and 15m |
| S200-TIME-28 | London lunch lull retraces a strong morning | Mon-Fri | T = 12:00 LDN; r = r[08:00->12:00 LDN]; abs(r) >= 1.5 ATRh | -sign(r) | 1.5 | 13:25 LDN | new |
| S200-TIME-29 | The NY morning's move continues after the COMEX settle | Mon-Fri | T = 13:35 NY; r = r[08:30->13:30 NY]; abs(r) >= 1.5 ATRh | sign(r) | 1.5 | 16:30 NY | new (afternoon leg) |
| S200-TIME-30 | Frankfurt/European open (07:00 LDN) sets the pre-London direction | Mon-Fri | T = 07:30 LDN; r = r[07:00->07:30 LDN]; abs(r) >= 0.75 ATRh | sign(r) | 1.5 | 10:00 LDN | new |
| S200-TIME-31 | A big prior week is partly unwound on Monday | Monday | T = 08:00 LDN; Wk = last Friday close - first P of the previous week; abs(Wk) >= 1.5 ATRd | -sign(Wk) | 2.0 | 13:30 NY Monday | weekday effects retired were unconditional |
| S200-TIME-32 | New-month inflows (savings plans, ETF creations) | first 2 business days of the month | T = 08:35 NY | long | 1.5 | 13:30 NY | turn-of-month was retired as a multi-day hold with swap; this is intraday NY only |
| S200-TIME-33 | Chinese physical demand buys Asian dips before London | Mon-Fri | T = 14:00 SH; r = P(T) - P(08:00 TKY); r <= -1.0 ATRh | long | 1.5 | 08:00 LDN | new (long-only dip rule) |

Where a time exit E falls at or before T on a given date (DST mismatch), the trade is skipped. Strategies 14-25 and
31-32 run on calendar subsets; their n may fall below 100 in explore, in which case they FAIL on n (no rescue).

## 4. Trial cost and K

33 explore reads (K +33, inside the sprint's 200), plus one confirm read per explore-passer (same rule, no new K). No
extra variants are planned; any that are run are added to K and reported. Mac load: at most 2 worker processes.

## 5. Reading rule (frozen)

Report every strategy, losers beside winners: explore n, gross and net mean R, t, control mean and difference, the 0.70
stress net; confirm n, net R, t, DSR at K 3,229 and 5,000, years positive. A survivor is a strategy that passes both.
Survivors only: rule code `goldbot/research/arm_rules_sprint200_time.py`, a unit test on a fixed bar fixture, and an
append-only exploration arm entry with its paired random control and qualification record.
