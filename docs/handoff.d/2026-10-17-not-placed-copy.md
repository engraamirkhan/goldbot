- Order not placed (trading-safety LOW-R1 follow-up to BACKLOG 66; copy `docs/ux/order-not-placed.md`): an approved
  entry the MT5 box or the broker definitely refused no longer archives as APPROVED. The engine stores
  `bridge_stale_order` (10006 with the bridge's stale prefix, tested before the retcode) or `broker_refused:<retcode>`
  (any other `DEFINITE_REJECT_RETCODES`) in `gate_refusal`, and queues exactly one owner notice per refusal. The
  paper broker's duplicate is `broker_refused:10027:paper`, worded "it was already sent; this repeat was refused" and
  never "Nothing was sent" (its first send went out; `NotPlaced.nothing_sent` false). One wording table,
  `goldbot/execution/not_placed.py`, feeds the Telegram card (`outcome_text`), the notice and the new API field
  `DecidedProposal.not_placed {by, reason, code, next_step, nothing_sent}`; the dashboard's red note reads it, with
  "Refused by the broker (code N); nothing was sent." for older APIs, and the card's state name is now "not placed".
  Ceiling codes keep their shipped words. A fill, any `ok` result or an ambiguous result (10008, 10010, 10012, -1,
  ...) still reads APPROVED. Not in this change: the amber "NOT CONFIRMED" state and auto-mode notices (open points
  in the spec, awaiting decisions).
