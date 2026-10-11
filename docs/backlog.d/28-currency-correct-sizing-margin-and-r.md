---
number: 28
rank: 1
title: Currency-correct sizing, margin and R on a non-USD account (ADR 0004, audit A3–A8)
stage: Position sizing, safety · RUN
size: S–M
status: '**in review** (built 2026-10-10; trading-safety review; approval-card currency label open for ui-ux)'
order: 24.5
---
- **28. Currency-correct sizing (HIGH, top priority; trading-safety review).** Value: today every trade on the GBP
  account is sized at ~0.75× intended and the computed realised risk is 1.33× the true figure, so the 15m min lot
  (true 0.82%) is refused at £1,000 even with 25; closed-trade R is understated 1.33× (ADR 0004 section 1, A3–A8:
  `goldbot/risk/gate.py:249-268`, `goldbot/engine/runner.py:576-577`, `:1591-1602`). Acceptance:
  - Stop risk per lot in the account currency comes from MT5 `order_calc_profit` (side, symbol, 1 lot, entry,
    stop), falling back to `trade_tick_value / trade_tick_size × stop`; both exposed through the broker protocol
    and the bridge (read-only, retry-safe). The paper broker answers in its own currency.
  - RiskGate sizes `lots_raw = equity × risk × m / risk_per_lot_acct`; realised risk, the 1:20 margin figure, the
    margin level and the combined-notional cap all compare like with like in the account currency. Tests on a
    GBP account at GBPUSD 1.33: £1,000, 15m stop $10.90 → realised 0.82%; a USD account gives decisions identical
    to today on a grid of equities and stops.
  - Fail closed: on a non-USD account with no conversion (None, error, non-finite, ≤ 0), entries are refused with
    `fx_conversion_unavailable`; exits are untouched (test: the exit path never calls the gate).
  - `Intent` gets `contract_oz`, `volume_min`, `volume_step`, `volume_max` and `stops_level_points` from
    `symbol_info` (A6), with a test that a 10-oz contract changes the lots.
  - Closed-trade `r` and `ret` use risk and notional in the account currency (A5); a test on a GBP fixture.
  - The supervisor and `other_equity` convert each engine's equity to one reporting currency or refuse to combine
    mixed currencies, failing closed (A8).
  - DESIGN Sizing paragraph, TRACEABILITY row and the approval card's risk line (currency label) in the same
    change. Reviews: trading-safety, code, ui-ux (card label).
  Depends on: nothing. 25's tests are restated in account currency on top of it.
  Status (2026-10-10): built (TRACEABILITY R32, S6; tests/test_currency_sizing.py). The supervisor refuses mixed
  currencies (halt) rather than converting. Open: the approval card still shows `risk_usd` (ui-ux label change).
