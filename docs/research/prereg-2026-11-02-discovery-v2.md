# Pre-registration: discovery grammar v2, one explore run each on 1h, 4h and 1d (research-director bet 1, week 2026-11-02)

Principal quantitative strategist, written 2026-10-31 **before any v2 search output exists**. Binding text: ADR 0058
(and its amendment 2026-11-02, below), `prereg-amendment-2026-10-30-kill-rules.md` A4 with its quant conditions, and
this file. Code frozen at commit **`2ccbc8e5`** (`goldbot/research/autodisc/grammar_v2.py`, `gen_gp.py`, `nightly.py`,
`outcomes.py`, `spec.py`; driver `scripts/research/autodisc_v2.py`). This document is committed on top of that commit and
before the first run; the results document cites both hashes. Any change to the code, the constants or the reading
rule below is a new pre-registration and a new K.

## 1. Question and why it is worth one run

v1 (2026-10-30, 288 specs per timeframe on 15m/1h/4h, K 864) found **0 survivors**: 1h best 0.841 vs null bar 1.337,
4h best 0.692 vs 1.019 (RC p 0.64, PBO 0.62); on 15m the top specs beat a cost-crushed null with a **negative own net
mean R** (-0.03 to -0.08 R). Three things were wrong with v1 for our costs: no absolute floor on own net R, priors-only
costs, and a vocabulary of only time and price-regime leaves. Every hand-written family since has failed on history
(scorecard 2026-10, 6 of 6 bets this week). v2 asks one question: **with the when-to-trade gates that have the best
published support (volatility forecasts, range compression, scheduled news, gold's slow macro drivers) and exits
that let winners run, does a bounded search find any 1h/4h/1d rule whose own net R is positive and beats its own
search null?** Prior P(at least one explore survivor) ~0.10 (scorecard 2026-10-31, bet 1). The result decides
A4: **0 survivors on all three timeframes freezes automated generation on 2026-11-20** until the Q1 state-of-the-art
refresh.

## 2. Evidence behind the v2 primitives (gates, not directions)

| Primitive | Claim | Source | Sample, instrument | Net of costs? | Robust or single |
|---|---|---|---|---|---|
| HAR range band | Realised volatility is forecastable from 1/5/22-day lags | Corsi 2009, *J. Financial Econometrics* 7(2) | many assets; replicated widely | n/a (a forecast) | Robust |
| HAR range band (ours) | HAR-only rho vs next-day window range on XAUUSD | `opportunity-days-screen-2026-11.md` (H-16 screen) | XAUUSD 15m, 2010-2017 | n/a | Our measurement; H-16's *added* inputs failed, the HAR baseline itself forecasts range |
| NR4 / NR7 | Narrow-range days precede range expansion | Crabel 1990, *Day Trading with Short Term Price Patterns* *(unverified)* | US futures, practitioner | No | Single, practitioner |
| Event day | Scheduled US releases cause price jumps in FX and bonds | Andersen, Bollerslev, Diebold & Vega 2003, *AER* 93(1) | FX futures 1992-1998 | n/a | Robust (replicated for gold by several studies *(unverified)*) |
| Real yield / dollar change | Gold's slow-horizon drivers | Erb & Harvey 2013, *FAJ* 69(4) | gold 1975-2012 | Gross | Robust as a level relation; as a 20-day *direction* rule it failed for us (R-16, H-84: t -0.59 / 0.47) |
| GVZ tercile | Implied vol regime | `state-of-the-art.md` (VRP/GVZ section) | GVZ 2008+ | n/a | Single for gold |
| r4h side | Short-horizon continuation | Moskowitz, Ooi & Pedersen 2012, *JFE* 104(2) (monthly, not 4h) | 58 futures 1965-2009 | Gross | Robust monthly; at 4h on gold our H-A read **-0.008 R (t -2.7)** on 15m random entries (`when-map-screen-2026-11.md`), so it enters only as a side the search may take with or against |
| BE + trail exit | Let winners run on trend days | `exits-adaptive-targets-2026-10-26.md` | XAUUSD | H-80 R2 runner FAIL (-0.018 R, t -1.12) on box entries | Single, failed once on one entry family |

Known failures this search must not re-test as stand-alone rules (all retired): H-84 macro direction (R-16), H-17
trend day (R-17), H-83 timing at the scalp clock, H-80 box runner. v2 uses their inputs only as gates inside a
counted search; a v2 survivor that is a re-expression of a retired rule is labelled as such in the results.

## 3. Frozen configuration

| Item | Value |
|---|---|
| Window | explore only, 2010-01-01..2017-12-31 (1m bars via `ExploreFrame`; macro and calendar rows cut at 2018-01-01 on available_utc, value_date and event_utc inside `PrimitiveProviderV2`). Confirm (2018-01..2025-09) and holdout (2025-10..2026-09) untouched |
| Timeframes | 1h, 4h, 1d; **one run each**, in that order, one shared ledger `~/tmp/autodisc-v2/discovery_real.db` (a second run of a timeframe is refused by the driver) |
| Search | G2 NSGA-II (`gen_gp.run_gp`), population 32, generations 8 (at most 288 search specs a timeframe), seed 20261102, `grammar="v2"`, ledger generator version "2" |
| Null | the whole search rerun 20 times on random sides with the timing kept (G0); bar = 95th percentile of best-of-search, floored at 0 |
| Judged | top 20 candidates (best fitness first) get the circular-shift null (whole weeks, p <= 0.05) and the plateau test over their grid neighbours (>= 60% positive and mean >= 0.5 x peak) |
| Batch | Hansen SPA p <= 0.10 and PBO <= 0.30 over the floor-passers, else nothing advances |
| Floors | events >= 300 (1h), 150 (4h), 100 (1d); time in market <= 40%; min-fold net t > 0; excess R over the drift baseline > 0; net mean R > 0 at 1.5x cost; **absolute floor: own net mean R >= +0.05 R at the cost of record** (new, A4 item 2) |
| Fitness | min over 4 purged, embargoed folds of the day-clustered t of excess net R (v1's, unchanged) |
| Inputs | `raw/xauusd_1m_dukascopy_2010..2017.parquet` (local only), macro `~/tmp/macro-pit/macro/macro_fred.parquet` (FRED first releases: real_yield_10y, broad_dollar, gold_vix), calendar `~/tmp/ts-data/cal/us_calendar.parquet` (calendar-v1, tier 1: NFP, CPI, PCE, FOMC). Nothing is committed |

**Vocabulary (enumerated; A4 quant condition).**

| Group | Leaves | Count |
|---|---|---|
| v1 family rules (entry leaves and sides) | 1h: box_break_close, box_next, level_break, pdc_break, squeeze_release (5); 4h: box_break_close, box_next, donchian_break, pdc_break, squeeze_release, tsmom (6); 1d: donchian_break, tsmom (2) | 5 / 6 / 2 |
| v1 time | asia, london, ny, london_ny_overlap, month_turn, mon..fri | 10 |
| v1 regime | rvol_q, trend_strength, hurst: lo/mid/hi each | 9 |
| **v2 gates** | har_lo/mid/hi (H-16 HAR band, monthly walk-forward refits, 500-day minimum fit), nr4, nr7, event_day, ry_up/ry_down (DFII10 20-obs change), usd_up/usd_down (broad dollar 20-obs % change), gvz_lo/mid/hi (GVZ tercile over its previous 252 observations) | 13 |
| **v2 side** | r4h_sign (sign of the 4h mid return ending at the decision close), `with` or `against`; never an entry leaf | 1 |
| Side rules | long, short, with/against any family or r4h | 2 + 2 x (families + 1) |
| Session windows | all, asia, london, ny, london_ny | 5 |
| **v2 exits** (closed list, A4 condition: five) | stop 1.0 ATR(14) with: time exit only; target 1R; 2R; 3R; break-even at +1 R with a 1 R trail. All close at MAX_BARS (1h 48, 4h 24, 1d 10 bars) | 5 |
| Bounds | entry depth <= 3 and <= 4 leaves; filter depth <= 2 | |

Point in time: every gate is read at the decision bar's close through `asof_join` on `available_utc` (daily values at
their opportunity day's 22:00 UTC close; macro on each series' first-release stamp; calendar by its latest row known at
the bar). `tests/research/autodisc/test_grammar_v2.py` perturbs bars, macro and calendar after a cut and requires every
v2 mask and the r4h side before it unchanged.

**Costs (cost of record, measured, not priors).** Fills at the Dukascopy bid/ask of 2010-2017 (median spread $0.34,
four times the broker's measured $0.085, so the spread is already conservative), plus **$0.207/oz** per trade from the
measured ICM cells of week 2026-10-26..30 (1,373 fills; scorecard 2026-10 s.4): round trip $0.208 less spread $0.085
= $0.123 slippage and commission, plus the measured stop overshoot $0.084 charged on every trade, not only on stops.
This is the worse of the measured probe cost and the signal-time cost by construction (A4 item 2). Swap: the settings
prior (long -$0.60/oz a night, x3 Wednesday; short 0), because no swap is measured. Median cost in R at a 1.0 ATR(14)
stop, swap excluded: **1h 0.172 R (p90 0.254), 4h 0.086 R, 1d 0.037 R**; a long held 4 nights adds ~0.37 R at 4h and
~0.15 R at 1d. The settings slippage prior ($0.37/oz) is *not* applied: the brief and A4 item 3 require measured
costs, and the 1.5x stress covers a cost about 50% higher.

## 4. Multiple testing: K_v2 declared now

- **K_v2 = 1,704** = 3 timeframes x (288 search specs + 20 judged candidates x at most 14 grid neighbours). The
  ledger's actual count will be at or below it; the DSR uses the declared figure whatever the ledger shows.
- **DSR K for any v2 survivor = 2,968** = program K before v2 (1,264: ~360 arm ids + v1 864 + 37 screen looks + 1
  registry trial + 2 weekend controls; scorecard 2026-10-31) + K_v2 1,704. `search_and_judge(k_offset=1264,
  k_declared=1704)`.
- Null specs (about 3 x 20 x 288 = 17k) count in K_null, not K.
- Registry trials: **0**. Budget slots: **0**. The local registry sidecar is topped up by `k_link.sync_discovery_k`;
  the program ledger (`hypotheses.md` section C) records +1,704.

## 5. Reading rule (decided now)

- **Explore survivor** (per spec): every floor and check in section 3, the batch passes, beats the search null, shift
  p <= 0.05, plateau. A survivor is a recorded fact, never evidence of edge.
- **Verdict on A4 (the only decision this run makes):**
  - **0 survivors on all three timeframes -> FAIL.** Automated generation freezes on 2026-11-20 until the Q1
    state-of-the-art refresh (A4 item 5); no v2.x; the nightly job stays off; discovery v2 is retired in
    `hypotheses.md` with these numbers. Its gates stay available to hand-written pre-registrations, which pay the K.
  - **>= 1 survivor on any timeframe -> CONTINUE generation past 11-20.** Each survivor is written to
    `hypotheses.md` as proposed. It advances to a confirm read (2018-01..2025-09) only if (a) its DSR at K 2,968 is
    >= 0.95 on >= 200 events (ADR 0058 amendment F1/F2), (b) it is not a re-expression of a retired rule, and (c) a
    confirm slot exists: 2026Q4 has no open slot, so a confirm read needs a program-director extension or 2027Q1.
    No confirm read is run in this lane.
- **Reported, not decisive:** per timeframe the real best fitness against the null bar and its null percentile, SPA
  p, PBO, the number of specs that beat the null but failed an explore check (and which check), and the net-R floor
  failures.
- **Void:** a crash after specs were ledgered voids that timeframe's run; its specs still count (they are in K_v2), it
  is not rerun, and the timeframe reads as 0 survivors.

## 6. Owner-only flags (nothing assumed)

- **Sizing.** A 1.0 ATR(14) stop on 4h is ~$6.4/oz on 2010-2017 and ~$15-25 today; on 1d ~$16 then, ~$40-60 today.
  At 0.01 lot those are £5-20 a trade at 4h and up to ~£45 at 1d, near the 1% cap at £1,000 and the £50 daily limit
  (ADR 0004). Any 1d survivor is demo/shadow only unless the owner changes balance or the minimum position size.
- **Budget.** A confirm read needs a budget slot (owner/PD); none is assumed.
- **Swap.** The binding long swap is a prior; a 4h/1d book depends on it more than on the spread.
