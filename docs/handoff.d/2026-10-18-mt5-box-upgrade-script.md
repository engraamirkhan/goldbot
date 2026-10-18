- **Attended MT5 box upgrade scripted (sre, 2026-10-18; for Sat 24 Oct 10:00 UK; not yet reviewed).**
  `goldbot/ops/linux/mt5-box-upgrade.sh` has phases `precheck`, `install`, `post-reboot`, each re-runnable, printing a
  PASS/FAIL table of states only. It refuses unless the market is closed (UTC window), no engine unit is active on the
  box and the PD attests `--book-flat --engine-stopped` (the box cannot see the brain's engine or positions). Install
  runs `goldbot-deploy <sha>`, then the four units and watchdog/probe scripts from root's own clone at exactly the sha,
  stops the transient `goldbot-mt5-terminal-check`, enables the terminal unit and leaves the watchdog off; post-reboot
  enables the watchdog timer and writes `/etc/goldbot/rollback-floor` only when the cold restart passes. RUNBOOK 0.5
  "Attended MT5 box upgrade" has the single-line commands and the rollback if the test fails. Tests:
  tests/test_mt5_box_upgrade_sh.py (29, fake systemctl/sudo/install, local git remote); shellcheck clean. Reviews due:
  security (root-run, hash-checked download), SRE. Not run on a server.
