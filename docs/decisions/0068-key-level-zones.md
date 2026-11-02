# 0068: Key-level zones: definition choices and room_to_level strength

Status: accepted (implementer, 2026-11-02)

## Context
The owner asked on 2026-11-02 for the system to "identify key levels, support and resistance on all time frames".
The sprint-3000 brief (ADDENDUM A) froze the outline: confirmed swings on 15m/1h/4h/1d/1w, PDH/PDL, PWH/PWL,
PMH/PML and $50/$100 round numbers, grouped within 0.25 x ATR(14, 1d). Strength is timeframe weight x touches,
age-decayed. `strength_pct` is the zone's percentile over the trailing 250 days, plus the timeframes it spans and a
role flip. It left open the details a working definition needs. The catalogue (PR #274) uses the four level
primitives in 379 specs, and the top-ranked ones filter on `room_to_level(min_r: 2)`. These details have to be frozen
before any screen result exists.

## Decision
`goldbot/features/key_levels.py` and `goldbot/research/level_vocab.py` implement the brief with these choices:

- **Point in time.** A swing at bar j is known at the close of bar j + 5. Period levels come from the finest bar closing
  at the evaluation, after the 17:00 New York roll. ATR(14, 1d) uses completed feature-day bars. The book is
  evaluated at every 15m close. An input bar reads the last evaluation at or before its own close. Prefix-invariance
  tests show that appending bars never changes a past zone or row.
- **Weights.** Swings use 15m 1 / 1h 2 / 4h 3 / 1d 4 / 1w 5. The previous day's high/low counts as 1d (4), the
  previous week's and month's as 5. Round numbers weigh 1 ($50) or 2 ($100) and are taken only within 3 ATR(1d) of
  the close.
- **Decay.** Swing weight halves every 3 / 7 / 20 / 60 / 180 days (15m / 1h / 4h / 1d / 1w) and the swing is dropped
  after three half-lives. Period and round levels do not decay.
- **Clustering.** Levels are grouped greedily from the lowest price, so no zone is wider than 0.25 ATR(1d). Single
  linkage would chain a busy range into one wide band.
- **Percentile pool.** The pool is the book at the first evaluation of each UTC day over the trailing 250 calendar
  days, excluding today. `strength_pct` stays None until 20 days are sampled.
- **Role.** Support above the zone, resistance below it, inside within it. A zone is born a resistance or a support
  according to the weighted majority of its members. `flipped` means it now plays the opposite role.
- **Primitives.** Entries (`level_break`, `level_reject`) use the zones known at the previous decision close. Filters
  (`near_level`, `room_to_level`) use the book at the decision close. `near_level` measures distance to the zone edge
  in ATR(14) of the entry timeframe, which is the stop's unit.
- **room_to_level strength = 75 (top quartile).** The brief names no strength for the "opposing zone". Counting every
  zone makes the filter measure zone density: the nearest zone is usually a fresh 15m swing or a bare $50 number,
  about 0.16 ATR(1d) away. Top quartile matches the bar the catalogue's `near_level` uses for "a zone". It also fits
  the mechanism: a target fails at strong levels, while weak ones are run through. A zone the close sits in leaves no
  room. No opposing zone means unlimited room.
- **Evidence behind the 75 choice.** It was frequency only; no returns were looked at. Over 2010-01 to 2025-09, a
  1h long with a 3 ATR stop passes `min_r 2` on 3.7% / 9.4% / 28% of bars at strength 50 / 75 / 90. Short: 1.8% /
  5.8% / 23%.

## Consequences
- The 379 level specs can be screened with one book pass per decision timeframe: `LevelContext.build`, about 31 s for
  1h and 14 s for 4h over 16 years with all 13 zone views.
- This record freezes the definition. A changed constant is a new definition, and specs run on it count as new ids.
- The live engine builds the zones from its own ~62 days of 1m bars, so most 1w swings and the 250-day pool are
  truncated live. The API and dashboard state `history_days`. Live/research parity of the zones matters only once a
  level rule trades, and that needs a longer engine history (follow-up).
- Zones are dense: about 36 within ±3 ATR(1d) of gold in 2026. Rules should filter on `strength_pct` and timeframe,
  not on "a zone exists".
