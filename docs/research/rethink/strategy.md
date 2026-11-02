# Rethink, lane STRATEGY: where gold edge is, why ~3,330 trials found none, what changes this week

Head of research (rethink lead), 2026-11-02. Read-only on outcomes: every number below is cost arithmetic, a
volatility measurement, a power calculation or a result already published by an earlier lane. No new strategy
outcome was computed, so this document adds **0 to K**. Candidates: `config/research/candidates/strategy.yaml`
(60 rules, RT-STRATEGY-001..060).

## Numbers first

| # | Number | Source |
|---|---|---|
| 1 | A strategy with a **true annual Sharpe of 0.5** (the size of a good single-market trend rule) passes our explore -> confirm -> TEST-OLD chain **4.4% of the time**; Sharpe 0.4: 1.8%; Sharpe 1.0: 53% | Appendix C, simulation of the W2 rules |
| 2 | The sprint-3000 confirm bar "DSR > 0 at program K" (K about 6,233) needs an observed annual **Sharpe of about 1.34** over 7.75 years on one gold rule | Appendix C, expected maximum of K null t-statistics |
| 3 | Published single-market trend Sharpe, 67 markets, 1880-2016: **about 0.4 on average**; the diversified portfolio of the same rules is far higher | Hurst, Ooi and Pedersen 2017 (via CXO summary) |
| 4 | Cost of one ICM round trip plus stop overshoot, per R at a 1 x ATR(14) stop, 2010-2017 prices: **15m 0.188 R, 1h 0.085 R, 4h 0.043 R, 1d 0.018 R, 1w 0.006 R** | Re-derived here from raw Dukascopy 1m, cor-2026-10-31.2 (Appendix B) |
| 5 | The shipped long swap, -$0.60/oz/night, is **5.5% a year at $4,000 but 17% a year at the 2010-2017 median of $1,287**. Rates were near zero then. Every multi-day explore result before W2 charged holds about **3x today's rate**, and probably 5x the real rate of the time | `config/settings.yaml:141`; `scripts/sprint200/trend_screen.py:934`; H-84 V3: net -0.054 R with swap, +0.097 R without |
| 6 | Sprint-3000 catalogue: **87% of the 3,000 specs are 15m-4h**. 1d holds are capped at 20 bars and 4h holds at 30 bars. There are no position, multi-asset or volatility-scaled designs | `config/research/sprint3000_catalogue.yaml`, counted here |
| 7 | At the minimum lot, 0.01 = 1 oz = about $4,000, i.e. **about 3x a £1,000 account**. One daily sigma is about $37 (£28). A 2 x ATR(1d) stop is about $94 (£70), **7x the 1% cap**. Swing trades cannot be sized on £1,000 under RiskGate; they can on the £10k demo | Appendix B; GBP/USD 1.33 assumed |
| 8 | £50 a day on £1,000 is 5% a day. A Sharpe-0.5 gold trend at the minimum lot expects about **£220 a year, or £0.9 a day** | Arithmetic: 1 oz carries 14.6% vol on $4,000, i.e. $584 a year; x 0.5 = $292, about £220 |

## (1) What edges exist in spot gold, for us

Detail and grades are in Appendix A.

| Edge | Horizon | Evidence | Viable at our costs? |
|---|---|---|---|
| **Trend / time-series momentum** | 1 week to 12 months | Peer-reviewed, many decades, all asset classes (Moskowitz, Ooi and Pedersen 2012 JFE; Hurst, Ooi and Pedersen 2017) | **Yes.** Round trip is about 0.02 R at 1d. The binding cost is financing, not spread |
| Volatility-managed exposure | Daily rebalance | Peer-reviewed. For commodities it does **not** raise Sharpe, but it cuts tails (Harvey et al. 2018 JPM) | As risk control, not as an edge |
| Safe-haven flow after equity crashes | 1-15 days | Peer-reviewed (Baur and Lucey 2010) | Yes, but rare (about 10 events a year) |
| Event drift (post-FOMC) | Hours to days | Own data: t 3.98, 8 of 8 years in explore. H-89 confirm due 2026-11-04 | Yes, about 8 trades a year |
| Macro (real yields, USD) | Weeks | Strong in levels 2003-2021, broke in 2022 (central-bank buying). H-84 failed as a bracket rule | Weak alone. One input to a blend |
| Positioning / ETF flows | Weeks | Mixed: returns lead positions (de Roon et al.). We hold CFTC data from 1986 and SPDR data from 2004 locally | Weak. Input to a blend |
| Seasonality (autumn) | Months | Peer-reviewed 1980-2010 (Baur 2013); not confirmed in a 2024 replication | Cheap screen only |
| Carry | n/a | Gold carry is the financing rate: longs pay it in every venue | Not an edge. It is a cost to minimise |
| Options volatility premium | Weeks | Practitioner. Option desks earn it | No options at our venue or size |
| Intraday patterns, scalping, sessions | Minutes to hours | Our own data: about 0 gross across thousands of trials | **No.** Costs are 0.09-0.19 R |

**Where professionals make money.** Bullion banks earn from spread, flow, financing, storage and location arbitrage. JPMorgan's metals desk made $109-234m a year from 2008 to 2018 and about $1bn in 2020 on the COMEX/London dislocation (Reuters, via mining.com). We pay that spread; we cannot earn it. CTAs earn the trend premium. They hold gold as one of 50-100 futures markets, risk-scaled, for weeks to months, and most of the P&L comes from a few long trends each year (2001-11 long, 2013 short, 2019-25 long). Their edge is breadth times holding period, and **our design removed both**. It tested one market, cut every trade with an R target or a time exit inside 1-5 days, and charged multi-day holds 3x the swap.

## (2) Why the search produced 0, in plain words

1. **The bar was higher than any real edge.** One gold rule needs about Sharpe 1.3 to clear DSR at K 6,233. Real ones run at 0.3-0.6, so a genuine edge had a 2-4% chance to qualify (numbers 1-3 above). Each extra near-duplicate trial raised the bar for every honest one. We bought K and spent power.
2. **We searched where there is no edge.** Most trials were 15m-1h entry patterns. There costs take 0.09-0.19 R, while gross was at most +0.05 R (number 4).
3. **Where there was gross edge, the cost model was wrong against it.** 1h-4h trend was gross-positive (11 of 34 at t >= 2 in sprint-200 trend). The swap that killed it was charged at 17% a year (number 5).
4. **The trade shape cut off the pay-off.** Fixed R targets, 20-bar caps and same-side "twin" controls measure entry timing. A trend edge lives in direction and holding. A same-side twin shares the direction, so its excess is close to 0 by construction.
5. **One market, eight years, one regime per window.** Explore 2010-17 was a bear market and confirm 2018-25 a bull. n >= 100 trades fails every honest monthly rule on count.

None of this says an edge exists. It says **our test could not have found the one that the literature says exists**.

## (3) What to change

- **Horizon.** Make 1 day to 3 months the primary horizon: a daily decision with a continuous target exposure. Use 4h only to time entries. Freeze new 15m-1h entry-pattern trials; only exits and costs may be tested at those timeframes.
- **Portfolio of weak signals, one trial each.** Fix ex-ante, equal-risk blends: trend at 3 speeds, plus real yield, USD, positioning and seasonality. Count each blend as **one** trial; never fit weights. Across markets on the same ICM account (silver, platinum, FX majors, indices, oil), Sharpe scales as SR x sqrt(N / (1 + (N - 1) rho)). For a 0.4-Sharpe rule that is 0.62 at N 6 and rho 0.3, and 0.92 at N 10 and rho 0.1. That is the CTA route. Trading more than gold is a scope decision for PD + PO. It costs no money.
- **Volatility targeting.** Use it as sizing (10-15% a year per position) and at portfolio level, where it helps (Harvey et al. 2018). It runs no separate trials. On £1,000 the minimum lot makes it impossible (number 7).
- **Pass rule (amendment W3, to freeze before any run).**
  - (a) A **power gate**: every pre-registration states its minimum detectable Sharpe. If a literature-sized effect has power below 50%, redesign (more history, pooled markets) rather than run.
  - (b) Position specs are read on **daily P&L** (Sharpe, day-block bootstrap) with **financing by date** (FRED 3-month bill + the measured ICM markup), under three venue scenarios.
  - (c) The **null for position strategies** is a sign-randomised exposure on the same schedule, plus a buy-and-hold drift benchmark. Same-side twins are not the null for these.
  - (d) BH-FDR applies inside each pre-registered batch, as W2 already does. Program-K DSR stays the **live-money** gate, which is the owner's.
  - (e) n >= 30 round trips **and** >= 500 days in market replaces n >= 100 trades for 1d+ specs.
  - Nothing is loosened silently: K counts every spec, and the holdout stays sealed.
- **Venue and instrument.** This is owner-only spending; options with public numbers are in Appendix D. Financing is not avoidable: a futures long pays it in the curve, and an ETC holder forgoes interest. A venue only saves the broker's markup (about 1.5-2.5% a year) and lets shorts earn carry.
  - **(A) Recommended now: stay on ICM, no spend.** Test under all three cost scenarios and run position arms on the £10k demo, where the 1% cap allows 1 oz at a 2 x ATR(1d) stop.
  - (B) Long/flat trend on a UK physical gold ETC (SGLN or SGLD, 0.12% a year). There is no financing, any size works, and it fits £1,000-£20. It is long-only.
  - (C) CME 1-ounce futures (1OZ; margin about 5-10% of notional per CME). Shorts earn carry. Fees are about 10x ICM per oz (estimate, unverified), which is fine at 1d+.
  - Swap-free accounts (ICM, Vantage) are for clients with a religious basis, and ICM charges a holding fee after a grace period. **Declined** unless the owner is eligible.
  - Recommendation: (A) until a position design qualifies, then (B) for real money below about £7,000.

## (4) Seven-day program, ranked (2026-11-02 to 11-09)

| # | Step | Owner, mode | Due | Accept / kill |
|---|---|---|---|---|
| 1 | Freeze W3 (power gate, daily-P&L read, financing by date, sign-randomised null, 3 venue scenarios) | research-director + quant-reviewer, docs | Tue 11-03 | Committed before any POS-1 bar is read |
| 2 | Daily **position engine** `goldbot/research/position/`: target exposure, vol scaling, no-trade band, multi-asset, financing by date. Parity with `triple_barrier_next_open_1m` on 1d 0/1 exposure | implementer, worktree | Wed 11-04 | Parity test green; nothing at or after 2025-10-01 readable |
| 3 | Data: XAU, XAG, XPT, 7 FX majors, US500, GER40, WTI from Dukascopy 1m into daily (2003-2025-09); FRED DTB3, DFII10, DTWEXBGS, T10YIE; CFTC and SPDR already local | data-engineer, local only | Wed 11-04 | sha256 manifest; nothing committed |
| 4 | **Batch POS-1**: 16 specs from the candidate file (001, 002, 004, 006, 008, 015, 016, 025, 026, 027, 033, 055, 056, 057, 059, 060), **K +16**. Explore, then confirm (m fixed), then TEST-OLD 2006-09 | strategy-researcher, worktree, 2 workers | Thu 11-05 | W3. Each failure is retired. Expected: P(at least 1 qualifies) about 30-45%, almost all from the multi-asset books |
| 5 | H-89 post-FOMC confirm (already pre-registered) | macro-researcher | Wed 11-04 | Frozen rule in decision-2026-11-01 |
| 6 | **Position arm** on icm-demo: daily rebalance at 0.01-lot steps, RiskGate unchanged; runs only POS-1 qualifiers plus a sign-randomised control arm | implementer + trading-safety-reviewer | Fri 11-06 | Arm live for qualifiers by Sat 11-07; none if none qualify |
| 7 | Friday read: POS-1 table (losers beside winners), K, the venue decision memo to the owner | research-director | Fri 11-06 | Posted on #34 |

**Kill rule for the line.** If 0 of 16 qualify, and the multi-asset books 026, 027 and 057 all fail confirm, the position line retires. The decision memo's option A (pause new hypotheses) then becomes my recommendation to the owner.

**Deliverables:**
- W3 amendment
- position engine and tests
- daily data manifest
- POS-1 pre-registration and results
- position-arm code
- demo arms for qualifiers
- Friday read
- venue memo with options (A)/(B)/(C) and a recommendation
- this document and the candidate file.

---
## Appendix A: evidence by edge (grade, mechanism, where it is earned)
- **Time-series momentum.** Peer-reviewed.
  - Moskowitz, Ooi and Pedersen 2012 (JFE 104:228-250) find significant TSMOM in all 58 futures, 1985-2009, gold included. Hurst, Ooi and Pedersen 2017 cover 67 markets back to 1880: the average single-market gross Sharpe is about 0.4, and the diversified portfolio is higher net of 2/20 fees.
  - Mechanism: slow diffusion of information, then herding, plus hedgers paying for insurance.
  - Caveat: Kim, Tse and Wald argue much of TSMOM's Sharpe comes from volatility scaling.
- **Volatility targeting.** Harvey, Hoyle, Korgaonkar, Rattray, Sargaison and Van Hemert 2018 (JPM): it improves Sharpe for equity and credit only; for commodities the Sharpe change is negligible, but the left tail is lower. Portfolio-level scaling helps.
- **Safe haven.** Baur and Lucey 2010 (Financial Review): gold acts as a safe haven for about 15 trading days after extreme equity falls.
- **Seasonality.** Baur 2013 (RIBAF 27:1-11): September and November are the only significantly positive months, 1980-2010. Potrykus and Augustynowicz 2024 could not confirm it. Grade: folklore-plus.
- **Macro.** Erb and Harvey 2013 ("The Golden Dilemma") on the real-yield link. Reported weekly correlation is about -0.30 before 2022 and -0.25 since (secondary source). Our H-84 failed as a bracket rule, but it was costed with the 17%-a-year swap.
- **Positioning.** De Roon, Nijman and Veld find hedging pressure moves futures returns, but other work finds that returns lead traders' positions. There is no gold-specific out-of-sample study (practitioner grade).
- **Intraday momentum.** Gao, Han, Li and Zhou 2018 (JFE) is on equity index ETFs. In Chinese gold futures the effect depends on the session. Our 15m-4h screens: about 0 gross after the leak-free control.
- **Session drift (Asia up, US down).** Evidence is a Copenhagen Business School thesis and a secondary report of a World Gold Council note. Our time-of-day screens: about 0 gross. Retired.
- **London fix.** Caminschi and Heaney 2014 (JFM) found information leaking around the old PM fix, a volume and volatility effect. The fix has been an IBA auction since 2015. No tradable return effect for us.
- **Bullion banks.** JPMorgan's metals profit was $109-234m a year 2008-18 (court testimony) and about $1bn in 2020 from storage, financing and the COMEX/London arbitrage. McKinsey called 2020 an outlier.

## Appendix B: costs per R and size (re-derived here from raw data)
Inputs: `raw/xauusd_1m_dukascopy_2010..2025.parquet`, cut before 2025-10-01; bid OHLC resampled; ATR(14) median. Cost is the cor ICM "all" round trip $0.166 plus overshoot $0.11, i.e. $0.276. The method is a non-outcome measurement: no signal and no forward return.

| tf | ATR14, 2010-17 | cost/R ($ at the price of the time) | cost/R (bps, W2) | ATR14, 2018-25.09 | cost/R ($) |
|---|---|---|---|---|---|
| 15m | $1.47 (11.1 bp) | 0.188 | 0.062 | $1.92 | 0.143 |
| 1h | $3.23 (24.5 bp) | 0.085 | 0.028 | $4.26 | 0.065 |
| 4h | $6.38 (48.5 bp) | 0.043 | 0.014 | $8.47 | 0.033 |
| 1d | $15.32 (117 bp) | 0.018 | 0.006 | $20.77 | 0.013 |
| 1w | $42.48 (320 bp) | 0.006 | 0.002 | $55.53 | 0.005 |

Other measurements:
- Daily vol: 14.6% a year in 2010-17 and 13.4% in 2018-25.
- Median 1m Dukascopy spread: 2.44 bp ($0.333) in 2010-17 and 1.87 bp ($0.340) in 2018-25. The bps conversion therefore **understates** historical spreads by about 2-4x: use max(bps-scaled cor, historical spread).
- Swap per 20-night hold at a 2 x ATR(1d) stop: CFD at 5.5% a year is about 0.13 R; futures carry at about 3% a year is about 0.07 R; the ETC fee of 0.12% a year is about 0.003 R.

## Appendix C: power of the qualification chain
The simulation assumes iid daily P&L, yearly t ~ N(SR, 1), and 200,000 draws. Pass legs: explore t >= 2 over 8 years; confirm t >= 1.65 over 7.75 years with >= 5 of 8 years positive; TEST-OLD t >= 1.28 over 4 years.

| True SR | P(explore) | P(confirm) | P(TEST-OLD) | P(all) |
|---|---|---|---|---|
| 0.3 | 0.12 | 0.21 | 0.25 | 0.007 |
| 0.4 | 0.19 | 0.30 | 0.32 | 0.018 |
| 0.5 | 0.28 | 0.40 | 0.39 | 0.044 |
| 0.7 | 0.49 | 0.62 | 0.55 | 0.167 |
| 1.0 | 0.79 | 0.88 | 0.76 | 0.533 |

- DSR about 0.5 needs a confirm-window SR of 1.34 at K 6,233, and 1.04 even at K_eff 300.
- With 30 years of daily history, SR 0.4 gives t 2.19, and SR 0.5 gives t 2.74. Longer history and pooled markets buy power; looser bars do not.

## Appendix D: venue options (public pages; spending is owner-only)
| Venue | Round trip per oz | Financing (long) | Short side | Minimum unit | Fits £1,000? | Note |
|---|---|---|---|---|---|---|
| ICM Raw MT5 (now) | $0.166 measured (cor) | -$0.60/oz/night shipped, about 5.5% a year at $4,000; re-measure from the symbol spec | 0 assumed | 1 oz | Intraday only (1% cap) | Same account trades XAG, XPT, FX, indices, oil |
| ICM / Vantage swap-free | about the same | holding fee after grace (ICM page: 5 days; gold terms unconfirmed); Vantage admin fee after 24 h | n/a | 1 oz | no | Eligibility is religious: **declined** unless genuine |
| CME 1OZ futures (IBKR) | estimate $1-2 per side (MGC: IBKR about $0.25 + about $0.70 in fees per contract, BrokerChooser); tick $0.25 | in the curve, about rates minus lease | earns carry | 1 oz | No for 1d stops; 1OZ liquidity is thin | Monthly roll costs one spread |
| CME micro gold MGC | about $0.2-0.3 | in the curve | earns carry | 10 oz (about $40k) | no | |
| UK physical gold ETC (SGLN / SGLD) | spread of a few bp + broker fee | 0.12% a year fee; no financing | none (long/flat) | fractional / 1 share | **yes** | No leverage. A trend becomes long/flat |

## Appendix E: sources
Market-data hosts were not used for any of these; all are public pages.
- [Hurst, Ooi, Pedersen 2017 PDF](https://fairmodel.econ.yale.edu/ec439/hurst.pdf), [CXO summary](https://www.cxoadvisory.com/momentum-investing/trend-following-with-intrinsic-momentum-over-the-very-long-run)
- [Moskowitz, Ooi, Pedersen 2012 SSRN](https://papers.ssrn.com/abstract=2089463), [Kim, Tse, Wald via Quantpedia](https://quantpedia.com/?p=637)
- [Harvey et al. 2018 PDF](https://people.duke.edu/~charvey/Research/Published_Papers/P135_The_impact_of.pdf), [Quantpedia summary](https://quantpedia.com/the-impact-of-volatility-targeting-on-equities-bonds-commodities-and-currencies)
- [Baur 2013 autumn effect](https://ideas.repec.org/a/eee/riibaf/v27y2013i1p1-11.html), [2024 replication](https://reference-global.com/download/article/10.2478/ijme-2024-0011.pdf)
- [Caminschi and Heaney](https://api.research-repository.uwa.edu.au/ws/files/4725264/A0205_use_this.pdf)
- [Gao et al. intraday momentum](https://c.mql5.com/forextsd/forum/173/intraday_momentum_-_the_first_half-hour_return_predicts_the_last_half-hour_return.pdf), [Chinese metal futures night session](https://ideas.repec.org/a/eee/pacfin/v80y2023ics0927538x2300152x.html)
- [CBS thesis, gold around the clock](https://research.cbs.dk/en/studentProjects/gold-price-dynamics-around-the-clock/), [FXStreet on WGC session split](https://www.fxstreet.com/analysis/a-strange-dichotomy-gold-up-on-the-year-in-asian-markets-down-big-in-the-west-202607131959)
- [JPMorgan $1bn precious metals 2020](https://www.mining.com/web/jpmorgan-dominates-gold-market-with-record-1-billion-revenue/), [JPM desk profits in court](https://mining.com/?p=1096544)
- [Hedging pressure, Dossani, Kang, Pan](https://business.ucdenver.edu/jpmorgancenter/content/session-2-presentation-4), [hedging pressure working paper](https://ageconsearch.umn.edu/record/18929/files/cp00sa01.pdf)
- [Erb and Harvey summary (CXO)](https://cxoadvisory.com/?p=23925)
- [CME 1OZ FAQ](https://www.cmegroup.com/articles/faqs/faq-1-oz-gold-futures.html), [1OZ product page](https://cmegroup.com/markets/metals/precious/1-ounce-gold.html), [MGC specs](https://www.cmegroup.com/trading/metals/precious/e-micro-gold_contractSpecs_futures.html), [BrokerChooser MGC fees](https://brokerchooser.com/best-brokers/lowest-margin-brokers-for-trading-micro-gold-futures-in-qatar)
- [ICM swap-free](https://icmarkets.com/global/id/trading-accounts/islamic-account), [ICM commodity specification](https://cdn.icmarkets.com/uploads/Commodity+Specification+Sheet.pdf), [Vantage swap-free](https://global.vantagehelpcenter.com/hc/en-us/articles/10574193329551)
- [SGLN costs (HL)](https://www.hl.co.uk/shares/shares-search-results/B4R1D93), [SGLD (HL)](https://www.hl.co.uk/shares/shares-search-results/B599TV6)
- [WGC on ETF flows and positioning](https://www.gold.org/goldhub/research/gold-market-commentary-january-2023)
