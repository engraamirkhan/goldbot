# Algo survey, slice 2 of 3: retail and commercial gold/FX systems with verified records

Date: 2026-11-13. Author: strategy-researcher-staff (read-only survey; no repo changes).
Scope: MQL5 Market/Signals, Myfxbook, Darwinex, Collective2, ZuluTrade/eToro, prop firms, well-known gold EAs.

## 0. Headline and data caveat

No platform (MQL5, Myfxbook, Collective2, ZuluTrade, eToro, FTMO) publishes a census of how many
signals/systems survive. The only first-party population numbers found are: ESMA/broker CFD loss
disclosures, Topstep's 2025 risk disclosure, and Darwinex's monthly DarwinIA allocation reports. Everything
else below is either a vendor figure, an informal tally, or a single documented case. Each claim is tagged:
[1P] first-party/regulator, [IND] independent analysis, [ANEC] forum/informal tally, [VEND] seller/affiliate.

## 1. Base rates (what the population looks like)

| Population | Figure | Tag | Source |
|---|---|---|---|
| Retail CFD accounts losing money (12-month window) | 74-89 % (ESMA 2018 range); broker-specific figures now mandatory | 1P | https://www.esma.europa.eu/sites/default/files/library/esma35-43-1135_notice_of_pi_decisions_on_cfds_and_binary_options.pdf |
| Topstep combines passed (2025) | 16.8 % of combines; 51.8 % of individuals eventually pass (many buy several) | 1P | https://www.topstep.com/topsteptv-213k-subscribers-extravaganza (disclosure footer); https://tradecovex.com/guides/how-many-combines-passed-express-topstep-2025 |
| Topstep funded-level individuals paid at least once (2025) | 33.3 %; ~0.7 % reach a live account | 1P via secondary | https://www.alphaexcapital.com/prop-trading/topstep-review |
| CFD prop firms (FTMO etc.) | FTMO publishes no pass rate; estimates 10-12 % pass both phases; ~7 % of all buyers ever paid; 1-3 % consistently funded long-term; 60-70 % of failures are drawdown breaches, not running out of time | VEND/IND estimates | https://track360.io/blog/prop-trading-industry-statistics-2026 ; https://www.jptradingcapital.com/blog/en/ftmo-challenge-pass-rate-statistics |
| Myfxbook "tracked" systems profitable | 313 / 799 = 39 % (snapshot, survivors only) | ANEC | https://nexusfi.com/showthread.php?p=648530 |
| Collective2 strategies aged 5+ years | 19 found on old-timer board; all 19 had at least one losing calendar year; 2009 CXO study: young systems have "high mortality" | ANEC/IND | https://forums.collective2.com/t/are-there-any-c2-strategies-that-stay-profitable/16231 ; https://www.cxoadvisory.com/?p=4128 |
| Darwinex DarwinIA SILVER (Sept 2025) | 1,746 of 9,215 DARWINs (19 %) got allocation; recipients' median 6-month return 10.9 %, median 6-month DD 5.3 %, median month 3.2 % | 1P | https://www.darwinexzero.com/blog/results-of-darwinia-in-september-2025 |
| Darwinex DarwinIA GOLD (needs 8+ months, 1y return > 20 %, return/DD > 2.5) | 104 of 345 allocated; median month 4.15 % (a selected best-month cohort, not an average month) | 1P | same + https://help.darwinex.com/darwinia?hsLang=en |
| ZuluTrade top-5 ranked providers | max DD 35-108 %; "good for ~6 months then lose it all in one go" | ANEC | https://forums.babypips.com/t/i-have-noticed-something-very-strange-on-zulutrade/83821 |
| Social-trading copiers (large platform, Joseph, Riedl, Pentland, Moro 2025) | mirrored trades underperform; followers pick leaders by popularity, poorly correlated with performance; per-trade mirrored ~ -61 bps | IND (arXiv) | https://arxiv.org/abs/2507.01817 |
| eToro Popular Investors | 2020: eToro claimed 95 % profitable (bull year, self-reported, no copier-net data) | VEND | https://www.etoro.com/news-and-analysis/copytrader/popular-investors-with-3-or-more-years-profit |
| Copy trading (Apesteguia, Oechssler, Weidenholzer 2020, Management Science) | the ability to copy leads to excessive risk taking (experiment) | IND | https://bse.eu/research/publications/copy-trading |

Survival estimate for MQL5 signals: no measured figure exists. MQL5's own bloggers suggest using "share of
listed signals older than 2 years" as a proxy (https://www.mql5.com/en/blogs/post/777995). A search for gold
signals running since 2019 found one (327 weeks, +13 % total, 60 % losing trades, style changed)
(https://www.mql5.com/en/signals/2337857); nearly every visible gold signal started in 2024-2026. Combining the
prop-firm numbers (1-3 % long-term) and the Darwinex allocation rate (~19 % get any allocation at all),
**a fair working prior is that under 10-20 % of publicly launched retail gold/FX systems are still alive and
net positive after 2 years, and under 5 % with drawdown below 25 %.** That is an inference, not a measurement.

## 2. By category: what survives and what kills

### 2.1 Grid / martingale / averaging (most MQL5 best-sellers historically; Waka Waka, Tokyo EA type)
- Shape: 85-95 % win rate, smooth curve, average loss 3-5x average win, profit from many small mean-reversion closes.
- Kill mechanism on gold: one sustained trend. 30 Jan 2026 gold fell ~9.5-9.8 % in a day (largest since 1983).
  Arithmetic on a $10k account at 0.01 starts: a flat $10-step grid is wiped; **any lot multiplier wipes the
  account after a 1.6-6 % straight move** (https://www.mql5.com/en/blogs/post/778166). Margin hikes after the
  event raise the stop-out risk further.
- Controlled test, 172 backtests on gold/USDJPY/indices 2013-2026: martingale lifts "% of start dates in profit
  after 90 days" from 46 % to 59 % but multiplies max DD 4.1x and worst trade 5.2x; return/DD unchanged;
  **sizing never created an edge (sign unchanged in 53 of 60 comparisons)** (https://www.mql5.com/en/blogs/post/779359).
- Practitioner pattern: accounts "survive for months then get wiped in a single bad week"
  (https://www.mql5.com/en/forum/502291). Waka Waka (FX crosses, not gold) is the best-known long-lived grid
  (since 2018) and still carries MQL5 "high current drawdown / no risk limitation" warnings
  (https://www.mql5.com/en/signals/995287; review: https://newyorkcityservers.com/blog/waka-waka-ea-review).
- Verdict: survives only on range-bound crosses with tiny exposure and periodic withdrawals; on gold the
  expected life is months.

### 2.2 Breakout (Gold Reaper type, session/range breakouts)
- Gold Reaper: 9 breakout sub-strategies, stop on every trade, no grid. Backtest 2020-24: PF 2.72, DD 12.4 %.
  Live moderate-risk Myfxbook: **PF 1.08, max DD 41.7 %, avg win $20.9 vs avg loss $51.1**
  (https://newyorkcityservers.com/blog/the-gold-reaper-review). Backtest DD understated live DD ~3.4x.
- Real-tick evidence (vendor, qualitative): tight-stop breakouts get wicked out and thin-TP scalps are eaten by
  spread/slippage; wide ATR/daily-range stops hold up (https://www.mql5.com/en/blogs/post/774275).
- Verdict: honest stop-based breakouts survive but live PF collapses toward 1.0-1.3; drawdowns 30-40 %.

### 2.3 Trend / long-biased gold
- 2022-2026 gold bull flatters any long-biased system. One seller's disclosure: their gold trend EA lost in
  2019 (PF 0.90, -4 %), 2020 (PF 0.71, -17 %) and 2021 (PF 0.57, -30 %); "edge comes from the regime since 2022"
  (seen in the MQL5 product listing search for Gold Pivot Trend Follower,
  https://www.mql5.com/en/market/product/185372 listing page).
- Long-only gold EA with "722 %" headline: absolute gain 55 %, max DD 38.8 %, 92 % win rate with avg loss 4.4x
  avg win (https://www.mql5.com/en/blogs/post/774361). Headline % is time-weighted after withdrawals.
- Verdict: the main survivorship trap of 2024-2026 gold signals. Any record that starts after 2022 and is
  mostly long is a beta-to-gold record, not an edge. Require a 2019-2021 or a short-side test.

### 2.4 News trading
- Most prop firms restrict or ban holding through news on funded accounts (FTMO: no news/weekend holds on the
  standard account) (https://marketmates.com/learn/swing-trading-prop-firms/). Stops slip in fast markets;
  spreads widen several-fold (https://www.mql5.com/en/blogs/post/778166). No verified long-lived gold news EA found.
- Verdict: honest winners mostly avoid or flatten around tier-1 news rather than trade it.

### 2.5 Scalping (M1/M5)
- Prop-firm dataset (Funded Trading Plus via FXEmpire, "hundreds of thousands of accounts, tens of millions of
  trades", promotional, success = pass or 1 withdrawal): traders holding <30 min are **up to 8x less likely to
  succeed**; successful traders risk <=1 % per trade, unsuccessful >2 %; stops used; 2+ markets
  (https://www.fxempire.com/news/article/the-2026-trading-roadmap-propiq-reveals-the-4-critical-habits-that-create-8x-more-successful-traders-1568430).
- FXCM 43 M real trades (2014-15): traders win >50 % of trades but lose overall (EURUSD avg win 43 pips vs
  avg loss 83); trades at <5:1 effective leverage profitable 61 % of the time, >25:1 the worst
  (https://www.fxcm.com/uk/trading-guides/traits-successful-traders/ ;
  https://www.fxstreet.com/education/trading-leverage-a-real-look-at-how-traders-may-use-it-effectively-201606210841).
- Verdict: scalps survive only where costs are a small share of target; matches goldbot's break-even 1m/5m book.

### 2.6 Diversified, risk-normalised (Darwinex DARWINs)
- Darwinex rescales every DARWIN to 3.25-6.5 % monthly 95 % VaR (https://help.darwinex.com/risk-manager).
  Under that cap, the allocated (better) cohort earns a median ~1.8 %/month (10.9 % over 6 months) with ~5 %
  6-month DD; the best-month GOLD cohort median 4.15 %.
- Verdict: this is the cleanest public picture of what "good" looks like at sane risk: 1-4 %/month.

## 3. Red flags (checklist)
1. Lot size grows after a loss or as price moves against (martingale, averaging, "recovery").
2. Win rate >85 % with average loss >2x average win, or no stop in trade history.
3. Max DD in backtest << live DD (Gold Reaper 12 % vs 42 %); fixed-spread / non-real-tick backtests.
4. Headline (time-weighted) gain >> absolute gain; deposits/withdrawals hiding the base.
5. Track record < 12 months, or starting after 2022 and mostly long gold.
6. "Style changed" / "part of history not included" / provider debarred notes on MQL5 signals.
7. Current floating DD hidden (closed-trade stats only; MQL5 stats are built from closed history).
8. High deposit load (>30-50 % margin used).
9. Leader ranking by popularity or follower count instead of risk-adjusted net return.
10. Vendor-owned review sites and affiliate "verified" claims.

## 4. Common traits of the honest long-term survivors
- Fixed risk per trade 0.5-1 % (prop data; Darwinex VaR cap), hard stop on every position, no lot escalation.
- Holding periods of hours to days (intraday-to-swing, M15-H4 signals), not seconds/minutes; few trades
  (a handful per day at most, often "nine trades this month").
- Stops sized by volatility (ATR, daily range), wide enough to survive a wick; target several times the round trip.
- Flat or reduced around tier-1 news and weekends; daily loss limit honoured (60-70 % of prop failures are DD breaches).
- Multiple uncorrelated markets or sub-strategies; losing years still occur (all 19 C2 5-year survivors had one).
- Returns of 1-4 %/month with 10-25 % max DD; anything above ~10 %/month in a verified record either has
  30-100 % DD or is younger than its first regime change.

## 5. Realistic monthly returns at sane drawdown
| Max DD tolerated | Realistic verified monthly return | Evidence |
|---|---|---|
| ~10 % | 1-2 % | Darwinex SILVER median (10.9 %/6 m, DD 5.3 %) |
| ~20-25 % | 2-4 % | Darwinex GOLD bar (1y > 20 %, R/DD > 2.5); seller snapshot +4.2 %/m at 8.6 % DD (young) |
| 35-50 % | 5-10 % (short-lived) | Gold Reaper / long-only gold EA / ZuluTrade tops |
The owner's 20-30 %/week target (~100-300 %/month) has no verified long-lived precedent in any category; at
that rate the reviewed populations show blow-up within months. Flag for the principal.

## 6. Implications for goldbot (for the principal to decide)
1. Move the bulk of trading from 1m/5m scalps to M15-H4 holds (hours-days) with ATR stops and targets >= 3-5x
   the round trip; the evidence (prop data, FXCM, real-tick commentary) and our own break-even book agree.
2. Keep risk per trade fixed at 0.5-1 % with no lot escalation, plus the daily loss cap; reject any candidate
   whose edge depends on averaging/martingale (sizing does not create edge).
3. Add a regime/beta check to every gold candidate: require a positive result in 2019-2021 or on the short side,
   because 2022-2026 long-gold beta is the dominant false positive in public gold records; flatten around tier-1 news.
