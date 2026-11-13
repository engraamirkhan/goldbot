---
number: 5
title: Q1 2027 pre-registered trial queue
stage: Idea generation · FIND
size: S
status: 'retired (triage 2026-11-13): the Q1 2027 queue was superseded by the 2026-11 sprint screens and the EVAL-2 pipeline (ADR 0076, PRs #344-#358)'
---

### 5. Q1 2027 pre-registered trial queue
Value: spends the Q1 budget on the hypotheses research ranks highest, written down before any result is seen (FIND).
Acceptance:
- `research_plan.json` holds, dated before 2027-01-01, these entries, each with a reading rule written in advance:
  - slow tsmom (4h bars, 1d trend condition, long `max_bars`, long and short, measured swap), superseding
    `tsmom 4h max_bars 12`;
  - Asia-session drift (00:00–07:00 UTC on 1h bars, flat before London);
  - the macro-conditioned variant of slow tsmom, whose reading rule requires 2022–24 to be positive.
- The budget guard accepts the queue on 2027-01-01 and refuses anything beyond the quarter's budget (existing test
  extended).
- Each trial's report issue shows gross and net R, the per-year table and DSR with the real trial count.

Depends on: 2 (costs), 4 (variant 3). The daily-family event floor is an owner decision (below).

Sweep 2026-10-16 (product owner): accountable `research-director`, due 2026-12-14 (freeze commit). The writer it needed (47) is merged (#76). `macro-v1` was published on 2026-10-13. Acceptance as above, plus the dry run of `scripts/preregister_quarter.py` on the final spec reviewed by the quant reviewer.
Job check 2026-10-16 (research director): data gaps G1-G5 from the coverage triage are on the Q1 readiness list (item 5). H-01 is at risk; one merged recount on repaired data (by 2026-11-13) decides whether it stays queued (>= 150) or is withdrawn by its rule (reservation 8 -> 7). W1-5 (H-13 cost filter, this item's budget-guard test) starts Mon 2026-10-19.
