# Pre-registration: the catalogue screen (WS-3), 2026-11-07

Owner of the text: strategy-researcher (principal). Signs before the run: quant-reviewer. Verdict: evaluator.
Plan: signed consolidated plan 2026-11-07 (`top30_screen_design`, WS-3 build_brief / tests_reviews / kill_rule,
quant A1-A3, PD 3/4). Sources: `top30_catalogue_1107.json` (sha256 `5948c752...ed5270`) and
`patterns_top30_1107.json` (sha256 `c01ba254...9224c`), copied into `goldbot/research/catalogue/specs.yaml`
(`card_text`, with the per-variant implementation in `units[]`).

This file is written and frozen BEFORE any return of any unit is computed. The only data read before signing is the
explore EVENT COUNT per unit (`run.py counts`: detectors only, no fill, price path or return is computed), used for
the feasibility drop in section 7.

## 1. What is tested, and the unit

- One harness, `goldbot/research/catalogue/`, one run, 8 workers on the Mac (14:00-16:00 UTC slot).
- **Verdict unit = (family, variant), pooled over every instrument AND timeframe of the variant** (quant A1). A cell
  (instrument x timeframe) is reported for routing only and never promotes alone.
- Units: see section 9 (generated table). Each has ONE registry row, written before any return
  (`run.py enumerate --write-registry`, family `catalogue:<id>`, config = the unit's spec + `specs_sha256`).
- Null units (A5 void rule): 7 random-entry units (one per timeframe 5m..1w), 3 buy-every-close units (US500, all
  indices, gold) and 1 random-rank cross-sectional book. They are counted in K, never promoted.

## 2. Trial budget and K (owner-only limits respected)

| item | value |
|---|---|
| quarter cap 2026Q4 (`trial_budget_overrides`, owner ADR 0073) | 3,500 |
| Q4 used before this run | max(local registry Q4 rows 3,173, plan floor 3,173) = 3,173 |
| reserved for WS-5 arms (<= 6), WS-6 (3), WS-7 (3) | 12 |
| this screen | 90 units = 79 real + 11 null (one registry row each, written 2026-11-07 before any return) |
| headroom after this screen and the reserve | 225 |
| program K after the screen (DSR reported at it) | 3,263 (K_cells = 90) |
| the enumerator aborts when used + units + reserved > cap | `run.assert_budget`, test `test_enumerator_aborts_over_quarter_budget` |

DSR is reported at the registry K (`n_trials_effective` + units) and at K_cells (the units of this run); it is
**never gated**. Rows written elsewhere today (RSI2 +3, N-1/N-3/N-9 cards) are inside the reserve or are passed as
`--extra-used`; the brain registry is merged by registry_sync as usual.

## 3. Windows (W2, frozen 11-02)

| window | span | use |
|---|---|---|
| explore | decisions 2010-01-01..2017-12-31 (warm-up from 2008-09; gold intraday warm-up 120 days) | the screen |
| learn_pre | 2003-05..2005 (Dukascopy) | same-sign check of the mean excess where n >= 60 |
| CONFIRM | 2018-01..2025-09, read ONCE (ledger `state/catalogue_reads.jsonl`) | explore passers only |
| TEST-OLD | 2006-02..2009 | CONFIRM passers only (`allow_test_old_for`) |
| holdout | >= 2025-10-01 | sealed: the loader raises (`HoldoutSealed`) and asserts no bar at/after it |

A unit whose CONFIRM window was already read by another line (sprint-3000, H-80 box, TREND-XASSET, weekly-target,
TV-author) would be reported only; none of the units below re-runs such a spec (S01 RSI2 and Double-7s are closed;
box_break_close, opening-range, Donchian/Turtle 55/20 and sweep_absorb were excluded by the pattern scout).

## 4. Data rules as implemented (A3)

- Gold 5m..1h: resampled from the Dukascopy 1m bid/ask files (mid OHLC, mean spread, tick count). Gold 4h/1d and
  every non-gold series: `raw/daily/bars/<key>_{4h,1d}.parquet` (Dukascopy h1, 2003+; indices 2011-13+); 1w is
  resampled from 1d by week of the close. Non-gold below 4h is not used (no history).
- Every bar carries its availability (`visible_at` = its close); a decision at bar i's close fills no earlier than
  bar i+1 (market at its open; stop/limit inside it). A daily decision therefore never sees a later-available bar.
- Close-only bars (high == low or no OHLC) are flagged; orders, stops and targets on them fill AT THE CLOSE (test).
- Source cutovers (e.g. USINDEX ICE-chained -> Dukascopy) split the series: per-cell detectors run per segment, so
  lookbacks and levels restart. Yahoo `=F` futures segments are never traded and are masked out of the S17 seasonal
  history; no level-based detector reads them.
- Pivots (ZZ(k), k = 2.0 on 1d/1w, 2.5 below) are known only from their confirmation bar; LMW extrema only 3 bars
  after the window that revealed them, and the first-known extremum is kept (no rewriting of history).
- Exogenous schedules known in advance: calendar-v1 US releases (2010+, `raw/calendar/us_calendar.parquet`, sha256
  `2da61655...6ee88921`, each event's `available_utc` precedes it), exchange holidays (weekdays missing from the
  cash index history), FOMC statement days. FRED 3m rates join by `available_utc` (asof).
- Every per-cell and cross-sectional detector passes the truncation-prefix look-ahead test
  (`tests/test_catalogue_lookahead.py`: events decided before bar n are identical on the full series and on its
  first n bars; non-vacuous: each detector produces events on the fixture). Three detectors failed it during the
  build and were fixed before this freeze (S07 meeting index, C04 LMW pivot replacement, B10 session-end hold).

## 5. Costs

- Gold: cost of record cor-2026-10-31.2, ICM all sessions, round trip $0.166/oz incl. $4.0/lot/side commission
  (0.4 bp at $4,000; test), + $0.11 overshoot on stop exits.
- Non-gold: the worse of the fallback (3 x typical raw spread, instruments.yaml; + ICM FX commission) and the
  Dukascopy spread of the fill bar; overshoot 0.5 x round trip on stop exits.
- Swap: 2.5 %/yr/leg per 21:00 UTC rollover held (calendar nights), index longs 7 %/yr. No measured swap is used.
- Reported at 1.0x and 1.5x (stress column). Cost R = cost / (|fill - stop|).

## 6. Controls (A2) and the pass statistic

- Default: random-SIDE twin at the same fill bar and price, mirrored stop/target; when the real trade exits on
  its time cap, exit mask or the data end, the twin is capped at that bar; when the real trade exits on its stop or
  target, the twin runs to the real trade's nominal time cap (T6: unbiased for gross under a martingale by optional
  stopping); matched excess per trade = (real - opposite) / 2.
- Side-biased units (explore events with long share outside 40-60 %) and the card-internal controls of
  S05/S06/S07/S08/S17/S26 (calendar or long-only rules): random-TIME same-side twin, same risk as a fraction of price,
  same target R and same bars held, entered at a seeded random bar of the same UTC day (intraday), +-20 bars (1d) or
  +-8 bars (1w). S03: random side at the same bar, i.e. inside its vol gate. The control chosen on explore is reused
  in CONFIRM and TEST-OLD.
- Statistic: t of the DAY-SUMMED gross matched excess (trades sharing a UTC fill day are one observation).

## 7. Feasibility drop (decided on event counts only, before signing)

A unit whose explore events (an upper bound of its trades) are below its n floor cannot pass explore; it is listed
"not run, no K" and is NOT registered. The list is in section 10 with the counts. The count pass computed no fill,
price path or return.

## 8. Reading rule (decided before any return)

Explore pass, ALL of:
1. t (day-summed gross matched excess) >= 2, where t is the Newey-West t with the unit's overlap lag L (section 14,
   C2; the plain t is reported beside it);
2. own mean net R > 0 (measured costs, 1.0x);
3. own mean gross R >= 2 x mean cost R;
4. >= 5 of the 8 years 2010-2017 with positive summed excess;
5. drift-adjusted mean gross R > 0;
6. n >= 300 trades when every timeframe of the unit is 1d/1w, else n >= 1,000;
7. learn_pre (2003-05..2005) mean excess of the same sign, where learn_pre has >= 60 trades (else not required).

Void (amended, section 14 C1): the run is VOID (no CONFIRM read, reported as void, nothing continues) when
k or more of the random nulls whose explore events reach their n floor pass explore, with k the smallest count with
P(X >= k) <= 0.02 at 5 % (today 4 feasible nulls: NULL-RAND-5m, -15m, -4h, NULL-XS-RANDOM, so k = 2, P = 0.014),
OR when any buy-every-close sentinel (NULL-LONGCLOSE-*) passes (a harness defect). The four nulls that cannot reach
their floor (NULL-RAND-30m, -1h, -1d, -1w) stay registered (K is conservative) and are reported as "cannot reach the
floor".

CONFIRM (explore passers only, read once): Benjamini-Hochberg q = 0.10 over the m explore passers on the one-sided
moving-block (block = max(5, L) fill days, C2) bootstrap p of the daily excess; AND >= 5 of the 7 full years 2018-2024 with positive summed
excess; AND own net > 0. 2025-01..09 is reported separately and is not gated.

TEST-OLD (CONFIRM passers only): n >= 60, net > 0, t (day-summed excess, Newey-West with lag L) >= 1.28. For
S11/S15/S16/S27/S30 TEST-OLD is also reported excluding 2009 (T4; reported, not gated).

Decision:
- **Zero CONFIRM passers closes the line today (11-07): no re-run, widening, renaming or neighbour variants.** The
  detectors may return only as point-in-time features under a new card counted in K.
- A unit that passes CONFIRM and TEST-OLD goes to WS-5 (demo learning arm + matched control, routed by the
  allocation rule; demo reads only kill). The card's money estimate uses the POOLED unit's CONFIRM estimate, never
  the selected cells. Lot-fit failures are listed, never resized.
- A unit that passes CONFIRM but fails TEST-OLD is retired with both numbers.
- Expected: 0-3 CONFIRM passers (low priors: Sullivan-Timmermann-White 1999 JF 54; Bajgrowicz-Scaillet 2012 JFE
  106; Park-Irwin 2007 J Econ Surveys 21; with 79 real units at a one-sided ~2.3 % t-threshold plus six other gates
  we expect about 0-2 explore passers from noise alone).

## 9. Registered units

Frozen: harness commit `b3f14388ff0ffb10a0596d0b09465a47bd823f08` (re-frozen after the quant amendments, section 14; the
registry rows were written against `87771d81` and carry over), specs.yaml sha256
`87975f069457579242b3cc7ea9a1c3893537801a490c6dab880b43aae2211256`, unit list
`docs/research/catalogue-units-2026-11-07.json` (sha256 `52021f2a...b0fa1d7701`, one `config_hash` per unit; the
registry rows carry the same hashes). 90 units: 79 real across 30 families + 11 null. The run refuses to start if the catalogue code differs from that
commit, if specs.yaml changed, or if any unit lacks its registry row. Any change after signing is a new card.

<!-- UNITS:BEGIN -->
| unit | instruments | timeframes | control | explore events | config hash | notes |
|---|---|---|---|---|---|---|
| S02-V0 | all 5 | 4h | A2 auto | 1128 | `5e444fbc9bca` | cash session built from 4h CFD bars (US 12-20 UTC, EU 08-16 UTC), flagged approximate per the card's data pin; fill at the next CFD open after the cash close |
| S02-V2 | all 5 | 4h | A2 auto | 1128 | `07d42f343b0a` | as V0; exit at the first cash close with IBS > 0.60, cap 3 days (18 4h bars) |
| S02-V3 | all 5 | 4h | A2 auto | 1695 | `4427d73d8c7c` | short cell |
| S04-V0 | all 5 | 4h | A2 auto | 2380 | `2558bab8f55a` | cash open/close from 4h CFD bars; gap = CFD price at the cash open vs the previous cash close; daily cash ATR20; +1 spread opening slippage NOT added beyond the bar spread (reported in the stress column) |
| S04-V1 | all 5 | 4h | A2 auto | 1147 | `613958c83b8c` | cash open/close from 4h CFD bars; gap = CFD price at the cash open vs the previous cash close; daily cash ATR20; +1 spread opening slippage NOT added beyond the bar spread (reported in the stress column) |
| S06-V0 | US500, USTEC, US30 | 1d | random_time | 656 | `587a33ab546b` | calendar-v1 US releases 2010+ (no learn_pre); PPI from calendar-v1; card control 'matched non-announcement day' implemented as the random-time same-side twin (+-20 trading days) |
| S06-V1 | US500, USTEC, US30 | 1d | random_time | 313 | `6dc167776168` | calendar-v1 US releases 2010+ (no learn_pre); PPI from calendar-v1; card control 'matched non-announcement day' implemented as the random-time same-side twin (+-20 trading days) |
| S06-V2 | US500, USTEC, US30 | 1d | random_time | 372 | `3f2d36b877fd` | calendar-v1 US releases 2010+ (no learn_pre); PPI from calendar-v1; card control 'matched non-announcement day' implemented as the random-time same-side twin (+-20 trading days) |
| S07-V0 | all 5 | 1d | random_time | 625 | `6316b0453660` | FOMC statement days from calendar-v1 (2010+, no learn_pre); odd-week card control approximated by the random-time same-side twin; V2 (vol-scaled notional) is identical to V0 in R and is not a unit |
| S07-V1 | all 5 | 1d | random_time | 604 | `a10f7cce2d62` | FOMC statement days from calendar-v1 (2010+, no learn_pre); odd-week card control approximated by the random-time same-side twin; V2 (vol-scaled notional) is identical to V0 in R and is not a unit |
| S08-V0 | all 7 | 4h | random_time | 29028 | `378549e137fe` | 4h UTC bars; leg A 08-16, leg B 16-20; time-shuffled card control approximated by the random-time same-side twin (same UTC day) |
| S08-V1 | all 7 | 4h | random_time | 14529 | `4c9cd989af4d` | 4h UTC bars; leg A 08-16, leg B 16-20; time-shuffled card control approximated by the random-time same-side twin (same UTC day) |
| S08-V2 | all 7 | 4h | random_time | 29000 | `9f80f9d7bb95` | 4h UTC bars; leg A 08-16, leg B 16-20; time-shuffled card control approximated by the random-time same-side twin (same UTC day) |
| S11-V0 | all 13 | 1d | A2 auto | 3268 | `0dec7ba5cfe3` | published 3m rates via asof on available_utc (FRED IR3TIB01*/DTB3); markup m = 2.5 %/yr fallback (no measured swap); crash guard and vol-target sizing omitted (sizing does not change R) |
| S11-V1 | all 13 | 1d | A2 auto | 3409 | `9c29c2f7cced` | published 3m rates via asof on available_utc (FRED IR3TIB01*/DTB3); markup m = 2.5 %/yr fallback (no measured swap); crash guard and vol-target sizing omitted (sizing does not change R) |
| S11-V2 | all 13 | 1d | A2 auto | 1771 | `1dae294a4bb4` | published 3m rates via asof on available_utc (FRED IR3TIB01*/DTB3); markup m = 2.5 %/yr fallback (no measured swap); crash guard and vol-target sizing omitted (sizing does not change R); Carver forecast, Friday rebalance, no inertia buffer |
| S15-V0 | all 15 | 1d | A2 auto | 618 | `e44ab7692100` | month-end rank per class (commodity XAU/XAG/XTI, index 5, FX 7 USD pairs by currency); exit and re-rank at the next month-end (a held leg pays a round trip each month: conservative); inverse-vol weights, vol scaling and the book drawdown pause do not change per-trade R and are omitted |
| S15-V1 | all 15 | 1d | A2 auto | 640 | `ef82ce057e20` | month-end rank per class (commodity XAU/XAG/XTI, index 5, FX 7 USD pairs by currency); exit and re-rank at the next month-end (a held leg pays a round trip each month: conservative); inverse-vol weights, vol scaling and the book drawdown pause do not change per-trade R and are omitted; 6-1 |
| S15-V2 | all 15 | 1d | A2 auto | 505 | `eb590d866e55` | month-end rank per class (commodity XAU/XAG/XTI, index 5, FX 7 USD pairs by currency); exit and re-rank at the next month-end (a held leg pays a round trip each month: conservative); inverse-vol weights, vol scaling and the book drawdown pause do not change per-trade R and are omitted; longs only when 12m return > DTB3 |
| S16-V0 | all 25 | 1d | A2 auto | 1340 | `0d2852e992fe` | month-end rank per class (commodity XAU/XAG/XTI, index 5, FX 7 USD pairs by currency); exit and re-rank at the next month-end (a held leg pays a round trip each month: conservative); inverse-vol weights, vol scaling and the book drawdown pause do not change per-trade R and are omitted; lookback restarts at source cutovers (segments run separately) |
| S16-V1 | all 25 | 1d | A2 auto | 618 | `28e3e3b4e1cc` | month-end rank per class (commodity XAU/XAG/XTI, index 5, FX 7 USD pairs by currency); exit and re-rank at the next month-end (a held leg pays a round trip each month: conservative); inverse-vol weights, vol scaling and the book drawdown pause do not change per-trade R and are omitted; lookback restarts at source cutovers (segments run separately) |
| S17-V0 | all 22 | 1d | random_time | 625 | `bb40718b4307` | monthly return history from the long daily files (raw/daily/long), futures-sourced (yahoo =F) months masked; trades on the Dukascopy 1d bars; random-time same-side twin stands for the month-shuffle control |
| S17-V1 | all 22 | 1d | random_time | 1194 | `cb2a301fcf1d` | monthly return history from the long daily files (raw/daily/long), futures-sourced (yahoo =F) months masked; trades on the Dukascopy 1d bars; random-time same-side twin stands for the month-shuffle control |
| S17-V2 | all 22 | 1d | random_time | 599 | `5bad50136f0f` | monthly return history from the long daily files (raw/daily/long), futures-sourced (yahoo =F) months masked; trades on the Dukascopy 1d bars; random-time same-side twin stands for the month-shuffle control |
| S20-V0 | all 16 | 1d | A2 auto | 2756 | `47af8c062aed` | daily OHLC fills for every instrument (gold too: no 1m path), a bar touching both OCO triggers = full loss; breakeven move and Crabel reversal entry omitted; cost gate range >= 4 x round trip |
| S20-V1 | all 16 | 1d | A2 auto | 7500 | `ed0ba8fa19f1` | daily OHLC fills for every instrument (gold too: no 1m path), a bar touching both OCO triggers = full loss; breakeven move and Crabel reversal entry omitted; cost gate range >= 4 x round trip |
| S20-V2 | all 16 | 1d | A2 auto | 4418 | `cdac8ce71433` | daily OHLC fills for every instrument (gold too: no 1m path), a bar touching both OCO triggers = full loss; breakeven move and Crabel reversal entry omitted; cost gate range >= 4 x round trip |
| S20-V3 | all 16 | 1d | A2 auto | 3730 | `7e0817888000` | daily OHLC fills for every instrument (gold too: no 1m path), a bar touching both OCO triggers = full loss; breakeven move and Crabel reversal entry omitted; cost gate range >= 4 x round trip |
| S21-V0 | all 16 | 1d | A2 auto | 53850 | `ddcb112c4584` | continuous CFD 1d bars: day t open = close of t-1; exit at the close of the trigger day (= next open); tier-1 news blackout not applied |
| S21-V1 | all 16 | 1d | A2 auto | 53850 | `b9307edd9050` | continuous CFD 1d bars: day t open = close of t-1; exit at the close of the trigger day (= next open); tier-1 news blackout not applied |
| S21-V2 | all 16 | 1d | A2 auto | 53850 | `a6d73896179c` | continuous CFD 1d bars: day t open = close of t-1; exit at the close of the trigger day (= next open); tier-1 news blackout not applied |
| S21-V3 | all 16 | 1d | A2 auto | 53882 | `c620dd9f627f` | continuous CFD 1d bars: day t open = close of t-1; exit at the close of the trigger day (= next open); tier-1 news blackout not applied |
| S22-V0 | all 16 | 1d | A2 auto | 444 | `ffe5c540b0ff` | breakeven move omitted; stop = EMA20 at the release bar, at least 0.3 ATR from the trigger |
| S22-V2 | all 16 | 1d | A2 auto | 510 | `209e57eb97f4` | breakeven move omitted; stop = EMA20 at the release bar, at least 0.3 ATR from the trigger; BandWidth at its 120-bar low within the last 6 bars, close outside the band |
| S22-V3 | all 16 | 4h | A2 auto | 2468 | `17804cef89d1` | breakeven move omitted; stop = EMA20 at the release bar, at least 0.3 ATR from the trigger; 4h |
| S23-V0 | all 16 | 1d | A2 auto | 695 | `055ac3cae160` | daily bars cannot order the sweep and the reclaim inside one day: V0 enters at the next open when the day CLOSED back above the old low + 0.1 ATR; V1 adds a next-day buy stop; trailing stop omitted |
| S23-V1 | all 16 | 1d | A2 auto | 1870 | `785c9e14602b` | daily bars cannot order the sweep and the reclaim inside one day: V0 enters at the next open when the day CLOSED back above the old low + 0.1 ATR; V1 adds a next-day buy stop; trailing stop omitted |
| S23-V2 | all 16 | 1d | A2 auto | 862 | `a3fff266b6d8` | daily bars cannot order the sweep and the reclaim inside one day: V0 enters at the next open when the day CLOSED back above the old low + 0.1 ATR; V1 adds a next-day buy stop; trailing stop omitted |
| S27-V0 | all 7 | 1d | A2 auto | 5461 | `80dd0aa1077e` | position size |S| and inverse-vol do not change R; card control (un-gated always-in) not computed: the A2 random-side twin is the gate |
| S27-V1 | all 7 | 1d | A2 auto | 6122 | `8c79a6ca1bae` | position size |S| and inverse-vol do not change R; card control (un-gated always-in) not computed: the A2 random-side twin is the gate |
| S27-V2 | all 7 | 1d | A2 auto | 2924 | `87e86bae886b` | position size |S| and inverse-vol do not change R; card control (un-gated always-in) not computed: the A2 random-side twin is the gate |
| S30-V0 | all 7 | 1d | A2 auto | 380 | `07aef283fcc0` | currencies ranked vs USD on the 7 USD pairs (crosses not used); spot return only (no swap accrual in the score) |
| S30-V1 | all 7 | 1d | A2 auto | 380 | `8dd6d1ed0726` | currencies ranked vs USD on the 7 USD pairs (crosses not used); spot return only (no swap accrual in the score) |
| B04-V0 | all 16 | 30m 1h 4h | A2 auto | 4743 | `0c6e201c56ca` | gold grid $50; non-gold grid = 10^(floor(log10 p)-1) (FX: -2); daily-or-coarser bars use OHLC fills, stop first when a bar touches stop and target |
| B04-V1 | all 16 | 30m 1h 4h | A2 auto | 1157 | `a5a9e1e1bead` | close-confirmed |
| B04-V3 | all 16 | 30m 1h 4h | A2 auto | 3498 | `ae6382904867` | ADX14 > 20 on the bar's own TF (not 4h)  |
| B05-V1 | all 16 | 1h 4h 1d | A2 auto | 3114 | `8f0f9707c41b` |  |
| B07-V0 | all 16 | 1h 4h 1d 1w | A2 auto | 1800 | `5668e5306db2` |  |
| B07-V1 | all 16 | 1h 4h 1d 1w | A2 auto | 1443 | `9cda9ec0647b` |  |
| B07-V3 | all 16 | 1h 4h 1d 1w | A2 auto | 1575 | `3f0204786e0f` |  |
| F02-V1 | all 16 | 30m 1h 4h 1d | A2 auto | 4846 | `7cedf4834765` |  |
| F03-V0 | all 16 | 30m 1h 4h | A2 auto | 54583 | `4fbb903bacc5` |  |
| F03-V1 | XAUUSD | 30m 1h 4h | A2 auto | 1639 | `b4cc5f564f02` | gold $100 grid |
| F03-V2 | all 16 | 30m 1h 4h | A2 auto | 31699 | `cf386bc247f7` |  |
| F03-V3 | all 16 | 30m 1h 4h | A2 auto | 38123 | `01d0038d0197` | 07-21 UTC |
| F05-V0 | all 16 | 1d | A2 auto | 4179 | `778e28b51c07` | daily bars only (no 1h path): buy stop at the prior low valid one day; stop 0.1 ATR below an assumed sweep of 1 ATR; trail variant not built |
| F05-V2 | all 16 | 1d | A2 auto | 1321 | `85c659f26420` | daily bars only (no 1h path): buy stop at the prior low valid one day; stop 0.1 ATR below an assumed sweep of 1 ATR; trail variant not built |
| F05-V3 | all 16 | 1d | A2 auto | 1589 | `396334de2a6f` | daily bars only (no 1h path): buy stop at the prior low valid one day; stop 0.1 ATR below an assumed sweep of 1 ATR; trail variant not built |
| F07-V0 | all 16 | 1d | A2 auto | 4326 | `cc6567e4dc4e` |  |
| F07-V1 | all 16 | 1d | A2 auto | 1987 | `2945e9415d60` |  |
| F07-V2 | all 16 | 1d | A2 auto | 2222 | `3af58c9a6ae7` |  |
| F07-V3 | all 16 | 1d | A2 auto | 2654 | `288d6888f5f5` |  |
| F08-V0 | all 16 | 4h 1d 1w | A2 auto | 2046 | `686d4bfc5a8c` |  |
| F08-V1 | all 16 | 4h 1d 1w | A2 auto | 1253 | `8cc58cbbbec7` |  |
| F08-V2 | all 16 | 4h 1d 1w | A2 auto | 1239 | `43225840cef4` |  |
| F09-V0 | all 16 | 1h 4h 1d | A2 auto | 7138 | `93a633d540c8` |  |
| F09-V1 | all 16 | 1h 4h 1d | A2 auto | 3009 | `286c38660da5` |  |
| F09-V2 | all 16 | 1h 4h 1d | A2 auto | 1427 | `6080637e8bb8` |  |
| F09-V3 | all 16 | 1d | A2 auto | 849 | `2e4ea966abe5` |  |
| C02-V0 | all 16 | 1h 4h 1d 1w | A2 auto | 1079 | `9ac68215aabf` | depth 2 ATR on every TF (10 % weekly rule not applied) |
| C03-V0 | all 16 | 15m 30m 1h 4h 1d | A2 auto | 2226 | `05b304dd6c18` | pole ends at a confirmed ZZ pivot; flag lines OLS on highs/lows |
| C03-V1 | all 16 | 15m 30m 1h 4h 1d | A2 auto | 2226 | `6e69228d7c1e` | pole ends at a confirmed ZZ pivot; flag lines OLS on highs/lows |
| C04-V0 | all 16 | 4h 1d 1w | A2 auto | 8398 | `0861bc8bb9ca` | LMW extrema with a rule-of-thumb bandwidth (not CV-optimal); 8 non-rectangle patterns (HS/IHS/BTOP/BBOT/TTOP/TBOT/DTOP/DBOT) |
| C04-V1 | all 16 | 4h 1d 1w | A2 auto | 8311 | `9d0be4fd86c7` | LMW extrema with a rule-of-thumb bandwidth (not CV-optimal); 8 non-rectangle patterns (HS/IHS/BTOP/BBOT/TTOP/TBOT/DTOP/DBOT) |
| C04-V2 | all 16 | 4h 1d 1w | A2 auto | 4886 | `6c5a99914b85` | LMW extrema with a rule-of-thumb bandwidth (not CV-optimal); 8 non-rectangle patterns (HS/IHS/BTOP/BBOT/TTOP/TBOT/DTOP/DBOT) |
| C05-V0 | all 16 | 1h 4h 1d | A2 auto | 6104 | `c4a41b645617` |  |
| C05-V2 | all 16 | 1h 4h 1d | A2 auto | 6104 | `2e615d54b76c` |  |
| C09-V2 | all 16 | 1h 4h 1d | A2 auto | 2197 | `c20630fe45cc` |  |
| C10-V1 | all 16 | 1h 4h 1d | A2 auto | 2928 | `38682dbdd9d6` |  |
| NULL-RAND-5m | XAUUSD | 5m | A2 auto | 5870 | `4cca2d28e914` | random-side entries at 1% of bars (hash of the bar time), stop 1 ATR14, target 2 ATR14, hold 10; rate set so the null can reach its n floor |
| NULL-RAND-15m | XAUUSD | 15m | A2 auto | 1890 | `aba5082c05fb` | random-side entries at 1% of bars (hash of the bar time), stop 1 ATR14, target 2 ATR14, hold 10; rate set so the null can reach its n floor |
| NULL-RAND-30m | XAUUSD | 30m | A2 auto | 965 | `fd2e23f4d634` | random-side entries at 2% of bars (hash of the bar time), stop 1 ATR14, target 2 ATR14, hold 10; rate set so the null can reach its n floor |
| NULL-RAND-1h | XAUUSD | 1h | A2 auto | 499 | `c080c80205f7` | random-side entries at 4% of bars (hash of the bar time), stop 1 ATR14, target 2 ATR14, hold 10; rate set so the null can reach its n floor |
| NULL-RAND-4h | all 16 | 4h | A2 auto | 1627 | `23e3ac6b23e7` | random-side entries at 1% of bars (hash of the bar time), stop 1 ATR14, target 2 ATR14, hold 10; rate set so the null can reach its n floor |
| NULL-RAND-1d | all 16 | 1d | A2 auto | 277 | `5df74d95c396` | random-side entries at 3% of bars (hash of the bar time), stop 1 ATR14, target 2 ATR14, hold 10; rate set so the null can reach its n floor |
| NULL-RAND-1w | all 16 | 1w | A2 auto | 46 | `f5f9bf0b2a2c` | random-side entries at 20% of bars (hash of the bar time), stop 1 ATR14, target 2 ATR14, hold 10; rate set so the null can reach its n floor |
| NULL-LONGCLOSE-US500 | US500 | 1d | A2 auto | 1449 | `3ec21ac6f5d0` | buy every 1d close, exit next close, catastrophe stop 2.5 ATR20 (must FAIL explore) |
| NULL-LONGCLOSE-IDX | all 5 | 1d | A2 auto | 6522 | `0d60f8e5ad9e` | buy every 1d close, exit next close, catastrophe stop 2.5 ATR20 (must FAIL explore) |
| NULL-LONGCLOSE-GOLD | XAUUSD | 1d | A2 auto | 2077 | `779df1308070` | buy every 1d close, exit next close, catastrophe stop 2.5 ATR20 (must FAIL explore) |
| NULL-XS-RANDOM | all 25 | 1d | A2 auto | 1384 | `e77731e03a63` | month-end cross-sectional book ranked by a hash of (instrument, month): thirds long/short |
<!-- UNITS:END -->

## 10. Not run (no K)

<!-- NOTRUN:BEGIN -->
| id | reason (no K) |
|---|---|
| S01 | closed 11-07 (RSI2/Double-7s failed TEST-OLD) |
| S09 | data-gated: needs a non-US tier-1 calendar (calendar-v1 is US only) |
| S10 | pre-gate (card, no K): no measured per-pair swap; the 2.5 %/yr fallback markup exceeds the 1.5 %/yr gate |
| S12 | n-infeasible: 1-3 trades/yr on one instrument cannot reach the n >= 300 explore floor |
| S13 | data-gated: no futures expiry calendar / roll log (basis not computable) |
| S14 | data-gated: no swap history and no commodity/index roll-yield carry proxy |
| S18 | n-infeasible: 2 trades/yr (16 in explore) vs n >= 300; needs its own low-N card |
| S19 | n-infeasible: 1 trade/yr/index vs n >= 300; needs its own low-N card |
| S25 | n-infeasible: 4-8 trades/yr per pair vs n >= 300 |
| S28 | data-gated: CPI vintages not local |
| S29 | n-infeasible: 2-5 events/yr vs n >= 300; needs its own low-N card |
| S26-V1 | data-gated: VIX not local |
| S26-V2 | data-gated: VIX not local |
| S07-V2 | identical to V0 in R (vol-scaled notional): not a separate unit |
| S15-V3 | not built: weekly 4-tranche book needs overlapping positions per instrument |
| S30-V2 | not built: weekly 4-tranche book |
| S16-sub | the card's 'XS gross pass / net fail -> rerun with S14 swap filter' sub-read is not run (S14 data-gated) |
| F06 | n-infeasible on 23h CFD bars: no daily opening gap beyond the prior extreme except weekends; needs cash-session bars |
| F05-V1 | not built: 1h trail exit |
| C01-V2 | not built: 1R partial + trail |
| C10-V2 | not built: 1d trend filter on 1h/4h (multi-TF join) |
| C03-V3 | not built: x4 higher-TF SMA50 (multi-TF join) |
| B08-V3 | card: drop when no consensus data (none local) |
| B01/B02/B03/B06/F01 | duplicates, screened once under S16/S20/S21/S22/S23 |
| B04-V2 | n-infeasible: 159 explore events < its n floor (counts only, no returns) |
| B05-V0 | n-infeasible: 224 explore events < its n floor (counts only, no returns) |
| B05-V2 | n-infeasible: 224 explore events < its n floor (counts only, no returns) |
| B05-V3 | n-infeasible: 85 explore events < its n floor (counts only, no returns) |
| B07-V2 | n-infeasible: 318 explore events < its n floor (counts only, no returns) |
| B08-V0 | n-infeasible: 638 explore events < its n floor (counts only, no returns) |
| B08-V1 | n-infeasible: 668 explore events < its n floor (counts only, no returns) |
| B08-V2 | n-infeasible: 456 explore events < its n floor (counts only, no returns) |
| B09-V0 | n-infeasible: 165 explore events < its n floor (counts only, no returns) |
| B09-V1 | n-infeasible: 77 explore events < its n floor (counts only, no returns) |
| B09-V2 | n-infeasible: 17 explore events < its n floor (counts only, no returns) |
| B09-V3 | n-infeasible: 126 explore events < its n floor (counts only, no returns) |
| B10-V0 | n-infeasible: 969 explore events < its n floor (counts only, no returns) |
| B10-V1 | n-infeasible: 582 explore events < its n floor (counts only, no returns) |
| B10-V2 | n-infeasible: 905 explore events < its n floor (counts only, no returns) |
| B10-V3 | n-infeasible: 571 explore events < its n floor (counts only, no returns) |
| C01-V0 | n-infeasible: 282 explore events < its n floor (counts only, no returns) |
| C01-V1 | n-infeasible: 177 explore events < its n floor (counts only, no returns) |
| C01-V3 | n-infeasible: 118 explore events < its n floor (counts only, no returns) |
| C02-V1 | n-infeasible: 540 explore events < its n floor (counts only, no returns) |
| C02-V2 | n-infeasible: 541 explore events < its n floor (counts only, no returns) |
| C02-V3 | n-infeasible: 178 explore events < its n floor (counts only, no returns) |
| C03-V2 | n-infeasible: 566 explore events < its n floor (counts only, no returns) |
| C04-V3 | n-infeasible: 75 explore events < its n floor (counts only, no returns) |
| C05-V1 | n-infeasible: 219 explore events < its n floor (counts only, no returns) |
| C05-V3 | n-infeasible: 207 explore events < its n floor (counts only, no returns) |
| C06-V0 | n-infeasible: 636 explore events < its n floor (counts only, no returns) |
| C06-V1 | n-infeasible: 954 explore events < its n floor (counts only, no returns) |
| C06-V2 | n-infeasible: 192 explore events < its n floor (counts only, no returns) |
| C06-V3 | n-infeasible: 126 explore events < its n floor (counts only, no returns) |
| C07-V0 | n-infeasible: 56 explore events < its n floor (counts only, no returns) |
| C07-V1 | n-infeasible: 372 explore events < its n floor (counts only, no returns) |
| C07-V2 | n-infeasible: 343 explore events < its n floor (counts only, no returns) |
| C07-V3 | n-infeasible: 19 explore events < its n floor (counts only, no returns) |
| C08-V0 | n-infeasible: 0 explore events < its n floor (counts only, no returns) |
| C08-V1 | n-infeasible: 4 explore events < its n floor (counts only, no returns) |
| C08-V2 | n-infeasible: 0 explore events < its n floor (counts only, no returns) |
| C08-V3 | n-infeasible: 0 explore events < its n floor (counts only, no returns) |
| C09-V0 | n-infeasible: 257 explore events < its n floor (counts only, no returns) |
| C09-V1 | n-infeasible: 0 explore events < its n floor (counts only, no returns) |
| C09-V3 | n-infeasible: 8 explore events < its n floor (counts only, no returns) |
| C10-V0 | n-infeasible: 152 explore events < its n floor (counts only, no returns) |
| C10-V3 | n-infeasible: 54 explore events < its n floor (counts only, no returns) |
| F02-V0 | n-infeasible: 320 explore events < its n floor (counts only, no returns) |
| F02-V2 | n-infeasible: 99 explore events < its n floor (counts only, no returns) |
| F02-V3 | n-infeasible: 236 explore events < its n floor (counts only, no returns) |
| F04-V0 | n-infeasible: 697 explore events < its n floor (counts only, no returns) |
| F04-V1 | n-infeasible: 56 explore events < its n floor (counts only, no returns) |
| F04-V2 | n-infeasible: 641 explore events < its n floor (counts only, no returns) |
| F04-V3 | n-infeasible: 166 explore events < its n floor (counts only, no returns) |
| F08-V3 | n-infeasible: 50 explore events < its n floor (counts only, no returns) |
| F10-V0 | n-infeasible: 47 explore events < its n floor (counts only, no returns) |
| F10-V1 | n-infeasible: 137 explore events < its n floor (counts only, no returns) |
| F10-V2 | n-infeasible: 37 explore events < its n floor (counts only, no returns) |
| F10-V3 | n-infeasible: 12 explore events < its n floor (counts only, no returns) |
| S02-V1 | n-infeasible: 567 explore events < its n floor (counts only, no returns) |
| S03-V0 | n-infeasible: 169 explore events < its n floor (counts only, no returns) |
| S03-V1 | n-infeasible: 65 explore events < its n floor (counts only, no returns) |
| S03-V2 | n-infeasible: 112 explore events < its n floor (counts only, no returns) |
| S03-V3 | n-infeasible: 129 explore events < its n floor (counts only, no returns) |
| S04-V2 | n-infeasible: 773 explore events < its n floor (counts only, no returns) |
| S04-V3 | n-infeasible: 230 explore events < its n floor (counts only, no returns) |
| S05-V0 | n-infeasible: 103 explore events < its n floor (counts only, no returns) |
| S05-V1 | n-infeasible: 70 explore events < its n floor (counts only, no returns) |
| S16-V2 | n-infeasible: 66 explore events < its n floor (counts only, no returns) |
| S16-V3 | n-infeasible: 78 explore events < its n floor (counts only, no returns) |
| S22-V1 | n-infeasible: 14 explore events < its n floor (counts only, no returns) |
| S23-V3 | n-infeasible: 380 explore events < its n floor (counts only, no returns) |
| S24-V0 | n-infeasible: 80 explore events < its n floor (counts only, no returns) |
| S24-V1 | n-infeasible: 178 explore events < its n floor (counts only, no returns) |
| S24-V2 | n-infeasible: 10 explore events < its n floor (counts only, no returns) |
| S24-V3 | n-infeasible: 792 explore events < its n floor (counts only, no returns) |
| S26-V0 | n-infeasible: 288 explore events < its n floor (counts only, no returns) |
<!-- NOTRUN:END -->

## 11. Deviations from the card texts (read before the verdict)

Implementation simplifications are listed per unit (`notes` column above, `impl_notes` in specs.yaml). The ones that
matter most:
- Sizing rules (vol targets, inverse-vol weights, |S| scaling, drawdown pauses) are not simulated: the screen reads
  per-trade R, which sizing does not change. Variants that differ ONLY in sizing (S07 V2) are not units.
- Daily-or-coarser fills use the bar's OHLC with stop-first when a bar touches stop and target, and a full loss when
  both OCO triggers sit inside one bar (S20/S21/S22). Gold daily rules are NOT simulated on the 1m path.
- Breakeven moves and trailing stops (S20, S22, S23, S24, F05 V1, C01 V2) are omitted; S16 TS exits on a close below
  the prior 20-day low instead of trailing.
- S02/S04 build the cash session from 4h CFD bars (US 12-20 UTC, EU 08-16 UTC): approximate by one hour in part of
  the year (DST); the card allows this as the "approximate" cell.
- S23 V0 on daily bars enters at the next open when the sweep day closed back above the old low (the same-day
  stop fill cannot be ordered inside a daily bar).
- Card-internal controls of S06/S07/S08/S17 are implemented as the random-time same-side twin (same instrument,
  nearby time), not as the exact non-event-day / odd-week / time-shuffle / month-shuffle constructions.
- XS books (S15/S16/S17/S30) close and reopen each leg at every month-end (a held leg pays one round trip a month:
  conservative for cost; immaterial for 1-month holds at these costs).
- Tick volume (B05 V3, C02 V2) is the Dukascopy tick count (gold) / bar volume (non-gold).

## 12. Flags (owner / director decisions, not taken here)

- S10 (carry) is NOT started by its own pre-gate: no measured per-pair swap exists; the 2.5 %/yr fallback is above the
  1.5 %/yr gate. The Sunday 22:00 read-only swap pull (plan) can start it next quarter, under a new card.
- S12/S18/S19/S25/S29 cannot reach the plan's n floor (low-N cards). The catalogue's own low-N rule (day-block
  bootstrap, card N floors) conflicts with the plan's n >= 300; resolving it is a research-director decision with
  the quant, and each would need its own card and K.
- S09/S13/S14/S26 V1-V2/S28 are data-gated (non-US calendar, futures roll log, swap history, VIX, CPI vintages).
- No research-only instrument is in any pooled unit; enabling one stays an owner decision.

## 13. Signature

- Pre-registration commit: (this file's commit)
- Harness commit: `b3f14388` (code under goldbot/research/catalogue; the run refuses any other)
- quant-reviewer: ______________________ (UTC time) ______

## 14. Amendments before the run (quant sign-off 2026-11-07 09:45 UTC, SIGN-WITH-CHANGES)

Landed 2026-11-07 before any return was computed. They change no K, registry row, unit, config hash, threshold or
window. Harness re-frozen at `b3f14388ff0ffb10a0596d0b09465a47bd823f08`; specs.yaml unchanged (sha256 `87975f06...2211256`).

- **C1 null void rule** (stats.null_void, run.cmd_explore; test_null_void_rule): as in section 8. Reason: four of
  the eleven nulls cannot reach their n floor and the three LONGCLOSE units are drift sentinels, so the original
  bound (void at 4 of 11) only fired if all four feasible nulls passed.
- **C2 overlapping holds** (stats.overlap_lag, stats.hac_t, run._window_work; tests test_hac_t_on_overlapping_holds,
  test_overlap_lag_from_holds): L = max(1, ceil(median nights held x fill days / calendar span in days)); the gated t
  (explore gate 1 and TEST-OLD) is the Newey-West t; bootstrap block = max(5, L). Deviation from the sign-off's
  "lag L": the Bartlett kernel uses bandwidth 2L, because a Bartlett window of L understates an MA(L) long-run
  variance by about 30 % (t about 20 % too large) and fails the sign-off's own 15 % test; 2L passes it. At L = 1 the t
  is the plain t exactly.
- **C3 sprint3000 fixture** (test_sim_reproduces_sprint3000_fixture): a gap through the stop (filled at the open), a
  short target hit and a Thursday-22:00 to Monday-21:00 hold; gross R equals sprint3000.engine.walk exactly; costs
  are checked against hand values (the conventions differ: sprint3000 uses a bps-scaled round trip and a Wednesday
  triple swap; the catalogue uses the USD cost of record and one swap per 21:00 UTC rollover crossed). The test
  found a defect, fixed here: an exit exactly AT 21:00 UTC was charged that night's rollover (one extra swap night
  on every daily-close exit; conservative, about 0.7 bp a trade). Nights now count rollovers strictly before the
  exit.
- **C4 S17 seasonal history**: outside the TEST-OLD read, monthly returns of 2006-02..2009-12 are masked from the
  same-month history (test_s17_history_masks_test_old_months: changing any monthly return in that span leaves the
  explore events unchanged). S17 explore events recounted (counts only): V0 625 -> 646, V1 1,194 -> 1,054,
  V2 599 -> 536; all stay above their 300 floor. The t >= 1.645 fallback is not needed.
- **T1 feasibility drop**: ruled sound (counts only; events bound trades). The 73 dropped variants are closed with
  this line under the kill rule. They return only under a new card counted in K, never as "untested".
- **T2 low-N conflict**: the plan floor governs this screen. S12/S18/S19/S25/S29 are not run and carry no K. A later
  low-N card needs its own K and floors and must state that at 1-8 trades a year it cannot meet the DESIGN gate
  (DSR >= 0.95 on 200+ trades), so its ceiling is a demo learning arm or a feature; the quant recommends the
  research-director deprioritise them.
- **T3 card deviations**: accepted and disclosed; the verdict reads "failed as implemented". Stop-first and the OCO
  full loss on daily OHLC are conservative (about -0.05 to -0.15 R a trade on S20/S21); no 1m path for gold daily is
  conservative; no breakeven/trail goes either way; the S02/S04 4h cash session carries up to 1 h of DST error
  (noise, no look-ahead). A fail of S06/S07/S17 is a fail against an attenuated nearby-time control; a pass is
  valid. The kill rule still applies.
- **T4 warm-up**: not contamination of TEST-OLD apart from S17 (C4). TEST-OLD is also reported excluding 2009 for
  S11/S15/S16/S27/S30 (reported, not gated).
- **T5 control choice**: sound (side share only, fixed on explore, reused).
- **T6**: section 6 twin text corrected to the code's behaviour.
- **T7 re-freeze**: new harness commit above; `catalogue-units-2026-11-07.json` `harness_commit` updated; enumerate
  was NOT re-run; Q4 registry rows = 3,263 after the re-freeze (checked); the 90 pre-registration rows (plan field
  `harness_commit` = 87771d81) carry over.
- Also recorded: passing this screen does NOT meet the DESIGN gates for real money (3 positive years including
  2021-22, DSR >= 0.95 on 200+ trades); WS-5 cards are demo learning arms only.

### 14a. Amendment 2026-11-13: gold cost by session (BACKLOG 146, quant review of 4571b28d)

Dated after the run; it applies **only to catalogue runs at or after the commit that lands it** (branch
feat/gold-cost-session-split). Every earlier catalogue read keeps the basis of section 5 ($0.166/oz, ICM all
sessions; harness `b3f14388`) and is not re-labelled; a later run cites this amendment and its harness commit, and is
not compared with an earlier one without saying so. No K, registry row, unit, config hash, threshold or window changes.

- **Basis.** Gold pays the cost-of-record cell (cor-2026-10-31.2) of its fill instant's session, the dearer of the two
  brokers, floored at $0.166: asia $0.204 (ICM, 309 fills; Vantage Asia is $0.158), london $0.293 (Vantage, 44 fills,
  90% CI 0.158-0.45; ICM $0.164), newyork $0.263 (Vantage, 69 fills; ICM $0.166). A cell on fewer than 30 fills reads
  no lower than its broker's pooled cell.
- **Thin hours** (New York 17:00 to the Tokyo open: rollover, widest spreads) are in no cost-of-record cell; they pay
  the higher of the dearest session and every broker's pooled figure ($0.293).
- **Sensitivity.** Summaries also report `net_icm_basis`: net with gold on ICM's own cells (asia 0.204, london and
  newyork 0.166, thin 0.204). Reported, never gated.
- **Scope.** The dearer-broker max is a screen basis only. H-90 and deployment price each broker on its own cells,
  never the max. The EVAL-2 deciding cost ($0.230) is unchanged.
