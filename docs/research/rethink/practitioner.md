# Rethink lane `practitioner`: 163 gold candidates from practitioners (2026-11-02)

Staff quantitative strategist, for the strategy-researcher. Brief: `rethink-brief.md` (owner 2026-11-02: "~3,330 tried,
0 qualified; find 100s of candidates and review how we look"). Catalogue: `config/research/candidates/practitioner.yaml`
(ids `RT-PRACTITIONER-001..163`). Public pages only: nothing signed up for, logged into, downloaded or paid for. No
market data was read and nothing was backtested, so K is unchanged by this lane. A candidate only counts in K once
it is run.

## Numbers first

| | Count |
|---|---|
| Candidates with an exact rule (entry, side, stop, exit, hold) | **163** |
| By source: books and known traders / TradingView scripts / MQL5-Myfxbook EAs and signals / session, fix and calendar / YouTube and courses / COT, sentiment and flows / quant blogs | 38 / 37 / 23 / 21 / 18 / 16 / 10 |
| Evidence grade: peer-reviewed / practitioner-documented / folklore / own-data | 8 / 56 / 98 / 1 |
| Timeframe: 15m / 1h / 4h / 1d | 42 / 50 / 22 / 49 |
| Mapping: fits the sprint3000 vocabulary as is / needs a new primitive (named in the row) | 60 / 103 |
| Test priority: A (maps at 1h/4h/1d, not already failed) / B (new primitive or 15m) / C (grid, martingale, paid or missing data, proxy rule, already failed or retired) | 47 / 55 / 61 |
| Flagged grid / martingale systems (listed with their risk) | 6 / 3 |
| Rows whose family **already failed here** or was retired (sprint-200, H-xx, R-08; marked in `prior_test_or_risk`) | 42 |
| **Gold sources with a long, third-party-verified, non-grid live record** | **0 found** |

## What "verified" turned out to mean

Myfxbook or MQL5 verification was the evidence bar. The searches did not find **one** gold practitioner with a
multi-year, third-party-verified live record that is not a grid or averaging system:

- **MQL5 gold signals** that came up are young (most < 1 year, many 1-10 weeks). MQL5 itself marks several as "newly
  opened account, results may be random". One that says "no grid, no martingale" (Gold H1 Phoenix Breakout, signal
  2388689) is **-66%** since going live in 2026-08. Gold Breakout Straddle (2386330) carries a "too few trading days"
  warning, and XAUUSD Power Breakouts (2381396) is -2% with 0 subscribers.
- **The long Myfxbook records that are claimed** belong to deep-drawdown systems, most of them grid or averaging:
  Forex Gold Investor +297% since 2020-10, **max DD 42%**; Golden Pickaxe (ML-filtered grid) +1,435% since 2022,
  **DD 51%**; XTrader Pro Gold 85% win rate, **DD 58%**; EA Gold Stuff (trend + grid, lot x1.5, stop loss off by
  default) reports DD of 19-30%; Quantum Queen (trend grid, martingale removed in v2.0) claims +214% over 20 months.
  That profile, a high win rate with a rare deep drawdown, is what averaging *into* gold's 2019-2026 rise produces. It
  is short volatility plus bull-market beta, not an edge we can screen with stop-and-target rules. They are listed
  (RT-040..043, 053, 054, 060) with that risk. Each one's Myfxbook page was **not opened**, so every one of these
  records is marked "claimed, not checked".
- **The "best gold EA" ranking pages are vendor marketing.** The main one (newyorkcityservers.com) sells the products
  it ranks, and its own figures disagree with each other (Forex Gold Investor: "4+ years" and "3+ years" in the same
  article).
- **The one practitioner write-up with real numbers** is the MQL5 London-breakout study (RT-038). It ran 715 trades
  from 2023-01 to 2026-09 on real ticks from two brokers' data, at profit factor 1.37-1.38, 49% wins and a 17-19% DD
  at 1% risk. The parameters were fitted on 2023-2025, and the "unseen" 2026 run gave PF 1.61 on 157 trades.
  **Costs are not stated**, and the whole sample is the gold bull run. It is the most testable practitioner claim
  found, and our 2010-2017 explore window is the honest test of it.
- **Unger Academy's GC futures systems** (RT-096..098) are the best-documented gold-specific systematic claims. Since
  2010 they report an average trade of $140-340 per contract, which is **$1.4-3.4/oz**, and "out of sample since
  2018". Rules are only partly disclosed. In the Supertrend system **74% of profit came from longs**.

**Reading.** Practitioners do not hold a verified gold edge that we have overlooked. What the population claims
comes in three forms:
- multi-day trend on gold, profitable mostly from the long side;
- session breakouts measured in 2023-2026;
- averaging systems that harvest the up-drift.

Our own results say the same. The 1h-4h trend is gross-positive, and costs plus swap eat it (sprint-200 trend), while
breakouts and fades at 15m are about zero gross.

## Review of the process: what the practitioner view says about "3,330 tried, 0 qualified"

1. **The cost model, not the search, is the binding constraint, and one input is probably too harsh for the explore
   years.** `config/settings.yaml` charges a long swap of -$60/lot/night ($0.60/oz). Its own comment says this is the
   2024-25 level at ~5% USD rates and "overcharges the low-rate 2010-2021 years". At $1,300 gold that is about
   **17% a year**. Gold futures carry over 2010-2017 was roughly the near-zero policy rate. Every practitioner
   multi-day gold system (Turtles, Clenow, Carver, Unger) lives on holds of days to weeks, so a swap that is 5-10x
   too high in the explore window kills that whole class before confirm. **Flag for the principal (not changed
   here):** price swap per era (benchmark rate + the broker's markup, measured on today's ICM table) before
   retiring 1d trend for good. The decision memo's option C (a swap-free venue) assumes futures carry is ~0, but it
   is not zero when rates are 4-5%.
2. **The practitioner trend edge is mostly long-side bull beta.** Unger is 74% longs, and Quantum Queen, Gold Guardian
   and Forex Gold Investor are long-biased. Our explore window (2010-2017) holds the 2011-2015 bear, and confirm
   (2018-2025) is a bull. A trend rule that is truly symmetric should survive both. A long-only rule will look like
   noise in explore and like an edge in confirm. The leak-free control is right, but a **long-only buy-and-hold
   control on the same holds** is also needed. Otherwise a confirm-window pass could be pure drift.
3. **Most of the popular-script universe is already tested.** Trend breakouts, EMA crosses, session ORBs, sweeps,
   ICT/SMC, candles, pivots, COT deciles, ETF flows, time-of-day, the Asia drift (H-04: gross +0.043 R at t 3.09,
   net -0.093 R) and silver/USD lead-lag have all been run. The 42 rows marked "already failed/retired" should not be
   rerun without new evidence.
4. **The new primitives practitioners use that the sprint3000 vocabulary lacks**, by how often they appear in the
   catalogue:
   - regime gates: `efficiency_ratio`, `trend_vote_score`, `corr_regime`
   - trail-flip entries: `chandelier_flip`, `halftrend`, `psar_flip`, `hma_slope_flip`, `kama_cross`
   - the opening-range family: `range_pct_band` (London breakout width filter), `stretch_orb` (Crabel), `open_range_breakout(k)` (Williams)
   - reversal setups: `turtle_soup`, `smash_day`, `eighty_twenty`
   - clock entries: `clock_window_entry` (fix and session windows)
   - calendar windows: `calendar_month_hold`, `calendar_event_window` (CNY, Diwali)
   - one exit: `flat_before_rollover`

   `flat_before_rollover` is the cheapest to add, and it goes straight at the measured failure mode.

## Ranked shortlist for the next screen (from priority A/B; prior from mechanism + cost arithmetic only)

| Rank | Id | Candidate | Why it is worth a slot | Vocabulary |
|---|---|---|---|---|
| 1 | RT-163 | 4h Donchian trend, entered on 1h, **flat before the 21:00 UTC rollover** | Keeps the measured gross trend premium (S200-TREND-03 +0.165 R OOS gross) and removes swap | `donchian_break(20)` + new exit `flat_before_rollover` |
| 2 | RT-038 | London breakout with a **range-width band** (0.30-1.60% of price), 2.5R, BE at 1.5R, flat 21:00 server | Only practitioner claim with 715 trades and two data feeds; the width filter is the untested part | `session_range_break(asia)` + `range_pct_band` |
| 3 | RT-002 / RT-047 / RT-162 | Trend entries gated by the **Kaufman efficiency ratio** | Chop is where trend rules pay costs for nothing; this gate has not been used here | `supertrend_flip` / `donchian_break` + `efficiency_ratio` |
| 4 | RT-061 / RT-062 / RT-079 | Turtle 20/10 and 55/20, Donchian 4-week | The canonical multi-day gold trend rules; run them only with era-priced swap (flag 1) or the RT-163 exit | `donchian_break(20/55)` |
| 5 | RT-097 | Unger 30m/1h mid-price Supertrend, multiday | Best-documented gold systematic claim | `supertrend_flip(10,3)` |
| 6 | RT-067 / RT-082 | Crabel stretch ORB; Fisher ACD on the COMEX open | Opening-range methods built on a volatility-normalised trigger, not a fixed range | new `stretch_orb`; `opening_range_break(ny,30)` |
| 7 | RT-068 / RT-069 | NR7 and ID/NR4 at **1d** | 1m/5m NR4 failed (H-81 Z6); daily untested, cost per R small | `nr_breakout(7/4)` + `inside_bar_break` |
| 8 | RT-072 | Raschke Holy Grail (ADX > 30 first pullback to EMA20) | Continuation in strong trends; 1d, few trades | `ema_pullback(20,0)` + `adx_min(14,30)` |
| 9 | RT-080 / RT-018 | Bollinger bandwidth-low breakout; TTM squeeze release (4h) | Compression precedes expansion; target is many times the cost | `compression` + `bb_break` |
| 10 | RT-089 / RT-153 | Brandt rectangle break; $50 round-number break at 1h | Fits the KEYLEVELS primitives; Osler (2003) stop-cluster mechanism | `level_break` |
| 11 | RT-120 | COT managed-money **gross-short** extreme (not net deciles) | H-86 used net; gross-short capitulation is a different mechanism | new `cot_mm_gross_short_pct` |
| 12 | RT-129 | Gold/silver ratio z-score extreme turning | The lead-lag X1 failed; the ratio-extreme variant is untested | new `ratio_zscore(XAG)` |
| 13 | RT-105 | Autumn effect (Sep, Nov long) | Peer-reviewed (Baur 2013); re-tested 2024, result not retrieved; 2 trades a year, so it is a filter more than a strategy | `calendar_month_hold` |
| 14 | RT-149 / RT-150 | Last-hour / first-London-hour intraday momentum | Gao et al. (2018) mechanism, a 1h hold rather than the failed 15m versions | `momentum_sign` + session |
| 15 | RT-114 | Post-NFP second-hour continuation | NFP 5-60 min failed (t -0.89); this pre-registers the 60-180 min window as a single look | new `event_followthrough` |

Not recommended: every grid/martingale row (not screenable with our stop-and-target harness, and negative skew is
ruin at £1,000), the paid-sentiment rows (HGNSI, Optix, DSI: no free history), and the retail-sentiment rows (IG,
Myfxbook outlook: no history, so they are forward-log only).

## Sources consulted (public pages, 2026-11-02)

MQL5: London breakout study https://www.mql5.com/en/blogs/post/777540, session notes
https://www.mql5.com/en/blogs/post/773690 and /772224, signals 2388689, 2386330, 2381396, 2379848, 2384268, products
166774, 183962, 176905, 169819. Vendor reviews (marketing): https://newyorkcityservers.com/blog/best-forex-gold-eas-robots,
/quantum-queen-ea-review, /ea-gold-stuff-review, /waka-waka-ea-review. TradingView listings
https://www.tradingview.com/scripts/gold/ and /scripts/editors-picks/, plus the script pages cited in each row.
Unger Academy https://ungeracademy.com/?p=7022 and the Supertrend/high-low article. CBS thesis (Donati & Jung 2019)
https://research.cbs.dk/en/studentProjects/gold-price-dynamics-around-the-clock/. Caminschi & Heaney (2014) JFM
https://ideas.repec.org/a/wly/jfutmk/v34y2014i11p1003-1039.html. Baur (2013) RIBAF
https://opus.lib.uts.edu.au/handle/10453/23416. Hulbert summaries
https://stockcharts.com/articles/muscular-investing/2018/12/mark-hulbert-sentiment-indices-4.html. SentimenTrader Gold
Optix https://sentimentrader.com/indicators/gold-optix. QuantifiedStrategies https://quantifiedstrategies.substack.com/p/a-gold-strategy.
Quantpedia overnight drift https://quantpedia.com/dangers-of-relying-on-ohlc-prices-the-case-of-overnight-drift-in-gdx-etf/.
Books are cited by author, title and year in each row and were not re-read for this lane (*unverified* against the
text). ForexFactory/Metalsmine threads: the fetch was refused (HTTP 403), and BabyPips threads gave no complete rule
sets, so no forum system is listed on forum evidence alone.

## Limits

- No Myfxbook or MQL5 signal page was opened account by account. Every "verified" claim in the YAML says "not
  checked", except the MQL5 signal growth figures shown in the search results.
- TradingView like and boost counts are the numbers shown in the listing on 2026-11-02, or come from third-party
  blogs (marked unverified). The global most-liked list could not be fetched (HTTP 404 on the sorted page).
- Paywalled rules (QuantifiedStrategies #12/#56/#97) and unread pages (Alpha Algo, TradeMachine) are entered as
  **proxies** and marked as such. They test the public idea, not the vendor's rule.
- Many forum, course and EA "rules" leave parameters out. Where a parameter was ours, the row says so ("our choice",
  "inferred"). These rows are hypotheses in the source's spirit, not replicas of it.
