# Slice A: sessions, opens, closes, fixes, rollover and DST (XAUUSD)

Research-director staff, 2026-10-26. Follows `README.md` of this folder (sections 4-5). Script (reusable, read-only,
no trial, no backtest job): `docs/research/candidates/time_structure/session_structure.py`; run `uv run python docs/research/candidates/time_structure/session_structure.py
<dir of xauusd_1m_dukascopy_YYYY.parquet> [dst decomp events gao asia orb tf spread]`. Default window is the discovery half.
Data: release `data-v1` 1m Dukascopy, mid = (bid+ask)/2, price at T = close of the last bar that started before T
(visible one minute after its start), stale prices over 10 minutes dropped.

## 0. Disclosure: what was looked at, and on which half (read first)

**I measured outcomes on 2020-01-01..2026-10-23 before the frame arrived.** That span contains the confirmation half
(2018-2025-09) and also the never-loaded holdout (2025-10-01..2026-09-30 plus Oct 2026): my first pass loaded it.
Nothing was tuned on it and nothing here is a result, but under README 4.1 every hypothesis that those looks touched is
charged as a full trial with **no confirmation credit**, and the holdout must be treated as seen for anything in section
7 "Peeked tables". After the frame arrived I re-ran every outcome table on **2010-2017 only** (sections 2-5 are
those). The peeked numbers appear only in section 7, as a same-sign check, never as evidence. The last-12-month
activity and cost tables (section 1) use 2024-10-01..2025-09-30, inside the confirmation half and clear of the holdout
(activity/cost tables may use both halves, README 4.1).

Looks behind this file: about 70 discovery statistics (10 anchors x 2, 6 Gao pairs, 6 segments + overnight control, 3 Asia
holds x 2, 24 ORB variants, 3 DST regimes x 25 slots, descriptive) and the same ~70 again on 2020-26. Bonferroni 5% is
|t| about 3.2-3.4. **Nothing below clears it.**

## 1. What the clock does to activity and cost (activity/cost only)

### 1.1 Hour and timeframe (2024-10-01..2025-09-30, mid, $)
| UTC hour | mean 1m range $ | note |
|---|---|---|
| 00-02 | 0.95 / 1.33 / 1.05 | Tokyo 09:00 JST = 00:00 UTC; 01:00 is the second Asia bump (SGE/China hours) |
| 03-05 | 0.87 / 0.68 / 0.90 | quietest block |
| 06-11 | 1.05 / 1.06 / 1.03 / 0.96 / 0.91 / 0.98 | London AM, flat |
| 12-15 | 1.34 / **1.76 / 1.71** / 1.38 | NY data + pit open + equity open; 2.6x the 04:00 hour |
| 16-20 | 1.08 / 0.92 / 0.83 / 0.78 / 0.65 | decay into the NY close |
| 21-23 | 0.43 / 1.02 / 0.74 | feed break then reopen |

| session (local clocks of `session.py` v2) | 15m range $ | 1h range $ | 4h range $ | $0.144 as % of 15m / 1h / 4h range |
|---|---|---|---|---|
| Asia | 4.06 | 8.41 | 17.90 | 3.6 / 1.7 / 0.8 |
| London | 4.28 | 9.26 | 21.79 | 3.4 / 1.6 / 0.7 |
| New York | 4.64 | 9.17 | 12.20 | 3.1 / 1.6 / 1.2 |

Cost (measured broker round trip $0.144 central, $0.23 conservative; `cost-model-1m-2026-10-25.md`) is 3-4% of a 15m
range in every session and below 2% at 1h: the broker cost does not choose a session; volatility does. Measured broker
cost by session: Asia 0.149, London 0.140, NY 0.139, late NY 0.138, rollover 21-23 UTC 0.153 $/oz (n 11-61, every
difference inside noise). Dukascopy spread is about 8x our broker's, so use it for shape only.

### 1.2 Spread around the NY close and reopen (Dukascopy median, 2024-10..2025-09, ratio to 09:00-16:00 NY)
16:00-16:55 NY 0.96-0.99; 16:55-17:00 1.06; **no quotes 17:00-18:00 NY** (CME break); 18:00-18:05 **1.39**;
18:05-18:30 1.17; 19:00-20:00 1.12; Tokyo 09:00-09:15 JST 1.10. The widening is real but modest and short (about 5-30
minutes after the 18:00 NY reopen); the weekly broker rollover hour (21-23 UTC) had no measured fill at 21 UTC. Do not
open or manage tight stops in 18:00-18:30 NY or 16:55-17:00 NY; nothing else here is a cost edge.

## 2. The three sessions, opens, closes: roles (2010-2017, 2,060 days)

Activity = mean |15 minute return| after the stamp in bp; control mean 6.6 bp (400 random UTC minutes). Persistence =
mean of sign(x) * y in bp (opens: first 30 min x, next 60 min y; fixes/closes: 30 min before x, 30 min after y), t
over days, and the percentile of that value among the 400 control draws (control sd 0.4-0.55 bp).

| Anchor | |15m| after (bp) | x control | persistence (bp) | t | control pctile | role (this data) |
|---|---|---|---|---|---|---|
| Tokyo open 09:00 JST | 6.3 | 0.95 | -0.49 | -1.13 | 20 | Asia range formation; no activity jump |
| SGE AM auction 10:15 CST | 4.0 | 0.61 | +0.43 | +1.92 | 87 | quietest anchor |
| London open 08:00 | 7.8 | 1.18 | +0.66 | +1.61 | 92 | range expansion, mild; takes an Asia extreme 68%/68% (README 6.4) |
| LBMA AM fix 10:30 London | 6.2 | 0.93 | -0.63 | -2.31 | 5 | no activity spike; reversal-tilted |
| NY data / COMEX pit open 08:30 NY | **14.4** | **2.17** | 0.00 | 0.00 | 53 | the activity event of the day (data slot + COMEX open 08:20) |
| US equity open 09:30 NY | 12.6 | 1.89 | -0.31 | -0.43 | 31 | second NY burst |
| LBMA PM fix 15:00 London | 13.0 | 1.95 | -0.16 | -0.30 | 38 | spike belongs to 10:00 NY, not the fix (2.1) |
| COMEX settle 13:30 NY | 6.5 | 0.97 | **-0.83** | **-2.94** | 3 | no spike; reversal-tilted |
| LSE close 16:30 London | 8.6 | 1.29 | -0.62 | -1.61 | 5 | mild |
| NY close 17:00 NY | n/a (36 days quoted) | | | | | feed stops; nothing to trade |

Day-variance shares (README 6.1): NY pit window 08:30-13:30 carries 45%, London AM 21%, Asia 20%, overnight 2%.
Return decomposition (bp per day, 2010-2017): Asia (Tokyo 08:00+5m to London 08:00) +2.36 (t 2.45, n 2,054), London AM
-2.46 (t -2.39), NY 08:30-17:00 +0.03 (t 0.02), NY close to Tokyo +0.95 (t 2.51, sd 16.9), overnight (NY close to
NY 08:30) +0.88 (t 0.61), intraday NY +0.03. Random windows of the same length: Asia-length mean -0.44 (95% -5.2..4.9), London-length
-0.29, NY-length -0.01. Asia +2.36 is +1.1 control sd (about the 86th percentile), London AM -2.46 is -1.2 sd (about the 11th): the
Asia-up/West-down shape of G3 is faintly present in 2010-2017, below any cost (research-prior round trip 4-8 bp; H-04
net -0.093 R, #44). Fading overnight through the NY session: corr 0.036, pays -0.34 bp (t -0.20). Per year the London
AM sign is negative in 5 of 8 years; Asia positive in 6 of 8.

### 2.1 Fixes
The 10:30 and 15:00 London fixes show **no activity spike of their own**. Evidence: 15:00 London is 10:00 NY when the
DST regimes agree, and in the UK/US gap weeks it is 11:00 NY; the half-hour |30m| ratio at 15:00 UTC is 1.62 when that
is 10:00 NY (winter) but 1.06 in the gap weeks, where 15:00 UTC is the fix but not 10:00 NY (section 4). Caminschi and
Heaney's pre-2015 front-running is irrelevant to the post-2015 electronic auction (SoA B2). Verdict: close as a rule.

## 3. Asia range then London: breakout vs fade (2010-2017)

Asia range = Tokyo 08:00 to London 08:00 (8-9 h). From the London open on the 15m grid, the first 15m close beyond the
range (within 180 min) is the signal; enter at that close. Gross bp, held `hold` minutes.

| hold | n | with the break (bp) | t | failed breaks n | fade after close back inside (bp) | t |
|---|---|---|---|---|---|---|
| 60m | 1,196 | -0.08 | -0.16 | 384 | **-2.22** | -2.47 |
| 120m | 1,195 | +0.82 | 1.16 | 384 | **-2.78** | -2.21 |
| 240m | 1,196 | +0.36 | 0.34 | 384 | +0.91 | 0.38 |

Control (60 random starts 06:00-15:00 UTC, same 9 h lookback, hold 120m): with-break mean -0.32 (sd 0.76);
fade-after-failure mean +0.21 (sd 1.38). Break: nothing and no better than random (+0.82 vs control +/-0.76 sd, 1.5 sd).
Failed-break fade (negative = fading the break profits): -2.8 bp at 120m is **2.2 control sd** from the control mean,
t -2.2 on 384 events, one of six looks in this table. The breakout-versus-fade question has a faint answer: fade the
failures, never chase the break, which agrees with README 6.4 (first take-out continues +0.06 range units, 30% close
back inside) but contradicts H-48's gross -0.028 R on prior-day extremes at 15m (#88); different reference (Asia range)
and a different entry (closed back inside, not a wick). Not significant after looks; 48 events a year.

## 4. DST (UK and US change on different dates)

Weekdays 2010-2017: both summer 1,225, both winter 726, gap 135 (UK on GMT while the US is still on EDT: the weeks after
the last Sunday of October until the first Sunday of November, and from the second Sunday of March until the last
Sunday of March; in 2026 the gap runs 25 Oct to 1 Nov, i.e. now). Mean |30m return| / regime mean, by UTC slot:

| UTC slot | both summer | both winter | gap | which clock? |
|---|---|---|---|---|
| 07:00 | 0.96 | 0.70 | 0.79 | London 08:00 is 07:00 UTC in BST |
| 08:00 | 0.75 | **1.02** | **1.03** | London open at 08:00 UTC in GMT and in the gap |
| 12:00 | 1.37 | 0.83 | **1.46** | NY data 08:30 EDT in 12:30 UTC, gap follows NY |
| 12:30 | **1.71** | 0.96 | 1.39 | |
| 13:30 | 1.53 | 1.46 | 1.42 | |
| 14:30 | 1.30 | **1.64** | 1.37 | |
| 15:00 | 1.16 | 1.62 | 1.06 | London PM fix (15:00 UTC in the gap) shows no spike |

Both openings follow their own city's clock (README 6.9 for NY). In the gap weeks the London open to NY open distance is
4.5 h instead of 5.5 h, so a rule defined as "N hours after London" or in UTC fires 1 h off the NY burst for about 2 weeks
a year in October-November and 2-3 weeks in March. `session.py` v2 is correct (local clocks); the fixed-UTC arms of
`arms-2026-10-25.md` (S0 07:00-19:50 UTC, S5 07-20 UTC) are not: their windows slide against London by one hour in winter.
Flag for the director; no change made.

## 5. Opening-range breakout variants (2010-2017, net of $0.144 central cost charged at the entry; 1 trade/session)

Range = first 15/30/60 min after the London 08:00 or NY 08:30 open; entry at the first 15m-grid close beyond it (within
120 min); stop = opposite side or range mid; exit 2R target or after 240 min (stop first on a tie); control = same rule at
random starts 06:00-15:00 UTC (40 draws).

| variant | n | net R | t | control mean | % of controls below |
|---|---|---|---|---|---|
| London win30 stop-opp, 240m exit | 1,711 | +0.031 | 0.88 | -0.047 | 100 |
| London win15 stop-opp, 240m exit | 1,809 | +0.013 | 0.35 | -0.057 | 95 |
| London win30 stop-opp, 2R | 1,711 | -0.005 | -0.18 | -0.071 | 95 |
| NY win30 stop-opp, 2R / 240m | 1,486 | -0.011 / -0.015 | -0.39 / -0.45 | -0.065 / -0.057 | 95 / 88 |
| NY win60 stop-mid, 2R | 1,487 | -0.082 | -2.77 | -0.100 | 88 |
| London win60 stop-mid, 240m | 1,429 | -0.122 | -2.78 | -0.089 | 25 |
| all 24 variants | 1,077-1,809 | -0.122 to +0.031 | best t +0.88, worst -2.78 | | |

Net R is within +/-0.03 of zero for the opposite-side stop and negative for the mid stop (cost per R is larger on the
tighter stop). The variants beat random-start ORB (which loses 0.04-0.11 R to cost) but do not make money: an open is a
slightly better place to start than a random minute, nothing more. This confirms R-02 and README 6.3 on a different
cost basis. Do not re-propose 15m ORB. The 5m arms S2A/S2B remain the only open form.

## 6. First-hour intraday momentum (Gao-Han-Li-Zhou form) and the overnight tug of war

OLS of r(last 30 min) on r(first 30 min), White t, bp, 2010-2017; control = 400 random opens with random 4-9 h spans
(beta mean +0.010, sd 0.053; |t|>2 in 7% of draws; sign-pnl mean +0.04, sd 0.44 bp):

| pair | n | beta | t | sign(first) x last, bp |
|---|---|---|---|---|
| London 08:00 -> LSE close 16:30 | 2,066 | -0.023 | -0.57 | -0.29 |
| **NY 08:30 -> COMEX settle 13:30** | 2,034 | **+0.039** | **2.72** | **+0.73** (1.6 control sd) |
| NY 08:30 -> NY close 17:00 | 2,018 | -0.005 | -0.84 | -0.30 |
| US equity open 09:30 -> 16:00 | 2,019 | +0.008 | 0.44 | +0.43 |
| Tokyo 09:00 -> 15:00 JST | 2,059 | +0.006 | 0.20 | +0.01 |
| Tokyo 09:00 -> London 08:00 | 2,059 | -0.010 | -0.30 | -0.28 |

One pair of six has the Gao sign at t 2.7 (R^2 0.43%, adding the penultimate half-hour: beta_12 0.096, t 2.2); it is the
window with a real mechanism (a COMEX settlement window and equity-derivative hedging after the pit open), and it is the
only one. R-05 (#18, #19: -0.006 R NY) is consistent: 0.7 bp is about 0.01 R at a 70 bp day stop. Literature: Gao et al.
(JFE 2018) find the effect on SPY and 10 other ETFs, stronger on high-volatility and macro-release days, and attribute
it to infrequent rebalancing and late-informed trading; neither mechanism exists in spot gold's OTC market, and the
COMEX close auction is a settlement, not an equity MOC. The overnight versus intraday split (Lou, Polk, Skouras, JFE 2019)
needs a persistent cross-sectional pattern; gold shows overnight +0.88 bp (t 0.61) against intraday +0.03: no tug of war.

## 7. Peeked tables (2020-01-01..2026-10-23; same-sign check only, no confirmation credit)

| statistic | 2010-17 | 2020-26 | same sign? |
|---|---|---|---|
| LBMA AM fix reversal (bp, t) | -0.63 (-2.3) | -0.91 (-2.9) | yes |
| COMEX settle reversal | -0.83 (-2.9) | -0.53 (-1.5) | yes |
| Gao NY 08:30 -> 13:30 beta (t) | +0.039 (2.7) | +0.027 (1.3) | yes, weaker |
| Failed Asia-break fade, 120m (bp) | -2.78 | -0.90 | yes, weaker |
| London AM drift (bp/day) | -2.46 | +0.41 | **no** |
| Asia drift (bp/day) | +2.36 | +3.24 | yes; random-window control +2.24 (gold uptrend): not distinguishable |
| NY close to NY 08:30 (overnight) | +0.88 | +5.43 | trend-driven; control not run for this length |
| ORB 24 variants net R | -0.12..+0.03 | -0.14..+0.02 | both nil |

The 2020-26 overnight number (+5.4 bp/day, t 2.9) is the whole 2020-26 uptrend (about 5.4 bp a day) captured outside NY
hours; the same tape paid ~0 inside NY hours, with 2026 intraday -12.6 bp/day. It is a trend fact, not a session edge, and the
holdout is in it.

## 8. Already tried, and what this adds

Tried (README 3): session-open breakout 15m R-02 (#1,#7,#11,#13), intraday momentum R-05 (#18,#19), Asia drift H-04
(#44), sweep-reclaim H-48 (#88), pre-FOMC/fix front-running R-08, S1A/S2A/S3A arms. New here: a drift-neutral random-time
control for every clock statistic, the cost-in-R at today's prices, the DST-regime clock test, the fix-versus-10:00-NY
disentangling, the failed-Asia-break fade by hold, and an explicit negative on all 24 ORB variants net of the measured
cost. Stopped: ORB 15m (all variants), London/NY open breakout, fix and COMEX-settle trades, overnight-versus-intraday
as a rule, Asia/West drift as a rule (H-04 stands).

## 9. Literature (peer-reviewed first; checked ones marked)

- Gao, Han, Li, Zhou, "Market intraday momentum", J. Financial Economics 129(2), 2018, 394-414 (checked via
  https://profiles.wustl.edu/en/publications/market-intraday-momentum/): SPY 1993-2013 plus 10 ETFs; stronger on
  volatile, high-volume, recession and macro-release days. Grade A for equities, C for gold.
- Lou, Polk, Skouras, "A tug of war: overnight versus intraday expected returns", JFE 134(1), 2019, 192-213 (checked,
  https://researchonline.lse.ac.uk/id/eprint/87481): cross-sectional equities; profits in one component, opposite
  signs. Not transferable to a single asset.
- Zarattini, Aziz, Barbon, "Beat the Market: an effective intraday momentum strategy for S&P500 ETF (SPY)", Swiss
  Finance Institute 24-97, 2024 (checked, https://www.sfi.ch/de/publications/n-24-97-beat-the-market-an-effective-intraday-momentum-strategy-for-s-p500-etf-spy):
  noise-area breakout, SPY only, 14-day lookback; the basis of H-17.
- Caminschi and Heaney, "Fixing a leaky fixing", J. Futures Markets 34(11), 2014 (SoA B2): pre-2015 front-running;
  procedure reformed.
- Cai, Cheung, Wong, "What moves the gold market?", J. Futures Markets 21(3), 2001 (SoA B2): intraday periodicity and
  US-release volatility; consistent with the 08:30 NY burst here.
- Batten, Lucey, McGroarty, Peat, Urquhart, PLoS ONE 12(4), 2017; Lucey, Larkin, O'Connor, Applied Economics Letters 2013
  (`knowledge-base/gold-specific.md`): n-shaped volume, no permanent price leader.
- Osler, "Stop-loss orders and price cascades in currency markets", J. Int. Money and Finance 24(2), 2005 (cited in
  `arms-2026-10-25.md`): clustering at opening extremes; mechanism for the failed-break fade.
- Andersen and Bollerslev, "Intraday periodicity and volatility persistence in financial markets", J. Empirical Finance 4,
  1997 *(unverified)*; Breedon and Ranaldo, "Intraday patterns in FX returns and order flow", JMCB 45, 2013
  *(unverified)*: currencies depreciate in their home hours, the closest analogue to the Asia-up/West-down pattern.

No new durable knowledge-base note: the DST and fix evidence is in README 6.9 and section 4 here.

## 10. Candidate hypotheses (ranked; trial budget 2026Q4 = 0, 2027Q1 free reserve = 6, K = 145, gate-1 t about 4.3)

None has a plausible gross edge above 3x the research-prior cost (4-8 bp in 2010-2017, about 1 bp today), and none
clears Bonferroni, so **no candidate here is recommended for a free-reserve slot as a rule-only trial**. If the director
must spend one, it is A-1. Everything else is a feature, a filter or a drop.

### A-1  Failed Asia-range break fade (London)  [rank 1; the only one worth a slot, as an arm first]
- Mechanism: stops resting beyond the Asia extreme trigger a burst; when the 15m close returns inside the range the
  stop-run is exhausted (Osler 2005). Grade C. Already tried?: new on reference and entry (H-48/R-14 faded wicks at
  prior-day extremes); S3A arm is the 5m form.
- TF and clock: 15m; Asia range = Tokyo 08:00 to London 08:00 local; signal window London 08:00-11:00 local.
- Entry: first 15m close beyond the range, then if the close 30 min later is back inside, enter against the break at that
  close (known on that bar). Side: fade. Exit: stop at the break extreme + 0.25 ATR15; target the opposite range edge
  or 120 min; one position.
- Control: random-start 9 h range, same rule (fade mean +0.21 bp, sd 1.38); needs 2 sd.
- Discovery evidence: Section 3: -2.78 bp, t -2.2, n 384, six looks. Cost check: 2.8 bp is about $0.39 at 1,400 and
  $1.1 at 4,000; vs research prior (about $0.65 in 2010-17) 0.6x, vs broker $0.144 at today's price 7x.
- Expected events: 384 in 2010-17 = 48/yr = 4/month (below the 150/1,000 floor: it cannot be a gate-1 trial).
- Kill rule: 0-trial diagnostic on the confirmation half is not allowed before pre-registration; kill if the
  discovery effect is < 1.5 control sd on a day-clustered re-estimate or the 5m arm S3A shows net <= 0 after 150 fills.
- Route: exploration arm on icm-probe (S3A variant "Asia range"); not a Q1 trial. P(continue) 10%.

### A-2  NY pit continuation to the COMEX settle (Gao form)  [rank 2; feature]
- Mechanism: post-data position adjustment and settlement-window hedging. Grade C. Already tried?: R-05 (Gao form on
  the whole NY session and last 30 min of the session; this variant is the 08:30-13:30 pit window, new).
- TF: 15m/30m, signal = r(08:30-09:00 NY), exit at 13:30 NY. Entry: 09:00 NY close in the sign of the first half hour.
  Exit: 13:30 NY or stop 1.5 ATR15. Control: random opens/spans (beta sd 0.053).
- Discovery: beta +0.039, t 2.7, sign pnl +0.73 bp (1.6 sd), n 2,034 (about 21/month). Cost: 0.73 bp = $0.10 in
  2010-17, $0.29 now (2x broker): below the 3x rule; R about +0.01.
- Kill rule: sign pnl < 0.5 bp or t < 2 in a day-clustered re-estimate. Route: `r_first_30` as an H-02 feature;
  0 trials. P(continue) 5%.

### A-3  Cost-window execution rule  [rank 3; filter]
- 08:00-11:30 NY and 12-15 UTC have the highest range per cost; avoid 16:55-17:00 and 18:00-18:30 NY (Dukascopy
  spread x1.4). Entry timing only, not a signal; measure it on arm fills (S0/S0R) as a 0-trial diagnostic: median
  fill cost in R per window. Kill: no window differs from the pool at the 95% bootstrap. P(useful) 30%, saving at most
  0.01-0.02 R per trade.

### A-4  Local-clock audit  [engineering, not a hypothesis]
- Verify every rule anchored in UTC (arm filters 07-20 UTC) against the October and March gap weeks (section 4); use
  `session.py` v2 local clocks. Route: owner of the arms.

### Dropped (0 trials, reason)
- 15m ORB at London/NY, all 24 variants (section 5): net R -0.12..+0.03 vs random-start control also about -0.07; R-02.
- London/NY AM fix and COMEX-settle trades: reversal 0.6-0.8 bp, t -2.3 to -2.9, 0.1 R or less, 5%/3% of control.
- Asia/West session drift, overnight versus intraday (H-04 stands; Lou-Polk-Skouras not applicable to one asset).
- Chasing the Asia-range break: no edge and no better than random.
- NY close/rollover trading: the feed stops 17:00-18:00 NY; spread x1.4 after reopen.

## 11. What I would stop doing, and looks taken
Stop: session-open breakouts at 15m, any UTC-hour filter on a rule meant to follow London or NY, treating the fix as an
event. Looks: sections 2-6 about 70 discovery statistics (Bonferroni |t| about 3.3); the same again on 2020-26 (seen).
