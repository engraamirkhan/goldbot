# Algo survey slice 1/3: institutional and academic systems relevant to XAUUSD / FX / index CFDs

Date: 2026-11-13. Author: strategy-researcher-staff (for the principal). Read-only web survey; no repo changes.
Context: demo break-even after costs (gold arms ~+£5 over 4,202 fills, mostly 1m/5m; non-gold -£262); daily cross-asset
trend + vol targeting already failed here after CFD financing (gross t 1.70, financing ate ~70%).

Evidence grading: **R** = replicated / multi-paper, **S** = single peer-reviewed paper, **P** = practitioner/blog only,
**U** = from memory, URL not fetched this session (verify before citing in a pre-registration).

---

## 0. Reality check first

| Benchmark | Annual return | Sharpe | Source |
|---|---|---|---|
| Trend following, 1880-2013, 2/20 fees, net | 11.2% (14.9% gross), vol 9.7% | 0.77 net | Hurst, Ooi, Pedersen (AQR) https://www.TrendFollowing.com/whitepaper/Century_Evidence_Trend_Following.pdf |
| Same, per single market average | - | ~0.4 net | CXO summary https://www.cxoadvisory.com/momentum-investing/trend-following-with-intrinsic-momentum-over-the-very-long-run |
| SG Trend Index, Dec 2000-Dec 2025 (excess of T-bills) | 4.2%, vol 13.4% | 0.31 | Fidelity https://institutional-prd1.fidelity.ca/content/dam/fci/en/articles/Trend-following_crisis_alpha-en.pdf |
| AQR Managed Futures (AQMIX), Jan 2010-Mar 2026 | 4.16% (T-bill 1.42%), vol 10% | 0.22 | https://funds.aqr.com/funds/aqr-managed-futures-strategy-fund |
| SG Trend drawdown May 2024-May 2025 | -20.4% | - | Cambridge Associates https://publishedresearch.cambridgeassociates.com/?p=19140 |
| Renaissance Medallion 1988-2018 (closed, capacity ~$10-15bn) | 66% gross / 39% net (arithmetic means) | not published | https://www.quantt.co.uk/resources/medallion-fund-history ; https://www.institutionalinvestor.com/article/b1k2fymby99nj0/Famed-Medallion-Fund-Stretches-Explanation-to-the-Limit-Professor-Claims |
| Retail CFD accounts | 74-89% lose money | - | ESMA https://www.esma.europa.eu/node/84933 |
| Taiwan day traders 1992-2006 | <1% predictably profitable net of fees | - | Barber, Lee, Liu, Odean (JFM 2014) https://faculty.haas.berkeley.edu/odean/papers/day%20traders/Day%20Trading%20Skill%20110523.pdf |

**Arithmetic against the owner goal.**
- 20%/week compounded = 1.20^52 ≈ **13,000x per year**; 30%/week ≈ **840,000x per year**. Medallion's 39%/yr net is
  ~0.63%/week. The best long-run institutional trend book (Sharpe 0.77) compounds ~11%/yr.
- With the £50/day loss cap on ~£1,000 (5%/day), 20%/week needs ~3.7%/day *mean* gain, i.e. a mean close to the daily
  stop. That implies a daily Sharpe around 0.7+, an annual Sharpe of ~10+, sustained. Only latency-advantaged market
  makers (capacity-limited, colocated) document anything like that, and not from a retail CFD seat.
- Kelly view: log growth ≈ SR²/2 per year at full Kelly. 13,000x needs ln = 9.5, so SR ≈ 4.4 *at full Kelly*, with full
  Kelly's ~50% chance of halving along the way. No published strategy reachable at retail clears SR 1.5 net for long.
- Realistic target for a genuinely good retail systematic book: **net Sharpe 0.5-1.5, 10-40%/yr at moderate leverage**.
  A great *week* of 20-30% happens through leverage on a lucky week, and is not a repeatable rate.

**Multiple-testing bar.**
- Hou, Xue, Zhang (RFS 2020): 65% of 452 anomalies fail |t| > 1.96; 82-85% fail at t > 3 (Harvey, Liu, Zhu 2016).
  https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html ; https://mathinvestor.org/2021/02/the-failure-of-anomaly-indicators-in-finance/
- Chen & Velikov dispute the setup (43% fail), https://cfr.ivo-welch.info/published/papers/chen2021open.pdf
- McLean-Pontiff: about 58% lower returns after publication; ML anomaly Sharpe ~26% lower post-publication (AEA 2025)
  https://www.aeaweb.org/conference/2025/program/paper/TihddefB
- Bailey, Borwein, López de Prado, Zhu: more than ~45 configurations on 5 years of data almost guarantees an
  in-sample SR of 1 with an expected OOS SR of 0 (PBO/CSCV, MinBTL). https://papers.ssrn.com/abstract=2326253 ;
  https://carmamaths.org/jon/backtest2.pdf
- Implication for us: with thousands of arms tried (sprint-200, 3,330 candidates), any survivor needs t ≥ 3 on
  pre-registered windows plus a deflated Sharpe, or it is the expected winner of noise.

---

## 1. Managed futures / CTAs

- **Evidence (R).** TSMOM in 58 futures (Moskowitz, Ooi, Pedersen JFE 2012, https://papers.ssrn.com/abstract=2089463)
  with 1-12 month persistence, partial reversal later; gold included. Baltas & Kosowski: momentum at several frequencies,
  low cross-correlation, no capacity constraint found
  (https://efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2012-Barcelona/papers/BK_MOMF_Full.pdf).
- **Decay post-2010 (S, new, important).** Kurth et al. 2026 "Is Trend Still Your Friend?" (~100 futures, 1995-2025,
  https://www.alphaxiv.org/abs/2607.01550): fast trend (5-10 day) Sharpe fell from >1.0 to ~0 after ~2009. Slow
  (≥50 day) held up. The split is **tick size relative to volatility**: small-tick markets (equity indices, FX, and by
  extension spot gold with a $0.01 tick against a ~$40-60 daily range) lost trend PnL; large-tick markets (some bonds,
  commodities) kept it. Proposed mechanism: HFT makers pull liquidity from predictable directional flow, which breaks
  the self-reinforcing impact loop. The losses come on large-move days.
- CTA dispersion ~10 pp/yr between best and worst (Man https://www.man.com/insights/deep-dive-trend-following); SG CTA
  index is >85% explained by trend (CFM https://www.cfm.com/steady-trends-the-reality-of-cta-return-dispersion/).
- **Holding period** weeks-months; **capacity** tens of $bn; **costs** futures (no financing spread beyond T-bill carry).
- **Retail CFD fit: poor.** Our own test agrees: CFD swap is roughly T-bill ± a broker markup of ~2-3%/yr each way, so
  a 4%/yr gross edge disappears. Kurth's result also *explains* why our 1h/4h tsmom (+0.06 R gross) and fast breakout
  arms have no edge on gold, indices and FX: these are exactly the small-tick markets where fast trend died.
- **Transferable lesson:** stop spending trials on fast trend/breakout on XAU, indices and FX unless a new
  conditioning variable exists. If trend is kept, keep it slow, on futures-like cost, or as a *filter* (direction of
  the 50-200 day trend) for intraday entries. Filters cost no financing.

## 2. Intraday / overnight anomalies in gold and FX

- **Gold overnight positive, day negative (S, replicated by same authors).** Blose & Gondhalekar: COMEX front gold
  1985-2012, overnight significantly positive, day significantly negative, "weakened substantially but still present"
  (https://ideas.repec.org/a/taf/apeclt/v21y2014i18p1269-1272.html). The 2018 extension finds the same pattern across
  the London PM fix, miners and gold ETFs (https://ideas.repec.org/a/spr/jecfin/v42y2018i3d10.1007_s12197-017-9403-0.html).
  A CBS thesis (P) finds a hat-shaped day: gold rises in Asian hours and falls through London/NY, with drops around
  the fix in the alleged-manipulation years (https://research.cbs.dk/en/studentProjects/gold-price-dynamics-around-the-clock/).
  Practitioner GLD close-to-open: ~0.04%/trade gross, ~11.4%/yr versus 11.8% buy-and-hold
  (https://quantifiedstrategies.substack.com/p/a-quantitative-look-at-the-gold-overnight). This is gross, with no split
  for recent years.
- **London PM fix anomaly (S/P).** Abrantes-Metz & Metz found unusual downward moves around the 3 pm fix 2004-2013
  (https://www.lbma.org.uk/alchemist/issue-73/has-there-been-a-decade-of-london-pm-gold-fixing-manipulation). Barclays
  was fined in 2014, and the fix was replaced by the IBA electronic auction in March 2015. **I found no post-2015 evidence
  that it persists. Treat it as likely dead and test it on 2016+ data only.**
- **Session structure (S).** London has the highest and Tokyo the lowest session returns (to 2011), per the gold
  session study (https://econpapers.repec.org/RePEc:pal:palchp:978-1-137-28643-7_4). Tokyo is more uninformed flow and
  NY more informed (Iwatsubo, Xu, Watkins https://www.rieti.go.jp/jp/publications/dp/17e120.pdf). Batten et al.
  (2017): strong 5-minute periodicity in returns, vol, volume and spread for all four precious metals over ~15 years
  (https://reading-clone.eprints-hosting.org/79175/1/BattenLuceyMcGroartyPeatUrquhart2017.pdf).
- **FX time-of-day (S, JMCB 2013).** Breedon & Ranaldo: currencies weaken in their own local trading hours, driven by
  order flow; a simple EUR/USD strategy was profitable over ~10 years
  (https://www.snb.ch/en/mmr/papers/id/working_paper_2011_04). Since gold is quoted in USD, the same reasoning implies
  USD weakens in US hours, which would support XAU in US hours. That is opposite in sign to the gold intraday-negative
  finding, so the sign has to be tested, not assumed.
- **Intraday momentum (R in equities, S elsewhere).** Gao, Han, Li, Zhou (JFE 2018): the first half-hour return
  predicts the last half-hour on SPY 1993-2013 (R² 1.6%, 2.6% with the 12th half-hour), ~6.3-6.7%/yr gross
  (https://econpapers.repec.org/RePEc:eee:jfinec:v:129:y:2018:i:2:p:394-414). Found in FX (Elaut et al. 2018, RUB/USD)
  and in Chinese commodity futures (Jin et al., JFM 2020, https://ideas.repec.org/a/wly/jfutmk/v40y2020i4p632-650.html).
  Gold not tested in the sources found. Lou, Polk, Skouras (JFE 2019): momentum profits accrue overnight and reverse
  intraday when institutions trade (https://researchonline.lse.ac.uk/id/eprint/119010).
- **Typical numbers.** Gross edge per trade ~1-10 bp; Sharpe 0.5-1.0 gross on single instruments; capacity large;
  holding minutes to hours; data needed is 1m bars (we have them).
- **Retail CFD fit: GOOD on cost structure, uncertain on edge.** Holding is intraday with no swap. Gold spread $0.09-0.20
  on ~$4,000 is ~0.25-0.5 bp, which is small next to session moves of $10-40. For the overnight leg, the CFD
  "overnight" (NY close 17:00 to next London/NY) **incurs one swap** (~1-2 bp/night for gold longs, which is about
  the size of the gross edge). The Asian-session long (open after rollover at ~22:00-23:00 UTC, close before
  London/NY) captures most of the same drift **without** a rollover.

## 3. Mean reversion / stat-arb in metals

- **Gold-silver (S, weak).** Escribano & Granger 1998: nonlinear error-correction beats a random walk for gold in and
  out of sample, but "the dependence is less out of sample, possibly meaning the two markets are becoming separated"
  (https://e-archivo.uc3m.es/bitstream/10016/2558/2/investigating_escribano_JF_1998_ps.pdf). Baur & Tran (2014)
  extend it to 2011. The ratio is unstable and trends for years. A naive GLD/SLV pairs backtest from 2023 on: -8.5%,
  SR -0.40 (P, https://www.quantconnect.com/terminal/cache/embedded_backtest_14145b688946fd2d41e76ad5ee46b924.html).
  I found no peer-reviewed net-of-cost OOS study.
- **Gold-miners (P).** Chan's GLD/GDX: test-set SR ~1.5 in a blog replication
  (https://epchan.blogspot.com/2006/11/gold-vs-gold-miners-another-arbitrage.html), with periods where it stays apart
  for a year and some pairs never converge. Equity CFDs on miners carry their own financing and borrow cost.
- **Gold vs real yields (R as a level relation, broken since 2022).** Erb & Harvey "Golden Dilemma"
  (https://www.nber.org/papers/w18706.pdf): real gold mean-reverts only over very long horizons. PIMCO cointegration
  of log real gold on 10y TIPS 2006-2022 (https://pimco.ca/en-ca/resources/education/understanding-gold-prices). After
  2022, central-bank buying (~1,000 t/yr) broke the link (R² reportedly ~0.8 to ~0.15; P, unverified figures). **A
  monthly-to-years horizon is useless for intraday CFD trading. It is at most a regime feature.**
- **Retail CFD fit: poor to moderate.** Two legs mean twice the spread, and swap on both legs for multi-day holds.
  Half-lives are typically days to weeks, so financing applies. The best version for us is an *intraday* XAU/XAG
  z-score fade with a hard close before rollover. That is plausible but unsupported by evidence, so its expected
  information per trial is low.

## 4. Volatility / event strategies

- **Pre-FOMC drift (R then decayed).** Lucca & Moench (JF 2015): large equity returns in the 24 h before FOMC, with *no*
  effect in Treasuries, money markets or FX (https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr512.html).
  Kurov, Wolfe, Gilbert (FRL 2021): it essentially disappeared after 2015 (https://papers.ssrn.com/author=3571041).
  Post-FOMC equity returns reverse (Boguth et al. 2023, "Noisy FOMC returns", https://conference.nber.org/conf_papers/f118768/f118768.pdf).
- **Gold at FOMC (S, 2024).** Awartani, Hussain, Virk (IRFA 2024): the gold response is asymmetric (stronger to
  dovish surprises), and **price and vol adjustment continue beyond 5 minutes**, so the market is short-run inefficient
  (https://pure.kfupm.edu.sa/en/publications/how-do-the-gold-intra-day-returns-and-volatility-react-to-monetar/).
- **Macro releases (S/P).** Gold responds strongly to CPI and capacity utilisation (Christie-David, Chaudhry, Koch;
  https://espace.curtin.edu.au/bitstream/handle/20.500.11937/28039/227202_227202.pdf?sequence=2). Smales finds COMEX
  gold reacts to CPI/GDP/PPI/NFP surprises (https://core.ac.uk/download/357567523.pdf). Most futures adjustment
  happens within 1 minute (Ederington & Lee 1993). For NFP, practitioner samples show a short-lived reaction and no
  link to the surprise after 4 h (P, https://www.fxstreet.com/analysis/us-january-nonfarm-payrolls-preview-analyzing-gold-price-reaction-to-nfp-surprises-202302021043).
- **Volatility targeting (contested).** Moreira-Muir alphas are not implementable in real time and lose out of sample
  across 103 strategies (Cederburg et al. JFE 2020, https://www.lehigh.edu/~xuy219/research/COWY.pdf). The
  conditional multifactor version is net-positive (DeMiguel et al. JF 2024). Useful for *risk sizing*, not as alpha.
- **Typical numbers.** ~8 FOMC + 12 CPI + 12 NFP = ~32 events/yr, i.e. ~300 over 10 years, which is below our
  1,000-event P4 floor unless pooled across instruments or extended with ECB/BoE/PCE/ISM.
- **Retail CFD fit: moderate.** Move sizes ($15-60 on gold) are 100x the spread, but CFD spreads widen 3-10x and
  slippage spikes in the first seconds, and retail latency loses the first minute. The reachable version is to
  **enter after the first 1-5 minutes in the direction of the surprise and hold 15-120 minutes**, relying on the
  "adjustment continues beyond 5 minutes" result. It needs an ex-ante surprise measure (consensus vs actual), and free
  consensus data is a known gap.

## 5. Microstructure / HFT

- Baron, Brogaard, Hagströmer, Kirilenko (JFQA 2019): HFT profits depend on *relative* latency; revenue concentration
  stays high; firms improve ranking by colocation upgrades
  (https://ideas.repec.org/a/cup/jfinqa/v54y2019i03p993-1024_00.html). Sharpe ratios are very high but the edge is
  winner-take-most on microseconds.
- Gold price discovery is led by COMEX futures, not London OTC (Hauptfleisch, Putniņš, Lucey 2016, JFM, "Who sets the
  price of gold? London or New York" - **U**). CFD broker quotes follow futures/LP feeds within milliseconds.
- **Retail CFD fit: none for latency arbitrage, queue position or rebate capture.** An MT5 retail seat has 10-200 ms
  of latency, takes the broker's spread, and has no order book, so we are the liquidity *taker* that HFT profits from.
  What does transfer: (a) **don't trade 1m/5m signals whose gross edge is under ~3x the round trip**; our 4,202 mostly
  1m/5m fills netting ~£5 fits a zero-edge taker paying spread; (b) avoid known spread-widening windows (rollover
  21:00-22:00 UTC, release seconds); (c) tick-count "volume" on CFDs is a broker artefact, so treat order-flow proxies
  as low prior.

## 6. ML in trading research and replication

- Gu, Kelly, Xiu (RFS 2020): NN long-short on US stocks, OOS SR 1.35 value-weighted; S&P timing SR 0.77 vs 0.51
  buy-and-hold (https://www.nber.org/papers/w25398.pdf). That is a cross-section of ~30,000 stocks, a monthly horizon
  and 900+ features, a regime that has nothing in common with one instrument on 1m bars.
- ML anomaly SR 0.94 gross / 0.84 net, ~26% lower after publication (AEA 2025). Another JF-listed study finds ML returns
  vanish after costs, post-publication decay and decimalisation (https://afajof.org/management/viewp.php?n=75544).
  Avramov et al. (2022): returns concentrate in small, costly stocks.
- Single-asset ML return prediction at intraday horizons has no robust published net-of-cost success that I could find.
  Our meta-model AUC ~0.50 agrees with this.
- **Transferable:** ML as **meta-labelling / regime filter on a base signal that already has gross edge**, with
  fold-internal selection, CPCV/PBO and a deflated Sharpe. Never ML on raw returns as the alpha source.

---

## 7. Ranked transferable ideas (evidence x fit to a retail MT5 CFD book)

| Rank | Idea | Evidence | Fit | Why it may work for us | Kill condition |
|---|---|---|---|---|---|
| 1 | **Gold session-drift: long XAU in Asian hours after rollover, flat or short into London/NY-open (fix) window** | S, replicated in 2 papers + thesis, weakening over time | High: intraday, no swap, spread ≈0.3 bp vs $10-30 session moves | Structural flow story (Asian physical demand; Breedon-Ranaldo local-hours order flow); hold-hours targets are many times the cost | No sign on 2016+ data (post IBA fix) at t ≥ 3 |
| 2 | **Post-event continuation (FOMC/CPI/NFP, pooled with ECB/BoE/PCE): enter 1-5 min after release in the surprise direction, hold 15-120 min** | S (Awartani 2024: adjustment beyond 5 min; CPI response strong) | Medium-high: big moves vs cost, but spread/slippage at release | Avoids the latency race by joining after the first minute; move/cost ratio ~50-100x | Fewer than ~600 pooled events, or edge gone after realistic release-spread costs |
| 3 | **Intraday (session) momentum: return of the first London/NY session hour (or Asia+London) predicts the last NY hour, gold and index CFDs** | R in equities, S in FX/commodities; gold untested | High: intraday, one trade/day/instrument | Hedging/late-informed flow mechanism; cheap to pre-register on existing 1m data | Gross t < 2 on ≥1,000 days pooled over gold + 3 indices |
| 4 | **Slow-trend regime as a FILTER (50-200 d direction / vol regime) on intraday entries, not as a held position** | R (century of trend; slow trend survived per Kurth 2026) | High: zero financing because exposure stays intraday | Keeps the robust slow-trend information while dropping the CFD swap that killed our daily test | Filter adds < 0.1 SR to a base signal in fold-internal test |
| 5 | **ML meta-labelling of 1-4 on regime features (realised-vol term structure, USD, real yields, event calendar)** | R as method (López de Prado), not as alpha | Medium | Raises hit rate and cuts trade count on bad days, which matches the north-star "less or none when poor" | Meta AUC < 0.55 OOS, or PBO > 0.5 |

**Ideas to retire or deprioritise:** fast trend/breakout on gold, FX and indices (Kurth: small-tick decay, matches our
tsmom); daily+ holds on CFDs without carry in our favour (financing); London PM fix fade (no post-2015 evidence);
gold-real-yield value (multi-year horizon, broken since 2022); HFT/latency/order-book ideas; ML on raw returns;
pre-FOMC equity drift (gone after 2015).

**Flags for the principal.**
- Ideas 1-3 are cheap to pre-register on existing 1m XAU data. Each must count against the K/trial budget, and the
  bar should be t ≥ 3 given our tested universe.
- Event-study ideas need a consensus/surprise series. Free sources are thin, so this is a data-engineer backlog item.
- The 20-30%/week goal is ~3-4 orders of magnitude above any documented net system. A repeatable outcome with the
  current £50/day cap is roughly 10-40%/yr at net SR 0.5-1.5. Expectation setting stays the CFO/owner's call.
- Kurth et al. 2026 and Awartani et al. 2024 are recent, single-paper results, so verify the full texts before relying
  on their numbers.
