---
number: 111
rank: 44
title: Allocator against controls (shuffled-weight null and journal replay)
stage: 'Research · MEASURE'
size: M
status: 'proposed (research director, time-structure synthesis, 2026-10-26) · accountable: ml-researcher (design), implementer · reviews: quant-reviewer, architect'
---
- **111. Allocator against controls (shuffled-weight null and journal replay).** Value: the rule-table allocator (`goldbot/allocator/rules.py`) weights every family, but it has never been compared with a control. Slice H's stylised test (allocator 0.47 vs shuffled weights 0.94) was ruled undetermined by the architect: day-level regime, no calendar, a single-day shuffle, F1 selected in-sample (docs/research/time-structure-2026-10-26.md 2.5). Acceptance: (1) four streams fixed in writing before the run (pre-specified arm shapes, not slice-selected), costed at $1.07/oz; (2) the allocator-weighted book vs a null of week-block-shuffled regime labels (at least 500 draws, seeded), on 2010-2017 only; (3) a decision-journal replay of the live engine with the allocator's weights vs all weights = 1, on admissible (H-52) decisions only; (4) the report states which wins with a CI and changes no weight (test); (5) a change to the allocator needs its own pre-registration.
