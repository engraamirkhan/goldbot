# 0071: Position engine booking and null conventions

Status: accepted with the W3 addendum (quant review of PR #291, round 1, 2026-11-02). The binding text is
`docs/research/pos1/w3-addendum.md`; this record keeps the reasoning.

## Context
Amendment W3 (PR #285) fixes the read of position specs: `pnl_d = e_{d-1} x r_d - costs_d - financing_d`, with r_d the
mid return "from fill to fill". It also fixes a 3 x ATR(20) disaster stop, a no-trade band, an episode-sign null
"re-costed and re-financed", and an exposure-matched null for long-only specs. The engine (`goldbot/research/position/`)
has to turn these into exact arithmetic. W3 leaves several points open, and each one changes numbers. The first review
blocked the engine on 13 findings; the addendum records the rulings, and they are summarised here.

## Decision
1. **Units, not constant exposure.** A fill converts the target exposure into units (e / fill price). Between
   rebalances the exposure drifts with price, and a trade happens only when the target leaves the band. Daily
   re-weighting to a constant e would trade, and pay costs, every day. Summed over a hold, P&L is units x (exit -
   entry): `triple_barrier_next_open_1m` parity is exact at zero spread.
2. **Marked at the 21:00 UTC close.** The day's P&L is a x (open - previous close) + b x (close - open). Fill to fill
   differs only in which day carries the minutes between the 21:00 close and the next 1m open. Window totals are
   identical.
3. **Flat at window edges.** Decisions dated before a window's first day are 0. The position is closed at the window's
   last close and pays the exit. No trade or P&L crosses from one window into another, or into the holdout. The
   signal sees only rows known by the window's last close. EXPLORE reads LEARN-OLD and LEARN as one series, with
   each part flat at its own edges.
4. **Weekend stubs fold forward.** The northern-winter Friday hour after 21:00 UTC is labelled Saturday by the 21:00
   UTC day. It is the next 1m open after Friday's decision, so it opens Monday's bar.
5. **Nulls keep the schedule.**
   - The episode-sign null flips each episode's units and keeps its start, end, |units| path and exit prices. A flip is
     therefore an exact negation of that episode's gross, so the mean null gross is 0 by construction. Each draw is
     re-costed and re-financed.
   - The exposure-matched null (long/flat specs) shifts the whole |e| path of the window circularly. An always-long
     spec's null mean is therefore buy-and-hold.
6. **BH p.** At CONFIRM only, over the committed confirm-eligible ids, BH-FDR uses the one-sided p of the
   block-bootstrap t (W2: "one-sided day-block-bootstrap p"). The null p is reported beside it.
7. **After a stop, restore at the last decision.** The next open re-enters at the last non-NaN decision, subject to the
   band. A same-sign re-entry continues the round trip; stops are counted apart.

## Consequences
- Costs are C1 point costs from the Asia cell of the 21:00 fill (0.51 bp round trip). The measured fill-minute spread
  (median 3.3 bp in 2010-17, 2.4 bp in 2018-25) is reported as venue S, which never gates.
- Financing never falls back to USD for a market whose currency is not USD: such a market is refused without its own
  rate. The same applies to a symbol without a triple rollover day.
- The disaster stop of a market entered before ATR(20) exists is set on the first day it does (the 260-day warm-up
  makes this moot in POS-1).
- The book-level 60-day covariance is zero-mean EWMA, consistent with the per-instrument vol. Its scale factor is
  capped at 2x, and every column is re-clipped to 2x after it.
- Any change to these rules is a W3 amendment, made before the run. It is not an engine tweak.
