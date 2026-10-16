---
number: 27
rank: 8
title: Broker contract terms recorded from the demo terminal (ADR 0004)
stage: Execution, position sizing · RUN
size: S
status: 'in progress (leverage, margin, stop-out, contract and volume read from the terminal 2026-10-16; swap, commission or Raw Spread, negative-balance protection and the collector file still open) · accountable: sre · due 2026-10-20'
---
- **27. Broker contract terms recorded.** Value: replaces the ADR's unverified leverage, minimum lot, step, contract
  size and stop-out level with the terminal's own values (Execution · RUN). Acceptance:
  - On the demo terminal, `state/broker_terms_<account>.json` records `volume_min`, `volume_step`, contract size,
    account leverage, margin for 0.01 lot at the current price (`order_calc_margin`) and the stop-out level, with a
    timestamp; no login, balance or account number.
  - ADR 0004's assumptions table is updated with the measured values (or a note that they matched).
  - Reviews: code, sre. Read-only on the broker: no trading-safety review.
  Depends on: 1.
  Sweep 2026-10-16 (product owner): accountable `sre`, due 2026-10-20 (two days after 1). Acceptance as above: the file on the demo box and the ADR 0004 assumptions table updated.
  **Read from the demo terminal 2026-10-16 (CFO capital plan; owner session, values only):** account leverage
  1:5000; margin call 100%; stop-out 50%, so `margin_so_mode` 0 (percent); contract size 100 oz; volume min 0.01,
  max 100, step 0.01; margin £0.63 GBP per 0.01 lot at the price of the day. So the 1:5000 applies to gold (no lower
  symbol leverage), and the broker would accept about 15.9 lots on £1,000: that finding opened item 66 (bridge
  ceiling) and owner decision C5 (lower the leverage). These readings do not yet satisfy the first criterion: the
  collector's file on the demo box is still owed.
  **Still open (sre, by 2026-10-20):** swap long and short for XAUUSD (points per lot per night, and the triple-swap
  day); commission per lot and whether the account is Raw Spread (OD-4); negative-balance protection (from the
  broker's terms, cited by URL and date). Each goes into the file or the ADR 0004 table; if the account reads
  Standard, OD-4's re-run of budget and break-even applies before any 15m trade.
