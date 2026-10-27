---
number: 128
rank: 48
title: 'Open trades show money: publish the conversion, risk and P&L in £ on every row'
stage: Engine · API · UI
size: S
status: 'proposed · accountable: implementer · design: ui-ux-designer · reviews: code-reviewer'
---
- **128. Open trades show money: publish the conversion, risk and P&L in £ on every row.** Value: every open row on the dashboard and `goldbot run positions` shows 'money not known' / 'risk ? GBP pnl ? GBP' because the engine never writes `usd_to_account` to engine_<account>.json and every account is GBP (docs/design/trades-ux-2026-10-27.md A1). Fields: engine payload `usd_to_account` (account currency per USD, from `_open_fx`) and `usd_to_account_utc`; `OpenPosition` gains `risk_amount` (loss at the stop at entry, account currency), `held_s`, `hold_limit_s`, `account_label`, `kind`. Acceptance: a test with a fresh GBP engine payload shows floating P&L and open risk on every row (fails before, passes after); totals per account are numbers, not None; a rate older than 120 s is shown stale, never as current; trading-safety-reviewer confirms the published rate is reporting-only.
