# Order not confirmed: the broker neither filled nor refused an approved order

Final owner-facing copy for BACKLOG 72 (`docs/backlog.d/72-order-not-confirmed.md`), written against the plan
`docs/proposals/2026-10-backlog72-not-confirmed-plan.md` **and its trading-safety conditions 1-7**, which change what
the engine does and so what the owner may be told. Status: **ready for build** (after BACKLOG 71 merges). The words
below are final; the implementer may change code layout, not wording, without a ui-ux review. This supersedes open
point 1 of `docs/ux/order-not-placed.md` ("checks again on its next tick" must not ship).

## The problem

The owner approves a card; the order goes to the broker and comes back with neither a fill nor a definite reject
(10008, 10010, 10011, 10012, 10031, -1, any undefined code). Today the card archives as ✅ APPROVED, so the owner cannot
tell whether a trade exists, and nobody tells them later. After this build the engine keeps the order unresolved, looks
it up at the broker on every pass (with or without ticks), and tells the owner what it finds.

## What the copy may promise (and the test that proves it)

| Promise in the copy | Proven by |
|---|---|
| "the engine looks it up at the broker" (with the market closed or the feed down too) | `test_the_look_up_runs_with_no_ticks_through_no_quote_pass` |
| "tells you what it finds" (exactly one notice per outcome) | `test_one_resolution_notice_per_order_across_a_restart_and_a_reconcile_adopt` |
| "or tells you after 10 minutes that it still doesn't know" | `test_still_unknown_at_exactly_600_s_sets_the_health_warn`, `test_an_unreadable_deal_history_keeps_looking_and_sends_at_most_one_still_unknown_notice` |
| "checked twice, at least 2 minutes apart" (no trade) | condition 2: two clean looks ≥ 120 s apart; `test_no_deal_is_not_declared_unfilled_before_settle_s_boundary` |
| "the engine set its stop" (adopted position) | condition 5 test (stop missing or looser → `modify(rec.sl, rec.tp)` in the same pass) |
| "it's in the trade record" (opened and already closed) | `test_a_closed_fill_reaches_the_stop_rule_and_the_ledger`, condition 3 |
| "new entries wait until it's settled" | condition 1, **refusal path only** (see below) |

Never written: "next tick", "within N seconds", "automatically fixed". The look-up needs the engine running and the MT5
box reachable; the copy does not promise more, and the Health tab already fails when either is down.

**Condition 1, recommendation.** From the owner's side, refusing entries with `unconfirmed_order_pending` is the better
of the two paths: it is one visible reason with words, while counting an unknown order as open exposure is invisible
(the owner would see entries refused for "too much risk already open" with no open trade). Trading-safety and the
architect choose; if they choose the exposure path, drop every `{gate}` sentence below and the refusal rows are unused.
`{gate}` = " New entries wait until it's settled." (leading space) on the refusal path, empty otherwise.

## One table, every surface

Same rule as BACKLOG 71: the words live in item 71's Python table (`goldbot/execution/not_placed.py`, or a sibling
`not_confirmed.py` it imports), used by `bot.outcome_text`, the runner's notices and the API. The browser holds no
wording policy (ADR 0011).

**API field (implementer adds it):** `DecidedProposal.not_confirmed: NotConfirmed | None`, with
`outcome: "pending" | "opened" | "opened_closed" | "no_trade" | "opened_late" | "still_unknown"`, `heading: str`,
`text: str`, `tone: "warn" | "ok" | "neutral" | "bad"`. Set when the approval archived with refusal code
`not_confirmed`; `None` otherwise. **`GET /api/proposals/recent` keeps a decision whose order is unresolved
(`pending`, `still_unknown`) past the 10-minute window**, until it resolves, so the dashboard never drops an
unanswered order; resolved ones follow the normal window from the resolution time. Then
`python scripts/export_openapi.py && npm --prefix web run gen:api`.

Placeholders: `{side}` LONG|SHORT; `{lots}` `f"{lots:g}"`, and on a part-fill (10010, condition 4)
`{dealt} of {lots}` from the deals; `{symbol}` the config symbol; `{account}` `account_name(account_id)` (masked);
prices to the symbol's digits; `{pnl}` the deal profit in the account currency via `entry_status.money`, signed with
U+2212 ("−£8.20", "+£12.30"); `{age}` `checks._base._age`. Never shown: the proposal id, the word "retcode", a code
number (except the fallback below), raw broker or bridge text, a host, a login.

## Copy

### Telegram card outcome (`outcome_text`; the card is edited at approval, then once more when resolved)

| Outcome | Text |
|---|---|
| At approval (`not_confirmed`) | `⚠️ NOT CONFIRMED: approved and sent, but the broker didn't confirm it. The engine looks it up at the broker and tells you what it finds, or tells you after 10 minutes that it still doesn't know.{gate}` |
| Opened | `✅ OPENED: the broker confirmed it late. Stop, target and exit are automatic.` |
| Opened and already closed | `ℹ️ OPENED AND CLOSED: the broker confirmed it late; it opened at {entry} and closed at {exit} ({pnl}).` |
| No trade | `🛑 NOT PLACED: approved and sent, but the broker has no trade for it (checked twice, at least 2 minutes apart). No trade was opened.` |
| Opened after all (correction) | `✅ OPENED AFTER ALL: the broker confirmed it after it was reported not placed. Stop, target and exit are automatic.` |
| Still unknown (10 min) | `⚠️ STILL NOT CONFIRMED after 10 minutes. The engine keeps looking; check MT5 for an open {symbol} trade.` |

The card edit at resolution is best effort (logged); the notice still arrives.

### Dashboard note (`ProposalCard` `Outcome`, `role="status"`, heading word carries the state)

| Outcome | Class | Note |
|---|---|---|
| pending | `outcome warn` (new: `--warn-fg`/`--warn-tint`) | **Not confirmed.** Sent, but the broker didn't confirm it. The engine looks it up at the broker and tells you what it finds, or tells you after 10 minutes that it still doesn't know.{gate} |
| opened | `outcome ok` | **Opened.** The broker confirmed it late; stop, target and exit are automatic. |
| opened_closed | `outcome muted-box` | **Opened and already closed.** The broker confirmed it late: it opened at {entry} and closed at {exit} ({pnl}). It's in the trade record. |
| no_trade | `outcome bad` | **Not placed.** Sent, but the broker has no trade for it (checked twice, at least 2 minutes apart); no trade was opened. |
| opened_late | `outcome ok` | **Opened after all.** The broker confirmed it after it was reported not placed; stop, target and exit are automatic. |
| still_unknown | `outcome warn` | **Still not confirmed.** After 10 minutes the broker hasn't shown whether it opened. The engine keeps looking; check MT5 for an open {symbol} trade.{gate} |

Each ends with ` · via Telegram` / ` · via the dashboard` when known. Amber, not red, while unknown: it is not a halt.
Red only for "Not placed", matching BACKLOG 71 (no trade exists).

### Owner notices (`bus.notify_owner`; at most one per outcome, plus the single correction; persisted before sending)

| Outcome | Notice |
|---|---|
| Opened | `Order confirmed: {side} {lots} lots {symbol} on {account} is open at {entry} (the broker confirmed it late). Stop {sl}, target {tp}; exits are automatic.` |
| Opened, stop put back (condition 5) | `Order confirmed: {side} {lots} lots {symbol} on {account} is open at {entry} (the broker confirmed it late). Its stop was missing or wider at the broker, so the engine set stop {sl} and target {tp}; exits are automatic.` |
| Opened, stop could not be set | `⚠️ Order confirmed, but without its stop: {side} {lots} lots {symbol} on {account} is open at {entry} and the engine couldn't set stop {sl}. Set stop {sl} in MT5 now.` |
| Opened, stop could not be set, price already past it (trading-safety review round 1, TS 5; **ui-ux review pending**) | `⚠️ Order confirmed, but without its stop: {side} {lots} lots {symbol} on {account} is open at {entry} and the engine couldn't set stop {sl}. The price is already past stop {sl}, so the engine closes the trade at the first quote.` |
| Opened and already closed | `Order confirmed, already closed: {side} {lots} lots {symbol} on {account} opened at {entry} and closed at {exit} ({pnl}). It's in the trade record.` |
| No trade | `Order not placed after all: the broker has no trade for {side} {lots} lots {symbol} on {account} (checked twice, at least 2 minutes apart). Nothing to do; if it turns up later, you'll be told.` |
| Correction (`unfilled` → `filled`) | `Correction: the order did open after all. {side} {lots} lots {symbol} on {account} is open at {entry}; the earlier "not placed" notice was wrong. Stop {sl}, target {tp}; exits are automatic.` |
| Still unknown (600 s) | `Order still not confirmed after 10 minutes: the broker hasn't shown whether {side} {lots} lots {symbol} on {account} opened. The engine keeps looking and tells you when it knows.{gate} Check MT5: if a {symbol} trade is open without a stop, set stop {sl}.` |
| Terminal unknown (7 days) | `Stopped looking: after 7 days the broker still hasn't shown whether {side} {lots} lots {symbol} on {account} opened. Check MT5 and ask Claude to reconcile the trade record.` |

Approved and automatic orders get the same notices (the automatic path has no card). "Opened" and "Opened and already
closed" use the dealt lots on a part-fill: `LONG 0.2 of 0.37 lots XAUUSD`.

### Health WARN line (engine check, `oldest_age_s >= 600`)

`{n} order(s) not confirmed by the broker, oldest {age}: the engine keeps looking{; new entries wait until it's settled}; check MT5 for an open {symbol} trade and its stop (Runbook: Order not confirmed)`

Written with real plurals ("1 order", "2 orders"); the bracketed clause on the refusal path only.

### Entry refusal `unconfirmed_order_pending` (refusal path of condition 1)

| Surface | Text |
|---|---|
| `entry_status.REASONS` (account level) | text `An earlier order isn't confirmed by the broker yet` · `since {time}`; next step `Nothing to do: the engine is looking it up at the broker. If it lasts over 10 minutes, check MT5 for an open trade and its stop.`; kind `wait` |
| `REFUSAL_TEXT` (a strategy's last refusal) | `an earlier order isn't confirmed by the broker yet` |
| Card refused at approval (dashboard) | **Refused at approval.** The RiskGate blocked the order (an earlier order isn't confirmed by the broker yet); nothing was sent. |
| Card refused at approval (Telegram) | `🛑 NOT PLACED: approved, but the risk check refused it at approval (an earlier order isn't confirmed by the broker yet)` |

The two card lines need the code worded through `REFUSAL_TEXT`, not `plain(code)` / the raw code as today.

### ProposalCard accessible state name

New `CardState` `not_confirmed`: `STATE_NAME.not_confirmed = "sent, not confirmed by the broker"`. Resolved outcomes use
`"opened"`, `"opened and closed"`, `"not placed"` (item 71's value) and `"still not confirmed"`.

### Fallback

An ambiguous result whose outcome the table cannot word (a future enum value) shows the pending texts above; the code
number appears only in the journal, never on any surface, because "not confirmed" needs no code to be true.

## States

| State | Telegram | Dashboard |
|---|---|---|
| Not confirmed (≤ 10 min) | ⚠️ NOT CONFIRMED | amber **Not confirmed.**; stays listed until resolved |
| Opened / opened after all | ✅ OPENED / ✅ OPENED AFTER ALL | green |
| Opened and already closed | ℹ️ OPENED AND CLOSED | neutral |
| No trade | 🛑 NOT PLACED | red |
| Still unknown (> 10 min) | ⚠️ STILL NOT CONFIRMED + notice | amber **Still not confirmed.**; health WARN |
| Terminal unknown (7 days) | notice only | card gone; WARN clears with the row |

Phone (360 px) and desktop, light and dark: the notes reuse the AA tokens; `outcome warn` is new CSS on the existing
`--warn-fg`/`--warn-tint` (≥ 4.5 : 1 both themes). The pending note (~190 characters) wraps to 5 lines at 360 px.
The note changes text at most twice (resolution, correction): the same heading-plus-sentence paragraph, no new
elements, so no shift beyond the line count. Loading, empty and error are the Approvals screen's own.

## Tests the implementer writes first (copy)

- `outcome_text`: every Telegram row byte for byte; no string holds a proposal id, "retcode", a code number or raw text.
- `ProposalCard.test.tsx`: every dashboard row (anchored regex), amber class on pending/still_unknown, the state
  name; an older API without `not_confirmed` falls back to the existing APPROVED note.
- Runner: each notice above exactly once; `test_an_adopted_position_whose_stop_cannot_be_set_tells_the_owner_to_set_it`
  (needed before the "couldn't set stop" notice ships); terminal unknown sends its one notice.
