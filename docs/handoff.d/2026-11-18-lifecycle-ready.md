- 2026-11-18 discovered-arm lifecycle inputs (prereg-amendment-2026-11-18-discovered-arms.md 4, 5.1 (5), 2.2): the
  nightly `arm_lifecycle` job now hands the read the engines' journal (`arm_registry_bracket_void`,
  `arm_registry_control_changed`, store `decisions` from the earliest registration on) and the bracket recompute from
  the minutes each engine stored (`arm_lifecycle.stored_recompute`: `bars_1m_live` first, point in time by write time,
  nominal closes, the engine's own `arm_bars` window, so Wilder ATR starts where the engine's did). Bracket voids and
  control changes now retire a discovered arm at night; before, it only held. A minute the engine logged
  `broker_bar_replaced` within 27 min before a decision leaves that candidate unrecomputable (void, not a refusal: the
  store keeps only the last copy). An unreadable journal holds the arm as unverified. `LIFECYCLE_READY` stays False:
  the flip waits on the quant and trading-safety re-review. The engine's retirement on the first void at entry (stricter
  than 5.1 (5)) is recorded in docs/research/arm-lifecycle-2026-11-18.md.
- 2026-11-18 discovered-arm lifecycle, fail closed (the reviews of 611d5adb): new goldbot/research/arm_integrity.py.
  A discovered arm's read is evidence only when the night saw everything: per account with a candidate, the whole
  journal (schema, a row on every candidate day, through the last candidate) from an engine that journalled
  `arm_registry_capable` since the arm's registration (the engine now does when it loads a discovered learning arm),
  and every account's minutes; otherwise it HOLDs `unverified` (kill rules still act) and health
  `discovered_arm_reads` WARNs, told, after 2 nights. The recompute reads only what the engine held (bars_1m_live and
  the release minutes before the cut-over; never broker M1; 1d/1w, a release minute after the cut-over, a minute
  replaced after the decision (now flagged on the stored minute, `history.REPLACED_COL`) or quarantined: void with its
  cause, never a refusal). Each verdict is cached once in aux.db `arm_lifecycle_verdicts` (migration 39, append-only).
  Every refusal carries the engine's and the night's bracket; the job summary counts refusals and voids by cause. A
  void the engine journalled at the entry retires as "bracket integrity: void at entry (engine)" (C1, section 5 of
  docs/research/arm-lifecycle-2026-11-18.md). `LIFECYCLE_READY` stays False.
- 2026-11-18 second review round on the discovered-arm lifecycle: a verdict is cached only once its day has settled
  and its account's coverage passes; a window not yet whole in the store is void `window_incomplete` and retried;
  1d/1w reads unverified (and never registers); every load of a discovered arm needs its own capability marker (the
  engine writes both at one time), so a rollback holds the arm unverified; the replaced-minute check reads every
  stored version; `discovered_arm_reads` counts only the latest night and skips retired arms.
