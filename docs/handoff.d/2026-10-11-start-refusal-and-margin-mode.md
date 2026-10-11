- Start refusal named, bridge protocol v4 required (2026-10-11, trading-safety re-verification LOW-A and LOW-B on
  the open-risk / MT5 branch). When the terminal-account check refuses an engine start (another login or server, demo
  vs real, or a margin mode that is not hedging), `ops.run.refuse_start` now writes `startup_error` (and the deposit
  currency, for the supervisor) into engine_<account>.json before the SystemExit, and health `engine:<account>` FAILs
  with "engine refused to start: <cause>". A margin mode the bridge does not report says "bridge older than protocol
  v4: deploy the MT5 box first". `bridge.MIN_COMPATIBLE` is 4, so `bridge_compat` and preflight also name a v3
  bridge (pinned in tests/test_bridge_compat.py). Deploy order, in the RUNBOOK ("Bridge compatibility") and
  docs/deploy/2026-10-batch.md section 3: the MT5 box first; brain-first only with a flat book. S12 in
  docs/AGENT_STANDARDS.md records the exception: a new bridge response field may carry a restriction only if an
  unknown or missing value fails closed on the brain AND the bridge enforces it independently, with a protocol bump
  (precedent: `AccountInfo.margin_mode`). Tests: tests/test_start_refusal.py.
