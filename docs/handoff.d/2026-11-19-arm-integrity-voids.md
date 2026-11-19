- 2026-11-19 discovered-arm integrity voids made precise, still fail closed (goldbot/research/arm_integrity.py; an SRE
  dry run on settled weekdays found most 15m candidates void `replaced_minute` and every 4h one `release_after_cutover`).
  (1) A `broker_bar_replaced` event within 27 min before a decision no longer voids by itself: the event (its ts the
  first replaced minute, its detail the count and the largest close change only) says minutes were replaced, never
  when. The engine's flagged stored copy (`history.REPLACED_COL`, since 2026-11-18) says when: written before the
  decision, the engine held the broker's bar and the night recomputes from it; written after, K1 voids. An event the
  store cannot account for (no flagged copy of its minute, fewer flagged minutes than the counts of all events whose
  spans overlap it, a detail that does not parse, any event from before the flag) still voids. (2) Root cause of the
  4h voids: the engine never holds a release minute at or after its cut-over (the earlier of its first stored minute
  and the rebased cut-over saved in aux.db `history_cutover`), but the night filled every gap in the engine's minutes
  from the release and voided. Release minutes after the cut-over are no longer read. The window after the cut-over
  is now proven against the broker's M1 (`bars_1m_broker`, the 23:20 UTC weekday reconcile): not yet pulled past the
  decision, `window_incomplete` (retried; still not after 96 h, `no_broker_coverage`); pulled but with a hole of more
  than 2 h of minutes the session table expects open (a missed pull, M1 history shorter than the window),
  `no_broker_coverage` (unverified, told); a minute the M1 has and the engine's stored minutes lack, the last one
  included, `gap_after_cutover` (renamed from `release_after_cutover`). Weekends and the daily break are observed
  silence, never a gap. Consequence: a discovered arm's read needs the broker's M1 pulled for every account it runs
  on, over its whole window (about 35 trading days for 4h). (3) The engine's hot loader refuses a learning row whose
  `since` is before the amendment's effective day; `EFFECTIVE_UTC` now lives in `research.arm_rules_spec` (same
  value) and registration imports it. Kill rules, caps, the prereg, `EFFECTIVE_UTC` and `LIFECYCLE_READY` are
  unchanged.
