# Plan: W2-1 state store — orders, risk state, approvals into core.db

Status: plan rev 2, architect (A-PLAN), 2026-10-16. Trading-safety pre-build review of 9f04599: CHANGES; F1-F10
are build conditions, answered below. Scope: the proposal's **steps 6 and 7** (the roadmap's "step 2"; the proposal's
step 2, backups, is done, ADR 0002). Step 5 (halt inputs, `supervisor.py`) is out of scope.

**Requirement.** DESIGN Stack "SQLite for live state"; X7 `pending_orders` before `order_send`; A7 first decision
wins; A11 owner halt fails closed; proposal §2 `core.db` `synchronous=FULL`.

## Decisions
1. The gain is transactional (files already fsync, `base.py:196-231`): order row and open trades in one commit; the
   primary key decides first-wins.
2. JSON is exported after each commit for one release. Readers left on files: `ledger.py:331`, `jobs.py:1077`,
   `checks/engine.py:240`, `checks/risk.py:101,138`, `perf_report.py:404,466`, `digest.py:310`. Readers moved onto
   the DB (F1): `runner.py:494-536`, `outbox.py:149`, `automode.py:177`, `goldbot-deploy.sh:562`,
   `scripts/e2e_server.py:148`.
3. A corrupt or unreadable `core.db` refuses the start (as today); broker-side stops protect open trades.

## Files (F10: refreshed anchors)
Amended 2026-10-17 (cto, ADR 0023): W2-1 builds on TD-2 step 1. The persistence code is in `engine/store.py` behind
`EngineStore`, and `RiskStateRecord` is replaced by step 1's `RiskSnapshot`.

| File | Change |
|---|---|
| `db/migrations.py:218-231` | core v3 `orders`, `open_trades`, `closing`, `state_meta`; v4 `risk_state`; v5 `proposals`, `decisions` (`card_created NOT NULL`), `control`, `control_log`; all additive; every row has `written_ns` |
| new `db/core_state.py` | `OrderStore`, `RiskStore`, `OrderPersistError(StateDbError)`, `export_state(state_dir)`; reuses `RiskSnapshot` / `OrdersSnapshot` from `engine/store.py` (no `RiskStateRecord`, ADR 0023) |
| `engine/store.py` (from TD-2 step 1, done 2026-10-17: `EngineStore`, `JsonEngineStore`, `RiskSnapshot`, `OrdersSnapshot`, `OpenTrade` / `PendingClose` / `SentOrder`, path helpers `orders_file` / `risk_file`) | `CoreDbEngineStore` behind `EngineStore`; additive protocol extensions only (e.g. entry-path `commit_sending` for F4/F6 timeouts, `written_ns` stamping for F3), each also implemented by `JsonEngineStore` (ADR 0023 S6) |
| `db/__main__.py` | `export-state` command |
| `db/import_state.py:85` | importers `once=True, backup=True`, insert-or-do-nothing |
| `engine/runner.py` | `_execute`, `_orders_record` (F7 `state_unwritable`; stays in the runner), `_save_risk_state_now`, `_archive_stale_proposals`, `_retired_proposal`, `_reset_owner_mode` (F5); the store is chosen in `__init__` (`self.state_store`, runner.py:322 at step 1; `self.store` is the market-data `Store`). The save/load paths of `_save_orders`, `_load_orders`, `_save_risk_state`, `_load_risk_state` are in `engine/store.py` after TD-2 step 1 and are not edited here. Anchors re-read at the build base |
| `telegram/bus.py:92-286` | DB-backed; existing API kept (incl. `discard_decision`, `notify_owner`) plus `retired`, `open_for_account`, `has_decision`, `is_open` |
| `telegram/outbox.py:149`, `automode.py:177`, `scripts/e2e_server.py:148` | onto the new bus methods |
| `risk/gate.py` | reason `state_unwritable` |
| `api/entry_status.py` | show `state_unwritable` |
| `ops/checks/` | `state_export_lag`, `core_db_corrupt` |
| `ops/linux/goldbot-deploy.sh:562` | bus check via the DB; `--rollback` runs `python -m goldbot.db export-state` first and refuses if it fails |

Must not change: `backup.py`, `supervisor.py`, `aux.db`, the file readers in decision 2.

## Interfaces and rules
- **F1 bus.** `retired(pid) -> str | None`: a DB error means retired. `open_for_account(acc) -> list[Proposal]`,
  `has_decision(pid) -> bool`, `is_open(pid) -> bool`. `publish` is insert-or-do-nothing: an existing id journals
  `proposal_id_reused` and is not proposed. New decision rows need `card_created`; `poll_bus` keeps the comparison
  at `approvals.py:243`. `_restart_archived` stays. BACKLOG 60 is merged (9113ce6): the build starts from it.
- **F2 loads.** `RiskStore.load(acc)` and `OrderStore.load(acc)` raise `StateDbError` on any DB error, so the start
  is refused. They return None only when a `state_meta(account_id, written_ns, generation)` row confirms the state is
  absent. HwmStore's "None on any error" is not copied.
- **F3 stamps.** `written_ns` (via `epoch_ns`) goes in every row and its export. At start the newer of the DB and the
  export wins. On a tie the stricter value wins: HALTED, `kill_everything`, owner halt, propose.
- **F4 exits.** Exit-path writes only mark the state dirty. One never-raising write at the end of the tick, busy
  timeout ≤ 500 ms. 2000 ms applies only to `commit_sending`.
- **F5 control.** `control()` returns an `unreadable` flag on a read error; entries read it as halted.
  `_reset_owner_mode` clears its pending flag only after a successful read or write.
- **F6 submit.** A plain INSERT inside `BEGIN IMMEDIATE`, with the guard (pending, outcome NULL, not expired) in the
  same transaction. Decision reads are strict (`tolerant=False`, ADR 0010) under the source `approvals/decisions:`.
  `archive` is one transaction. `discard_decision` deletes only the refused card's row.
- **F7 entries.** `persist_failed` sets `state_unwritable`. It is checked at propose, at the approval re-check and in
  auto mode. It clears only when an `OrderStore` snapshot commits, tried every tick while set. `sent_ids.add` stays
  before the commit. A failed status commit after a send never drops `self.open`.

## Data flow
1. Entry: `sent_ids.add` → `BEGIN IMMEDIATE` → `sending` row + open trades + `written_ns` → `COMMIT` (FULL, 2000 ms)
   → export (never raises) → `place_order` → status commit; failure keeps `self.open`, sets dirty.
2. Commit failure: nothing sent, `persist_failed` journalled, `state_unwritable` set.
3. Exits: broker first; state marked dirty; end-of-tick write (≤ 500 ms, never raises).
4. Start: load (F2), compare stamps with the export (F3), then X7 reconciliation as today.

## Cutover (F8, RUNBOOK; first weekend close Fri 23 Oct)
Stop every writer; `import-state` refuses while any goldbot service is up. Pending proposals and decisions are archived
as withdrawn with an owner notice, not imported. `done/` and `control.json` are imported exactly (unreadable → halted
and propose). Every open-trade field is kept. All services restart together. **F9:** a health alert on a corrupt
`core.db`; a restore applies F3's newest-stamp rule.

## Failing tests first
`tests/test_core_state.py`: `test_v3_to_v5_are_additive_and_older_code_opens_the_file`,
`test_the_order_row_commits_before_place_order`, `test_busy_core_db_sends_nothing_and_sets_state_unwritable`,
`test_state_unwritable_blocks_propose_approval_and_auto_until_a_snapshot_commits`,
`test_a_failed_status_commit_after_send_keeps_the_open_trade`, `test_a_close_reaches_the_broker_when_core_db_is_unwritable`,
`test_locked_core_db_never_delays_or_skips_retry_closes_or_the_kill_switch`,
`test_sent_ids_and_open_trades_survive_a_restart`, `test_sending_rows_never_pruned_filled_pruned_at_7_days_not_before`,
`test_unreadable_risk_row_refuses_the_start_never_first_start`, `test_unreadable_orders_row_refuses_the_start`,
`test_absent_state_needs_the_meta_row`, `test_roll_forward_after_rollback_keeps_the_newer_halt`,
`test_a_stamp_tie_takes_the_stricter_value`, `test_reimport_never_overwrites_newer_db_state`,
`test_export_failure_never_raises_and_shows_state_export_lag`, `test_corrupt_core_db_refuses_the_start_and_alerts`,
`test_risk_state_round_trips_every_field`, `test_failed_risk_write_never_narrows_the_kill_switch`,
`test_kill_switch_mode_reset_survives_a_failed_control_read`, `test_halted_restart_retries_the_mode_reset_from_core_db`.

`tests/test_approval_bus.py`: `test_eight_processes_one_decision_wins`, `test_decision_at_exactly_expires_ns_is_refused`,
`test_control_read_error_is_unreadable_and_halts_entries`, `test_decisions_are_strict_and_keep_only_the_channel`,
`test_a_reused_proposal_id_is_journalled_not_proposed`, `test_a_decision_without_card_created_is_refused`,
`test_retired_on_db_error_is_retired`, `test_discard_decision_deletes_only_that_card`, `test_archive_is_atomic`.
`tests/test_import_state.py`: `test_core_imports_insert_or_do_nothing_once`, `test_import_refuses_while_a_service_is_up`,
`test_pending_proposals_are_withdrawn_not_imported`. `tests/test_deploy_rollback.py::test_rollback_refused_when_export_fails`.

## Risks
| Risk | Test |
|---|---|
| Lock stalls an exit | ≤ 500 ms end-of-tick write test |
| Rollback then roll-forward loses a halt | newest-stamp and tie tests |
| A reused id re-decided | reuse and `card_created` tests |
| `runner.py` conflicts with Wave 3 | merge in sequence |

**Reviews.** Trading-safety F1-F10 re-check; security (0600, A15 channel only); SRE (cutover, rollback, health).
**Docs.** TRACEABILITY X7, A7, A11, S4 + state-store row; proposal status; RUNBOOK cutover; HANDOFF.
**Not in scope.** Step 5, step 8, moving the decision-2 file readers, exits-only mode, `aux.db`, `phase_state.json`.

**Carried build condition C1 (trading-safety re-check, LOW).** The roll-forward deploy after a rollback runs a merge step
before any service starts, for the state the API and Telegram own: `control` takes the newest stamp, and a tie keeps
halted and `propose`; `done/` ids are inserted into `proposals` (insert-or-do-nothing). Test:
`test_roll_forward_after_rollback_keeps_the_newer_owner_halt_and_retired_ids`.

**Review record.** trading-safety-reviewer 2026-10-16: CHANGES @ 9f04599 (F1-F3 HIGH, F4-F7 MEDIUM, F8-F10 LOW); APPROVE
on re-check @ bde611b, with C1 carried. The built change needs a trading-safety review against F1-F10 and C1 before
merge, with every named test passing.
