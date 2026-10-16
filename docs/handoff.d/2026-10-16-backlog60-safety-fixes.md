- Approval window: trading-safety findings on BACKLOG 60 and the dashboard audit follow-up to BACKLOG 52
  (2026-10-16). Review not yet recorded: routed to `trading-safety-reviewer` (F60-1, F60-2) and `security-reviewer`
  (audit).
  - F60-1 (LOW): a click made before a restart could be applied to the same proposal raised again after it. The
    proposal id depends only on account, agent and bar close, and a restarted engine can re-decide the last close.
    The engine start now archives every pending file of its own account, inside its window or not, with outcome
    None. `archive` also deletes the decision file. The account is checked on the record as well as by the file
    glob, so an account named `<id>-<suffix>` is never touched. Defence in depth: `ApprovalCenter.poll_bus` discards
    a decision stamped more than CLOCK_AHEAD_TOLERANCE_S before its proposal's `created` (new
    `ApprovalBus.discard_decision`), so the card shown stays decidable. Tests:
    `test_after_a_restart_an_on_disk_proposal_is_never_executed_and_falls_back_to_the_wall_clock` (updated),
    `test_a_click_made_before_a_restart_is_never_applied_to_the_same_proposal_raised_again`,
    `test_a_decision_stamped_before_its_proposal_was_raised_is_discarded_and_never_applied`,
    `test_a_decision_stamped_a_couple_of_seconds_before_its_proposal_is_still_applied`
    (tests/test_approval_window_clock.py), and
    `test_every_proposal_of_this_account_expired_or_not_is_archived_on_start` (tests/test_cov_engine.py, renamed
    from the expired-only rule).
  - F60-2 (LOW, health only): the `approvals` health check uses `window_over(created, window_s + grace, now)`, so a
    NaN or future `created` is reported as stuck rather than read as fine. Test:
    `test_a_proposal_stamped_in_the_future_or_not_a_number_is_reported_stuck` (tests/test_health.py).
  - Audit: `POST /api/decisions` catches and logs an audit failure (channel and proposal, never the email) and
    still returns SUBMITTED, as the Telegram service does. Before, the decision stood on the bus while the owner saw
    a 500. Test: `test_a_failing_audit_never_undoes_or_hides_a_dashboard_decision`
    (tests/test_decision_identity_followup.py). The API contract does not change.
