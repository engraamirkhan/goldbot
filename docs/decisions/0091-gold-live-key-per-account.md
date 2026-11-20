# 0091: Gold live key per account, one writer per account, boot id on every row

Status: proposed (implementer, 2026-11-20; architect plan with quant conditions Q1-Q6 and trading-safety S1-S5)

## Context
The nightly discovered-arm read (`research/arm_integrity.py`, `AccountMinutes.held`) recomputes a candidate's
bracket from the minutes its engine stored. Gold engines of one broker stored their minutes under one key
(`books.live_key` = broker, symbol) and the store keeps one copy per minute, so the read could not tell which engine
wrote a stored minute. icm-demo and icm-probe share that key: a broker bar that never arrived for one engine voided
the other's 1h windows for about nine trading days. vantage-demo is alone on its key but still voided its own
sampled minutes, because a second overlapping process on the account could not be ruled out (#414 kept both fail
closed). Nothing stored named the writing process.

## Decision
- `books.live_key` of a gold book is (account, symbol), as every other book's already was. The broker's key is
  `books.legacy_live_key`: read under the account's own minutes (the account's copy of a minute wins), never written.
  The engine's warm start, re-read and rebase read both keys, and `saved_cutover` takes the earlier cut-over saved
  under either, so a restart holds the same bars and cut-over as before (S4). The new column never reaches the
  engine's bars (S5).
- One engine per account writes: `engine_lock.hold_writer` takes an exclusive flock on its own lock file
  (state/.engine-writer-<account>.lock, the repair protocol's lock unchanged) before any bridge connection and writes
  its pid into it. Held by another engine: the start is refused with a journal line naming the holder's pid,
  state/engine_duplicate_<account>.json (health `duplicate_engine:<account>` WARN, told once) and exit 75, which
  systemd restarts. It is never refused through `start_refused`, which would write the holder's `startup_error` and
  halt real accounts (S1). A flock error, or no flock at all (the Windows VPS), starts the engine with its rows
  unproven (S2, Q6). There is no pidfile or takeover rule: the kernel frees the lock when its holder dies (S3).
- Each start has a boot id (uuid4 hex, `EngineConfig.boot_id`). It is stamped on every bars_1m_live row (`writer`),
  every dq event the engine writes, and every exploration candidate (aux.db migration 40, `exploration_obs.boot_id`).
  Each start also records `engine_boot` in dq_events with `writer_lock`.
- The read proves a window for a candidate that has a boot id (`AccountMinutes.writer_proof`), and all of these
  must hold:
  - the boot held the lock;
  - no later start began before the decision;
  - no start without the lock began inside the window;
  - every live minute is the account's own key's copy, written by a locked start (the deciding one or an earlier
    one), at a write time inside that start's life (from its boot to the next start).

  A proven window is read without the `sampled_minute` void and without other accounts' events. Every other rule
  still applies (K1, L1, gaps, quarantine). Anything else is void `unproven_writer`, never a refusal. A candidate
  without a boot id is read by the rules before this ADR only if it was decided before the account's first recorded
  start; after that it is void (unknown start).
- Readers union both keys, and legacy minutes are read only before the account's first locked start
  (`account_bars.live_cutover`, Q4). That cut-over never uses the first own minute, because a back-fill would move
  it. Updated readers: `account_bars` (minute_versions, account_bars_1m), `arm_integrity`, `long_history`,
  `trade_chart`, and the quarantine reads (both sources). A test fails if any module outside the known list names
  the table, or if any module reads it by source (Q5).

## Consequences
- The benefit applies only to rows written after the deploy. icm cross-account voids stop for windows that lie
  wholly after it, and existing spans age out in about nine trading days. vantage's own sampled voids stop the same
  way. The 24 Nov re-sim is not rescued; about 4 Dec it is.
- Candidates decided before the deploy keep their current verdicts and rules (no regression). Post-deploy windows
  that still hold legacy minutes are void until those minutes leave the window.
- A duplicate engine start loops every 10 s until the duplicate is stopped. The running engine trades on, and the
  owner is told once.
- Rollback means reverting this change set as a whole. Reverting only `live_key` would make the engines write the
  legacy key again after a locked boot exists, and readers would then read those minutes as legacy past the
  cut-over and drop them.
- Hosts without flock write unproven rows, so their discovered-arm decisions stay void.
- RiskGate, reconciliation and exits do not read bars_1m_live. Only `engine.history` is in the money path, and it
  reads exactly what it read before.
