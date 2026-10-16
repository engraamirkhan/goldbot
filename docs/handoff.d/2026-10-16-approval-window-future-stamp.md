- The approval window no longer trusts the wall clock alone (2026-10-16, BACKLOG 60; needs trading-safety review).
  `Proposal.expired` was `time.time() > created + window_s`, so a backward clock step stretched the owner's 90 s
  window and a pending file stamped in the future (a restored backup, a bad write) stayed approvable. Now
  `window_over` (goldbot/telegram/approvals.py) uses the heartbeat rule of BACKLOG 58: a `created` more than
  `CLOCK_AHEAD_TOLERANCE_S` (5 s) ahead, or not a finite number, counts as expired. The bus, the outbox, the dashboard
  list and the digest all use it. The digest counts such a pending proposal as EXPIRED_UNAPPROVED. The engine's
  `ApprovalCenter` also keeps a monotonic deadline per proposal it raised, set at `propose` to now + `window_s`. A
  click after that deadline counts as EXPIRED_UNAPPROVED whatever the wall clock says. The deadline is in memory only,
  with no new persisted field. After a restart the engine holds no proposal, so one left on disk is never executed,
  and it expires by the wall-clock rule with the guard. One stamped days ahead is archived at engine start. Tests:
  tests/test_approval_window_clock.py.
