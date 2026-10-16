- Heartbeats stamped in the future fail closed (2026-10-16, BACKLOG 58). Before this, a `ts` ahead of the reader's
  clock, or a NaN, made `now - ts > limit` False, so a dead supervisor whose last write carried a fast clock (or came
  from a restored backup) never halted the engines. The shared rule is `goldbot.data.timeutil.heartbeat_problem`:
  a stamp that is not a finite number is `invalid`, one more than a tolerance ahead is `future`, one older than the
  limit is `stale`. Engines halt on `supervisor_heartbeat_future` (more than 5 s ahead, `CLOCK_AHEAD_TOLERANCE_S`)
  or `supervisor_state_unreadable` (a non-numeric or NaN `ts`, or a file that is not an object). The supervisor halts
  every entry on an engine file more than 5 s ahead (`engine_heartbeat_future`, files in `future_engines`; that
  file's currency and money still count). Health fails the supervisor, engine, engine loop, service heartbeat and
  scheduler lines (warns the alert loop) on a stamp more than 60 s ahead (`HEALTH_CLOCK_AHEAD_S`: a pass takes its
  `now` once and can run for many seconds). The Feeds page shows such a supervisor heartbeat as not reporting, the
  dashboard hides combined caps read from it, and a bridge verdict confirmed in the future counts as `unchecked`.
  Left alone on purpose: report-age checks (drift, restore drill, ledger, crossfeed, costs, risk-day roll, re-arm,
  stuck approvals, job overdue), where a future stamp only hides a "report old" warning and the scheduler heartbeat
  still catches a dead producer; the bridge's order age (already refuses either way and on NaN); the tick checks
  (`tick_from_future`); in-process timers (watchdog, dead man's switch, runner timers).
