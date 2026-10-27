---
number: 138
rank: 8.48
title: 'Strategy board: ranked view by default'
stage: 'UI (strategies) · API'
size: S
status: 'built 2026-10-27 (U33; screenshots via the e2e run) · reviews owed: code-reviewer, quant-reviewer, ui-ux-designer'
---
- **138. Strategy board: ranked view by default.** Value: the strategy board answers "which families earn after costs" at a glance while keeping progress to each read visible; lifecycle: review and improve; helps FIND an edge. Spec: docs/design/trades-ux-2026-10-27.md §7.5 (supersedes §3.4's "no order by result", ADR 0053). Acceptance: (1) "Ranked" is the default order, from 136's builder (numbers equal the ranking endpoint, test); "By progress to read" stays as a toggle and is remembered in the URL; (2) each ranked row keeps its progress bar (n of read) and anneal state; (3) greyed rows (under 30 fills or 10 days) are unranked, below, and say why; (4) controls and retired families in their own bands; (5) every row shows n and the 95% CI and "early, not proven" until its read; (6) 360 px and desktop, light and dark screenshots, no horizontal scroll; vitest on the default order and the toggle. Depends: 136, 132. TRACEABILITY: none yet; implementer extends 132's U row.
