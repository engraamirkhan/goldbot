- Engine loop heartbeat and watchdogs (2026-10-11, SRE first-deploy risk). A hung engine used to show only after 45
  minutes. Now each loop pass writes `state/heartbeat_engine-<account>.json`, with or without ticks
  (goldbot/ops/watchdog.py `LoopWatch`). Health check `engine_loop:<account>` FAILs after 150 s.
  goldbot-engine@.service is `Type=notify` with `WatchdogSec=300`, so systemd restarts a stuck loop, and the
  restart reconciles with the broker as usual. On the MT5 box, `goldbot-bridge-watchdog@.timer` restarts a bridge
  that stops answering `GET /health`. Hosts set up earlier need the units reinstalled, then the rollback floor set
  to this commit: RUNBOOK "Engine loop stuck, and the watchdogs". Tests: tests/test_engine_watchdog.py.
