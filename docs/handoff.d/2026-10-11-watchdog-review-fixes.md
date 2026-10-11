- Engine watchdog review fixes (2026-10-11, recommended for the first demo deploy on Tue 13 Oct).
  - MEDIUM-1: brain_bootstrap.sh writes the bootstrap commit to `/etc/goldbot/rollback-floor` when no floor exists
    (root-owned, 0644).
  - LOW-1: the loop heartbeat now carries `last_pass_s` and `max_pass_s`, and any pass over 30 s is logged.
  - LOW-2: both bootstraps turn apport off and set `kernel.core_pattern=|/bin/false` in sysctl.d.
  - LOW-3: the bridge watchdog needs at least 1 probe and a timeout of at least 1 s.
  - docs/deploy/first-deploy.md: a core_pattern and floor check (1d), watchdog acceptance plus a `kill -STOP` drill
    (6W), and a `WatchdogSec=0` fallback.
