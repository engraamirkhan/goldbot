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
  - Review round 1 (code and security APPROVE, ui-ux REQUEST CHANGES), fixed in one commit:
    - Tap targets: the Proposed tag and the proposed-thresholds note are 44 px tall.
    - The stop rule's Edge sentence spans the value and meter columns at 360 px.
    - An item that shows "have X of Y" no longer repeats its `need`.
    - The breakdown select falls back to the first breakdown when a refresh drops the chosen one or the screen
      narrows below 960 px.
    - An unknown stop-rule status or verdict from a newer server reads "Unknown (…)" in grey instead of blanking
      the tab.
    - Notes are keyed by index, and the "by agent" breakdown has a test.
  - Follow-ups:
    - API: send `checked_now` on the stop rule. The screen copies the server's "month 18 and more than 500 trades"
      rule only to choose its wording (marked in `LongRun`).
    - ui-ux backlog:
      - Gate evidence and need in plain words from the API (`gates_phase.py` around lines 563, 634 and 638).
      - The breakdown as a real table from 720 px.
      - "(2 days ago)" in the stale note.
      - A family-name map.
      - For a gate met on a proposed threshold, show "sign it off first" before the record commands. This is needed
        before gate 3 can be met.
  - Reviews needed: ui-ux-designer (re-check of the three fixes).
