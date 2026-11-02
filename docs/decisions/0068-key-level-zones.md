# 0068: Key-level zones: definition choices and room_to_level strength

Status: accepted (implementer, 2026-11-02; amended after the quant and safety reviews of #281)

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
- **Evidence and disclosure for 75.** 75 is fixed by the catalogue's own definition of a zone (`near_level`
  min_strength_pct 75, catalogue sha ea344498…, committed before any bar was read) and by the mechanism; it was not
  selected from data. After the choice, pass-rate frequencies (no returns, no outcomes) were computed over
  2010-01..2025-09, which includes the confirm window, contrary to prereg §0's no-2018-2025 statement: a 1h long with
  a 3 ATR stop passes min_r 2 on 3.7% / 9.4% / 28% of bars at strength 50 / 75 / 90 (short 1.8% / 5.8% / 23%). These
  figures did not move the choice (75 is not the n-maximising value) and cannot change the null distribution of any
  pass statistic. 75 is frozen; any other value is a new definition, new spec ids, counted in K.
- **Corrected minutes (quant review F1).** An input bar visible later than its natural close (a minute corrected
  after the fact) is dropped, as `resample_bars(drop_late=True)` drops it from decision bars. Input whose
  `visible_at` goes backwards is refused.
- **Maturity (quant review F2).** The frame carries `pct_ready` (strength_pct has its 20 days of samples) and
  `book_mature` (540 days of input history, the life of a 1w swing). `room_to_level` passes only where ATR(1d) is
  finite and pct_ready. No level primitive emits anything where the book is not mature. `LevelContext` is built once
  on the whole history and sliced to a window; slicing a context built from bars that start after the window's start
  is refused. Consequence: the first 540 days of the history (2010-01..2011-06) carry no level signals.
- **Recording level results (quant review F3).** A room_to_level spec that fails explore or confirm on n < 100 is
  recorded as untested (insufficient events), not rejected. ROOM_MIN_STRENGTH_PCT is not changed after any result; a
  changed value is a new definition with new ids counted in K. Long-side room_to_level results are reported alongside
  the same entry without the filter, so an all-time-high regime effect is not read as a level effect.

### Display (live dashboard and API only; the research definition above is unchanged)
- **Long history.** The engine's zones are built on the broker's own M15 bars, 800 days deep (540-day mature book +
  250-day pool + 10 days). They are pulled through the bridge's `get_bars_range` in 90-day pages on a separate client
  and thread, cached in the store (`bars_15m_broker`, source = account) and topped up once a day from the last cached
  bars minus two days. The engine's own 15m bars replace them from its first one on. 15m is the book's evaluation
  clock, so the live book is the research book: on the 2026-10-23 bar it reproduces the 16-year build's zones and
  percentiles, for example 4001.62 at 68.3 and 3943.38 at 66.1. The build runs off the bar close (about 0.11 s). Builds
  over 1 s are logged, and the first failure of each exception type is logged with its traceback. A non-finite price
  or ATR keeps the last zones. The API marks zones over 30 minutes old as stale.
- **Density rule** (not fitted to any data; all thresholds follow from the frozen definition):
  - Only zones with strength_pct >= 50 (the median zone) are shown.
  - A lone single-touch 15m swing is never shown; it ages out of the book after 9 days anyway.
  - Neighbours less than 0.25 ATR(1d) apart (the research cluster width) merge for display, up to 0.5 ATR wide.
  - At most 3 zones are shown on each side of the price. They are chosen by strength-weighted proximity:
    strength_pct / 100 / (1 + edge distance / ATR(1d)).

  The 36-zone book on 2026-10-23 shows 3 above and 2 below.

## Consequences
- The 379 level specs can be screened with one book pass per decision timeframe: `LevelContext.build`, about 31 s for
  1h and 14 s for 4h over 16 years with all 13 zone views.
- This record freezes the definition. A changed constant is a new definition, and specs run on it count as new ids.
- Live and research zones agree once the broker's M15 history is cached. Without a bridge (paper) the engine's own
  ~62 days are used, and the dashboard says the history is short.
- Research zones are dense: about 36 within ±3 ATR(1d) of gold in 2026. Rules should filter on `strength_pct` and
  timeframe, not on "a zone exists". The display shows only the few strongest nearby.
