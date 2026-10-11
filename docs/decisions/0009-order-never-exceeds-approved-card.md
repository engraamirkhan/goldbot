# 0009: An approved order never exceeds the card the owner approved

Status: accepted (trading-safety review of b97f9f9, 2026-10-11)

## Context
The owner confirms each entry on a card that shows its size and its risk in the account currency ("0.12 lots, risk
£8.20"). On approval the engine re-runs the RiskGate with the current equity, price and broker conversion
(`Engine._on_decision`) and, until now, sent whatever size that re-check produced. The re-check is right to refuse
(a halt, a blackout or a spent loss limit during the 90 s window), but it could also **grow** the order: if equity
rose during the window (an open trade's profit, a deposit) the gate sized up, and with the minimum lot fixed, a rise in
the broker's USD-to-account conversion raised the risk at the same lots. The owner would then be committed to more
than they approved. A test reproduced it: equity doubled during the window and 0.25 lots were sent for a 0.12-lot card.

## Decision
- The order sent is `min(re-check lots, card lots)`. The re-check can only reduce the size; a smaller re-check size is
  sent as the gate decided it. A cap is journalled (`approval_size_capped`, with the gate's lots and the lots sent).
- If the risk at the lots sent (`GateDecision.risk_amount` scaled to those lots) exceeds the card's `risk_amount` by
  more than 2% (`APPROVED_RISK_TOLERANCE` = 1.02 in `goldbot/engine/runner.py`), the approval is refused with
  `risk_above_approved`, the same way as any refusal at approval: journalled as `gate_at_approval:...`, archived with
  `gate_refusal`, shown as "Refused at approval" on the dashboard and Telegram. Nothing is sent.
- 2% absorbs the ordinary move of a currency pair within 90 s (well under 0.5% at normal volatility) and the cent
  rounding of the card. A card from an older engine without `risk_amount` is capped at its lots only.
- This only ever removes or reduces an entry. Exits are untouched and never gated.

## Consequences
- What the owner approves is the upper bound of what is placed, in size and (within 2%) in money.
- When equity rises sharply in the window the trade is slightly smaller than the gate would now allow: accepted; the
  next proposal sizes on the new equity.
- A conversion jump of more than 2% inside the window costs one trade (refused, never resized up). If it happens often
  on a non-USD account, the evidence would be `risk_above_approved` refusals in the journal; then widen the tolerance
  with a recorded reason, never drop the rule.
- Auto mode is unaffected: there the gate's decision at proposal time is executed directly, with no card to exceed.
