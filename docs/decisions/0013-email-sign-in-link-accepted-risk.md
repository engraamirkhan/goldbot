# 0013: Owner email sign-in link: mailbox plus authenticator is accepted as a sign-in

Status: accepted (implementer, 2026-10-11; owner request of 2026-10-11, security review of fe58fae, finding M1)

## Context
The owner asked for "send link to email for login, that will only be for me". Until then, every sign-in to the owner
account needed the password and a current authenticator code (TRACEABILITY U1). This account confirms real-money
entries, re-arms after a halt and switches auto mode.

The email link replaces the password. With `auth.mail.enabled` on, the factors for an owner sign-in become
**access to the owner's mailbox plus the authenticator code** as well as **password plus authenticator code**. The
authenticator is still required on both paths. Anyone who controls the mailbox (a stolen phone that is signed in to
Gmail, a phished Google account, a mail-forwarding rule) no longer needs the password, only the second factor.

## Decision
Accept the risk, because the owner asked for it, the feature is off by default, and the second factor is never
removed. The limits that bound it:
- **Owner only.** A link is issued only to `auth.owner_email` (trimmed, case-insensitive), when that account exists,
  holds the owner role and is enabled. Every other address gets the same generic answer and no mail.
- **Off unless configured.** `auth.mail.enabled` is false by default and needs host, from-address, an https dashboard
  origin, and the SMTP password in the keyring. Turning it off removes the path.
- **The authenticator is still needed.** Redeeming needs a current TOTP code, spent once (RFC 6238 replay guard).
- **Short-lived and single use.** A 32-byte random token, stored hashed, valid 15 minutes. Any redeem of a live link
  deletes every outstanding link, so a wrong code burns the link and a stolen link gets one guess. That guess counts
  towards the sign-in lockout. A password change, forgot-password, recovery-code sign-in and sign-out-everywhere
  (dashboard or `revoke-sessions`) also delete outstanding links.
- **Rate-limited.** At most 3 requests an hour per address and per client IP. The health check warns when the
  owner's address keeps hitting the limit.
- **Kept out of logs.** The token travels in the URL fragment and is never logged or audited. The address appears
  only in the audit table.
- **Announced.** Every sign-in with a link sends the owner a Telegram notice (agent_runs relay) and an email. Both
  carry the time and a coarse network hint (IPv4 /16, IPv6 /32), never the token or the full address, and name the
  server-side `revoke-sessions` command.

## Consequences
- Mailbox security is now part of the owner account's security. The owner guide tells the owner to protect the Gmail
  account with 2-Step Verification. That is also required for the App Password.
- A compromise needs both the mailbox and the authenticator. The notice gives the owner a chance to react after the
  fact, but it cannot stop the first session.
- Someone who knows the owner's address can use up the 3 requests an hour and delay the owner's link. Password
  sign-in is unaffected, and the health check surfaces it.
- What would change this decision: evidence that the mailbox is weaker than the password (no 2-Step Verification on
  the Google account), a second person given the owner role (it is never granted today), or a live-money incident.
  Any of these should turn `auth.mail.enabled` off until reviewed.
