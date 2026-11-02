- Key levels (owner 2026-11-02, sprint-3000 ADDENDUM A; ADR 0068). `goldbot/features/key_levels.py` builds
  point-in-time support and resistance zones from five sources: confirmed 15m/1h/4h/1d/1w swings, the previous
  day/week/month high and low, and $50/$100 numbers. Levels are clustered at 0.25 ATR(1d). Each zone carries an
  age-decayed strength, its 250-day `strength_pct`, the timeframes it spans and a role flip. Corrected minutes are
  dropped. `pct_ready` and `book_mature` (540 days of history) gate the primitives. Runtime on 16 years of 1m bars:
  `zones_asof` 7 s, the full per-minute frame 38 s, one decision-timeframe `LevelContext` 14-31 s (built once,
  sliced per window). `goldbot/research/level_vocab.py` holds `level_break`, `level_reject`, `near_level` and
  `room_to_level` (opposing zone = top quartile, frozen to the catalogue's zone bar) for the sprint-3000 screen. They
  still have to be registered in the sprint3000 engine's vocabulary once that engine is on main.
- The engine builds `key_zones` off the bar close (display only), on 800 days of the broker's M15. The M15 bars are
  pulled in pages through `get_bars_range`, cached in the store table `bars_15m_broker` and topped up daily, so the
  live zones equal research's. It publishes at most 3 strong zones each side (strength_pct >= 50, merged neighbours,
  ranked by strength-weighted proximity). `GET /api/key-levels` marks zones over 30 minutes old as stale. The Chart
  tab draws them as bands, thicker for stronger, labelled with their timeframes, and says how much history they were
  built on.
