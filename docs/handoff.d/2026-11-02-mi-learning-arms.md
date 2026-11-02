- Multi-instrument learning arms (`docs/research/learning-mi-2026-11-02.md`, ADR 0061 amendment, ADR 0072).
  - **The pairs.** One opening-range breakout per instrument in its own session (`session_range_break` with the new
    `session_local`; GS1/GS1C unchanged), each with a dedicated seeded control (`session_range_control`).
  - **Registered.** 8 of 26 pairs, ranked by evidence, cost per R and slot capacity:
    - icm-probe: US500, DE40, XTIUSD, EURUSD, USDJPY, JP225 (a new 4-slot intraday pot inside the unchanged 23);
    - vantage-demo: UST10Y, BUND10Y.
  - **Queued.** 18 instruments, frozen in the doc with no K row until they are registered.
  - **Status.** The pairs are `blocked` until lane L3 runs arms on their own instrument (today's engine would compute
    them on gold). The unblock is `status: run` on both rows of a pair, with nothing hashed.
  - **K rows.** 16 learning K rows, written by `scripts/record_learning_k_rows.py` with the gold ones (46 in all). Its
    new `--budget` prints the quarter's count against the 3,500 cap before any write.
