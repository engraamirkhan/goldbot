- **Engine state keeps moving while the market is closed (2026-10-16; BACKLOG 59; trading-safety and SRE review
  pending).** The engine wrote `engine_<acct>.json` on tick time only. With the market closed the tick stamp is
  frozen, so the file stopped, and health failed `bridge_compat:<acct>` once its `checked_at` was 5 min old: a
  Telegram FAIL and a Healthchecks.io `/fail` every 2 min all weekend, and a FAIL/recovered pair every weekday evening.
  - **The fix.** On the live clock, and only while the session table has the market closed, the engine also writes
    its state when `state_every_s` (10 s) of host time has passed with no write (`Engine._state_heartbeat_due`,
    `_write_heartbeat` in `goldbot/engine/runner.py`). Any write restarts that timer, so the rate stays one per 10 s,
    with the same fsync rules. A host clock stepped back rewrites the file at once in any session (BACKLOG 58: no
    stamp from the future). A session table that cannot be read never takes the heartbeat. Replays and backtests
    (tick clock) are unchanged.
  - **Review HIGH, fixed.** The first version also wrote in market hours, so a quote frozen in an OPEN session kept
    `bridge_compat` OK and `engine` at WARN forever. Now the file ages in market hours as before: `bridge_compat`
    FAILs after 5 min and `engine` after 45 min (`test_a_frozen_feed_in_market_hours_is_never_masked_and_fails_health`).
  - **What the heartbeat does not do.** It reads no account and runs no stage machine, kill switch, weekend rule or
    other exit step. Those stay on the tick-time refresh, as before. It changes no EngineState field, so the gate sees
    exactly what it saw before. No change to the order or exit path; RiskGate untouched.
  - **Fields stay true.** The heartbeat restates `last_tick_age_s`, `tick_ahead_s` and `market_closed` on the host
    clock. On a weekend the file says the last tick is hours old and the market is closed. Before, it kept the last
    market-hours reading. Owner-visible: on a weekend the dashboard's entry status reads the engine's live state, not
    the "engine idle while closed" fallback. It shows only "Market closed": `stale_data` is skipped when the engine
    says the market is closed (`goldbot/api/entry_status.py`), so a weekend never reads like a fault.
  - **The age rules still bite.** Health's rules are unchanged. An engine that writes nothing still FAILs
    `bridge_compat` on the weekend (5 min) and `engine` 45 min after the reopen; `engine_loop` is unchanged.
  - **Backups, checked, left alone.** With no restic keys, `job:backup` and `job:restore_drill` FAIL, which is
    correct until the owner sets the keys; the reason names `goldbot accounts set <key>`. The job check's
    160-character cut (which drops "RUNBOOK 'Backups')") is handled in a separate lane.
