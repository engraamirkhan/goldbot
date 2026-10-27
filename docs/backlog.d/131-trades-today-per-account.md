---
number: 131
rank: 8.3
title: 'Today per account: fills vs target, net £ by kind after costs, caps and open risk'
stage: API · UI
size: M
status: 'built 2026-10-27 (backend; U28-U30) · reviews owed: code-reviewer, finance-auditor (numbers); web lane builds the Today cards'
---
- **131. Today per account: fills vs target, net £ by kind after costs, caps and open risk.** Value: the owner sees per account whether the day is on plan (target about 200 fills per account, from config) without asking (docs/design/trades-ux-2026-10-27.md §2.4, §3.5). Fields on `GET /api/today`: `fills_today`, `fills_target`, `pace`, `by_kind` {fills, closed, net_pnl, costs, net_r}, `open_n`, `open_risk`, `day_cap_used`, `week_cap_used`, `exploration_pot_used`. Acceptance: Today tab per account card replaces Summary; demo and real never summed, currencies never summed; quiet-day wording on non-opportunity days; numbers equal `/today` in Telegram (one builder). Lifecycle: run an edge (daily profit against the ADR 0004 north-star) and find an edge (learning volume on plan). Depends: 128, 129. TRACEABILITY: none yet; implementer adds a U row. Daily £ summary (owner expects it): per account a 14-trading-day strip of net £ after costs per day against the daily target from config (ADR 0004 £50 on the £1,000 basis; scaled per account by config, never hard-coded), opportunity days marked, plus week-to-date and month-to-date net £ and costs £ (spread, commission, swap). Tests: today's net £ equals the sum of today's closed ledger rows (129) to the penny; a seeded 14-day fixture renders 14 bars with the target line; quiet-day wording appears only when the volatility screen says so.
