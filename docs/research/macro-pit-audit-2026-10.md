# Point-in-time audit of `macro-v1` and `positioning-v1`, and the SW-0 macro/flows feature design (2026-10-20)

Principal gold macro and flows researcher. Base `origin/main` 9a44bdcb. Status: **audit (evidence) + design draft**.
Not a pre-registration: SW-0's pre-registration is written in `preregistration-2027Q2-draft.md` by its owner (swing
lane, `candidates/swing-2026-10-17.md`); this file gives it the point-in-time (PIT) layer for the macro and flows
groups and the tradeability numbers.

**Nothing was run as a trial.** No label, no gold return and no outcome was read; no registry row, budget, setting
or pre-registration was changed; the holdout bars were not loaded. Data read: the two release files (values,
`available_utc`, `vintage`), monthly ALFRED vintages of the seven FRED series (macro values only), the legacy FRED
series DTWEXB, and two CFTC archive pages. The staleness grid is synthetic timestamps (weekdays 2010-01-04 ..
2025-09-30), no bars.

Files audited (sha256):

| Release | Asset | Published | sha256 | Rows |
|---|---|---|---|---|
| `macro-v1` | `macro_fred.parquet` | 2026-10-13 12:07 UTC (run 38394945106) | `47473d2c3f9c4b35ae4b20368cbcb1f19678e65f40e81ed755363a6c45eaa2ed` (= H-02's reference digest, `preregistration-2026Q4-pullforward.md` 2.2 item 3) | 36,192 |
| `positioning-v1` | `positioning.parquet` | 2026-10-17 09:30 UTC (run 39021326011) | `75c978a960b941312cffacbe7de79d0cee20f8a466a28e96af288503ada4777d` | 5,310 |

Code audited: `goldbot/data/macro.py` (`fred_available_utc`, `merge_vintages`, `first_release`, `driver_frames`),
`goldbot/data/positioning.py` (`cot_available_utc`, `gld_available_utc`, `merge_releases`),
`goldbot/features/macro.py` (`macro_drivers` v5), `goldbot/features/positioning.py` (`positioning` v1),
`goldbot/data/store.py::asof_join`, `scripts/fred_macro.py`, `.github/workflows/data-{macro,positioning}.yml`.

---

## 0. Bottom line

1. **No as-of violation touches any bar in H-02's window (2010-01 .. 2025-09). H-02 can be pinned and dispatched
   on the reference digest as it stands.** Two real look-aheads exist in the files, and both are measured below:
   - **DTWEXBGS revisions (finding A1).** `macro-v1` holds one vintage (2026-10-13) for the whole history, so every
     broad-dollar value is the *latest* revised value, not the one public at the time. Measured against ALFRED
     near-first releases (2019-03 .. 2026-09): 90% of values differ, the largest by 1.66 index points (p99 1.4%);
     the 20-observation change that `macro_dollar_chg20` uses correlates 0.994 with its first-release version and
     flips sign on 1.2% of days. **Small, and not linked to gold's future path** (the revisions are trade-weight
     updates). For H-02 it is recorded as a known deviation on the row, not fixed: fixing it changes the file, the
     digest and the window hash, and the effect sits in one non-cluster column of about 300.
   - **COT disaggregated history before September 2009 (finding B1).** The 168 reports dated 2006-06-13 ..
     2009-08-25 are stamped at their own Fridays, but the disaggregated report did not exist then: the CFTC first
     published it on 2009-09-04 (positions as of 2009-09-01, gold included) and released the three-year history
     after that, by 2009-12-04 at the latest. **No bar on `data-v1` (which starts 2010-01) can see the difference**:
     every one of those rows was public before the first bar. No trial has read positioning yet (`research.yml` has
     no positioning input). Fix in code anyway (floor), so a future pre-2010 data set cannot inherit it.
2. **H-01 reads no macro or positioning column** (its `research.yml` row has `macro: false`). Nothing to audit for
   it beyond the bars. H-06, the only H-01 variant that used macro, is withdrawn.
3. **Every stamp is at or after the source's real publication time.** The daily FRED stamps (next US business day
   23:00 UTC) are 1.75-2.75 h after the H.15 posting and about a day after the CBOE close for GVZ/VIX; the weekly
   dollar stamp is one day after H.10's Monday release; the COT stamp is the CFTC's Friday 15:30 ET release, moved to
   the next business day in holiday weeks and floored after the three shutdowns. The cost of this conservatism is
   staleness, not bias: at a 4h decision the daily series are a median 3.0 days old and the dollar 9.8 days (table
   in section 2). **For H-02 this matters for reading, not for validity:** H-03 (VRP) and H-07 (VIX) are screened on
   a 2-day label with a 2-3-day-old implied vol, so a null there is weak evidence against those mechanisms at 4h and
   none at 10 days.
4. **Four hygiene items (none an as-of violation on our window):** federal closure days missing from the macro
   business-day calendar (A3), no maximum age on any as-of join, so shutdown weeks feed 8-12-week-old COT as current
   (C1), no GLD rows at all (B3), and pre-2010 index backfills for GVZ and VIX (A4). Owners and dates in section 5.
5. **Tradeability (section 4.6, measured ATR, GBP 0.75 per USD):** at today's volatility a 4h trade at 0.01 lot needs
   **about £6,900** of equity under the design's 0.6% min-lot rule (about £4,150 with the 1% demo exception). **1d
   cannot be placed at any equity** while ATR(1d) is above about $44, because the £50 daily limit is projected at
   entry and one 1.5 × ATR(1d) stop is about £99. 1d becomes placeable at **£1,650** (0.6%) or **£990** (1%, demo)
   only with a **0.1 oz** minimum position, a broker and product question for the owner. 4h with 0.1 oz fits at
   £1,000 today. **Owner flags; nothing here assumes them.**

---

## 1. Release facts

| Series (release) | Source id | Rows | value_date range | Vintages in file | Revision rows | Notes |
|---|---|---|---|---|---|---|
| real_yield_10y | DFII10 (H.15) | 5,947 | 2003-01-02 .. 2026-10-08 | 1 (2026-10-13) | 0 | |
| breakeven_10y | T10YIE (FRED, = DGS10 − DFII10) | 5,948 | 2003-01-02 .. 2026-10-09 | 1 | 0 | |
| nominal_2y | DGS2 (H.15) | 5,947 | 2003-01-02 .. 2026-10-08 | 1 | 0 | |
| broad_dollar | DTWEXBGS (H.10, weekly) | 5,203 | 2006-01-02 .. 2026-10-02 | 1 | 0 | series created 2019 (A2) |
| gold_vix | GVZCLS (CBOE close) | 4,620 | 2008-06-03 .. 2026-10-09 | 1 | 0 | |
| VIXCLS | VIXCLS (CBOE close) | 6,015 | 2003-01-02 .. 2026-10-09 | 1 | 0 | |
| SP500 | SP500 (FRED) | 2,512 | 2016-10-13 .. 2026-10-12 | 1 | 0 | FRED serves ~10 years only |
| cot_* (5 series) | CFTC 088691, disaggregated futures-only | 1,062 each | 2006-06-13 .. 2026-10-13 | 1 (2026-10-17) | 0 | 13 Monday and 1 Wednesday report dates (holiday weeks) |
| gld_tonnes | SPDR archive | **0** | — | — | — | source refuses scripted clients (B3) |

DFF and DGS10 are in `FRED_SERIES` but not in the release (`MACRO_RELEASE_SERIES`); DGS10 is recoverable as
T10YIE + DFII10. `data-macro` runs Tuesdays 04:41 UTC; the 2026-10-20 run had not published when this was written,
so the reference digest is still current. H-02's window hash (pullforward 2.2 item 3) protects it from a refresh:
`merge_vintages` only appends rows stamped at or after the download day, all after 2025-10-01.

---

## 2. Lag table (every feature)

Columns: **stamp rule** = the code's `available_utc`; **source time** = when the number was really public (cited);
**margin** = stamp − source time (positive = conservative); **staleness** = decision time − value_date of the newest
visible observation, median / p95 / max in days, measured on the release with first releases and the running-max
stamp exactly as `first_release` builds them. "4h" is H-02's decision at the 4h bar close (the join is at the bar
open, `asof_join` on bars' `ts_utc`, so 4 h more conservative still); "1d" is a 13:30 New York settlement (SW-0).

### 2.1 Source series

| Series | Stamp rule (code) | Source time | Margin | Staleness 4h (med / p95 / max d) | Staleness 1d |
|---|---|---|---|---|---|
| DFII10, DGS2 | next US business day 23:00 UTC (`fred_available_utc`) | H.15 "posted daily Monday through Friday at 4:15pm" ET, previous business day's rates ([Fed H.15](https://www.federalreserve.gov/releases/h15/)); FRED posts the same afternoon | +1.75 h (EST) / +2.75 h (EDT) | 3.00 / 5.33 / 6.0 | 2.77 / 5.73 / 5.8 |
| T10YIE | same | FRED derives it from DGS10 and DFII10, posted with them | same | 3.00 / 5.33 / 6.0 | 2.77 / 5.73 / 5.8 |
| GVZCLS, VIXCLS | same | CBOE close at 16:15 ET on D *(CBOE timing unverified)*; FRED posts D+1 | ≈ +1 day vs CBOE, ≥ 0 vs FRED | 3.00 / 5.17-5.33 / 7.0 | 2.77 / 5.73 / 6.7 |
| SP500 | same | S&P close 16:00 ET on D; FRED posts D+1 | ≈ +1 day | 3.00 / 5.33 / 6.0 | 2.77 / 5.73 / 5.8 |
| DTWEXBGS | 23:00 UTC on the first US business day after the Monday following D's week (Tue; Wed after a Monday holiday) | H.10 "On Mondays at 4:15 p.m." ET for the previous business week; moved to the next business day after a Monday holiday ([Fed H.10](https://www.federalreserve.gov/releases/h10/)) | ≈ +1 day | 9.83 / 14.67 / 17.0 | 9.77 / 14.73 / 16.8 |
| COT 088691 (5 series) | Friday of the report week 15:30 ET; next US business day after that Friday in a week with a federal holiday or closure day; shutdown floors (`cot_available_utc`) | CFTC Friday 15:30 ET; holiday weeks typically the following Monday; 2019 catch-up current with the 2019-03-08 release ([CFTC 7864-19](https://www.cftc.gov/PressRoom/PressReleases/7864-19); Saxo summary [here](https://home.saxo/en-au/content/articles/commodities/cftc-releases-cot-schedule-30012019)); 2013 catch-up expected current by 2013-11-08 ([CFTC 6745-13](https://www.cftc.gov/PressRoom/PressReleases/6745-13)) | 0 normally; floors 2013-11-30, 2019-03-11, 2026-01-03 are later than the catch-up (conservative) | 8.67 / 13.33 / 81.8 | 8.77 / 13.73 / 81.7 |
| gld_tonnes | next US business day 14:00 UTC | SPDR posts the 4:15 pm NY holdings that evening *(unverified)* | ≥ +12 h | no data | no data |

Observed stamps agree with the rules: daily series lag value_date by 47 h minimum (D+1 23:00), 119 h maximum
(weekends plus a holiday); the dollar by 119-239 h, stamped on Tuesdays (5,145) or Wednesdays (58); COT on Fridays
(829), the Monday after a holiday week (206), the Tuesday when that Monday is itself the New Year holiday (6: report dates 26-27 December), and the
three shutdown floors (21 + 11 rows at 00:00 UTC). Staleness maxima above 81 days are the 2013 and 2025 shutdown
floors.

### 2.2 Derived feature columns

All are computed on their series' first releases in value_date order, with `available_utc` the running maximum, one
row per stamp, and reach bars only through `asof_join` (backward, on `available_utc`). Checked in code: no column
uses an observation later than its stamp; ties on one stamp (a week of H.10 values) keep the latest observation.

| Column (family, version) | Inputs | Transform | Warm-up | PIT status | H-02 / SW-0 use |
|---|---|---|---|---|---|
| `macro_real_yield_chg20` (`macro_drivers` v5) | DFII10 | 20-observation difference | 21 obs | OK | H-02 macro group; SW-3 |
| `macro_real_yield_z252` | DFII10 | 252-obs z-score (min 202) | ~10 months | OK | H-02; SW-3 |
| `macro_dollar_chg20` | DTWEXBGS | 20-obs % change | 21 obs | **A1 (revision look-ahead, small), A2 (pre-2019 backcast)** | H-02; SW-3 |
| `macro_gvz` | GVZCLS | level | — | OK (A4 pre-2010 only) | H-02 H-03 cluster; SW-4 |
| `macro_gvz_chg20` | GVZCLS | 20-obs difference | 21 obs | OK | H-03; SW-4 |
| `macro_vrp` | GVZCLS + bars | GVZ² − equal-weight HAR-RV² over completed stub-free UTC days before the bar's day | 22 days | OK (GVZ as of the bar; HAR strictly before the bar's day) | H-03; SW-4 |
| `macro_vix` | VIXCLS | level | — | OK (A4 pre-2010 only) | H-02 H-07 cluster; SW-5 |
| `macro_vix_chg5` | VIXCLS | 5-obs difference | 6 obs | OK | H-07; SW-5 |
| `macro_spx_dd20` | SP500 | close / 20-obs max − 1 | 20 obs | OK; NaN before ~2016-11 (masked per fold by `full_coverage_columns`) | H-07; SW-5 |
| `pos_cot_mm_net_pct_oi` (`positioning` v1, opt-in) | cot_mm_net, cot_open_interest | 100 × net / OI; stamp = max of the two | — | **B1 (pre-2009-09, immaterial on data-v1)**, C1 | not in H-02; SW-2 |
| `pos_cot_mm_net_pct_oi_z52` | same | 52-report z-score (min 40) | 40 reports | as above | SW-2 |
| `pos_cot_mm_net_pct_oi_chg4` | same | 4-report difference | 5 reports | as above | SW-2 |
| `pos_gld_tonnes_chg5_pct`, `_chg20_pct` | gld_tonnes | 5- and 20-obs % change | — | **no data (B3): all NaN** | none |

---

## 3. Revisions and vintages (measured)

Method: for each series, the ALFRED vintage on the first business day of every month 2018-01 .. 2026-10
(`alfredgraph.csv?id=<series>&vintage_date=<date>`, no API key), keeping observations at most 40 days old at the
vintage, earliest vintage per date ("near-first release"), compared with the value in `macro-v1`. Scripts in
`~/tmp/macro-pit/scripts/` (not committed; `revisions.py`, `staleness.py`, `audit.py`).

| Series | First ALFRED vintage seen | Obs compared | Share of values revised | Max abs revision | p99 relative | chg20 corr (first vs latest) | chg20 sign flips |
|---|---|---|---|---|---|---|---|
| DTWEXBGS | 2019-03-01 (absent on 2019-01-02) | 1,920 (2019-01 .. 2026-09) | **90.2%** | 1.659 index pts | 1.42% | 0.9937 | **1.21%** |
| DFII10 | ≤ 2018-01 | 2,213 | 0 | 0 | 0 | 1.0000 | 0 |
| T10YIE | ≤ 2018-01 | 2,213 | 0 | 0 | 0 | 1.0000 | 0 |
| DGS2 | ≤ 2018-01 | 2,213 | 0 | 0 | 0 | 1.0000 | 0 |
| GVZCLS | ≤ 2018-01 | 2,224 | 0.04% (1 value) | 0.09 vol pts | 0 | 1.0000 | 0.05% |
| VIXCLS | ≤ 2018-01 | 2,256 | 0 | 0 | 0 | 1.0000 | 0 |
| SP500 | **not yet audited**: ALFRED returned no SP500 vintages (licensed series) | — | — | — | — | — | — |

Legacy check for the DTWEXBGS backcast (A2): the old broad index DTWEXB (published in real time until its 2019-2020
discontinuation, itself revised) against the 2019-built DTWEXBGS, 2006-01 .. 2019-05, 3,349 days: 20-day % change
correlation **0.9964**, sign disagreement **2.24%**, median absolute difference 0.076 pp (p95 0.305 pp).

---

## 4. Findings

Severity: **violation** = a value visible before it was public on a bar we use; **latent** = a value stamped too
early but on no bar we use; **hazard** = no look-ahead, but a way to mislead a reading.

### A. `macro-v1`

- **A1 Revision look-ahead in the broad dollar (violation, small; H-02 and SW-0).** Cause: `fetch_fred_csv` reads
  fredgraph.csv, which serves only the latest vintage, and the first build (2026-10-13) stamped every historical
  value with the publication rule. `merge_vintages` records revisions only from the first download on. Measured
  effect in section 3: 1.2% sign flips and 0.994 correlation on the 20-day change. The revisions come from the
  Fed's trade-weight updates, which are not a function of future gold prices, so the leak carries little
  information about the label; it still makes a backtest slightly cleaner than real time.
  **Ruling for H-02 (recommendation to the research director):** do not change the file before the 2026-12-11 pin
  or the dispatch; record "A1: DTWEXBGS values are latest-vintage (macro-pit-audit-2026-10.md section 3)" on H-02's
  verdict record. A survivor that depends on `macro_dollar_chg20` would need a re-check on true first releases
  before its slot trial (0 trials; data-engineer).
  **For SW-0:** build true first releases from ALFRED (section 4.4, prerequisite P-1).
- **A2 DTWEXBGS is a 2019 construction back-cast to 2006 (violation in the strict sense, small).** ALFRED has no
  DTWEXBGS vintage before 2019 (empty on 2019-01-02, present on 2019-03-01); the H.10 page dates the re-indexation of
  the daily broad, AFE and EME indexes to 2019-06-24. So 2010-01 .. 2019-02 values (about 9 of H-02's 15.75 years)
  did not exist in this form at the time. The legacy real-time index agrees with it to 0.996 on the 20-day change.
  Same ruling as A1 for H-02. For SW-0, splice the legacy DTWEXB first releases before the DTWEXBGS first vintage,
  re-based at the splice date, or accept with the measured agreement written into the pre-registration (choice
  pinned at the Q2 freeze, before any SW-0 outcome).
- **A3 Federal closure days are missing from the macro calendar (latent; 2 value dates).** `fred_available_utc` uses
  `USFederalHolidayCalendar` without `positioning.CLOSURE_DAYS` (2018-12-05, 2025-01-09). The values for 2018-12-04
  and 2025-01-08 are stamped 23:00 UTC on the closure day. Whether the Board posted H.15 on those days is
  *unverified*; if it did not, those rows are one day early. That is 2 of about 3,950 days in the window; no reading
  can depend on it. Fix (implementer, 0 trials, after H-02 dispatch): use `positioning.US_CLOSED_BDAY` in
  `fred_available_utc` for new builds; published rows stay as published (`merge_vintages` never rewrites them), so a
  feature-level floor is the clean route for SW-0.
- **A4 Pre-2010 index backfills (latent).** GVZ history starts 2008-06-03, before CBOE began disseminating the index
  in 2008 *(launch date unverified)*; VIX values before the 2003-09 methodology change are a recalculation
  *(unverified)*. No bar on `data-v1` (from 2010) sees either: the 20- and 252-observation windows that start before
  2010 are complete by early 2010 and all their inputs were public by then.
- **A5 Conservative stamps cost about one day on the CBOE series (hazard for reading H-03 / H-07 at 4h).** GVZ and
  VIX are public at the CBOE close (16:15 ET on D) but stamped D+1 23:00 UTC because FRED is the source. At H-02's
  12-bar label (about 2 trading days) the implied vol it sees is 2-3 days old. Not a violation; a reading caution
  (bottom line 3). For SW-0, an optional same-day stamp (D 23:30 UTC) is admissible only if the data-engineer
  documents the CBOE publication time; until then it stays next-day.

### B. `positioning-v1`

- **B1 Disaggregated COT before 2009-09 (latent on data-v1).** Evidence: CFTC's disaggregated archive has gold from
  "Positions as of September 1, 2009" (`cotarchives/2009/futures/other_sf090109.htm`, gold row present) and nothing
  for 2009-08-25 (HTTP 404); CFTC release of 2009-12-04: "The CFTC began publishing disaggregated data for 22 major
  physical commodity markets on September 4, 2009 ... As it did with respect to the initial 22 markets, the CFTC will
  shortly make available three years of historical data for the remaining markets"
  ([CFTC 5760-09](https://www.cftc.gov/PressRoom/PressReleases/5760-09); gold is in the initial set by the archive).
  So the history for gold was public by 2009-12-04, and the exact day is *unverified*. 168 report dates
  (2006-06-13 .. 2009-08-25) carry stamps up to 3 years too early. **On data-v1 the features are identical with or
  without a fix**, because every affected row is public before the first bar (2010-01). Fix for hygiene
  (implementer, 0 trials): a `SHUTDOWN_FLOORS`-style floor `("2006-06-13", "2009-08-31", "2009-12-05 00:00")`,
  test included; it changes the file and the positioning feature version, so it lands before SW-0's freeze, not
  in the middle of a trial.
- **B2 COT backfill is corrected values (accepted, documented in `cot_frame`).** The history files hold current
  values; CFTC corrections are listed on its "COT Historical Special Announcements" page
  ([CFTC](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalSpecialAnnouncements/index.htm)). Not
  measured here; the data-engineer can diff the archived weekly HTML reports against the history file for gold
  (0 trials) if SW-2 survives.
- **B3 No GLD rows (hazard).** `pos_gld_*` are NaN on every bar. Any trial that opts into positioning must not count
  them as screened features (K_eff) and the discovery's non-constant-column check must not be satisfied by them.
  H-12 and SW-7 stay blocked; ETF flows are not worth a scraping workaround (the issuer refuses scripted clients).

### C. Join mechanics

- **C1 No maximum age on as-of joins (hazard).** `asof_join` is called with `tolerance=None` by both feature
  families, so a series that stops (a shutdown, a failed FRED download, SP500's window moving) is carried forward
  indefinitely as if current. In H-02's window this happens to COT in 2013-10 .. 11 and 2018-12 .. 2019-03 (not in
  H-02) and to nothing in `macro-v1` beyond its release schedule (daily series at most 7 days, the dollar 17). For SW-0: a per-series maximum age
  (daily FRED 7 days, H.10 17 days, COT 21 days) beyond which the column is NaN, pinned before the freeze.
- **C2 Join on the bar open (conservative).** Features join on bars' `ts_utc` (the bar open), while the decision is
  at the close (`visible_at`). Valid, and 4 h staler than needed at 4h. **At 1d it would cost a whole day:** SW-0
  must join on the settlement (`visible_at`), which is admissible because nothing is decided before it. A truncation
  test pins it (section 4.4, E-M3).

### Verdict against the 2026-12-11 config pin

| Item | H-02 | H-01 | Action before 2026-12-11 |
|---|---|---|---|
| A1, A2 | in the pool (`macro_dollar_chg20`, macro family group, not a cluster) | not used | record on H-02's verdict record; no re-pin |
| A3 | 2 value dates, ≤ 1 day | not used | none for H-02; fix after dispatch |
| A4 | no bar affected | — | none |
| A5 | reading caution for H-03 / H-07 | — | add one line to the evaluator's H-02 record |
| B1-B3, C1 | positioning not in H-02 | — | none; fix before SW-0's freeze |

**No as-of violation needs to stop or re-pin H-02.**

---

## 5. Owed items (none costs a trial)

| # | Item | Owner | Due |
|---|---|---|---|
| O-1 | One line on H-02's record: A1/A2 (latest-vintage, back-cast dollar) and A5 (implied-vol staleness at 4h) | evaluator (research director routes) | at H-02's verdict |
| O-2 | `cot_available_utc` floor for report dates before 2009-09-01 (B1), with a test | implementer, data-engineer review | 2027-02-26 |
| O-3 | Closure days in `fred_available_utc` (A3), with a test | implementer | 2027-02-26 |
| O-4 | `macro-v2`: true first releases from ALFRED for DTWEXBGS (weekly vintages from 2019-03) and the legacy DTWEXB splice before it (A1, A2); DFF added (swap sensitivity, swing doc 1.5) | data-engineer | 2027-02-26 |
| O-5 | Optional: CBOE close time documented, GVZ/VIX same-day stamp (A5) | data-engineer | 2027-02-26, else dropped |
| O-6 | Per-series maximum-age mask in both macro and positioning feature families (C1), opt-in parameter so H-02's version is untouched | implementer, quant-reviewer | 2027-02-26 |

---

## 6. SW-0 macro and flows layer (design only, for the Q2 pre-registration)

SW-0 is the swing lane's vehicle (`candidates/swing-2026-10-17.md` section 2): one 1d discovery trial, groups
SW-1..SW-6, SW-8, SW-9, per-side models (SW-P3), 15-day block clustering (SW-P1), drift-adjusted net (SW-P2), its
reading rule and forward-shadow rule written there on 2026-10-17. **This section adds nothing to that reading rule**;
it specifies the macro and flows groups so they are point in time, and states what the trial can and cannot buy.

### 6.1 Pre-registration fields (draft)

| Field | Value |
|---|---|
| Config | `swing_daily` specialist (E-S1, owed); candidates = every stub-free daily settlement 2010-01-01 .. 2025-09-30, labels exiting after 2025-10-01 dropped; long and short triple barrier 3.0 / 1.5 × ATR(1d) Wilder 14 on mid; time barrier = 10th stub-free settlement; swap by side; 1d `WINDOWS` (train 60 m expanding, test 12, purge 20 d, embargo 10 d) |
| Timeframe | decision 1d (research and shadow only, ADR 0004); 1w context |
| Economic reason | Macro and flow drivers act over days to weeks (real-rate and dollar repricing, positioning unwinds, implied-vol risk premia), where a 1.5 × ATR(1d) stop makes costs 0.01-0.04 R (spread + commission); swap is the cost that matters (0.08-0.37 R per full 10-day long hold, swing doc 1.5) |
| `research.yml` inputs | `specialist=swing_daily`, `macro=true`, plus a `positioning` input the workflow does not have yet (E-M1); `discover=true`, `families=cluster` with the SW group map; `from_year=2010`, `to_year=2025` |
| P4 expectation | Not a P4 screen. Candidates ≈ 3,940 per side; one-at-a-time trades ≤ ~394 over the window, ~270 out of sample; the `min_taken=150` rule of the swing doc governs |
| Gates | Gate 1 is unreachable (≥ 500 trades); SW-0 can only retire groups or make them context features. No promotion route |
| Reading rule | The swing doc's SW-0 rule, verbatim, written 2026-10-17 before any outcome |
| Trial cost | 1 trial (Q2 free reserve, conditional) + K_eff ≈ 16 (8 groups × 2 sides) on every later DSR |

### 6.2 Macro and flows groups, with their PIT specification

Every column joins by `asof_join` on `available_utc` **at the settlement time** (`visible_at`, C2), first releases
only, with the maximum-age mask (C1). "Lag at 1d" is the measured median / p95 staleness from section 2.

| Group | Columns (exist / new) | Source and stamp | Lag at 1d (d) | PIT prerequisite |
|---|---|---|---|---|
| SW-3 rates and dollar regime (a state, not a sign rule; R-08 and H-06 stay retired) | exist: `macro_real_yield_chg20`, `_z252`, `macro_dollar_chg20`. New: `macro_breakeven_chg20` (T10YIE), `macro_2y_chg20` (DGS2), `macro_curve_2s10s` (T10YIE + DFII10 − DGS2), rolling 120-settlement beta of gold's daily return to ΔDFII10 and to Δdollar (past settlements only) | H.15 / H.10, rules in 2.1 | 2.8 / 5.7 (rates), 9.8 / 14.7 (dollar) | O-4 (dollar first releases + splice); beta truncation test (swing doc E-S1) |
| SW-4 gold VRP | exist: `macro_gvz`, `macro_gvz_chg20`, `macro_vrp` | CBOE via FRED, next-day stamp (A5) | 2.8 / 5.7 | none; O-5 optional |
| SW-5 equity stress | exist: `macro_vix`, `macro_vix_chg5`; `macro_spx_dd20` only under the per-fold full-coverage mask (from ~2016-11) | CBOE / S&P via FRED | 2.8 / 5.7 | `full_coverage_columns` carried into SW-0's config |
| SW-2 COT positioning (first screen of COT anywhere) | exist: `pos_cot_mm_net_pct_oi`, `_z52`, `_chg4`. New: `pos_cot_comm_net_pct_oi` (hedging pressure) | CFTC Friday 15:30 ET, holiday-shifted, shutdown floors | 8.8 / 13.7 | O-2 (B1 floor), C1 mask (COT 21 days), E-M1 (positioning input in `research.yml`); GLD columns excluded (B3) |

Not in SW-0, and why (so they are not re-proposed without new evidence or data):
- **Central-bank buying** (WGC / IMF IFS): monthly or quarterly, published with a 1-3 month lag *(lag
  unverified per source)*; about 60-190 observations in the window against a 10-day label is no power. Context only,
  if ever.
- **Macro surprises against consensus**: no consensus feed (owner ruling C). SW-8 uses the release-day move instead,
  and only if `calendar-v1` exists.
- **ETF flows (GLD)**: no data (B3).
- **Real-yield sign rule, pre-FOMC drift**: retired (R-08); H-06 withdrawn.

### 6.3 Data and code prerequisites (all 0 trials; dates are the Q2 freeze path)

| # | Item | Owner | Due |
|---|---|---|---|
| E-M1 | `research.yml` input `positioning` (download `positioning-v1`, pass `--positioning`), recorded digest | implementer | 2027-02-26 |
| E-M2 | The new SW-3 and SW-2 columns, opt-in (default feature version unchanged), with truncation tests | implementer, quant-reviewer | 2027-02-26 |
| E-M3 | Settlement-time join for 1d (`asof_join(on="visible_at")` path in the swing specialist) with a test that a value stamped one second after the settlement is not seen | implementer | 2027-02-26 |
| O-2, O-4, O-6 | Section 5 | as listed | 2027-02-26 |
| — | Release digests of `macro-v2` and `positioning-v1` pinned in SW-0's row at the freeze | data-engineer | 2027-03-12 |

### 6.4 Power, honestly

About 270 effectively independent 10-day labels out of sample; the AUC standard error is about 0.035, so a lower
bound above 0.50 needs an observed AUC of about 0.57 (swing doc). The macro and flows groups are slow: SW-2 changes
weekly (≈ 560 reports out of sample, fewer distinct regimes), SW-3 regime spells last months. **Expected outcome:
fail or retire for most groups.** The trial's value is that it retires four macro and flows ideas (SW-2..SW-5) at
their literature horizon for one trial, and it is the first and only honest screen of COT on our data.

### 6.5 Evidence the groups rest on (as cited in the swing doc; status unchanged)

| Group | Claim | Source | Sample, instrument | Net of costs | Grade |
|---|---|---|---|---|---|
| SW-2 | Speculative pressure premium | Fan, Fernandez-Perez, Fuertes, Miffre 2020, *J. Futures Markets* 40(4), doi 10.1002/fut.22085 | cross-section of commodity, currency, equity futures *(sample unverified)* | gross | single study; not a gold time-series test |
| SW-3 | Gold vs real yields | Erb and Harvey 2013, *FAJ* 69(4) | 1975-2012 | n/a | replicated before 2022; broke 2022-23 (practitioner) |
| SW-4 | Gold VRP predicts returns | Nguyen, Prokopczuk, Wese Simen 2017 *(venue unverified)* | gold options, monthly horizons | gross | single study |
| SW-5 | Haven bid after equity shocks (~15 days) | Baur and Lucey 2010, *Financial Review*; Baur and McDermott 2010, *JBF* | 1979-2009, gold | gross | replicated |

### 6.6 Tradeability: what account or lot size makes 4h and 1d feasible

Inputs: measured ATR(14), last 90 days to 2026-10-05: 4h **$36.61**, 1d **$87.63** (`atr-measured-2026-10-18.md`);
stop 1.5 × ATR; 0.01 lot = 1 oz (IC Markets); GBP 0.75 per USD; min-lot rule: refused unless risk ≤ 1.2 × the
0.5% target (0.6%), or ≤ 1% through the demo-only exception (`sizability-2026-10-18.md`, `config/settings.yaml`);
daily limit = min(2% × equity, £50), projected at entry with the new trade's full stop (ADR 0004 §3).

| Timeframe | Stop ($/oz) | Risk at 0.01 lot | Equity at 0.6% (live rule) | Equity at 1% (demo exception) | £50 projected daily limit | Feasible at £1,000? |
|---|---|---|---|---|---|---|
| 4h | 54.92 | £41.19 | **£6,865** | £4,119 | passes (first trade of a day only) | no |
| 1d | 131.45 | £98.58 | £16,430 | £9,858 | **fails at any equity** | no |
| 4h at 0.1 oz | 54.92 | £4.12 | £687 | £412 | passes | **yes** |
| 1d at 0.1 oz | 131.45 | £9.86 | **£1,643** | £986 | passes | demo only (exception) |

- **1d is volatility-gated, not equity-gated**, while the £50 absolute limit stands: it admits a 1.5 × ATR(1d)
  stop at 0.01 lot only when ATR(1d) ≤ about **$44** (2025's median was $47; 2024's $28.50). So the M4 rung of ADR
  0004 (£7,900, built on a $70 estimate) is unreachable at today's $88, at any equity.
- **The two levers are owner decisions** and are flagged, not assumed: (a) a product with ≤ 0.1 oz granularity
  (broker or account type; none verified here), which makes 4h placeable now and 1d at about £1,650; (b) changing
  the £50 absolute limit (ADR 0004; not recommended by me: it is the owner's loss ceiling, and the research case for
  1d is context features, not a book). A tighter 1d stop (≤ 0.5 × ATR(1d)) is a different design and would need its
  own pre-registration.
- These thresholds scale linearly with ATR; recompute at each quarter freeze from the measured last-90-day ATR.

---

## 7. Known-failed and closed ideas in this area (do not re-test without new evidence)

- R-08: real-yield sign rules, pre-FOMC drift, London-fix front-running, weekday/weekend effects (literature, never
  trialled).
- H-06 macro-conditioned slow TSMOM: withdrawn 2026-10-16 (lower bound 107 events < 150).
- R-06 / R-10: tsmom at 1h/4h, and its re-scoring.
- H-12 / SW-7 ETF flows: blocked on data, not failed.

Multiple-testing count: this audit adds **0 trials and 0 K_eff**. SW-0, if funded in Q2, adds 1 trial and K_eff ≈ 16,
already counted in the Q2 projection (`hypotheses.md`, Q2 row). The registry's `n_trials_effective` is 110 today.

## 8. Owner-only decisions flagged (none assumed)

1. A smaller minimum position (≤ 0.1 oz) via another product or broker (instrument decision).
2. Any change to the £50 absolute daily limit (risk limit).
3. Live trading or sizing on 4h/1d at any equity.

## 9. Audit coverage (what is and is not checked)

Checked: every source series' stamp rule against its publication time (section 2.1); every derived column in
`macro_drivers` v5 and `positioning` v1 (2.2); measured staleness at 4h and 1d; revisions 2018-2026 for DTWEXBGS,
DFII10, T10YIE, DGS2, GVZCLS, VIXCLS (section 3).
**Not yet audited:** SP500 revisions (no ALFRED vintages); revisions before 2018 for the six daily series (the
monthly vintage pull started 2018-01; pre-2018 rests on the "market closes are not revised" argument, *unverified*);
CFTC corrections inside the COT history (B2); the H.15 posting on the two closure days (A3); the CBOE close time (A5);
the exact day the CFTC published gold's 2006-2009 disaggregated history (B1, bounded by 2009-12-04).
