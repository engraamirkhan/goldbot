---
number: 87
rank: 23
title: The Telegram card is edited once more when a NOT CONFIRMED order resolves
stage: Owner interface (Telegram cards) · RUN
size: S
status: 'ready (ui-ux follow-up on BACKLOG 72, 2026-10-17) · accountable: implementer · reviews: ui-ux, code-reviewer · due 2026-10-31'
---
- **87. The Telegram card is edited once more when a NOT CONFIRMED order resolves.** Value: today the owner's phone card stays "⚠️ NOT CONFIRMED" after the order resolves; only the notice and the dashboard carry the outcome (the outbox forgets message ids once it reports). Acceptance: after `unconfirmed._card` rewrites the archived proposal, the bot edits the original Telegram message with `card_text(<outcome>)` (OPENED / OPENED AND CLOSED / NOT PLACED / OPENED AFTER ALL / STILL NOT CONFIRMED), best effort, never blocking the engine; the outbox keeps the message id for unresolved cards until terminal; one test per outcome; a lost edit never hides the notice.
