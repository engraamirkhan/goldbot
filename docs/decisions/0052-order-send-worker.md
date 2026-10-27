# 0052: Entry orders off the tick thread; an unanswered send is ambiguous, not a tick error

Status: proposed (implementer, 2026-10-27)

## Context
On vantage-demo, `bridge place_order` timed out at the engine's 15 s read timeout while the broker was still filling
(SRE: Vantage fills 2.6-5.3 s p95, up to 31 s). The fill was adopted, but the timeout raised as a tick error, which
refused every entry until a whole clean decision bar (15 min), and the synchronous send stalled the tick loop for up to
15 s. With the Vantage scalp arms this repeats several times a day.

## Decision
- An order call with no answer (`ConnectionError`, `TimeoutError`; the bridge's `BridgeUnavailable` is one) returns an
  unanswered result; the row is `ambiguous` and the reviewed BACKLOG 72 look-up settles it. An error the bridge or
  terminal answered with keeps today's path (raise, row `sending`).
- Through the bridge, entries are sent by `order_send.Sender` (one daemon thread per order, one order in flight per
  account, its own `RemoteBroker` with the account's `order_timeout_s`). The tick thread waits at most `WAIT_S` = 5 s:
  a reply in time is handled exactly as before, so multi-arm bar closes keep their sequential, exposure-aware entries.
- Declined from the brief: "block only that arm" while an order is unresolved. Condition 1 of BACKLOG 72 refuses every
  entry while any order's exposure is unknown, and the MT5 box serves one call at a time, so a second order could not
  reach the terminal before the first anyway (and would be refused stale after `ORDER_MAX_AGE_S`). With the tick error
  gone, the account-wide wait lasts until the look-up (at the next pass after the late reply), seconds rather than a
  decision bar. Per-arm blocking would need the gate to count in-flight orders as filled at their sent size; revisit
  if the MT5 box ever serves calls concurrently.

## Consequences
- No 15-minute entry block after a slow fill; the tick loop is held at most 5 s (was 15 s) by an order.
- The engine's own calls still queue behind an order the terminal is sending (single-threaded bridge,
  MetaTrader5 not thread-safe): engine-side closes do NOT keep per-tick cadence; they land after that order, at worst
  `order_send.exit_delay_bound_s` (order hold + one 2 x 10 s tick-read cycle, about 45-55 s at 35 s; not a
  regression). Broker-side stops cover the window; BACKLOG 135 (close-priority queue on the bridge) is owed before any
  real-money account. A tick read failing behind our own order in flight is not a tick error.
- `OrderInFlight` (the backstop) is journalled `entry_refused:order_in_flight` on the auto, cost-probe and approval
  paths (the approval card is archived "not sent"), never a tick error.
- `order_timeout_s` is bounded at 60 s, below `AMBIGUOUS_SETTLE_S` (120 s), so the look-up can never call an order not
  placed while its call can still answer.
