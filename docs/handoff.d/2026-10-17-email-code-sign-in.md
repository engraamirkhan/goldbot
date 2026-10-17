- Email-code sign-in, backend (BACKLOG 77 steps 1-2, implementer, 2026-10-17; ADR 0028, TRACEABILITY U22; spec and
  security conditions 1-11 in docs/ux/sign-in-email-code.md). The owner can sign in with password + a 6-digit code
  sent by email while mail is ready: `POST /api/auth/email-code` (same challenge shape for every input),
  `/email-code/verify` (session method `email_code`), `/email-code/resend` (60 s pacing, shared 3-an-hour limit with
  the email link). An email-code session needs the authenticator or a recovery code (`totp` field) for invite, reset
  link, role, disable, enable and revoke-sessions (403 "authenticator code required"); a router test fails for any
  new owner write route that is neither stepped up nor already authenticator-gated. aux.db migration 8 is additive
  (`email_codes`, `sessions.method`, `auth_suspensions`). HMAC key `email-code-hmac-key` lives in the keyring only
  (created on first use; unreadable or corrupt means off). RUNBOOK 2.10 "Email sign-in code". Not done here: the
  Login/Users/Account screens and the e2e (step 3, web lane, against the regenerated contract), OWNER_GUIDE.
