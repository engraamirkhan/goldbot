# 0056: MT5 bridge: closes ahead of reads and orders, and an order lane

Status: proposed (implementer, 2026-10-27; trading-safety, sre and security review owed)

## Context
BACKLOG 135, the trading-safety review of 5d661e94 (MEDIUM, ADR 0052). A terminal connection serves one call at a
time (the MetaTrader5 package is not thread-safe), and up to protocol v6 the bridge also accepted one HTTP request at
a time. While the terminal sent an entry (Vantage: up to 31 s; vantage-demo's order timeout is 35 s), the engine's
closes waited behind it and behind every read the brain had already given up on: the worst engine-side exit delay was
about 45-55 s. Demo accounts rely on the broker-side stop for that window; a real-money account must not.

The backlog item asked for two things that cannot both hold: "still one terminal call at a time" and "a close
reaches the terminal within 5 s while an order holds the terminal 30 s". On one connection the order cannot be
interrupted, so no queue order meets the second. Worse, the engine cannot even see its stop crossed: no tick can be
read on that connection either.

Options considered:
1. Priority queue only (one connection). Closes run next after the order, abandoned reads dropped. Bound: the order's
   hold plus one pass (about 36 s at 35 s). Meets acceptance (1), (2), (5), (6), not (4).
2. A second endpoint for closes (second bridge process, port, tunnel forward, keyring entry and SSH `permitopen`).
   New attack surface and four new deploy artefacts per box, and closes alone do not help: the stop check needs ticks.
3. An order lane: the bridge itself starts a child process with its own terminal connection that sends `place_order`
   only; the bridge's own connection keeps serving ticks, closes, stop changes and the kill switch. No new port,
   unit, tunnel or secret; the brain is unchanged (it still sends entries to the same URL).

## Decision
1 and 3 together (protocol v7):
- The bridge accepts requests concurrently (ThreadingHTTPServer) and runs each connection's terminal calls on one
  worker from a priority queue: `close`/`modify`, then /health, then reads, then `place_order`
  (goldbot/execution/bridge_queue.py). A request whose client closed its socket is never run; a read queued past the
  timeout its client sent (`timeout_s`, new optional request key) is dropped (504); an order older than
  ORDER_MAX_AGE_S when it reaches the front is refused stale (409). Nothing is ever re-sent; a running call is never
  interrupted. At most 64 requests wait (503 beyond).
- The order lane (goldbot/execution/bridge_lane.py) is opt-in per account (`order_lane: true` in accounts.yaml) and
  turned on only after the drill on that box (RUNBOOK "Order lane"). The child runs the same GuardedBroker (magic,
  3 lots, the registered account read on every order, AutoTrading, the notional ceiling, the order's age just before
  the send). It never logs in; a child whose initialize launched a terminal exits 76 and the bridge exits with it. An
  order whose child died or stayed silent past 90 s gets no reply at all, so the brain books it ambiguous and the
  BACKLOG 72 look-up settles it; the lane stays down and entries return to the shared connection. /health reports
  `order_lane` as a fixed token (off, starting, separate, down).
- Protocol v7, MIN_COMPATIBLE stays 5 (same methods, records and arguments; a v6 server ignores `timeout_s`), so either
  box can be deployed first. A live account requires v7 (`bridge_compat.CLOSE_PRIORITY_VERSION`): its entries are
  refused (`bridge_incompatible`) on an older bridge; exits never read the verdict.

## Consequences
- With the lane: an engine-side stop close, the Friday cut and the kill-switch flatten reach the terminal within 5 s
  while an entry holds the lane 35 s, with reads flooding the bridge (tests/test_bridge_close_priority.py).
  Without it: the close runs next after the order, `order_send.exit_delay_bound_s` = the hold plus one pass (was
  the hold plus 2 x 10 s).
- Unverified until the drill: that the MT5 terminal under Wine serves two Python IPC clients at once, including an
  `order_send` on one while the other reads and closes. If it serialises them internally, the lane gives no gain and
  no harm (the bound falls back to option 1's); if the second attach disturbs the first, the drill shows the bridge's
  /health `not_running` and the rollback is `order_lane: false`. This is why the lane is opt-in per account.
- The bridge holds one thread per waiting request (at most 64 queued) instead of one connection at a time.
- What would change this: an MT5 terminal that cannot take a second client (then a second terminal install per account
  would be the next option, at the cost of a second login), or MetaQuotes shipping `order_send_async` in Python.
- For the real-money pilot as planned (one position at a time, owner-confirmed entries) an entry and an open position
  never overlap, so the 5 s bound matters for the kill switch and a second account, not for the pilot's own stop.

## Amendment 1 (trading-safety review of 49335812, approved with conditions; PD decision)
- A live account needs bridge v7 **and** its order lane up (`order_lane: separate` on /health); entries are refused
  (`bridge_incompatible`) otherwise; exits never read it. A lane that is `down` FAILs health (`bridge_compat:<account>`,
  Telegram) on any account. `icm-live` has `order_lane: true` and the bootstrap's `terminal_path`
  (`C:\MT5\ICMarkets\terminal64.exe`). This supersedes docs/deploy/bridge-v7-rollout.md section 8 ("lane stays off
  for the pilot"): the pilot now waits for the lane drill on the live box.
- Cancelled requests no longer count toward MAX_QUEUED, and a close or stop change is never refused by the cap.
- An order the lane never sent (lane down before the write, or the write failed) is a definite reject
  (`order_lane_not_sent`, 10006), not silence, so the brain books no ambiguous order.
- `load_accounts` now reads `order_lane` (the first commit dropped it, so the flag could never turn on).
