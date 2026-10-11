- The record now carries what the monthly finance audit needs (2026-10-11, docs/finance/audit-framework.md gaps
  GAP-01/02/03/04/05/10/13; TRACEABILITY X18). `ClosedTrade` gains `decision_price`, `decision_spread_points` and
  `decision_utc` (the signal, from the proposal) next to `requested_price` (the quote the order was priced from: in
  propose mode the quote at the owner's approval), so side x (requested - decision) is the cost of the 90 s wait;
  `timeframe`, `family` and `model_version` (the agent's champion registry version); `initial_sl`, `final_sl`,
  `final_tp`; `money_source` (broker | engine) and `pnl_basis`, "gross" unless commission and swap are both known, so
  a fallback record is never summed as net. The entry facts travel as `EntryAudit` on the pending order and the open
  trade, so a restart or a recovered fill keeps them. Exit legs (engine closes, the scale-out, broker stop and target
  fills against their level) go to a new store table `exit_fills`, kept apart from `fills` so the entry slippage
  table is unchanged; the legs are buffered in memory and written by `_flush_closed` after the tick's exits, so an
  exit never waits on a file write (a failed write keeps them for the next flush). The archived proposal keeps
  `decided_ts` and `decided_channel` ("telegram" | "dashboard", no id), taken by `bus.archive` from the decision file
  for approved or rejected proposals only (a stale one archived on restart takes none; an unreadable file is "no
  decision"). The paper broker's deals now say `swap` None (not modelled), so paper records are gross. The journal
  `order` row now names its proposal (GAP-12). Every new field defaults to unknown (ADR 0010). Not done: the monthly
  report and digest do not yet read `pnl_basis` (PR-1 must exclude "gross" rows from net), no health count of gross
  records, no fx on the record (GAP-06), no broker reconciliation (GAP-08). The existing `decided_by` /
  `decided_via` still keep the Telegram id and dashboard email in approvals/done on the VPS: BACKLOG 52.
  Tests: tests/test_finance_audit_record.py.
