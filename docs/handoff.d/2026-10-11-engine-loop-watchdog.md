- A hung engine shows in about 3 minutes and is restarted after 5 minutes (2026-10-11, SRE first-deploy risk).
  Before this change, a hung engine (process alive, loop stuck) showed only as a stale `engine_<account>.json` after
  45 minutes with the market open, and nothing restarted it.
  - The engine loop writes its own heartbeat, `state/heartbeat_engine-<account>.json`, on every pass (at most every
    15 s), with or without ticks. Code: goldbot/ops/watchdog.py `LoopWatch`, called from
    goldbot/ops/run.py `run_engine`.
  - Health check `engine_loop:<account>` (goldbot/ops/health.py `check_engine_loop`) WARNs after 60 s and FAILs
    after 150 s, whether or not the market is open. The Telegram health pass runs every 60 s, so a hung loop is
    alerted within about 4 minutes.
  - goldbot-engine@.service is now `Type=notify` with `WatchdogSec=300`, `TimeoutStartSec=900` and `LimitCORE=0`.
    READY=1 is sent just before the loop starts; after that only the loop thread sends WATCHDOG=1. A stuck loop gets
    SIGABRT, and `faulthandler` prints every thread's stack to the journal. Then the normal restart runs (restart
    reconciliation, row X7).
  - During the start (bridge connect, sessions, bridge check, warm start, model load), a thread keeps the watchdog
    fed and writes "starting" beats for at most 15 minutes. A slow start therefore never trips the watchdog, and a
    hung start is ended by `TimeoutStartSec`.
  - Exits are never gated by any of this. A stuck loop was already not running exits; the restart brings them back.
    Stops rest with the broker in the meantime.
  - MT5 box: Wine's Windows Python cannot reach the notify socket. Instead, a root timer
    (`goldbot-bridge-watchdog@<account>.timer`, every 2 minutes, script goldbot/ops/linux/goldbot-bridge-watchdog.sh,
    installed to /usr/local/sbin by mt5_bootstrap.sh from root's own clone) probes `GET /health` on 127.0.0.1:8765.
    The bridge serves one request at a time, so a call stuck in the terminal also blocks /health. The timer restarts
    the bridge after 3 failed probes 20 s apart. It skips the first 5 minutes after a (re)start and does nothing if
    the unit was restarted while it probed.
  - Tests: tests/test_engine_watchdog.py.
  - RUNBOOK text still to add when the RUNBOOK is edited (it has no fragment directory):
    - `engine_loop:<account>` FAIL. Run `journalctl -u goldbot-engine@<account> -n 200`. A watchdog kill shows
      "Watchdog timeout" and a Python stack: file that stack in a postmortem. If health keeps failing after the
      restart, it is a start problem: look for `startup_error` in the engine state and check the bridge.
    - Bridge restarted by its watchdog: `journalctl -u goldbot-bridge-watchdog@<account>`, then the MT5 terminal
      over VNC.
  - Existing hosts: `goldbot-deploy` does not reinstall units. Reinstall goldbot-engine@.service from root's clone,
    run `daemon-reload`, then restart the engines. On the MT5 box, rerun the install step. Once these units are in
    place, set `/etc/goldbot/rollback-floor` to this commit: an older release never sends READY=1, so under
    `Type=notify` it would be killed at `TimeoutStartSec` and restart in a loop.
