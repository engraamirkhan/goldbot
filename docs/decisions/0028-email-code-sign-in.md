# 0028: Password plus emailed code signs the owner in

Status: accepted (implementer, 2026-10-17; owner request of 2026-10-17, BACKLOG 77; security plan review
APPROVE-WITH-CONDITIONS d025dca, conditions 1-11 in docs/ux/sign-in-email-code.md). Supersedes, for the owner's
email-code path only, ADR 0013's clause "The authenticator is still needed"; that clause still holds for the email
sign-in link.

## Context
The owner asked for "the one time password send to owners email option" and dislikes typing an authenticator code
at every sign-in. Until now every owner sign-in needed the authenticator (password + code, or ADR 0013's mailbox
link + code). This account confirms real-money entries, re-arms after a halt, switches auto mode and manages users.

## Decision
With mail ready (`auth.mail.enabled`, complete settings, the SMTP password and the `email-code-hmac-key` in the
keyring), the owner may sign in with **password + a 6-digit code sent to `auth.owner_email`**, without the
authenticator. Approvers and viewers cannot (ruling 3).

The trade-off in plain words: someone who has the owner's password AND the owner's mailbox can sign in as the owner.
In that session they can view everything, approve or reject the engine's proposals and halt. They cannot keep
access or raise authority: the session records `method = email_code`, and invite, reset link, role change,
disable, enable and sign-out-everywhere need the authenticator or a recovery code (step-up); re-arm, auto mode,
password change and new recovery codes need the authenticator as before; unlock-live stays server-side only. An
approval is still one tap, so a proposal can be approved by them, but the RiskGate checks every order whoever
approves it, and every exit stays automatic.

Limits that bound it (security conditions):
- The code: `secrets.randbelow(10**6)`, zero-padded; stored only as HMAC-SHA-256(key, challenge|code), the key in
  the OS keyring (never aux.db or a backup), compared with `compare_digest`; tied to a random 32-byte challenge held
  only by the requesting browser and stored hashed; single use, 10 minutes, 3 wrong tries burn it, only the newest
  code works.
- Same answer for every input: scrypt runs on every request (dummy hash for unknown addresses), a decoy challenge
  has the same shape, the mail goes in a background task after the reply; wrong codes and decoys get one message.
- Wrong codes count towards the sign-in lockout (`email:<owner>`); unknown or decoy challenges count against the
  client IP only. Three burned codes or one sign-in lockout in 24 h suspend email codes until the next password +
  authenticator sign-in, with one Telegram notice.
- Mail: the email link's limit is shared (3 an hour per address and per client IP); STARTTLS with certificate
  checks only; the code is never in the subject, a log, an audit row or a URL.
- Every email-code sign-in sends the owner a Telegram notice and an email. Password change, forgot-password,
  recovery sign-in, sign-out and revoke-sessions delete open codes. Email never resets the password.

## Consequences
- The owner's mailbox and password together are now enough to sign in; the authenticator remains the gate for
  everything that changes authority or risk, and the way in when mail is down. First run still enrols it.
- A thief with password + mailbox gets a session until the owner reacts to the notice (revoke-sessions); they can
  approve proposals within the RiskGate's limits but cannot invite, re-arm, switch to auto or lock the owner out of
  the authenticator path.
- Someone who knows the address can suspend email codes (failed sign-ins) or use up the hourly mail budget; the
  authenticator path is unaffected.
- **Revert trigger:** turn email codes off (`auth.mail.enabled: false`, restart the API) on any live-money incident,
  or if the Gmail account has no 2-Step Verification. A second person ever holding the owner role also reopens this
  decision.

## Amendment 2026-10-17: the code goes through Telegram (BACKLOG 77; security APPROVE-WITH-CONDITIONS, 1-7)
Context: the owner will not provide a Gmail App Password, so no mail can be sent. The code is delivered through the
existing Telegram bot instead; the mail path stays behind a `CodeDelivery` seam and `auth.code_channel: telegram|mail`
(default `telegram`).

Decision: everything above stands with "sent to the owner's goldbot Telegram chat" for "emailed", and these changes.
- **One chat.** Keyring `telegram-owner-chat-id` holds one positive integer that must also be in
  `telegram.allowed_user_ids`; before every send a Bot API `getChat` must return `type == "private"`. Any failure
  sends nothing and audits `code_channel_unavailable`. Never a group chat.
- **Sent by the API itself** with `sendMessage` over HTTPS (certificate checks on) in a background task. The code is
  never written to agent_runs.jsonl or `_notify_owner`, the Telegram outbox, aux.db or any file; the API never calls
  `getUpdates`. The bot token is in the URL, so httpx/httpcore log at WARNING and errors are logged by type only.
- **Message:** the code, its expiry and "Not you? Your password is known — change it with your authenticator."; no email,
  no password context, `protect_content`, no `parse_mode`. Only the Telegram message id is stored with the challenge
  (aux.db migration 10); `deleteMessage` on use, burn, resend and expiry, best effort.
- **Condition 10 (a notice to the mailbox) is replaced:** every Telegram-code sign-in writes a `tg_code_login` audit
  row, and after every owner sign-in a banner lists these sign-ins (time, coarse network, last 7 days) with a one-tap
  "Sign out everywhere" (revoke-sessions; after a code sign-in it still needs the authenticator).
- Unchanged: HMAC-keyed single-use code, 600 s, 3 tries, challenge binding, decoy parity including resend,
  suspension, step-up routes, and the shared send limit (now covering Telegram sends).

Threat model now: password + the owner's Telegram account (previously password + mailbox). A Telegram code cannot
change the chat id or turn itself on: both are server-side settings and keyring entries.

**Revert trigger (replaces the Gmail one):** Telegram two-step verification is off on the owner's account, or any
live-money incident. To revert, set `auth.code_channel` to something other than a configured chat, or remove
`telegram-owner-chat-id` from the keyring and restart the API: the login page then offers only the authenticator.
