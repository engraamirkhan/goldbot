# Pre-registration: positioning and flows (COT managed money, GLD holdings) at 1d decisions / 1-week holds

Written 2026-11-01 by the principal gold macro and flows researcher, **before any outcome of these rules was computed
on any window**. Base `3c83b6fb` (origin/main). Hypothesis rows H-86, H-87, H-88 (`hypotheses.md` A22).

**Seen before this freeze (no outcome):** file listings, sizes and sha256 of the downloaded CFTC and SPDR files; the
SPDR archive's header row and its first and last dates (2004-11-18 .. 2026-10-29); the existing PIT loader
`goldbot/data/positioning.py` and features `goldbot/features/positioning.py` (code, not data). No COT or GLD value was
printed, plotted or joined to a price. The H-84 decision machinery's event counts (2,061 daily decisions 2010-2017,
median ATR(1d) $18) are already public in `prereg-2026-11-03-h84-macro.md`.

Why this line now: every price, time and macro edge tried so far failed (`scorecard-2026-10.md`,
`discovery-v2-screen-2026-11.md`, H-84 `h84-macro-screen-2026-11.md`). R-18 named positioning and flows as the next
*new information source*. These are weekly/daily signals with 1-week holds, so the broker cost is ~1% of R; the
only cost that matters is the long swap.

## 1. Evidence (literature check, graded)

| Claim | Source | Sample, instrument | Net of costs? | Grade |
|---|---|---|---|---|
| Hedging pressure (commercials' net short) predicts futures risk premia; speculators earn a premium for taking the other side | de Roon, Nijman & Veld 2000, *Journal of Finance* 55(3) | 20 US futures incl. metals, 1986-1994, weekly COT | Gross | Replicated in commodities: Basu & Miffre 2013, *J. Banking & Finance* 37(7) (27 commodities, 1992-2011, long-short portfolios, gross) *(volume/issue unverified)*. Cross-sectional, not a single-asset timing rule |
| Short-term (weekly) *changes* in speculators' positions predict returns **negatively**: speculators demand liquidity, hedgers supply it and earn a short-horizon premium | Kang, Rouwenhorst & Tang 2020, *Journal of Finance* 75(1), "A Tale of Two Premiums" *(page/sample details unverified)* | 26 US commodity futures, ~1994-2014, weekly COT | Gross | Single major paper for the change-contrarian sign; cross-sectional; gold not reported separately (unverified) |
| Speculator sentiment from COT predicts *continuation*, hedger sentiment reversal | Wang 2001/2003, *Journal of Futures Markets* 23(1) 1-31, https://ideas.repec.org/a/wly/jfutmk/v23y2003i1p1-31.html | 15 US futures (6 agricultural in the sentiment version), 1990s | Gross | Single paper; **sign conflicts with Kang et al.**; abstract says speculators are contrarian traders after risk controls |
| Reporting traders' positions do not predict weekly energy futures returns; positions follow prices | Sanders, Boris & Manfredo 2004, *Energy Economics* 26(3) 425-445 | Crude, heating oil, gasoline, natgas, 1992-2003 | n/a | Replicated in direction (Büyükşahin & Harris 2011, *Energy Journal* 32(2), crude, Granger price -> positions) *(issue unverified)* |
| In precious metals, returns drive managed-money positions (positive feedback), not the reverse | PolyU thesis, https://theses.lib.polyu.edu.hk/handle/200/7971 *(author, year unverified)* | COMEX gold, silver, platinum, palladium, disaggregated COT | n/a | Thesis level (E); gold-specific; **against** any MM signal |
| Practitioner rule: MM net long > ~200k contracts marks correction risk; extremes persist in trends | MetalCharts, https://metalcharts.org/cot/gold ; jinlow substack | gold | No test | Vendor (F) |
| GLD/ETP holdings changes Granger-cause gold spot post-2008 only; no causality in the full sample | Aalto thesis 2015, https://epub.lib.aalto.fi/en/ethesis/id/14319 | physically backed ETPs, gold, 2000s-2014 | n/a | Thesis (E) |
| Two-way feedback between GLD flows and gold returns | Taiwan thesis 2012, https://ndltd.ncl.edu.tw/handle/79415745161990386644 *(unverified)* | GLD, 2004-2011 | n/a | Thesis (E) |
| Gold price changes lead ETF flows, not the reverse | Deutsche Bank note via Tickmill, https://www.tickmill.com/blog/institutional-insights-deutsche-bank-who-drives-gold-prices *(original note unseen)* | gold ETFs, recent years | n/a | Sell-side (E/F); **against** a flow-leads-price rule |

**Reading of the evidence.** The only peer-reviewed, replicated positioning result is the *cross-sectional* hedging
pressure premium; there is no replicated single-asset timing result for gold. The weekly-change sign is contested
(Kang et al. contrarian vs Wang continuation), and gold-specific work says positions and ETF flows mostly *follow*
price. Prior P(any variant passes) ~0.12. This is a cheap, honest test of a new source, not a high-conviction bet.

## 2. Frozen inputs (point in time)

- **Bars:** `raw/xauusd_1m_dukascopy_2010..2017.parquet` only (local; loader asserts no bar at or after 2018-01-01),
  resampled to 4h (`drop_late=True`) and feature-day, exactly as H-84 (`scripts/research/h84_macro_screen.py`).
- **COT:** CFTC disaggregated futures-only, `fut_disagg_txt_hist_2006_2016.zip` (sha256 `68c593209136...`) and
  `fut_disagg_txt_2017.zip` (`6fc467b989df...`), downloaded 2026-11-01 to `~/tmp/positioning/raw/` (never committed).
  Parsed and stamped by the existing loader, unchanged: `goldbot.data.positioning.parse_cot` + `cot_frame` (COMEX gold
  088691; positions as of Tuesday; `available_utc` = Friday 15:30 ET, next US business day in a federal-holiday week,
  never before the 2013 shutdown floor 2013-11-30). Known accepted look-ahead: the history files hold corrected
  values (loader docstring); revisions are rare and small.
- **GLD:** SPDR historical archive (xlsx from the issuer's archive endpoint, sha256 `7fd2326e38d8...`, converted to
  CSV with the header row unchanged), parsed by `parse_gld_csv` + `gld_frame`, unchanged: tonnes at 4:15 pm NYT on
  value_date, `available_utc` = next US business day 14:00 UTC. Latest-vintage file (holdings are essentially never
  revised; disclosed).
- Every value reaches a decision only through `goldbot.data.store.asof_join` on `available_utc` at the **decision
  bar's close**. Rows with `available_utc` >= 2018-01-01 are dropped before any computation (asserted).

## 3. Common mechanics (all three variants)

- **Decision bar:** each stub-free feature-day's first 4h bar closing at or after the 13:30 New York settlement
  (H-01/H-84 `_visible_on`), closes 2010-01-01 .. before 2017-12-01.
- **Entry:** that bar's mid close. **R** = 1.5 x ATR(1d) (Wilder 14, stub-free daily mid bars, as of the decision bar),
  frozen for the trade.
- **Exit:** protective stop at 1 R (filled at the worse of the stop and the bar's open: gaps), checked on 4h mid bars;
  otherwise **time exit at the close of the decision bar five decision days later** (about one trading week). No
  target. A trade whose fifth decision bar lies past the data is closed at the last bar (`truncated`).
- **One position at a time per variant.**
- **Costs (ADR 0064, pinned `cost_of_record: cor-2026-10-31.2`, broker `icm`, session `newyork`):** round trip
  `charged_round_trip_usd("icm","newyork")` = max(0.23, upper 90% 0.194) = **$0.23/oz** every trade, plus
  `overshoot_charge_usd("icm","newyork")` = **$0.196/oz** on every stop exit. The screen stops if the loaded table is
  not that version (`require_version`). At R ~ $27 that is ~0.009-0.016 R a trade.
- **Swap (binding prior, unmeasured):** long $0.60/oz per rollover night, short 0, x3 on Wednesday's rollover
  (Europe/Athens), via `labels.triple_barrier.rollover_nights` (H-84's). ~7 nights a week-long trade = ~$4.2/oz =
  **~0.155 R per long**. 1.5x stress: cost x1.5, long swap $0.90, short swap $0.15. Swap-free net reported, never read.

## 4. Variants (exact rules; K +3)

| Id | Rule (side at the decision bar; 0 = no trade) | Input series | Decisions | Expected trades | P4 expectation |
|---|---|---|---|---|---|
| **P1_EXT** (H-86) | x = 100 x `cot_mm_net` / `cot_open_interest` per report (first releases). pct = share of the **previous 156 reports'** x strictly below the current x (needs >= 104 previous reports). **Long if pct <= 0.10, short if pct >= 0.90.** Contrarian on crowding | cot_mm_net, cot_open_interest | weekly: only the first decision bar at which a new report is visible | ~60-90 | gross 0 to +0.08 R; P(pass) ~0.05 |
| **P2_FLOW** (H-87) | d = x - x of the previous report (percentage points). **side = -sign(d)** when \|d\| >= the 2/3 point of the previous 52 reports' \|d\| (share of them below \|d\| >= 2/3; needs >= 40). Contrarian on speculators' weekly flow (Kang, Rouwenhorst & Tang 2020) | same | weekly, as P1 | ~110-135 | gross 0 to +0.06 R; P(pass) ~0.04 |
| **P3_ETF** (H-88) | g = 100 x (tonnes / tonnes 5 observations earlier - 1) on GLD first releases. **side = sign(g)** when \|g\| >= the 2/3 point of the previous 252 observations' \|g\| (needs >= 126). Flow momentum (persistent ETF demand) | gld_tonnes | daily (every decision bar, subject to one at a time) | ~150-250 | gross 0 to +0.05 R; P(pass) ~0.04 |

Not tested (and so not counted): the opposite signs (Wang continuation for P2; H-12's 3-5-day reversal for P3),
other lookbacks or thresholds, commercials' net, the legacy report. Choosing one sign each before data is the point.

## 5. Statistic and reading rule (frozen)

- **Unit:** net R per trade at the pinned cost and binding swap. Also reported: gross, 1.5x net, swap-free net, long
  and short split, per-year net, exit mix, correlation of the side with the sign of the prior 5-day return
  (descriptive: is it price reversal/momentum in disguise?).
- **Uncertainty:** circular block bootstrap over calendar weeks 2010-01-04 .. 2017-12-31 (trades to their entry
  week, empty weeks kept), block 4 weeks, B = 10,000, seed 20261101; one-sided p for net mean <= 0 as in H-84; gross
  t = gross mean / bootstrap SE.
- **Random-entry control (matched):** 2,000 draws, seed 20261102. Each draw replaces every trade of the variant by a
  trade with **the same side** entered on a decision bar drawn uniformly from **the same calendar year's** eligible
  decision bars (weekly variants: the weekly decision bars; P3: all decision bars), same exit and costs, trades
  independent (no one-at-a-time). Control statistic: the draw's mean net R. q95 = its 95th percentile.
- **Multiplicity:** Holm across the three variants at one-sided alpha 0.05.
- **PASS** (all seven): (a) n >= 50; (b) Holm-adjusted p < 0.05 on net; (c) gross t >= 2; (d) >= 5 of the 8 years
  positive (a year counts only with >= 5 trades and mean net R > 0); (e) net mean > 0 at 1.5x; (f) **net-R floor:**
  net mean >= +0.05 R; (g) net mean > the matched control's q95. Otherwise **FAIL**.
- **On FAIL:** retired (`hypotheses.md` B, with numbers); no re-parameterisation (window, threshold, hold, sign)
  without new evidence and a new id. If all three fail, COT/GLD *timing rules* at a 1-week hold are retired; the
  columns stay as PIT features (`goldbot/features/positioning.py`) for a model or gate, counted there.
- **On PASS:** one confirm per survivor on 2018-01-01 .. 2025-09-30 (s.6), run once in this lane (the brief allows
  it) and counted as one registry trial each, to be synced by the research director.

## 6. Confirm rule (written now)

Same code, data cut at 2025-10-01 (COT files 2018-2025 from the same CFTC URLs; the loader's 2018-19 and 2025
shutdown floors apply; GLD same archive). PASS if (a) net mean > 0 with week-block bootstrap one-sided p < 0.05 (no
further Holm); (b) >= 5 of 8 years positive (2025 to September counts, >= 5 trades a year); (c) net > 0 at 1.5x;
(d) net >= +0.05 R; (e) net > matched control q95. A confirm PASS earns a demo/shadow arm proposal only (sizing
below); a FAIL retires the variant.

## 7. Trial cost and multiple-testing count

- Screen: **0 registry trials, K +3** (one look per variant): K ~2,968 -> **~2,971**. Each confirm, if any: 1
  registry trial. Holdout (2018+) sealed unless a screen passes. Any survivor pays the DSR at the K of its day.

## 8. Owner-only flags (nothing assumed)

- **Sizing:** a 1.5 x ATR(1d) stop today is ~$60-90/oz; at 0.01 lot that is ~£45-70, above the 1%-of-£1,000 per-trade
  cap and near the £50 daily limit. Any survivor is demo/shadow only unless the owner changes balance or minimum size.
- **Swap:** the binding long swap is a prior, not measured; a week-long book depends on it more than on the spread.
- **Data source terms:** the SPDR archive says redistribution needs consent; it is used locally only, never committed
  or published (`data-local-only`).
