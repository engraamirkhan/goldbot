- Arms wave 2 config (lane E4; arms-2026-10-25.md 8, 8.13, 8.14; arms-expansion section 11): 25 new arm ids in
  `config/exploration_arms.yaml` (F2A/F2C, PDC1/PDC4, OBR15, KSQ1/KSQ4, DON4/DOND, C30A/C30C, KA15/XC15, BXR/BXT/BXF
  x 15m/30m/1h/4h); F1A/F1C re-frozen as `hurst_band_break(_control)`, WFRI as `friday_long`/30m (new hashes, no fill or
  history run existed); G1A/G1C keep `level_break` and their hashes. Placeholders `band_hurst_switch`/`weekday_hold`
  left `arms.RULES`. New `Arm.forward_only` (not hashed): `runnable_ids` skips it, so time-structure, KA15/XC15 and
  H-80 arms never enter the H-60 matrix. Blocked until their engine lane merges: E1 F1A/F1C/F2A/F2C/WFRI; E2
  G1A/G1C/C30A/C30C/DOND; E3 XC15 and all BX rows. Running now: PDC1, PDC4, OBR15, KSQ1, KSQ4 (no host), DON4, KA15.
  Swing pot max_open 1 -> 2 on icm-demo and vantage-demo; no £ or cap moved. BXR registered on icm-demo (prereg named
  icm-probe, whose £6.50 scalp pot cannot hold a 1.5 ATR 15m+ stop); BXT/BXF and KSQ4/DOND registered nowhere.
  Open for review: PDC4 (32 x 4h) and C30A (460 x 15m) holds always reach the Friday cut, so `crosses_friday` refuses
  them until E2's weekend work; whether wave-2 8.x arms get an H-60 history run is the quant's call.
