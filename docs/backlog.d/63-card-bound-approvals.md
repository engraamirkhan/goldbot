---
number: 63
rank: 12
title: 'Card-bound approvals: a tap is bound to the exact card the owner saw'
stage: Approvals (owner confirms entries, 90 s) · PROTECT · RUN
size: S
status: 'built 2026-10-16 (implementer; TRACEABILITY A16), awaiting trading-safety-reviewer APPROVE · accountable: implementer, then trading-safety-reviewer · due 2026-10-23'
---
- **63. Card-bound approvals: a tap is bound to the exact card the owner saw.** Decided 2026-10-16 by program
  director + product owner (owner delegated). Value (RUN, protect): the residual of BACKLOG 60's trading-safety
  re-verify (F60-1). A tap carries only the proposal id, so a click on a card shown before a restart could approve
  a proposal re-raised under the same id with different terms. The engine guard already blocks every known path;
  this is defence in depth that binds the owner's consent to the card's content. TRACEABILITY A1 (and R29, the
  approval re-check). Depends on: 60 (fix lane on goldbot/telegram/approvals.py, bot.py, the bus and runner.py must
  merge first).
  Acceptance:
  - the Telegram Approve and Reject callback data and the dashboard decision request carry the card's `created`
    (the proposal stamp the card was rendered from); the engine refuses, and logs with a reason
    (`card_mismatch`), any decision whose `created` differs from the pending proposal's, never approving it;
  - a decision without `created` (an old card, an old client) is refused, not accepted (fail closed);
  - every callback data string stays within Telegram's 64-byte limit for the longest proposal id the system can
    make (a test asserts it);
  - the API contract is regenerated (`scripts/export_openapi.py`, `npm --prefix web run gen:api`) and the dashboard
    sends the field;
  - tests: a tap on the pre-restart card for a re-raised proposal is refused; a tap on the current card approves;
    the 64-byte bound; the dashboard request without `created` is refused;
  - trading-safety-reviewer APPROVE recorded at the merge (E1).
