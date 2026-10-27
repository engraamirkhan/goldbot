---
number: 130
rank: 50
title: 'Trades list: one row per trade, filters, phone rows and desktop table'
stage: UI
size: M
status: 'proposed · accountable: implementer · design: ui-ux-designer · reviews: code-reviewer'
---
- **130. Trades list: one row per trade, filters, phone rows and desktop table.** Value: one place to see what is open and find any trade (docs/design/trades-ux-2026-10-27.md §3.1, §3.2, §3.6). Acceptance: Trades tabs become Now / List / Strategies / Today with old hashes redirected; Now groups open trades by account with risk £, P&L £ and R, time held; List is one row per trade with filters in the URL and cursor paging; real proposal pinned at the top with account and REAL first; blocked notice once; 360 px and 1280 px screenshots in light and dark; no horizontal scroll; fixed row heights (no layout shift on poll, checked in e2e); 44 px targets; no meaning by colour alone; lint, typecheck, vitest, build, e2e pass.
