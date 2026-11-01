# Cross-asset lead-lag screen X1-X3 on 2010-2017: 0 of 3 pass (macro researcher, 2026-11-01)

Question: does gold reprice with a lag of minutes to silver and the USD, enough to pay a $0.25/oz round trip?
Pre-registration `prereg-2026-11-01-cross-asset.md`, frozen by commit `f2daf9b1` before any data, with two amendments
committed before any gold-versus-lead number was computed (`a1876e41` data source, `4851c5f1` clock fix). Code
`docs/research/candidates/cross_asset/cross_asset_screen.py`; fetcher `scripts/cross_asset_fetch.py`; tests
`tests/research/test_cross_asset_screen.py`. Data is local only (`raw/xasset/`, git-ignored). The window is 2010-2017
only; 2018+ and the holdout were not read (the loaders assert this). There was one look per variant, run as 3
parallel processes. Cost is `cor-2026-10-31.2`, ICM, x1.5.

## Result

Rule: when the lead's 5m move implies a gold move of at least 2 residual sd (a rolling 20-day beta, point in time) and
gold has done at most half of it, trade gold that way. Entry is at decision + 1 min. Exit is a 15m time exit, with a
1.5 x ATR15 stop. Each trade has a 4-twin random control (same day, random minute and side, same mechanics).

| Variant | n | trades/wk | gross $/oz | net $/oz | gross R | net mean R (90% LB) | twin R | real - twin per trade R (Bonf-3 p) | yrs + | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| X1 silver leads | 10,869 | 26.0 | -0.009 | -0.270 | -0.002 | **-0.097** (-0.107) | -0.098 | +0.002 (1.00) | 0/8 | FAIL |
| X2 USD leads (USDJPY, -EURUSD) | 1,505 | 3.6 | -0.003 | -0.270 | +0.009 | **-0.103** (-0.134) | -0.113 | +0.010 (0.97) | 0/8 | FAIL |
| X3 joint fair value (XAG, EUR, JPY) | 11,382 | 27.3 | +0.034 | -0.226 | +0.015 | **-0.081** (-0.091) | -0.099 | +0.019 (**0.004**) | 0/8 | FAIL |

Mean risk is about $3.6/oz, so the 1.5 x cost of about $0.26/oz is 0.07 R. Gross hit rates are 44-48%. Stops
trigger on 7-11% of trades.

## Reading

1. **No variant clears (a) or (c).** Every year is net negative for every variant: 0 of 24 variant-years are
   positive. The bar fails on two of three legs.
2. **X3 has a real but tiny gross lead-lag.** It beats its random twin by +0.019 R a trade (Bonferroni-3 p 0.004),
   which is +$0.03/oz gross against about $0.004 for the twin. Gold does catch up to the joint silver/FX fair value a
   little, even from the second minute. But the effect is **about 1/8 of the cost**: closing the gap needs a round
   trip under $0.03/oz, less than half of ICM's commission alone ($0.08). No execution we can buy reaches that. This is
   a gross finding, not an edge: it cannot be traded at our costs.
3. **Silver alone (X1) and the USD alone (X2) show nothing**, gross or against the twin. The small X3 effect is the
   joint residual, consistent with gold being priced off the complex within a minute and leaving only crumbs.
4. **The measurement nearly produced a false positive.** A wrong clock alone would have passed. HistData's stamps are
   New York time with DST, not the documented fixed EST. A sub-minute cross-feed offset also remains: HistData minute t
   correlates 0.08-0.25 with Dukascopy minute t+1. Mixing feeds, as the first prereg did, would have turned that
   offset into a lead-lag "edge". Amendment 2 moved gold to the same feed and delayed entry by a minute. A planted
   3-minute lead on synthetic data was detected (+1.28 R, pass), and random leads gave 0 signals. So the machinery
   has power, and the null result is not a code artefact.

## Verdict and what it retires

**FAIL, 0 of 3.** Cross-asset 1-15m lead-lag (silver, EURUSD, USDJPY, jointly or alone) on 1m bars is **retired** as a
standalone rule for spot gold at our costs. It returns only with sub-minute (tick) data and a cost below about
$0.05/oz round trip. No such cost is available to a retail CFD account, and changing broker is an owner decision.
The X3 gross residual (+0.019 R over random) may still be worth one column in a future pooled feature screen, as a
filter on entries that already pay for themselves. It is not a reason to trade.

## Data notes (for the next researcher)

- Dukascopy's bi5 archive throttled this host after about 28 instrument-months (6-75 s per day file, then dropped
  connections). HistData's free M1 ASCII (one POST per pair-year, about 30 s each) was the workable route.
- HistData stamps: `America/New_York` local with DST (`scripts/cross_asset_fetch.py histdata_to_utc`). Lead coverage
  on decision minutes is 82-92%. Missing lead minutes never fire, and nothing is forward-filled.
- Never mix 1m feeds from different vendors in a lead-lag or cross-asset study without the same-instrument
  offset check (`cross_asset_screen.py --clock-check`).

## Multiple testing

K +3 (X1-X3; explore screen, 0 registry trials). Program K ~3,020 (brief) -> **~3,023**. The clock checks and the
planted-lead sanity run computed no gold-versus-lead outcome on real data and add 0.
