# Plan: BACKLOG 72 — an approved entry the broker neither filled nor refused is NOT CONFIRMED, never ✅ APPROVED

Status: plan, architect, 2026-10-17 (recorded by the program director; the architect is read-only); trading-safety plan
review before any build. Build after BACKLOG 71 merges (about 2026-10-27).

**Requirement.** `docs/backlog.d/72-order-not-confirmed.md`; TRACEABILITY X7 ("reconciled against history_deals_get"
on restart), X8, and the A row item 71 adds. ADR 0023 store seam: `SentOrder` fields stay additive.

**Today.** `_execute` (`runner.py:1402`) marks every non-fill `rejected`, so `_resolve_stale_sending` (`:2569`) and
`_resolve_sent` (`:2526`) look only at `sending` rows; `_load_orders` (`:2518`) and the 7-day prune in `_save_orders`
(`:2480`) likewise. `_after_auto_order` (`:1351`) already pauses auto entries for the bar on an ambiguous result (stays).
A fill that closes between `_reconcile` passes is never recorded. `_record_closed_while_down` (`:2582`) already records
a closed fill from its deals — reused.

**Protocol: no bump.** `positions` and `deals_since` already cross the bridge (`bridge.py:77`, `:524-531`) and
`_resolve_sent` matches by comment through them. `history_orders_get` is not needed; `protocol_digest` untouched.

## Design (reading taken)

1. New sent-order status `ambiguous`. `_execute:1402` becomes three-way: fill → `filled`; `retcode in
   DEFINITE_REJECT_RETCODES` or `bridge_refused` → `rejected`; anything else, including `ok` with no `position_id` →
   `ambiguous`. The row stays in `sent_ids`, so it is never re-sent.
2. Additive `SentOrder` fields: `notified: str | None = None` (`opened` | `opened_closed` | `no_trade` | `unknown`),
   `unknown_notified: bool = False`.
3. One method `_resolve_ambiguous(positions) -> bool`, placed after `_resolve_stale_sending`; reuses `_resolve_sent`
   (extended to accept `ambiguous`) then `_notify_resolution(cid, rec, outcome)`. Outcomes:
   - position found → adopt as `OpenTrade` with the row's stop, lots, policy (reuse `:2543-2561`, incl. already-in-
     `self.open`); notice "open";
   - no position but an entry and an exit deal for the comment → `_record_closed_while_down` → `_note_closed`, so the
     stop rule, phase gates and ledger see it; notice "opened and closed". Entry deal only = close in flight, keep looking;
   - no position, no deal, deal history readable on consecutive looks for ≥ `AMBIGUOUS_SETTLE_S` (120 s; a late 10012
     can still fill) → `unfilled`, through item 71's NOT PLACED path; notice "no trade opened";
   - deal history unreadable → stay `ambiguous`, look again;
   - unresolved after `AMBIGUOUS_ALERT_S` (600 s) → one "still unknown" notice, `unknown_notified=True`, engine-state
     field `unconfirmed_orders: {count, oldest_age_s}`, health WARN (module found with `grep closed_records_lost`) when
     `oldest_age_s >= 600`. Keep looking until the 7-day prune; the prune keeps `ambiguous` rows.
4. The loop runs from `_reconcile` (`:1836`, beside `_resolve_stale_sending`) **and** from `no_quote_pass` (`:718`), so
   it runs with no ticks — the bridge only. Cadence: every pass (30 s). `_adopt` (`:1879`): when the row it finds is
   `ambiguous`, route through `_notify_resolution`, or an adopted position gets no notice.
5. Card: `_on_decision` (~`:1283`) archives a not-confirmed approval with refusal code `not_confirmed` through item 71's
   `not_placed.py` table and `outcome_text` — amber, never ✅ APPROVED. On `unfilled`, call 71's NOT PLACED outcome and
   notice. Do not edit 71's wording; rebase on 71 first.
6. Notices via `bus.notify_owner`, copy from the 71 table; no proposal id, retcode word or raw message. Persist
   `notified` **before** sending (at-most-once; a crash between persist and send loses one notice, journalled).
7. Restart: `_load_orders` loops over `sending` and `ambiguous`. Row age drives the settle rule, so a restart cannot
   declare `unfilled` early.
8. Rollback (ADR 0010): an older release sees an unknown status string, keeps the id in `sent_ids`, never re-sends; it
   only fails to resolve it — state this in X7.

**Runner footprint** (62/71 are editing runner.py): edit three places (`_execute:1402` branch, `_load_orders` loop
condition, `_save_orders` filter); add one block (`_resolve_ambiguous`, `_notify_resolution`) after
`_resolve_stale_sending`; one call each in `_reconcile` and `no_quote_pass`. Everything else in a new
`goldbot/engine/unconfirmed.py` (pure outcome decision, health summary). `_decide`, the gate, the approval flow and the
bridge untouched.

**Non-negotiables.** RiskGate stays the only path to an order — the loop only reads; no re-send exists. Exits untouched.
No features, no bar joins. Timestamps via `epoch_ns`; row age via `pd.Timestamp.now("UTC")` as at `:2573`. Demo-first
unchanged. No secrets; account names through `account_name`.

## Failing tests first (`tests/test_order_not_confirmed.py`)

The eight names in `docs/backlog.d/72-order-not-confirmed.md`, plus:
`test_ten_retcodes_including_10099_and_minus_one_archive_not_confirmed_not_approved`,
`test_a_definite_reject_stays_rejected_and_is_not_looked_up_again`,
`test_the_look_up_runs_with_no_ticks_through_no_quote_pass` (proves "checks again"),
`test_no_deal_is_not_declared_unfilled_before_settle_s_boundary` (119 s vs 120 s),
`test_still_unknown_at_exactly_600_s_sets_the_health_warn` (599 s vs 600 s),
`test_one_resolution_notice_per_order_across_a_restart_and_a_reconcile_adopt`,
`test_a_corrupt_ambiguous_row_stops_the_start_loudly`,
`test_an_older_release_loads_a_file_with_ambiguous_rows` (in `tests/test_persistence_compat.py`),
`test_a_closed_fill_reaches_the_stop_rule_and_the_ledger`.

**Copy promise.** "Looks it up at the broker and tells you what it finds — or tells you after 10 minutes that it still
doesn't know." Never "next tick". ui-ux finalises.

**Docs.** HANDOFF fragment; TRACEABILITY X7, X8 and A rows ("Reading taken: ambiguous = not a definite reject; unfilled
only after 120 s of clean empty looks"); RUNBOOK one line on the health WARN and what to check on the MT5 box.

**Risks.** (1) A late fill after `unfilled` was declared — the settle window, and `_reconcile` still adopts a late
position by comment. (2) Duplicate or lost notices — the at-most-once choice. (3) Conflict with 71 in `_on_decision` —
build after 71 merges. (4) `_record_closed_while_down` uses `rec.lots`, not the deal's lots.

**Reviews.** trading-safety (plan verdict and build), code-reviewer, ui-ux-designer (copy and amber states), sre (the
health WARN). No quant review. **Owner-only decisions:** none.

## Conditions from the trading-safety plan review (APPROVE-WITH-CONDITIONS, d8de4ad, 2026-10-17)

Build blocked until 1-4 are honoured; they override the text above where they differ.
1. **HIGH — the gate must not fail open while a row is unresolved.** While any `ambiguous` row is unresolved, either
   count its `rec.lots` and stop risk as open exposure in the RiskGate inputs (max positions, heat, combined caps) or
   refuse entries with `unconfirmed_order_pending`. Tests for both paths; a late 10012 fill must never let a second
   entry pass with understated exposure.
2. **HIGH — one empty deal read is not proof of no fill.** `unfilled` needs at least two clean looks in this process,
   ≥ `AMBIGUOUS_SETTLE_S` apart, counted from the first clean look; a clean look = positions read and deals read both
   succeeded and the terminal connected. Row age alone never declares `unfilled` (restart case). State that market
   orders on a hedging account leave no resting order, which is why `orders_get` is not read.
3. **HIGH — exit deals do not carry our comment** (`[sl …]`/`[tp …]`). Find the position id from the entry deal's
   comment, then look for the exit by `position_id`. `_record_closed_while_down` (~2585) returns silently without a
   `position_id` column: on this path keep the row `ambiguous`; never mark it resolved without a record.
4. **MEDIUM — partial (10010) fills record the dealt lots**, not `rec.lots` (`_record_closed_while_down` ~2597,
   `_resolve_sent` ~2553 set `initial_lots=rec.lots`; `_record_closed` ~2741 prefers it). Test
   `test_a_partial_ambiguous_fill_records_the_dealt_lots`.
5. **MEDIUM — a fill adopted in `no_quote_pass` may have no stop.** A found position with no stop, or one looser than
   `rec.sl`, gets `broker.modify(rec.sl, rec.tp)` in the same pass (modify needs no quote). Test it.
6. **MEDIUM — notices.** At-most-once stands, plus one correction notice ("it did open after all") when a fill appears
   after `unfilled` (row `unfilled` → `filled` via `_adopt`). Card and dashboard show the outcome whether or not a
   notice was sent. After 7 days an unresolved row becomes terminal `unknown` (journalled) and is then pruned.
7. **LOW — footprint.** Real edits: `_execute`, `_save_orders`, `_load_orders`, `_resolve_sent`,
   `_record_closed_while_down`, `_adopt`, `_on_decision` (~1243), `_reconcile`, `no_quote_pass`. No conflict with 62
   (it edits `_kill_switch` only); 71 shares `_execute` and `_on_decision`, so build after 71 merges. Run the ambiguous
   lookup before `_reconcile`'s adoption loop so the row's stop and policy are used and the owner is told.

## Decisions after the UX copy (docs/ux/order-not-confirmed.md, 2026-10-17, program director + ui-ux; trading-safety
allowed either reading of condition 1)

- **Condition 1 is met by refusing entries** with `unconfirmed_order_pending` while any row is unresolved (fail-closed,
  and the owner sees "an earlier order isn't confirmed yet" rather than "too much risk open" with no trade). The `{gate}`
  sentence stays in the copy.
- `/api/proposals/recent` keeps an unresolved order listed past its 10-minute window (the dashboard must not drop the
  card when the warning fires); test it.
- A position adopted whose stop cannot be set tells the owner to set it in MT5:
  `test_an_adopted_position_whose_stop_cannot_be_set_tells_the_owner_to_set_it`.
- Build additions: `DecidedProposal.not_confirmed` API field (regenerate the contract), amber `outcome warn` style, the
  refused-at-approval reason in plain English; map the 7-day "gave up" notice into the `notified` values (add
  `gave_up`).
