# 0021: Operational orders pass the RiskGate's account limits, not its model checks

Status: accepted 2026-10-16. Proposed by the architect 2026-10-16 (W1-1 bridge smoke, BACKLOG 1). Trading-safety
review 2026-10-16 at `d1d86d6`: ADR APPROVE with fences F1-F6, all required; plan APPROVE with the G1a-c text fix
(`combined_size_down`, `in_blackout`, dq/tick flags), applied in `d4ce33d` (review record:
`docs/proposals/2026-10-w1-1-bridge-smoke-plan.md`, "Review record").

## Context
BACKLOG 1 needs one real 0.01-lot round trip on the demo terminal through the bridge (`run.py bridge-smoke`). The
design says the RiskGate is the only path to an order and the owner confirms every entry. `RiskGate.check`
(`goldbot/risk/gate.py:391`) also runs model checks that need a win probability, a target and a cost. A smoke order
has none, so passing `check` would mean inventing them, which launders the gate.

## Decision
Add `RiskGate.check_operational(intent: OperationalIntent, st: AccountState, now_utc, margin_required) ->
GateDecision`. It shares one code path with `check` for every account and limit check. It skips only the model
checks, named here: `time_exit_ev_unsupported`, `negative_ev`, `target_below_cost_floor`, `standard_account_edge`,
`standard_account_15m` (S6). It keeps `account_class_unknown`. It takes no p, target or cost.

Fences (all required):
- **F1** Refuses `operational_not_demo` unless `account_mode == "demo"` and `paper_to_tiny_live` is not in
  `phase_state.json`'s `gates_passed`. An unreadable phase file refuses.
- **F2** No lots parameter. Size is exactly `volume_min` rounded down to `volume_step`. Every sizing limit still
  applies: per-trade risk cap, margin floor, combined exposure, daily and weekly caps, absolute daily loss limit, and
  heat.
- **F3** An AST test: only `goldbot/ops/smoke.py` calls `check_operational`, and `goldbot/engine`,
  `goldbot/allocator`, `goldbot/api` and `goldbot/telegram` never import `goldbot.ops.smoke`.
- **F4** `GuardedBroker.place_order` refuses magic `magic_base + 99` unless the terminal's account reports
  `trade_mode == "demo"`. This is internal only: `METHODS`, signatures, `API_VERSION` and `protocol_digest` are
  unchanged.
- **F5** The split of `check` into shared helpers is a pure refactor. A decision-grid test shows `check` returns
  identical decisions before and after.
- **F6** The smoke entry goes through `ApprovalBus`: one owner click within 90 s. Otherwise it is
  `EXPIRED_UNAPPROVED`, the command exits 3, and nothing is sent. The gate re-runs at approval time. No exception.

Boundaries match `check`: the daily cap refuses at equality, and the absolute daily-loss projection allows equality
(S10). Exits never call either check.

## Consequences
- An operational order is bounded by every account limit and needs the owner's click, as any entry does. It can never
  run on a live account or after the live gate, and only the smoke command can reach it.
- The smoke's order does not use the engine's `pending_orders` table (X7 departure). It uses a deterministic comment
  and a 24 h look-up instead (plan S9, docs/proposals/2026-10-w1-1-bridge-smoke-plan.md).
- `gate.py` changes shape. Any later change to `check` must keep the grid test green.
- What would change this: a second operational use, which would need its own ADR, or a design rule that tests run
  only on paper.

## Acceptance
Accepted as written, fences F1-F6 included. Rationale:
- It is the only way to run BACKLOG 1's real round trip without inventing a p, target or cost for `check`, which
  would launder the gate. The operational path keeps every account and limit check and the owner's click (F6), so
  no safety rail is weakened: RiskGate stays the only path to an order, the entry is owner-confirmed, it is
  demo-only (F1), and exits call neither check.
- The trading-safety reviewer approved it with the fences at `d1d86d6`; the plan's G1a-c finding is fixed in
  `d4ce33d`. At the build, code review re-checks F1-F6 and the F5 decision-grid test, and the trading-safety reviewer
  verifies the fences on the code before merge.

Decided 2026-10-16 by PD + PO + CTO (owner delegated).

**Reverse if:** the F5 grid test shows `check` changed behaviour, a fence cannot be built as written (the lane stops
and returns to the architect), or a second operational use appears (its own ADR, as above).
