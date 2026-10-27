---
number: 132
rank: 52
title: 'Strategy board: families across timeframes, progress to read, anneal state, verdicts vs control'
stage: API · UI · research
size: L
status: 'proposed · accountable: implementer · design: ui-ux-designer · reviews: code-reviewer'
---
- **132. Strategy board: families across timeframes, progress to read, anneal state, verdicts vs control.** Value: the owner thinks in strategies; this shows each family across timeframes, progress to its read, its annealing state and, after the read, its verdict against its control (docs/design/trades-ux-2026-10-27.md §2.3, §3.4). Fields: `GET /api/strategies/board` (family_label, style, variants[timeframe, arm_id, is_control, n_counted, n_read_at, fills_today, fills_7d, eta_read_date, band, anneal_state, open_now] and, after the read only, verdict, net_r_per_trade, ci90, hit_rate, vs_control_r, net_gbp, since_read). Acceptance: quant-reviewer signs the payload before build (ADR 0050); no pre-read aggregate in the payload (seeded leak test); ordering by state and progress, never by result; engine strategies show full figures with n and 90% CI; lab totals only with 3+ arms.
