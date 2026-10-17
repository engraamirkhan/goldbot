---
number: 64
title: Dashboard header shows the signed-in role, never the email
stage: Dashboard (every screen; privacy) · RUN
size: S
status: 'done, accepted with follow-up 2026-10-17 by the product owner (bb80ac1 on main via PR #84). Criteria met: role in the header, email only under Account > Your sign-in, unit and e2e checks (App.test.tsx, roles.test.ts, Account.test.tsx, dashboard and auth e2e). Gap: the ui-ux-designer and code-reviewer verdicts are not recorded at a merge (only claimed in the PR #84 body); both record them in the HANDOFF fragment by 2026-10-19 · accountable: ui-ux-designer'
---
- **64. Dashboard header shows the signed-in role, never the email.** Decided 2026-10-16 by program director +
  product owner (owner delegated). Value (RUN, privacy): the header shows the signed-in email, and it appears in
  every phone screenshot the owner shares; the role ("Owner", "Viewer") tells the reader everything they need.
  TRACEABILITY: none (UI privacy). Depends on: nothing; touches the `web/src/` header component and, if the API
  sends the email only for the header, the session endpoint in goldbot/api.
  Order (product owner change to the proposal): ui-ux-designer writes the one-line spec first (label, where the
  email is still reachable, e.g. the account page for the owner only), then the implementer, then the
  ui-ux-designer reviews, per CLAUDE.md "ui-ux-designer for any screen change -> implementer".
  Acceptance:
  - no screen's persistent chrome (header, side bar, footer, safety strip) renders the email or any part of it; the
    header shows the role;
  - the email stays reachable only where the owner manages users or their own account;
  - tests: a web unit test renders the header for an owner session with an email and asserts the role text and
    that the email string is absent; an e2e check on Overview asserts the same;
  - ui-ux-designer approval and code-reviewer APPROVE recorded at the merge.
