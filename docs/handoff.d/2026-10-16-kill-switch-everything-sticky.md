- The kill switch's close-everything choice is now state (2026-10-16, trading-safety finding F1 MEDIUM; needs
  trading-safety re-review). Before, `everything` was true only on the tripping read, and the owner-mode write
  (`ApprovalBus.set_mode`) ran before the flatten: in auto mode a full disk raised out of it and closed nothing, and a
  positions() read failing on that read did the same; every later run closed only this engine's magic range, so a
  manual or other-magic position stayed open. Now `Engine._kill_everything` (goldbot/engine/runner.py) is set before
  any I/O on the trip, saved with the risk state (`kill_everything` in `risk_<account>.json`; absent = false), used by
  every retry (tick, no-quote pass, halted refresh) and after a restart (`_kill_pending` starts true), and cleared only
  once a run has closed every position on the account. The mode write moved after the flatten into
  `_reset_owner_mode`: it never raises, a failure is a tick error and is retried on the next kill-switch run; the
  in-memory `approval_mode = "propose"` already blocks auto entries. No stop widened, no size added, no new order path.
  ADR 0016 carries the correction. Tests: tests/test_kill_switch_everything.py (6).
