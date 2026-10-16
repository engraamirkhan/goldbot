---
number: 46
status: 'parked: the replay needs a shadow book, which needs 1; schedule after 45'
---
- **46. Replay the entry windows before adopting any (performance analyst, strategy researcher; review M4).**
  Value: each window (items 17, 18) removes entries; whether the removed ones were worse than the kept ones is
  unknown. Do: over the shadow book and the backtest candidates, the fraction of entries each window (tier 2,
  reopen, holiday, alone and together) would have filtered, and R of the filtered vs kept trades, by family and
  timeframe; add a `research_pass.py --entry-windows` filter option that drops signals inside the enabled windows
  (point in time: the archive as known at the signal, `available_utc` joins), so trials can be run with and without.
  No trial budget is spent by the replay itself. Acceptance: the table per window, family and timeframe, attached to
  the owner's adopt decision (OWNER_GUIDE section 4). Quant review.
  Sweep 2026-10-16 (product owner): Parked: the replay needs a shadow book, which needs 1; schedule after 45.
