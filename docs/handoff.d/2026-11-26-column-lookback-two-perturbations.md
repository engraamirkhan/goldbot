- 2026-11-26 Column lookbacks: two perturbations and a locked read ledger. This is the quant sign-off follow-up of #472
  and must land before the A4 unseal.
  - Lookbacks: each declared column lookback is now the larger of two truncation tests: (i) a continuous 3×-vol path,
    and (ii) a +5 × median-range shift with 1 % spikes of ±20 ranges. They are declared on 32 seeds; CI re-checks 8.
    A guard test refuses any shrink below the signed floor (`tests/fixtures/column_lookbacks_floor.json`) unless a
    quant re-review marker is set.
  - Read ledger: the C-4 consumption check and `windows.record_read_once` now run under an exclusive flock, with one
    O_APPEND write plus fsync. A torn final line is reported and moved aside; a malformed middle line refuses the
    ledger. Columns are compared as a set.
  - Counts (T2 n_eff after tier survival):

    | Unit (window) | Column set | 1m | 5m | 15m | 30m | 1h | 4h | 1d | 1w |
    |---|---|---|---|---|---|---|---|---|---|
    | gold 2010-17 | core-6 = core-12 | 5,226 | 3,846 | 2,707 | 1,643 | 1,067 | 123 | 0 | 0 |
    | gold 2010-17 | P30 | 2,151 | 1,617 | 1,222 | 831 | 660 | 123 | 0 | 0 |
    | EURUSD 2010-17 | core-6 = core-12 | 6,996 | 5,238 | 3,907 | 2,607 | 1,968 | 422 | 35 | 0 |
    | EURUSD 2010-17 | P30 | 5,644 | 4,247 | 3,214 | 2,191 | 1,734 | 422 | 35 | 0 |

    Gold 2003-09 is unchanged.
