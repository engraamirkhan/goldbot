---
number: 58
rank: 47
title: Fail closed on heartbeats stamped in the future
stage: Execution and risk (supervisor) · PROTECT
size: S
status: done 2026-10-16 (needs trading-safety and SRE review)
---
- **58. Fail closed on heartbeats stamped in the future (found 2026-10-16).** Value: every liveness check had the
  shape `now - ts > limit`. A `ts` in the future (a clock step, a bad write, a state backup restored from a box whose
  clock ran fast) made the age negative, and a NaN made every comparison False, so the heartbeat read as fresh for
  ever. A dead supervisor would then never halt the engines. Two tests in tests/test_telegram_status.py relied on
  this until 14 Oct. Acceptance:
  - `Supervisor.engine_should_halt` halts with `supervisor_heartbeat_future` when `supervisor.json` is stamped more
    than `CLOCK_AHEAD_TOLERANCE_S` (5 s) ahead, and with `supervisor_state_unreadable` when `ts` is not a finite
    number or the file is not an object; a skew of 1-2 s does not halt;
  - the supervisor halts every entry (`engine_heartbeat_future`, file named in `future_engines`) on an engine file
    stamped more than 5 s ahead;
  - both reasons have plain-language text on the dashboard and Telegram (entry_status `REASONS`) and a RUNBOOK row;
  - health fails `supervisor`, `engine:<account>`, `engine_loop:<account>`, `heartbeat:<service>` and `scheduler`
    (warns `alerts`) on a stamp more than `HEALTH_CLOCK_AHEAD_S` (5 s) ahead of max(the pass's `now`, the wall
    clock), so a slow pass never flags a stamp written while it ran, and on a NaN or non-numeric stamp;
  - the Feeds page shows such a supervisor heartbeat as not reporting, the dashboard does not show combined caps from
    it, and a bridge verdict confirmed in the future counts as `unchecked`;
  - tests/test_heartbeat_clock_ahead.py covers each: beyond tolerance fails, within tolerance does not, NaN/str fails.
