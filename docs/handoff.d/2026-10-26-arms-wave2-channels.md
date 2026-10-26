- Wave-2 channel arm rules (2026-10-26, lane W2c; ARM-SPEC docs/research/arms-2026-10-25.md section 8 and 8.13):
  goldbot/research/arm_rules_channels.py adds `squeeze_release` (KSQ1 1h / KSQ4 4h), `donchian_break` (DON4 4h /
  DOND 1d), `low30_touch` (C30A, 15m on trading-day context) and `low30_control` (C30C, whole trading day,
  hash_u < 0.00065, C30A's bracket and calendar). The 8.0 filters (no entry 16:55-17:00 / 18:00-18:30 New York, listed
  CME holidays) are applied in the rule. "No target" is NO_TARGET_R = 20 R. An arm is refused at load on a foreign
  timeframe or a `params.hold_bars` other than the frozen hold. 2010-2017 counts: KSQ1 533 (slice 566), KSQ4 94 (94),
  DON4 512 (512), DOND 96 (77; 13:30 NY settlement days, declared), C30 155 (slice 310 raw / 118 non-overlapping).
  Open for the engine lane: C30A/C30C need about 31+ trading days of 15m bars and DOND 56+ daily bars, more than
  `exploration.LOOKBACK` / the 1m memory give, so they emit nothing live (fail closed) until the engine feeds more
  history; the 460-bar C30 hold and the 10-day DOND hold always meet `crosses_friday` (weekend_hold). No config rows
  were added (arms are registered by their own lane). Tests: tests/test_arm_rules_channels.py.
