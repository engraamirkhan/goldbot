- Multi-instrument learning arms (`docs/research/learning-mi-2026-11-02.md`, ADR 0061 amendment, ADR 0072).
  - **The pairs.** One opening-range breakout per instrument in its own session (`session_range_break` with the new
    `session_local`; GS1/GS1C unchanged), each with a dedicated seeded control (`session_range_control`).
  - **Registered.** All 26 pairs (research lead 2026-11-02), each on the account that lists its instrument: 19 on
    icm-probe, 7 on vantage-demo.
  - **Slot counts raised, nothing else.**
    - icm-probe: `learning_max_open` 6 -> 14, intraday pot 12;
    - vantage-demo: intraday pot 6 -> 10.
    - The host total of 23, the per-trade caps, the days, margin and notional are unchanged.
  - **Slot refusals** are reported per arm in the read as a bias diagnostic.
  - **Status.** The pairs are `blocked` until lane L3 runs arms on their own instrument (today's engine would compute
    them on gold). The unblock is `status: run` on both rows of a pair, with nothing hashed.
  - **K rows.** 52 learning K rows, written by `scripts/record_learning_k_rows.py` with the 30 gold ones (82 in all).
    `--budget` prints the quarter's count against the 3,500 cap, the missing rows per pre-registration, and what is
    left, before any write.
