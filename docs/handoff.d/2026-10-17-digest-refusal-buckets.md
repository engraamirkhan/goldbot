- Digest and approval report count refusals by cause (BACKLOG 83, implementer, 2026-10-17). Before, every approved
  card with a `gate_refusal` was "refused by the risk check", including item 71's MT5-box and broker refusals and item
  72's `not_confirmed` cards.
  - `not_placed.approval_cause(codes, outcome)` sorts an approved card into `risk_check`, `not_placed` (the
    `broker_refused:*`, `bridge_stale_order` and ceiling codes, and a not-confirmed card the broker showed no trade
    for), `not_confirmed` (pending, still unknown or an unknown outcome) or `opened` (opened, opened and closed, opened
    after all: an opened trade, in no count). `CAUSE_PHRASE` holds the three labels next to item 71's `BY_PHRASE`.
  - Digest: "✅ N approved (1 refused by the risk check · 4 not placed by the MT5 box or the broker · 2 not confirmed by
    the broker)", naming only causes that occurred. The approved count is unchanged.
  - Performance report: `ApprovalReport.gate_refused_after_approval` now counts the RiskGate's refusals only; new
    `not_placed_after_approval` and `not_confirmed_after_approval`. The markdown line adds "; not placed by the MT5 box
    or the broker: N" / "; not confirmed by the broker: N" only when non-zero.
  - Frozen outputs: a day with only RiskGate refusals renders byte-identically in both (the existing digest assertion
    and a new report test pin it); the only change is the new bucket text when those causes occur.
