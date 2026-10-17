- Email-code sign-in, web (BACKLOG 77 step 3, implementer, 2026-10-17; ADR 0028, TRACEABILITY U22; spec
  docs/ux/sign-in-email-code.md states A-H). `web/src/screens/Login.tsx`: when `AuthState.email_code` is true the Sign
  in screen has a "Second step" radio (Email me a code / Authenticator app; the choice is the only thing stored, as
  `signin.method` in localStorage; email is the default); the authenticator field keeps its slot hidden so switching
  moves nothing. The code screen (B-F) masks the address in the browser, shows the expiry in local time with UTC in the
  title, one numeric `one-time-code` field that signs in on the 6th digit, a fixed 44 px resend slot (countdown, the
  hourly limit when `resend_after_s` > 60, or the Resend button), a 2-minute "No email?" hint, one `aria-live` region
  (Code sent / 1 minute left / Code expired) and `role="alert"` errors joined by `aria-describedby`. Wrong codes are
  counted in the browser (the server says the same words for every refusal): after 3 the Sign in button is disabled
  and Resend is offered at once; a resend that returns a new challenge restarts the code (three fresh tries), the same
  challenge back means wait. A lockout reply shows State F for both methods. Without mail the screen is today's (H).
  `Users.tsx` asks for "Authenticator code (or a recovery code)" after an email-code sign-in (or once the server says
  so) and sends it as `totp` with invite, role, disable, enable, reset link and sign-out-everywhere; the 403 reads in
  words. `Account.tsx`: a neutral "Email codes: on/off" row for the owner. `scripts/e2e_server.py
  install_email_code_key` seeds a throwaway HMAC key in-process with `--fake-mailer`, so an e2e run never writes the
  developer's keyring or `state/.secrets.json`. e2e: emailed-code sign-in from the fake outbox, the code screen at
  360 px, a refused then accepted invite (step-up). OWNER_GUIDE "Signing in" updated.
  Review round 1 (ui-ux, code-reviewer): `lib/api.ts` throws `ApiError` (HTTP status; 0 with "Could not reach
  goldbot..." instead of the browser's text) and `SERVER_SAYS`, the server refusals the screens match word for word
  (`auth.SIGN_IN_LOCKED`, `app.STEPUP_REQUIRED`, `app.STEPUP_LOCKED`; `tests/test_web_auth_wording.py` pins both
  sides). Only a 401/403 counts as a wrong code; a lockout goes to State F, a 5xx or the network reads in plain words;
  Enter on a partial code spends no try; one code check at a time. The password field has a 44 px Show/Hide toggle;
  the sign-in form waits for `/api/auth/state`, so the method choice never pushes it down. Found on the way: a spent
  `#signin=` token came back after sign-out in the same page load: fixed on the tip by `links.clearLinkTokens` (issue
  #89); this lane adds a links test for it.
