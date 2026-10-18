# Multi-timeframe analysis and confluence

Part of the [knowledge base](README.md). Format, grades and markers are defined there.

**Scope.** Using higher timeframes as a map and lower ones as a trigger; "the intraday trade must agree with the daily
chart"; TLS (trend-level-signal) as a three-part gate; confluence checklists and counts. Our implementation: the
multi-timeframe feature join (`goldbot/features/mtf.py`), the owner-style study `owner-style-mtf-2026-10-18.md`, and
H-31 (1h tsmom aligned with the daily SMA200 side).

## Summary

- **Trend agreement across horizons is the one part with academic support**, and it is support for *slow* trend:
  combining several look-backs (1, 3, 12 months) is standard in trend following (Hurst, Ooi, Pedersen 2017), and
  blending slow and fast signals helps at turning points (Garg, Goulding, Harvey, Mazzoleni 2021, *FAJ* 77(4)
  *(unverified)*). No peer-reviewed study tests the trader's
  "daily map, 1h trigger" alignment as such.
- **Confluence has a statistical cost the notes do not mention.** Each added condition is another degree of freedom
  (data snooping) and cuts the event count. Our owner-style multi-timeframe rules found 0-18 events in 15 years.
- **TLS is a sound *process* (no trade without a reason on each axis)**, but its parts each carry the weak evidence of
  their own topic file. Their product is not stronger than its strongest part unless the parts are independent and
  each has an edge.

## Claims

| # | Claim | Course baseline (paraphrased) | External evidence | Grade | Our data |
|---|---|---|---|---|---|
| T1 | Higher timeframes (daily, 4h) are for planning; 1h for entries and exits; 15m only to refine an entry, never to find setups | Daily/4h = map, 1h = trigger on the hourly close, 15m = scalps and precision only | No peer-reviewed test of this division of labour found; practitioner material only (searches 2026-10-18 returned indicator scripts and blogs). The closest academic idea: trend signals at several horizons combined (Hurst, Ooi, Pedersen 2017, *JPM* 44(1), https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing; 67 markets 1880-2016, net of estimated costs) | **D** for the trader version; **A** for multi-horizon slow trend | **study:** `owner-style-mtf-2026-10-18.md` |
| T2 | The intraday trade must agree with the daily chart's direction | The single most important intraday rule | Moving-average and breakout signals are weighted sums of past returns, so "agreeing with the daily" is a slow momentum filter (Levine & Pedersen 2016, *FAJ* 72(4), "Which trend is your friend?" *(unverified)*). Slow trend in gold: positive but weak as a single asset (Huang et al. 2020, *JFE*, in `state-of-the-art.md` A1). Gold's 2010-2025 upward drift makes a long-side filter look good by construction; a drift control is needed | **B** (slow-momentum filter); **C** for gold alone | **pre-registered:** H-31 (1h tsmom with side = sign(daily close − SMA200), with a drift control on the long side). **routed:** H-33 (structure trend as filter) into H-02 |
| T3 | Trade only when Trend, Level and Signal all line up (TLS); any one missing means no trade | Trend from 4h/daily swings and EMAs; level from boxes, horizontal S/R, EMA20, 50% retracement, event areas, broken-level retests; signal = a clear candle on the 1h close | Each component's evidence is in its own file: trend (A slow / B against intraday), levels (B, gross, FX), candle signals (A against). No test of the conjunction found | **D** for the conjunction | **withdrawn:** H-34 (4h+1h trend, 15m CHoCH, discount zone, FVG: 0-16 candidates at L0-L3) |
| T4 | More confluence factors mean a better trade; six aligned factors make a near-ideal setup | A list of about a dozen factors (4h trend, active box, 4h/daily S/R, EMA20 at the level, 50% retracement, pin or engulfing, mfi extreme, broken-level retest, event area, declining volume, closing off the line, trendline touch) | Every added filter is another choice in a search over rules; data-snooping bias grows with the size of the search (Sullivan, Timmermann, White 1999, *JF* 54(5); White 2000 Reality Check, *Econometrica* 68(5) *(unverified)*). The t-hurdle for a new factor should be about 3, not 2 (Harvey, Liu, Zhu 2016, *RFS* 29(1), in `state-of-the-art.md` A8) | **A** that searches over combinations inflate false discoveries | **study:** owner-style mtf counts (0-18 events); **withdrawn:** H-34, H-35 |
| T5 | Entering on a lower timeframe gives a smaller stop and a better entry than trading the daily alone | Smaller-timeframe entries mean smaller stops | Arithmetically true, but cost in R scales with 1/stop: at the cost prior a trade needs about 0.71 $/oz ÷ stop $ in gross R to break even, and on our data gross R is flat across stop widths (`tight-stop-entry-study-2026-10-18.md`). A smaller stop therefore *raises* the required edge | **our data against** | **study:** tight-stop study (net R falls from −0.11 to −0.45 R as stops tighten) |
| T6 | Score confluence instead of gating on it | (Implied by the factor list) | Scoring factors and letting a model weigh them is what fold-internal feature selection does, at a known multiple-testing cost (stability selection: Meinshausen & Bühlmann 2010 *(unverified)*) | **B** method | **design:** this is O-4/H-02 (groups screened once, survivors only) |

## What this means for goldbot

1. **Turn checklists into features, not gates.** A hard AND of six conditions cannot reach 1,000 events. Feeding the
   same conditions to the discovery screen as columns (D-TVR, D-OMTF) tests them all for one trial and records K_eff.
2. **Test "agree with the daily" once, cleanly** (H-31), with a drift control, before any multi-timeframe rule. If it
   fails, the core of the owner's multi-timeframe rule fails with it.
3. **Lower-timeframe entries are not free.** They shrink the stop and raise cost in R one for one; only a larger gross
   edge pays for them.

## Open checks

- Verify Levine & Pedersen 2016 and White 2000; consider fetching Garg et al. 2021 (*FAJ*) for the slow/fast blend.
