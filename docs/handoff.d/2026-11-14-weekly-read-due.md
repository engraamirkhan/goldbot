- 2026-11-14 (branch fix/weekly-read-due; R156; BACKLOG 262): the weekly review no longer raises `read_due` from the
  fill count alone. On 2026-11-14 it raised two false alarms, for H-81:Z7 (300th fill on 4 session days) and H-81:Z4
  (300th fill on 8 session days). Both units had been retired under A5 on 2026-11-06.
  - config/arm_reads.yaml has a new `min_session_days` map, per arm like `read_at`. It is set to 20 for the HV arms
    and their twins, F1A, G1A, every Z arm and the VS twins, following HV "Graduation" and H-81 section 4 ("at the
    300th admissible family fill on >= 20 session days"). Any other arm has no minimum, so its behaviour is unchanged.
    The arms of a pooled unit must share one minimum; if they do not, no unit is read (fail closed).
  - The ranking's `RankProgress` gains `n_days` and `min_days` (API contract regenerated). `n_days` counts the
    distinct opportunity days of the unit's counted fills. A unit whose every arm is retired unread (`retired_day`,
    or at its registered stop) gets no progress, the same as a unit that is already read. `read_due` now needs both
    the fill count and the day count.
  - The evaluator's two void reads are recorded in `reads` (state R). docs/research/weekly-decisions-2026-11-14.md
    records the week's decisions.
  - BACKLOG 262: 7 closed exploration rows have a P&L but no fill price. This branch does not fix them.
  - The board and ranking UI do not show the day count yet. Review: quant-reviewer (read rule encoding, the recorded
    reads).
