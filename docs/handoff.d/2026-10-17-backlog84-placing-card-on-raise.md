- An approval whose send raises is archived NOT CONFIRMED, never left placing (BACKLOG 84, implementer, 2026-10-17;
  trading-safety residual on BACKLOG 72).
  - `ApprovalCenter._finish`: if `on_decision` raises, the card's `placing` flag is cleared and it is archived with
    the refusal code `not_confirmed` and the facts pending (as `unconfirmed.archive_pending` does), then the error is
    re-raised. A refusal or NOT CONFIRMED the engine already archived stands. The outbox reports it NOT CONFIRMED
    at once instead of after PLACING_MAX_S, and the dashboard no longer shows "submitted" and then drops it.
  - `engine/unconfirmed.py`: a `sending` row (the send raised, or the engine died mid-send and the outbox's
    silent-engine fallback reported the card NOT CONFIRMED) whose position is found later, by `_resolve_sent`
    (restart or stale-sending) or by the reconcile adopt, sends the "opened" notice once and rewrites the card.
    `_card` also rewrites a card still `placing` (`shown_unconfirmed`), which sets `not_confirmed`, the refusal code
    and clears `placing`. A `sending` row with no card shown NOT CONFIRMED (the auto path) is adopted silently, as
    before. No new owner-facing strings and no runner change.
  - Not covered: an engine silent for 60 s whose send then returns a plain fill (status `filled`, no look-up). The
    outbox has already reported NOT CONFIRMED, and no "opened" notice follows. A raise before the order row exists
    (for example `last_tick`) is also archived NOT CONFIRMED with nothing to look up, so the dashboard lists it as
    pending until UNCONFIRMED_LISTED_S. Both are routed to trading-safety.
