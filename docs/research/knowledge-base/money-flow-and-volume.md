# Money flow and volume

Part of the [knowledge base](README.md). Format, grades and markers are defined there.

**Scope.** The mfi / money flow oscillator (0-100, typical price × volume over n bars), its zones, extremes,
divergences and crosses; candle volume classes (high-volume and low-volume candles); the "volume veto" at levels; and
what CFD tick counts can and cannot stand in for. Our implementation: `money_flow` in `goldbot/features/smc_replica.py`
(n = 12; events at crosses of 20 and 80), `candle_class` (same file), tick counts in the 1m bars.

**First fact to remember.** Our XAUUSD bars carry **tick counts, not traded volume** (DESIGN, data section). On our
data the mfi is a *tick-flow* index. Every volume claim below is about traded volume unless it says otherwise.

## Summary

- **Volume does matter in the academic record, but not the way the oscillator uses it.** High-volume moves driven by
  liquidity needs tend to reverse; moves driven by informed or speculative trading tend to continue (Campbell,
  Grossman, Wang 1993; Llorente et al. 2002). Which kind of flow it is matters more than the level of a 0-100 line.
- **No independent, peer-reviewed test of the money flow index itself was found.** The best is a conference paper on
  S&P 500 stocks whose default settings did not work and whose tuned settings carry in-sample bias.
- **Gold's activity has a strong time-of-day shape** (Batten et al. 2017). A raw "volume above 150% of the last 10
  bars" fires mostly at the London and New York opens, which are also the high-volatility hours. That confounds every
  volume rule on intraday bars.
- **On our data:** short-horizon oscillator fades have no gross edge (R-01). mfi cross rules were not trialled, by
  decision (A7). The mfi is in the replica feature group for O-4.

## Claims

| # | Claim | Course baseline (paraphrased) | External evidence | Grade | Our data |
|---|---|---|---|---|---|
| M1 | Volume leads price, so a volume-weighted oscillator leads a price-only one | mfi is volume-based and so treated as leading; use it with price action, never alone | Theory: volume carries information about the quality of signals that prices alone do not (Blume, Easley, O'Hara 1994, *JF* 49(1) *(unverified)*). Empirical: the sign of the volume-return link depends on who trades (Llorente, Michaely, Saar, Wang 2002, *RFS* 15(4) 1005-1047, https://www.johnhcochrane.com/s/llorente_michaely_saar_wang_volume_rfs.pdf; NYSE/AMEX stocks, daily, gross). "Leading" is not established for any oscillator | **B** that volume is informative; **D** that mfi leads | not checked |
| M2 | mfi zones: above 60 bullish, below 40 bearish, 40-60 ranging; a pullback that holds above 40 keeps an up-trend alive | Zones as stated; circles at 20 and 80 mark extremes | No test found | **D** | **feature:** `mfi` (replica). **pending: pa-claims** if it covers zone persistence |
| M3 | Extremes: above 80 is overbought, below 20 oversold; entering an extreme is an exit signal for an open trade | Extremes as exits; also as "risk on that side" filters | Oscillator overbought/oversold fades on single assets have no robust evidence after data-snooping control; see Park & Irwin 2007 survey (*J. Economic Surveys* 21(4) 786-826, https://ideas.repec.org/a/bla/jecsur/v21y2007i4p786-826.html): 56 of 95 studies positive but with snooping and cost caveats. An MFI study on large S&P 500 stocks found recommended parameters did not give the best results and optimised ones beat buy-and-hold (Marek & Marková 2020, APLIMAT, https://otik.uk.zcu.cz/handle/11025/37043; gross; tuned in-sample) | **C** (tuned, gross); **A against** generic oscillator fades net | **tested (related):** R-01 (RSI/Bollinger/VWAP fades, no gross edge). **feature:** `mfi_os_cross`, `mfi_ob_cross` |
| M4 | A second visit to an extreme (a "double circle") that falls back into range is a stronger reversal signal | Two extreme readings in a row, the second followed by a return inside 80 (or 20), sets up a fade | None found | **D** | not checked |
| M5 | Price/mfi divergence (price makes a new extreme, mfi does not) warns of a large reversal | Divergence = possible large reversal | No peer-reviewed test of mfi divergence found. Divergence rules in general sit inside the snooping problem of M3 | **D** | not checked |
| M6 | An mfi cross of 50 in the direction of the move confirms the move | The 50 cross is a signal in the mfi's direction | None found. A 50 cross of a bounded volume-weighted momentum line is a short-horizon momentum signal; intraday momentum on gold has no gross edge on our data | **D**; related evidence against | **tested (related):** R-05 (intraday momentum), R-06 (tsmom 1h/4h net negative) |
| M7 | mfi is best combined with S/R boxes or structure: box = where, mfi = which side carries the risk | Neither alone is a signal; the break or rejection must agree with the extreme (break down from overbought, break up from oversold) | No test of the combination found. Combining two weak signals multiplies the search space (see the multi-timeframe file) | **D** | **withdrawn:** H-35 (1h box break + 1h mfi extreme + 15m CHoCH: 0-18 candidates). Box/mfi rules not trialled (A7) |
| M8 | A candle with volume above 150% of the recent average is a "high-volume" candle; below 50% is "low volume", and a move on low volume is not sustainable | The box and structure tools colour candles by these thresholds (10-bar average in the notes; our replica fit uses 20 bars) | High volume predicts reversal for liquidity-driven moves (Campbell, Grossman, Wang 1993, *QJE* 108(4) 905-939, https://ideas.repec.org/p/hrv/faseco/3128710.html; US stock indices, daily) and continuation for informed moves (Llorente et al. 2002). A high-volume return premium in stocks: Gervais, Kaniel, Mingelgrin 2001, *JF* 56(3) *(unverified)*. Intraday, gold volume follows an n-shaped hourly pattern with a peak in the European-US overlap (Batten, Lucey, McGroarty, Peat, Urquhart 2017, *PLoS ONE* 12(4) e0174232, https://pmc.ncbi.nlm.nih.gov/articles/PMC5407636/; 5-minute data, May 2000-Apr 2015), so a raw 10- or 20-bar threshold mostly flags session opens | **B** that volume conditions continuation vs reversal (equities, daily); **none found** for the thresholds | **feature:** `candle_class` (replica). New hypothesis **KB-3** (time-of-day-normalised activity) |
| M9 | Volume veto: do not fade a level when the approach comes with high volume and large candles | Stand aside or trade with the move | As M8: the direction depends on whether the flow is informed. Unclear for tick counts | **B** mechanism; **none found** for gold | New hypothesis **KB-3** |
| M10 | Tick counts are a good proxy for traded volume | Not discussed; the notes assume chart volume is real volume | A practitioner study reported about 0.9 correlation between FX tick volume and actual traded volume (Marney 2011, summarised at https://c.mql5.com/forextsd/forum/145/fxvolume_tick_volume_vs_real_volume_study_1.pdf *(figure unverified)*). An informal comparison found broker tick volume largely uncorrelated with CME euro futures volume (https://www.earnforex.com/blog/do-you-believe-in-forex-tick-volume/). Tick counts differ by broker feed | **D** | DESIGN: CFD volume is tick count and is never labelled otherwise. Real COMEX volume (paid, Databento) deferred until a feature needs it: **owner decision** (paid data) |
| M11 | A spinning top on low volume precedes consolidation; on high volume it precedes a breakout | Candle-plus-volume reading from the pattern lessons | None found | **D** | not checked |

## What this means for goldbot

1. **Treat the replica mfi as a tick-flow feature, not a volume signal.** It enters O-4 only inside the D-TVR group.
2. **Normalise activity by time of day before asking any volume question** (KB-3). Otherwise "high volume" is a proxy
   for "London or New York just opened", which is already in `hour_utc` and `session_id`.
3. **The interesting volume question is informed vs uninformed flow**, which the literature ties to continuation vs
   reversal. Our nearest proxies: activity around scheduled releases (informed, H-18/H-10) vs activity in quiet hours
   (liquidity-driven). That split is a feature design, not a rule.
4. **Real traded volume would need paid COMEX data.** Not recommended until a tick-based activity group survives O-4;
   if one does, the owner decides on the spend.

## Open checks

- Verify Blume, Easley, O'Hara 1994 and Gervais, Kaniel, Mingelgrin 2001.
- Broker vs Dukascopy tick-count correlation per hour once `costs-v1` has 20 days of broker ticks (CB-0 step 6).
