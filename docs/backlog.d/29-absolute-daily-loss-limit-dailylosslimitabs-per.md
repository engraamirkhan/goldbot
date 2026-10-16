---
number: 29
title: Absolute daily loss limit `daily_loss_limit_abs` per account (ADR 0004)
stage: Capital and drawdown management · RUN
size: S
status: 'done (PR #71, main 423d2b1; 8cb7c66); card allowance in PR #73 (b97f9f9)'
---
- **29. Absolute daily loss limit (trading-safety review).** Value: makes the owner's £50 outer limit a hard rail
  as the account grows; the 2% / 1.5% caps stay and bind first below £2,500 / ~£3,333 (Capital management · RUN).
  Acceptance:
  - New `daily_loss_limit_abs` (account currency; validated > 0 or null; per account in accounts.yaml after review, `50` on icm-demo). Null keeps
    every gate decision identical to today (grid test).
  - Projected check, entries only: refuse `daily_loss_limit_abs` when (day-start equity − equity) + this trade's
    full stop risk in account currency (from 28) > the limit. Tests: £5,000 equity, £40 lost, a £15-risk entry is
    refused and a £9 one allowed; at £1,000 equity the 1.5% supervisor cap still stops entries first.
  - The supervisor applies the same absolute to the combined day loss (one currency, from 28) and writes HALT.
  - Exits, stops and closes never consult it (test). Health and the approval card show the remaining daily
    allowance. DESIGN Hard limits and TRACEABILITY updated. Reviews: trading-safety, code, ui-ux.
  - **Projected percentage caps too (from `docs/research/opportunity-days.md` section 5).** The 2% account cap
    (`gate.py:231-234`) and the 1.5% supervisor cap are checked on the loss so far, so at £1,000 a second 0.85% loss
    ends the day at ~1.69%, past the 1.5% supervisor cap (ADR 0004 section 3). Acceptance:
    - With `risk.project_pct_caps: true`, the gate refuses `daily_cap_headroom` when day loss + this trade's full
      stop risk (account currency, from 28) > the effective daily limit, min(2%, supervisor 1.5%, the absolute).
      The weekly cap is projected the same way (`weekly_cap_headroom`).
    - Tests on each side of each boundary, including £1,000 with one 0.85% loss and a 0.82% entry (refused: 1.67%
      > 1.5%).
    - With the setting false, decisions are identical to today.
    - Exits untouched.
    - Owner: adopt or not. At £1,000 it means at most one full loss a day; the alternative is a worst day of ~£17.
  Depends on: 28.
  Status (2026-10-10): built (TRACEABILITY R34; tests/test_daily_loss_abs.py). After the trading-safety review it is
  per account, `daily_loss_limit_abs: 50` on icm-demo in config/accounts.yaml (its currency, GBP), not a global
  setting; the paper broker standing in for an account drops it. Engine state (`limits.daily_loss_limit_abs`) and
  health show the limit and what is left. Open: the approval card's remaining allowance (ui-ux); 35 and 36.
  Sweep 2026-10-16 (product owner): Done (PR #71, main 423d2b1; 8cb7c66); card allowance in PR #73 (b97f9f9). Acceptance record: merged on green CI into main; checked against git and the named tests exist; the criteria were not re-run one by one in this sweep.
