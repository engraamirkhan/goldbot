# Plan: W2-1 state store — orders, risk state, approvals into core.db

Status: plan, architect (lane A-PLAN), 2026-10-16. Checked against docs/DESIGN.md, docs/TRACEABILITY.md and
`docs/proposals/2026-10-state-store.md`. Due Wed 10-21.

**Scope correction.** The roadmap's "state store step 2" is not the proposal's step 2: that one (backups) is done
(ADR 0002). W2-1 is the proposal's **steps 6 and 7**: the approvals bus and control, then orders, open trades and risk
state. Step 5 (halt inputs) touches `supervisor.py` and stays out of this lane.

**Requirement.** DESIGN Stack: "SQLite for live state". Row X7: a `pending_orders` table written before `order_send`.
Rows A7, A11: first decision wins; the owner halt fails closed. Proposal §2: `core.db` with `synchronous=FULL`. ADR 0002
already backs up every `state/*.db`, so `backup.py` does not change.

## Decisions

1. **What fsync buys.** Files already fsync (`base.py:196-231`). The gain is transactional: the order row and the open
   trades commit together, and the primary key enforces the first decision.
2. **JSON during the change.** JSON is written as an export after the commit for one release. Seven readers keep
   reading the files unchanged (`ledger.py:331`, `jobs.py:1077`, `checks/engine.py:240`, `checks/risk.py:101,138`,
   `perf_report.py:404,466`, `goldbot-deploy.sh:562`), so a rollback stays safe. The exports retire in step 8.
3. **Corrupt `core.db`.** Keeps today's refusal to start; broker-side stops protect open trades. The proposal's
   "exits-only mode" is a separate design item.

## Files

| File | Change |
|---|---|
| `db/migrations.py:218-231` | core v3 `orders`, `open_trades`, `closing`; v4 `risk_state`; v5 `proposals`, `decisions`, `control`, `control_log` — all additive |
| new `db/core_state.py` | `OrderStore`, `RiskStore` (after `hwm.HwmStore`, `hwm.py:85`); `OrderPersistError(StateDbError)`; `RiskStateRecord` |
| `db/import_state.py:85` | importers per account for these files, approvals and control |
| `engine/runner.py` | `_orders_record` 2328, `_save_orders` 2337, `_load_orders` 2348, `_execute` 1317, `_save_risk_state` 2763, `_save_risk_state_now` 2776, `_load_risk_state` 2789; store opened near 418 |
| `telegram/bus.py:85-254` | same public API, backed by `core.db` |

Must not change: the public `ApprovalBus` API and its 8 callers, `backup.py`, `supervisor.py`, the readers above,
`aux.db`.

**Interfaces.** `OrderStore.commit_sending(order, open, closing, lost) -> None` raises `OrderPersistError` (busy timeout
2000 ms); `OrderStore.load() -> OrdersSnapshot | None`; `RiskStore.save(acc, RiskStateRecord)`, `RiskStore.load(acc)`.
Timestamps go in as `ts_ns` via `epoch_ns`.

## Data flow

1. Entry: `BEGIN IMMEDIATE` → order row `sending` plus open trades → `COMMIT` (FULL).
2. Then the JSON export (never raises), then `place_order`, then the status commit.
3. A failed commit sends nothing, journals `persist_failed`, and blocks entries until a write succeeds.
4. Exits: the broker close first; its database write is retried afterwards. Exits are never gated.
5. On start, a missing database row reads the JSON, upserts it and counts it in `state_fallback`.

## Failing tests first

`tests/test_core_state.py`: `test_v3_to_v5_are_additive_and_older_code_opens_the_file`,
`test_the_order_row_commits_before_place_order`, `test_busy_core_db_sends_nothing_and_blocks_entries`,
`test_entries_resume_after_the_next_good_write`, `test_a_close_reaches_the_broker_when_core_db_is_unwritable`,
`test_sent_ids_and_open_trades_survive_a_restart`, `test_sending_rows_never_pruned_filled_pruned_at_7_days_not_before`,
`test_dual_read_imports_once_and_counts_fallback`, `test_export_failure_never_raises`,
`test_corrupt_core_db_refuses_the_start`, `test_risk_state_round_trips_every_field`,
`test_failed_risk_write_never_narrows_the_kill_switch` (regression for trading-safety F1; the risk state now carries
`kill_everything`, 6cf86e3).

`tests/test_approval_bus.py`: `test_eight_processes_one_decision_wins`,
`test_decision_at_exactly_expires_ns_is_refused`, `test_control_reads_halted_on_any_db_error_or_unknown_field`,
`test_decisions_keep_only_the_channel`. Also `tests/test_import_state.py::test_core_imports_are_idempotent`.

## Risks

| Risk | Test |
|---|---|
| Lock contention stalls the engine | busy-timeout test; no compute inside a transaction |
| An in-flight approval is lost at cutover | cut over at the weekend close; idempotent import |
| Rollback | the additive test plus the JSON exports |
| `runner.py` conflicts with Wave 3 | merge in sequence |

**Reviews.** Trading-safety: the order path, halts, first decision wins. Security: file mode 0600, no identities in
the tables (A15). SRE: cutover, migrate, WAL checkpoint, rollback, RUNBOOK.

**Docs.** TRACEABILITY rows X7, A7, A11, S4 plus a state-store row; the proposal's status line; RUNBOOK cutover
(`python -m goldbot.db migrate`, then `import-state`); HANDOFF.

**Not in scope.** Step 5 (halt inputs), step 8 (removing the JSON), moving the file readers to the database,
exits-only mode, `aux.db`, `phase_state.json`, drill checks in `backup.py` (SRE follow-up).

**Sequencing.** Run before Wave 3, which shares `runner.py`. The approval-bus work must rebase on the BACKLOG 60 fixes
(F60-1/F60-2, in flight 2026-10-16), which change `bus.py` and `_archive_stale_proposals`.
