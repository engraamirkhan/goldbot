- 2026-11-20 gold live key per account (ADR 0091; architect plan, quant Q1-Q6, trading-safety S1-S5). Before this,
  gold engines of one broker stored their minutes under one key, so the night could not tell which engine wrote a
  minute: icm cross-account and vantage own `sampled_minute` voids. Changes:
  - `books.live_key` is (account, symbol). The broker's key is legacy: read under the account's own minutes, never
    written. A restart holds the same bars and cut-over as before (S4).
  - An exclusive per-account writer lock (`engine_lock.hold_writer`, its own lock file, pid inside) is taken before
    any bridge. A duplicate start is refused with a journal line, health `duplicate_engine:<account>` WARN (told once)
    and exit 75 (systemd restarts it). It never goes through `start_refused` (S1).
  - A boot id (`EngineConfig.boot_id`) on every bars_1m_live row (`writer`), every engine dq event and every
    exploration candidate (aux.db migration 40), plus a dq `engine_boot` per start recording its lock.
  - The night reads a candidate with a boot id only on a proven window (`AccountMinutes.writer_proof`), and then
    without the sampled void. Anything unproven is void `unproven_writer`. Candidates decided before the first
    recorded start keep the old rules.
  - Readers union both keys, legacy minutes only before the first locked start: account_bars, arm_integrity,
    long_history, trade_chart.

  Benefit only for rows written after the deploy (about 9 trading days to age out; the 24 Nov re-sim is not
  rescued). Kill rules, caps, the prereg, `LIFECYCLE_READY`, RiskGate and exits are unchanged.
  Review round (data NO-GO, safety/quant LOW):
  - Every gold read of bars_1m_live names its symbol. The trade chart unions the legacy key with the account's own
    over it, so the account's non-gold books never mix into the gold chart.
  - Engine starts are read from engine_boot rows only, and the cut-over is cached. An unreadable dq_events no
    longer breaks a bar read; it only leaves the proof unproven.
  - engine_boot no longer counts as a data_quality warning.
  - `duplicate_engine_1h:<account>` tells again after an hour of refusals.
  - The 10-26 quarantine script is retired: it refuses once a locked engine start exists.
  - RUNBOOK: restart all engines of a host together; rollback is whole-release.
