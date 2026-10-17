- Telegram NOT CONFIRMED card edited at resolution (BACKLOG 87; copy `docs/ux/order-not-confirmed.md`). This closes
  the "Not done" of BACKLOG 72.
  - When the outbox reports a card NOT CONFIRMED, the Telegram service keeps the ids of the reply messages that went
    out. They live in aux.db table `outbox_watch` (migration 9, additive).
  - On each poll it re-reads the archived proposal, which `engine.unconfirmed._card` rewrites at each resolution. When
    the outcome changes, it edits those replies to `not_confirmed.card_text` (OPENED, OPENED AND CLOSED, NOT PLACED,
    OPENED AFTER ALL, STILL NOT CONFIRMED). The shown outcome is recorded in aux.db, so a restart never repeats an
    edit.
  - Failed edits (review round 1): an edit Telegram refuses for good (`BadRequest`: already shown, deleted, too old;
    `Forbidden`) is not retried (`bot.edit_is_final`). A rate limit, timeout or network error keeps the card watched
    and is retried after 2 s, doubling to 300 s, for 10 attempts per outcome (about 13 minutes); then it is given up
    for that outcome and logged. The attempt count is kept in aux.db, so it survives a restart.
  - Known window: `finished` deletes the sent row before the service sends the NOT CONFIRMED reply, and the reply ids
    are recorded only after it went out. A crash between the send and `watch` (one aux.db write) leaves that card
    unedited at resolution. The reply ids can't be recorded earlier because they don't exist yet; the notice and the
    dashboard still carry the outcome.
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
