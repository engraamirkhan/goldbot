---
number: 60
rank: 5
title: Approval window expiry trusts a future-stamped proposal
stage: Approvals (owner confirms entries, 90 s) · PROTECT
size: S
status: 'changes requested (bf5240b/2bfeb36) · trading-safety-reviewer CHANGES 2026-10-16 at 626b56b: 3 decision paths expire at 90 s and future/NaN stamps are refused (tests/test_approval_window_clock.py); F60-1 LOW a pre-restart click can apply to the same proposal re-raised after a restart (runner.py:486-498, bus.py:186-198); F60-2 LOW checks/services.py:138 reads NaN/future as fine; fix lane running, then re-verify of F60-1/F60-2 only · accountable: implementer, then trading-safety-reviewer · due 2026-10-20'
---
- **60. Approval window expiry trusts a future-stamped proposal (review of BACKLOG 58, 2026-10-16).** Value:
  `Proposal.expired` (goldbot/telegram/approvals.py:120) is `time.time() > created + window_s`, the same
  `now - ts > limit` shape BACKLOG 58 closed for heartbeats. A backward clock step of S seconds stretches the owner's
  90 s window by S, and a pending file stamped in the future (a restored backup, a bad write) stays approvable for
  longer than 90 s. The RiskGate runs again at approval, so the caps still hold, but the `EXPIRED_UNAPPROVED`
  contract (an entry the owner did not confirm within 90 s never opens) loosens. Accountable: trading-safety-reviewer;
  built by the implementer; due 2026-10-23. Acceptance:
  - a proposal whose `created` is more than `CLOCK_AHEAD_TOLERANCE_S` (5 s) ahead of now counts as expired, never
    approvable (`goldbot.data.timeutil.heartbeat_problem` or the same rule), and a non-finite `created` too;
  - the engine also holds a monotonic deadline per proposal it raised (`time.monotonic()` at creation + `window_s`),
    and refuses an approval after it whatever the wall clock says; a restart drops the deadline and the proposal
    (fail closed);
  - the bus, Telegram and dashboard show such a proposal as expired, and the digest counts it as `EXPIRED_UNAPPROVED`;
  - tests: `created` 10 s ahead is expired; 2 s ahead is still open; a backward wall-clock step during the window does
    not let an approval through after the monotonic deadline; NaN or a string `created` is expired.
