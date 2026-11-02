# Rethink lane ACADEMIC: what the literature offers gold, and why our pipeline could not have found it

Principal strategy researcher, 2026-11-02. Candidates: `config/research/candidates/academic.yaml` (172 items,
`RT-ACADEMIC-001..172`). Written from sources and from EXPLORE-window (2010-2017) numbers already published by earlier
lanes. **No price, outcome or holdout data was read. K added: 0.** Holdout (>= 2025-10-01) sealed.

## 1. Numbers first

| | count |
|---|---|
| Candidates, each a mechanical rule (entry, side, stop, exit, hold) with a citation | **172** |
| Evidence grade: peer-reviewed / practitioner-documented / folklore / own-data | 88 / 57 / 20 / 7 |
| Already tested here: yes / partially / not at all | 25 / 44 / 103 |
| Priority: A (single pre-registered trial) / B (inside one bulk family trial) / C (feature or low prior) / X (do not test) | 4 / 28 / 104 / 36 |
| Testable (not X) and never tested in any form | 100 |
| Map onto the sprint3000 vocabulary / need a new primitive | 31 / 141 |
| Primitive most needed: CP (continuous vol-targeted position) / XS (external daily series) / EVT / W1 / CAL | 68 / 50 / 19 / 12 / 10 |
| Data: local / free / paid or unverified | 133 / 30 / 9 |
| Horizon: daily or slower (1d, weekly, monthly) / intraday | 160 / 12 |
| Candidates with at least one citation marked *(unverified)* (cited from memory; a lead, not evidence) | 105 |

The X rows (36) stay in the file on purpose: they are the literature's ideas that this program already ran, retired,
or that the evidence contradicts. Listing them keeps the tested universe explicit and stops re-tests.

## 2. Diagnosis: why 3,330 trials produced 0 qualified strategies

Five causes, ranked by how much of the result each explains. The first three are design, not market.

**2.1 Horizon mismatch (largest).** Most of the program's trials were 15m-4h rules with 1-2 ATR stops and fixed R
targets. The literature predicts that this region is empty for gold net of retail costs:
- Short-horizon trend has not paid on small-tick futures since about 2009 (Kurth, Eisler, Rej, Bouchaud 2026, arXiv
  2607.01550, ~100 futures 1995-2025). Gold is small-tick.
- Large technical-rule searches show no out-of-sample edge once data snooping is counted: Sullivan, Timmermann, White
  1999 JF (DJIA); Marshall, Cahan, Cahan 2008 JBF 32(9) (7,846 rules, 15 commodity futures 1984-2005: profits vanish
  in 14 of 15).
- The one replicated trend premium is slow (holds of weeks to months), vol-scaled and diversified: Moskowitz, Ooi,
  Pedersen 2012 JFE; Hurst, Ooi, Pedersen 2017 JPM (1880-2016, net of estimated costs); Lemperiere et al. 2014
  (t about 5 since 1960); Babu et al. 2020 JIM (82 new markets out of sample).

Our own record agrees: the 1h-4h trend rules were gross-positive and cost-negative. This lane's 172 candidates are
93% daily or slower for that reason.

**2.2 The gates cannot detect a literature-sized effect at multi-day horizons.** For a strategy with daily P&L,
t = SR x sqrt(years). Under W2, a spec must clear explore t >= 2 on 8 years, confirm (BH-FDR, about t >= 1.28 when
m = 1) on 7.75 years, and TEST-OLD t >= 1.28 on 4 years. Normal approximation, ignoring the year-count legs (which
only lower these numbers):

| true net SR (annual) | P(explore) | P(confirm) | P(TEST-OLD) | P(qualify) | P(t >= 2) on one 2003-2025 read |
|---|---|---|---|---|---|
| 0.2 | 0.08 | 0.23 | 0.19 | 0.003 | 0.15 |
| 0.3 | 0.12 | 0.33 | 0.25 | 0.010 | 0.28 |
| 0.4 | 0.19 | 0.43 | 0.32 | 0.026 | 0.46 |
| 0.5 | 0.28 | 0.54 | 0.39 | 0.059 | 0.65 |
| 0.7 | 0.49 | 0.75 | 0.55 | 0.20 | 0.91 |

A single-asset trend or macro strategy with net SR 0.3-0.5 (the plausible range for gold, section 4) has a **1-6%**
chance to qualify under W2. A null spec qualifies with probability 0.023 x 0.10 x 0.10 = 0.0002 (about 0.7 false
qualifiers per 3,000 specs). The gates control false positives well and have almost no power for the strategies the
literature supports. Intraday rules get t from many trades, which steers the search to the horizons where costs win.
That is a structural cause of "0 of 3,330", not a fact about gold.

**2.3 The cost model overcharges multi-day longs in the explore window.** W2 charges the long swap as a flat 5.48% a
year of notional in every window (sprint3000 prereg section 2). A broker's long swap tracks the policy rate plus a
markup. The effective fed funds rate (FRED DFF, in macro-v1) was about 0.1-1.4% in 2010-2017, so the flat rate
overstates the explore-window long financing by roughly 2-4% a year (the markup is not measured for that period;
estimate, not a measurement). For a 30-night long held against a 2 x ATR20 stop, the flat swap costs about 0.17-0.19 R.
A date-varying swap would cost about 0.05 R. That gap is the size of the whole expected edge of a slow trend trade, and
it falls on exactly the strategies the literature supports. The decision memo of 2026-11-01 ("swap 0.13-0.15 R a
trade, above any plausible single-asset TSMOM gross") reads the 2026 rate back into 2010-2017.

Related correction: a futures or ETF venue is not swap-free in economic terms. A long futures position pays the
financing through the basis (F = S x e^((r - lease) T)). Against futures, the CFD costs roughly the broker markup on
longs (about 1-2% a year, an estimate) and the interest a short would earn (near 0 at zero rates, about the policy rate
now). That is a smaller gap than the memo implies for longs and a larger one for shorts.

**2.4 Trade form instead of exposure form.** The trend premium is earned by a vol-scaled position held while the
forecast persists. Our harness converts every idea into single trades with ATR stops and R targets. Stop-loss rules
lower expected return unless returns are autocorrelated (Kaminski, Lo 2014 J. Financial Markets 18). Fixed targets cut
the right tail, which is where trend's positive skew lives. The sprint3000 catalogue's 398 daily specs all cap holds at
20 bars with 1-2 ATR stops: too short for the trend premium, long enough to pay swap. H-01, the one literature-shaped
trial, read gross +0.166 R (t 2.04) on 294 events and net +0.002 R, inconclusive by power, not refuted.

**2.5 Multiple testing as a tax.** At K about 3,330 (and 6,330 after sprint3000), the deflated-Sharpe bar for any
later idea is high. Bulk grids of near-identical variants (1,700 discovery-v2 specs, 3,000 sprint3000 specs) spend
that budget on correlated tests. Pre-specified ensembles tested once, as practitioners build them (Carver 2015/2023),
spend one trial on the whole family.

**What would change this diagnosis:** a CP trend ensemble that reads net SR < 0.2 on 2003-2025 with a date-varying
swap. Then gold's slow trend is not there for a CFD trader either, and the honest conclusion is that this
instrument at this venue has no documented edge we can harvest.

## 3. Robust findings vs single studies (what the 172 rest on)

| Finding | Sources (sample, instrument, costs) | Grade |
|---|---|---|
| Slow (1-12 month) vol-scaled trend pays across futures, including gold, over decades | Moskowitz-Ooi-Pedersen 2012 JFE (58 futures 1965-2009, gross); Hurst-Ooi-Pedersen 2017 JPM (67 markets 1880-2016, net est.); Lemperiere et al. 2014 JIS (1800+, gross); Babu et al. 2020 JIM (new markets, gross) | R |
| Single-asset trend predictability is weak; profits resemble a historical-mean strategy | Huang, Li, Wang, Zhou 2020 JFE 135(3) (55 futures 1985-2015, gross) | R (credible challenge) |
| MA crossovers, TSMOM, Kalman/HP filters are one family of linear trend filters | Levine, Pedersen 2016 FAJ 72(3) (58 futures, gross) | R (theory + data) |
| Short-horizon trend gone on small-tick contracts since ~2009 | Kurth et al. 2026 arXiv 2607.01550 (~100 futures 1995-2025) | S (strong authors) |
| Technical rules on commodity futures fail after data-snooping control | Marshall, Cahan, Cahan 2008 JBF (15 commodities 1984-2005); contra Szakmary et al. 2010 JBF (28 commodities 1959-2007, abstract-level) | conflicting |
| Vol targeting adds nothing for commodities; real-time versions fail | Harvey et al. 2018 JPM *(unverified)*; Cederburg et al. 2020 JFE (103 equity strategies) | R |
| Gold is a short-lived safe haven (about 15 trading days) after equity shocks | Baur, Lucey 2010 FR; Baur, McDermott 2010 JBF *(both unverified)*; Baur, Smales 2020 JBF (threats, 1985-2018) | R |
| Uncertainty (EPU) Granger-causes gold returns in quantiles | Balcilar, Gupta, Pierdzioch 2016 RP; Raza et al. 2018 RP (in-sample, gross) | S |
| Commodity-currency FX forecasts commodity index prices out of sample | Chen, Rogoff, Rossi 2010 QJE (quarterly, indices; not gold-specific) | S for gold |
| Open-interest growth predicts commodity returns | Hong, Yogo 2012 JFE (aggregate commodities 1965-2008, gross) | S for gold |
| Negative skew earns a premium in commodities | Fernandez-Perez et al. 2018 JBF (cross-section, gross) | S for gold (time series untested) |
| Gold autumn effect (Sep, Nov) | Baur 2013 RIBAF (1980-2010, gross); reversed after publication: Potrykus, Augustynowicz 2024 IJME | S, decayed |
| Same-calendar-month seasonality, incl. commodities | Keloharju, Linnainmaa, Nyberg 2016 JF; 2021 JFE (offset by seasonal reversals) | R for stocks, S for gold |
| Equity premium earned in even FOMC-cycle weeks | Cieslak, Morse, Vissing-Jorgensen 2019 JF (US equities 1994-2016, gross) | S, untested on gold |
| Next-day reversal after abnormal gold days | Caporale, Plastun 2021 FMPM (gold 2009-2020); earlier 2014 study says counter-moves did not pay in commodities | S, conflicting |
| Overnight vs intraday clientele continuation | Lou, Polk, Skouras 2019 JFE (stocks) | S for gold |
| Miners lead gold | Contradicted: Lucey, O'Connor 2016 AEL (gold leads miners) | contradicted |
| SGE premium predicts London price | Meader 2016, LBMA Alchemist 83 (2005-2016, descriptive, weak) | S, weak |
| Lease rate / GOFO predicts gold | Practitioner anecdotes, 2013-14; critics 2016 | folklore |

## 4. Expected size vs our costs (before any data)

- **Slow trend, CP form, 10% vol target.** Gross SR for one futures market is typically 0.2-0.5. Haircut by half for
  post-publication decay (McLean, Pontiff 2016 JF) gives about 0.15-0.35. Costs: round trip at the W2 rate (0.575 bp)
  x about 8 turnovers a year is under 0.05% a year, which is negligible. Swap at average |notional| about 0.67 and
  long share about 0.6 is 2.2% a year flat (W2) or 0.4-1.0% a year date-varying, against 1.5-3.5% a year gross (SR
  0.15-0.35 at 10% vol). **Net SR: about -0.1 to +0.1 under the W2 flat swap, about 0.05-0.3 date-varying.**
- **Trade form (Turtle 55, 2 x ATR20 stop, ~30-night hold).** Round trip about 0.003 R. Swap on longs 0.17-0.19 R
  (flat) or about 0.05 R (date-varying). Shorts pay none. The gross per trade implied by H-01 is about +0.15 R.
- **Haven events (10-day long after a shock).** Round trip about 0.002 R. Swap about 0.06 R flat or 0.02 R
  date-varying against a 3 x ATR20 stop. The literature (Baur-Lucey) implies roughly +0.5-1% over 10 days, i.e. about
  +0.15-0.25 R gross.

So costs no longer decide these outcomes; swap modelling and statistical power do. That is the opposite of the 15m-4h
world, where round trip and overshoot (0.15-0.4 R) decided everything.

## 5. Primitives the candidates need (engine work, 0 trials)

| Primitive | Definition (frozen here) | Used by |
|---|---|---|
| CP | Daily forecast f in [-20, +20]; position = clip(f/10, -2, 2) x 10% / sigma_ann (EWMA span 35 days of close-to-close returns). Decide on the 21:00 UTC close, fill at the first 1m open after 23:00 UTC (after the rollover spread), 10% no-trade buffer. Daily P&L = position x return - abs(trade) x round-trip bps - swap by side per night. Report annual SR, HAC t (Newey-West, 20 lags), turnover, P&L by year, vs CP-HOLD and a 200-draw sign-randomised twin (block-shuffled forecast signs, 21-day blocks) | 68 |
| SWAP-T | Long swap(t) = DFF(t) + m, with m = (cor long swap as % a year) - DFF at the cor date; short swap as in the cor. Report the W2 flat version next to it. A cost-of-record change: research director and finance-auditor own it (ADR 0064 amendment) | all multi-day |
| XS | External daily series as-of joined on `available_utc`: FRED VIXCLS, DEXUSAL, DEXSFUS, DEXCHUS, DEXUSEU, DEXJPUS, DCOILWTICO; ALFRED vintages of CPIAUCSL, INDPRO, M2SL; daily GPR; daily US EPU | 50 |
| EVT | Threshold event on a daily series -> trade with a wide ATR(20,1d) stop and a fixed time exit; one position at a time; holds above 20 bars allowed | 19 |
| W1 / CAL | Weekly bars (Friday 21:00 UTC); calendar windows (month, turn of month, FOMC-cycle week, festival dates) | 22 |
| s3k-next | Exits `opposite_channel(n)`, long chandelier; `max_hold_bars` up to 250 on 1d; entries `ema_pullback`/`adx_onset` on 1d | 31 trade forms |

## 6. Ranked proposals and pre-registration drafts

Ranked by expected information per trial. The drafts freeze the configuration and the reading rule now, before any
of these strategies has been run. The engine and data primitives must exist before the run; they change no rule.

### P1. Slow trend ensemble in exposure form (RT-ACADEMIC-015, with control 047; diagnostics 013, 014, 034, 035)
- **Config.** CP primitive. f_015 = 1.15 x (0.6 x f_013 + 0.4 x f_014), cap +-20, where f_013 is EWMAC 16/64, 32/128
  and 64/256 equal-weighted with FDM 1.1, and f_014 is the 40/80/160/320-day breakout. Vol target 10%. SWAP-T is
  primary; W2 flat is reported. Daily bars are built from the Dukascopy 1m mid; the warm-up may read earlier bars.
- **Why it should work.** Section 3, first three rows. Gold is in every TSMOM panel. The mechanism (under-reaction,
  then hedging flows) has no reason to stop in gold. Huang et al. 2020 is the reason for a CP-HOLD control.
- **Expected edge vs costs.** Gross SR 0.15-0.35 after the decay haircut; net about 0.05-0.3 under SWAP-T (section 4).
  Trade-form equivalent: +0.15 R gross per trade vs 0.003 R round trip and 0.05-0.19 R swap on longs.
- **Sample.** About 5,700 daily P&L days on 2003-01..2025-09 (W2 windows minus the 2006-01 embargo); about 60-100
  forecast sign changes.
- **Reading rule (frozen).** Primary: net SR of 015 under SWAP-T on one read of 2003-01-01..2025-09-30.
  - CONTINUE (one 0.01-lot forward demo sleeve; holdout read once): net SR >= 0.42 (t_HAC >= 2), AND net SR >= CP-HOLD
    net SR, AND net SR > 0 in both halves (2003-2014 and 2015-2025-09), AND >= 50% of calendar years positive, AND net
    under W2 flat > 0, AND above the 95th percentile of the sign-randomised twins.
  - INCONCLUSIVE (0.20 <= net SR < 0.42): no variants. The trend state becomes a feature only. Re-test only with
    pre-2003 daily data, which is new evidence.
  - STOP (net SR < 0.20, or negative in either half): retire the slow-trend family on the XAUUSD CFD. That retires
    001-048 in exposure form and 049-060 as entries, and gets recorded in hypotheses B.
  - Diagnostics 013, 014, 034, 035 and 047 are reported and never promoted from this read.
- **Under W2 as it stands** (if the owner does not adopt the single-read class, below): explore 2010-2017, then
  confirm, then TEST-OLD per W2, on the CP P&L. Expected P(qualify) is about 1-3% (section 2.2), so the trial is
  worth running only for its estimate.
- **Trial cost.** K +1. P(CONTINUE) about 5-25% (the t >= 2 column of section 2.2 at net SR 0.05-0.3). The trial is
  worth its one K because either outcome settles the family: CONTINUE opens the only literature-backed line, STOP
  closes 60 candidates at once.

### P2. Haven and uncertainty events, pooled (102, 103, 105, 106; 068 reported separately)
- **Config.** Long gold at the first 23:00 UTC open after the trigger's `available_utc`; stop 3 x ATR20; time exit 10
  trading days (one common exit for pooling); one position at a time. Triggers:
  - E1: VIX close up >= 25% day-on-day, or VIX >= its 2-year 95th percentile and rising (VIX only, which avoids the
    10-year FRED SP500 limit);
  - E2: gold < -1.5 sd and VIX up > 20% on the same day (liquidation), with entry one day later;
  - E3: GPR Threats > its 250-day 95th percentile and > 1.5 x its 20-day mean;
  - E4: the 7-day mean of US EPU > its 2-year 95th percentile.
- **Why it should work.** Haven demand after shocks is the most replicated gold-specific finding (section 3). These
  are free, point-in-time data with a mechanism the program has never tested (H-07 and H-15 were proposed and never
  run).
- **Expected edge.** +0.15-0.25 R gross per event vs about 0.02-0.06 R costs. Union events: about 400-450 on
  2003-2025, about 150 in 2010-2017 (estimates from trigger frequencies, not counted). Power: t about 3.5 at n 450 if
  the edge is +0.15 R with sd 0.9 R; about 2.0 on explore alone.
- **Reading rule (frozen).** W2 as written: explore n >= 100, net > 0, day-summed t >= 2, net >= the leak-free control
  (random entry days in the same calendar month, same side and exits). Then confirm, then TEST-OLD. Components are
  reported, not gated. A pass promotes the pooled rule only.
- **Trial cost.** K +1.

### P3. Monthly and weekly predictor discovery, one trial (074, 084, 086, 093, 094, 063, 117, 038, 133, 017)
- **Config.** Weekly decisions with a 4-week forward label (next 20-day log return of gold, vol-scaled). Ten
  pre-registered predictors, each z-scored on trailing data only. Fold-internal selection (stability selection by
  ridge-logistic over 5 expanding folds, inside 2003-2017 only). The combined signal is the mean of the selected
  z-scores, with signs fixed from the sources (listed in the YAML), not learned.
- **Reading rule (frozen).** On 2018-01..2025-09: rank IC of the combined signal with the 4-week return >= 0.05,
  HAC t >= 2 (overlapping labels, 4-week Newey-West), and positive in >= 5 of 8 years. A pass sends the signal to P1's
  CP primitive as a second forecast, which needs its own pre-registration. Fail: all ten are retired as gold timing
  signals.
- **Trial cost.** 1 registry trial; K_eff +10 declared (each screened predictor is counted).

### P4. Daily trade forms through the next catalogue (049, 051, 053, 059, 023-025, 130, 134)
These are cheap once s3k-next exits and holds exist. They should enter the next sprint catalogue as ranked specs
under its own prior, not as individual trials. They are not proposed for this quarter's budget.

### Not proposed (X, 36 rows)
These are ideas already run or retired here, contradicted, or untradeable on our venue. Highlights:
- vol targeting as alpha (R-08);
- 20-day real-yield and dollar rules (R-16);
- COT and ETF flow rules (R-19);
- pre-FOMC drift, day-of-week, holiday, expiry and FND effects;
- intraday momentum, last-30-minute and ORB rules (R-02/R-05);
- RSI(2), IBS, turtle soup and their near-equivalents (sprint200 meanrev);
- NR7 daily (GS-C4);
- GLD/GDX pairs (decayed after publication, not on our venue);
- "miners lead gold" (contradicted).

## 7. Scalping (owner's standing request)

The literature adds no cost-surviving gold scalp. The intraday rules with peer-reviewed support have all been run here
and failed: ORB (Holmberg et al. 2013), first and last half-hour (Gao et al. 2018; Jin et al. JFM), settle-window
gamma momentum (Baltussen et al. 2021), fix front-running (Caminschi, Heaney 2014). The one working paper with
positive 5-minute gold results (Urquhart, Batten, Lucey et al. 2015, 2008-2014) does not charge clear costs. Its
common rules lose. The scalping agenda stays open on the existing microstructure lines (H-89 and the release-minute
cost arm), not on new literature.

## 8. Data to add (free first; join on `available_utc`)

| Source | Series | Unlocks | Cost |
|---|---|---|---|
| FRED | VIXCLS, DEXUSAL, DEXSFUS, DEXCHUS, DEXUSEU, DEXJPUS, DCOILWTICO | P2, 084, 094, 101, 107 | free |
| ALFRED (vintages) | CPIAUCSL, INDPRO, M2SL | 086, 111, 112 | free |
| Caldara-Iacoviello | daily GPR, GPR Threats | 105 | free |
| policyuncertainty.com | daily US EPU | 106 | free |
| Dukascopy 2003-2009 (W2 fetch) | gold 1m | the power gain for P1-P3 | free, in progress |
| Daily gold before 2003 | Bundesbank fix series, Stooq, Yahoo GC=F | 038, 117 and the P1 re-test | free, licences unverified (LBMA/IBA terms apply to fix data) |
| AQR public datasets | Commodities for the Long Run (monthly to 2015) | 171, cross-checks | free |
| CME GC curve | F1/F2 settlements | 071-073 (carry, lease, basis-momentum) | paid or unverified; not recommended now |

## 9. Owner decisions flagged (recommendation given, not assumed)

1. **A single-read evaluation class for fixed-spec, zero-parameter strategies.** One read on 2003-2025-09, gate
   net SR >= 0.42 (t >= 2) plus the P1 legs. Under W2's three-window chain these strategies have about 1-6% power.
   Gate thresholds are the owner's call. Recommendation: adopt it for P1 only, because P1 has no fitted parameter, so
   splitting the sample buys no protection against selection.
2. **Swap model SWAP-T (date-varying long swap).** A cost-of-record amendment (research director, finance-auditor,
   ADR 0064). Not owner-only, but it changes every multi-day result. Recommendation: adopt before P1/P2 run; report
   both.
3. **Venue (decision memo option C).** Futures or ETF would save the CFD markup on longs and the interest shorts would
   earn, about 0.5-3% a year depending on rates (section 2.3), not the whole swap. Recommendation: decide only after
   P1 reads; if P1 is INCONCLUSIVE because of swap, that is the evidence for the spend.
4. **Instruments.** XAGUSD as a second leg (096) is not recommended: there is no peer-reviewed net evidence.

## 10. Multiple-testing ledger and integrity

- This lane: K +0 (no run). If P1-P3 run: K +3 registry trials, K_eff +12 declared (P3 counts its 10 predictors).
- Every candidate later screened in bulk counts in K whether it fires or not. Near-duplicates (001-012 vs 013/015)
  should be tested as the ensemble once, not as 12 trials.
- Rows owed in `docs/research/hypotheses.md` (this lane may not edit it; research director to add):
  - P1-P3 as proposed;
  - the X list as "retired/contradicted from the literature, never trialled" (extends R-08);
  - a note on H-01 that its "power gain" condition is met by CP + 2003-2025 + SWAP-T.

## 11. Sources (fetched or confirmed in this lane, 2026-11-02)

- Szakmary, Shen, Sharma 2010 JBF 34(2) 409-426: https://ideas.repec.org/a/eee/jbfina/v34y2010i2p409-426.html
- Marshall, Cahan, Cahan 2008 JBF 32(9) 1810-1819: https://ideas.repec.org/a/eee/jbfina/v32y2008i9p1810-1819.html
- Han, Hu, Yang 2016 JBF 70 214-234 'Are there exploitable trends in commodity futures prices?': https://ideas.repec.org:443/a/eee/jbfina/v70y2016icp214-234.html
- Levine, Pedersen 2016 FAJ 72(3) 51-66: https://ideas.repec.org/a/taf/ufajxx/v72y2016i3p51-66.html
- Lemperiere et al. 2014, arXiv 1404.3274: https://arxiv.org/abs/1404.3274
- Hurst, Ooi, Pedersen 2017: https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing
- Babu et al. 2020 JIM 18(1) 'Trends everywhere': https://joim.com/trends-everywhere
- Goulding, Harvey, Mazzoleni 2023 JFE 'Momentum turning points' (summary): https://www.cxoadvisory.com/technical-trading/mitigating-impact-of-price-turning-points-on-trend-following/
- Wood, Roberts, Zohren 2022 JFDS, arXiv 2105.13727: https://arxiv.org/abs/2105.13727
- Lim, Zohren, Roberts 2019 JFDS, arXiv 1904.04912: https://arxiv.org/abs/1904.04912
- Kaminski, Lo 2014 JFM 18: https://dspace.mit.edu/handle/1721.1/114876
- Bongaerts, Kang, van Dijk 2020 FAJ 76(4): https://ideas.repec.org/a/taf/ufajxx/v76y2020i4p54-71.html
- Fernandez-Perez, Frijns, Fuertes, Miffre 2018 JBF 86: https://openaccess.city.ac.uk/id/eprint/17843/
- Bianchi, Drew, Fan 2015 JBF 59: https://ideas.repec.org/a/eee/jbfina/v59y2015icp423-444.html ; Zhang, Urquhart 2020: https://ideas.repec.org/a/eme/rbfpps/rbf-05-2019-0067.html
- Boons, Porras Prado 2019 JF 74(1): https://ideas.repec.org/a/bla/jfinan/v74y2019i1p239-279.html
- Kang, Rouwenhorst, Tang 2020 JF 75(1): https://ideas.repec.org/a/bla/jfinan/v75y2020i1p377-417.html
- Hong, Yogo 2012 JFE 105(3): https://www.nber.org/papers/w16712
- Levine, Ooi, Richardson, Sasseville 2018 FAJ 74(2): https://www.nber.org/papers/w22793
- Chen, Rogoff, Rossi 2010 QJE 125(3): https://papers.ssrn.com/abstract=1112013
- Brooks 2017 'A half century of macro momentum': https://www.aqr.com/Insights/Research/White-Papers/A-Half-Century-of-Macro-Momentum
- Pukthuanthong, Roll 2011 JBF 35(8): https://ideas.repec.org/a/eee/jbfina/v35y2011i8p2070-2083.html
- Erb, Harvey 2013 FAJ 69(4) 'The golden dilemma': https://www.nber.org/papers/w18706
- Baur, Smales 2020 JBF 117: https://ideas.repec.org/a/eee/jbfina/v117y2020ics037842662030090x.html
- Balcilar, Gupta, Pierdzioch 2016 RP 49; Raza et al. 2018 RP 57: https://ideas.repec.org/a/eee/jrpoli/v57y2018icp61-68.html
- Baur 2013 RIBAF 27(1): https://ideas.repec.org/a/eee/riibaf/v27y2013i1p1-11.html ; Potrykus, Augustynowicz 2024: https://ideas.repec.org/a/vrs/ijomae/v60y2024i3p157-172n1001.html
- Keloharju, Linnainmaa, Nyberg 2016 JF 71(4): https://www.nber.org/papers/w20815.pdf
- Cieslak, Morse, Vissing-Jorgensen 2019 JF 74(5): https://papers.ssrn.com/abstract=2687614
- Awartani, Hussain, Virk 2024 IRFA (gold and FOMC surprises): https://e-space.mmu.ac.uk/635429
- Caporale, Plastun 2021 FMPM 35: https://link.springer.com/10.1007/s11408-021-00380-w
- Lou, Polk, Skouras 2019 JFE 134(1): https://personal.lse.ac.uk/polk/research/TugOfWar.pdf
- Neely, Rapach, Tu, Zhou 2014 MS 60(7): https://pubsonline.informs.org/doi/suppl/10.1287/mnsc.2013.1838
- Li, Yu 2012 JFE (anchors): https://academicnewsletter.sufe.edu.cn/info/356820
- Hauptfleisch, Putnins, Lucey 2016 JFM 36(6): https://opus.lib.uts.edu.au/handle/10453/41414
- Lucey, O'Connor 2016 'Are gold bugs coherent?': https://research.ucc.ie/en/publications/are-gold-bugs-coherent/
- Meader 2016, LBMA Alchemist 83 (SGE premium): https://www.lbma.org.uk/alchemist/issue-83/links-between-the-chinese-and-international-gold-prices
- Batten et al. 2017 PLOS ONE (intraday precious metals): https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5407636/
- Urquhart et al. 2015 WP (5-minute gold/silver rules; summary): https://www.cxoadvisory.com/technical-trading/high-frequency-technical-trading-of-gold-and-silver/
- Jin, Kearney, Li, Yang, JFM (intraday TSMOM, Chinese metals): https://hull-repository.worktribe.com/output/3295550
- Clenow rules (author quoted): https://scan.stockcharts.com/discussion/comment/1456
- GLD/GDX replication (decay after publication): https://quantrocket.com/blog/pairs-trading-still-viable
- Roesch, Schmidbauer, Jiang 2013 (Chinese New Year): https://ecomod.net/system/files/gold_and_china_ecomod2013.pdf
- Turn of month (vendor): https://www.interactivebrokers.com/campus/traders-insight/securities/commodities/how-does-gold-perform-around-the-turn-of-the-month/
- Earlier verified in `state-of-the-art.md`: Kurth et al. 2026, Huang et al. 2020, Baltas-Kosowski, Cederburg et al.
  2020, Baltussen et al. 2021, Caminschi-Heaney 2014, Blose-Gondhalekar 2013, Kurov et al. 2021, Caldara-Iacoviello GPR.

All other citations in the YAML carry *(unverified)* and are leads until a staff check confirms them.
