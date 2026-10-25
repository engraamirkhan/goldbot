# 2026-10-22: the icm-demo MT5 bridge was down 23 h after an unattended library update

Status: open (closed when the demo box has run the attended upgrade, RUNBOOK 0.5, and the needrestart drop-in is on
both MT5 boxes and the brain)
Severity: S1 trading stopped on icm-demo for a full session (demo money; no open positions are known to have been affected)
Author: sre. Reviewed by: pending (code-reviewer, security-reviewer: root-run scripts).

## Summary
At 06:58 UTC unattended-upgrades installed a liblzma5 security update on the icm-demo MT5 box. needrestart, which
it runs afterwards, restarted every service mapping the old library, including the hand-started transient
`goldbot-mt5-terminal-check` (the MT5 terminal), goldbot-xvfb and `goldbot-bridge@icm-demo`. The terminal did not stop
in time and was SIGKILLed; a `winedevice.exe` survived in its cgroup and the terminal that systemd then started never
answered MT5 IPC. The bridge crash-looped on `mt5.initialize failed: (-10005, 'IPC timeout')` 279 times and never
listened on 8765, so every engine RPC through the tunnel was reset. It recovered at 06:23 UTC the next day only
because a zlib1g update made needrestart restart the terminal cleanly.

## Impact
- 2026-10-22 06:59 to 2026-10-23 06:36 UTC: no ticks, entries or engine-managed exits on icm-demo (the engine logged
  `BridgeUnavailable` every minute). Broker-side stops stayed in place.
- Detection: `bridge:icm-demo` FAILs on the brain and is alerted once on the transition; it is not repeated while it
  stays failing. Telegram sends are not logged, so whether the owner received that alert is not proven.

## Timeline (UTC)
| Time | Event |
| --- | --- |
| 10-22 06:58:29 | unattended-upgrade: liblzma5, xz-utils |
| 06:59:35 to 06:59:55 | systemd daemon re-exec; needrestart stops terminal-check, bridge, xvfb |
| 07:01:26 | terminal SIGKILLed after the stop timeout; winedevice.exe left over; terminal-check started again |
| 07:00 onward | bridge restarts every ~3 min on IPC timeout; old watchdog skips it (not active, or up under 300 s) |
| 07:07 | second unattended-upgrade (ca-certificates), a second needrestart round |
| 10-23 06:23:01 | unattended-upgrade: zlib1g; needrestart restarts terminal-check cleanly |
| 06:36:28 | bridge attaches: "bridge serving GuardedBroker" |

## Root cause
1. The demo box never ran the attended upgrade (RUNBOOK 0.5, planned Sat 24 Oct and not run yet). It still has the old
   `goldbot-bridge@.service` (no `Requires=goldbot-mt5-terminal@`), the old `/health`-only watchdog, no probe and no
   terminal unit; its terminal is a hand-started transient unit. The current watchdog restarts the terminal unit when
   the bridge crash-loops while that unit has run 10 min (goldbot-bridge-watchdog.sh, the attach-loop rule). That rule
   would have caught this in about 15 min, but it needs the terminal unit.
2. needrestart was allowed to restart goldbot units during market hours.
3. `goldbot-deploy-manual.service` (failed) is unrelated: a manual deploy of c28ed08 on 10-16 refused because CI had
   not passed on that commit. The box runs f0ef28e1 code (deployed 10-20).

## Prevention
- [x] `/etc/needrestart/conf.d/goldbot.conf` keeps every goldbot-* unit out of needrestart's restarts (installed by
  mt5_bootstrap.sh, brain_bootstrap.sh and mt5-box-upgrade.sh install); tests in tests/test_mt5_box_upgrade_sh.py.
- [x] The watchdog logs an attach loop when the terminal unit is not active (the box is not on the terminal unit), so
  the journal names the cause; test in tests/test_engine_watchdog.py.
- [ ] Run the attended upgrade on the demo box (RUNBOOK 0.5) with the market closed.
- [ ] Repeat a FAIL alert for `bridge:` and `engine:` while it lasts (every 30 min) and log every Telegram send.
- [ ] Planned weekly restart, with the market closed, so goldbot services pick up library updates.
