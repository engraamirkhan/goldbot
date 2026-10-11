- Email sign-in link: follow-ups to the security review of fe58fae (approved; no timing oracle found).
  - M1: ADR 0013 records the accepted risk (with mail on, mailbox plus authenticator signs the owner in) and its
    limits. Every `email_link_login` sends the owner a Telegram notice (`_notify_owner`) and an email
    (`sign_in_notice_message`), with no token and only a coarse network hint (`auth.coarse_ip`: IPv4 /16, IPv6 /32).
  - L1: `auth.mail.dashboard_origin` is checked with `urlsplit`: https with a host and no user info, path, query or
    fragment; http only for exactly localhost, 127.0.0.1 or ::1.
  - L2: sign-out-everywhere (`revoke_sessions`) and the server's `revoke-sessions` command also delete email links,
    in the same transaction.
  - L3: new health check `email_link` warns when the owner's address hit the hourly limit 3 or more times in 24 h
    (`email_link_health_note`, never names the address). OWNER_GUIDE says what to do when no link arrives.
  - L4: `web/src/main.tsx` takes link tokens out of the address bar at start, signed in or not.
  - L5: `SmtpMailer.ready()` is cached for 60 s (`READY_CACHE_S`).
  - UX: a failed redeem goes straight to "Email me a sign-in link" (the old link is spent).
  - RUNBOOK: aux.db schema 6 cannot be stepped back to 4. Releases since state-store step 3 open it; older ones are
    refused by the deploy.
