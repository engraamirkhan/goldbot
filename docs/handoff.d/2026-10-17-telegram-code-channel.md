- Sign-in code by Telegram instead of email (BACKLOG 77 amendment, implementer, 2026-10-17; ADR 0028 amended, TRACEABILITY
  R47; security APPROVE-WITH-CONDITIONS, conditions 1-7). The owner will not provide a Gmail App Password, so the code
  now goes through the existing bot: a `CodeDelivery` seam (goldbot/api/code_delivery.py) with `TelegramCodeDelivery`
  (default) and `MailCodeDelivery` (kept), chosen by `auth.code_channel: telegram|mail`. Telegram: the API itself calls
  Bot API `getChat` (must be `private`) then `sendMessage` (`protect_content`, no `parse_mode`) over HTTPS in a background
  task, to the ONE chat id in keyring `telegram-owner-chat-id` that is also in `telegram.allowed_user_ids`; any failure
  sends nothing and audits `code_channel_unavailable` (error type only). The code is never in agent_runs.jsonl, the
  outbox, aux.db, a log or a file; only the message id is stored (aux.db migration 10, `email_codes.message_id`) and
  `deleteMessage` runs on use, burn, resend and expiry. httpx/httpcore logging is WARNING (the token is in the URL). All
  other code rules stay. Every Telegram-code sign-in writes `tg_code_login`; `GET /api/auth/code-sign-ins` feeds a banner
  (time, coarse network, last 7 days) with "Sign out everywhere" after every owner sign-in. `AuthState.email_code` is now
  `code_sign_in` + `code_channel` (contract regenerated); web copy is channel-aware ("Send me a code", "we sent a 6-digit
  code to your goldbot Telegram chat"; the mail words stay for `mail`) for ui-ux review. The e2e fake mailer got a fake
  code channel writing to the same outbox. Owner setup (RUNBOOK 2.10): `goldbot accounts set telegram-owner-chat-id`,
  no mail needed. Session method string stays `email_code` and the routes stay `/api/auth/email-code*` (stored sessions
  and the contract's route names are not churned). Not verified: a live Bot API call (no token here; the HTTP shape is
  tested against a `httpx.MockTransport`); the dashboard e2e project; gates.sh.
