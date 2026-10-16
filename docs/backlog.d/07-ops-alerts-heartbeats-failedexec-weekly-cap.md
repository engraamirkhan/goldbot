---
number: 7
title: 'Ops alerts: heartbeats, FAILED_EXEC, weekly cap'
stage: Operational resilience · RUN
size: S–M
status: 'done (PR #62, main 1a194e9)'
---

### 7. Ops alerts
Value: an unattended system must tell the owner when it is silent, failing to fill or capped (RUN).
Acceptance:
- Every service (engine, bridge, scheduler, supervisor, API, Telegram) writes a heartbeat. A Telegram alert fires
  within 6 minutes of 5 minutes' silence, once per incident, with a recovery message. Tested with a fake clock (S5).
- A `FAILED_EXEC` after the retries sends a Telegram alert with account, side, lots and retcode (X6).
- A weekly 5% cap trip sends the owner a Telegram message once per week per account (R11).
- Alerts only add information: a test asserts that no alert path changes orders, sizing or halts.

Rows: S5, X6, R11.

Sweep 2026-10-16 (product owner): Done (PR #62, main 1a194e9). Acceptance record: merged on green CI into main; checked against git and the named tests exist; the criteria were not re-run one by one in this sweep.
