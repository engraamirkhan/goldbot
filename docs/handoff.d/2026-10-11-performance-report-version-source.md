- **Performance report: the model version comes from the closed record (2026-10-11; quant re-verify of a4e532f).**
  The record lane writes `model_version` on the closed-trade record (EntryAudit), linked by client order id =
  proposal id, not on the proposal.
  - An approved proposal that became a trade is matched to the shadow version named on its record.
  - Rejected and expired proposals, and approved ones without a record or a version, match the nearest p. The table
    labels them "version inferred (nearest p)", with a count per outcome.
  - The report now reads the typed `ClosedTrade` fields. The raw-JSON reader (`load_closed_trade_rows`) is gone, so
    `gates_phase.load_closed_trades` is unchanged from main.
  - Costs split the record's entry price gap into execution slippage, side x (fill - `requested_price`), and
    approval drift, side x (`requested_price` - `decision_price`), the cost of the owner's wait.
  - Tests: `tests/test_perf_report.py` (38). The three new tests failed against the previous code.
