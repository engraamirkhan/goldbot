- The risk file notes schema skew (BACKLOG 74, ADR 0023 addendum A1, implementer, 2026-10-17; TRACEABILITY X7).
  `JsonEngineStore.load_risk` now calls `note_schema_skew("risk_<account>.json", "risk file", extra)` for keys that
  are not `RiskSnapshot` fields, as the orders loader already did. The keys are still ignored and nothing raises, so a
  rollback below a release that added a risk field shows in health (`schema_skew_summary`) and the halt and stage
  load as before.
  - Tests: `tests/test_store.py` (the note, and no note for a file this release wrote). The two step-1 pins that
    asserted "no note" now assert the load (golden edge test renamed to
    `test_a_newer_key_in_the_risk_file_is_ignored_and_the_file_still_loads`); the golden JSON files are unchanged.
  - Not changed: runner.py, the risk file's format, RiskGate, any order or exit path.
