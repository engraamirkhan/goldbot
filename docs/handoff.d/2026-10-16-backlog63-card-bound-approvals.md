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
