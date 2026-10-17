# MT5 terminal lifecycle on the Wine box, and the saved-login cold-restart test

Status: proposal (sre), 2026-10-17. Plan only; server changes stay owner-approved.

## Problem
The terminal runs under a transient unit started by hand on 16-17 Oct: nothing starts it after a reboot or restarts
it when it dies, and the saved login was never tested from cold. The bridge unit's comment ("starts the terminal
itself") assumes `initialize(path=...)` launches it; under Wine that was never verified. `GET /health` returns a fixed
`{"ok": true}` without asking the terminal, so a dead or logged-out terminal goes unnoticed until an RPC fails. Stops
stay at the broker, but the engine cannot manage exits or reconcile.

## 1. Who owns the terminal
**Options:** (a) the bridge process launches the terminal; (b) a new `goldbot-mt5-terminal@.service` alone launches
it and the bridge unit pulls it in. **Choice: (b):** one process per unit, restart and backoff without code; under (a)
a bridge restart could orphan or duplicate the terminal.

- `goldbot-mt5-terminal@.service`: `User=mt5`, `DISPLAY=:99`, after xvfb, `ExecStart=/usr/bin/wine <terminal_path>`,
  `Restart=always`, `RestartSec=15`. Backoff: `RestartSteps=5`, `RestartMaxDelaySec=10min` on systemd 254+ (24.04);
  on 22.04 the fixed 15 s plus the watchdog's limit below.
- **Never two terminals:** `ExecStartPre=` (as mt5) refuses to start while any `terminal64.exe` runs for user mt5. The
  transient unit and RUNBOOK 0.3 step 4's hand `wine ... &` are retired. The RUNBOOK says `systemctl start` instead.
- `goldbot-bridge@.service`: `Requires=` and `After=goldbot-mt5-terminal@%i.service`, so starting the bridge launches
  the terminal first. The bridge's `MT5Broker` init keeps `path=` + `timeout=60000` (it attaches; it no longer
  launches).
- **Dead terminal:** a terminal that exits is restarted by its unit. The bridge sees `terminal_info()` return `None`
  or IPC errors on two consecutive reads, exits non-zero, and `Restart=always` re-attaches it once the terminal is up.
  A **hung** terminal (process alive, IPC dead) is the watchdog's job (section 4).
- Risk to rehearse: bridge and terminal share one wineserver, so stopping the terminal may stop the bridge (it
  restarts). Measure RSS and swap on the 1 GB box before and after; no process is added.

## 2. Cold-restart test of the saved login
**When:** owner reachable (Telegram, VNC tunnel ready), outside London/NY hours, market closed, **not Fri 23 Oct**
(classifier read day). Proposed **Sat 24 Oct, 10:00 UK**, ~2 h; fallback Sat 31 Oct (not Sun 25 Oct: clocks change).
The PD confirms with the owner.

Preconditions: change merged and installed by owner-approved deploy, book flat, engine stopped. As mt5, copy the
terminal's `Config/` to a 0700 directory on the box (it holds the saved login: never off-box, never GitHub).

1. Record: unit states, one `terminal64.exe`, `/health` shows `terminal: ok`, RSS/swap.
2. `sudo reboot`; start a timer.
3. **Pass within 10 min:** xvfb, terminal and bridge active; exactly one terminal; local `/health` shows
   `terminal: ok`. On the brain, `bridge:icm-demo` and `terminal:icm-demo` are ok, and the bridge's start-up account
   check passed. Telegram shows the down alert within 6 min and then the recovery.
4. `sudo systemctl kill -s KILL goldbot-mt5-terminal@icm-demo`. The terminal is back within its backoff and the bridge
   re-attaches, still one terminal.
5. Write the result in `docs/incidents/`.

**Rollback if the login is lost** (`terminal: not_logged_in`): stop the watchdog timer; the owner logs in over VNC
(RUNBOOK 0.3), or restore the `Config/` copy; reboot once more. If it fails again, disable the terminal unit, start
the terminal by hand as before and open a postmortem. Finish well before Sunday's open.

## 3. Health wording
`/health` gains `terminal`, one of `ok | not_running | not_connected | not_logged_in | wrong_account`. It never
carries a login, server or balance. It answers 200 only when the value is `ok` and 503 otherwise. A new brain check,
`terminal:<account>`:
- **FAIL** "MT5 terminal not running on the MT5 box; it is being restarted. No entries; stops stay at the broker. If
  this lasts 10 min: RUNBOOK 'Terminal down'."
- **FAIL** "MT5 terminal running but not logged in (saved login lost?). No entries; stops stay at the broker. Owner
  login over VNC: RUNBOOK 0.3."
- **WARN, then FAIL after 5 min** "MT5 terminal not connected to the broker server."
- **FAIL** "MT5 terminal logged in to a different account than registered; the bridge refuses orders."

## 4. Watchdog logic, and the failing tests to write first
The watchdog parses the body strictly (at most 1 KB, exactly one known value; mt5 controls it), then:
- no answer -> restart the bridge (today's behaviour);
- `not_running`/`not_connected` on every probe, terminal unit active for at least 10 min -> restart the terminal unit
  (at most once per 10 min);
- `not_logged_in`/`wrong_account` -> log only, never restart (a restart cannot restore a login, and a loop would hide
  it).

Tests (`tests/test_engine_watchdog.py`, fake-PATH harness):
1. `ok` -> no restart. 2. `not_running` x3, terminal up 1 h -> restart the terminal unit, not the bridge.
3. Same with the terminal up under 10 min -> no restart. 4. `not_logged_in`/`wrong_account` x3 -> no restart, a log
line naming the state. 5. Unknown, over 1 KB or metacharacter bodies -> "no answer", never evaluated. 6. Mixed probes
-> no restart. 7. Terminal restarted while probing -> no restart. 8. Units: bridge `Requires=`/`After=` the terminal;
terminal `Restart=always` + single-instance `ExecStartPre`; bootstrap installs it from root's clone. 9. Bridge:
`/health` maps fake `terminal_info`/`account_info` to each state with no login digits; two `None` reads exit non-zero.

Reviews: security (root parses mt5 output), trading-safety (bridge exit), code-review.

## Conditions from the security plan review (APPROVE-WITH-CONDITIONS, 0526017, 2026-10-17)

Binding on the build.
1. The `/health` probe runs as a separate unprivileged step or user (`DynamicUser=` or `nobody`) and emits only a
   fixed token: `ok|not_running|not_connected|not_logged_in|wrong_account|noanswer`. Root consumes the token. If one
   root script remains, root never `eval`s, expands or logs the raw body and never puts it in a path, unit name or
   argument; bounded read (`curl --max-filesize 1024 -m …`), exact-match `case` on the allow-list, no regex/grep;
   anything else is `noanswer`.
2. Log only the matched token, never the body (journald log injection).
3. Root's actions stay fixed: `systemctl restart` on two literal units derived from the already-validated `$inst`;
   the body never chooses an action or a unit.
4. Test 5 includes newline, NUL, ANSI and `$(...)` bodies and asserts the log contains no body bytes.
5. The `Config/` backup (holds the saved login): `umask 077`, mt5-owned, in a 0700 directory outside the git clone and
   outside any synced or backed-up path; no scp/upload/cron copy; deleted after the test passes or kept only there;
   never pasted (contents or hashes) into issue comments.
6. Health wording carries no login, server or host; a test fails if `/health` or the check text matches `\d{6,}` or
   contains the server name; `wrong_account` echoes neither account.
7. The incident file in `docs/incidents/` records states and timings only — no login, server, hostname, VNC or tunnel
   details.
