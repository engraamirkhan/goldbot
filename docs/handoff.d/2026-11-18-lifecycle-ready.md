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
