- BACKLOG 63, card-bound approvals (2026-10-16, implementer). Review not yet recorded: routed to
  `trading-safety-reviewer` (APPROVE needed at the merge, E1) and `code-reviewer`. TRACEABILITY A16.
  - A tap is bound to the card the owner saw, not to the pending file at submit time (the BACKLOG 60 binding).
    Telegram button data is `ok:<token>:<id>` / `no:<reason letter>:<token>:<id>`, where the token is the card's
    `created` as its 8 float bytes in base64url (11 characters, exact). `/api/proposals` sends `created`, and the
    dashboard sends it back in `POST /api/decisions` (required: a request without it is a 422).
  - `ApprovalBus.submit(..., card_created=)` is keyword-only and required. None is refused (`card_missing`), and a
    stamp that differs from the pending proposal's is refused (`card_mismatch`, logged). `ApprovalCenter.poll_bus`
    refuses both again (defence in depth for a re-raise after the check), logs the reason, discards the decision
    and notifies the owner. The BACKLOG 60 fallback (an unbound decision applied when stamped after its proposal)
    is removed: an unbound decision is never applied.
  - 64-byte bound: account names are capped at `ACCOUNT_ID_MAX_BYTES = 24` (UTF-8) in `load_accounts`, so the
    longest proposal id gives 63-byte reject data. `decision_callbacks` raises rather than cut an id that does not
    fit. Today's names (icm-demo, vantage-demo, icm-live) are well inside.
  - Rollout: Telegram cards sent before the deploy are refused on tap (`card_missing`) and expire within 90 s. An
    open dashboard tab with the old bundle gets a 422 until reloaded. Rollback: a BACKLOG 60 engine reads
    `card_created` and checks it against its proposal; an older engine refuses the field (strict `BusDecision`),
    so the proposal expires. Deploy the API, Telegram and engine services together.
  - Tests: tests/test_card_bound_approvals.py (19). Existing callers now decide on the shown card through
    `tests/cards.py`. `test_an_unbound_decision_from_an_older_release_is_never_applied_whatever_its_stamp`
    replaces the BACKLOG 60 fallback test.
  - Review round on 2c6e805 (trading-safety APPROVE, 194 passed; code-reviewer APPROVE, 30/30; security PUSH-OK;
    all 2026-10-16). The LOW and INFO findings are fixed in the follow-up `fix:` commit:
    - Security LOW: proposal ids are allow-listed (`PROPOSAL_ID_PATTERN = ^[A-Za-z0-9_.-]{1,47}$`, and no `..`) in
      `parse_decision_callback`, `decision_callbacks`, `ApprovalBus.submit/get/discard_decision` and
      `Decision.proposal_id` (a 422 for anything outside the pattern; `..` gets a 400 from the bus). An empty id is
      refused at parse (trading-safety INFO-2).
    - Trading-safety LOW-1: a card whose buttons cannot be built is logged and the owner is told. It is recorded as
      sent, so the pump moves on to later cards, outcome edits and reports; the proposal expires undecided.
    - Trading-safety LOW-2: the engine start (`goldbot/ops/services/engine.py::approval_ids_problem`) checks the
      longest id from `Engine._proposal_id` before any broker call and refuses to start (`startup_error`) when no
      card could carry it. runner.py is untouched.
    - INFO-1: a deploy tap with deploys off answers "no longer on offer".
    - Tests (tests/test_card_bound_approvals.py): the traversal and empty ids at parse, bus and dashboard;
      `test_a_card_that_cannot_be_built_is_skipped_the_owner_is_told_and_later_cards_still_go_out`;
      `test_an_engine_whose_proposal_ids_no_card_can_carry_refuses_to_start_before_any_broker_call`;
      `test_a_deploy_tap_with_no_deploy_watch_says_no_longer_on_offer`.
