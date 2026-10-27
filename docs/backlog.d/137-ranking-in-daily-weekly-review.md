---
number: 137
rank: 8.45
title: 'Ranking in every daily review (Telegram, dashboard) and the weekly digest'
stage: 'Research (review) · Telegram · UI'
size: M
status: 'ready after 136 · accountable: implementer · design: ui-ux-designer · reviews: quant-reviewer, code-reviewer · due 2026-11-06'
---
- **137. Ranking in every daily review (Telegram, dashboard) and the weekly digest.** Value: every review of trades carries the ranking, as the owner directed, so the daily and weekly learning loop starts from which families earn after costs and why; lifecycle: review and improve; helps FIND an edge. Spec: docs/design/trades-ux-2026-10-27.md §7.5. Acceptance (tests): (1) the morning Telegram review has a ranking section of at most 12 lines: the heading line of §7.4 rule 5, the top 5 ranked, the 2 lowest ranked when 8 or more are ranked, "+N ranked, M too few to rank" and the dashboard link; each line has net R/trade lower bound, mean, n, hit rate, net £, edge vs random and status; no tables, true minus; (2) the dashboard daily review shows the full §7.3 table with expandable variants, controls and retired bands; (3) the weekly digest shows the week's and the version's ranking with rank change vs last week ("up 2", "new"); (4) all three numbers equal `GET /api/strategies/ranking` for the same window (test); (5) the daily review's `blind()` and banned-token check no longer drop the ranking section (ADR 0053 decision 4) and still run on the other sections until 139 lands; (6) greyed rows say "too few to rank (n of 30 fills)"; (7) no slugs, ids or logins (leak test). Depends: 136. TRACEABILITY: none yet; implementer adds a U row.
