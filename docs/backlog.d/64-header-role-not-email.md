---
number: 64
rank: 12
title: Dashboard header shows the signed-in role, never the email
stage: Dashboard (every screen; privacy) · RUN
size: S
status: 'spec ready (docs/ux/header-role.md, 2026-10-16; no API change) · next: implementer (failing tests first), then ui-ux-designer review · accountable: implementer · due 2026-10-27'
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
