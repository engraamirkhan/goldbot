- 2026-11-14 (branch fix/weekly-read-due; R156; BACKLOG 262): the weekly review no longer raises `read_due` from the
  fill count alone. On 2026-11-14 it raised two false alarms, for H-81:Z7 (300th fill on 4 session days) and H-81:Z4
  (300th fill on 8 session days). Both units had been retired under A5 on 2026-11-06.
  - config/arm_reads.yaml has a new `min_session_days` map, per arm like `read_at`. It is set to 20 for the HV arms
    and their twins, F1A and G1A (futility-only), every Z arm and the VS twins, following HV "Graduation" and H-81
    section 4 ("at the 300th admissible family fill on >= 20 session days"). Any other arm has no minimum, so its
    behaviour is unchanged. W1 has no minimum until the evaluator rules on it. The arms of a pooled unit must share one
    minimum; if they do not, the file is unreadable (fail closed).
  - The ranking's `RankRow` gains `read_units`, which lists every unread unit with a read coming, a due one first, and
    `RankProgress` gains `n_days` and `min_days` (API contract regenerated). Session days are the distinct entry
    opportunity days of the unit's counted fills. A unit whose every arm was retired unread before its read fell due
    (`retired_day`, or at its registered stop) gets no progress. A unit retired on or after the day it fell due keeps
    its read. `read_due` now checks every unit of a row and needs both counts.
  - When config/arm_reads.yaml cannot be read, the ranking marks it in `unreadable` ("arm reads"). The weekly review
    then queues no read and adds one `arm_reads_unreadable` action, which goes to the backlog issue.
  - The strategy board says "due now" only once the day minimum is also met.
  - The Z4 and Z7 VOID verdicts are kept as comments in arm_reads.yaml, not as `reads` rows: a row would show a read
    that never happened as one. Their descriptive figures are in docs/research/weekly-decisions-2026-11-14.md.
  - BACKLOG 262: 7 closed exploration rows have a P&L but no fill price. This branch does not fix them.
  - The board and ranking UI do not show the day count yet. Review: quant-reviewer (read rule encoding, the verdicts).
