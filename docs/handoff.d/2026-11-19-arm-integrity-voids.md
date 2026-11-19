- 2026-11-19 discovered-arm integrity voids made precise, still fail closed (goldbot/research/arm_integrity.py; an SRE
  dry run on settled weekdays found most 15m candidates void `replaced_minute` and every 4h one `release_after_cutover`).
  (1) A `broker_bar_replaced` event within 27 min before a decision no longer voids by itself: the event (its ts the
  first replaced minute, its detail the count and the largest close change only) says minutes were replaced, never
  when. The engine's flagged stored copy (`history.REPLACED_COL`, since 2026-11-18) says when: written before the
  decision, the engine held the broker's bar and the night recomputes from it; written after, K1 voids. An event the
  store cannot account for (no flagged copy of its minute, fewer flagged minutes than it counts, a detail that does
  not parse, any event from before the flag) still voids. (2) Root cause of the 4h voids: the engine never holds a
  release minute at or after its cut-over, but the night filled every gap in the engine's minutes from the release
  and voided. Release minutes after the cut-over are now evidence only; a missing minute after the cut-over voids as
  `gap_after_cutover` (renamed from `release_after_cutover`) when the broker's M1 (`bars_1m_broker`, the daily
  reconcile) has it, or the release has it and the broker's M1 is not seen silent around it (bars on both sides at
  most 2 h apart). A minute the broker's M1 shows it never had is left out, as the engine left it out. Stricter than
  before: a gap the broker's M1 shows is now void after the release ends too. (3) The engine's hot loader refuses a
  learning row whose `since` is before the amendment's effective day; `EFFECTIVE_UTC` now lives in
  `research.arm_rules_spec` (same value) and registration imports it. Kill rules, caps, the prereg, `EFFECTIVE_UTC`
  and `LIFECYCLE_READY` are unchanged.
