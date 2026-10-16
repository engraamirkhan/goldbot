---
section: next
---
- **H-06 run-input table written; its count says withdraw (BACKLOG 48, 2026-10-16; quant review).** The H-06
  section of `preregistration-2027Q1.md` now has the run inputs in the H-01/H-04 format: `tsmom`, 2010-2026,
  `variants ["slow_macro"]`, `macro true`, agent `tsmom-g0-fed4baec9f`, hash `5650ad206bda`, `f-51ac5ebabb`,
  data-v1 + macro-v1. The writer's dry run accepts the spec row. count-owed-events (run 38931951849, `main` @
  `eafc6ce`) gives H-06 1,105 signals (711 long / 394 short) and a one-at-a-time lower bound of **107, below the 150
  floor**. **Next (research director):** once the quant reviewer confirms `count_h06`, withdraw H-06 by its rule
  (spec `withdrawn:`, `reservation` 8, `research.reserved_trials_quarter` 2027Q1 8). No trial is spent. The same run
  gives H-01's lower bound as 152, not the doc's 154; this still clears 150, and the gap is explained with the
  2026-11-01 recount paste.
