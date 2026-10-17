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
  - Entries are refused `unconfirmed_order_pending` while any row is unresolved (condition 1, refusal path). After
    review round 1, a `sending` row counts as unresolved: the engine is single-threaded, so a `sending` row seen at a
    gate check is a send that raised.
  - Notices: at most one per outcome, plus one correction if a fill turns up after "no trade". `SentOrder.notified` and
    `SentOrder.unknown_notified` are persisted before each notice is sent.
  - One wording table, `goldbot/execution/not_confirmed.py`, feeds `outcome_text`, the notices, the health line and the
    new API field `DecidedProposal.not_confirmed {outcome, heading, text, tone}`. A new `DecidedProposal.refusal_text`
    words the RiskGate codes on "Refused at approval".
  - `/api/proposals/recent` keeps unresolved cards listed past the 10-minute window. The ProposalCard has a new state
    `not_confirmed` and amber `outcome warn`.
  - Telegram "refused at approval" now words the RiskGate code ("daily loss", not `daily_loss`).
  - `runner.py`: 3,471 to 3,444 lines. The adoption block of `_resolve_sent` moved into `unconfirmed.py`, and
    `_record_closed_while_down` was removed. The cap is unchanged.
  - Trading-safety review, round 1:
    - Race (HIGH): `_finish` archives an approval as `placing` (new `Proposal.placing`) and clears it once the engine's
      `on_decision` has the broker's answer. `Outbox.finished` waits while a card is `placing`. If the engine loop
      heartbeat is older than 60 s, or the card has been placing for 120 s, it reports NOT CONFIRMED, never ✅
      APPROVED. The API shows such a card as "submitted".
    - `sending` path (condition 3): `_resolve_sent` with no position marks the row `ambiguous`, never `filled` or
      `unfilled` on one look; the look-up then decides with the two-look rule.
    - Clean look (condition 2): a look is clean only when the last quote is valid and no older than
      `stale_tick_seconds`. No look in `no_quote_pass` is clean, so "no trade" is never declared while the market is
      closed or the feed is down. A restart's first look (before any quote) is not clean.
    - A look that raises is logged and waits. The other rows and the reconcile loop go on.
    - `_adopt` puts the trade in the open table before `adopted` saves the row and tells the owner.
    - A late-adopted trade keeps the tighter of the stop sent and the broker's stop.
    - A stop that can't be set while the price is already past it gets a notice that the engine closes the trade at
      the first quote. This is a new row in the UX copy, pending ui-ux review.
    - The look-up journal records the row's send time, not its age, so the store golden is clock-independent.
  - The bridge smoke reads the same refusal from `orders_<account>.json` (an unreadable table counts as one).
  - Store golden grid (192 cases), checked by comparing each case at fb59e15 with the current one after removing the
    new keys (`notified`, `unknown_notified`, `unconfirmed_orders`):
    - At the first build (a4fe4cb), all 192 cases matched fb59e15 exactly.
    - After review round 1, the 64 "none" cases still match in all five files. The 64 "old" cases differ only in the
      restart journal: an old `sending` row now reads `reconcile_sent_ambiguous` and then `unconfirmed_gave_up`
      (past 7 days) instead of `reconcile_sent_unfilled`, and it is pruned as before.
    - The 64 "fresh" cases also add an `ambiguous` row, which stays ambiguous across the restart, and their `sending`
      row is restarted as `ambiguous`.
    - The engine-file comparison drops `unconfirmed_orders.oldest_age_s`, which the host clock stamps, as it already
      drops `ts`.
  - Known limit: a row whose entry deal carries no position id never resolves. It stays `ambiguous` for up to 7 days,
    with entries refused, until it turns terminal `unknown` with one notice.
  - `tests/test_auto_same_close.py`: the second auto entry in a bar is now refused at the gate
    (`unconfirmed_order_pending`) before the bar pause is consulted. The pause still applies.
  - Filed BACKLOG 79: the digest and the approval report count `not_confirmed` cards and item 71's broker refusals
    as "refused by the risk check".
  - Not done: the second Telegram card edit at resolution. The outbox forgets a card's message ids once it has reported
    the outcome, so resolution reaches Telegram as the notice only (the copy calls that edit best effort). The
    dashboard shows the resolved outcome.
  - Reviews needed: trading-safety (order and exit path), code, ui-ux (copy and amber states), sre (health WARN).
