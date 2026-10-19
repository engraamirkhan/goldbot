---
number: 97
rank: 33
title: Research screen summary-first, detail on demand
stage: 'UI (research) · UX'
size: M
status: 'proposed, severity MEDIUM · accountable: ui-ux-designer (spec) then implementer · reviews: code-reviewer, research-director (what the summary says)'
---
- **97. Research screen summary-first, detail on demand.** Value: Research is ~6,200 px on desktop and ~11,300 px on a phone: plan details, registry rows, document hashes (`web/src/screens/Research.tsx:68-79`) and the first hypothesis table expanded (`:197` `open={i === 0}`). The owner cannot find the answer to "is anything getting closer to live?". Acceptance: top of screen is a summary card (trials used of budget this quarter, best candidate and its verdict in words, next decision and date, what changed since last week) under 600 px at 390 wide; plan, moves, registry and hypotheses collapse by default with one-line summaries; hashes and commit ids only inside an "Audit detail" disclosure. No data removed. e2e: full-page height at 390 px under 2,500 px with everything collapsed.
