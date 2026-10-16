- **BACKLOG 64: the dashboard header shows the signed-in role, never the email (implementer, 2026-10-16; spec
  `docs/ux/header-role.md`).** The header reads "Owner", "Approver" or "Viewer" (screen readers hear "Signed in as
  …"; "Signed in" when the session record is missing), from one map `web/src/lib/roles.ts` `roleLabel` that the
  Users tab's role column also uses. The Account tab gains "Your sign-in" (role and own email) as its first section.
  "Sign out" is a 44 px tap target below 720 px. No API change. Tests: `web/src/App.test.tsx`,
  `web/src/lib/roles.test.ts`, `web/src/screens/Account.test.tsx` ("Account: your sign-in"), and the banner, safety
  strip, Account and 360 px row/tap-target checks in `web/e2e/dashboard.spec.ts` and `web/e2e/auth.spec.ts`.
  Rule for future screens: persistent chrome never renders an identity. Next: ui-ux-designer review, code-reviewer.
