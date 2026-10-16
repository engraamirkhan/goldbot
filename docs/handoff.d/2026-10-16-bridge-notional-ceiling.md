- BACKLOG 66, equity-scaled notional ceiling at the bridge (implementer, 2026-10-16; plan
  `docs/proposals/2026-10-backlog66-bridge-notional-ceiling-plan.md` plus trading-safety conditions C1-C6; TRACEABILITY
  R44, cross-references R7). `GuardedBroker.place_order` now refuses an order that would take the terminal's gross
  notional on the symbol above 6x its own equity (DESIGN.md:112: 30% x 1:20), read entirely on the MT5 box and failing
  closed. A refusal is a definite reject (retcode 10006, message `bridge_notional_ceiling: ...`), never a 500:
  - the engine journals the message on the order entry, notifies the owner on Telegram, pauses auto entries for that
    bar close (`auto_paused:bridge_refused`), and an approved card shows "refused by the bridge" (Telegram and dashboard);
  - the account is read once for every order (also without a verifier); the changed-account raise stays;
  - the order's age is checked again just before the inner send (C6, `bridge_stale_order`);
  - close, modify, `MAX_ORDER_LOTS`, the gate and the wire protocol (API 5, `protocol_digest`) are unchanged.
  Built on the current `place_order` (C1): W1-1's F4 fence slots in after the account check, before the ceiling.
  Deploy: MT5 box first (the guard works with any brain), then the brain. Reviews pending: trading-safety (build),
  code-reviewer, security PUSH-OK, sre for the deploy.
