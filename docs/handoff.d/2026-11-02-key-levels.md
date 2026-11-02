- Key levels (owner 2026-11-02, sprint-3000 ADDENDUM A; ADR 0068). `goldbot/features/key_levels.py` builds
  point-in-time support and resistance zones from five sources: confirmed 15m/1h/4h/1d/1w swings, the previous
  day/week/month high and low, and $50/$100 numbers. Levels are clustered at 0.25 ATR(1d). Each zone carries an
  age-decayed strength, its 250-day `strength_pct`, the timeframes it spans and a role flip. Runtime on 16 years of
  1m bars: `zones_asof` 7 s, the full per-minute frame 38 s, one decision-timeframe `LevelContext` 14-31 s.
  `goldbot/research/level_vocab.py` holds `level_break`, `level_reject`, `near_level` and `room_to_level`
  (opposing zone = top quartile) for the sprint-3000 screen. They still have to be registered in the
  sprint3000 engine's vocabulary once that engine is on main. The engine publishes `key_zones` (3 above, 3 below)
  every 15m close, display only. `GET /api/key-levels` serves them, and the Chart tab draws them as bands: thicker is
  stronger, and each label lists the zone's timeframes. Live zones see only the engine's ~62 days of bars
  (`history_days` is shown).
