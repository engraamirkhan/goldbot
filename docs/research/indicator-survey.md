# Indicator and tool survey for goldbot (XAUUSD)

Written 2026-10-10 for the Q1 2027 budget (20 trials). It complements `state-of-the-art.md` (the "SoA review"), which
already covers TSMOM, volatility targeting, carry, intraday momentum, meta-labelling, regime filters, CFD costs,
overfitting controls and gold macro drivers. This survey does not repeat that material. It covers the indicator and
tool space: the owner's trader toolkit, plus what the owner asked us to find beyond it.

**Legend.**
- **Evidence:** **S** strong (replicated, ideally net of costs), **M** moderate (one good study or replicated gross),
  **W** weak (thesis, practitioner, in-sample, or another asset only), **N** none found. Bracketed numbers point to
  the reference list at the end. *(unverified)* means we did not fetch or confirm the source this session.
- **Data:** **D** = Dukascopy XAUUSD bars and tick counts (in hand). **D+** = other Dukascopy instruments, free,
  needing new pull jobs (XAGUSD, EURUSD, USDJPY, USDCNH, USA500.IDX, BRENT). **F** = FRED (DTWEXBGS, DFII10, DGS10,
  DGS2, DFF and T10YIE are already in `goldbot/data/macro.py`; GVZCLS and VIXCLS would be new and free). **C** = CFTC
  COT (managed-money net is already loaded). **E** = GLD holdings (already loaded).
- **In goldbot:** the registered feature name in `goldbot/features`, "partial" if only part of it exists, or "no".
- **Horizon:** the bar sizes (15m, 1h, 4h, 1d) where the signal's expected move can clear our round trip of about
  0.14–0.39 R.

## 1. Universe table

### 1a. Trend and momentum

| # | Name | Definition | Evidence | Gold rationale | Horizon | Data | In goldbot | PIT risk |
|---|---|---|---|---|---|---|---|---|
| 1 | Vol-scaled TSMOM (slow) | Sign or z of the 1–12-month return over volatility | S gross, M net [SoA A1] | Slow investor and CTA flows; works only diversified and slow | 1d | D | `tsmom` (1h horizons only) | Low |
| 2 | Vol-scaled TSMOM (intraday) | The same on 24–480 bars | W; dead on small-tick contracts after 2009 [SoA A1, R4] | Small tick, so HFT front-runs the trend flow | 1h, 4h | D | `tsmom` | Low |
| 3 | MA crossover / distance / ribbon | Fast vs slow SMA, EMA or HMA, in ATR units | W; profits found in old samples, declining since [R6, R7] | Proxy for slow trend | 4h, 1d | D | `moving_averages` | Low |
| 4 | MACD | EMA12 − EMA26 and its signal line | W/N *(unverified)* | Same information as #3 | 4h, 1d | D | no | Low |
| 5 | ADX / DMI | Wilder's directional-movement strength | N | Trend-vs-chop filter | 1h–1d | D | `trend_strength` | Low |
| 6 | Donchian channel position / breakout | Close vs the n-bar high and low | M for slow turtle-style rules on futures [SoA A1]; W intraday | Stop clustering at extremes [R9, R10] | 4h, 1d | D | `trend_strength`, `breakout` | Low (uses `shift(1)`) |
| 7 | Kalman trend / local-level slope | Filtered level and slope of a state-space model | W *(unverified)* | Adaptive trend with fewer whipsaws | 4h, 1d | D | no | **High** if the smoother is used instead of the filter |
| 8 | Regression-slope t-stat | OLS slope / SE over n bars | W | Trend quality | 4h, 1d | D | no | Low |
| 9 | Kaufman efficiency ratio | \|net move\| / sum of \|moves\| | N | Trend vs noise | 1h–1d | D | no | Low |
| 10 | Supertrend / Parabolic SAR | ATR-trailing or accelerating stop line | N | Only an exit mechanic | 1h–4h | D | no | Low |
| 11 | Ichimoku | Tenkan, kijun, cloud, chikou | N | None specific | 4h, 1d | D | no | **High**: chikou is the close shifted 26 bars *back* |
| 12 | Heikin-Ashi trend | Smoothed recursive candles | N | None | any | D | no | **High**: HA close is not a tradable price |

### 1b. Mean reversion

| # | Name | Definition | Evidence | Gold rationale | Horizon | Data | In goldbot | PIT risk |
|---|---|---|---|---|---|---|---|---|
| 13 | RSI(14) / extremes | Wilder RSI with 25/75 zones | W/N; our trial #3 shows no gross edge | None specific | 15m–1h | D | `mean_reversion` | Low |
| 14 | Bollinger z / %b | (close − SMA20) / SD20 | W/N | None | 15m–1h | D | `mean_reversion` | Low |
| 15 | Stochastic / Williams %R / CCI | Close vs the n-bar range | N | None | 15m–1h | D | no | Low |
| 16 | Distance to VWAP (tick-weighted) | Close vs the rolling tick-weighted typical price | W | Execution anchor for institutions | 15m–1h | D | `mean_reversion` (`dist_vwap48_atr`) | Medium: tick counts are broker-specific |
| 17 | Session-anchored VWAP | VWAP since the session open | W/N | Desk benchmark at the London and NY opens | 15m–1h | D | no | Low |
| 18 | Ornstein–Uhlenbeck half-life | AR(1) speed of reversion on a spread or price | W (practitioner) | Regime measure, not a signal | 1h–1d | D | no | Low if fitted on a rolling window |
| 19 | Variance ratio (Lo–MacKinlay) | Var(k-bar return) / (k·Var(1-bar return)) | M as a test [R26]; W as a signal | VR > 1 means trending, < 1 means reverting | 1h–1d | D | no | Low |
| 20 | Rolling Hurst / DFA | Long-memory exponent | W; gold H is unstable over time [R27] | Regime diagnostic | 4h, 1d | D | no | Low (rolling) |
| 21 | Short-term reversal after large moves | Fade a > k-ATR bar | W [R18] | Liquidity provision after stop cascades | 15m–1h | D | partial (`returns`) | Low |
| 22 | Overnight vs intraday ("tug of war") | Asia-session return predicts the London/NY return with the opposite sign | M in equities [R18]; W in gold | Asian physical buying vs Western selling [SoA B2] | 1h | D | partial (`session`) | Low |

### 1c. Volatility and range estimators (mostly for sizing, exits and filters, not direction)

| # | Name | Definition | Evidence | Gold rationale | Horizon | Data | In goldbot | PIT risk |
|---|---|---|---|---|---|---|---|---|
| 23 | ATR(14) and ratio | Wilder true range | S as a volatility measure | Stop and target scaling | all | D | `atr` | Low |
| 24 | Realised vol (close-to-close) | Rolling SD of log returns | S for forecasting volatility | Sizing, regime | all | D | `realised_vol` | Low |
| 25 | Parkinson | High–low range estimator | S (efficiency) [R25] | Uses the bar's full range | all | D | `realised_vol` (`parkinson_20`) | Low |
| 26 | Garman–Klass / Rogers–Satchell | OHLC estimators; RS is drift-robust | S (efficiency) [R25] | Better volatility with fewer bars | all | D | no | Low |
| 27 | Yang–Zhang | Overnight, open and RS combined | S; cuts TSMOM turnover by more than a third [R5, R25] | Handles weekend and session gaps | 4h, 1d | D | no | Low |
| 28 | HAR-RV forecast | Volatility regressed on daily, weekly and monthly RV | S for volatility forecasting [R25] *(unverified)* | Forecast the expected move, so targets can be set ≥ 3× costs | 1h–1d | D | no | Medium: coefficients must be fitted inside the fold |
| 29 | Intraday-periodicity-adjusted range | Range / typical range for that hour of the week | S for volatility seasonality [R25] *(unverified)* | Separates "quiet for 03:00" from "quiet" | 15m–1h | D | no | Low if the profile uses only past data |
| 30 | Bipower variation / jump share | RV minus jump-robust variance | M [R25] | Jump days behave differently | 1h–1d | D | no | Low |
| 31 | Lee–Mykland jump flag | Return / local bipower volatility > threshold | S as a detector [R17]; post-jump drift untested for gold | News shocks and stop cascades | 15m–1h | D | no | Low |
| 32 | Realised skew / kurtosis | Higher moments of intrabar returns | M in equities and commodities (cross-section) [R15] | Crash-risk premium | 1d | D (ticks) | no | Low |
| 33 | Vol-of-vol | SD of rolling RV | W | Regime instability | 4h, 1d | D | no | Low |
| 34 | Volatility term structure | RV short / RV long | W | Expansion vs compression | 1h–1d | D | `realised_vol` (`rv_ratio`), `atr` | Low |
| 35 | Range compression (NR7, squeeze) | Narrowest range, or bandwidth percentile | N (practitioner) | Breakouts after compression | 1h–4h | D | `breakout` (`compression_8_96`) | Low |

### 1d. Volume and tick-flow proxies

| # | Name | Definition | Evidence | Gold rationale | Horizon | Data | In goldbot | PIT risk |
|---|---|---|---|---|---|---|---|---|
| 36 | Tick count / z-score | Quote updates per bar | W as a volume proxy (FX ρ ≈ 0.9, secondary) [R39] | Activity and news | all | D | `microstructure`, `breakout` | **Medium**: Dukascopy ticks ≠ VPS broker ticks (train/serve skew) |
| 37 | MFI(12) | RSI weighted by volume | N | The owner's KOG indicator | 15m–1h | D | `money_flow` | Low |
| 38 | OBV / Chaikin A/D | Cumulative volume signed by close location | N | None | 1h–1d | D | no | Low |
| 39 | Signed tick imbalance (tick rule) | (upticks − downticks) / ticks per bar | S at seconds horizon (OFI) [R16]; decays fast | Order-flow pressure | 15m | D (raw ticks) | no | Low; needs a tick-level job |
| 40 | Amihud-style illiquidity | \|return\| / tick count | M for liquidity pricing [R34] *(unverified)* | Thin-market moves revert | 1h–1d | D | no | Low |
| 41 | Spread regime | Spread / ATR, spread vs median | S as a cost measure | Cost filter; spread widening ahead of news | all | D | `microstructure` | Low |
| 42 | VPIN | Volume-bucketed order imbalance | W; contested [R33] *(unverified)* | Toxic flow ahead of jumps | 15m–1h | D (ticks) | no | Medium: bucket boundaries |

### 1e. Market structure and price action

| # | Name | Definition | Evidence | Gold rationale | Horizon | Data | In goldbot | PIT risk |
|---|---|---|---|---|---|---|---|---|
| 43 | Session open/high/low (Asia, London, NY) | Running extremes of the current session; completed extremes of prior sessions | W | Desk handover levels | 15m–1h | D | partial (`intraday_session` has the open only) | **High** if the session's final high/low is used before the session ends |
| 44 | Opening range / ORB | First n minutes' range, then breakout | W [R11]; our session_open trials #1/#7/#11/#13 null | Flow at the open | 15m | D | retired family | Low |
| 45 | Previous day/week/month high-low-close | Distances in ATR | M via stop clustering [R9, R10] | Stops sit beyond obvious extremes | 1h–4h | D | partial (`breakout` uses 96-bar windows) | Low if the day boundary matches the 22:00 UTC rollover |
| 46 | Round numbers ($5/$10/$50/$100) | Distance to the nearest round level | M in FX: take-profits cluster at round numbers, stops just beyond them [R9] | Gold is quoted in whole dollars; options strikes sit at round levels | 15m–4h | D | no | None |
| 47 | Swing highs/lows, HH/HL state | Confirmed fractal pivots | W | Structure; stop placement | 1h–4h | D | `swings` (shifted by lag) | **High** without the lag shift |
| 48 | S/R level book | Clustered confirmed swing levels, touches, age | M via [R9]; W otherwise | Order clustering | 1h–4h | D | `support_resistance` | Medium: confirmation lag |
| 49 | Liquidity sweep | Wick beyond a prior extreme, then a close back inside | W via [R10] (stop cascades exhaust, then revert) | Stop runs at Asia and London highs | 15m–1h | D | no | Low if it uses only the closed bar |
| 50 | Break of structure / CHoCH | Close beyond the last confirmed swing | N | Same as #47 | 1h–4h | D | partial (`swings`) | Medium |
| 51 | Fair value gap | 3-bar gap: low₃ > high₁, or high₃ < low₁ | N; FVG "fill" rate ≈ the random-level base rate [R19] | None beyond a gap | 15m–1h | D | no (`gaps` covers bar-to-bar gaps) | Low once bar 3 closes |
| 52 | Order block | Last opposite candle before an impulsive move | N [R19] | None | 15m–1h | D | no | **High**: "impulsive" is defined after the fact |
| 53 | Premium/discount zone | Position within the dealing range (50% line) | N | Same as #6 | 1h–4h | D | partial (`donchian_pos_20`) | Medium: range anchors |
| 54 | Market profile / volume-at-price (POC, value area) | Tick-count histogram by price, per session | N [vendor only] | Acceptance levels | 15m–1h | D (ticks) | no | **High** if built on the unfinished session |
| 55 | Floor pivots / Fibonacci | Formula levels from the previous day; retracements | N | Self-fulfilling at most | 15m–1h | D | no | Low |
| 56 | Candlestick patterns | Engulfing, pin bar, inside/outside bars | N net of costs [R29] *(unverified)* | None | 15m–4h | D | `candles` | Low |
| 57 | Gaps (weekend, session) | Open − previous close; fill status | W | Weekend news repricing | 1h–1d | D | `gaps` | Low |

### 1f. Intermarket, positioning and flows, options-implied

| # | Name | Definition | Evidence | Gold rationale | Horizon | Data | In goldbot | PIT risk |
|---|---|---|---|---|---|---|---|---|
| 58 | Broad USD (level, 5/20-day change) | DTWEXBGS | S contemporaneous; W predictive | Gold is priced in USD | 1d | F | `macro` | Low (H.15 timestamp) |
| 59 | 10-year real yield, breakevens | DFII10, T10YIE | S before 2022; broken after [SoA B1, R23] | Opportunity cost | 1d | F | `macro` | Low |
| 60 | Curve / policy (10y−2y, fed funds) | DGS10 − DGS2, DFF | W | Policy cycle | 1d | F | `macro` (levels) | Low |
| 61 | Silver, gold/silver ratio z | XAGUSD returns; log ratio z-score | W (no out-of-sample journal test found) | Shared precious-metals factor; silver is more cyclical | 1h–1d | D+ | no | Low |
| 62 | Intraday FX lead (EURUSD, USDJPY, USDCNH) | Lagged 1–4-bar returns, rolling beta residual | W/N | USD and CNY flows | 15m–1h | D+ | no | Low; align bar closes |
| 63 | Equity stress (USA500 drawdown, VIX) | Index return and drawdown; VIXCLS | S as a short-lived haven (about 15 days) [R22] | Flight to safety | 1h–1d | D+, F | no | Low |
| 64 | Crude / copper-gold | Brent returns; Cu/Au ratio | W | Inflation and growth proxy | 1d | D+ (Cu: paid or LME) | no | Low |
| 65 | Gold miners (GDX) | Miner vs gold returns | W; gold leads miners at long horizons [R37] | Equity-market view of gold | 1d | Paid (EOD ~free via Stooq) | no | Low |
| 66 | Geopolitical risk index | Caldara–Iacoviello daily GPR | M (threats, not acts) [R24] | Haven demand | 1d | Free CSV | no | Medium: vintages are recomputed |
| 67 | COT speculative pressure | MM net / OI vs its 52-week range | M across commodities (gross Sharpe 0.55) [R14]; mixed for gold [SoA B3] | Crowding, then reversal | 1d (weekly) | C | partial (`cot_mm_net` raw) | **Medium**: Tuesday data released Friday 15:30 ET |
| 68 | ETF flow pressure | GLD tonnes Δ1/Δ5/Δ20 | W: +1–2 days, then reversal at 3–5 days (thesis) [R21] | Western investor flow | 1d | E | partial (raw tonnes) | Medium: published next day |
| 69 | SGE premium | Shanghai vs LBMA price | W [R20] | Chinese physical demand | 1d | WGC (free, lagged) | no | Medium |
| 70 | Central-bank purchases | IMF IFS reserves | S as a level driver; useless intraday [SoA B1] | Price-insensitive buyer | monthly | IMF (free) | no | **High**: 1–2-month lag, revised |
| 71 | GVZ level / change | 30-day implied volatility of GLD options | W for direction (no peer-reviewed test found) | Fear gauge | 1d | F (GVZCLS) | no | Low |
| 72 | Gold variance risk premium | GVZ² − forecast RV² | M: predicts gold excess returns at 1–24 months, in and out of sample; sign depends on definition [R15] | Compensation for crash and jump risk | 1d (4h execution) | F + D | no | Low; sign convention must be pre-registered |
| 73 | Options skew / risk reversals | 25-delta RR on COMEX options | M in commodities (implied skew) [R15] | Tail demand | 1d | CME DataMine (paid) | no | Low |
| 74 | Futures basis / lease rate | GC front month vs spot | M as carry [SoA A3] | Swap model; scarcity | 1d | CME (paid) or broker swap | no | Low |

### 1g. Calendar and events, regime models, ML representations

| # | Name | Definition | Evidence | Gold rationale | Horizon | Data | In goldbot | PIT risk |
|---|---|---|---|---|---|---|---|---|
| 75 | Session / hour-of-week | Session id, minutes since each open, DST flags | W (Asia drift) [SoA B2] | Physical demand clears in Asia | 1h | D | `session` | Low |
| 76 | Tier-1 event proximity | Minutes to/since NFP, CPI, FOMC | S for volatility; N for direction [SoA B2, R36] | Volatility bursts | 15m–4h | Calendar | `calendar_events` | Low (scheduled) |
| 77 | Macro surprise (actual − consensus) / SD | Standardised surprise | M: CPI, unemployment and capacity utilisation move gold intraday [R36]; drift undocumented | USD and rates repricing | 15m–4h | Consensus feed (scraped, or paid Bloomberg) | no | **High**: consensus and actuals get revised; use first release |
| 78 | FOMC day / pre-FOMC | Announcement-day dummies | N after 2015 [SoA B3] | — | 1h–1d | Calendar | partial | Low |
| 79 | Turn of month / quarter-end | Last 2 and first 3 trading days | W *(unverified)* | Rebalancing and fund flows | 1d | Calendar | no | None |
| 80 | COMEX options expiry, first notice day | Days to the GC expiry or roll | W *(unverified)* | Pinning at strikes, roll flow | 1h–1d | CME calendar (free) | no | None |
| 81 | Lunar New Year window | ±10 trading days | W (one working paper) [R13] | Chinese buying ahead of the holiday | 1d | Calendar | no | None |
| 82 | Autumn/winter seasonality, Indian festivals | Month dummies; Diwali/Akshaya Tritiya windows | W; the autumn effect faded and a "winter effect" replaced it [R12] | Jewellery demand | 1d | Calendar | no | None |
| 83 | Weekday / weekend / triple-swap Wednesday | Day-of-week dummies | W, fragile [R44] | Weekend risk; swap ×3 | 1d | D | `session` (`dow`) | None |
| 84 | Volatility tercile regime | RV percentile over history | M as a conditioning variable | Cost in R varies with volatility | all | D | `realised_vol` (`vol_tercile`) | Low (rolling) |
| 85 | HMM / Markov-switching states | Filtered probabilities of 2–3 return/volatility states | M multi-asset out of sample, W for gold alone [R28] | Haven vs risk-on regimes | 4h, 1d | D | no | **High**: forward-backward smoothed probabilities leak; use filtered only, fit in-fold |
| 86 | Change-point (BOCPD, CUSUM) | Run-length posterior; residual CUSUM | W as a signal *(unverified)* [R41] | Regime breaks (e.g. 2022) | 1d | D | CUSUM exists in health checks, not as a feature | Low (online) |
| 87 | Fractional differentiation | Memory-preserving stationary price | W [R31] | Keeps level information for ML | 1h–1d | D | no | Low (fixed-window weights) |
| 88 | Path signatures (depth 2–3) | Iterated integrals of the (t, price, ticks) path | W *(unverified)* | Compact nonlinear path summary | 15m–4h | D | no | Low |
| 89 | Random-feature / large ridge models | Many random nonlinear features with shrinkage | M for market timing (equities) [R32] *(unverified)* | Complexity helps when the signal is weak | 1d | D, F | no | Medium: tuning must be in-fold |
| 90 | Autoencoder / TS2Vec / foundation models (Chronos, TimesFM) | Learned embeddings or zero-shot forecasts | N for net trading returns [R40] | — | any | D | no | **High**: pretraining corpora may include our test years |
| 91 | Gradient-boosted meta-model | Model over bulk features | Our AUC ≈ 0.50 (#53); M only with an edged primary [SoA A5] | — | all | all | `meta`/pooled | Low (purged CV) |

That is 91 candidates. 50 have no direct counterpart in goldbot today.

## 2. Top 15 shortlist

Each row was scored 1–5 on evidence, gold rationale, cost fit (expected move vs 0.14–0.39 R) and data cost (5 = in hand,
free). Rank = product; ties are broken by event count, then data cost.

| Rank | Candidate (#) | Score E·R·C·D | Why it might work in gold | How to test |
|---|---|---|---|---|
| 1 | Slow vol-scaled TSMOM, 1d signal, multi-week hold (#1) | 4·4·4·5 = 320 | Only trend horizon with replicated support; target ≫ spread; shorts pay no swap | Own trial (SoA H1), 4h execution, measured swap |
| 2 | Gold VRP = GVZ² − HAR-RV² (#72, #28) | 4·4·3·5 = 240 | Option sellers demand a premium for jump risk; documented for gold out of sample | Bulk features, then the discovery screen; own trial if it survives (1d signal, 5–20-day hold) |
| 3 | Volatility forecast stack: YZ, GK/RS, HAR, periodicity-adjusted (#26–#29) | 4·2·5·5 = 200 | Not alpha. It sets targets at ≥ 3× round trip and filters trades whose expected move cannot pay the costs | Bulk features plus a cost-to-move filter applied to every family. No trial on its own |
| 4 | Round numbers + previous day/week extremes (#45, #46) | 3·4·3·5 = 180 | Osler: take-profits cluster at round numbers (reversal), stops just beyond them (acceleration); gold is quoted in whole dollars | Bulk features; screen; specialist rule "break of $X0 with a stop cluster" only if selected |
| 5 | Asia-session drift / overnight–intraday split (#75, #22) | 2·4·4·5 = 160 | Asian physical/ETF demand vs Western selling | Own trial (SoA H2) on pre-holdout data |
| 6 | Equity stress / VIX jump (#63) | 4·3·3·4 = 144 | Short-lived haven bid after equity shocks | Bulk (USA500 needs a D+ pull; VIXCLS free); screen |
| 7 | USD, real-yield and breakeven 5/20-day changes (#58, #59) | 3·3·3·5 = 135 | Opportunity cost and USD pricing; the 2022 break makes it a conditioning variable, not a rule | Already in `macro`; add changes; screen; SoA H3 as a variant |
| 8 | COT speculative pressure, 52-week normalised (#67) | 3·3·3·5 = 135 | Crowded managed-money longs unwind (weekly, so fewer events than #7) | Bulk feature (Friday release stamp); screen at 1d |
| 9 | Liquidity-sweep flag (#49) | 2·4·3·5 = 120 | Stop cascade exhausts at the Asia or London extreme, then reverts | Bulk feature (causal definition); screen |
| 10 | Jump flag + post-jump behaviour (#31, #30) | 3·2·3·5 = 90 | News shocks: continuation if fundamental, reversal if a stop cascade | Bulk; screen; a rule trial only if selected |
| 11 | ETF flow pressure Δ1/Δ5 (#68) | 2·3·3·5 = 90 | Western flow price pressure, then reversal | Bulk; screen |
| 12 | Signed tick imbalance (#39) | 4·2·2·4 = 64 | Order-flow pressure. Very short-lived, so mainly an entry-timing input | Bulk (needs a tick job); screen at 15m |
| 13 | Filtered HMM state probabilities (#85) | 2·3·2·5 = 60 | Haven vs risk-on regimes change the sign of the other features | Fitted inside each fold within the discovery pipeline; never bulk-precomputed |
| 14 | Macro-surprise continuation (#77) | 3·3·3·2 = 54 | CPI and labour data reprice USD and rates; volatility several times the spread | Needs a consensus feed; own trial (SoA H4) later |
| 15 | Silver and FX intraday lead-lag (#61, #62) | 2·3·2·4 = 48 | Shared metals factor; USD and CNY flows | Bulk after the D+ pulls; screen |

## 3. Known traps

### Repainting and look-ahead

| Tool | Trap | Rule for goldbot |
|---|---|---|
| ZigZag | The last leg is redrawn until a reversal of x% confirms it | Never use as a feature. Use `swing_points` shifted by `lag` |
| Swing pivots / fractals / BOS | A pivot needs `lag` bars on its right | Shift by lag (as `swings` does); a level exists only from its confirmation bar |
| Centred MAs, DPO, Hodrick–Prescott (two-sided), wavelet denoising, kernel regression [R30] | They use future bars | Causal one-sided filters only |
| Kalman smoother, HMM forward-backward | Smoothed states use the whole sample | Filtered states only, with parameters fitted in the training fold |
| Ichimoku chikou | The close shifted 26 bars *back* | Exclude |
| Heikin-Ashi | Backtests fill at the HA close, which never traded | Real OHLC only |
| Session/day high-low, volume profile, opening range | The final extreme is unknown until the session closes | Running values intraday; completed values from the next session |
| Order blocks | "Impulsive move" is defined by what came after | Define by a fixed causal rule (an n-ATR move confirmed on close), stamped at confirmation |
| Revised macro (NFP, CPI, GDP), GPR, IMF reserves | Revised values were not known at the time | First-release vintages (ALFRED) and `available_utc` |
| COT, GLD holdings | Values are dated before they were published | Stamp COT on Friday 15:30 ET and GLD on the next day; `asof_join` only |
| Full-sample normalisation or percentiles | Uses future distribution information | Rolling or in-fold only (`vol_tercile` already does this) |
| Foundation models | Pretraining may include the test years | Exclude from evaluation |
| Tick counts | Dukascopy ticks ≠ the broker's ticks in live trading | Ratio or z-score forms only; compare against VPS tick counts in shadow |

### Popular tools with no evidence after costs

These have no peer-reviewed support net of costs for gold or FX that we could find:
- Fibonacci, Elliott, Gann and harmonic patterns.
- Floor pivots, Ichimoku, Parabolic SAR and Supertrend as signals.
- Stochastic, Williams %R and CCI on their own.
- Candlestick patterns [R29].
- Volume profile / market profile.
- Every "smart money" construct (FVG, order blocks, BOS/CHoCH, premium/discount). A literature check found no
  peer-reviewed test, and one unreviewed preprint found no edge as taught [R19].
- "Killzone" session timing, which our session_open and intraday_momentum nulls already cover.
- Data-mined MA parameter grids. Gold 5-minute rules worked only after a parameter search, and the paper does not show
  costs [R8].

These stay as cheap bulk features (several already are) so the screen can reject them honestly. None gets its own
trial.

## 4. Efficient testing plan

### 4a. Bulk features (no trial cost)

Add these as registered, point-in-time features that pass the lookahead test. Adding a feature is not a trial. A trial
is a registry entry that is evaluated out of sample.
- **`vol_estimators`:** GK, RS, YZ, HAR-RV forecast, periodicity-adjusted range, bipower/jump share, Lee–Mykland flag,
  realised skew/kurtosis, vol-of-vol.
- **`regime_stats`:** variance ratio, rolling Hurst, efficiency ratio, OU half-life, regression-slope t.
- **`levels_ext`:** round-number distances ($5/$10/$50/$100), previous day/week/month high/low/close distances, running
  session high/low, completed prior-session high/low, sweep flag, FVG nearest-unfilled distance and age, causal order
  block distance.
- **`flow`:** signed tick imbalance, Amihud-style ratio.
- **`calendar_ext`:** turn of month, quarter-end, COMEX expiry and first notice day, Lunar New Year and Indian
  festival windows, triple-swap Wednesday, days to FOMC.
- **`intermarket`:** XAGUSD, EURUSD, USDJPY, USDCNH, USA500 and Brent returns; rolling beta residual; gold/silver z.
  Needs D+ pull jobs on `data-dukascopy.yml`, run incrementally (no `full_refresh`).
- **`macro_ext`:** GVZCLS, VIXCLS, VRP, 5/20-day changes, 10y−2y, COT pressure, GLD Δ1/Δ5/Δ20, GPR.
- **`ml_repr`:** fractionally differentiated close, depth-2 path signature of the last 32 bars.

That is roughly 120 columns. The 40-feature cap applies to models, not to the library.

### 4b. One pre-registered feature-discovery trial

- **Target:** a triple-barrier label on **4h** decision bars, with target and stop set from the HAR-RV forecast so the
  target is at least 3× the measured round trip, and `max_bars` 30. Both sides are labelled, so the label does not
  depend on any primary.
- **Validation:** an expanding walk-forward with 6-month test folds, purged and embargoed. The research window ends
  2025-09-30 and the holdout stays untouched.
- **Fold-internal selection (training fold only):**
  1. Cluster features by |Spearman ρ| > 0.7 and keep each cluster's medoid.
  2. Stability selection [R35]: L1-logistic regression on 100 half-samples drawn by weekly block bootstrap. Keep a
     feature if its selection probability is ≥ 0.75. This bounds expected false selections at E[V] ≤ q²/((2π−1)p).
  3. Confirm with grouped permutation importance from a shallow LightGBM on an inner purged validation split. A
     feature must also reduce log-loss.
  4. Cap at 40 features. Refit on the full training fold and predict the test fold.
- **Reading rule (decided now):**
  - **Continue** if pooled out-of-sample AUC ≥ 0.53 with block-bootstrap 95% lower bound > 0.50, **and** the
    pre-specified rule (trade when p ≥ the training fold's top-tercile cut) gives net mean R > 0 on ≥ 1,000 events.
  - **Survivors** are clusters selected in ≥ 70% of outer folds with a stable sign. At most five go forward.
  - **Stop** if AUC < 0.52. The bulk features carry no 4h directional information: retire them as signals and keep
    them as risk and cost filters only.
- **Deflated-Sharpe accounting:**
  - The discovery trial is **one registry trial**: selection sits inside the estimator, so its out-of-sample score is
    honest for the procedure.
  - Choosing survivors afterwards looks at outer-fold selection frequencies over the whole research window. Every later
    survivor trial therefore uses **N = registry count + K_eff**, where K_eff is the number of clusters screened
    (expected about 35). It does not use the registry count alone.
  - hypotheses.md records K_eff so the count stays honest.

### 4c. Q1 2027 allocation (20 trials; plan 13 and hold 7 in reserve)

| Slot | Trial | Trials |
|---|---|---|
| 1 | Slow TSMOM, 1d signal, 4h execution, measured swap (SoA H1) | 1 |
| 2 | The same, conditioned on USD and real yields (SoA H3) | 1 |
| 3 | Asia drift (SoA H2) | 1 |
| 4 | Feature discovery (4b) | 1 |
| 5–9 | Up to five survivors as specialist rules or features on the best primary (VRP and round numbers are the leading priors) | ≤ 5 |
| 10 | Macro-surprise continuation, only if a consensus feed exists by February | 1 |
| 11–13 | Next-generation variants of any passer (exit or horizon neighbours) | ≤ 3 |
| — | Reserve, not spent unless a passer needs a confirmation | 7 |

Owner decisions flagged:
- D+ data pulls (Actions minutes).
- A paid consensus calendar or CME options data.
- The P4 event minimum for daily families (slots 1–2 and VRP).
- Raising the budget is not recommended.

## References

- R4 Kurth, Eisler, Rej, Bouchaud 2026, arXiv 2607.01550 (see SoA).
- R5 Baltas & Kosowski, SSRN 2140091 (see SoA).
- R6 Park & Irwin 2007, *J. Economic Surveys* 21(4) 786–826, https://ideas.repec.org/a/bla/jecsur/v21y2007i4p786-826.html. Survey of 95 studies, 56 positive but with data-snooping and cost caveats (secondary summary).
- R7 Brock, Lakonishok, LeBaron 1992, *JF* 47(5), https://ideas.repec.org/a/bla/jfinan/v47y1992i5p1731-64.html. DJIA, gross. Sullivan, Timmermann & White 1999: the edge goes once data snooping is accounted for.
- R8 Urquhart, Batten, Lucey, McGroarty, Peat, "Does technical analysis beat the market? Evidence from high frequency trading in gold and silver", https://c.mql5.com/forextsd/forum/174/does_technical_analysis_beat_the_market__evidence_from_high_frequency_trading_in_gold_and_silver.pdf. Year, venue and cost treatment *(unverified)*.
- R9 Osler 2003, *JF*, "Currency orders and exchange rate dynamics"; NY Fed SR125, https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr125.html. FX dealer order book, gross.
- R10 Osler 2005, *JIMF* 24(2) 219–241, "Stop-loss orders and price cascades", https://ideas.repec.org/p/fip/fednsr/150.html.
- R11 Holmberg, Lönnbark, Lundström 2013, *FRL* 10(1) 27–33, https://ideas.repec.org/a/eee/finlet/v10y2013i1p27-33.html. Results *(unverified)*.
- R12 Baur 2013, "The autumn effect of gold", https://opus.lib.uts.edu.au/handle/10453/23416. Potrykus & Augustynowicz 2024, *IJME*, https://doaj.org/article/020bbdef98bc4868b2323d2b260fd09a: the effect reversed into a winter effect. arXiv 2003.11027.
- R13 Rösch, Schmidbauer, Jiang 2013, EcoMod working paper, https://ecomod.net/system/files/gold_and_china_ecomod2013.pdf.
- R14 Fan, Fernandez-Perez, Fuertes, Miffre, "Speculative pressure", summary at https://www.cxoadvisory.com/?p=31911. Venue *(unverified)*. Gross.
- R15 Nguyen, Prokopczuk, Wese Simen 2017 (gold VRP and jump tail premium, in and out of sample); Prokopczuk & Wese Simen 2013; Xu & Roh (GLD VRP); BIS WP 619, https://www.bis.org/publ/work619.pdf. Fernandez-Perez, Frijns, Fuertes, Miffre (commodity skewness). Venues *(unverified)*. Gross.
- R16 Cont, Kukanov, Stoikov 2014, *J. Fin. Econometrics* 12(1) 47–88, https://arxiv.org/abs/1011.6402. NYSE stocks.
- R17 Lee & Mykland 2008, *RFS* 21(6), https://www.scheller.gatech.edu/directory/research/finance/lee/pdf/leemykland08.pdf.
- R18 Lou, Polk, Skouras 2019, *JFE*, https://eprints.lse.ac.uk/87481/. Della Corte, Kosowski, Wang 2015, summary at https://www.cxoadvisory.com/technical-trading/overnightintraday-return-reversal-trading/ (about 100% daily turnover).
- R19 No peer-reviewed SMC/ICT test found. The secondary review https://thortradecopier.com/blog/does-ict-smart-money-concepts-work cites Mahadzva 2026 (SSRN) *(unverified; we could not locate it)* and an FVG base-rate simulation.
- R20 LBMA *Alchemist* 83, https://www.lbma.org.uk/alchemist/issue-83/links-between-the-chinese-and-international-gold-prices.
- R21 GLD flow thesis (Taiwan), https://ndltd.ncl.edu.tw/handle/79415745161990386644 *(unverified details)*.
- R22 Baur & Lucey 2010; Baur & McDermott 2010 (see SoA).
- R23 Erb & Harvey 2013; real-yield break (see SoA B1).
- R24 Caldara & Iacoviello 2022; Baur & Smales 2020 (see SoA).
- R25 Parkinson 1980; Garman & Klass 1980; Rogers & Satchell 1991; Yang & Zhang 2000; Corsi 2009 (HAR); Andersen & Bollerslev 1997 (intraday periodicity); Barndorff-Nielsen & Shephard 2004 *(all unverified this session; standard results)*.
- R26 Lo & MacKinlay 1988, *RFS* *(unverified)*.
- R27 arXiv 1510.08615 "Gold, currencies and market efficiency"; Cheung & Lai 1993 *(unverified)*.
- R28 DTU thesis on HMM regime allocation, https://www2.imm.dtu.dk/pubdb/pubs/6808-full.html (equities, net of costs); Hamilton 1989 *(unverified)*.
- R29 Marshall, Young, Rose 2006, *JBF*, candlesticks on DJIA stocks *(unverified)*.
- R30 Lo, Mamaysky, Wang 2000, *JF* *(unverified)*.
- R31 López de Prado 2018, *AFML*.
- R32 Kelly, Malamud, Zhou 2024, *JF*, "The virtue of complexity in return prediction" *(unverified)*.
- R33 Easley, López de Prado, O'Hara 2012, *RFS*; Andersen & Bondarenko 2014 critique *(unverified)*.
- R34 Amihud 2002, *JFM* *(unverified)*.
- R35 Meinshausen & Bühlmann 2010, *JRSS-B* *(unverified)*; Bailey & López de Prado 2014 DSR (see SoA).
- R36 Intraday gold/silver futures and macro releases, https://scholarscommons.fgcu.edu/esploro/outputs/journalArticle/Do-macroeconomics-news-releases-affect-gold/99384088373606570; Cai, Cheung, Wong 2001 (see SoA).
- R37 "Are gold bugs coherent?", *Applied Economics Letters* 2017, https://research.ucc.ie/en/publications/are-gold-bugs-coherent/.
- R39 Tick vs real FX volume study, https://c.mql5.com/forextsd/forum/145/fxvolume_tick_volume_vs_real_volume_study_1.pdf (secondary; the ρ ≈ 0.9 figure is *unverified*).
- R40 Chronos (Ansari et al. 2024), TimesFM (Das et al. 2024) *(unverified)*.
- R41 Adams & MacKay 2007, BOCPD *(unverified)*.
- R44 Blose & Gondhalekar 2013 (see SoA).
