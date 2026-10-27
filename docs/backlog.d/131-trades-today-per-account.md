---
number: 131
rank: 51
title: 'Today per account: fills vs target, net £ by kind after costs, caps and open risk'
stage: API · UI
size: M
status: 'proposed · accountable: implementer · design: ui-ux-designer · reviews: code-reviewer'
---
- **131. Today per account: fills vs target, net £ by kind after costs, caps and open risk.** Value: the owner sees per account whether the day is on plan (target about 200 fills per account, from config) without asking (docs/design/trades-ux-2026-10-27.md §2.4, §3.5). Fields on `GET /api/today`: `fills_today`, `fills_target`, `pace`, `by_kind` {fills, closed, net_pnl, costs, net_r}, `open_n`, `open_risk`, `day_cap_used`, `week_cap_used`, `exploration_pot_used`. Acceptance: Today tab per account card replaces Summary; demo and real never summed, currencies never summed; quiet-day wording on non-opportunity days; numbers equal `/today` in Telegram (one builder).
