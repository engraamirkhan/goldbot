---
number: 96
title: Loss budget label shown once on the approval card
stage: 'UI (approvals) · UX'
size: S
status: 'later (triage 2026-11-13): cosmetic, approval card · accountable: ui-ux-designer'
---
- **96. Loss budget label shown once on the approval card.** Value: the card prints "Loss budget" as the label and again in the text ("Loss budget left today: unknown"; `ProposalCard.tsx:67` with `web/src/lib/approval.ts:72-84`), against the spec in `docs/ux/approval-card.md:79` ("LOSS BUDGET £11.80 left today ..."). Acceptance: `budgetLine` texts drop the "Loss budget left today:" prefix ("unknown until an engine reports", "can't check (…)"); the label stays once; Telegram text unchanged unless it repeats too. Vitest updated.
