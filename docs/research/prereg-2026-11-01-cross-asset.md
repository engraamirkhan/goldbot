# Pre-registration: cross-asset lead-lag on 1m, 2010-2017 (macro researcher, 2026-11-01)

Frozen by commit before any 2010-2017 outcome is computed (the lead-asset data was still downloading when this was
committed). Results: `cross-asset-screen-2026-11.md`. Code: `docs/research/candidates/cross_asset/cross_asset_screen.py`. Data fetch:
`scripts/cross_asset_fetch.py` (Dukascopy bi5 bid m1, local `raw/xasset/`, never committed).

## Why this line, and what it is not

Every gold-only price, macro, positioning, event and convex-payoff line has failed net of cost (~3,020 looks, 0
survivors; `hypotheses.md`, `growth-screens-2026-11.md`). The one input never tested is **other markets' prices
inside the minute-to-quarter-hour horizon**. Mechanism: gold's price is set in part off the USD and the precious-metal
complex; if dealers and algos reprice XAUUSD with a lag of minutes after a USD or silver move, the lead asset's last 5
minutes predicts gold's next 15.

Evidence and priors (stated before the data):
- Price discovery across gold venues and between gold and related markets is fast: COMEX futures lead spot/OTC by
  seconds to a few minutes (Hauptfleisch, Putnins, Lucey 2016, *Journal of Futures Markets* 36(6), "Who sets the price
  of gold? London or New York", 2008-2013 intraday, gross, no costs; single paper). *(citation details unverified)*
- Gold-silver and gold-USD intraday co-movement is strong and largely contemporaneous at 5m (practitioner consensus;
  no peer-reviewed 1-15m net-of-cost lead-lag result for spot XAUUSD found). *(unverified; no source claimed)*
- FX cross-rate lead-lag at 1m is mostly non-synchronous trading and is arbitraged within seconds on EBS/Reuters
  (Epps effect literature; e.g. Epps 1979, *JASA* 74(366); Hayashi and Yoshida 2005, *Bernoulli* 11(2)). Gross only.

Prior: low. A lag long enough to trade on 1m bars after a $0.25/oz round trip would be a market inefficiency that
HFT should have removed by 2010. That is exactly why it is worth one cheap look: it is the one piece of untested
information, and a clean FAIL closes it.

Not a re-test: R-01 (short-horizon gold mean reversion) is retired. To keep this from being R-01 in disguise, a signal
needs the **lead** to have moved, not only gold (rule below: the predicted move is at least 2 residual sd and gold has
done at most half of it).

## Common (frozen)

| Item | Frozen value |
|---|---|
| Window | 2010-01-01..2017-12-31 only. Gold: `raw/xauusd_1m_dukascopy_2010..2017.parquet` (main checkout, read-only), 1m mid = (bid + ask) / 2. Leads: XAGUSD, EURUSD, USDJPY bid 1m from `raw/xasset/`. The loader asserts no 2018+ bar. Confirm 2018-01..2025-09 and holdout 2025-10..2026-09 untouched |
| Point in time | A 1m bar opening at t is available at t + 1m. Lead bars reach the gold grid only through `goldbot.data.store.asof_join` on `available_utc = ts + 1m` with zero tolerance (a lead minute that did not print is missing, never forward-filled). Decision at the close of gold minute t; entry at the open of gold minute t + 1 |
| Returns | r_x = ln(close_t / close_{t-5m}) per instrument from exact minute bars; any missing bar -> no signal at t. USD composite r_usd = (r_USDJPY - r_EURUSD) / 2 |
| Decision minutes | gold bars opening 07:00..19:44 UTC, Monday-Friday (London + New York, all four markets liquid; the 15m hold ends by 20:00) |
| Beta and sd | per UTC day D, OLS without intercept of r_gold on the variant's lead regressor(s), on the 5m returns at minutes with minute % 5 == 0 in the decision window, over the previous 20 trading days (strictly before D; first 20 days skipped). sigma = residual sd of that fit |
| Signal | pred_t = beta . r_lead,t. Trade when abs(pred_t) >= 2 sigma AND r_gold,t / pred_t <= 0.5 (gold has done at most half of the predicted move, including the wrong way). Side = sign(pred_t) |
| Trade | one position at a time per variant (signals during an open trade are ignored). Stop 1.5 x ATR15 (ATR(14), simple mean of true range of 15m mid bars, last completed before the decision) = 1 R. Time exit at the open of the gold minute 15 minutes after entry. Stop checked from the entry minute on; a gapped stop fills at that minute's open (worse) |
| Cost | `config/cost_of_record.yaml` **cor-2026-10-31.2**, ICM cell by entry session (london 07:00-12:30, newyork >= 12:30 UTC): **1.5 x round_trip_usd** per trade plus **1.5 x overshoot_usd** on a stop exit. No swap (intraday) |
| R | net USD/oz / stop distance |
| Random twin | 4 per real trade, same UTC day, entry at a uniform random decision minute of that day (with an ATR available), fair-coin side, identical stop, time exit and cost. Seed sha256(variant, day, trade index, i) |

## Variants (K +3)

| ID | Lead regressor(s) | Mechanism |
|---|---|---|
| X1 | r_XAGUSD | silver leads gold (precious-metal complex, silver's larger speculative flow) |
| X2 | r_usd (USDJPY up, EURUSD down) | USD repricing passes to gold with a lag (USD-denominated spot) |
| X3 | r_XAGUSD, r_EURUSD, r_USDJPY jointly (3-regressor OLS) | the combined cross-asset fair value |

## Pass bar (growth-sprint section 2) and reading rule (written before data)

Per variant, all three:
- (a) net mean R > 0 with day-block bootstrap (10,000 resamples, seed 20261101) one-sided p, **Bonferroni x3 < 0.10**
  (equivalently the 96.7% one-sided lower bound > 0; Holm over 3 can only be looser, a Holm-only pass is reported, not
  claimed);
- (b) real minus mean-twin R per day, same bootstrap, Bonferroni x3 < 0.10;
- (c) >= 5 of 8 calendar years with net mean R > 0.

Reported for each variant regardless: n, trades/week, gross and net mean R and $/oz, hit rate, share of stop exits,
twin mean, year table, and the gross 15m forward return in $/oz after a signal. No horizon, threshold, session or
beta window is varied after the data is seen; no other variant is computed.

Reading: a pass earns one confirm proposal on 2018-2025-09 at the program deflated bar (t about 4.0 at registry K,
DSR >= 0.95, >= 200 trades); nothing is tuned on this read. If **all three FAIL**, cross-asset 1-15m lead-lag in spot
gold on 1m bars is retired: it returns only with tick data (sub-minute lags) or a new lead instrument with a stated
mechanism. If the gross edge is positive but costs eat it, that is reported as a gross finding with its size against
the $0.25/oz cost, not as an edge.

Trial cost: **K +3** (explore screen, no registry trials). Program K ~3,020 (brief, 2026-11-01) -> ~3,023. Gates
touched: none. Owner-only items: none (no instrument, budget, sizing or live change; data stays local).
