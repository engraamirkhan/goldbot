# Order not placed: broker and MT5-box refusals after approval

Final owner-facing copy for trading-safety's LOW-R1 follow-up to BACKLOG 66 (throughput plan lever 2: copy before
code). Status: **ready for build**. The words below are final; the implementer may change code layout, not wording,
without a ui-ux review.

## The problem

The owner approves a card. The RiskGate allows it. The order goes to the MT5 box and on to the broker, and comes back
with a **definite reject** (`DEFINITE_REJECT_RETCODES` in `goldbot/engine/runner.py`): nothing was placed. Today only
the bridge's notional ceiling (BACKLOG 66) turns the card into "NOT PLACED". Every other definite reject, the MT5 box's
`bridge_stale_order` and every broker retcode (10019 no money, 10016 invalid stops, 10018 market closed, ...), leaves
the card archived as **APPROVED**, with the green "Approved, order sent" note on the dashboard and no notice. The owner
believes a trade is open when none is. That is a safety-state lie, so it is red, like the ceiling refusal.

Scope: definite rejects on the **approval path** (`_on_decision`). Out of scope, with a recommendation each, under
"Open points" below: the ambiguous results (not confirmed) and auto-mode rejects.

## One table, three surfaces

The card on Telegram, the dashboard note and the owner notice say the same reason in the same words. To keep them from
drifting, the words live in **one Python table** (suggested: `goldbot/execution/not_placed.py`, `not_placed_reason(code)
-> (by, reason, next_step)`, where `reason` is the clause in the per-cause table, `None` for the fallback, and the code
number is returned for the fallback), used by `bot.outcome_text`, the runner's notice and the API. The API gives the dashboard
the worded reason; the browser holds no wording policy (the ADR 0011 rule the entry panel follows).

**API field (implementer adds it):** `DecidedProposal.not_placed: NotPlaced | None`, with
`by: "broker" | "mt5_box"`, `reason: str | None` (the clause below; `None` for the fallback), `code: int | None`
(the retcode, set for the fallback only), `next_step: str`. Set when `refusal` holds one
of the codes in this document; `None` otherwise. Then `python scripts/export_openapi.py && npm --prefix web run gen:api`.
The existing two ceiling codes may move into the same table, but their words stay exactly as shipped.

**Refusal codes the runner stores in `p.gate_refusal`** (never shown to the owner):

| Cause | Code |
|---|---|
| The MT5 box's notional ceiling (shipped) | `bridge_notional_ceiling`, `bridge_notional_ceiling_unreadable` |
| The MT5 box dropped an order that aged past `ORDER_MAX_AGE_S` (retcode 10006, message prefix `bridge_stale_order`) | `bridge_stale_order` |
| Any other definite reject | `broker_refused:<retcode>`, e.g. `broker_refused:10019` |
| The paper broker's 10027 (a duplicate send, not Algo Trading off) | `broker_refused:10027:paper` |

Bridge prefixes are tested before the retcode, because the bridge's own refusals also use 10006.

## Copy

### Pattern

| Surface | Template |
|---|---|
| Telegram card outcome (replaces the outcome line, `outcome_text`) | `🛑 NOT PLACED: approved, but {by_phrase} refused it: {reason}. Nothing was sent.` |
| Dashboard red note (`ProposalCard` `Outcome`, state `refused`) | **`{Heading}`** `{Reason}; nothing was sent.` (reason capitalised) + ` · via Telegram` when known |
| Owner notice (`bus.notify_owner`, a separate Telegram message) | `Order not placed: {by_phrase} refused {LONG\|SHORT} {lots} lots {symbol} on {account}: {reason}. Nothing was sent; open trades keep their stops. {next_step}` |
| Fallback (no mapped reason), all three | card `🛑 NOT PLACED: approved, but the broker refused it (code N). Nothing was sent.`; dashboard **Refused by the broker** `(code N); nothing was sent.`; notice `Order not placed: the broker refused {LONG\|SHORT} {lots} lots {symbol} on {account} (code N). Nothing was sent; open trades keep their stops. {next_step}` |

- `by_phrase`: "the broker" or "the MT5 box". Headings: **Refused by the broker.** / **Refused by the MT5 box.**
- `{reason}` is a lower-case clause with no final full stop, worded so it reads after either heading (it never
  repeats "the broker"). On the dashboard it starts with a capital and ends before "; nothing was sent."
- `{account}` is `account_name(account_id)` (masked: "icm-demo-…67"). `{lots}` is `f"{lots:g}"`. `{symbol}` is the
  config symbol (XAUUSD).
- Never shown: the proposal id, the bridge or broker message, a host, a login. Those stay in the journal and the log.
- No auto-mode sentence: this path is the approval path (see Open points for auto).
- The retcode number appears only in the generic fallback. The journal keeps it for every cause.

### Per cause

| Code | `{reason}` (Telegram card and notice; capitalised on the dashboard) | `{next_step}` (notice only) |
|---|---|---|
| `bridge_stale_order` (by: MT5 box) | it took more than 10 s to check the order, so it dropped it rather than trade on an old price | Nothing to do if it's a one-off. If it repeats, the MT5 box is overloaded or slow: check the Health tab. |
| `broker_refused:10004`, `:10020` | the price moved before the order could be filled | Nothing to do: the next signal is priced afresh. |
| `broker_refused:10021` | there was no price to fill it at | Nothing to do if it's a one-off. If it repeats, check the MT5 terminal is connected. |
| `broker_refused:10007` | the order was cancelled on the MT5 terminal | Check nobody is working on the MT5 terminal by hand. |
| `broker_refused:10013`, `:10015`, `:10022`, `:10030`, `:10035` | the order's settings weren't accepted | Ask Claude to check: the order settings and the broker's rules disagree. |
| `broker_refused:10014` | the trade size wasn't accepted | Ask Claude to check the broker's lot size rules for this account. |
| `broker_refused:10016` | the stop or target wasn't accepted (too close to the price) | Nothing to do if it's a one-off. If it repeats, ask Claude to check the broker's minimum stop distance. |
| `broker_refused:10017` | trading in this symbol is switched off on the account | Check the account in MT5: the broker may have switched trading off. |
| `broker_refused:10018` | the market was closed | Nothing to do: entries resume when the market reopens. |
| `broker_refused:10019` | there wasn't enough free margin | Check free margin on the Overview before approving the next trade. |
| `broker_refused:10024` | it was too busy (too many requests at once) | Nothing to do if it's a one-off. |
| `broker_refused:10026` | automatic trading is switched off for this account at the broker | Contact the broker: automatic trading is off at their end. |
| `broker_refused:10027` (live or demo terminal) | Algo Trading is switched off in the MT5 terminal | Turn on Algo Trading in the MT5 terminal. |
| `broker_refused:10027:paper` (paper broker) | it was already sent; this repeat was refused. Its first send went out, so no surface says "Nothing was sent" (trading-safety review LOW): card `🛑 NOT PLACED: approved, but the broker refused it: it was already sent; this repeat was refused.`; dashboard "Refused by the broker. It was already sent; this repeat was refused."; notice "…: it was already sent; this repeat was refused. Open trades keep their stops. Nothing to do: the first send stands." | Nothing to do: the first send stands. |
| `broker_refused:10033`, `:10034`, `:10040` | the account's limit on open trades or total size was reached | Check open trades on the Overview. |
| `broker_refused:10042` | only buys are allowed in this symbol right now | Nothing to do: it's the broker's restriction. |
| `broker_refused:10043` | only sells are allowed in this symbol right now | Nothing to do: it's the broker's restriction. |
| `broker_refused:10044` | only closing trades are allowed in this symbol right now | Nothing to do: it's the broker's restriction. |
| `broker_refused:10046` | the account doesn't allow opposite trades at the same time (no hedging) | Check open trades on the Overview. |
| `broker_refused:<any other N>`, including 10006 from the broker | none: the fallback template above | If it repeats, ask Claude to check (broker code N). |

10006 from the broker itself means "rejected" with no reason, so it gets the fallback. That is honest: the broker
gave no reason, so the copy doesn't invent one.

### Worked examples (final strings)

Telegram card outcome:

```
🛑 NOT PLACED: approved, but the broker refused it: there wasn't enough free margin. Nothing was sent.
🛑 NOT PLACED: approved, but the broker refused it: the stop or target wasn't accepted (too close to the price). Nothing was sent.
🛑 NOT PLACED: approved, but the MT5 box refused it: it took more than 10 s to check the order, so it dropped it rather than trade on an old price. Nothing was sent.
🛑 NOT PLACED: approved, but the broker refused it (code 10006). Nothing was sent.
```

Dashboard red note (`role="status"`, class `outcome bad`):

```
Refused by the broker. There wasn't enough free margin; nothing was sent. · via Telegram
Refused by the broker. The stop or target wasn't accepted (too close to the price); nothing was sent.
Refused by the MT5 box. It took more than 10 s to check the order, so it dropped it rather than trade on an old price; nothing was sent.
Refused by the broker (code 10006); nothing was sent.
```

Owner notice:

```
Order not placed: the broker refused LONG 0.37 lots XAUUSD on icm-demo: there wasn't enough free margin. Nothing was sent; open trades keep their stops. Check free margin on the Overview before approving the next trade.
Order not placed: the broker refused SHORT 0.1 lots XAUUSD on icm-demo (code 10006). Nothing was sent; open trades keep their stops. If it repeats, ask Claude to check (broker code 10006).
Order not placed: the MT5 box refused LONG 0.37 lots XAUUSD on icm-demo: it took more than 10 s to check the order, so it dropped it rather than trade on an old price. Nothing was sent; open trades keep their stops. Nothing to do if it's a one-off. If it repeats, the MT5 box is overloaded or slow: check the Health tab.
```

### Wording around the card

- `STATE_NAME.refused` (the card's accessible state name) reads "refused by the RiskGate". With this change a refused
  card may be the RiskGate's, the MT5 box's or the broker's. New value: **"not placed"**. The visible notes already
  say who refused it.
- `docs/ux/approval-card.md` gets the new state row (below).
- "Nothing was sent" is kept for consistency with BACKLOG 66. For a broker refusal the order did reach the broker; to
  the owner, "sent" means "a trade went out", and none did. If the owner ever reads it as "the bot didn't try",
  switch every NOT PLACED string together to "No trade was opened." Don't change just one.

## States (the card's outcome)

| State | When | Telegram outcome | Dashboard |
|---|---|---|---|
| Approved | fill (or a duplicate already sent before: `res is None`) | ✅ APPROVED | green "Approved, order sent. …" |
| Refused at approval | RiskGate re-check refused | 🛑 NOT PLACED: approved, but the risk check refused it at approval (…) | red "Refused at approval. …" (unchanged) |
| Refused by the MT5 box (ceiling) | shipped, BACKLOG 66 | unchanged | unchanged |
| **Refused by the MT5 box (stale)** | `bridge_stale_order` | this doc | this doc |
| **Refused by the broker** | any other definite reject | this doc | this doc |
| Not confirmed | ambiguous result (10008, 10010, 10011, 10012, 10031, -1, unknown) | BACKLOG 72: docs/ux/order-not-confirmed.md | BACKLOG 72 |

Phone (360 px) and desktop, light and dark: the note is the existing `outcome bad` paragraph (AA tokens
`--bad-fg`/`--bad-tint`), the heading word carries the state, so colour is never alone. The longest dashboard note
(stale, ~130 characters) wraps to 4 lines at 360 px. The card swaps from the pending layout to the decided layout once,
as every decided card does; the note's text never updates in place, so no layout shift.
Loading, empty and error states are the Approvals screen's own (unchanged). Telegram has no loading state: the outcome
edits the card once when archived; if the edit fails, the notice still arrives (best effort, logged).

## approval-card.md row (to add)

| Not placed | approved, then the MT5 box or the broker refused the order | Red note: "Refused by the broker. There wasn't enough free margin; nothing was sent." Wording per cause: docs/ux/order-not-placed.md |

## Tests the implementer writes first

- `tests/…/test_not_placed.py`: every retcode in `DEFINITE_REJECT_RETCODES` has a row or reaches the fallback;
  the fallback names the code; no string contains a proposal id, `bridge_`, `broker_refused`, the word "retcode"
  or the raw message.
- Runner: an approved order refused with 10019 archives with `gate_refusal == ["broker_refused:10019"]` and queues
  exactly the notice above; `bridge_stale_order` (10006 with that prefix) archives `["bridge_stale_order"]`, never
  `broker_refused:10006`; a ceiling refusal is unchanged; a fill and an ambiguous result still archive APPROVED.
- `outcome_text`: the four worked card examples, byte for byte.
- `ProposalCard.test.tsx`: the four dashboard examples (regex anchored like the BACKLOG 66 ones); an older API
  without `not_placed` and a `broker_refused:N` code shows "Refused by the broker (code N); nothing was sent." and
  never "The RiskGate blocked the order".

## Open points (recommendations, not in this build)

1. **Not confirmed (ambiguous results)**: superseded by docs/ux/order-not-confirmed.md (BACKLOG 72); the
   "next tick" wording below must not ship. Original note: still reads ✅ APPROVED. This is worse than a definite reject: the owner
   cannot tell whether a trade exists. Recommended copy, for a later item with trading-safety:
   Telegram `⚠️ NOT CONFIRMED: approved and sent, but the broker didn't confirm it. Check Positions; the engine
   checks again on its next tick.`; dashboard amber **Not confirmed.** "Sent, but the broker didn't confirm it. Check
   Positions; the engine checks again on its next tick." Amber, not red: it doesn't stop trading.
2. **Auto-mode definite rejects** are journalled (`auto_rejected:<retcode>`) with no notice. Recommend the same notice
   with " Automatic entries carry on." and at most one per cause per hour, so a repeating refusal can't flood the
   phone. Needs a product-owner call.
