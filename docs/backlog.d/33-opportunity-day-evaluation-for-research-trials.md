---
number: 33
title: Opportunity-day evaluation for research trials (£/day distribution, days captured)
stage: Performance review · FIND
size: S–M
status: 'parked: needs 20 and 26 results, which need trades'
---
- **33. Opportunity-day evaluation for research trials (quant review; read-only).** Value: the north-star KPIs of
  item 26 applied to research trials and the shadow book, so a hypothesis is judged the way the owner states the
  goal, without making it a target (FIND). Acceptance:
  - For any research trial's trade list and the monthly shadow book, every trade is re-sized as a configurable
    account (default £1,000 GBP: 0.01 lot or refused under the gate's rules, account currency from 28).
  - The report shows:
    - the £/day distribution: P(≥ £50), P(≥ £20), median, mean, p5, worst day, days traded;
    - opportunity days captured, with item 26's definition (range ≥ 1.5 × ATR(1d), ≥ 1 R made) plus the stricter
      variant from `opportunity-days.md` section 3.4. Misses are split by stand-aside, gate reason and wrong side;
    - stand-aside accuracy;
    - drawdown statistics from item 20;
    - net R by score band.
  - It reuses item 30's descriptive base-rate report; it does not recompute it.
  - Golden test on a fixture. Not in the Telegram digest. The header says "diagnostic, not a target".
  - Depends on: 20, 26.
  Sweep 2026-10-16 (product owner): Parked: needs 20 and 26 results, which need trades.
