# XAUUSD trader playbook and gap analysis

Written 2026-10-10 by the strategy researcher, in answer to the owner's request: "review and analyse what a successful
trader should consider, what affects gold ... all the concepts, including risk management". It lists what a
consistently profitable professional XAUUSD trader checks, and for each item gives the evidence and goldbot's status.

It builds on, and does not repeat, `state-of-the-art.md` ("SoA", section ids such as B1), `indicator-survey.md`
("survey #n" = universe row) and `hypotheses.md` (H-nn, R-nn). Code citations are `file:line` on base `bb22940`.

**How to read the tables.**
- **Grade.** **R** = replicated (several samples or authors). **S** = single study, or industry/official data
  without an independent test. **P** = practitioner consensus, with no rigorous test. **F** = folklore: widely
  repeated, no evidence found.
- **Use.** **Edge** = evidence of a return effect (the column says if it is net of costs). **Edge?** = weak, mixed
  or untested. **Risk** = it changes the risk taken or avoided, not expected return. **Ctx** = context for regime or
  diagnosis only.
- **Status.** **impl** = implemented (with a citation), **part** = partial, **miss** = missing.
- Sources fetched for this review carry a URL. A source marked *(unverified)* was cited from memory or from a
  secondary summary. Treat it as a lead, not as evidence.
- **Holdout warning.** Market commentary dated 2025-10-01 .. 2026-09-30 is inside our holdout. It is cited here to
  explain mechanisms. It **must not** be used to choose or tune a hypothesis (the same rule as SoA B2 for the
  Asia-drift press).

**Scale today (matters for sizing).** The 2025 average LBMA PM price was $3,431/oz (WGC FY2025,
https://www.gold.org/download/file/20498/FY_2025_GDT_Press_Release.pdf). Gold was about $4,608 at end-March 2026
(WGC via Times of Oman, https://timesofoman.com/article/170483-gold-prices-record-worst-monthly-drop-since-2013-with-12-fall-in-march,
in holdout). At about $4,000–4,600:
- 1 lot (100 oz) is $400–460k notional. A $1 move is $100 per lot and $1 per 0.01 lot.
- 1:20 margin is $20–23k per lot.
- The long swap prior (−60 USD/lot/night, `config/settings.yaml:67`) is about 5% a year of notional.

---

## 1. What moves gold

| # | Driver | Why it matters (source) | Grade | Use | goldbot status |
|---|---|---|---|---|---|
| 1.1 | **Real yields / TIPS** | Opportunity cost. Strong inverse relation 1975–2021, broken after 2022 (corr ≈0.84 → ≈0.03). SoA B1 (Erb & Harvey 2013; Barsky et al. 2021; RBC WM; Janus Henderson) | R before 2022, S after | Ctx; Edge? as a conditioner | **part.** DFII10 and T10YIE pulled with publication-lag `available_utc` (`goldbot/data/macro.py:21-29`, `:66`). Features: 20-obs change and 1-year z (`goldbot/features/macro.py:47`). H-06 tests the conditioning. No model uses it yet |
| 1.2 | **USD (DXY / broad dollar)** | Gold is priced in USD. The dollar rose while gold fell in the March 2020 liquidation (Gulf News summary of Refinitiv, https://gulfnews.com/amp/story/business%2Fretail%2Fwhat-triggered-golds-march-fall-and-why-april-looks-different-1.500504903). Contemporaneous, weakly predictive (survey #58) | R contemporaneous | Ctx | **part.** DTWEXBGS is weekly (H.10) and joined with its lag (`goldbot/data/macro.py:69`). **No intraday USD** (EURUSD, USDJPY, DXY futures): survey #62, D+ data |
| 1.3 | **Fed path and FOMC** | 5 minutes after FOMC, gold returns and volatility react more to dovish than hawkish surprises, and adjustment runs past 5 minutes (Awartani, Hussain, Virk 2024, high-frequency, https://pure.kfupm.edu.sa/en/publications/how-do-the-gold-intra-day-returns-and-volatility-react-to-monetar/ ; venue *(unverified)*). Daily-futures evidence has the opposite sign for rate surprises (Gospodinov & Jamali, Atlanta Fed WP, 1998–2008, https://www.centralbanking.com/central-banks/monetary-policy/2317490/unexpected-increases-in-fed-funds-rate-prompt-gold-prices-to-rise). Pre-FOMC drift gone after 2015 (SoA B3) | S (sign unstable by horizon) | Risk (volatility burst); Edge? (dovish continuation) | **part.** FOMC statement and presser are tier-1 blackouts (`goldbot/data/econ_calendar.py:24-29`, `config/settings.yaml:55`). DGS2 and DFF pulled (`macro.py:24-26`). **No fed-funds-futures implied path. No surprise measure.** Fed chair speeches and FOMC minutes are not blacked out |
| 1.4 | **CPI, NFP, PCE and other tier-1 releases** | Intraday gold volatility is dominated by US releases, and prices adjust within minutes (SoA B2: Cai, Cheung, Wong 2001; Elder et al. 2012). US macro news predicts about 34% of intraday gold jumps, with the FOMC dominant (MDI paper, https://mdi.ac.in/research/what-triggers-intraday-price-jumps-and-co-jumps-in-gold *(venue unverified)*) | R for volatility; N for direction | Risk | **impl** for CPI, NFP, FOMC, PCE: −15/+30 min no entries (`goldbot/engine/runner.py:1555`, `goldbot/risk/gate.py:199-200`). Early close if re-scored p < 0.5 (`runner.py:871-908`). Minutes to and since a tier-1 event as features (`goldbot/features/session.py:57-81`). **Tier-2 (PPI, retail sales, ISM, GDP, JOLTS, jobless claims, Fed speakers, minutes) has neither a blackout nor a feature** |
| 1.5 | **Central-bank buying** | Price-insensitive official demand. WGC: 863 t in 2025 including unreported buying (FY2025 GDT, above). Reported purchases were 328 t in 2025: Poland 102 t, Kazakhstan 57 t, SOFAZ 53 t, Brazil 43 t, China 27 t, Turkey 27 t (WGC via Kitco, https://www.kitco.com/news/article/2026-02-04/central-banks-buy-19t-gold-december-total-328t-2025-averaging-27tm-world). Turkey can also be a seller: about 50 t used in swaps in March 2026 (WisdomTree, https://www.wisdomtree.com/investments/blog/2026/04/17/the-month-gold-broke-five-lessons-from-the-march-madness-selloff-and-the-rebound-opportunity, in holdout). Data arrive monthly, 1–2 months late (SoA B1) | R (data) | Ctx (level driver, untradeable intraday) | **miss.** Correctly so as a signal. A monthly context series for the pre-session brief is optional (survey #70: high PIT risk) |
| 1.6 | **ETF flows** | Global ETF holdings rose 801 t in 2025 (WGC FY2025). Western flow drives momentum episodes: WGC's GRAM attributed March 2026's −12% mostly to ETF outflows (about $12bn), a COMEX net-long unwind and trend reversal (Gulf News / WisdomTree summaries, in holdout). Price pressure 1–2 days, then reversal at 3–5 (thesis-level, survey #68) | S | Ctx; Edge? (H-12) | **part.** GLD-tonnes loader (`goldbot/data/macro.py:237`) is **not in the `macro-v1` release** (`macro.py:66`, `scripts/fred_macro.py:30`), so no model sees it |
| 1.7 | **COT positioning** | Speculative pressure works across commodities. Mixed for gold (SoA B3; survey #67). CTA positioning amplifies breaks of the 50/55-day average (WGC commentary on March 2026, in holdout) | S, mixed | Edge? (feature, H-09) | **part.** CFTC disaggregated loader exists (`goldbot/data/macro.py:212`). Not in the release, so not in models |
| 1.8 | **Geopolitics / haven bids** | Gold responds to geopolitical *threats*, not realised acts (Baur & Smales 2020; GPR index, SoA B1). The haven effect is short-lived, about 15 trading days (Baur & Lucey 2010, SoA B1) | S→R | Ctx; Edge? (H-07) | **part.** LLM-scored headlines with a 30-min shock blackout (`config/settings.yaml` `news:`, `runner.py:1555-1566`). VIX and S&P drawdown features (`goldbot/features/macro.py:30-48`). **No daily GPR series** (survey #66) |
| 1.9 | **Risk-off liquidation (gold falls in crashes)** | In acute deleveraging, gold is sold to meet margin calls and redemptions. In 2020 it fell about 10% from 24 Feb to 16 Mar (ii.co.uk citing AJ Bell, https://ii.co.uk/analysis-commentary/why-did-safe-haven-gold-fall-value-during-market-sell-ii512586), driven by VaR-forced cross-asset selling (Refinitiv via Mining Weekly, https://www.miningweekly.com/article/recent-gold-plummet-the-result-of-multiple-factors-says-refinitiv-2020-04-21). The same pattern occurred in 2008 and again in March 2026 (−12%, worst month since June 2013, WGC summary, in holdout). It usually rebounds once the dash for cash ends *(pattern; no systematic test found)* | S (episodes) | Risk (correlation flips to +1 with equities); Edge? (rebound) | **part.** The equity-stress features exist, but **no rule recognises the liquidation state**. H-07 assumes haven *buying* after equity shocks, the opposite of a liquidation. Candidate C-1 below separates the two |
| 1.10 | **China / India physical demand and seasonality** | Jewellery fell 18% in tonnes in 2025 against a 67% price rise, so demand is price-elastic. Bar and coin hit a 12-year high at 1,374 t (WGC FY2025). Akshaya Tritiya (Apr–May) is India's second-largest buying day after Dhanteras/Diwali (Oct–Nov). Indian buyers delay purchases into price spikes. Chinese Q1 demand is strong into Lunar New Year (Reuters "Asia Gold" via Business Standard, https://www.business-standard.com/article/reuters/low-gold-prices-spur-buying-in-asia-china-premiums-rise-117051201048_1.html). The autumn effect faded and was replaced by a "winter effect" (survey #82; Baur 2012 seasonality, *(unverified)*) | S / W | Edge? (weak); Ctx | **miss.** No festival, Lunar-New-Year or Shanghai-premium calendar (survey #69, #81, #82). Asia-session drift is H-04 |
| 1.11 | **Mining supply** | Mine output is inelastic: 3,672 t in 2025, +1%; recycling 1,404 t, +3% despite record prices; producer de-hedging −74 t (WGC FY2025 supply, https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-full-year-2025/supply) | R (data) | Ctx (irrelevant below monthly) | **miss.** Correctly so. Not a trading input at our horizons |
| 1.12 | **2022 regime break** | Real-yield beta collapsed. Central-bank and Asian demand took over. WGC GRAM residuals and momentum were large in 2024–26 (SoA B1) | S→R | Risk (model decay) | **impl** as a governance response. Reading rules must hold in 2022–24 (H-06). Positive expectancy in ≥ 3 years including 2021/2022 (`goldbot/research/gates.py`). Drift PSI/CUSUM halts (`goldbot/research/drift.py`, `config/settings.yaml` `drift:`) |
| 1.13 | **Silver** | Shared precious-metals factor; silver is more cyclical. No out-of-sample journal test of the gold/silver ratio found (survey #61) | W | Ctx; Edge? | **miss** (D+ data, owner decision) |
| 1.14 | **Oil** | Inflation and growth proxy. Weak and time-varying link (survey #64). No dedicated daily-return study found in this review | W | Ctx | **miss** (D+) |
| 1.15 | **Equities** | Gold is a hedge on average and a short-lived haven (Baur & Lucey 2010), but correlation turns positive in liquidations (1.9) | R | Risk; Ctx | **part.** VIXCLS and SP500 as daily features (`goldbot/features/macro.py:30`). No intraday index |
| 1.16 | **BTC** | Return correlation with gold is small and unstable: about 2% for 2013–19 (Cointelegraph analysis, https://cointelegraph.com/markets/new-data-suggests-bitcoin-and-gold-arent-as-correlated-as-you-think). Positive in a 2019–22 DCC study (cited in the same search) | S, unstable | none | **miss**, and correctly so. Do not add |
| 1.17 | **Options expiry** | COMEX gold options expire 4 business days before the end of the month preceding the contract month, moved earlier if that day is a Friday or a pre-holiday (CME Rulebook ch. 115, https://cmegroup.com/content/dam/cmegroup/rulebook/COMEX/1a/115.pdf). Futures last trade is the third-last business day (CME fact card). Pinning around strikes is **F/P** for gold: no gold-specific study found | P/F | Edge? (feature only) | **miss.** The expiry and first-notice calendar is deterministic and free (survey #80) |
| 1.18 | **Round-number levels** | Round numbers act as barriers, changing the conditional mean and variance of gold prices. The $100 levels are where gold is less likely to keep going (Aggarwal & Lucey 2007, *Rev. Financial Economics* 16(2), https://ideas.repec.org/a/eee/revfin/v16y2007i2p217-230.html ; daily and intraday gold). Stop and take-profit clustering at round numbers (Osler 2003/2005, FX; survey) | S (gold), R (FX clustering) | Edge? (H-05, H-08) | **part.** $5/$10/$25/$50 distance and touch counts (`goldbot/features/survey.py:56`, `:120-140`). **No $100 step**, the one level the gold study singles out, and the natural one at $4,000+ |

## 2. Market microstructure of XAUUSD CFDs

| # | Item | Why it matters (source) | Grade | Use | goldbot status |
|---|---|---|---|---|---|
| 2.1 | **Where liquidity is** | About $361bn/day in 2025: OTC $180bn (mostly London spot), futures $174bn (COMEX $114bn, SHFE $51bn), ETFs $7bn (WGC, https://www.gold.org/goldhub/data/gold-trading-volumes). A spot CFD is a price derived from London OTC and COMEX, so liquidity follows the London and New York hours | R (data) | Risk/cost | **impl** as measurement: spread and slippage tables per session from the account's own ticks and fills (`goldbot/execution/costs.py:152`, `:170`) |
| 2.2 | **Spreads by session** | Widest at the daily reopen and in the Asian session; tightest in the London–NY overlap (P). Fixed USD costs mean cost in R varies 2–3× with volatility (SoA A7) | P | Risk/cost | **impl.** Session tables (`costs.py:112-135`). The gate uses the round trip in ATR (`runner.py:1688-1704`) and the 2.5× target floor (`gate.py:225-226`). **part:** the spread cap is a fixed 45 points (`config/settings.yaml:53`, `gate.py:211`), not relative to the session's measured p90 |
| 2.3 | **Rollover and swap; triple-swap Wednesday** | Swap is charged at server midnight (17:00 New York). Wednesday's rollover charges three nights, because spot settles T+2 across the weekend (P; broker convention) | P (mechanics are certain) | Cost | **part.** Labels and research charge swap per rollover, with the triple day (`goldbot/execution/costs.py:62-67`, `config/settings.yaml:67-69`). **The live gate's EV ignores swap**: `_cost_atr` is the round trip only (`runner.py:1688-1704`), and `ev = pT − (1−p)S − c` (`gate.py:222`) charges nothing for nights held. Harmless for 15m/1h. Material for 4h and for H-01 longs (about 0.05–0.10 R over 10 nights, H-01 row) |
| 2.4 | **Daily break** | Server 23:59–01:02 (`goldbot/data/calendar.py:20-24`), which is 21:59–23:02 UTC in summer and 22:59–00:02 UTC in winter. Spreads spike into the close and for the first minutes after the reopen (P) | P | Risk | **part.** No entries within ±5 min of server midnight (`runner.py:1099-1104`, `gate.py:201-202`). That window misses most of the **post-reopen** spike (01:02 server onward), which only the fixed spread cap catches. Bars across the break are not formed (DESIGN Data architecture) |
| 2.5 | **Weekend gaps** | Stops do not protect across a gap: the fill is at the reopen price. Thin Monday-Asia liquidity amplifies moves. On 9 Aug 2021, gold fell about $80 (≈4.5%) at the Asian open, on a Japanese holiday, after a strong NFP. The move was forced futures liquidation into "zero liquidity", and it recovered most of the fall before Europe (Kitco, https://www.kitco.com/opinion/2022-07-21/gold-bounces-1678-low-august-2021-flash-crash ; XTB, https://xtb.com/en/market-analysis/chart-of-the-day-gold-09-08-2021) | S (episodes) | Risk | **part.** Friday 21:30 server: losers closed, winners' stops tightened to lock in half the open profit, no entries until reopen (`runner.py:1099-1150`). **Winners are still held over the weekend.** Labels fill gapped stops at the stop, which is optimistic (`goldbot/labels/exit_policy.py:25-28`, documented). **No Monday-open entry delay** |
| 2.6 | **Holidays and thin sessions** | US, UK, Japanese and Chinese holidays, Christmas to New Year, and early closes thin liquidity. The flash crash in 2.5 happened on one (P; episode) | P | Risk | **miss.** The engine knows only the weekly session table (`calendar.py:1-6`). MT5 `session_deals` carries no holiday schedule *(unverified for ICM)*. No holiday calendar anywhere in `goldbot/` (grep) |
| 2.7 | **Slippage on news; stop runs** | Market orders and stops slip in the release minute. The paper broker models 20 points of adverse stop slippage (DESIGN Broker abstraction) | P | Risk | **impl.** No entries in the blackout (1.4). Measured slippage table by session (`costs.py:170`). Server-side SL/TP at entry (DESIGN Order lifecycle). Exits never gated |
| 2.8 | **Broker stop level, lot step, contract size** | Read from `symbol_info`, never hard-coded (DESIGN) | certain | Risk | **impl.** The stop is floored at the stop level plus spread (`gate.py:248`). Lot step and minimum come from the intent (`gate.py:251-256`) |
| 2.9 | **London AM/PM auctions (fixes)** | IBA's LBMA Gold Price auctions run at 10:30 and 15:00 London time, in 30-second rounds after a 30-minute order window (https://www.ice.com/iba/lbma-gold-silver-price ; https://www.lbma.org.uk/lbma-prices-summary). The pre-2015 front-running edge is gone (SoA B2, B3). Volume concentrates around the auctions (P) | S | Edge? (microstructure, 15m), Risk | **miss.** No auction-window feature. The fixed UTC session windows (`calendar.py:28-33`) do not move with UK DST, so 10:30 London is 09:30 UTC in summer and 10:30 UTC in winter |
| 2.10 | **COMEX open and the 08:30 ET data slot** | CME Globex gold trades Sun–Fri 18:00–17:00 ET with a 60-minute break (CME fact card, https://www.cmegroup.com/content/dam/cmegroup/market-regulation/files/gold-futures-and-options-fact-card.pdf). The old pit open (08:20 ET) still coincides with US data at 08:30 ET, the day's biggest volatility point (P) | P | Risk; Edge? | **part.** The NY session opens at a fixed 12:30 UTC (`goldbot/features/session.py:13`). 08:30 ET is 12:30 UTC only in US summer; it is 13:30 UTC in US winter. US and EU DST flags are features (`session.py:29-30`), but the session windows, zones and cost-table sessions are fixed in UTC |
| 2.11 | **IC Markets specifics** | Server `ICMarketsSC-Demo`, server time Europe/Athens (`config/accounts.yaml`). Commission prior $3.5/lot/side (`config/settings.yaml` `costs:`). Raw-account classification is measured (`goldbot/execution/classifier.py`). ICMarketsSC is the Seychelles entity: ESMA/FCA retail rules (1:20 on gold, 50% margin close-out, negative-balance protection; ESMA 2018, https://www.esma.europa.eu/node/84933) may not apply there *(unverified; check the SC client terms)*. The broker's own stop-out level must be read from `account_info` *(unverified)* | certain / unverified | Risk | **impl.** goldbot self-imposes 1:20 and a 300% margin-level floor whatever the broker allows (`gate.py:47-48`, `:159-182`, `:258-263`). ICM's official spec page could not be fetched (404). Swap, commission and stop level are measured from the terminal (`runner.py:136-139`), not from a web page |

## 3. Technical and price-action practice

Most of this is weak or folklore as a *standalone* edge (survey §3; R-09). The professional use is as context and
timing inside a process that already has an edge. goldbot treats these as point-in-time features screened in H-02,
not as rules.

| # | Practice | Evidence | Grade | Use | goldbot status |
|---|---|---|---|---|---|
| 3.1 | Multi-timeframe structure (higher-TF trend, lower-TF entry) | Slow trend is the only robust horizon (SoA A1). MTF alignment itself is untested | P | Edge? | **impl.** The HTF bar is visible only after it closes (DESIGN Multi-timeframe rule; `goldbot/features/mtf.py`). H-01 uses a 1d signal with 4h execution |
| 3.2 | Session ranges (Asia, London, NY), previous day and week high/low | Session-open breakout failed here (R-02). Asia drift is untested (H-04) | P / S | Edge? | **impl** as features: `goldbot/features/trader.py:131` (session zones), previous day/week levels (`trader.py:107-128`). **part:** windows are fixed UTC, not DST-aware (2.9, 2.10) |
| 3.3 | Support and resistance | Swing levels with touch counts. Round-number barriers (1.18) | P; S for round numbers | Edge? | **impl:** `goldbot/features/structure.py:68` (confirmed levels, fractal lag) |
| 3.4 | Liquidity sweeps / stop hunts | Mechanism: stop clustering (Osler 2005). Untested in gold (H-08) | S (FX) | Edge? | **impl** as a feature: `goldbot/features/trader.py:168` |
| 3.5 | FVG, order blocks, premium/discount ("SMC") | No peer-reviewed support (survey §3) | F | none known | **impl** as features: `trader.py:215`, `:258`. Retired as rules (R-09) |
| 3.6 | VWAP | Anchored session VWAP is an execution benchmark for institutions. As a signal, it failed with mean reversion (R-01) | P | Risk (execution), Edge? | **part.** A rolling 48-bar tick-weighted VWAP-like distance (`goldbot/features/technical.py:226-227`). No session-anchored VWAP. CFD tick count is not volume (DESIGN) |
| 3.7 | ATR regimes | Cost in R falls as volatility rises (SoA A7). Volatility is persistent (HAR; survey 1c) | R | Risk; Ctx | **impl.** ATR stops and sizing (`gate.py:248-250`). Yang–Zhang and GK estimators (`goldbot/features/survey.py:97`). Expected move in round-trip costs (`survey.py:222`). H-13 cost filter (0 trials) |
| 3.8 | Trend vs range detection | ADX and EMA alignment. Variance ratio, Hurst, efficiency ratio. A regime filter halves events and has no single-asset net evidence (SoA A6) | P / W | Ctx | **impl.** `technical.py:58` (ADX), `survey.py:175` (VR/Hurst/ER), rule allocator (`goldbot/allocator/rules.py:36-45`) |
| 3.9 | 15-minute scalping | Costs 0.2–0.4 R per round trip. Q4 15m families had no gross edge (R-01, R-02, R-05). Short-term trend has been dead on small-tick contracts since about 2009 (Kurth et al. 2026, SoA A1) | R (against) | Edge? only with a microstructure mechanism | **part.** H-14 (cost-gated 15m discovery). Account-class rule disables 15m on Standard accounts (`gate.py:229-234`) |

## 4. Risk management

| # | Practice | Why (source) | Grade | Use | goldbot status |
|---|---|---|---|---|---|
| 4.1 | **Risk per trade 0.25–1%** | Survival first. The ESMA retail data show 74–89% of CFD accounts lose (SoA A7) | P | Risk | **impl.** 0.5%, 0.1% tiny-live (`config/settings.yaml:42-43`). 1% hard clamp after the multiplier (`gate.py:245-247`) |
| 4.2 | **ATR-based stops** | Normalises risk to volatility. Stop-loss rules add value only when returns have momentum. Under a random walk they cut expected return (Kaminski & Lo 2014, *J. Financial Markets* 18, https://dspace.mit.edu/handle/1721.1/114876) | S (theory + equity/futures tests) | Risk | **impl.** `stop_atr × ATR`, floored at the stop level plus spread (`gate.py:248`). Server-side stop at entry |
| 4.3 | **Position sizing, fixed-fractional** | Lots = equity × risk × m / (stop × 100 oz) | certain | Risk | **impl:** `gate.py:248-256`. A trade is skipped if the minimum lot exceeds 1.2× target risk (`gate.py:255-256`) |
| 4.4 | **Min-lot feasibility** | At $4,000+ and a 1.5 × ATR(1d) stop (roughly $75–170/oz at 2025–26 volatility, *estimate*), 0.01 lot risks $75–170. The 1.2× rule then needs equity ≥ $12.5k–28k at 0.5% and ≥ $62k–142k at the 0.1% tiny-live rate | arithmetic | Risk | **gap (analysis only).** The gate refuses correctly (`min_lot_exceeds_risk`). But **H-01 cannot trade at tiny-live on a small account**: an owner decision before H-01 can go live |
| 4.5 | **Vol-targeting** | Helps equities, not commodities (SoA A2). Already implicit: per-trade ATR sizing | R | Risk | **impl** at trade level. No portfolio vol target, correctly (SoA C3) |
| 4.6 | **Fractional Kelly** | Full Kelly maximises long-run growth but its short-run risk is large. Fractional Kelly keeps most of the growth with much less risk (MacLean, Thorp, Ziemba 2010, *Quantitative Finance*, https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf). Kelly on an estimated edge is dangerous: use the lower confidence bound | R (theory) | Risk | **part.** Size multiplier = capped linear map of p above break-even (`goldbot/research/metrics.py:52-57`), bounded 0.25–1.5 (`gate.py:239-240`). **No ceiling tied to the measured edge.** A fractional-Kelly cap from the lower bound of shadow expectancy would set risk to zero for a strategy with no proven edge (proposal G-10) |
| 4.7 | **Daily and weekly loss limits** | Bound the loss-chasing in 4.13. Pod shops cut at 5–7% (DESIGN "best systematic traders") | P | Risk | **impl.** 2% daily and 5% weekly per account (`gate.py:215-220`). Supervisor combined 1.5% / 4% (`goldbot/risk/supervisor.py:62-65`). Owner alerted (`goldbot/ops/health.py` `check_loss_caps`) |
| 4.8 | **Max-drawdown circuit breakers** | Drawdown control: Grossman & Zhou 1993, *Math. Finance* 3(3) (risk in proportion to the surplus over a drawdown floor, https://ideas.repec.org/a/bla/mathfi/v3y1993i3p241-276.html). The staged size-down approximates it | R (theory) | Risk | **impl.** 8% halves risk, 12% closes everything (`gate.py:139-149`, `runner.py:1071-1097`). Re-arm with probation (`runner.py:117-120`). Drift halt at 1.5× backtest drawdown (`config/settings.yaml:149`). Stop rule P6 (`goldbot/ops/gates_phase.py:320`) |
| 4.9 | **Risk of ruin / expected drawdown** | A driftless strategy's expected maximum drawdown grows like 1.25·σ·√N: about 28 R after 500 trades, which is 14% at 0.5% risk (Magdon-Ismail et al. 2004, *J. Applied Probability* *(unverified)*). So a zero-edge system trips the 12% switch within a few hundred trades. That is the switch working, but nobody has computed when a *positive*-edge system trips it by bad luck | R (math) | Risk | **miss.** No Monte Carlo of drawdown against the 8%/12%/weekly caps from shadow or backtest R (proposal G-5) |
| 4.10 | **Correlation and exposure caps** | Two accounts on one instrument are one position (DESIGN) | certain | Risk | **impl.** Combined lots and notional cap (`gate.py:264-270`), 2 positions per account (`gate.py:213-214`). **part:** no cap on total open risk in R ("heat") or on same-direction stacking by different agents. Two longs at 1% each are one 2% bet on gold |
| 4.11 | **News blackout windows** | 1.4 | R (volatility) | Risk | **impl** for tier-1 and unscheduled shocks. **miss** for tier 2 |
| 4.12 | **Weekend and holiday flat rules** | 2.5, 2.6 | P | Risk | **part** (weekend). **miss** (holidays, Monday-open delay) |
| 4.13 | **Leverage and margin** | 1:20 retail cap (ESMA 2018). Margin close-out at 50% for EU/UK retail | R (regulation) | Risk | **impl.** max(broker margin, 1:20), 300% floor (`gate.py:159-182`, `:258-263`). Combined notional ≤ 6× equity (`gate.py:49-52`) |
| 4.14 | **Trailing, breakeven, partial exits** | Exits are where momentum edges are realised (DESIGN). Stops add value only with momentum (Kaminski & Lo 2014). "Move to breakeven at +1R" is **F**: no study found, and it raises the stop-out rate under noise | S / F | Edge? (exit design is a trial dimension) | **impl:** trail and scale-out policies shared by labels, shadow and live (`goldbot/labels/exit_policy.py:46-48`, `runner.py:678-716`, `:819-864`). Hard flat (M7). Weekend tighten. **No breakeven rule**, correctly until tested |
| 4.15 | **Expectancy and R-multiples** | R-multiple accounting (Van Tharp, practitioner *(unverified)*) | P | Measurement | **impl.** `metrics.py:9`, `:84` (expectancy in R). `ClosedTrade` carries `r` (`runner.py:1497`). Attribution by cell (`goldbot/research/attribution.py:221`) |
| 4.16 | **Avoid revenge trading and overtrading → throttles** | Humans: CBOT traders take more afternoon risk after morning losses (Coval & Shumway 2005, *JF* 60(1), https://ideas.repec.org/a/bla/jfinan/v60y2005i1p1-34.html). The heaviest-trading households earned 11.4% vs the market's 17.9% (Barber & Odean 2000, *JF*; the gross-return reading is contested, https://faculty.haas.berkeley.edu/odean/papers/returns/returns.html). Disposition effect: winners sold, losers held (Odean 1998, *JF* 53(5), https://faculty.haas.berkeley.edu/odean/papers/disposition/disposition.html). The system analogue is a malfunctioning or decayed model that fires repeatedly into a regime it does not understand | R (humans) | Risk | **part.** Daily cap, 2-position cap, drift and CUSUM halts, and "exits never gated" (which prevents the disposition effect). **Missing: a per-agent loss-streak cooldown and a cap on entries per agent per session.** Today 4 straight full losses (2%) are needed before the daily cap stops an agent |
| 4.17 | **Trade journaling and review** | Every pro reviews every trade (P). MAE/MFE analysis tunes stops and targets (Sweeney, practitioner *(unverified)*) | P | Measurement | **impl:** decisions log, closed-trade records (`goldbot/ops/gates_phase.py:75-97`), daily attribution, weekly journal-coach role (`goldbot/agents/roles.py:67`), approve/reject reason codes (DESIGN). **miss:** MAE/MFE per trade (not in `ClosedTrade` or the shadow book; grep finds none) |

## 5. Process

| # | Practice | goldbot status |
|---|---|---|
| 5.1 | **Pre-session preparation** (calendar, overnight moves, levels, bias) | **impl.** `agents_presession` at 06:30 UTC (`config/settings.yaml` scheduler), macro and news analyst role (`goldbot/agents/roles.py:56`). Reporting only, no orders |
| 5.2 | **Economic calendar** | **impl:** Forex Factory archived daily (`goldbot/data/econ_calendar.py`, job `calendar_archive`). **part:** forecast and previous are stored (`econ_calendar.py:30`), but there is no release-time *actual*, so no surprise measure (H-11 blocked), and tiers 2 and 3 have no use |
| 5.3 | **Regime classification** | **impl:** rule allocator (`goldbot/allocator/rules.py`), volatility terciles (`gap_watch`), drift. **part:** no liquidation-state flag (1.9), no macro regime input live yet (BACKLOG item 4 acceptance) |
| 5.4 | **Post-trade review** | **impl:** attribution (gross/net R, t, by family, session, regime and cost), journal coach, auto-mode veto test (DESIGN). **miss:** MAE/MFE |
| 5.5 | **Metrics a pro tracks**: net expectancy (R), hit rate, payoff, profit factor, Sharpe, max DD and duration, MAR/Calmar, cost share of gross, slippage vs model, trades per week, worst day/week, exposure time | **impl:** expectancy, Sharpe, max DD, profit factor, DSR (`goldbot/research/metrics.py:9-44`), trades per week (`goldbot/engine/shadow.py:179-183`), slippage vs model (`gates_phase.py:497`). **miss:** drawdown duration, Calmar, worst day/week vs caps, time in market |
| 5.6 | **Honest evaluation** (pre-registration, deflated Sharpe, holdout) | **impl** (SoA A8; `preregistration-2027Q1.md`; holdout `config/settings.yaml:117-118`) |

---

## 6. Ranked gap list

Ranking = expected effect on capital protection or net R, divided by effort. The **A** items need no trial budget.
**B** items add data or features at no trial cost: they enter models only through H-02 or a later pre-registered trial.
**C** items are hypotheses: they must go through the trial registry. **No trial was run, no budget was used, and
the 13 reserved Q1 2027 trials and the holdout were not touched.** "Owner" marks items that need an owner decision.

### A. Risk-management gaps (no trial budget)

| Rank | ID | Gap | Fix (acceptance sketch) | Size | Owner? |
|---|---|---|---|---|---|
| 1 | G-1 | **Live EV ignores swap.** `gate.py:222` and `runner.py:1688` charge the round trip only. A long expected to hold N nights pays swap × N, with Wednesday counting 3 | Add expected swap in ATR (side, expected nights from `max_bars`, triple day) to `cost_atr` for the EV check. Test: a 4h long whose EV is positive before swap and negative after is refused; a short is unchanged. Trading-safety review | S | no |
| 2 | G-2 | **No holiday or thin-liquidity rule.** No holiday calendar; entries allowed in the first minutes after the daily reopen and the Monday open (2.4–2.6) | Holiday calendar (US, UK, JP, CN; 24 Dec–2 Jan; early closes) blocks entries. No entries for the first 15 min after each daily reopen and 30 min after the weekly open. Tests on a fake clock | S–M | thresholds |
| 3 | G-3 | **Tier-2 events unprotected** (PPI, retail sales, ISM, GDP, JOLTS, jobless claims, FOMC minutes, Fed chair speeches) | Tier-2 blackout −5/+15 min (config). `min_to_next_tier2` feature. Test per tier | S | yes: fewer trades |
| 4 | G-4 | **No cap on total open risk ("heat") or same-direction stacking** | Sum of open R per account and combined ≤ 1.0% (proposed). Two same-side positions by different agents count as one bet. Gate test | S | value |
| 5 | G-5 | **No drawdown/ruin expectation** | Monthly job: bootstrap shadow or backtest R into 10,000 paths at the live risk settings. Report P(8%), P(12%), P(weekly cap) within 6 and 12 months, and the expected time to the 12% switch. Reporting only | S | no |
| 6 | G-6 | **No loss-streak throttle** (the system analogue of revenge trading and overtrading) | Per agent: after 3 consecutive full stop-outs in a risk day, or more than N entries in a session, no entries until the next session. Logged, alerted, never touches exits | S | thresholds |
| 7 | G-7 | **Min-lot feasibility for slow horizons** (4.4) | A `run.py` sizing-feasibility report per agent: minimum equity for the minimum lot at the current ATR and risk rate. Phase-gate evidence | S | **yes: account size or tiny-live risk for H-01** |
| 8 | G-8 | **Weekend winners carry gap risk** (2.5) | Options: (a) keep the design (tighten to half profit); (b) flat every intraday-family position at the Friday cut, keeping only multi-day families such as H-01 with a tightened stop. Recommend (b) | S | **yes: design change** |
| 9 | G-9 | **No MAE/MFE** in closed-trade and shadow records | Record maximum adverse and favourable excursion in R per trade (shadow and live). Attribution shows them by exit type | S | no |
| 10 | G-10 | **Size not tied to proven edge** | Ceiling on `risk_frac × m` = 0.25 × Kelly computed from the lower 90% bound of the agent's shadow expectancy (zero while unproven). Diagnostic first, enforcement later | M | **yes: sizing policy** |
| 11 | G-11 | Fixed 45-point spread cap | Also refuse when the spread is above 2× the session's measured p90 (from the cost table) | S | no |

### B. Data and feature gaps (no trial cost; screened in H-02)

| Rank | ID | Gap | Note |
|---|---|---|---|
| 1 | D-1 | **DST-aware sessions.** Session windows, zones, cost-table sessions and `SESSION_OPENS_UTC` are fixed UTC (`calendar.py:28-33`, `session.py:13`) | London and NY opens move by 1 h twice a year, so "London open" features are an hour off for part of the year. Fix before H-04 and H-14 run (data-engineer, quant review) |
| 2 | D-2 | **Deterministic calendars:** holidays, COMEX option expiry and first notice, Lunar New Year, Dhanteras/Diwali, Akshaya Tritiya, month and quarter end | Free and PIT-safe (survey #79–82). Also feeds G-2 |
| 3 | D-3 | **COT and GLD into `macro-v1`** (loaders exist, `macro.py:212`, `:237`) | Needed for H-09 and H-12 and the 1.6/1.7 context. COT available Friday 15:30 ET |
| 4 | D-4 | **Tier-2 event proximity and a release-time `actual` archive** | Enables a surprise measure (H-11, C-3). The source choice (free scrape vs paid) is an owner decision |
| 5 | D-5 | **$100 round-number step; LBMA 10:30/15:00 London auction and 08:30 ET windows** | Aggarwal & Lucey 2007 single out $100. Needed by H-05, H-08 and H-14 |
| 6 | D-6 | **Daily GPR index** (Caldara–Iacoviello) | Free. Vintages are recomputed, so store the vintage (survey #66) |
| 7 | D-7 | **Intermarket D+:** XAGUSD, EURUSD/USDJPY/USDCNH, Brent, US500, intraday | Owner decision on source (survey #61–64). BTC not recommended |
| 8 | D-8 | **Liquidation-state flag:** gold and S&P both down more than k σ on the day with a VIX jump | Derived from existing series. Needed to separate C-1 from H-07 |

### C. Candidate hypotheses (registry only; none run)

| ID | Hypothesis | Evidence | Route and cost |
|---|---|---|---|
| C-1 | **Liquidation rebound:** after a day when gold falls *with* equities and the VIX jumps, gold is long for 5–15 days | 2008 and 2020 episodes (1.9). March 2026 is **in holdout: not usable** | Feature D-8 in H-02 first. A daily rule will have fewer than 1,000 events (owner ruling on the daily floor). 0 trials until it survives |
| C-2 | **$100 barrier:** reversal at the first touch, continuation on a close beyond | Aggarwal & Lucey 2007 (S) | Fold into H-05 as a $100 step. No new trial |
| C-3 | **Dovish-FOMC continuation** for 15m–1h after the statement | Awartani et al. 2024 (S). Sign unstable across studies | Fold into H-11. Blocked on D-4 |
| C-4 | **Seasonal demand windows** as conditioning (Lunar New Year, Indian festivals) | Weak (1.10) | Features D-2 in H-02. 0 trials |
| C-5 | **Exit variants** (breakeven, wider trail) for an H-01 survivor only | Kaminski & Lo 2014: stops help only with momentum | Only after H-01 passes. 1 reserve trial at most |
| C-6 | **Auction and 08:30 ET window microstructure** scalps | P only | Into the H-14 15m discovery screen. 0 extra |

**Known-failed ideas, not to re-test** (unchanged): R-01 … R-09 in `hypotheses.md`. BTC as a gold signal is added
here as "do not add". Its relation is unstable, and we found no mechanism.

---

## 7. Needs the owner

1. **Account size or tiny-live risk for slow horizons (G-7).** At 0.1% risk, H-01's minimum lot needs roughly
   $62k–142k of equity (4.4). Options: larger demo/tiny-live equity, a higher tiny-live risk rate for daily-stop
   families, or accept that H-01 stays in shadow. Recommendation: decide before H-01's result is known, so the
   result cannot bias the choice.
2. **Weekend rule (G-8).** Recommendation: intraday families go flat at the Friday cut. Only multi-day families
   hold, with a tightened stop.
3. **Tier-2 blackout (G-3)** and the **holiday/reopen windows (G-2)**. Both cut trade count. Recommendation: adopt.
4. **Throttle thresholds (G-6)**, **heat cap value (G-4)** and **edge-linked sizing (G-10)**. These are
   risk-policy values.
5. **Data sources:** a release-time actuals/consensus feed (D-4); D+ intermarket data (D-7).
6. Pending from earlier reviews: the P4 event floor for daily families (C-1, H-07); other instruments.

## 8. Not verified / left out

- The IC Markets spec page returned 404, so contract terms, stop-out level and the Seychelles entity's leverage and
  close-out rules are unverified. They are read from the terminal at runtime.
- Several sources are secondary summaries (the March 2026 WGC commentary, the March 2020 Refinitiv analysis, the
  Asia demand reports). Venues for Awartani et al. 2024 and the MDI jump paper are unconfirmed.
- ATR(1d) ranges in 4.4 are estimates, not measured from `data-v1`. G-7 measures them.
- No gold-specific study of options-expiry pinning, breakeven stops or session VWAP was found. They are graded F/P.
- No code was changed and no data was read. The multiple-testing ledger is unchanged: 20 trials in Q4 2026, none
  added.

## References (fetched for this review unless marked)

- Aggarwal, R. & Lucey, B. (2007). Psychological barriers in gold prices? *Review of Financial Economics* 16(2), 217–230. https://ideas.repec.org/a/eee/revfin/v16y2007i2p217-230.html
- Awartani, B., Hussain, S., Virk, N. (2024). Gold intraday returns and volatility and monetary policy surprises. https://pure.kfupm.edu.sa/en/publications/how-do-the-gold-intra-day-returns-and-volatility-react-to-monetar/ *(venue unverified)*
- Barber, B. & Odean, T. (2000). Trading is hazardous to your wealth. *JF* 55(2). https://faculty.haas.berkeley.edu/odean/papers/returns/returns.html
- CME Group. COMEX Rulebook ch. 115 (gold options). https://cmegroup.com/content/dam/cmegroup/rulebook/COMEX/1a/115.pdf ; gold futures and options fact card. https://www.cmegroup.com/content/dam/cmegroup/market-regulation/files/gold-futures-and-options-fact-card.pdf
- Coval, J. & Shumway, T. (2005). Do behavioral biases affect prices? *JF* 60(1), 1–34. https://ideas.repec.org/a/bla/jfinan/v60y2005i1p1-34.html
- ESMA (2018). Product intervention on CFDs. https://www.esma.europa.eu/node/84933
- Grossman, S. & Zhou, Z. (1993). Optimal investment strategies for controlling drawdowns. *Mathematical Finance* 3(3), 241–276. https://ideas.repec.org/a/bla/mathfi/v3y1993i3p241-276.html
- ICE Benchmark Administration. LBMA Gold Price. https://www.ice.com/iba/lbma-gold-silver-price
- Kaminski, K. & Lo, A. (2014). When do stop-loss rules stop losses? *J. Financial Markets* 18, 234–254. https://dspace.mit.edu/handle/1721.1/114876
- Kitco (2022). August 2021 flash crash. https://www.kitco.com/opinion/2022-07-21/gold-bounces-1678-low-august-2021-flash-crash ; Kitco (2026). Central banks 2025. https://www.kitco.com/news/article/2026-02-04/central-banks-buy-19t-gold-december-total-328t-2025-averaging-27tm-world
- MacLean, L., Thorp, E., Ziemba, W. (2010). Good and bad properties of the Kelly criterion. *Quantitative Finance*. https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf
- Magdon-Ismail, M. et al. (2004). On the maximum drawdown of a Brownian motion. *J. Applied Probability* 41(1) *(unverified)*
- Mining Weekly / Refinitiv (2020). Gold's March 2020 fall. https://www.miningweekly.com/article/recent-gold-plummet-the-result-of-multiple-factors-says-refinitiv-2020-04-21
- Odean, T. (1998). Are investors reluctant to realize their losses? *JF* 53(5). https://faculty.haas.berkeley.edu/odean/papers/disposition/disposition.html
- World Gold Council. Gold Demand Trends FY2025 (press release, supply). https://www.gold.org/download/file/20498/FY_2025_GDT_Press_Release.pdf ; https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-full-year-2025/supply ; trading volumes https://www.gold.org/goldhub/data/gold-trading-volumes
- WisdomTree (2026). The month gold broke (in holdout). https://www.wisdomtree.com/investments/blog/2026/04/17/the-month-gold-broke-five-lessons-from-the-march-madness-selloff-and-the-rebound-opportunity
- XTB (2021). Chart of the day: gold, 9 Aug 2021. https://xtb.com/en/market-analysis/chart-of-the-day-gold-09-08-2021
- Everything else is cited through `state-of-the-art.md` and `indicator-survey.md`.
