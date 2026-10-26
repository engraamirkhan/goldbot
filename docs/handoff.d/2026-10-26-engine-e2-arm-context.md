- Engine lane E2 (2026-10-26): exploration arms that need more than the engine's ~40-day 1m window now read it live
  as history does (`goldbot/engine/exploration_context.py`). C30A/C30C (20,000 15m bars), DOND (300 1d bars) and
  G1A/G1C (2,500 1h bars) get the store's release bars of their own timeframe before the window (read once, point in
  time, the store must reach into the window). smc tvr1/tvr2 (XTV1, VXTV2) resume a replica state from their own
  aux.db feed `xauusd-s2010-15m-*`, bootstrapped in the background from the 2010 series start (B4a). No store, a
  store ending before the window, a store that does not start 2010, or a bootstrap running: the arm is journalled
  `exploration_held_back` (once per reason) and sends nothing. New rule `level_break` (H-71 G-1, G1A and its control
  G1C) in `goldbot/research/arm_rules_levels.py`: previous day/session/week/month and round-number levels set at the
  trading day's start, slice G's first-touch break-close, one a day; declared choices for the quant review: the
  nearest-to-close level when one bar breaks several, the prereg's "every week/month" level set (slice G used period
  levels only in a period's first five days), session levels carried from D-1.
  Weekend: unchanged. ADR 0043 X3 with quant Q2 refuses every style whose hold reaches the Friday cut, as for W1A/W1B,
  so DOND (10 x 1d), W1B (11 x 1d) and PDC4 (32 x 4h = 128 h) can never enter and C30A/C30C only from the Sunday
  reopen. Lifting that needs an arm amendment that runs the Friday cut in history (as WFRI, ARM-SPEC 8.14 (2)),
  routed to quant + trading-safety. Ops: XTV1/VXTV2 need the 2010-2015 release 15m bars on the exploration host's
  store, or they stay held back. Config rows (status blocked) are for the config lane.
