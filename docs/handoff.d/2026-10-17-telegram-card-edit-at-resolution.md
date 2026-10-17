- Telegram NOT CONFIRMED card edited at resolution (BACKLOG 87; copy `docs/ux/order-not-confirmed.md`). This closes
  the "Not done" of BACKLOG 72.
  - When the outbox reports a card NOT CONFIRMED, the Telegram service keeps the ids of the reply messages that went
    out. They live in aux.db table `outbox_watch` (migration 9, additive).
  - On each poll it re-reads the archived proposal, which `engine.unconfirmed._card` rewrites at each resolution. When
    the outcome changes, it edits those replies to `not_confirmed.card_text` (OPENED, OPENED AND CLOSED, NOT PLACED,
    OPENED AFTER ALL, STILL NOT CONFIRMED). There is one attempt per outcome, recorded in aux.db, so a restart never
    repeats an edit.
  - The watch ends on a settled outcome (`not_confirmed.SETTLED`: opened, opened and closed, opened after all). "No
    trade" stays watched for the correction. The watch also ends when the archive is gone or after 8 days
    (`WATCH_MAX_S`).
  - A card whose reply never went out is never watched or edited. Every step is best effort and logged; a failed edit
    or a busy aux.db never stops the owner's notices in the same pass.
  - The edited message is the NOT CONFIRMED reply (the message that carries the outcome line), not the proposal text
    above it, which the bot cannot rebuild. No new owner-facing strings.
  - `TelegramBot._pump`'s outbox work moved into `_outbox_pass` so it can be tested without the network.
  - Engine path unchanged (no edit to `goldbot/engine` or `approvals._on_decision`).
  - Reviews needed: ui-ux, code.
