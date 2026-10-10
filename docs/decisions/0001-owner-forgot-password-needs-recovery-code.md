# 0001 Owner forgot-password needs a recovery code; lockout persisted with a per-IP limit

## Context
The security review found that forgot-password reset any account, the owner included, with the email and a
current authenticator code. Someone holding the owner's phone or TOTP seed could take over the admin account.
The two fixes on the table were: disable forgot-password for the owner, or require a recovery code as well.

## Decision
Require a recovery code (spent) in addition to the TOTP for the owner. Disabling it would leave an owner who lost
only the password with no remote path, since recovery-login needs the password. Two independent factors (the
phone and the recovery codes in the password manager) are needed either way. Every successful forgot-password,
for any account, queues a Telegram notice to the owner.

Lockout counters move from memory to aux.db (`auth_failures`, pruned to the 15-minute window): 5 failures per
email and 20 per client IP across emails. CF-Connecting-IP is trusted only from the loopback peer (cloudflared).

## Consequences
- Owner who lost both the password and the authenticator, or the authenticator and every recovery code, needs
  server access (RUNBOOK).
- Anyone who knows an email can keep that account locked out by failing on purpose; the per-IP limit slows one
  source but not a distributed one. Accepted: a lockout delays the owner by 15 minutes, a takeover costs money.
  Revisit if the audit log shows sustained lockouts.
