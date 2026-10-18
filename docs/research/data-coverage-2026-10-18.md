# data-v1 coverage after the 2026-10-17/18 refetch (data-engineer, 2026-10-18)

Gate used: G1 = 0 unexpected weekday gaps (weekday under 700 1m bars, not a listed closure) in 2010-01..2025-09.
G2 = holdout 2026 repaired (no gap weekdays, no bid-imputed months). Source: coverage issues #24 #25 #27 #29 #30 #2 #32.

## Runs
| run | scope | status |
|---|---|---|
| 39035057414 | 10 months 2020-2025 | completed success (2026-10-17 19:35Z) |
| 39035068394 | 2026-02..05 | completed success (2026-10-18 00:44Z); 2026 file published |
| 39097230486 | 2026-06..09 | in progress at 04:29Z (2026 build step still downloading) |

## G1: NOT closed
Month bar counts all clear 15,000, but the day check still lists the same 21 empty weekdays as before the refetch
(0 bars each time, so the refetch recovered nothing; counts unchanged e.g. 2020-05 24,535).
Months short (gap days): 2020-05 (26-28), 2021-11 (05), 2022-01 (31), 2023-11 (08), 2024-01 (10-12), 2024-06 (07),
2024-07 (01, 16), 2025-02 (26-28), 2025-03 (26-28), 2025-05 (12-14). Every other month 2020-01..2025-09 has 0 gap days.
Pre-2020 unchanged (closures and Sunday-reopen artefact only, per the 10-16 triage).

## G2: NOT closed
Gap weekdays per month in 2026 (file after run 39035068394): 04: 10, 06: 7, 07: 11, 08: 6, 09: 8, 10: 9 (51 total).
01, 02, 03, 05: 0 gap days, but 02, 03, 05 are bid-imputed on every bar. Runs for 06-09 not finished; the 51 count
may drop after 39097230486 publishes (06-09 were "kept" in the earlier run, i.e. not re-downloaded). 2026-04 was
re-downloaded and is still 10 empty days.

## Re-run
Identical zero days after a real re-download point at throttling or no data at source, not a missed month. Wait for
39097230486, then a single probe of the hardest case before spending ~1 h of minutes:
`gh workflow run data-dukascopy.yml --ref main -f months=2020-05`
If that again returns 0 for 05-26..28, the days are unavailable from Dukascopy and G1 needs a different source or an
accepted-gap ADR (decision for owner/research director). If it fills, run the full list:
`gh workflow run data-dukascopy.yml --ref main -f months=2021-11,2022-01,2023-11,2024-01,2024-06,2024-07,2025-02,2025-03,2025-05,2026-04`
(Confirm the input name/format in data-dukascopy.yml before dispatch.)

## Update 2026-10-18 (later)
Run 39097230486 is still in_progress (1h44m at check time), so the 2026 gap count stays 51 (04: 10, 06: 7, 07: 11,
08: 6, 09: 8, 10: 9) and no new coverage issue has opened since #32. The probe was NOT dispatched: one concurrency
group, and a second dispatch would cancel a pending run. Input confirmed: `months` (comma-separated YYYY-MM, max 24).
Next step once the run completes: `gh workflow run data-dukascopy.yml --ref main -f months=2020-05`; re-check
05-26..28 (and the 2026 count) after it publishes.

### Decision options if Dukascopy cannot serve the gap days (owner / research director)
Scale: 21 empty weekdays in 2020-2025 (10 months), 51 in 2026 pending run 39097230486.
| option | what | cost / risk |
|---|---|---|
| A. Broker M1 backfill | IC Markets demo MT5 `copy_rates_range` for the gap days, stored as its own source with `source` and `available_utc` set | Free, VPS-only (MT5 is Windows). History depth for XAUUSD M1 is limited; broker quotes and spread differ from Dukascopy, so basis shift at the seams; must be flagged per bar |
| B. TradingView OANDA / IC history (TV pull) | Pull the gap days only through the TV reader | A second feed, never primary. Unofficial cookie pull (ToS risk already accepted), rate limits, bar-count caps on M1, different volume and price basis. Use as a gap patch, flagged `source=tv`, never blended silently |
| C. Accepted-gap ADR | Leave gaps; document them | Simplest and cleanest provenance. Walk-forward rule: drop gap days from features, labels and fills, with no forward-fill across them; labels whose horizon touches a gap day are dropped; folds report the excluded-day count; holdout 2026 must have none (G2 stays a hard gate) |
Recommendation: C for 2020-2025 (21 days of about 1,500 is 1.4%, history only), with the exclusion rule enforced in the
loader and tested. For the 2026 holdout use A (broker M1 same-feed as live execution) if run 39097230486 leaves gaps;
B only as a cross-check. Any patched day carries `source` and a dq_events row. Owner decides; G1 wording would change
to "0 unexplained gaps".
