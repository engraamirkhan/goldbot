---
number: 27
rank: 29
title: Broker contract terms recorded from the demo terminal (ADR 0004)
stage: Execution, position sizing · RUN
size: S
status: ready (after 1)
---
- **27. Broker contract terms recorded.** Value: replaces the ADR's unverified leverage, minimum lot, step, contract
  size and stop-out level with the terminal's own values (Execution · RUN). Acceptance:
  - On the demo terminal, `state/broker_terms_<account>.json` records `volume_min`, `volume_step`, contract size,
    account leverage, margin for 0.01 lot at the current price (`order_calc_margin`) and the stop-out level, with a
    timestamp; no login, balance or account number.
  - ADR 0004's assumptions table is updated with the measured values (or a note that they matched).
  - Reviews: code, sre. Read-only on the broker: no trading-safety review.
  Depends on: 1.
