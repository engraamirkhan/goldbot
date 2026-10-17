# Sign in with a one-time code sent to the owner's email

Owner feedback (2026-10-17, verbatim): "i don't like this sign in setup and it doesn't have the one time password
send to owners email option as well, that should have been there".

Today every sign-in needs email + password + a current authenticator (TOTP) code (`web/src/screens/Login.tsx`,
`AuthStore.login`). An optional owner-only email sign-in LINK exists (`/api/auth/email-link*`, ADR 0013, RUNBOOK 2.10
"Email sign-in link", OWNER_GUIDE) but it is off by default and it still asks for the authenticator code, so it does
not answer the request. The owner wants what most banks and brokers offer: password, then a 6-digit code from email.

Backlog item: `docs/backlog.d/77-sign-in-email-code.md`. Status: design note, needs security-reviewer plan review
before any build.

## What changes, in one paragraph

On the Login screen the owner types email + password and picks how to get the second code: **Email me a code**
(default when mail is set up) or **Authenticator app** (today's path). With email, the server checks the password,
emails a 6-digit code that works once for 10 minutes, and the owner types it in. Everything else stays: the
authenticator (or a recovery code) is still needed for the owner-only money and admin actions, first run still
enrols an authenticator, the lockout is unchanged, and when mail is not configured the screen looks exactly like
today.

## Security questions (security-reviewer decides; my recommendation in bold)

1. **Is password + email code enough to sign in to the owner account, without the authenticator?** This is weaker
   than ADR 0013's link (mailbox + authenticator): it becomes password + mailbox, and the mailbox is usually on the
   same phone. **Recommend yes for sign-in, owner only, off unless `auth.mail` is ready, recorded as a new ADR that
   supersedes the relevant part of 0013**, because the owner asked for it, the two factors are still independent
   (knowledge + mailbox possession), and the actions that change risk keep the authenticator (Q2).
2. **Which actions keep the authenticator (or a recovery code) as a step-up?** Already gated by a TOTP code today:
   re-arm after a halt, auto mode on (dashboard and Telegram), password change, recovery-code regeneration. Unlock
   live is server-side only (`unlock_live` + typed phrase) and does not change. **Today invite, role change, disable
   user and owner-created reset links need only an owner session: recommend they also need an authenticator code
   when the session was opened with an email code** (the session records its sign-in method; see the Data
   section). Approving or rejecting an entry stays one tap with no step-up, whatever the sign-in method: the 90 s
   decision must not be slowed, and the RiskGate still checks every order.
3. **Should approvers get email codes too?** **Recommend no in this item (owner only, like ADR 0013).** Approvers
   approve real-money entries; extending it is a separate decision.
4. **Code in the email subject?** It is faster to read from a lock-screen notification, but anyone who can see the
   phone can read it. **Recommend no: subject without the code, code on the first body line** (iOS and Android still
   offer it for autofill from the message body).
5. **Separate or shared hourly send budget with the email link?** **Recommend shared**: one counter of 3 mails an
   hour per address and per client IP covers link + code, so turning both on does not double what an attacker can
   trigger.

## Flow and server rules (what the screens assume)

1. `POST /api/auth/email-code` with email + password. The answer is the same for every input (no oracle for the
   password or the address): an opaque challenge id, `expires_in_s` (600) and `resend_after_s` (60). A mail is sent
   only when the address is `auth.owner_email`, the account is the enabled owner, the password is right and mail
   is ready. A wrong password counts towards the sign-in lockout (`email:<e>`, 5 per 15 min), exactly as today.
   For anything else the challenge id is a decoy that can never verify.
2. The code: 6 digits from `secrets.randbelow(10**6)`, zero-padded; stored only as a keyed hash (HMAC-SHA-256 with
   a server secret, or scrypt; a plain SHA-256 of a 6-digit space is brute-forceable from a database copy); never
   logged, never audited, never in an exception message; the mail body is not logged.
3. `POST /api/auth/email-code/verify` with the challenge id + code. Right code: the challenge is deleted (single
   use), a 12 h session opens, the owner gets the ADR 0013 sign-in notice (Telegram + email, time + coarse network,
   names `revoke-sessions`). Wrong code: counts towards the sign-in lockout; after 3 wrong codes the challenge is
   burned and a new code is needed. Unknown, expired, decoy or already-used challenge: one generic refusal, counted
   against the client IP only (garbage cannot lock the owner out).
4. Resend: a new code replaces the old one (only the newest code works); not before 60 s; at most 3 mails an hour
   per address and per client IP (Q5). Over the limit the answer is still generic; the screen shows the cooldown.
5. A password change, forgot-password, recovery-code sign-in and sign-out-everywhere (`revoke-sessions`) delete
   open challenges, as they do for links.

## Login screen

Phone first: 360 px wide, 16 px gutters, one column, every button and the method choice at least 44 px high,
inputs 16 px text (no iOS zoom). Desktop: the same card, max 400 px wide, centred. Light and dark use the existing
tokens; all text meets WCAG AA (4.5:1; 3:1 for the input borders and focus ring).

### State A: sign in, choose method (mail ready)

```
goldbot
Sign in

Email            [ owner@example.com          ]
Password         [ ••••••••••••••             ] [Show]

Second step
 ( • ) Email me a code        ( ) Authenticator app      <- radio group, two 44 px rows on phone

[        Continue        ]                               <- primary, full width

Forgot password?   Use a recovery code
```

- The radio group defaults to the owner's last choice (stored in localStorage as `signin.method`, no secret), and
  to Email when nothing is stored. Choosing Authenticator app shows today's "Authenticator code" field under the
  password and the button reads **Sign in**; nothing else moves (the field slot is reserved, no layout shift).
- With Email chosen the button reads **Email me a code**.
- The old "Email me a sign-in link" link stays only while `email_link` is enabled; recommend retiring the link once
  this ships (one email path is simpler to explain and to secure).
- Loading: the button shows "Sending..." and is disabled; the fields stay as typed.

### State B: code sent

```
goldbot
Enter the code we emailed

If the email and password are right, a 6-digit code is on its way to
o•••@g•••.com. It works once, for 10 minutes (until 14:42).   <- local time, UTC in the title

Code             [ _ _ _ _ _ _ ]                          <- one field, autofocused

[        Sign in        ]

Resend code in 0:47                                       <- becomes the button "Resend code"
Use the authenticator app instead    Start again
```

- The masked address is built in the browser from what the owner typed; the server never echoes it.
- One input, not six boxes: `inputMode="numeric"`, `autocomplete="one-time-code"`, `pattern="[0-9 ]*"`,
  `maxLength` 7 (a pasted "482 913" is accepted; spaces are stripped). Submits on the 6th digit only when typed or
  pasted, never on a partial value.
- Countdown text uses tabular numerals and a fixed-width slot, so the line never reflows.
- "Use the authenticator app instead" returns to State A with Authenticator chosen and the email and password kept.
  "Start again" clears the password and returns to State A.
- Loading: "Checking..." on the button.

### State C: wrong code

Error under the field, linked with `aria-describedby`, focus returns to the field with the text selected:

> That code did not work. Check the newest email from goldbot. 2 tries left for this code.

After the third wrong code: "That code can no longer be used. Send a new one." and the Resend button is enabled at
once (still within the hourly limit).

### State D: expired or already used

> This code has expired or was already used. Send a new code.

Resend is enabled (subject to the 60 s cooldown and hourly limit). The same message covers decoy challenges, so a
wrong password at State A surfaces here as "no email arrived" plus this message.

### State E: resend cooldown and hourly limit

- Cooldown: "Resend code in 0:47" (plain text, not a disabled button, so screen readers do not hit a dead control).
- Hourly limit reached: "You have asked for 3 codes in the last hour. Try again after 15:05, or use the authenticator
  app." The time comes from the server's `resend_after_s`; the authenticator path is always offered.
- Mail never arrives: under the field, after 2 minutes, "No email? Check the spam folder, check the password you
  typed (we do not say whether it was right), or use the authenticator app."

### State F: locked out (existing rule, 5 failures per email in 15 minutes)

Wrong passwords at State A and wrong codes at State C share the one counter. Copy:

> Too many attempts. Sign-in for this account is paused for 15 minutes (until 15:12). This applies to the
> email code and the authenticator alike. If you did not cause it, someone may know your password: change it once
> you are back in.

Both buttons are disabled until the time shown; the time is local with UTC in the title.

### State G: success

Straight to Overview, as today. The owner also gets the sign-in notice by Telegram and email.

### State H: mail not configured

`GET /api/auth/state` returns `email_code: false`. The Login screen is exactly today's: no radio group, the
Authenticator code field and **Sign in**. For a signed-in owner the Account screen shows a neutral (not red) row:

> Email codes: off. Mail is not set up on the server (RUNBOOK 2.10, "Email sign-in"). Sign-in uses your
> authenticator app.

and the Health screen keeps its existing mail and request-flood checks. Red is never used: this does not stop trading.

### Step-up screens (re-arm, auto mode, user management)

Unchanged where they already ask for a code. Where Q2 adds one, the same field label is used:
"Authenticator code (or a recovery code)", with the hint "Signed in with an email code: this action also needs your
authenticator." Telegram /rearm and /mode keep their authenticator code.

## First run (setup screen)

Yes, first run still enrols an authenticator and shows the 10 recovery codes: the money actions need it, and it is
the way in when mail is down. The intro copy changes so the owner knows why:

> Add goldbot to your authenticator app. You will need it to re-arm after a halt, switch auto mode and manage users.
> Day-to-day sign-in can use a code sent to your email once mail is set up.

## Email copy

Plain text only, no links, no images, no tracking. From: `auth.mail.from_address`.

```
Subject: Your goldbot sign-in code

482913

This is your goldbot sign-in code. It works once, until 14:42 London time (13:42 UTC).

Requested at 14:32 London time from network 81.2.x.x.
Did not ask for it? Someone has your goldbot password. Change it now, and on the server run
revoke-sessions to sign everyone out.

goldbot never asks for this code by phone, chat or Telegram.
```

The time zone is the server's configured display zone; the network hint is the existing coarse form (IPv4 /16,
IPv6 /32). The code line has nothing else on it, so a long press copies only the digits.

## Data the API needs (implementer adds; then `python scripts/export_openapi.py && npm --prefix web run gen:api`)

- `AuthState.email_code: bool` (mail ready and owner exists).
- `EmailCodeRequest {email, password}` -> `EmailCodeChallenge {challenge: str, expires_in_s: int,
  resend_after_s: int}`.
- `EmailCodeVerifyRequest {challenge, code}` -> `LoginResponse` (unchanged).
- `EmailCodeResendRequest {challenge}` -> `EmailCodeChallenge`.
- Sessions record `method` (`password_totp`, `email_link`, `email_code`, `recovery`) for the Q2 step-up; it is not
  shown in the UI except the step-up hint.

## Accessibility

- Every state change is announced once through one `aria-live="polite"` region ("Code sent", "1 minute left",
  "Code expired"); the per-second countdown is not announced.
- Errors use `role="alert"`, sit under their field and are joined with `aria-describedby`; they are words, never
  colour alone.
- Visible focus ring on every control; tab order is fields, method, primary button, secondary links.
- Labels are visible (no placeholder-only fields); the password field has a 44 px Show toggle.
- Every time shown is local with UTC in the `title`; every duration says its unit ("10 minutes", "0:47").

## Out of scope

Approver email codes (Q3), SMS, passkeys, retiring the email link (recommended follow-up), Telegram sign-in.
