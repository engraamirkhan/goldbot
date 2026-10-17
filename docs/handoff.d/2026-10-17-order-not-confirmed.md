- Order not confirmed (BACKLOG 72; plan `docs/proposals/2026-10-backlog72-not-confirmed-plan.md` with trading-safety
  conditions 1-7 and the PD/UX decisions; copy `docs/ux/order-not-confirmed.md`, byte for byte). An approved or
  automatic entry the broker neither filled nor definitely refused (10008, 10010, 10011, 10012, 10031, -1, any undefined
  code, `ok` without a position) no longer archives as ✅ APPROVED.
  - The pending_orders row becomes `ambiguous`. It stays in `sent_ids` and is never re-sent.
  - It is looked up at the broker on every pass: from `_reconcile`, before its adoption loop, and from `no_quote_pass`
    every `reconcile_every_s`, so it also runs with no ticks. The logic is in `goldbot/engine/unconfirmed.py`.
  - Outcomes:
    - a position is adopted with the stop sent, at the dealt lots. A missing or looser stop is put back in the same
      pass; a stop that can't be set tells the owner to set it in MT5;
    - an entry deal plus an exit deal found by position id (never by comment) is recorded as a closed trade, at the
      dealt lots;
    - `unfilled` only after two clean looks in this process at least 120 s apart;
    - otherwise it keeps looking: one "still not confirmed" notice and a health WARN at 600 s, and terminal `unknown`
      (with one notice) at 7 days, after which the row is pruned.
  - Entries are refused `unconfirmed_order_pending` while any row is unresolved (condition 1, refusal path).
  - Notices: at most one per outcome, plus one correction if a fill turns up after "no trade". `SentOrder.notified` and
    `SentOrder.unknown_notified` are persisted before each notice is sent.
  - One wording table, `goldbot/execution/not_confirmed.py`, feeds `outcome_text`, the notices, the health line and the
    new API field `DecidedProposal.not_confirmed {outcome, heading, text, tone}`. A new `DecidedProposal.refusal_text`
    words the RiskGate codes on "Refused at approval".
  - `/api/proposals/recent` keeps unresolved cards listed past the 10-minute window. The ProposalCard has a new state
    `not_confirmed` and amber `outcome warn`.
  - Telegram "refused at approval" now words the RiskGate code ("daily loss", not `daily_loss`).
  - `runner.py`: 3,471 to 3,450 lines. The adoption block of `_resolve_sent` and `_record_closed_while_down` moved into
    `unconfirmed.py`, and the cap is unchanged.
  - The bridge smoke reads the same refusal from `orders_<account>.json` (an unreadable table counts as one).
  - The store golden grid (192 cases) was regenerated for the additive keys only: `notified`, `unknown_notified` and
    `unconfirmed_orders`. With those keys removed, all 192 cases match the old golden.
  - `tests/test_auto_same_close.py`: the second auto entry in a bar is now refused at the gate
    (`unconfirmed_order_pending`) before the bar pause is consulted. The pause still applies.
  - Not done: the second Telegram card edit at resolution. The outbox forgets a card's message ids once it has reported
    the outcome, so resolution reaches Telegram as the notice only (the copy calls that edit best effort). The
    dashboard shows the resolved outcome.
  - Reviews needed: trading-safety (order and exit path), code, ui-ux (copy and amber states), sre (health WARN).
