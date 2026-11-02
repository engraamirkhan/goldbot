# POS-1 data manifest (market availability, written before any run)

Data lane (principal data engineer), 2026-11-02. Governs which markets of `docs/research/pos1/prereg.md` (PR #285)
exist for the run. A market leaves the universe **only** through this file. Data availability is not an outcome.

**Status: availability fixed, files still filling.** The Dukascopy archive is throttling this host (13-75 s per
request, dropped connections, while the XAUUSD 2003-2009 1m lane runs on the same address), so the hourly pull runs
detached and resumable (`scripts/daily_fetch.py`, chain in `~/tmp/daily_lane/`). **POS-1 must not start** until
`~/tmp/daily_manifest.json` lists every file below with `last` = 2025-09-30 and this file is re-stamped with its
sha256 values. Nothing at or after 2025-10-01 is requested, stored or returned (`goldbot/data/daily.py`, `CUTOFF`).

## How the bars are made

- Source: Dukascopy hourly candles, bid and ask (`<CODE>/<YYYY>/<MM-1>/{BID,ASK}_candles_hour_1.bi5`), one request
  per instrument-month-side. Hourly, not 1m: the 21:00 UTC close needs only hour boundaries, and 1m would be ~22x the
  requests against a throttled archive. The prereg's "1m" matters only for the fill: the bar's `open` is the first
  traded price after the previous 21:00 close, which is the next 1m open by construction.
- Daily mid bars, close at 21:00 UTC (prereg section 1): bar D = every traded hour in (previous bar's close, D 21:00].
  Hours after a Friday close (a winter FX 21:00-22:00 hour, the Sunday reopen) belong to Monday. Mid = (bid + ask) / 2
  per field, high/low widened to bracket open/close. `visible_at` = D 21:00 UTC; joins use `asof_join(...,
  available_col="visible_at")`.
- XAUUSD comes from the XAUUSD lane's 1m files (`raw/xauusd_1m_dukascopy_<year>.parquet`, read only) with the same
  clock and `n_minutes`. Today it starts 2010-01; rebuild with `daily_fetch.py xauusd` when 2003-2009 lands.
- Columns: date, open, high, low, close, spread_close, n_hours (or n_minutes), visible_at, source.

## U_16 availability

Live = first data day + 260 trading days of warm-up (prereg). Windows: LEARN 2003-05 and 2010-17, CONFIRM
2018-01..2025-09, TEST-OLD 2006-02..2009.

| market | Dukascopy code | hourly from | live from (approx.) | LEARN 03-05 | LEARN 10-17 | CONFIRM | TEST-OLD | status |
|---|---|---|---|---|---|---|---|---|
| XAUUSD | XAUUSD (1m lane) | 2003-05 (lane pending; 2010-01 on disk) | 2004-05 | lane pending | yes | yes | lane pending | in |
| XAGUSD | XAGUSD | 2003-05 | 2004-05 | partial | yes | yes | yes | in |
| XPTUSD | XPTCMDUSD | 2021-11 | 2022-11 | no | no | 2022-11 on | no (old-test pending) | in, late |
| WTI | LIGHTCMDUSD | 2011-09 | 2012-09 | no | 2012-09 on | yes | no (old-test pending) | in, roll gaps |
| US500 | USA500IDXUSD | 2011-09 | 2012-09 | no | 2012-09 on | yes | no (old-test pending) | in |
| US100 | USATECHIDXUSD | 2011-09 | 2012-09 | no | 2012-09 on | yes | no (old-test pending) | in |
| GER40 | DEUIDXEUR | 2013-09 | 2014-09 | no | 2014-09 on | yes | no (old-test pending) | in |
| JP225 | JPNIDXJPY | 2011-09 | 2012-09 | no | 2012-09 on | yes | no (old-test pending) | in |
| UK100 | GBRIDXGBP | 2011-09 | 2012-09 | no | 2012-09 on | yes | no (old-test pending) | in |
| EURUSD | EURUSD | 2003-05 | 2004-05 | partial | yes | yes | yes | in |
| GBPUSD | GBPUSD | 2003-05 | 2004-05 | partial | yes | yes | yes | in |
| USDJPY | USDJPY | 2003-05 | 2004-05 | partial | yes | yes | yes | in |
| USDCHF | USDCHF | 2003-05 | 2004-05 | partial | yes | yes | yes | in |
| AUDUSD | AUDUSD | 2003-08 | 2004-08 | partial | yes | yes | yes | in |
| USDCAD | USDCAD | 2003-08 | 2004-08 | partial | yes | yes | yes | in |
| NZDUSD | NZDUSD | 2003-08 | 2004-08 | partial | yes | yes | yes | in |

No market is dropped. "Hourly from" is the archive's first month (dukascopy-node instrument metadata, the point size
and price level of each code checked against the archive on 2026-11-02); the measured first date, gaps and day counts
come from the manifest table when the pull completes.

**Consequences the research director must re-stamp (prereg section 3).**
- U_16 has at least 8 live markets from about 2004-08 (XAG + 7 FX, XAUUSD making 9 if the 1m lane lands), so its
  LEARN 2003-05 window is effectively 2004-08..2005-12, about 1.4 years; with 2010-17 that is about 9.4 learn-years,
  not 10. The indices and WTI join only in 2012-2014; XPT only in 2022-11.
- U_metals is XAU + XAG until 2022-11; XPT adds under three years, all inside CONFIRM.
- TEST-OLD (2006-02..2009) exists for XAG and the seven FX pairs (and XAUUSD once its lane lands); every index, WTI
  and XPT is "old-test pending" by the prereg's own rule.

## Macro and spec 033 inputs (FRED public CSV, `goldbot.data.macro` conventions)

| file | series | rows | value dates | availability rule |
|---|---|---:|---|---|
| fred_DTB3.parquet | 3-month bill | 5,940 | 2002-01-02..2025-09-29 | next US business day 23:00 UTC |
| fred_DFII10.parquet | 10y real yield | 5,690 | 2003-01-02..2025-09-29 | next US business day 23:00 UTC |
| fred_T10YIE.parquet | 10y breakeven | 5,690 | 2003-01-02..2025-09-29 | next US business day 23:00 UTC |
| fred_DTWEXBGS.parquet | broad dollar (2006 on) | 4,949 | 2006-01-02..2025-09-26 | weekly H.10: Tuesday after the week, 23:00 UTC |
| fred_DTWEXB.parquet | broad dollar (discontinued) | 4,553 | 2002-01-01..2019-12-31 | weekly H.10, same rule |
| yahoo_gspc.parquet | ^GSPC close (spec 033) | 5,975 | 2002-01-02..2025-09-30 | date 21:15 UTC (prereg) |

- Rows whose `available_utc` falls on or after the cutoff are not stored (the 2025-09-30 values publish on 10-01).
- USD broad: `goldbot.data.daily.usd_broad` ratio-chains DTWEXB onto DTWEXBGS at 2006-01-02 (one constant factor),
  so no 20-day change straddling the splice carries the two indices' level difference. A plain splice would.
- Vintage: fredgraph serves the latest vintage only (retrieved 2026-11-02). These are market rates and an index;
  first-release values would need ALFRED (an API key). DTWEXBGS is the series most exposed to historical revision.
- Spec 033 uses Yahoo ^GSPC because Dukascopy USA500 hourly starts 2011-09 (its yearly day-candle files reach back
  further, but on a UTC-midnight clock of unknown lineage, so they do not meet the prereg's "covers 2003 onward").

## Non-USD short rates (W3.3 financing of FX and non-USD indices)

OECD Main Economic Indicators 3-month interbank rate, monthly average, % a year (FRED `IR3TIB01<CC>M156N`, public CSV,
requested through 2025-09-30). Same schema as DTB3 (value_date, value, available_utc, plus series, vintage, ts_utc).
GER40 uses EUR, UK100 GBP, JP225 JPY.

| currency | file | months | value dates | missing months | last available_utc |
|---|---|---:|---|---:|---|
| EUR (euro area) | fred_IR3TIB01EZM156N.parquet | 283 | 2002-01..2025-07 | 0 | 2025-09-01 23:00 |
| GBP | fred_IR3TIB01GBM156N.parquet | 283 | 2002-01..2025-07 | 0 | 2025-09-01 23:00 |
| JPY | fred_IR3TIB01JPM156N.parquet | 280 | 2002-04..2025-07 | 0 | 2025-09-01 23:00 |
| AUD | fred_IR3TIB01AUM156N.parquet | 283 | 2002-01..2025-07 | 0 | 2025-09-01 23:00 |
| CAD | fred_IR3TIB01CAM156N.parquet | 283 | 2002-01..2025-07 | 0 | 2025-09-01 23:00 |
| CHF | fred_IR3TIB01CHM156N.parquet | 283 | 2002-01..2025-07 | 0 | 2025-09-01 23:00 |
| NZD | fred_IR3TIB01NZM156N.parquet | 283 | 2002-01..2025-07 | 0 | 2025-09-01 23:00 |

- Availability (`goldbot.data.daily.oecd_monthly_available_utc`): month M is known on the first day of M+2 at
  23:00 UTC. A monthly average cannot exist before the month ends; OECD's MEI update publishes it around mid M+1 and
  FRED posts it days later. Without ALFRED vintages this stamp is set at or after the latest plausible release, so
  August and September 2025 (available 2025-10-01 and 11-01) are not stored. The cost is up to about six weeks of
  staleness on a financing rate.
- Every series starts by 2002-06 in availability terms, before the earliest market goes live (2004-05).
- Settings mapping (`research.position.currency_rate_paths`, PR #291):
  `{EUR: raw/daily/fred_IR3TIB01EZM156N.parquet, GBP: ...GBM156N, JPY: ...JPM156N, AUD: ...AUM156N, CAD: ...CAM156N,
  CHF: ...CHM156N, NZD: ...NZM156N}`; `goldbot.data.daily.currency_rate_paths()` returns exactly this.

## Venue terms (ICM)

- XAUUSD only, from the brain's `broker_terms_icm-demo.json` (measured 2026-11-02 05:02 UTC): swap long -52.22 and
  short +36.00 USD per lot per night (triple-swap weekday recorded as 2), commission 8.04 USD per lot round trip.
- Every other U_16 symbol: **missing.** The read-only MT5 symbol read through the brain's bridge was refused by the
  session's permission policy (production read). Scenario A financing follows W3.3 (DTB3 + margin) regardless; the
  swaps are a cross-check, not an input.

## Known data risks

1. WTI (LIGHTCMDUSD) is a futures-based CFD: its daily bars keep the roll gap. The manifest counts close-to-open jumps
   over 3% per file; the engine must not read a roll as a return (the TSMOM/Turtle signals on WTI otherwise see fake
   moves).
2. Mid high/low is the mid of bid and ask extremes, which need not be simultaneous; ATR is slightly understated
   relative to a true mid series. Negligible at the daily horizon, stated for completeness.
3. Dukascopy CFD index prices include their own fair-value and dividend treatment; they are not the cash index.
