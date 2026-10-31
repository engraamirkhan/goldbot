---
number: 141
rank: 50
title: Record Vantage exit legs (exit_fills) so its cost of record measures exit slippage and overshoot
stage: 'Data (execution ledger) · cost of record'
size: S
status: 'proposed, severity MEDIUM · accountable: data-engineer then implementer · reviews: quant-reviewer, trading-safety-reviewer'
---
- **141. Record Vantage exit legs (exit_fills) so its cost of record measures exit slippage and overshoot.** Value: the Vantage demo host wrote 0 `exit_fills` rows for its exploration exits in the week of 2026-10-26 (ICM wrote 552). Without them, Vantage's exit slippage comes only from its scheduled probes. Its stop exits also read exactly at the stop price, so the overshoot is untrusted. Under ADR 0064 every new Vantage pre-registration is therefore charged the 0.70 stress on each stop exit, and the probe sleeve cannot shrink (docs/research/cost-of-record-2026-10-31.md sections 5 and 6). Acceptance:
  - Every exploration market exit and stop-out on the Vantage host writes an `exit_fills` row: requested, filled, side, kind, client_order_id.
  - The stop exit records the broker's deal price, not the order's SL level.
  - A test runs a fake bridge close through the exit path on a second broker and asserts the row.
  - After one week of rows, `python -m goldbot.research.cost_of_record` shows `exit_slip_source: strategy_exit_legs` for Vantage. Its overshoot is then trusted, or the deal-price evidence shows it is a demo artefact.
