- Owner-only email sign-in link and clearer sign-in screens (owner request 2026-10-11; TRACEABILITY U20). The login
  page now titles each flow (Sign in, Reset your password, Email me a sign-in link, Finish signing in, Set a new
  password), shows **Forgot password?**, **Email me a sign-in link** (only when the server has mail configured) and
  **Use a recovery code**, and fits a phone (16px inputs, 44px targets). `POST /api/auth/email-link {email}` answers
  one generic 200 for every address; it mails a link only to `auth.owner_email` (trimmed, case-insensitive) when that
  account is the enabled owner and mail is configured. Token: `token_urlsafe(32)`, SHA-256 in aux.db `email_links`
  (migration 6, additive), single use, 15 min; at most 3 requests an hour per address and per IP
  (`email_link_requests`). `POST /api/auth/email-link/redeem {token, totp}` STILL needs the authenticator code; any
  redeem of a live link deletes every outstanding link (a wrong code burns it and counts towards the sign-in lockout;
  unknown tokens count against the IP only). A password change, forgot-password or recovery-code sign-in also
  deletes outstanding links. Mail: `goldbot/api/mailer.py`, smtplib + STARTTLS, settings `auth.mail` (real values in
  settings.local.yaml), password in the keyring as `smtp-password`; sent in a background task after the response,
  failures logged and audited by exception type only. `goldbot run mail-test` checks the setup. Rollback note: aux.db
  schema 6 is additive, so every release since state-store step 3 opens it; a release from before step 3 is refused
  by the deploy (no 6 -> 4 step-back yet). Needs security review.
