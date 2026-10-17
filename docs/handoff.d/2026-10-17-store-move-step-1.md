- Engine split step 1: state save/load moved to `goldbot/engine/store.py` (TD-2, ADR 0023 step 1, implementer,
  2026-10-17). Move-only. The PR 0 golden grid (192 cases) and every golden under `tests/data/engine_store_golden/`
  pass unchanged.
  - New: `EngineStore` protocol, `JsonEngineStore(state_dir, account_id)`, FrozenRecords `OrdersSnapshot` and
    `RiskSnapshot`. `OpenTrade`, `PendingClose` and `SentOrder` live in the store; `engine.runner` re-exports them.
  - The runner reaches the store as `self.state_store`, because `self.store` is already the market-data `Store`.
    The eight persistence method names stay as thin delegates. `_orders_record` (X7), `_save_risk_state_now`, the
    7-day prune, reconciliation and the kill-switch reset all stay in the runner.
  - Ratchets: `runner.py` 3,552 -> 3,471 lines, cap 3,471. `write_atomic` is banned from `runner.py`;
    `json.loads` / `read_text` are allowed only in `_archive_stale_proposals`, `_drift` and `_account_class` (A1).
  - The full-disk test now patches `store.write_atomic` (S4).
  - Not done here: the ADR 0010 skew note for unknown risk-file keys (BACKLOG 74, the next PR in `store.py`).
  - Reviews needed: trading-safety (restart and state path) and code. Review with
    `git diff --color-moved=dimmed-zebra`.
