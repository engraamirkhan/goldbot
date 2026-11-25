---
number: 272
rank: 96
title: 'Power-first Q3 follow-ups: clone arms on count-only cards, Q1 source field, unguarded scripts'
stage: research
size: S
status: proposed
---
- **272. Power-first Q3 follow-ups: clone arms on count-only cards, Q1 source field, unguarded scripts.** Value: closes the non-blocking items of the quant re-verify of PR #450, so power stays honest from H-102 onward. Acceptance:
  - **N2, clone arms on count-only cards.** `PowerCard.arms: int = Field(1, ge=1)`, with a validator that requires `learn_uniqueness` when arms > 1 or more than one timeframe is pooled. H-101 keeps its signed verdict. Test: a 2-arm count-only card is invalid.
  - **N3, Q1 provenance.** `rho_day_le_60m_source: str | None`, required when the value is below 0.25 (the RD lane's measurement record, ADR 0085 item 4). Test: 0.05 with no source fails at load.
  - **N4, unguarded scripts.** None of the 20 scripts in `holdout_guard.UNGUARDED` runs until it calls `refuse`/`cut` or windows.py. The RD checklist carries the rule, and each script leaves the list once it is guarded (the test enforces both).
