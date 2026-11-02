# W3 addendum: how the position engine reads POS-1 (frozen before any run)

Implementer (position engine lane), 2026-11-02. Written on the quant review of PR #291 (BLOCK, findings 1-13), and
committed with the engine before any POS-1 bar, result or engine output exists. It adds to amendment W3
(`docs/research/amendment-w3.md`) and the POS-1 pre-registration (`docs/research/pos1/prereg.md`). It changes no
threshold, no window and no K: every item below fixes arithmetic that W3 left open. Code:
`goldbot/research/position/`, `scripts/position_screen.py`. Tests: `tests/research/position/`.

## Rulings carried from ADR 0071

1. **Units, not constant exposure.** A fill turns the target exposure into units (e / fill price). Between rebalances
   the exposure moves with price, and a trade happens only when the target leaves the band. Summed over a hold, P&L is
   units x (exit - entry). This is exact parity with `triple_barrier_next_open_1m` at zero spread.
2. **Marked at the 21:00 UTC close.** A day's P&L is a x (open - previous close) + b x (close - open), where a is the
   units held into the day and b the units after the fill. Fill to fill differs only in which day carries the minutes
   between the 21:00 close and the next 1m open. Window totals are identical.
3. **Flat at window edges.** Decisions dated before a window's first day are 0. The position is closed at the window's
   last close and pays the exit. The signal sees only rows known by the window's last close.
4. **Weekend stubs fold forward.** The winter Friday hour after 21:00 UTC is the next 1m open after Friday's decision,
   so it opens Monday's bar. The data lane's daily files follow the same clock (data manifest, "How the bars are
   made").
5. **The episode-sign null keeps the schedule.**
   - Every holding episode keeps its start, end, |units| path and exit prices, and gets an independent random sign.
   - Each draw is re-costed and re-financed.
   - **Rationale, rewritten:** a flipped episode's gross is the exact negation of its actual gross. Prices, units and
     exit days are unchanged, and only the sign of the units moves. So the expected null gross is 0 by construction,
     and the null measures direction against costs and financing on the same schedule.
   - Costs are sign-free, so they are identical in every draw. Financing differs, because longs pay and shorts earn.
   - A stop exit is kept at its price for both signs. This is part of "keeps the schedule", not a re-simulation.
6. **BH p.** At CONFIRM, BH-FDR uses the one-sided p of the block-bootstrap t (W2: "one-sided day-block-bootstrap p").
   The null p is reported beside it.

## New rulings

7. **EXPLORE = LEARN-OLD + LEARN, read as one series.**
   - Each part runs flat at its own edges, with its own warm-up reading earlier bars, and the signal sees only rows
     known by that part's last close.
   - The daily series are concatenated. t_boot, Sharpe, the years rule and the W3.4 counts are taken on the
     concatenation.
   - The null nets are summed per draw across parts, with the same seeds (seed 20261102 + the market's index in the
     spec).
   - A part with no bars for any market of the spec is skipped and named in `windows_read`; it is not padded.
8. **Years rule on years in the market.** Only years with at least one day in the market count as "available years".
   Warm-up years and all-flat years count neither for nor against the 60% rule.
9. **Restore after a stop.**
   - After a disaster stop, the next open re-enters at the last non-NaN decision, subject to the band. A month-end
     rule is therefore not left flat until the next month end. This is W3's "restored only if the signal still says
     so", read with the signal's last word.
   - A stop at the open skips that day's fill, and the following open restores.
10. **Round trips.**
    - A W3.4 round trip is an entry from flat or a sign change, closed by a return to flat or a flip.
    - A same-sign re-entry at the next open after a disaster stop continues its round trip; the stop does not close it.
    - The engine reports `n_round_trips_signal` (this count, used by W3.4) and `n_stops` (stop exits) separately.
11. **Exposure-matched null for long/flat specs.**
    - The window's whole |e| path is circularly shifted by a uniform 1..n-1 days, per draw.
    - It is re-costed and re-financed, with no stops and the last window day flat.
    - An always-long spec's null mean is therefore buy-and-hold. Buy-and-hold is reported beside it.
12. **Point-in-time cut on publication.**
    - Every panel frame must say when each row became known: `available_utc` for aux series, `close_utc` for bars.
    - The point-in-time check cuts at T's 21:00 UTC close on that stamp.
    - A frame with neither is refused, and a signal that joins an aux series on its value date fails the check.
13. **Financing inputs.**
    - **USD rate.** DTB3 is read from the data lane's `raw/daily/fred_DTB3.parquet` and joined on its STORED
      `available_utc`, cut before 2025-10-01 on both the value date and `available_utc`.
    - **FX and non-USD indices.** An FX pair is financed at quote minus base, from FRED OECD 3-month rates
      (`research.position.currency_rate_paths`, as-of on `available_utc`). GER40, JP225 and UK100 are financed at the
      EUR, JPY and GBP rate.
    - **Refusals.** A U_16 market without its rate frame is refused; it is never financed at the USD rate. So is a
      symbol without a triple rollover day (`research.position.triple_weekday_by_symbol`; XAUUSD Wednesday).
14. **Book scale.** After a portfolio vol scale (026, 057), every column is re-clipped to +-2x.
15. **Cost cell.** The 21:00 UTC fill falls in the Asia session, so the C1 point costs come from the cost of record's
    `icm` / `asia` cell (cor-2026-10-31.2): round trip $0.204 = 0.51 bp, and overshoot $0.10 = 0.25 bp at the 4,000
    reference.
16. **Report-only spread sensitivity (venue S).**
    - Venue S is venue A with each fill charged max(point RT, the measured spread at the fill minute), i.e. the first
      Dukascopy 1m bar after the 21:00 close.
    - Measured on XAUUSD 1m (a cost measurement, no outcome), the median is **3.3 bp in 2010-17 and 2.4 bp in
      2018-25**, by year from 1.95 (2024) to 4.89 (2010). The review quoted 2.0-2.6 bp; that matches 2018-25, while
      2010-17 is wider.
    - S never gates.
17. **Read once.**
    - TEST-OLD and CONFIRM need `--frozen-prereg`, tracked at HEAD in a clean tree. The pre-registration's blob sha is
      recorded in the output.
    - Each (spec, window) is claimed in `research.position.read_registry` before the read. A second claim refuses the
      whole batch, and a crashed read still counts.
    - BH-FDR (q 0.10) runs only at CONFIRM, over the confirm-eligible ids of the committed, re-stamped section 3
      table. No other window carries a BH verdict.
18. **Y_learn.**
    - The engine emits Y_learn per spec from bar dates only: per calendar year of LEARN-OLD and LEARN, the days the
      book is live (at least `min_live` markets past their 260-day warm-up) over 252, capped at 1.
    - The section 3 re-stamp uses it in P_explore = Phi(SR_prior x sqrt(Y_learn) - 2).

## What this does not change
- K, the 16 POS-1 ids, every explore, confirm and TEST-OLD threshold, the windows, the 3 x ATR(20) stop, the 10%/yr
  vol target and the venue that gates (A).
- Any change to a ruling above is a new amendment committed before the run.
