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
