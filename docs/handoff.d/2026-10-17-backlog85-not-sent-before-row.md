- An approval that raises before its order row exists is archived "not sent", never "approved and sent" or NOT
  CONFIRMED (BACKLOG 85, implementer, 2026-10-17; trading-safety residual 1 on BACKLOG 84).
  - `Engine._on_decision` now wraps the old body (`_apply_decision`); on any raise it calls
    `unconfirmed.archive_not_sent`, then re-raises. With no `pending_orders` row for the proposal (nothing can have
    reached the broker) and no answer archived yet, the card gets the refusal code `not_sent:mt5_box` (a bridge error
    or a connection failure, e.g. `account()` at approval) or `not_sent:engine` (anything else), `placing` cleared,
    archived, one owner notice and a `not_sent` journal row. A raise after the row exists keeps BACKLOG 84 (NOT
    CONFIRMED, entries refused until the look-up settles it). No row, so nothing is pending and the next entry is
    allowed.
  - Wording in `goldbot/execution/not_placed.py` (`not_sent_code`, `NotPlaced.not_sent`, `by` gains "engine"): card
    "🛑 NOT PLACED: approved, but the order could not be sent: the MT5 box couldn't be reached. Nothing was sent.";
    dashboard red "Could not be sent. …; nothing was sent."; the notice names the trade and the masked account, never
    the proposal id, the exception, a host or a login. Rows added to docs/ux/order-not-placed.md for ui-ux approval.
  - API contract: `NotPlaced.by` gains "engine" and `NotPlaced.not_sent` (bool) is added; web/openapi.json and
    web/src/lib/schema.d.ts regenerated, `ProposalCard` picks the "Could not be sent" heading.
  - Trading-safety review LOW: only `BridgeError`, `ConnectionError` and `TimeoutError` read "the MT5 box couldn't be
    reached"; any other `OSError` (a local disk or file error) is `not_sent:engine`. The digest and the performance
    report count a not-sent card under "not placed by the MT5 box, the broker or the engine" (`CAUSE_PHRASE`).
