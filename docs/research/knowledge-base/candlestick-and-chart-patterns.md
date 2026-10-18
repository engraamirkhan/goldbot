# Candlestick and chart patterns

Part of the [knowledge base](README.md). Format, grades and markers are defined there.

**Scope.** Single- and multi-candle patterns (pin bars, hammers, shooting stars, engulfing, dark cloud and piercing,
dojis, harami and inside bars, marubozu, stars, three soldiers/crows, false-break "fakey" bars, wicked bars) and chart
patterns (triangles, head and shoulders, double/triple tops and bottoms, channels). Our implementation: `f_candles`
(`goldbot/features/structure.py`), `candle_class` (replica, engulfing and MA-traverse classes).

## Summary

- **Candlestick patterns: the evidence is consistently negative on liquid markets once costs and data snooping are
  handled.** Daily DJIA stocks (Marshall, Young, Rose 2006), 5-minute DJIA stocks (Duvinage et al.), and currency
  futures 1978-2013 (UTS working paper) all find little or nothing net. Grade A against as standalone rules.
- **Chart patterns carry *some* information, but not enough to trade alone.** Automated pattern detection finds
  incremental information in US stocks (Lo, Mamaysky, Wang 2000), head-and-shoulders predicts excess returns but not
  as a standalone strategy (Savin et al. 2007), and in FX it earns profits that simpler rules beat (Chang & Osler 1999).
- **The course itself ranks patterns** (pin bar at a key level highest, then engulfing, doji at a level, head and
  shoulders, double tops) and insists they only count *at a level* and *with the trend*. That conditioning is the
  testable part; the patterns themselves are mostly a way of describing a rejection wick or a strong close.
- **On our data:** candlesticks are retired as rules (R-09) and kept as features.

## Claims

| # | Claim | Course baseline (paraphrased) | External evidence | Grade | Our data |
|---|---|---|---|---|---|
| C1 | Single candlestick patterns predict the next move | A quick reference of a dozen patterns with exact shape rules (shadow ≥ 2× body for a hammer, close > 50% into the prior body for dark cloud/piercing, etc.); confirmation only | DJIA stocks 1992-2002: no reliable predictive power; bootstrap finds no evidence that any of 28 signals predicts (Marshall, Young, Rose 2006, *JBF* *(volume/pages unverified)*; thesis https://mro.massey.ac.nz/items/edc6c1cf-63d9-4c94-a13d-92ffe1a53481/full; summary https://www.cxoadvisory.com/technical-trading/candlesticks-fiddlesticks/). 5-minute DJIA stocks: about a third of rules beat buy-and-hold gross at Bonferroni, but none survives snooping correction and costs (Duvinage, Mazza, Petitjean, https://dial.uclouvain.be/pr/boreal/en/object/boreal%3A136070/datastream/PDF_02/view; venue/year *(unverified)*). GBP, CHF, JPY futures 1978-2013: very few rules have significant predictive value (UTS QFR rp362, https://ideas.repec.org/p/uts/rpaper/362.html) | **A against** (three independent samples incl. intraday and FX) | **retired as rules:** R-09. **feature:** `f_candles`, `candle_class` |
| C2 | A pin bar (long wick ≥ about 65% of the range, small body) that pierces a level and closes back ("off the line") is the most reliable reversal signal | Highest-ranked signal; enter on the 1h or 4h close, stop beyond the wick, target the next level, minimum 2:1; avoid pins far from levels or against the trend without a retrace | No test of pin bars at levels found. The wick-through-level-and-close-back shape is the same event as a liquidity sweep (structure file S6), whose mechanism is order clustering (Osler 2003, 2005) | **D** for the pattern; **B** for the mechanism | **feature:** `market_structure` sweep columns. **pending: pa-claims** (pin bar at level base rates) |
| C3 | Engulfing at the end of a prolonged trend signals reversal | Second-ranked; must engulf the whole prior body and open beyond the prior close | Engulfing patterns on the S&P 500: a statistical analysis in *Quarterly Review of Economics and Finance* 79 (2021) 221-244 (https://ideas.repec.org/a/eee/quaeco/v79y2021icp221-244.html; results *(unverified)*). Otherwise as C1 | **C** | **feature:** `candle_class` engulfing |
| C4 | Inside bars mark indecision: continuation in trends, coiling in ranges; trade the break of the mother bar, best on 4h/1d with the daily trend; pin + inside bar at a level is a low-risk reversal | Exact entry on a break of the mother bar, stop beyond the other side | None found for inside bars. Volatility clustering predicts that quiet bars are followed by quiet bars, the opposite of "coiling" (HAR, Corsi 2009 *(unverified)*) | **D** | New hypothesis **KB-7** (compression vs expansion, as a range-forecast feature) |
| C5 | A false break of an inside bar against the trend that closes back inside (a "fakey") is a high-probability with-trend entry | With-trend version preferred; counter-trend only at obvious levels | None found. Same mechanism as a sweep (C2) | **D** | as C2 |
| C6 | Triangles: symmetrical can break either way with a slight trend bias; false breaks are common before the true break; target 2× the height; prefer the retest | Trade the 4h triangle on the retest; stop beyond the line; target 2× height or the next level | Automated detection of several patterns (incl. triangles, rectangles, head and shoulders, double tops/bottoms) on US stocks 1962-1996: some provide incremental information, especially on Nasdaq (Lo, Mamaysky, Wang 2000, *JF* 55(4) 1705-1765, https://www.nber.org/papers/w7613; gross; conditional return distributions, not trading profits) | **B** (information, gross, US stocks); **none found** for gold or targets | not checked |
| C7 | Descending triangle on gold (flat support, falling highs) after a decline is a continuation short; wait for a close below support, short the retest; a break above the falling line cancels it | Gold-specific note: at least two, better three, lows at a similar price; wick sweeps of support do not count | As C6; no gold-specific study found | **D** | **pending: pa-claims** if it counts triangles |
| C8 | Head and shoulders in an up-trend is a bearish reversal; enter on the neckline break or its retest; target the head-to-neckline distance | Invalid if the right shoulder is above the head | FX: profitable on daily dollar rates 1973-1994 under a bootstrap test, but inefficient because simpler rules did better (Chang & Osler 1999, *Economic Journal* 109(458) 636-661, https://ideas.repec.org/a/ecj/econjl/v109y1999i458p636-61.html; gross). US stocks 1990-1999: little support for a standalone strategy, strong evidence of predicted excess returns (5-7% a year risk-adjusted), partly negative momentum (Savin, Weller, Zvingelis 2007, *J. Financial Econometrics* 5(2) 243-265, https://ideas.repec.org/a/oup/jfinec/v5yi2p243-265.html) | **B** (two markets, gross; dominated by simpler rules) | not checked |
| C9 | Double and triple tops/bottoms at similar prices are reversals; buy the retest of the second bottom; target the neckline | Tolerance "very close, not exact"; longer timeframes stronger; triples rarer and stronger | Lo, Mamaysky, Wang 2000 include double tops/bottoms (C6). Practitioner pattern statistics (Bulkowski, *Encyclopedia of Chart Patterns*, Wiley *(unverified)*) report success rates without data-snooping control | **B** information, gross; **D** for the practitioner rates | **feature:** `eqh`/`eql` share the tolerance idea. New hypothesis **KB-5** |
| C10 | Wicked bars (a meaningful wick, not a full pin) signal rejection; longer wicks protruding from neighbours are stronger | Observe much, trade little: only with confluence at S/R, in line with the daily chart | None found | **D** | not checked |
| C11 | The more reliable the signal, the less extra confluence it needs | Ranking rule across signals | None found; see the multi-timeframe file for the cost of adding conditions | **D** | not checked |

## What this means for goldbot

1. **Do not spend trials on candle or chart pattern rules.** Three independent negative samples for candlesticks, and
   chart patterns that are dominated by simpler rules, close this topic as rules (R-09 stands).
2. **The useful content is the wick-through-level-and-close-back event**, which is the same object as a liquidity
   sweep. It is already a feature and gets a better test through the order-clustering framing (KB-2).
3. **Inside bars and triangles are compression.** If they matter, they matter for *how much* price will move, not
   which way: test them as range-forecast inputs (KB-7), where a wrong sign costs nothing.

## Open checks

- Fetch Marshall, Young, Rose 2006 *JBF* record and the Duvinage et al. venue; verify the 2021 *QREF* engulfing paper.
