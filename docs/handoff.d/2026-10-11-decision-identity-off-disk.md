- Decider identity kept out of approval files and the API (BACKLOG 52, TRACEABILITY A14; 2026-10-11).
  - `Proposal` no longer has `decided_by` / `decided_via`, so `approvals/done/` files record only `decided_channel`
    and `decided_ts`. Older files that still carry the fields load normally (they are not counted as schema skew),
    and their identity is dropped as they are read.
  - API contract change: `DecidedProposal.decided_by` is replaced by `decided_channel` and `decided_ts`. The
    dashboard's decided cards say "via Telegram" or "via the dashboard" and no longer show an email.
  - Identity for audit: dashboard decisions are already in the auth audit log (`decision`, with the email).
    Telegram decisions have no identity record anywhere now, because only allow-listed ids can decide.
  - Still on disk: the short-lived `approvals/decisions/<id>.json` keeps `by` ("dashboard:<email>" or
    "telegram:<id>") until the engine archives the proposal (the next tick). Proposed as a follow-up.
  - Needs security and trading-safety review.
