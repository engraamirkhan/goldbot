# Positioning and flow data: schema for research (local only)

Owner: data-engineer. Code: `goldbot/data/positioning.py` (sources, availability rules, revisions) and
`goldbot/data/positioning_local.py` (the local Mac build). Data never leaves the Mac (ADR data-local-only; the SPDR
archive also forbids redistribution): nothing here is published to a GitHub release.

## Where it is

| what | path | how to read |
|---|---|---|
| point-in-time file (canonical) | `~/myproject/goldbot/raw/positioning/positioning.parquet` | `goldbot.data.release.read_positioning_files(Path(...))` |
| Parquet store table `positioning` | `data_root` (`~/myproject/goldbot/data`), partitions `source=cftc`, `source=cftc_legacy`, `source=spdr` | `goldbot.data.release.store_positioning(Store(data_root))` |
| downloaded source files (lineage) | `~/myproject/goldbot/raw/positioning/src/<YYYY-MM-DD>/` + `manifest.json` (sha256) | not for research |

Rebuild / refresh (idempotent; keeps every published row, adds new dates and revisions):
`python -m goldbot.data.positioning_local --raw ~/myproject/goldbot/raw --store ~/myproject/goldbot/data`

## Columns (one long table, one row per series, value_date and vintage)

| column | type | meaning |
|---|---|---|
| `series` | str | series name (below) |
| `series_id` | str | `088691` (COMEX gold, CFTC) or `GLD` |
| `value_date` | datetime64 (naive date) | what the number is about: COT = the Tuesday positions date; GLD = the trading day (4:15 pm NYT) |
| `value` | float64 | contracts (COT) or the unit in the series table |
| `vintage` | datetime64 (naive date) | the day the row was retrieved |
| `available_utc` | datetime64[ns, UTC] | conservative time the value was public. **Join only on this.** |
| `ts_utc` | datetime64[ns, UTC] | = `available_utc` |
| `source` | str | `cftc` (disaggregated), `cftc_legacy` (legacy), `spdr` (GLD) |

Revisions are extra rows (same series and value_date, later `available_utc`); the first release is kept. Use
`goldbot.data.macro.first_release(df, series)` for first-release values, or the latest row per value_date with
`available_utc <= t` for as-known-at-t values.

## Series

COT disaggregated, futures only (`source=cftc`, weekly, 2006-06-13 on). Contracts.
`cot_mm_long`, `cot_mm_short`, `cot_mm_net` (= long - short), `cot_mm_spread` (managed money);
`cot_pm_long`, `cot_pm_short` (producer/merchant/processor/user); `cot_swap_long`, `cot_swap_short`, `cot_swap_spread`
(swap dealers); `cot_comm_net` (= producer/merchant net + swap net); `cot_other_long`, `cot_other_short`,
`cot_other_spread` (other reportables); `cot_nonrept_long`, `cot_nonrept_short` (non-reportable);
`cot_open_interest`.

COT legacy, futures only (`source=cftc_legacy`, 1986-01-15 on). Contracts.
`cot_legacy_nc_long`, `cot_legacy_nc_short`, `cot_legacy_nc_net`, `cot_legacy_nc_spread` (non-commercial);
`cot_legacy_comm_long`, `cot_legacy_comm_short`, `cot_legacy_comm_net` (commercial);
`cot_legacy_nonrept_long`, `cot_legacy_nonrept_short`; `cot_legacy_open_interest`.

GLD (`source=spdr`, daily trading days, 2004-11-18 on): `gld_tonnes` (tonnes in the trust), `gld_ounces` (troy oz in
the trust), `gld_shares` (shares outstanding = ounces / ounces per share), `gld_volume` (daily share volume).

IAU (iShares): not loaded; the issuer has no free scripted historical download (backlog item, free route still open).

## Availability rules (`available_utc`)

* COT: Friday 15:30 ET of the report week (DST-correct via zoneinfo). A US federal holiday Monday..Friday of that week
  moves it to 15:30 ET on the next US business day after the Friday (the CFTC's holiday schedule is never later than
  that). Shutdown backlogs (2013, 2018-19, 2025) have not-before floors. Legacy reports before 2001 add the
  fortnightly/semi-monthly publication lag (14 / 28 days).
* GLD: the next US business day after value_date (federal holidays skipped), 14:00 UTC. The trust publishes the
  evening of value_date, so this is conservative.
* Known, accepted look-ahead: the history files carry corrected values, so the backfill stamps corrected values at the
  first-release time. Revisions seen after the first local build are new rows.

## Joining to bars

```python
from goldbot.data.store import asof_join
s = df[df.series == "cot_mm_net"].sort_values("available_utc")
bars = asof_join(bars, s[["available_utc", "value"]].rename(columns={"value": "cot_mm_net"}))
```

Never join on `value_date`. Data quality: `goldbot.data.positioning_local.check_positioning(df)` returns the dq events
(errors are quarantined out of the file at build time, warnings are kept and flagged).
