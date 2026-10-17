- Progress tab screens (ROADMAP W2-3, implementer, 2026-10-17; spec docs/ux/progress-screens.md, API from the
  progress-tab-api lane): a new **Progress** tab, third after Overview and Approvals, for every role. It is one page
  with jump chips and three sections. **Stop rule** shows NOT BREACHED, NOT STARTED, BREACHED (amber, with "Go to
  Halt" for approvers) or CAN'T CHECK, with both conditions as meters. **Road to live** shows the four gates as
  `details` rows (RECORDED, MET, MET ON A PROPOSED THRESHOLD, NOT MET k of n, WAITS); the next gate starts open and
  each item shows "have X of Y", the Proposed tag, the earliest date and the gates' 95% range, labelled "per-trade
  interval, not day-clustered". **Results** shows net R per trade with the verdict in words (no AHEAD/BEHIND below
  100 trades), breakdown rows in a fixed order judged on `verdict_adj`, "by construction" exit rows with no range,
  costs, live fills, and a stale note that says when the next run is. "Your account's trades" covers the last 30
  calendar days and names the trading days. Overview gains a one-line summary that opens Progress, and an amber
  banner while the stop rule is breached.
  - Words and order only live in `web/src/lib/progress.ts`; verdicts, flags and statuses all come from the API.
    Nothing on the tab is red, records a gate or halts. The owner alone sees the `record-gate` commands for a met gate.
  - Tests: `src/lib/progress.test.ts` and `src/screens/Progress.test.tsx` (loading, can't check, empty, populated,
    stale, met on a proposed threshold, breach for approver and viewer, 404, no identifiers); the viewer e2e story
    opens Progress at phone width.
  - Not built: the desktop breakdown as a real table (rows are disclosures at every width) and the per-section
    "last success more than 3 polls ago" rule (only the gates query shows the "can't reach the server" banner).
  - Reviews needed: ui-ux-designer (against the spec), code, security (no identifiers rendered).
