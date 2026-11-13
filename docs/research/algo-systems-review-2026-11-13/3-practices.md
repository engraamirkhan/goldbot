# Algo survey slice 3 of 3: open-source/platform systems and the practices that separate profitable systematic traders

Staff quant strategist, 2026-11-13. Read-only literature survey plus a check against goldbot's own research record
(`docs/research/hypotheses.md`, `profitability-2026-11-12.md`, `hb-exits-screen-2026-11.md`,
`opportunity-days-screen-2026-11.md`, `meta-label-screen-2026-11.md`). No trials run, K unchanged.
Citation honesty: most sources below were read as abstracts or secondary summaries; items marked *(secondary)* were not
checked against the primary text; *(unverified)* means a vendor or self-reported figure.

---

## 0. Bottom line

1. The owner's premise ("existing algo systems prove much better is possible") is **not** what the evidence on
   platform/community systems shows. The largest study of platform strategies (Quantopian, 888 algos) found in-sample
   Sharpe explains 1-2% of out-of-sample Sharpe, and the more a developer backtested, the bigger the gap. The
   practitioners who make money (Carver, AQR-style CTAs, Davey) do it with **small, slow, diversified, cost-budgeted
   edges** (realistic Sharpe per sub-system ≤ 0.4, portfolio ≤ 1.0), not with a single high-Sharpe intraday rule.
2. goldbot's problem, in practitioner terms, is not the threshold. It is **turnover against cost**: every gross edge
   found (tsmom 1h/4h +0.06 R t 2.6; Asia drift +0.043 R t 3.1; meta-label ranking +0.10-0.15 R gross) is roughly equal
   to or smaller than the round trip, and the live demo arms run on 1m/5m, where the random controls lose 0.06-0.19 R per
   trade, which is the cost itself. Carver's "speed limit" (cost ≤ 1/3 of gross) would have screened out almost every arm
   now trading.
3. Evaluation: **too strict on power per arm, too loose on hypothesis supply.** A t 3.6 read at 300 fills can only
   see edges of about +0.28 R/trade; real systematic edges are 0.03-0.10 R/trade. At the same time, 174 live arms and
   1,704 declared search specs are exactly the "many backtests" failure mode Quantopian measured. The fix is fewer,
   slower, pooled hypotheses, read with power-aware sequential tests, not a lower bar.

---

## 1. Platform and community systems: what the evidence says

### 1.1 Quantopian, "All that glitters is not gold" (Wiecki, Campbell, Lent, Stauth 2016, SSRN 2745220)
- 888 US-equity algorithms (from 7,152), IS 2010 to deployment Jan-Jun 2015, OOS to Feb 2016 (6-12 months).
- IS Sharpe explains R² 0.01-0.02 of OOS Sharpe; IS annual return correlated slightly **negatively** with OOS.
- More backtesting (total backtest days per author) → larger IS-OOS gap: direct evidence that search volume is the
  overfitting driver.
- Volatility (R² 0.67) and max drawdown (R² 0.34) persist; returns do not. A non-linear model on backtest features
  reached R² 0.17 OOS; its top-10 portfolio had OOS Sharpe 1.8 vs 0.7 for the top-10 by IS Sharpe (short, single-regime
  OOS; author himself said unclear if it matters in reality).
- Sources: https://www.cxoadvisory.com/big-ideas/in-sample-vs-out-of-sample-performance-of-888-trading-strategies ,
  https://quantopian-archive.netlify.app/forum/threads/q-paper-all-that-glitters-is-not-gold-comparing-backtest-and-out-of-sample-performance-on-a-large-cohort-of-trading-algorithms ,
  https://hedgefundalpha.com/strategies/comparing-backtest-sample-performance-large-cohort-trading-algorithms/
- **Lesson for goldbot:** risk is forecastable, return is not; rank arms on shrunk/deflated return and use risk
  metrics for sizing. Quantopian closed in 2020: its crowd-sourced alpha did not pay
  (https://discussion.fool.com/t/why-quantopian-shut-down/88667, forum, low weight).

### 1.2 QuantConnect
- No published live-vs-backtest decay study found. QC runs an out-of-sample "reconciliation" backtest beside every
  live deployment and attributes gaps to data, fill modelling and parameters
  (https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/reconciliation). goldbot's demo-vs-labeller parity
  check is the same practice.
- The best-replicated intraday public result on QC is Zarattini, Barbon, Aziz (2024, SSRN 4729284): 5-min ORB on US
  "Stocks in Play" (top 20 by opening relative volume), 2016-2023, net Sharpe 2.81; a QC re-creation got 2.40
  (https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/ ,
  https://concretumgroup.com/a-profitable-day-trading-strategy-for-the-u-s-equity-market/). The edge is **cross-sectional
  selection of which instrument is in play today** among 7,000 stocks. A single instrument (XAUUSD) cannot reproduce
  that; goldbot's single-instrument analogue (H-17, R-17) failed (gross -0.003 R).

### 1.3 Freqtrade / Jesse / Backtrader community strategies
- Freqtrade ships `lookahead-analysis` and `recursive-analysis` because community strategies so often leak future
  data or depend on indicator warm-up; the docs admit false negatives
  (https://codeclimate.com/github/freqtrade/freqtrade/docs/lookahead-analysis.md/source ,
  https://codeclimate.com/github/freqtrade/freqtrade/docs/recursive-analysis.md/source ,
  https://brookmiles.github.io/freqtrade-stuff/2021/04/12/backtesting-traps).
- Candle-only fills (no intrabar path) and default fees are the other two documented traps. None of these repos
  publishes audited live P&L; published hyperopt results are in-sample by construction.
- **Lesson:** goldbot already exceeds these platforms on leakage control (asof_join, lookahead tests, 1m walk parity).

### 1.4 TradingView XAUUSD strategy scripts: why they fail live
- Repainting via `request.security()` on higher timeframes and `lookahead_on` without `[1]`; signals computed on the
  unclosed bar (TradingView's own docs: https://www.tradingview.com/pine-script-docs/v4/essential/indicator-repainting ,
  https://tradingview.com/support/solutions/43000478429-script-or-strategy-gives-different-results-after-refreshing-the-page-repainting).
- Default fills at the signal bar close, OHLC-only intrabar ordering (Bar Magnifier off), zero or flat commission and
  fixed spread (https://blog.traderspost.io/article/pine-script-backtesting-limitations-and-validation ,
  https://blog.crosstrade.io/pine-script-repainting/).
- Strategy-tester ranking on the "top scripts" pages selects for max net profit over one chart's history, i.e. one
  maximum of a huge search with K unknown; there is no OOS. Gold scripts at 1m-15m with a 1-pip assumed cost meet
  goldbot's measured 0.15-0.3 R costs and die. goldbot's own TV-author read (K +6) found INSUFFICIENT/drift.
- **Lesson:** "existing systems prove better is possible" usually means "existing backtests". No public XAUUSD script
  with audited multi-year live results was found.

---

## 2. Practitioner literature

| Source | Claim | Sample / instrument | Net of costs? | Robust? |
|---|---|---|---|---|
| Carver, *Systematic Trading* (2015), via https://www.cxoadvisory.com/big-ideas/a-few-notes-on-systematic-trading , https://the7circles.uk/?p=24243 *(secondary)* | Realistic SR per instrument sub-system ≤ 0.4, portfolio max ~1.0; backtest SR 2-3 is overfitting. **Speed limit: annual cost ≤ 1/3 of conservative pre-cost SR**. Diversify across instruments more than rules; combine capped forecasts with a diversification multiplier; vol-target positions | Futures, decades | Yes (cost-budgeted) | Practitioner consensus |
| Carver blog (https://qoppac.blogspot.com/2025/03) *(secondary)* | Applies the speed limit to a 2-day forecast: SR ~0.22 after time-scaling, so cost budget ~0.07 SR | Futures | Yes | Single author |
| Davey, *Building Winning Algorithmic Trading Systems* (Wiley 2014) *(secondary)* | Idea → limited test → walk-forward → Monte Carlo on trade sequence → incubation 6-12 months on paper before money | Futures | Yes | Practitioner |
| Chan, CPO blog 2021 (https://epchan.blogspot.com/2021/04/conditional-parameter-optimization.html) *(unverified, vendor)* | Regime-conditional parameters on GLD Bollinger MR: SR 2.33 vs 1.95 | GLD | Unclear | Single vendor result |
| Chan, general | Retrain when new data > 10% of training set; treat window as hyperparameter | — | — | Opinion |
| López de Prado, AFML (2018); Joubert, JFDS 4(3) 2022; Meyer, Barziy, Joubert, JFDS 5(2) 2023 | Triple barrier, meta-labelling (secondary model sizes/filters a primary), purged/embargoed CV, CPCV | Various | Rarely | **No independent controlled Sharpe evidence found** (https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy/ is vendor; https://www.quantconnect.com/forum/discussion/14706/why-meta-labeling-is-not-a-silver-bullet/) |
| Bailey, Borwein, López de Prado, Zhu, J. Comp. Fin. 2017 (https://papers.ssrn.com/abstract=2326253) | PBO via CSCV; minimum backtest length grows with number of trials | Synthetic + examples | n/a | Robust method |
| Bailey & López de Prado, JPM 2014 (https://papers.ssrn.com/abstract=2460551); López de Prado & Lewis, QF 2019 | Deflated Sharpe; effective N via clustering of trial return correlations | n/a | n/a | Robust method |
| Harvey & Liu "Backtesting" JPM 2015 (https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF); HLZ RFS 2016 | Haircut Sharpe: Bonferroni/Holm/BHY; t ≈ 3.0 for new factors given ~315 tested | US equity factors | n/a | Robust, but contested |
| Harvey & Liu, "False (and Missed) Discoveries", JF 2020 | Choose hurdle by trading Type I against Type II; power matters | Factors, fund managers | n/a | Robust method |
| Chen & Zimmermann (FEDS 2018, https://www.federalreserve.gov/econres/feds/files/2018033pap.pdf); Chen 2022 (https://arxiv.org/pdf/2204.10275v3) | With publication-bias modelling, t ≈ 1.8 controls FDR in published anomalies; hurdles > 3 not identified by the data | 156 / ~200 equity predictors | n/a | Counterweight to HLZ |
| McLean & Pontiff, JF 2016 (https://Www.Gwern.net/doc/economics/2016-mclean.pdf) | Predictor returns 26% lower OOS, 58% lower post-publication | 97 US equity predictors | Gross | Robust |

### 2.1 Exits
- Kaminski & Lo (J. Fin. Markets 2014, https://dspace.mit.edu/bitstream/handle/1721.1/114876/Lo_When%20Do%20Stop-Loss.pdf):
  under a random walk stops always reduce expected return; they add value only with positive serial correlation
  (momentum) or regime switching. Monthly 1950-2004 equities and daily futures; gross.
- Trailing vs fixed: no peer-reviewed head-to-head for intraday FX/metals. Theory (arXiv 1701.03960) and the one equity
  study (LeCompte, via https://www.cxoadvisory.com/technical-trading/using-trailing-stop-losses-to-reduce-risk) say
  trailing mainly cuts variance, not raises mean. Practitioner blogs disagree with each other
  (https://primefoldtools.beehiiv.com/p/the-exit-rule-that-survived , single backtest).
- MAE/MFE (Sweeney 1990s; https://nexusfi.com/wiki/trading-wiki/Maximum-Adverse-Excursion): diagnostic, not an edge.
  Losers with ~zero MAE are an entry problem; large MFE-vs-realised gap is an exit problem.
- **goldbot already measured this:** H-82 (`hb-exits-screen-2026-11.md`, 337,341 history entries, 2010-2017): every
  target/time-exit variant moved net R by < 0.01 R vs control (CI ±0.001 R). R-17 runner exits failed too. Consistent
  with Kaminski-Lo: with no serial correlation in the entries, exit geometry cannot create edge. **Do not re-test exits
  on entries without gross edge.**

### 2.2 Position sizing
- Volatility targeting: Harvey, Hoyle, Korgaonkar, Rattray, Sargaison, Van Hemert (JPM 2018), 60+ assets, daily data
  from 1926: raises Sharpe for equities/credit (leverage effect), **negligible Sharpe effect for commodities and FX**,
  but cuts tails and vol-of-vol everywhere (https://alphaarchitect.com/volatility-targeting-improves-risk-adjusted-returns/ ,
  https://quantpedia.com/the-impact-of-volatility-targeting-on-equities-bonds-commodities-and-currencies). Moreira &
  Muir's volatility-managed alphas do not survive OOS in 103 equity strategies (Cederburg et al., JFE 2020,
  https://econpapers.repec.org/RePEc:eee:jfinec:v:138:y:2020:i:1:p:95-117). goldbot already retired vol targeting as
  alpha (R-08); keep it as **risk normalisation** only.
- Kelly: MacLean, Thorp, Ziemba (QF 2010; https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf): full
  Kelly on an estimated edge over-bets; fractional Kelly ≈ Kelly on a shrunk estimate (arXiv 2503.17927). If the true
  edge is half the estimate, full Kelly has negative growth *(practitioner derivation)*. At a break-even edge, Kelly
  says size zero: **sizing amplifies an edge, it cannot create one.**
- Forecast-proportional sizing (Carver; Meyer-Barziy-Joubert 2023 "calibration and position sizing"): scale size by
  calibrated forecast strength instead of binary filter. Useful only once some forecast bucket is net-positive.

### 2.3 Regime filters
- Trend/vol regime for gold intraday: no peer-reviewed intraday XAUUSD regime-filter paper found. A self-published
  test of Kakushadze's Alpha101 on intraday gold found almost no directional information
  (https://rahulsp-research.pages.dev/papers/alpha101-intraday-gold , *(unverified)*).
- The opportunity-day idea works in equities because of cross-sectional selection (Zarattini et al.). In goldbot's own
  test the HAR range forecast is real (rho 0.29, +0.20 over baseline) but does not pick direction (H-16/H-17).
  **A range forecast is useful for cost-in-R, not for direction** (see practice 4 below).

### 2.4 Portfolio of weak edges vs one big edge
- Fundamental law (Grinold): IR ≈ IC × √breadth. Carver and AQR (Hurst, Ooi, Pedersen JPM 2017, 67 markets since
  1880, https://fairmodel.econ.yale.edu/ec439/hurst.pdf; Moskowitz, Ooi, Pedersen JFE 2012, 58 futures,
  https://papers.ssrn.com/abstract=2089463) earn their Sharpe from diversification across many instruments of a
  modest per-instrument edge; MOP report positive 12-month TSMOM in every one of 58 contracts, but a portfolio SR
  ~1.0-1.3 *(secondary)* that no single contract reaches.
- The diversification benefit is real only if the edges are weakly correlated; trend rules on one instrument are
  highly correlated with each other (Carver, https://www.toptradersunplugged.com/captivate-podcast/ttu90-why-diversification-is-key-ft-robert-carver-author-trader-2of2/).
  goldbot's 174 arms on mostly one instrument and overlapping intraday mechanics are probably far fewer independent bets
  than 174 (which also means K_eff < 174).

---

## 3. Is goldbot's evaluation too strict or too loose?

### 3.1 Power arithmetic (assume sd of per-trade R ≈ 1.1 for 1:1.5-2 brackets; check with own data)
Required n for 80% power, one-sided: n = ((t* + 0.84) × sd / δ)².

| true net edge δ (R/trade) | n at t* 3.6 | n at t* 3.0 (BHY-ish) | n at t* 2.0 |
|---|---:|---:|---:|
| 0.05 | ~9,500 | ~7,100 | ~3,900 |
| 0.10 | ~2,400 | ~1,800 | ~980 |
| 0.20 | ~600 | ~450 | ~240 |
| 0.30 | ~265 | ~200 | ~110 |

At 300 fills and t 3.6 the smallest edge detected with 80% power is ~0.28 R/trade. The power for a real 0.10 R edge at
n 300 is about 2%. Real systematic edges (Carver SR ≤ 0.4/sub-system; goldbot's own gross finds 0.04-0.15 R) sit at
0.03-0.10 R. **So a 300-fill t 3.6 read rejects almost every real but small edge.** That is by design a false-
discovery guard, but it makes 300-fill reads close to uninformative for the edge sizes that exist.

### 3.2 The bar itself
- Bonferroni over K = 174 (t ~3.6) is conservative: arms share mechanics, sessions and instrument, so K_eff (López de
  Prado & Lewis clustering) is likely well below 174, and BHY (Harvey-Liu's preferred method) at this K gives about
  t 3.0-3.2. Chen & Zimmermann argue t ~1.8-2.0 is enough once publication/selection bias is modelled for *ex ante,
  theory-motivated* predictors. Practitioners like Carver do not run per-rule significance at all: they accept rules
  with an ex-ante economic story and cap their weight, relying on diversification and shrinkage.
- But the bar is not the binding constraint. The binding constraint is net expectancy: arms whose gross edge ≈ cost
  are break-even regardless of what threshold is used. Lowering t would only admit more break-even arms.

### 3.3 Where it is too loose
- 174 live arms + 1,704 declared discovery specs + many post-hoc cuts is the Quantopian "more backtests, bigger gap"
  pattern. goldbot counts K honestly, which protects the verdicts, but the search volume itself burns the statistical
  budget on short-horizon ideas with no cost headroom.
- Post-hoc cuts (London +0.132 R t 2.9; shorts) are correctly labelled hypotheses; keep it that way.

### 3.4 Observation worth a pre-registered test (not evidence)
Signal-vs-random-control excess per account: t 2.39, 1.96, 2.29 (`profitability-2026-11-12.md`). Combined as
independent accounts ≈ t 3.8 for the single hypothesis "goldbot's signal arms beat matched random entries". Caveats:
post-hoc pooling, overlapping arms across accounts, side drift (controls long-heavy in a falling market). If it
replicates under a pre-registered, side-matched read, it says goldbot's entries have selection skill of ~+0.1 R, about
equal to cost, i.e. **the edge exists and the problem is cost**, which points to practices 1-3 below.

### Verdict
Too strict on per-arm power (300-fill t 3.6 can only see ≥ 0.28 R edges), too loose on hypothesis volume and
turnover. Do not lower the bar. Change the design: fewer, slower, pooled hypotheses, K_eff by clustering, BHY-style
FDR instead of Bonferroni across correlated arms, and sequential reads (alpha-spending) that let a promising arm run to
1,500-3,000 fills instead of being retired or graduated at 300.

---

## 4. Five practices most likely to move goldbot from break-even to profitable

1. **Carver's cost budget ("speed limit") as a hard design gate.** Evidence: Carver 2015; goldbot controls lose
   0.06-0.19 R (= cost) on 1m/5m; every known gross edge ≈ cost. Test: a 0-trial audit computing, for every live arm,
   measured round trip ÷ expected gross R; retire arms with cost > 1/3 gross; re-express the surviving gross edges
   (tsmom 1h/4h slow, Asia drift, meta-rank top decile) at stop widths where cost-in-R ≤ 1/3 gross; one pre-registered
   trial on 2018-25 confirm window, reading rule net R > 0 with LB > 0.
2. **One combined forecast instead of many separate arms (forecast combination, capped, equal pre-set weights), and
   across instruments.** Evidence: Carver forecast diversification multiplier; MOP 2012 / HOP 2017 portfolio SR from
   breadth; Grinold √breadth. Test: one trial; combine the gross-positive families into a single position per
   instrument (cost paid once, signals net against each other), weights fixed before data; compare net SR with the
   best single family; extend to the multi-instrument set for the TSMOM-slow leg.
3. **Execution-cost engineering as alpha.** Trade only when the live spread is in its low quantile, avoid rollover
   for intraday, prefer passive entries where adverse selection is measured as acceptable (RS-1 showed limit-fill
   adverse selection, so measure it, do not assume). Evidence: goldbot's own cost tables; Freqtrade/TV fill-model
   failures. Test: 0-trial cost-probe analysis by spread quantile and hour; then a matched A/B on demo (same signals,
   market vs gated entry), read on cost per trade, not P&L.
4. **Range-forecast gating of cost-in-R, not direction.** H-16 showed the HAR range forecast is real (rho 0.29) but
   not directional. Use it to trade only when expected range ÷ round trip is high (stops wider in £ terms relative to
   spread), and vol-target size so £ risk stays constant (Harvey et al. 2018: tails cut, no Sharpe claim). Test: on
   existing gross-positive entries, pre-registered split by ex-ante range ÷ spread tercile; reading rule top tercile
   net R > 0 and > bottom tercile.
5. **Power-aware sequential evaluation with pooled hypotheses (Harvey-Liu 2020 Type I/II trade-off; DSR with
   clustered K_eff).** Evidence: §3. Test: replace 300-fill reads with an alpha-spending design pre-registered per arm
   (looks at 300/1,000/2,000), FDR across clusters, and pre-register the pooled signal-vs-control hypothesis (§3.4)
   side-matched on the next 30 days.

Known not to work in goldbot (do not re-test): exit geometry on edge-less entries (H-82, R-17), meta-label as filter
(R-07, R-20), vol targeting as alpha (R-08), SMC/retail setups as rules (R-09, R-RS), opportunity score as direction
(H-16/17), search-grammar generation over bar/macro inputs (R-18).

Owner-only items flagged: adding instruments (practice 2), any change to gate thresholds (practice 5), budget.
