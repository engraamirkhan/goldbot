---
number: 5
rank: 12
title: Q1 2027 pre-registered trial queue
stage: Idea generation · FIND
size: S
status: 'in progress (draft; owner rulings 2026-10-11; freeze commit 2026-12-14, rows 2026-12-18..23; needs 48) · accountable: research-director · due 2026-12-14'
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
