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
