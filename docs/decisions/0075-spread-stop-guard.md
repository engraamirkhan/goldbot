# 0075: Spread against the stop and commission in the planned risk

Status: proposed (implementer, 2026-11-03; for trading-safety and quant review)

## Context
The read-only audit of demo non-gold fills (2026-11-03) found exploration arms taking entries whose stop sat inside the
spread: S0-SUGAR traded a 0.00055 stop with a 0.00055 spread (12/12 stopped, -£57.67); BUND10Y's spread was 0.52 of
its stop (27/32 stopped), UST10Y's 0.89. The only spread rule was the absolute `max_spread_points` cap (per instrument
for a non-gold book), which cannot see a stop that is short relative to the spread; `sizing.stop_distance` floors the
stop to the stops level plus the spread, so a rule stop inside the spread became a stop AT the spread. The units were
right (each book's `spread_points` is in its own registry point). A non-gold book has no cost table yet, so its cost
was the configured 0.10 R whatever the live spread, and the commission was not part of the planned risk: on FX
min-lot scalps £0.06 commission is 25-75% of the £0.08-0.25 planned risk (18/113 stops lost more than 1.2x planned).

## Decision
- RiskGate refuses `spread_over_stop` when the spread (state `spread_points` x the intent's point) exceeds
  `risk.max_spread_stop_frac` (0.3, (0, 1]) of the stop the entry ASKS for (stop_atr x atr_usd), on every purpose
  (signal, learning probe, cost measurement, exploration). The asked stop, not the floored one: the rule's R and
  target are measured on it, and the floored stop is never shorter, so this is the stricter reading. Equal is allowed.
- `Intent.commission_usd_per_oz` (round trip, price units per contract unit, as `swap_usd_per_oz`) is part of one
  lot's risk: sizing, `risk_amount`, `risk_fraction`, the minimum-lot exception, the arm caps, heat and the daily
  projection all count it. 0 (the default) leaves every pinned gold decision byte-identical.
- The engine's `_cost_atr` is never below the live spread plus the round-trip commission; `_commission_usd_per_oz` is
  the cost table's measured figure, else the broker prior `costs.commission_per_lot_side_usd[broker]` for gold, other
  metals and FX (per lot, over the contract; a USD-base pair converted at its price), 0 for index, energy, bond and
  soft CFDs (spread-priced).

## Consequences
- Tightening only: on the pinned decision grid every changed case is an entry now refused `spread_over_stop`
  (tests/test_spread_stop_guard.py); nothing refused before is allowed.
- The daily projection and the pilot's per-trade cap now count the commission twice (in the stop risk and in the
  round-trip cost), as the spread already was: conservative by design. On the live pilot (0.01 lot, ~£0.05) it can
  refuse an entry that sat within pennies of the £3.12 cap.
- Small-stop scalps whose stop is under 3.3 spreads stop trading (gold: a $1 stop needs a spread of $0.30 or less).
- Non-USD metal quotes (XAUEUR, XAUAUD) keep the USD commission figure unconverted (a few percent of a ~1% term).
  A per-symbol measured commission (deal history by symbol) replaces the class prior when a book gets its cost table.
