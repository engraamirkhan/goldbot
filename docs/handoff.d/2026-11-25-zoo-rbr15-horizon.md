- **H-101 replay horizon fix: a candidate's max_hold_s is its wall-clock exposure (Stage L crash on RBR15).**
  - **Symptom.** Stage L stopped before any read or K booking with "RBR15: a label exit after its horizon".
  - **Root cause.** The label's time barrier counts decision bars: it is the close of bar idx + 1 + max_bars. The bar
    frame has no rows while the market is shut. `hold_seconds` assumed contiguous bars, (bars + 1) x the timeframe.
    So a hold across a weekend, the daily break, a holiday or a data gap ran past its max_hold_s. On RBR15 in
    2010-17 that was 22 of 13,610 trades: Friday decisions held to Sunday or Monday (up to 73.75 h against 24.25 h),
    and intraday gaps (24.75 h).
  - **Fix.** `history_replay.horizon_seconds`: from the decision close to the close of the decision bar the barrier
    ends on, gaps included, never less than the nominal span. One helper (`_horizons`) feeds both `candidates` and
    `label`, and the exit check stays fail-closed.
  - **Horizon definition changed.** Quant re-verifies it. Contiguous holds are unchanged. A hold across a gap now gets
    a longer horizon, so the purge and the embargo can only drop more rows, never fewer.
  - **Evidence.** The outcome-blind Stage 0 count on a scratch registry copy matches the old code exactly: per arm,
    per block and the verdict. All 10 pre-named arms label 2012-17 without the error (60 of 60 arm-years).
