- 2026-11-09 trading-safety R1/R2 (branch fix/account-recheck-1109): **the terminal's account is checked again after
  start** (`goldbot/engine/terminal_account.py`). The service wires the start check (`accounts.verify_terminal_account`,
  `services.engine.verify_account`) into `Engine.verify_account`; it runs on every account refresh and every
  reconciliation. On a mismatch: entries refused `terminal_account_changed` (RiskGate), the account read is not used
  (equity, stages, high-water mark, risk period keep this account's last reading), every positions and deals read is
  refused and counted as `positions_unreadable` (no adoption, no inferred close, no unconfirmed-order resolution from
  the other book), engine state `terminal_account_changed` (health `engine:<account>` FAIL) and one Telegram notice each
  way. Exits are never gated; positions keep their broker stops. Back on the account, entries resume after
  `healthy_resume_s`. **R2:** an exploration or cost-probe ledger row settled by the BACKLOG 72 look-up carries
  `resolution` (`adopted` / `opened_closed` / `unfilled`; JSON body only, no migration); the retcode stays as answered.
  Golden `tests/data/engine_store_golden` regenerated for the new engine-state key only.
  Review round 1 (trading-safety 2 HIGH, 2 MED, 4 LOW): the risk-period roll checks its own read and rolls only once the
  terminal is back; exits are never gated while flagged (due closes, the engine stop and the kill switch send by ticket,
  never dropped as gone, never re-reading the whole account); `no_quote_pass` checks every `reconcile_every_s` and the
  kill switch checks before a whole-account read; a re-arm waits unconsumed (`rearm_refused` terminal_account_changed;
  re-arm source pin in tests/test_demo_roll.py updated); a closed record takes the engine's equity; `note_resolution`
  opens the ledger inside its try; every book's `data_recovering` waits `healthy_resume_s` from the restore.
