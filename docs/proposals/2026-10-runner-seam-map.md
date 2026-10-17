# Seam map: splitting goldbot/engine/runner.py (TD-2 / ADR 0023, option C)

Status: architect, 2026-10-17, at ab671be (runner.py = 3,492 lines). For CTO sign-off. **Decided 2026-10-17: ADR 0023 accepts it with conditions; the step order and line-cap target there supersede this map.** Requirements: DESIGN Stack
"SQLite for live state", TRACEABILITY X7, A11, S4.

## Step 1 — `engine/store.py` (move-only, due Fri 10-30, after BACKLOG 62 merges)

**Moves:** `OpenTrade`, `PendingClose`, `SentOrder` (258-323; runner re-exports them); from `_orders_path` /
`_save_orders` / `_load_orders` (2465-2525) the path, JSON dump, `write_atomic`, `json.loads`, `load_record` and the
`note_schema_skew` call; from `_risk_path` / `_save_risk_state` / `_load_risk_state` (2900-2953) the 12-field payload and
parse, word for word; the `engine_<acc>.json` write in `_write_state` (3488).

**Interface:**
```
class EngineStore(Protocol):
    def load_orders(self) -> OrdersSnapshot | None   # None = confirmed absent; unreadable raises
    def save_orders(self, s: OrdersSnapshot) -> None
    def load_risk(self) -> RiskSnapshot | None
    def save_risk(self, s: RiskSnapshot) -> None
    def write_engine_state(self, payload: dict[str, Any]) -> None   # durable=False
class JsonEngineStore: __init__(self, state_dir: Path, account_id: str)
```
`RiskSnapshot` / `OrdersSnapshot` are FrozenRecords built after the existing parse (same error types). W2-1 adds
`CoreDbEngineStore` behind this protocol and reuses `RiskSnapshot`; "None only when state_meta confirms absent" (W2-1 F2)
is already the protocol contract.

**Stays in runner.py:** the state attributes; all eight method names as thin delegates (tests call them 52 times);
`_orders_record` (it changes `sent_ids`, the X7 ordering — the one deviation from the brief); `_save_risk_state_now`
(never-raise and `note_tick_error` are engine rules); `_flush_closed` and the 7-day prune; reconciliation,
`excursion_approx`, `write_startup_error`; the `kill_everything=False` reset and first-start `rearm_seen`; `_kill_pending`
and `_mode_reset_due` (492, 495); how `_write_state` builds its payload. The store is built in `__init__` before line
437; the constructor signature is unchanged. `tests/test_equity_confirmation.py:348` patches `runner.write_atomic` →
becomes `store.write_atomic` (a one-line import-site edit).

**Proving move-only:**
1. **PR 0 (tests only, on today's code):** `tests/test_engine_store_golden.py::test_save_load_round_trip_matches_golden_grid`
   — 4 stages × kill_everything × stage-2 pending × halted_at × propose_only × 3 order-row sets (192 cases), canonical
   JSON goldens for orders_/risk_/engine_ (ts removed) and the post-restart attribute dump; the move PR must reproduce
   them unchanged. Edge tests: older risk file without kill_everything; corrupt files refuse the start; absent files
   take the first-start rearm; newer keys note schema skew; sending rows never pruned, filled pruned after 7 days;
   saved kill_everything restarts pending and owes the mode reset; a full disk during the flatten never narrows it.
2. Review with `git diff --color-moved=dimmed-zebra`, plus full gates.

**Ratchets (`tests/test_engine_structure.py`):** `test_runner_py_stays_under_its_line_cap` (cap lowered each step,
target ≤1,200); `test_runner_does_no_state_file_io` (AST: no `write_atomic` / `json.loads` / `read_text`).

## Steps 2-7 (one move-only PR each)

| # | Module | Code that moves (current lines) | Depends on |
|---|---|---|---|
| 2 | `engine/account_guard.py` | `_refresh_account` … `_reset_owner_mode` (1963-2261); `_roll_risk_period` … `_rearm_conditions` (2407-2463) | BACKLOG 62, W2-1 (F5) |
| 3 | `engine/exits.py` | `_read_positions` … `_blackout_rescore` (1476-1818) | W2-1 (F4) |
| 4 | `engine/reconcile.py` | `_own` … `_lot_terms` (1819-1937); `_resolve_sent` … `_closed_excursion` (2526-2899) | step 1, W2-1 |
| 5 | `engine/clock.py` | `_host_now` … `_host_clock_exits` (711-784); `_server_clock` … `_check_new_bars` (2262-2406) | none |
| 6 | `engine/sizing.py` | `_account_currency` … `_published_limits` (980-1142); `_refresh_broker_terms` … `_throttle_state` (3251-3421) | TD-16 |
| 7 | `engine/windows.py` | `_blackout` … `_windows_state` (2980-3131) | none; by Fri 12-11 |

The account guard goes second because it carries the kill switch. **Stop rule:** a seam that needs more than about 8
Engine attributes is stopped and recorded, not forced through a mixin. Non-negotiables untouched (RiskGate, exits,
features); no owner decisions. Docs to update: ADR 0023 seam table, the W2-1 file table, HANDOFF, TRACEABILITY S4.
