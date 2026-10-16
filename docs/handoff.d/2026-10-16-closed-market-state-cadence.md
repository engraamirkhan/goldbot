- **Engine state keeps moving while the market is closed (2026-10-16; BACKLOG 59; trading-safety and SRE review
  pending).** The engine wrote `engine_<acct>.json` on tick time only. With the market closed the tick stamp is
  frozen, so the file stopped, and health failed `bridge_compat:<acct>` once its `checked_at` was 5 min old: a
  Telegram FAIL and a Healthchecks.io `/fail` every 2 min all weekend, and a FAIL/recovered pair every weekday evening.
  - **The fix.** On the live clock the engine also writes its state when `state_every_s` (10 s) of host time has
    passed with no write (`Engine._state_heartbeat_due`, `_write_heartbeat` in `goldbot/engine/runner.py`). Any write
    restarts that timer, so the rate stays one per 10 s, with the same fsync rules. Replays and backtests (tick clock)
    are unchanged.
  - **What the heartbeat does not do.** It reads no account and runs no stage machine, kill switch, weekend rule or
    other exit step. Those stay on the tick-time refresh, as before. It changes no EngineState field, so the gate sees
    exactly what it saw before. No change to the order or exit path; RiskGate untouched.
  - **Fields stay true.** The heartbeat restates `last_tick_age_s`, `tick_ahead_s` and `market_closed` on the host
    clock. On a weekend the file says the last tick is hours old and the market is closed. Before, it kept the last
    market-hours reading. Owner-visible: on a weekend the dashboard's entry status reads the engine's live state, not
    the "engine idle while closed" fallback, so it lists "Market closed" and also "stale data" (no tick for hours,
    true); entries are refused either way.
  - **The age rules still bite.** Health's rules are unchanged. An engine that writes nothing still FAILs
    `bridge_compat` on the weekend (5 min) and `engine` 45 min after the reopen; `engine_loop` is unchanged.
  - **Backups, checked, left alone.** With no restic keys, `job:backup` and `job:restore_drill` FAIL, which is
    correct until the owner sets the keys. The reason reads "restic not configured, missing in the secret store:
    <keys> (goldbot accounts set <key>;" because the job check cuts the error at 160 characters, so "RUNBOOK
    'Backups')" is lost when all four keys are missing. `backup_age` and `restore_drill` warn and name the RUNBOOK.
