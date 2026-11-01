# 2026-11-01: a broker-side "Service is not available" latched vantage-demo's login off for 6 h

Status: closed (every prevention item shipped)
Severity: S2 a service down, no trading impact
Author: sre. Reviewed by: (code-reviewer, trading-safety-reviewer, security-reviewer on the PR).

## Summary
At 01:32 UTC the vantage-demo bridge logged in from the keyring while the broker's auth service was down. The
MetaTrader5 package reported `-6 Authorization failed`, which the login ledger treats as a refused password, so the
mark latched every later login off. The bridge stayed down about 5 h 50 min and the brain deploy of 3c83b6fb rolled
back because the vantage-demo engine could not reach its bridge. Markets were closed; nothing was traded or lost.

## Impact
- goldbot-bridge@vantage-demo: no healthy /health from 01:29 to about 07:21 UTC (Sunday, market closed).
- goldbot-engine@vantage-demo: BridgeUnavailable; the brain deploy of 3c83b6fb rolled back to 37d315d1.
- The watchdog restarted the terminal about every 12 min (about 30 restarts), each killed at the 90 s stop timeout.
- Safe all along: 0 open positions on vantage-demo; no credential was resent into a refusal (the latch did what it
  was built for, just on the wrong evidence).

## Timeline (UTC)
| Time | Event |
| --- | --- |
| 01:28:09 | terminal journal: connection to the broker's demo server lost (broker side) |
| 01:29:41 | watchdog: /health stopped answering; bridge and terminal restarted |
| 01:32:26 | attach without login: IPC timeout (-10005); keyring login: `-6`; journal: `authorization ... failed (Service is not available)`; ledger writes `vantage-demo.refused` |
| 01:37:37 | the terminal authorizes by itself with its saved login; the bridge's mark still blocks every start |
| 01:41-07:15 | watchdog restart loop of the terminal; every bridge start stops at the mark |
| morning | brain deploy of 3c83b6fb rolls back (engine BridgeUnavailable); incident opened |
| 07:20 | evidence: journal reason broker-side; keyring entries untouched by the vantage-247 work (hashes distinct, the stored password was later accepted); right terminal (`C:\MT5\ICMarkets`, the configured path) |
| 07:21 | watchdog timer and bridge stopped; the terminal's stray Login dialog dismissed; `goldbot-mt5 login vantage-demo --retry-refused` (one try): ok |
| 07:22 | bridge started: /health ok, terminal ok, api_version 9 |
| 07:23:41 | `goldbot-release 3c83b6fb...`: DEPLOYED |

## Root cause
`goldbot/ops/login.py` mapped every `-6` to "the broker refused the password". The MetaTrader5 package returns the
same `-6 Terminal: Authorization failed` when the broker could not judge the password at all (its service was down
during weekend maintenance). The reason is only in the terminal's own journal, which the login did not read. The test
suite had no case for a broker-side `-6`, because the latch was designed (correctly) against lockouts and the
ambiguity of the package's code was not known.

## Detection
Found through the deploy rollback, about 6 h late. /health already reported `login: refused`, but nothing alerts on a
latched refusal while the market is closed, and the watchdog's terminal restarts hid it as churn.

## Recovery
docs/RUNBOOK.md, "MT5 login" paragraph: read the journal reason (digits masked); broker-side reason followed by an
`authorized on` line: stop the watchdog timer and the bridge, `goldbot-mt5 login <account> --retry-refused` once,
start the bridge and the timer. `Invalid account`: stop; never retry.

## Prevention
| Item | Type | Where | Status |
| --- | --- | --- | --- |
| `-6` latches only when the journal names bad credentials or nothing known; `Service is not available`, `No connection`, server busy, timeout are retried under the backoff and hourly cap | test + code | goldbot/ops/login.py, tests/test_mt5_login.py | shipped (this PR) |
| Runbook step: tell a broker-side `-6` from a refused password, and the one-try recovery | runbook | docs/RUNBOOK.md | shipped (this PR) |
| Watchdog never restarts the terminal while the login refusal mark stands (tested as mt5, never followed by root); the brain pages once per outage: `terminal:<account>` FAILs on `login: refused` with both recoveries, `bridge:<account>` FAILs after 5 min unreachable (warning before, one page per outage, closed after 3 min answering) | test + check | goldbot/ops/linux/goldbot-bridge-watchdog.sh, goldbot/ops/checks/bridge.py, goldbot/execution/bridge.py probe_terminal; tests/test_engine_watchdog.py, tests/test_mt5_terminal_state.py | shipped (2026-11-01, brought forward from 11-08) |

## Lessons
- Went well: the latch held; no credential was resent into a real refusal. The terminal journal gave a definite answer.
- Did not: a single ambiguous error code was trusted as a verdict. Read the source of truth (the terminal journal)
  before latching anything that needs a human to clear.
- Lucky: Sunday, market closed, no positions.
