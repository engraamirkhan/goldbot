# 0071: Position engine booking and null conventions

Status: proposed (implementer, 2026-11-02; for quant-reviewer before the POS-1 run)

## Context
Amendment W3 (PR #285) fixes the read of position specs: `pnl_d = e_{d-1} x r_d - costs_d - financing_d`, with r_d the
mid return "from fill to fill". It also fixes a 3 x ATR(20) disaster stop, a no-trade band, an episode-sign null
"re-costed and re-financed", and an exposure-matched null for long-only specs. The engine (`goldbot/research/position/`)
has to turn these into exact arithmetic. W3 leaves six points open, and each one changes numbers.

## Decision
1. **Units, not constant exposure.** A fill converts the target exposure into units (e / fill price). Between
   rebalances the exposure drifts with price, and a trade happens only when the target leaves the band. Daily
   re-weighting to a constant e would trade, and pay costs, every day. Summed over a hold, P&L is units x (exit -
   entry): `triple_barrier_next_open_1m` parity is exact at zero spread.
2. **Marked at the 21:00 UTC close.** The day's P&L is a x (open - previous close) + b x (close - open). Fill to fill
   differs only in which day carries the minutes between the 21:00 close and the next 1m open. Window totals are
   identical.
3. **Flat at window edges.** Decisions dated before a window's first day are 0. The position is closed at the window's
   last close and pays the exit. No trade or P&L crosses from one window into another, or into the holdout. A run
   hands the signal only the rows up to the window's last day.
4. **Weekend stubs fold forward.** The northern-winter Friday hour after 21:00 UTC is labelled Saturday by the 21:00
   UTC day. It is the next 1m open after Friday's decision, so it opens Monday's bar.
5. **Nulls keep the schedule.** The episode-sign null flips each episode's units, keeps the realised stop exits at
   their prices, and re-books costs, overshoot and financing. Stops are not re-simulated for the flipped side.
   The exposure-matched null moves each episode's |e| path to a uniform random start inside the window, with the
   first window day excluded. Overlaps add, and it has no stops.
6. **BH p.** `scripts/position_screen.py` puts the one-sided p of the block-bootstrap t into BH-FDR (W2: "one-sided
   day-block-bootstrap p"). The null p is reported beside it.

## Consequences
- Costs are C1 point costs. Dukascopy's historical spreads ran 2-4x wider (rethink Appendix B). W3 chose point costs,
  so a 1d spec's turnover cost is the lower bound. Quant-reviewer should confirm this before POS-1.
- The episode-sign null under-states a stop's asymmetry, because a flipped episode keeps the original exit day. This
  is conservative only where stops are rare: at 3 x ATR(20) they are.
- The disaster stop of a market entered before ATR(20) exists is set on the first day it does (the 260-day warm-up
  makes this moot in POS-1).
- The book-level 60-day covariance is zero-mean EWMA, consistent with the per-instrument vol. Its scale factor is
  capped at 2x.
- Any change to these rules is a W3 amendment, made before the run. It is not an engine tweak.
