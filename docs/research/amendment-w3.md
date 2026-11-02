# Amendment W3: power gate, position strategies, financing by date

Adopted 2026-11-02 by the research director (rethink lead). It is frozen before any position-engine run and before any
POS-1 bar is read.

W3 **adds to** amendments W2 (windows, TEST-OLD) and C1, both already adopted. C1 brought:
- point costs in bps;
- measured swap, with shorts credited;
- the random-side null;
- the ensemble stage;
- BH-FDR at confirm, with no DSR term.

W3 does not restate or loosen those. Where W3 is silent, W2 and C1 govern. The live-money gate stays owner-only. The
holdout (2025-10-01 onward) stays sealed. Rationale and numbers: `docs/research/rethink/strategy.md`.

## W3.1 Power gate (every pre-registration from now on)
1. **Prior Sharpe (SR_prior), annual, net, written before any data is read.** Take it from the evidence grade:
   - **Peer-reviewed:** the published figure for that rule class. Default 0.4 for a single-market trend rule (Hurst,
     Ooi and Pedersen 2017, average across 67 markets).
   - **Practitioner-documented:** 0.75 x the nearest peer-reviewed class.
   - **Folklore and own-data without an out-of-sample result:** 0.
   - **Blends and books:** SR_prior = SR_member x sqrt(N / (1 + (N - 1) rho)), with rho written down from the
     literature or set by default:
     - 0.6 among trend rules on one market;
     - 0.5 among precious metals;
     - 0.15 across a diversified CFD universe;
     - 0.3 between trend and macro or event signals on one market.
2. **Power, computed from Y_learn.** Y_learn is the number of LEARN years in which the spec can trade: 2003-05 plus
   2010-17 under W2, at most 11. Years with missing data or warm-up do not count.
   - Explore power is P_explore = Phi(SR_prior x sqrt(Y_learn) - 2.0).
   - Chain power is P_chain, simulated as in rethink Appendix C, with confirm over 7.75 years and TEST-OLD over 3.9
     years. P_chain is reported but does not gate.
3. **Gate.** A spec with **P_explore >= 0.50** is *confirm-eligible*.
   - A spec below the gate may still be run in explore as a *member*. It counts in K, and its result is reported,
     but it can only reach confirm inside a blend or C1 ensemble that is itself confirm-eligible.
   - This keeps low-power tests out of the BH set m, where they would dilute power for the others.
   - The fix for a low-power line is more history or pooling across markets, never a lower bar.
4. **Each pre-registration tabulates** SR_prior with its source, Y_learn, P_explore, P_chain and eligibility for
   every spec.

## W3.2 Position strategies: continuous target exposure, read on daily P&L
- **Scope.** A position strategy is any spec whose decision tf is 1d or 1w and whose output is a target exposure
  e_t in [-cap, +cap] (cap 2) per instrument. W3.2 replaces R-bracket accounting for these specs. Bracket specs on 1d
  or 1w keep R accounting, but take the sample rule in W3.4.
- **Clock.**
  - The decision is made on the 1d bar that closes at 21:00 UTC, with bars built from mid 1m data.
  - The fill is at the next available 1m open, at the C1 point cost.
  - The weekly decision is taken at Friday's 21:00 UTC close.
  - Every input is joined with `asof_join` on `available_utc` at or before the decision.
- **Daily P&L.** P&L is computed per instrument and summed for a book, in units of starting equity, with no
  compounding:

  `pnl_d = e_{d-1} x r_d - costs_d - financing_d`

  - r_d is the instrument's mid return from fill to fill.
  - costs_d = RT_point_bps / 2 x |delta e| on each rebalance. Disaster-stop exits also pay the C1 overshoot.
- **Disaster stop.** Every position spec, including those whose rule says "stop: none", carries one at
  3 x ATR(20, 1d) from the last rebalance price, so that research tests what demo would trade.
  - If it is hit, exposure is 0 until the next daily decision.
  - On that decision, the exposure is restored only if the signal still says so.
- **No-trade band.** Use the band the spec states; if it states none, 10% of the target. Below the band, no trade.
- **Pass statistics.** These replace day-summed R for position specs; every other C1 explore and confirm leg keeps
  its threshold.
  - **Primary statistic:** the mean of pnl_d over all calendar trading days in the window, flat days included.
  - **t-statistic:** a stationary day-block bootstrap with mean block length 20 days and 4,000 resamples. The
    annual Sharpe is reported with it.
  - **Years positive:** the C1 rule of at least 5 of 8 years positive becomes at least 60% of available years
    positive, rounded up.
  - **Drift-adjusted gross:** sum of e_{d-1} x (r_d - mu_w). Here mu_w is the instrument's mean daily return in
    that window; it is used for this diagnostic only, never inside a signal.
- **Random-side null for position specs (the C1 null, transposed).**
  - A *holding episode* is a maximal run of days with the same nonzero sign of e.
  - Each draw keeps every episode's start, end and |e| path, and gives the episode an independent random sign.
    Draws are re-costed and re-financed with the flipped signs.
  - There are 1,000 draws, with seed 20261102.
  - Excess = actual net minus the mean null net. The C1 rule (excess >= 0) applies.
  - Long-only specs use an exposure-matched null. Each episode is shifted to a uniformly random start in the same
    window, with the same length and |e| path. Buy-and-hold is reported beside it.
- **Long and short legs** are reported separately, as in C1.

## W3.3 Financing by date (all windows, all instruments)
- **Charge.** Each rollover night charges or credits |notional| x rate_side(d) / 365, with the broker's triple day
  (Wednesday for ICM XAUUSD).
  - rate_long(d) = DTB3(d) + m_long. Longs pay.
  - rate_short(d) = DTB3(d) - m_short, credited to shorts. If it is negative, the short pays.
  - DTB3 is the FRED 3-month T-bill, `asof_join` on `available_utc`, published the day after its observation date.
- **Markups**, from the measured ICM broker terms (swap version `swap-icm-2026-11-02`, long -52.22 and short +36.00
  USD/lot/night):
  - m_long = annualised long swap at the XAUUSD mid at `measured_utc`, minus DTB3 as of `measured_utc`.
  - m_short = DTB3 minus the annualised short credit.
  - The data lane derives both and records them with their inputs.
  - Indicative value at the C1 reference price of 4,000 and DTB3 = 4.02%: m_long = 4.76 - 4.02 = **0.74%/yr** and
    m_short = 4.02 - 3.29 = **0.74%/yr**.
  - At the 2010-17 mean DTB3 of 0.21%, longs therefore pay about **0.95%/yr** and shorts about **0.53%/yr**. The
    shipped dollar swap charged longs about 17%/yr at 2010-17 prices.
- **Other symbols.**
  - Each uses its own measured ICM terms, derived the same way against the relevant rates.
  - For FX, the 3-month rate differential of the two currencies comes from FRED's OECD 3-month interbank series
    (monthly, `asof_join`). The measured markups are added on both sides.
  - Any symbol without measured terms uses a markup of 2.5%/yr each side (conservative).
  - Index CFDs use price returns. Dividend adjustments are omitted (a stated limitation, conservative for net-long
    books).
- **Venue scenarios.**
  - **(A) ICM CFD, as above.** This is the **gating** scenario.
  - Reported only, never gated: **(B) UK gold ETC.** Long/flat only, 0.12%/yr fee, no financing, round trip = C1
    point RT + 5 bp.
  - Reported only, never gated: **(C) CME futures.** Long pays DTB3 and short earns DTB3, with no markup. The round
    trip is the C1 point RT plus $2.00/oz a side for 1OZ, plus one round trip at each monthly roll.

## W3.4 Sample rule for 1d and 1w specs
For any spec whose decision tf is 1d or 1w, bracket or position, C1's n >= 100 (60 for 4h/1d) is replaced by both of:
- **n >= 30 round trips.** A round trip is an entry from flat or a sign change, closed by a return to flat or a flip.
- **>= 500 days in market** (days with e != 0).

Both counts are taken per window: explore, confirm and TEST-OLD. A spec that fails them is FAIL-COUNT and still
counts in K.

## W3.5 Blends count as one trial
- A blend is a combination of signals or instruments **whose weights and rules are written in the
  pre-registration** before any data is read. It enters K as **one** trial.
- Components run on their own (as singles or members) add their own trials. Components that are only computed
  inside the blend add none.
- Blend weights are never fitted. They must be equal weight, equal risk (inverse trailing volatility) or a stated
  literature weight. Any change is a new trial.
- C1's ensemble stage (composites of explore passers) is unchanged and separate.

## W3.6 Volatility targeting is for sizing only
- **Program-wide defaults, fixed and never a free parameter:**
  - target 10%/yr per instrument, unless the spec states a portfolio target;
  - EWMA volatility with a 60-day span, lagged one day;
  - cap 2x.
- Two specs that differ only in the vol-target level or window are **not** allowed. A spec that changes the *time
  pattern* of exposure through volatility (for example RT-STRATEGY-018 or -045) is a distinct trial that states its
  rule.
- **Pass statistics** (t, Sharpe, years positive, null excess) are invariant to the scalar target, and gates are read
  on them.
- **Demo sizing** is min(vol-target exposure, the exposure at which the 3 x ATR disaster stop costs the RiskGate
  per-trade cap), rounded down to the lot step. RiskGate, the 1% cap and the daily loss limit are unchanged.

## W3.7 Demo for qualified position strategies
- A qualifier (explore, confirm and TEST-OLD all passed) gets a position arm on icm-demo within 48 hours. The arm
  rebalances once a day at the decision clock.
- A random-side control arm runs beside it, using the W3.2 episode-sign randomisation on the live schedule.
- **Cost and fill kill**, checked after 20 rebalances or 30 calendar days: kill if realised cost plus financing
  exceeds 1.5 x the W3.3 model.
- **Performance** is read only after 250 trading days. Before that the arm is a cost/fill check, not a verdict.
