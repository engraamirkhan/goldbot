---
number: 61
rank: 11
title: 'Mixed-currency accounts: the loss budget shows the account cap, not the combined cap'
stage: Dashboard and Telegram (loss budget, ADR 0004 section 3) · PROTECT
size: S
status: 'merged on the integration branch 2026-10-16 at 921a38a (4c3d087 + ecce681; trading-safety APPROVE, code-review APPROVE + PUSH-OK, ui-ux copy approved, per the merge message); ships in the next PR; product-owner acceptance at that PR; follow-ups L1/L2 in BACKLOG 65 · accountable: release-manager · due 2026-10-23'
---
- **61. Mixed-currency accounts: the loss budget shows the account cap, not the combined cap (UX review,
  2026-10-16).** Value: with accounts in more than one currency, `account_view._combined`
  (goldbot/ops/account_view.py) leaves out the supervisor's combined 1.5%/4% caps with a note, and the Overview card,
  the safety-strip chip and the approval cards show the account's own 2% cap. That is the roomier figure, the same
  gap the supervisor-report fallback closed. Accountable: implementer; due 2026-10-30. Acceptance:
  - until BACKLOG 28's currency lane lands, a mixed-currency budget reads "can't check" in amber
    (`LossBudget.unknown` with the owner's reason, `unknown_paused` false unless the supervisor halts on
    `mixed_account_currencies`, then red), with no amount on the card, the chip or the approval card;
  - the account caps stay in `caps`, so the entry status's spent-cap blocks are unchanged;
  - tests: API (`/api/accounts`, `/api/proposals`) with a GBP and a USD engine; BalanceCard and SafetyStrip show
    "can't check" in amber.
