# State of the art: systematic trading and gold (Q4 2026 review)

Written 2026-10-10, before the Q1 2027 trial budget resets. Refresh each quarter.

Companion: [`xauusd-trader-playbook.md`](xauusd-trader-playbook.md) is the full checklist of what a professional
XAUUSD trader considers (drivers, microstructure, technicals, risk management, process). Each item has its evidence
and goldbot's status, and it ends with a ranked gap list.

**How to read this.** **R** = replicated across independent samples or authors. **S** = single study, or practitioner
evidence only. "Net" means the source charges realistic costs. "Gross" means it does not. "?" means the source does not
say, or we could not check. A URL marked *(unverified)* was cited from memory and not fetched for this review. Treat
those citations as leads, not as evidence.

**Our evidence so far (HANDOFF, Q4 2026):**
- No gross edge: mean reversion (15m), session-open breakout (15m), trend pullback (1h), range breakout (1h), and
  intraday momentum (London and NY).
- tsmom: small gross edge, +0.06 R/trade (t 2.6 on 1h, 2.4 on 4h). Net of costs it is −0.079 R (1h) and −0.014 R
  (4h, before swap).
- Meta-models: OOF AUC about 0.50.

The literature below largely predicts these results.

---

## A. Trading-system design

### A1. Time-series momentum (TSMOM) and trend following

| Finding | Source | Sample / instrument | Costs | Grade |
|---|---|---|---|---|
| Returns over the past 1–12 months predict the next month. A diversified TSMOM portfolio has Sharpe ≈1 gross. | Moskowitz, Ooi, Pedersen 2012, *JFE* 104(2) https://doi.org/10.1016/j.jfineco.2011.11.003 *(unverified)* | 58 futures incl. gold, 1965–2009 | Gross | R |
| Trend is positive in every decade since 1880 and survives fees and estimated costs. Metals are charged about 58 bp one way in the early periods. | Hurst, Ooi, Pedersen 2017, *JPM* 44(1) https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing | 67 markets, 1880–2016 | Net (estimated) | R |
| Asset-by-asset predictability is weak in sample and out of sample. Pooled t-statistics fail bootstrap critical values. The profits look like those of a strategy built on historical means. | Huang, Li, Wang, Zhou 2020, *JFE* 135(3) https://doi.org/10.1016/j.jfineco.2019.08.004 | 55 futures, 1985–2015 | Gross | S (but a credible challenge) |
| Better volatility estimators (e.g. Yang–Zhang) and smoother trend rules cut turnover by more than a third with no significant loss of return. | Baltas & Kosowski, SSRN 2140091 https://papers.ssrn.com/abstract=2140091 | Futures, 1974/84–2013 | Net (roll + rebalance model) | S |
| Short-term trend has not paid since about 2009 on small-tick contracts (tick size small relative to volatility), at every horizon. It is intact on large-tick contracts. The proposed cause is HFT market makers pulling liquidity ahead of predictable trend flow. | Kurth, Eisler, Rej, Bouchaud 2026, arXiv 2607.01550 https://arxiv.org/abs/2607.01550 | ≈100 futures, 1995–2025 | ? | S (new, strong authors) |

**For goldbot:**
- Trend is robust only as a slow, diversified premium (holding periods of weeks to months, dozens of markets).
- On one instrument, Huang et al. warn that a t of 2.5 is about what noise produces.
- Gold is a small-tick market: a $0.01 tick against a daily range of about $40–80. Kurth et al. therefore predict
  that our intraday trend families fail, which they did.
- The one horizon the literature still supports is daily bars with multi-week holds. That is exactly where our
  long-side swap is largest.

### A2. Volatility targeting

| Finding | Source | Sample | Costs | Grade |
|---|---|---|---|---|
| Much of TSMOM's alpha comes from volatility scaling, not from the sign of the trend. | Kim, Tse, Wald 2016, *J. Financial Markets* 30 *(unverified)* | 55 futures, 1985–2012 | Gross | S |
| Volatility targeting raises Sharpe and cuts left tails for equities and credit, because volatility and returns are negatively correlated there. It does almost nothing for bonds, FX and commodities. | Harvey et al. (Man Group) 2018, *JPM* https://papers.ssrn.com/abstract=3175538 *(unverified)* | 60+ assets, 1926–2017 | Gross | R |
| Volatility-managed factor portfolios have positive spanning alphas... | Moreira & Muir 2017, *JF* 72(4) | US factors, 1926–2015 | Gross | S |
| ...but real-time versions underperform the unmanaged originals in most of 103 strategies, and do not survive costs. | Cederburg, O'Doherty, Wang, Yan 2020, *JFE* 138 https://www.lehigh.edu/~xuy219/research/COWY.pdf ; Barroso & Detzel 2021 | 103 equity strategies | Real-time; net in B&D | R |

**For goldbot:** treat volatility scaling as a risk control (we already size in R), not a source of alpha. Do not
spend a trial on it.

### A3. Carry

- Koijen, Moskowitz, Pedersen, Vrugt 2018, *JFE* 127(2) (*unverified* DOI 10.1016/j.jfineco.2017.11.002).
  - Carry predicts returns across asset classes, including commodities. Sample 1983–2012. Gross. Grade R.
- Gold's carry is roughly the USD rate minus the gold lease rate. That has been negative for longs in every
  non-ZIRP year, and it is what the −60 USD/lot/night swap charges.
- With one instrument there is no cross-section to exploit. Carry here is a cost asymmetry, not a signal: shorts pay
  nothing at our broker, longs pay about 5% a year at current rates.

**Implication:** model swap as rate differential × notional, not as a constant. A flat −60 USD overstates the cost in
2010–2021 (ZIRP, gold under $2,000) and may understate it today.

### A4. Mean reversion, intraday seasonality and intraday momentum

| Finding | Source | Sample | Costs | Grade |
|---|---|---|---|---|
| The first half-hour return predicts the last half-hour return. | Gao, Han, Li, Zhou 2018, *JFE* 129(2) *(unverified)* | SPY, 1993–2013 | Gross (thin after costs) | R |
| The last 30 minutes are predicted by the rest of the day's return, in 60+ futures across all asset classes. The driver is gamma hedging by option dealers and leveraged ETFs, and the effect reverses over the next days. | Baltussen, Da, Lammers, Martens 2021, *JFE* 142(1) https://ideas.repec.org/a/eee/jfinec/v142y2021i1p377-403.html | 1974–2020 | Gross | R |
| Intraday momentum exists in FX. | Elaut, Frömmel, Lampaert 2018, *J. Financial Markets* 37 *(unverified)* | RUB/USD, 2005–2012 | Gross | S |
| A large search over technical trading rules shows no out-of-sample edge once data snooping is accounted for. | Sullivan, Timmermann, White 1999, *JF* 54(5) | DJIA, 1897–1996 | — | R |

**For goldbot:**
- Intraday momentum works through a mechanism: a close auction plus gamma hedging. Spot XAUUSD has neither, so our
  null result at London and NY mid-session fits the literature.
- We found no peer-reviewed evidence for short-horizon mean reversion in gold that survives costs.

### A5. Meta-labelling and ML return prediction

- López de Prado 2018, *Advances in Financial Machine Learning* (Wiley), defines meta-labelling.
  - A secondary model decides whether to act on a primary signal and how large to size it.
  - It can raise precision on a primary model that already has an edge. It cannot create an edge.
- Joubert 2022 and Meyer, Barziy, Joubert 2023, *J. Financial Data Science* (*unverified*), report on position
  sizing with meta-labels.
  - Calibration helps fixed sizing rules.
  - The evidence is synthetic or single-strategy. Grade S.
- Gu, Kelly, Xiu 2020, *RFS* 33(5) (https://doi.org/10.1093/rfs/hhaa009, *unverified*): ML gains are real for the
  cross-section of stocks. For the market as a whole, monthly out-of-sample R² is under 1%. Grade R.
- Welch & Goyal 2008, *RFS* 21(4): classic return predictors for a single asset fail out of sample. Grade R.

**For goldbot:** AUC about 0.50 is the expected result when the primary signals have no gross edge and the features
come from price. Do not retry meta-models until a primary passes P4 on its own.

### A6. Regime filters

- TSMOM pays most in extreme markets, the "smile" (Moskowitz et al. 2012).
- Blending slow and fast trend at turning points reduces drawdowns: Garg, Goulding, Harvey, Mazzoleni 2021, *FAJ*
  77(4) (*unverified*). Grade S. Gross.
- Momentum crashes are predictable from bear-market states and high volatility: Daniel & Moskowitz 2016, *JFE*
  122(2). This is cross-sectional equity momentum. Grade R.
- A regime filter adds parameters and halves the event count. We found no single-asset commodity evidence that one
  survives out of sample net of costs.

### A7. Execution and costs for retail CFDs

- ESMA 2018 (https://www.esma.europa.eu/node/84933): 74–89% of retail CFD accounts lose money, with average losses of
  €1,600–29,000 per client. This is regulatory data, not a strategy test.
- Heimer & Simsek 2019, *JFE* (*unverified*): US leverage caps reduced retail FX losses. Grade S.
- Practical points not covered by papers:
  - Spreads widen at the 21:00–22:00 UTC rollover and around releases.
  - Stops fill at the touch plus slippage.
  - Swap is charged ×3 on Wednesday.
  - Costs are fixed in USD per lot while R scales with price and volatility, so cost in R is time-varying. It was
    roughly 2–3× higher in R in low-volatility 2012–2019 than in 2024–26.
- Use measured fills (`run.py publish-costs` -> release `costs-v1`) rather than priors. Report net R by year and by volatility tercile.

### A8. Overfitting controls

| Tool | Source | Use |
|---|---|---|
| Deflated Sharpe ratio | Bailey & López de Prado 2014, *JPM* 40(5) https://papers.ssrn.com/abstract=2460551 *(unverified)* | Adjusts for the number of trials, skew and kurtosis. This is already our gate. |
| PBO / CSCV | Bailey, Borwein, López de Prado, Zhu 2017, *J. Computational Finance* https://papers.ssrn.com/abstract=2326253 *(unverified)* | Probability that the best in-sample configuration underperforms out of sample. Cheap to add to reports. |
| CPCV | López de Prado 2018 | Many purged paths instead of one walk-forward. Gives a distribution of Sharpe. |
| t > 3 hurdle | Harvey, Liu, Zhu 2016, *RFS* 29(1) | Our P4 t ≥ 2 is a screen only; final gates must be stricter. |
| Post-publication decay | McLean & Pontiff 2016, *JF* 71(1): returns fall 26% out of sample and 58% after publication. Hou, Xue, Zhang 2020, *RFS*: most anomalies fail replication. | Haircut any literature edge by at least half. |
| Backtest protocol / pre-registration | Arnott, Harvey, Markowitz 2019, *JFDS* https://papers.ssrn.com/abstract=3275654 *(unverified)* | Decide the reading rule before the run. We already do. |

---

## B. Gold

### B1. Macro drivers and the 2022 break

| Finding | Source | Sample | Grade |
|---|---|---|---|
| Gold moves inversely with real yields (10-year TIPS). Gold looked "expensive" against its long-run real value. | Erb & Harvey 2013, *FAJ* 69(4) https://papers.ssrn.com/abstract=2078535 *(unverified)* | 1975–2012 | R (pre-2022) |
| Gold is sensitive to expected long-term real rates and to macro pessimism. | Barsky et al. 2021, *Chicago Fed Letter* 464 https://www.chicagofed.org/publications/chicago-fed-letter/2021/464 | 1970s–2021 | R (pre-2022) |
| Gold is a hedge against equities and a short-lived safe haven (about 15 trading days after a shock). | Baur & Lucey 2010, *Financial Review*; Baur & McDermott 2010, *JBF* | 1979–2009 | R |
| Gold responds to geopolitical *threats*, not to realised acts. | Baur & Smales 2020, *JBF* 117 https://ideas.repec.org/a/eee/jbfina/v117y2020ics037842662030090x.html ; GPR index: Caldara & Iacoviello 2022, *AER* https://www.matteoiacoviello.com/gpr.htm | 1985–2018 | S→R |
| **Break:** the real-yield correlation fell from about 0.84 (2005–21) to about 0.03 (2022–23). JPMAM puts the R² at 85% → 16% after 2022. | RBC WM https://www.rbcwealthmanagement.com/en-asia/insights/golds-regime-change ; Janus Henderson https://www.janushenderson.com/social/article/chart-to-watch-whats-behind-the-divergence-between-gold-and-real-treasury-yields/ | 2005–2025 | S (practitioner; replicated in direction) |
| Central banks bought more than 1,000 t a year in 2022–24 and 850 t in 2025. Gold was 27% of official reserves at end-2025. | ECB, *International role of the euro* 2026 https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.pr260602~f941e87516.en.html ; Arslanalp, Eichengreen, Simpson-Bell 2023, IMF WP/23/14 *(unverified)* | 2000–2025 | R (data) |
| The WGC GRAM attributes monthly returns to opportunity cost, risk, momentum and flows. Momentum and the residual have been large in 2024–26. | WGC https://www.gold.org/goldhub/data/short-term-gold-price-drivers | Monthly | S (vendor-neutral industry body) |

**For goldbot:**
- Do not hard-code "real yields up ⇒ short gold". The sign held for 20 years and then vanished.
- Official-sector buying is price-insensitive and reported with a lag of months (IMF IFS), so it cannot be traded
  intraday.
- Macro data is useful as **conditioning or diagnostic features at daily frequency**, not as intraday triggers.

### B2. Intraday and session patterns

- **London PM fix (pre-2015):**
  - Trades in the first minutes of the fix predicted its direction, in some cases more than 90% of the time.
  - Informed traders profited before publication.
  - Source: Caminschi & Heaney 2014, *J. Futures Markets* 34(11)
    (https://ideas.repec.org/a/wly/jfutmk/v34y2014i11p1003-1039.html). GC futures and GLD, about 2008–2013. Gross.
    Grade S.
  - The effect was leakage that led to fines (Barclays 2014). The fix was replaced by the IBA electronic auction in
    2015. Do not expect it to persist.
- **Volatility around releases:**
  - Intraday gold volatility is dominated by US releases (NFP, CPI): Cai, Cheung, Wong 2001, *JFM*
    (https://scholars.hkbu.edu.hk/en/publications/what-moves-the-gold-market/). COMEX 1994–97.
  - Prices adjust within minutes: Elder, Miao, Ramchander 2012, *JBF* 36(1) (*unverified*). 2002–08.
  - This is about volatility, not direction. Grade R.
- **Asia up, West down:**
  - Gold "robustly" rises in Eastern hours and falls for the rest of the day, a hat-shaped pattern: Donati & Jung,
    CBS MSc thesis (https://research.cbs.dk/en/studentProjects/gold-price-dynamics-around-the-clock/). Grade S, weak.
  - Overnight returns are negative and day returns positive: Blose, GVSU working paper
    (https://scholarworks.gvsu.edu/fsdg/200). Grade S.
  - H1 2026: about +13% in Asian hours and −15% in US hours, citing WGC, IBA and Bloomberg data (Caixin 2026,
    https://www.caixinglobal.com/2026-07-03/analysis-gold-caught-in-a-tug-of-war-between-asian-bulls-and-a-hawkish-fed-102460438.html).
    Gross. **This falls inside our holdout (2025-10..2026-09), so it is contaminated as evidence.**
  - Plausible mechanism: Chinese and Indian physical and ETF demand clears in Asian hours, while Western
    futures and ETF selling dominates in US hours.
- **Weekend and weekday effects:**
  - Weekend returns are weaker and depend on whether gold is in a bull or bear market: Blose & Gondhalekar 2013,
    *Accounting & Finance* 53(3) (https://ideas.repec.org/a/bla/acctfi/v53y2013i3p609-622.html).
  - Other studies conflict (Lucey & Tully 2006, and regional spot-market studies).
  - Grade S, fragile.

### B3. Documented gold anomalies and what survived

| Anomaly | Evidence | Survived costs / later samples? |
|---|---|---|
| Momentum / trend (monthly) | Part of every TSMOM panel (MOP 2012; Hurst et al. 2017). Commodity momentum: Miffre & Rallis 2007, *JBF*. GRAM shows momentum as a monthly driver. | Yes, but only diversified and slow. For gold alone the evidence is weak (Huang et al. 2020). |
| Short-term trend (minutes to hours) | Kurth et al. 2026; our tsmom 1h/4h | **No** since about 2009 on small-tick contracts |
| Pre-FOMC drift | Lucca & Moench 2015, *JF* (equities). Fixed income showed none (NY Fed SR512 https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr512.pdf). | **No**: disappeared after 2015 (Kurov, Wolfe, Gilbert 2021, *FRL* 40 https://ideas.repec.org/a/eee/finlet/v40y2021ics1544612320315956.html). No gold-specific evidence. |
| FOMC/CPI surprise reaction | Gold reacts within minutes (Elder et al. 2012) | Absorbed too fast for 15m bars. No documented drift after the release. |
| London fix leakage | Caminschi & Heaney 2014 | No; the venue changed in 2015 |
| Asia-session drift | Thesis, working paper, 2026 press | Untested net of costs; never out of sample |
| COT positioning | Hedging pressure timed gold well in 2000–06 (Basu, Oomen, Stremme, working paper, *unverified*). Speculative pressure works across commodities (Fan, Fernandez-Perez, Fuertes, Miffre 2020, *JFM*, *unverified*). Rouwenhorst & Tang 2012 (*ARFE*) find no prediction. | Mixed. Weekly frequency gives fewer than 1,000 events. |
| Weekend / Monday | Blose & Gondhalekar 2013 | Fragile; regime-dependent |

---

## C. Implications for goldbot, ranked

Costs set the ranking.
- The round trip (spread + commission + slippage) is fixed in USD/lot, so a hypothesis is only worth a trial if its
  expected move is several times that.
- Swap at about −60 USD/lot/night (about −1% of a daily-ATR R per night at 2025–26 prices, up to −4% at 2015 prices) punishes long holds on the long side
  only.

### C1. Hypotheses for Q1 2027 pre-registration

1. **Slow tsmom: daily signal, multi-day hold, long and short, measured swap.**
   - Rationale: the only trend horizon the literature supports (A1). A larger target makes the spread a smaller
     fraction of R, and the short side earns no swap cost.
   - Expected edge: gross ≈ +0.05–0.10 R. Holding about 10 nights costs the long side about 0.05–0.1 R, so the
     result likely hinges on the short side and on measured swap.
   - Horizon: 1d signal, 4h execution, 5–20 days.
   - Issue: the 1d version had only 454 events, below P4's 1,000. Either run it on 4h bars with a 1d trend condition
     and a long `max_bars`, or the OWNER rules on the event minimum for daily families.
   - This supersedes the planned `tsmom 4h max_bars 12`, which is likely to land near −0.01 R before swap.
2. **Asia-session drift (long in Asian hours, flat before London).**
   - Rationale: physical and ETF demand clearing in Asia (B2). It is intraday, so it pays no swap. About 4,000
     events from 2010 to 2025-09 easily pass P4.
   - Expected edge: unknown. The only quantified evidence is in-holdout press. Assume ≤0.03% a session against a
     round trip of about 0.01%, which would be 2–3× costs.
   - Horizon: about 00:00–07:00 UTC, using the 1h bars.
   - Write the pre-registration on pre-holdout data only. Expect decay (McLean–Pontiff).
3. **Daily macro-conditioned trend.**
   - Gate slow tsmom on the 20-day change in DFII10 and the broad dollar.
   - Rationale: B1 relations were strong from 2005 to 2021 and broke in 2022–23. The trial's purpose is to learn
     whether the conditioning still adds anything net of costs.
   - Expected edge: small. It costs only 1 trial if run as a variant of hypothesis 1.
   - The reading rule must require 2022–24 to be positive, since that is the regime test.
4. **Post-release continuation (CPI, NFP, FOMC) on 15m→4h.**
   - Rationale: large surprises create a volatility burst that is several times the spread (B2).
   - Evidence of directional drift: none. This is a single exploratory trial at low priority.
   - It needs a timestamped surprise calendar as data first.

### C2. Data to add (all daily or slower; join with `asof_join` on `available_utc`)

| Source | Series | Latency to model |
|---|---|---|
| FRED | DFII10 (10-year TIPS), T10YIE (breakevens), DTWEXBGS (broad USD), GVZCLS (gold VIX) | Next day; publication-date lag |
| CFTC | Disaggregated COT, gold managed-money net | Tuesday data released Friday 15:30 ET |
| SPDR / iShares | GLD and IAU daily holdings in tonnes (ETF flows) | Next day |
| Caldara–Iacoviello | Daily GPR index | Next day |
| CME | GC front-month vs spot basis (implied lease rate) | For modelling swap as rate × notional |
| Economic calendar with consensus | CPI, NFP, FOMC times and surprises | Needed for hypothesis 4 |

IMF IFS central-bank reserves are monthly with a 1–2 month lag. Use them as context only.

### C3. Do not spend trials on

- Any 15m/1h trend, breakout, or intraday momentum variant. This is our null result, and Kurth et al. 2026 predict
  it for small-tick markets.
- Meta-labelling or ML on price features over primaries with no gross edge. AUC 0.50 is expected (A5).
- Volatility-managed overlays as alpha (Cederburg et al. 2020; Harvey et al. 2018 find no effect for commodities).
- Pre-FOMC drift (gone after 2015), London-fix front-running (venue reformed), and weekday or weekend effects
  (fragile).
- Real-yield sign rules on their own (broke in 2022).
- Short-horizon mean reversion. No cost-surviving evidence exists for gold.

### Owner decisions flagged

- The P4 event minimum for daily families.
- Whether, after hypotheses 1–2, the budget would be better spent on other small-tick or large-tick instruments.
  Kurth et al. suggest large-tick contracts keep trend.
- Raising `trial_budget_quarter` is not recommended; the evidence base does not justify more trials.
