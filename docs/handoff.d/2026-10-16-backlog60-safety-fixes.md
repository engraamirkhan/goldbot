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
  - Trading-safety re-verify of d5aa35b (CHANGES, 2026-10-16), all three findings fixed in the next commit:
    - Finding 1 (MEDIUM): the old Telegram card stayed live after a restart, and a tap on it approved the same id
      raised again, possibly at another size. The engine now never raises an id archived at its start
      (`restart_archived`) or finished before it (`proposal_finished`), via `Engine._retired_proposal` on the one
      publish path. Ids stay per bar close, because they drive sent_ids and order idempotency. `Outbox.finished`
      reports a card archived with no outcome as expired at once, so it loses its buttons; a tap on it is refused.
      Test (integration): `test_a_restart_inside_the_window_never_raises_the_same_proposal_again_and_the_old_card_expires`.
    - Finding 2 (LOW): the 5 s tolerance is gone. `submit` copies the card's `created` into the decision
      (`BusDecision.card_created`), and `poll_bus` applies a decision only when it matches the proposal held. A
      decision file from an older release, with no card stamp, falls back to `ts < created`, with no tolerance.
      Tests: `test_a_decision_bound_to_an_earlier_card_with_the_same_id_is_discarded_and_the_owner_is_told`,
      `test_submit_binds_the_decision_to_the_card_it_read`,
      `test_an_unbound_decision_from_an_older_release_is_applied_only_if_not_stamped_before_its_proposal`.
      Rollback note: `BusDecision` is strict, so a pre-binding engine refuses a decision file that carries
      `card_created`; the proposal expires (fail closed). Deploy the engines with the API and Telegram services.
    - Finding 3 (INFO): a tap that is dropped (deleted at a restart, or bound to an earlier card) queues an owner
      notice through `ApprovalBus.notify_owner` (state/agent_runs.jsonl, relayed by the Telegram service). Asserted
      in `test_a_click_made_before_a_restart_is_never_applied_to_the_same_proposal_raised_again` and the binding test.
