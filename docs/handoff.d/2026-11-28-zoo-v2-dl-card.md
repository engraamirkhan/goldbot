- 2026-11-28 H-104 card, revision 2 (research director, label-blind, 0 K):
  `docs/research/prereg-2026-11-28-zoo-v2-dl.md` pre-registers the reads of 38 (learner x unit) cells on gold
  history, at the cost of record srv3-cost-2b3628372a9b3bfa:
  - the sealed zoo-v2 learners: G-cw (with D-40 as its inputs), HB, AVG;
  - the SEQ-1 label head, RL-X1 exits and agg-v1;
  - the owner's cascades: CAS-1 (SEQ-1 embedding into G-cw, 40 inputs, d1_slope_ema20 dropped) and CAS-2 (an OOF
    deep + tree stack), each at 4h and 1d+1w.
  - Revision 2 applies quant review F1-F10. The ADR 0085 start rule applies per cell. S0-3 power is the record
    reason; S0-2 cost feasibility (σ 1.5) is reported, never the sole reason.
  - Predicted: 0 of 38 start. Gold confirm holds 2018 only under r11-v2.1 (149 days), and test_old none.
  - Stage 0 runs the day after the last signature (no later than 2026-12-04). The filter cells then retire as
    UNTESTABLE at the cost of record on gold 2010-25 history (not a NULL).
  - Reopening routes are new cards: a second graded 1m gold source, a forward reserve-week window, the pooled body,
    or DEEP-DAILY.
  - Backlog 277 (seal guard, an S0-1 precondition) and backlog 278 (`ic_within_instrument` is not an IC).
