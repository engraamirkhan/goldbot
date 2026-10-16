---
section: next
---
- **H-06 run-input table written; its count says withdraw (BACKLOG 48, 2026-10-16; quant review).** The H-06
  section of `preregistration-2027Q1.md` now has the run inputs in the H-01/H-04 format: `tsmom`, 2010-2026,
  `variants ["slow_macro"]`, `macro true`, agent `tsmom-g0-fed4baec9f`, hash `5650ad206bda`, `f-51ac5ebabb`,
  data-v1 + macro-v1. The writer's dry run accepts the spec row. count-owed-events (run 38931951849, `main` @
  `eafc6ce`) gives H-06 1,105 signals (711 long / 394 short) and a one-at-a-time lower bound of **107, below the 150
  floor**. **Withdrawn by its rule (2026-10-16, quant reviewer approved):** the spec's H-06 row carries
  `withdrawn:` (107 < 150, run 38931951849), `reservation` 8 and `research.reserved_trials_quarter` 2027Q1 = 8 in one
  change; the writer's dry run accepts it (4 queued + 4 slots) and writes nothing (`freeze_commit` still empty). No
  trial is spent. The same run gives H-01's lower bound as 152, not the doc's 154 (provisional: identical signals,
  lower-bound arithmetic, the 154 was an unscripted count). `count_owed_events.py`'s lower-bound loop was one bar
  stricter than `one_at_a_time` and is fixed (`test_tsmom_lower_bound_equals_one_at_a_time_on_time_only_rows`).
  **Next (strategy researcher):** re-dispatch count-owed-events after this merges and paste the fixed H-01 (and
  H-06) lower bounds into `preregistration-2027Q1.md` by 2026-11-01.
