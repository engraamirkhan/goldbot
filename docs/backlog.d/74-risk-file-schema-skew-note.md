---
number: 74
rank: 10
title: The risk file notes schema skew on unknown keys (ADR 0010 gap)
stage: Execution and reconciliation (persisted risk state) · PROTECT
size: S
status: 'ready after the ADR 0023 step-1 move (architect addendum A1, 2026-10-17) · own PR in engine/store.py before step 2 · reviews: trading-safety, code-reviewer · accountable: implementer · due 2026-11-06'
---
- **74. The risk file notes schema skew on unknown keys (ADR 0010 gap).** Value: `_load_risk_state` ignores keys it
  does not know without calling `note_schema_skew`, while the orders loader already notes them (runner.py ~2500); a
  rollback below a release that added a risk field is therefore invisible to health. Acceptance: `store.py` calls
  `note_schema_skew(src, "risk file", extra)` and keeps ignoring the keys — never raises, so the file loads as today
  apart from the note (`tests/test_store.py::test_risk_file_unknown_key_notes_skew_and_still_loads`); the PR 0 golden
  edge test asserts only that the file loads, not "no note". Ordered after the step-1 move (ADR 0023 S1/S7: the move
  changes no golden) — trading-safety ruled the frozen behaviour fail-safe (a newer key is ignored, halt and stage still
  load, nothing widens).
