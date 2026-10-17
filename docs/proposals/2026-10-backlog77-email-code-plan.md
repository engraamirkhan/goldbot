# Plan: BACKLOG 77 — sign in with a one-time code sent to the owner's email

Status: plan, architect, 2026-10-17 (recorded by the program director). Spec and the 11 binding security conditions:
docs/ux/sign-in-email-code.md (d025dca + d3abc2d). Owner priority: built ahead of all other owner-facing work.
TRACEABILITY: add `U19` ("password + emailed code signs the owner in; the authenticator stays for money and admin
actions"); U1 and U4 get a "see U19" note. Routes as the spec binds them: `POST /api/auth/email-code`,
`/api/auth/email-code/verify`, `/api/auth/email-code/resend`.

**Non-negotiables.** Nothing touches the engine or runner.py. Approve/reject (`/api/decide`) stay one tap; `/halt`
unchanged; unlock-live stays server-side. Public repo: placeholders only (`owner@example.com`).

## Files and changes

1. `goldbot/db/migrations.py`: AUX migration 8, additive like v6: `email_codes(challenge_sha256 PK, email, code_hmac,
   expires_ns, tries, consumed, created_ns)`; `sessions.method TEXT NOT NULL DEFAULT 'password_totp'` (additive ALTER; a
   rollback release ignores it); suspension derived from audit (see 3) or `auth_suspensions(email PK, since_ns)`.
2. `goldbot/api/auth.py`: `EMAIL_CODE_TTL_S=600`, `EMAIL_CODE_TRIES=3`, `RESEND_AFTER_S=60`;
   `request_email_code(email, password, ip, *, enabled) -> (challenge, code|None)` (always scrypts, `_dummy_hash` ~161);
   `verify_email_code(challenge, code, ip) -> token` via `_new_session(..., method="email_code")` (~651);
   `resend_email_code(challenge, ip)`; `session_method(token)`; `need_stepup(method)` true only for `email_code`;
   `verify_stepup(email, code, ip)` = TOTP through `verify_code_guarded` (~545) then a recovery code, both counted under
   `code:<e>`; `DELETE FROM email_codes` beside every `DELETE FROM email_links` (~727, 772, 903, 1058, 1097); `method`
   set at login (`password_totp`), `redeem_email_link` (`email_link`), `recovery_login` (`recovery`).
3. **Suspension (condition 3):** three `email_code_burned` audit rows or one `login_locked` in 24 h suspend email codes
   until the next `login()` success (`email_code_resumed` audit row); while suspended `request_email_code` answers
   generically and sends nothing; the first suspension queues a `_notify_owner` Telegram notice (~790).
4. **Shared hourly limit (ruling 5):** reuse `email_link_requests` keys (`email:<n>`, `ip:<ip>`, 3/hour, ~914).
5. **HMAC key (condition 4):** keyring entry `email-code-hmac-key` via `goldbot.ops.accounts.get_secret`
   (accounts.py:125); 32 random bytes generated lazily with `set_secret` (~139) on first use, cached in-process; never
   in aux.db or backups; unreadable keyring ⇒ `enabled=false` (fail closed); `accounts set email-code-hmac-key` rotates
   it (open codes die). Store `HMAC(key, challenge_id||code)`; `hmac.compare_digest`. Binding the challenge makes a
   code useless for any other challenge (condition 5).
6. **Decoy timing (condition 6):** scrypt on every request; decoy challenge = `secrets.token_urlsafe(32)` with no row;
   mail from `BackgroundTasks` after the reply (as app.py:362); same JSON shape (600 and 60);
   `test_email_code_decoy_timing` compares medians within a tolerance.
7. `goldbot/api/mailer.py`: `email_code_message(code, expires_local, utc, network)` — no code in the subject, code alone
   on line 1, body never logged; STARTTLS unchanged (condition 7).
8. `goldbot/api/schema.py` (~162): `AuthState.email_code`, `EmailCodeRequest`, `EmailCodeChallenge`,
   `EmailCodeVerifyRequest`, `EmailCodeResendRequest`; optional `totp: str = ""` on `InviteRequest`, `UserRef`,
   `RoleChange` (`UserRef` covers reset-link, disable, enable, revoke-sessions); `LoginResponse.signed_in_with`; `/api/me`
   returns it.
9. `goldbot/api/app.py`: three routes beside ~361 with a ready check, `client_ip`, a Telegram notice from
   `_notify_owner` on every success (condition 10) and `send_sign_in_notice`; a `stepup(u, creds, body.totp)` helper
   raising 403 "authenticator code required" when `need_stepup(session_method)` and `verify_stepup` fails; wired into
   exactly `invite` (~391), `reset_link` (~430), `set_role` (~468), `disable` (~476), `enable` (~484),
   `revoke_sessions` (~492). Already authenticator-gated, untouched: `rearm` (~640), `/mode` (~775),
   `change_password` (~407), `recovery_codes` (~455). Not gated: `decide`, `halt`, reads. **A parametrised test iterates
   the router so a new owner route fails unless it is on the allow-list.**
10. e2e: `scripts/e2e_server.py --fake-mailer` + `goldbot/api/e2e_mailer.py` (`CaptureMailer` JSONL outbox) need no
    change; `web/e2e/auth.spec.ts` reads the 6-digit line from the outbox.
11. Web: `Login.tsx` method radio (`signin.method` in localStorage), `emailCode` mode for states B-F, fixed-width
    countdown slot, `aria-live`, `role="alert"`; states G/H per spec; `lib/api.ts` three calls + `totp` on the six admin
    calls; Users screen shows an authenticator-code field on those six actions when the session method is `email_code`;
    `Account.tsx` (~103) "Email codes: off / on" row.
12. ADR: `new-adr --slug email-code-sign-in --title "Password plus emailed code signs the owner in"`, superseding ADR
    0013's "authenticator still needed" clause; trade-off in plain words; revert trigger (condition 1).
13. Docs: HANDOFF fragment; `new-row U19 --slug email-code`; backlog 77 → implemented; OWNER_GUIDE; RUNBOOK 2.10 (VPS:
    `accounts set email-code-hmac-key`, `smtp-password`; a real keyring required on the VPS — the `.secrets.json`
    fallback is for the brain only); regenerate the contract.

## Failing tests first

`tests/test_email_code.py`: the 14 names in docs/backlog.d/77 + `test_email_code_stepup_on_enable_and_reset_link`,
`test_email_code_suspended_after_burns`, `test_email_code_decoy_timing`; boundaries (expiry at exactly 600 s; the 3rd
wrong code burns the challenge; the 4th mail in an hour refused); single use survives an `AuthStore` restart; missing or
corrupt key ⇒ off; replay after sign-out fails; the router allow-list test. `tests/test_migrations.py`: v8 additive, a v7
file upgrades. `web/src/screens/Login.test.tsx` states A-H; `web/e2e/auth.spec.ts` emailed-code sign-in + a step-up
refusal.

## PR-sized steps

1. ADR, migration, `auth.py` core + tests (critical path). 2. `schema.py`, `app.py`, `mailer.py`, step-up wiring
(sequential with 1: overlapping files). 3. Web + e2e after the contract regen. 4. Docs.

**Risks.** An admin route that forgets the step-up (router test); decoy/real timing drift (scrypt on every call + the
background send); keyring fallback to plaintext on a headless host (RUNBOOK requires a real keyring on the VPS);
password + mailbox can approve proposals (RiskGate still bounds them, said in the ADR). **Owner decision:** none.

**Reviews.** security (plan done; code review before push), code-reviewer, trading-safety (step-up on money routes),
release-manager.
