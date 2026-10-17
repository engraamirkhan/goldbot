---
number: 72
rank: 7
title: 'NOT CONFIRMED: an ambiguous order result is resolved against the broker and the owner told'
stage: Execution and reconciliation (sent orders, entry outcome truth) · PROTECT · RUN
size: M
status: 'ready for the architect (decided 2026-10-17 by program director + product owner) · next: architect plan and trading-safety plan verdict by 2026-10-21, ui-ux copy by 2026-10-21, build after 71 (same files) · accountable: architect · due 2026-10-21 (plan; the plan names the build date, target 2026-10-27)'
---
- **72. NOT CONFIRMED: an ambiguous order result is resolved against the broker and the owner told
  (docs/ux/order-not-placed.md open point 1).** Decided 2026-10-17 by program director + product owner (owner
  delegated). Value (RUN, protect): an approved entry whose result is neither a fill nor a definite reject (10008,
  10010, 10011, 10012, 10031, -1, any undefined code) archives as ✅ APPROVED, so the owner cannot tell whether a trade
  exists. Worse than 71's case, and more likely on our set-up (a Wine bridge on a free VM: timeouts and dropped
  connections). It finds no edge; it keeps the owner's picture and the trade record true.
  **What the engine does today (product-owner reading of `goldbot/engine/runner.py` at 765ec27; the plan must confirm
  it):** `_execute` marks the pending_orders row `rejected` for every result that is not a fill, ambiguous ones
  included. So `_resolve_stale_sending` (rows still `sending` after `SENDING_RESOLVE_S` = 60 s) and the restart's
  `_resolve_sent` never look that order up again. What does happen: `_reconcile` (engine start, every bar close, every
  `reconcile_every_s` = 30 s of tick time, so only while ticks arrive) adopts any position in the engine's magic range;
  `_adopt` finds the row by comment and marks it filled, and a missing stop is reinstated. Gaps: (a) a fill that
  closes before the next pass (stopped out inside ~30 s) is never recorded, so it is missing from the closed-trade
  record the stop rule (P6), the phase gates (P7) and the finance ledger read; (b) the owner is never told the
  answer; (c) with the market closed or the feed down, no pass runs. So the UX doc's proposed "the engine checks again
  on its next tick" over-promises and must not ship as written.
  Depends on: 71 (same table, `outcome_text`, API field, ProposalCard; built in the same lane after it). Files: as 71,
  plus `_execute`, `_resolve_sent`, `_resolve_stale_sending`, `_after_auto_order` in runner.py. TRACEABILITY: X7
  (pending_orders reconciled against deals; extend the row), X8 (reconciliation), the A row 71 adds.
  Acceptance:
  - Plan first: architect plan with a trading-safety-reviewer verdict on it before any code; ui-ux-designer finalises
    the copy against the plan, and the copy promises only what a test proves (a check "within N minutes" only if a
    test shows it, and it says the check needs the market open). Starting text: Telegram `⚠️ NOT CONFIRMED: approved
    and sent, but the broker didn't confirm it. Check Positions; the engine looks it up at the broker and tells you
    what it finds.`; dashboard amber **Not confirmed.** Amber, not red; the heading word carries the state.
  - `tests/test_order_not_confirmed.py` (written first, failing on `main`):
    `test_an_ambiguous_result_on_approval_archives_not_confirmed_never_approved` (each code above and 10099);
    `test_an_ambiguous_result_keeps_the_sent_row_unresolved_and_it_is_never_resent`;
    `test_a_position_found_later_is_adopted_with_the_sent_stop_and_the_owner_told_it_is_open`;
    `test_a_fill_closed_before_the_next_check_is_recorded_from_its_deals`;
    `test_no_position_and_no_deal_marks_unfilled_and_the_owner_told_no_trade_opened`;
    `test_an_unreadable_deal_history_keeps_looking_and_sends_at_most_one_still_unknown_notice`;
    `test_an_auto_unconfirmed_result_is_resolved_the_same_way_and_auto_entries_stay_paused_for_the_bar`;
    `test_a_restart_resolves_an_unconfirmed_row_as_before`.
  - Exactly one resolution notice per order (open, opened and already closed, or no trade); no string holds a
    proposal id, the word "retcode" or the raw message (the 71 table's rule).
  - `outcome_text` and `ProposalCard.test.tsx`: the final NOT CONFIRMED copy byte for byte, amber, at 360 px and
    desktop, light and dark.
  - Reviews on record at the merge: trading-safety-reviewer (plan and build), code-reviewer, ui-ux-designer.
