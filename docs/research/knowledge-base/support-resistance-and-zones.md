# Support, resistance and zones

Part of the [knowledge base](README.md). Format, grades and markers are defined there.

**Scope.** Horizontal levels and zones; sr_channels ("boxes"): automatic S/R zones built from pivots; the break trade
and the rejection trade at a box; role reversal (broken support becomes resistance) and retests; moving averages as
dynamic support; round numbers; floor pivots; prior day/week extremes; channels; level-to-level targets. Our
implementation: `sr_channel_breaks` (`smc_replica`), confirmed swing levels (`goldbot/features/structure.py`
`f_levels`), round numbers (`goldbot/features/survey.py` `f_round_numbers`), session/day/week levels
(`goldbot/features/trader.py` `f_session_zones`), floor pivots (replica).

## Summary

- **S/R levels do carry information, in one documented way:** levels that market participants publish or watch
  predict intraday *trend interruptions* (Osler 2000), and round numbers act as barriers in gold (Aggarwal & Lucey
  2007). Grade B, gross, mostly FX.
- **Trading on levels as standalone rules has not survived data-snooping control and costs** (Sullivan et al. 1999;
  Bajgrowicz & Scaillet 2012). Grade A against.
- **Pivots and Fibonacci ratios have no good evidence.**
- **On our data:** 15m and 1h breakouts have no gross edge (R-04; B03 #29-#32). Box-break and mfi rules were not
  trialled, by decision (A7). Round numbers and prior extremes (H-05) are queued as features.

## Claims

| # | Claim | Course baseline (paraphrased) | External evidence | Grade | Our data |
|---|---|---|---|---|---|
| R1 | Prices tend to stall or reverse at levels many traders watch | S/R works through collective memory: buyers return where price was rejected before and shorts take profit there; longer timeframe and more recent touches make a level stronger | Levels published daily by six FX firms predicted intraday trend interruptions better than chance (Osler 2000, FRBNY *Economic Policy Review* 6(2) 53-68, https://www.newyorkfed.org/medialibrary/media/research/epr/00v06n2/0007osle.html; DEM, JPY, GBP vs USD, Jan 1996-Mar 1998, 1-minute quotes, gross). Predictive power varied across firms and currencies | **B** (FX, gross) | **feature:** `f_levels` (touch counts, confirmed with fractal lag). Not screened yet (H-02 / O-4) |
| R2 | Levels are zones, not lines; wider zones hold more orders | Treat levels as areas; a wide zone is where orders accumulate and a move accelerates once it leaves | Order clustering at and around round numbers (Osler 2003, NY Fed SR125, https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr125.html; FX dealer order book, gross) supports "areas"; zone width itself is untested | **C** | **feature:** sr_channels width (`width_pct`) in the replica |
| R3 | At a box, price either closes through it (continue to the next box) or rejects it (reverse); it rarely lingers inside | Price sits above or below a box, not inside; a body close 20-30% through the box is a break, a touch without a close through is a rejection | Take-profit clusters produce reversals at levels; stop clusters produce acceleration through them (Osler 2003; Osler 2005, NY Fed SR150, https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr150.html). The "rarely inside" claim is untested | **B** for the two-outcome mechanism; **D** for "rarely inside" | **pending: pa-claims** (time spent inside boxes; break vs rejection base rates). **feature:** `sr_break_up`, `sr_break_dn` (replica, 99.86% match) |
| R4 | A break trade: a body close through a box, aligned with mfi, confirmed on 15m, entered on the pullback, stop beyond the box, target the next box, minimum 2:1 | Six ordered steps across 1h and 15m (see the money-flow file for the mfi part) | Breakout rules on single assets: no significance after data-snooping control (Sullivan, Timmermann, White 1999, *JF* 54(5), https://doi.org/10.1111/0022-1082.00163); in-sample edges wiped out by low costs (Bajgrowicz & Scaillet 2012, *JFE* 106(3) 473-491, https://ideas.repec.org/a/eee/jfinec/v106y2012i3p473-491.html; DJIA 1897-2011). Opening-range breakouts on futures: Holmberg, Lönnbark, Lundström 2013, *FRL* 10(1) *(results unverified)* | **A against** simple break rules net of costs | **tested:** R-04 (1h range breakout, gross +0.015 R, t 0.38); B03 #29-#32 (15m breakout, all FAIL, net −0.52 to −0.63 R). Box-break rules **not trialled, by decision** (A7). **withdrawn:** H-35 |
| R5 | Broken support becomes resistance (role reversal), and the first retest of a broken level is a high-probability entry | A 1h close beyond a key level breaks it; the retest from the other side is often rejected and is the preferred entry, with a tighter stop | Osler 2003 finds stop-loss orders cluster just beyond levels, so a broken level has had its stops consumed; that is consistent with, but does not test, role reversal. No peer-reviewed test of retest entries found | **C** | **pre-registered:** H-25 (15m breakout, limit at the broken level). New hypothesis **KB-1** (retest adverse selection) |
| R6 | Moving averages (EMA 10/20/50/200) act as dynamic support/resistance; the 1h EMA20 is touched on almost every trading day | MAs are dynamic S/R; the course claims the 1h EMA20 is touched at least once on about 98% of days; a wide 15m EMA10-EMA50 gap tends to close; the 4h/1d EMA200 often turns price | MA crossover rules: Brock et al. 1992 (gross, DJIA) and their failure under data-snooping control (Sullivan et al. 1999). Gold intraday MA rules: some long-window simple MA rules significant, gross, gold only, not silver (Urquhart, Batten, Lucey, McGroarty, Peat 2018, *JIFMIM* 52, 102-113; https://eprints.soton.ac.uk/411993). "Touched 98% of days" is near-certain by construction for a 20-bar average inside a day's range | **B** for gross MA predictability in gold; **D** for the touch statistic (and it carries no edge) | **pending: pa-claims** (touch rate; EMA-gap closing). **feature:** `ma_stack_sma_ema` (replica), MA distances (technical) |
| R7 | Round numbers ($10, $50, $100) are barriers: price hesitates before them and moves faster once through | Not taught directly; the course's daily levels often fall on round numbers | Round numbers act as barriers in daily and intraday gold, changing the conditional mean and variance; gold is less likely to continue after reaching $100 levels (Aggarwal & Lucey 2007, *Rev. Financial Economics* 16(2) 217-230, https://ideas.repec.org/a/wly/revfec/v16y2007i2p217-230.html; gross). Barriers at numbers ending in 0 and 00 for gold, not silver (Lucey & O'Connor 2016, *FRL*, https://research.ucc.ie/en/publications/mind-the-gap-psychological-barriers-in-gold-and-silver-prices/). FX order clustering at round numbers (Osler 2003) | **B** (gold-specific, two papers, gross) | **feature:** `f_round_numbers` ($5/$10/$25/$50; **no $100 step**, D-5). **proposed:** H-05. New hypothesis **KB-4** (target front-running) |
| R8 | Floor pivots (PP, R1-R3, S1-S3 from yesterday's H/L/C) give the day's bias and its main levels; R1/S1 strongest | Above the pivot = bullish day; R1/S1 strong, R2/R3, S2/S3 weaker | No peer-reviewed test found. A master's thesis on three FX pairs found no support for pivots used the traditional way, and some profit when inverted for two pairs (Vilhelmsson, Lund University, https://www.lunduniversity.lu.se/lup/publication/8979805) | **C against** | **feature:** replica daily pivot. R-09 (pivots retired as rules) |
| R9 | Prior day and prior week highs/lows are key levels where reactions occur | The structure tool can show daily/weekly/monthly highs and lows; the owner turned daily and weekly on | Covered by the round-number and stop-clustering mechanism (R7, Osler); no gold-specific study of prior-day extremes found | **C** | **feature:** previous day/week levels (`f_session_zones`). **proposed:** H-05, H-08 |
| R10 | Channels: in an up-channel buy only the lower line, in a down-channel sell only the upper line; target the far side, bank most at the midline | Stop just outside the channel, take most of the position at the midpoint, the rest at the far side | No peer-reviewed test of parallel channels found. Lo, Mamaysky, Wang 2000 test rectangles and other shapes on US stocks (see the pattern file) | **D** | not checked |
| R11 | Trade level to level: the target is always the next level in the trade's direction | Every trade starts at a box and targets the next box; no box, no trade; never enter between levels | No test found. If take-profits cluster at levels (R7), a target placed at the level fills less often than one just before it | **D** for the rule; **B** for the front-running inference | New hypothesis **KB-4** |
| R12 | Do not fade a level when price arrives with high volume and large candles | Large volume and large candles approaching a level can break any level: stand aside or trade with the move | Volume conditions continuation vs reversal (Llorente, Michaely, Saar, Wang 2002, *RFS* 15(4); see the money-flow file) | **B** for the mechanism (equities); **none found** for gold or tick volume | New hypothesis **KB-3** |
| R13 | "Event areas" (where an impulse started) act as future S/R | The market remembers where a burst began | None found | **D** | not checked |

## What this means for goldbot

1. **Levels are worth keeping as features; rules built only on them are not worth a trial.** The B-grade evidence
   (Osler, Aggarwal & Lucey) is about *conditional behaviour near levels*: slower before, faster after. That is
   exactly what a model can use as an input and what a fixed rule wastes.
2. **Add the $100 step** (D-5) before H-05/O-4 runs: it is the one level the gold papers single out.
3. **Box breaks are breakouts.** On our data breakouts at 15m and 1h have no gross edge. The box tool changes where
   the level is, not the mechanism, so a box-break trial would need new evidence first (B4b rule).

## Open checks

- pa-claims: time inside boxes, break vs rejection base rates, EMA20 touch rate.
- Verify Holmberg et al. 2013 and Osler 2003 bibliographic details.
