# Sprint-200 lane `patterns`: price patterns with managed exits (pre-registration)

Principal trade management researcher, 2026-11-02. Owner directive 2026-11-02 (sprint-200 shared brief). Written and
committed **before any bar of 2010+ is read for this lane**; the screen code follows in a separate commit, also before
the run. One look at explore (2010-01-01..2017-12-31), one look at confirm (2018-01-01..2025-09-30) for explore-passers
only. Nothing at or after 2025-10-01 is read (loader cut + assertion). No tuning after the look; no variant beyond
the 34 rows below is run.

## 0. What already failed, and how this differs

- **R-09** retired candlesticks/classic chart tools as standalone rules (no peer-reviewed support net of costs). This
  lane is the owner's explicit sprint request; it runs as a dated exception under the sprint's pass bar, not as a
  reversal of R-09. The prior is low and stated (section 6).
- **H-43 inside bar / H-44 fakey** (4h, daily trend, level, next-box target): never traded (counts below the floor,
  0 trials). Rows 11-14 are therefore not re-tests of a failed trial; they differ in timeframe and context and use
  managed exits.
- **Discovery v1/v2** carried `candle_class` as one bulk feature among hundreds inside an ML filter; neither tested a
  candle rule paired with a managed exit. **Retail setups RS-1..3** (ICT) and **H-82 hb-exits** (wider targets on the
  scalp arms' entries moved net R by < 0.01 R against control) are not re-run. Lesson taken from H-82: an exit cannot
  create an entry edge, so every row here is read against a random-entry control under the **same** exit.
- Asia-range sweep (#88/H-76), round-number magnet (G-7), VWAP bands (R-01) and box+MFI (H-80) are not used as
  contexts.

## 1. Data, bars, point in time

- 1m Dukascopy `raw/xauusd_1m_dukascopy_<year>.parquet` via `goldbot.data.release.read_bar_files` (error-flagged bars
  dropped). Mid = (bid + ask) / 2 for o/h/l/c. Explore loads 2010..2017 and **cuts at 2018-01-01** (a trade still open
  at the cut is censored and dropped, so explore never reads a confirm bar). Confirm loads 2017-01-01..2025-09-30
  (2017 only as indicator warm-up, no entries) and cuts at 2025-10-01 (asserted); censored trades are dropped.
- TF bars (15m, 1h, 4h): 1m mid resampled left-labelled, closed-left, empty bars dropped. Bar i is known at its close
  `open_i + tf`. Decision at that close; **entry at the open of the first 1m bar at or after the close** (send delay 0
  for 15m+ arms, `arms.send_delay_s`).
- Indicators at bar i use bars <= i only. `A` = ATR(14), the simple mean of true range over bars i-14..i-1 (the signal
  bar's own range is excluded from sizing). EMA20/50/200 of close (ewm span, adjust=False) through bar i.
- Trend up `TU`: EMA50_i > EMA200_i and c_i > EMA50_i; trend down `TD` is the mirror. "trend" in a row = long only in
  TU, short only in TD.
- `PDH/PDL`: high/low of the previous UTC calendar day (complete day of 1m bars). `PWH/PWL`: the previous Mon-Sun UTC
  week.
- At-level (long): l_i <= PDL + 0.25 A and c_i > PDL; short: h_i >= PDH - 0.25 A and c_i < PDH (PWH/PWL for row 34).
- Windows on the decision close, UTC: `LO` 07:00 <= t < 10:00; `NYO` 12:30 <= t < 15:30.
- Candle parts: body = |c-o|, range = h-l, upper wick = h - max(o,c), lower wick = min(o,c) - l.

## 2. Patterns (long side; the short side is the exact mirror)

| Code | Rule at bar i (long) |
|---|---|
| ENG | c_{i-1} < o_{i-1}; c_i > o_i; o_i <= c_{i-1}; c_i >= o_{i-1}; body_i > body_{i-1}; range_i >= 0.5 A |
| PIN | lower wick >= 0.6 range; body <= 0.3 range; range >= 0.8 A |
| IBB | bar i-1 inside bar i-2 (h_{i-1} <= h_{i-2}, l_{i-1} >= l_{i-2}); c_i > h_{i-2} |
| FAKEY | bar i-1 inside bar i-2; l_i < l_{i-2}; l_{i-2} < c_i < h_{i-2}; c_i > o_i |
| OB | h_i > h_{i-1}; l_i < l_{i-1}; c_i >= l_i + 0.75 range; range >= 1.0 A |
| R3 | c_{i-2} < o_{i-2}; l_{i-1} < l_{i-2}; c_i > h_{i-1}; c_i > o_i |
| MARU | c_i > o_i; body >= 0.8 range; range >= 1.5 A |
| MARU-F | fade: a bull MARU with range >= 2.5 A -> SHORT (bear -> long) |
| 3S | bars i-2, i-1, i bullish; c_i > c_{i-1} > c_{i-2}; each body >= 0.5 of its range; each c >= h - 0.3 range |
| NRB | bar i-1 is inside bar i-2 and its range is the smallest of the last N bars (N 7 on 1h, 4 on 4h); c_i > h_{i-1} |
| PIN>ENG | PIN (long) at bar i-1 and ENG (long) at bar i |
| MARU-PD | MARU (long) with c_i > PDH and c_{i-1} <= PDH |

## 3. Stops and exits

The stop distance `d` is fixed at the decision; the stop sits at entry -/+ d (long/short) and R = d.
- `ST` structure: long stop level = min(low of the pattern's bars) - 0.1 A, d = c_i - level. IBB: the mother bar's
  (i-2) opposite side - 0.1 A. NRB: the NR bar's (i-1) opposite side - 0.1 A. FAKEY: l_i - 0.1 A. Pattern bars: ENG,
  OB, PIN, MARU = i-1..i (ENG, OB) or i (PIN, MARU); R3, 3S = i-2..i; PIN>ENG = i-1..i.
- `Ak`: d = k x A.
- Every row: skip the setup when d < 0.3 A, d > 3 A, or d < $1.00 (the round trip of ~0.23-0.27 would exceed ~0.25 R).
- One position at a time per strategy (a signal while the previous trade is still open is skipped).

Exits (H = time cap in TF bars: exit at the close of the last 1m bar before the close of bar i+H). Stops and targets are
checked on 1m mid bars; **stop first when stop and target fall in one minute**; a 1m open beyond the stop exits at that
open (gap); a target exits at the target price. Trailed stops update at TF bar closes (the engine's cadence) and apply
from the first minute after that close; break-even triggers intrabar on the 1m high/low and applies from the next
minute.
- `CHm` chandelier: after each TF close k > i, stop = max(stop, highest high of bars i+1..k - m x A_entry). No target.
- `ATm` ATR trail from close: stop = max(stop, c_k - m x A_entry). No target.
- `BE>T` break-even: once +1 R is touched, stop -> entry; target T R.
- `PT` partial: 50% closes at +1 R (limit); the rest moves its stop to entry once +1 R is touched, then trails CH3;
  cap H. Needs 0.02 lot to deploy (0.01 min lot cannot be split): flagged if it survives.
- `FT T`: fixed target T R. `TS`: time stop only (initial stop + cap). `VT m`: target at m x A_entry from entry.
- `MR`: target = |c_i - EMA20_i| from entry (reversion to the mean); skip when that is < 0.5 R.

## 4. The 34 strategies (each counts 1 in K)

Mechanism key and evidence grade. **rej** = a rejection candle shows a failed auction (liquidity taken, price returned),
which in a trending liquid market tends to resume the dominant flow (Bulkowski 2008, *Encyclopedia of Candlestick
Charts*, Wiley, US stocks, gross, single-source practitioner study; counter-evidence: Marshall, Young & Rose 2006,
J. Banking & Finance 30(8), DJIA stocks 1992-2002, no value after costs). **brk** = volatility contraction then
expansion (Crabel 1990, *Day Trading with Short Term Price Patterns and Opening Range Breakout*, S&P futures, gross,
practitioner). **mom** = large directional bars carry order-flow imbalance that persists intraday (Gao, Han, Li & Zhou
2018, J. Financial Economics 129(2), SPY 1993-2013, intraday momentum, gross; an adjacent mechanism, not a candle study).
**lvl** = prior day/week extremes are where stop and resting orders cluster (Osler 2003, J. Finance 58(5), FX
stop-loss/take-profit order clustering, bank order data). **exh** = an outsized bar exhausts short-term liquidity and
partially reverts (no gold study; single-source intuition). Exits: chandelier (LeBeau & Lucas 1992, *Technical
Traders Guide to Computer Analysis of the Futures Market*, practitioner, gross); stop-loss rules add value only under
serial correlation (Kaminski & Lo 2014, J. Financial Markets 18, theory + US equities/bonds, gross; extended with
costs by Lo & Remorov 2017, J. Financial Markets 34). None is a gold study net of costs; only the stop-loss/serial
correlation result is replicated (two papers, same group).

| ID | Pattern | Context | TF | Stop | Exit | H | Mechanism |
|---|---|---|---|---|---|---|---|
| S200-PATTERNS-01 | ENG | trend | 1h | ST | CH3 | 48 | rej + trend |
| S200-PATTERNS-02 | ENG | trend | 1h | ST | BE>2 | 24 | rej + trend |
| S200-PATTERNS-03 | ENG | at PDH/PDL | 1h | ST | FT 2 | 24 | rej + lvl |
| S200-PATTERNS-04 | ENG | trend | 4h | ST | CH3 | 30 | rej + trend |
| S200-PATTERNS-05 | ENG | trend, LO or NYO | 15m | A1.0 | TS | 8 | rej at session flow |
| S200-PATTERNS-06 | PIN | trend, l_i <= EMA20_i < c_i (short: h_i >= EMA20_i > c_i) | 1h | ST | CH3 | 48 | rej at dynamic support |
| S200-PATTERNS-07 | PIN | at PDH/PDL | 1h | ST | BE>2 | 24 | rej + lvl |
| S200-PATTERNS-08 | PIN | trend | 4h | ST | PT | 30 | rej + trend |
| S200-PATTERNS-09 | PIN | LO or NYO | 15m | A1.0 | FT 1.5 | 16 | rej at session flow |
| S200-PATTERNS-10 | PIN | stretch: long c_i < EMA20_i - 2.5 A (short mirror) | 1h | ST | MR | 12 | exh |
| S200-PATTERNS-11 | IBB | trend | 1h | ST (mother) | CH3 | 48 | brk + trend |
| S200-PATTERNS-12 | IBB | trend | 4h | ST (mother) | PT | 30 | brk + trend |
| S200-PATTERNS-13 | IBB | LO | 15m | ST (mother) | FT 2 | 16 | brk at London open |
| S200-PATTERNS-14 | FAKEY | any | 1h | ST | FT 2 | 24 | rej (failed breakout) |
| S200-PATTERNS-15 | OB | trend | 1h | ST | CH3 | 48 | mom + trend |
| S200-PATTERNS-16 | OB | at PDH/PDL | 1h | ST | BE>2 | 24 | rej + lvl |
| S200-PATTERNS-17 | OB | trend | 4h | A1.5 | TS | 6 | mom + trend |
| S200-PATTERNS-18 | R3 | at PDH/PDL | 1h | ST | BE>3 | 24 | rej + lvl |
| S200-PATTERNS-19 | R3 | trend | 1h | ST | CH3 | 48 | pullback end in trend |
| S200-PATTERNS-20 | R3 | trend | 4h | ST | PT | 30 | pullback end in trend |
| S200-PATTERNS-21 | R3 | NYO | 15m | A1.0 | TS | 8 | rej at NY flow |
| S200-PATTERNS-22 | MARU | any | 1h | A1.0 | CH3 | 48 | mom |
| S200-PATTERNS-23 | MARU | any | 1h | A1.5 | TS | 4 | mom (burst) |
| S200-PATTERNS-24 | MARU | trend | 4h | A1.5 | PT | 30 | mom + trend |
| S200-PATTERNS-25 | MARU | LO | 15m | A1.0 | AT2 | 16 | mom at London open |
| S200-PATTERNS-26 | MARU-PD | any | 1h | A1.0 | CH3 | 48 | mom + lvl break |
| S200-PATTERNS-27 | MARU-F | any | 1h | A1.0 | FT 1 | 4 | exh |
| S200-PATTERNS-28 | 3S | trend | 1h | A1.5 | CH3 | 48 | mom + trend |
| S200-PATTERNS-29 | 3S | any | 4h | A1.5 | TS | 12 | mom |
| S200-PATTERNS-30 | NRB (N 7) | trend | 1h | ST (NR bar) | CH3 | 48 | brk + trend |
| S200-PATTERNS-31 | NRB (N 4) | trend | 4h | ST (NR bar) | BE>3 | 30 | brk + trend |
| S200-PATTERNS-32 | PIN>ENG | any | 1h | ST | CH3 | 48 | double rejection |
| S200-PATTERNS-33 | ENG | daily pullback: long when the previous UTC day's close > EMA20 of daily closes (through that day) and c_{i-1} < EMA20_{i-1} (1h); short mirror | 1h | ST | VT 2 | 24 | rej + higher-TF trend |
| S200-PATTERNS-34 | PIN | at PWH/PWL | 4h | ST | CH3 | 30 | rej + weekly lvl |

## 5. Costs, control, statistics, pass rule

- **Costs**: `config/cost_of_record.yaml` version `cor-2026-10-31.2` pinned (`ArmCosts.require_version`), broker ICM,
  charged with `goldbot.research.arms.screen_net_r` by the entry's `DEFAULT_SESSIONS` label: round trip = max(0.23,
  the cell's round-trip upper 90% bound) (asia 0.272, london 0.23, newyork 0.23; outside every session the dearest),
  stop-exit overshoot = max(0.12, the cell's overshoot upper 90% bound) (0.136 / 0.12 / 0.196) on **every** stop exit,
  including moved and trailed stops (PT: half the overshoot when one half stops). Swap: `SwapSpec.shipped()` per
  rollover night held (`rollover_nights`). Stress line (descriptive only): overshoot 0.70 on stop exits.
- **Matched random control** (per trade, 4 draws, seed 20261102 hashed with the strategy id): a TF bar close drawn
  uniformly from the same UTC day and the same session label as the trade's decision close, excluding the signal bar
  (if none, the same UTC day in any session; if none, no control for that trade); same side, same d, same A_entry, same
  target distance, same exit and cap, entered at the next 1m open after that close. Control mean = the mean over every
  control draw.
- **Explore pass** (all of): n >= 100; net mean R > 0 with t >= 2.0 (t = mean / sd x sqrt n, per trade); net mean R
  minus control net mean R >= 0.05 R.
- **Confirm pass** (explore-passers only, all of): net mean R > 0; t >= 2.0; deflated Sharpe > 0, read as the
  per-trade Sharpe above the expected maximum of K unskilled trials (`metrics.DsrInputs`: SR > SR0, i.e. the DSR
  probability > 0.5) at K = 3,029 + 6 x 34 = **3,233** (an upper bound on the sprint's trials; the output keeps the DSR
  inputs so the orchestrator re-reads it at the final K); yearly net mean R > 0 in >= 6 of the 8 confirm years
  (2018..2025, 2025 to September).
- Survivors (confirm-pass) become exploration arms on demo (min lot, RiskGate, 1% cap, paired random control) under
  brief rule 7. PT rows need 0.02 lot: flagged as an owner sizing item, not assumed.

## 6. Reading rule (written before any data)

- A row that fails explore is retired for this sprint; no neighbour (other TF, multiplier, context) is run.
- A row that passes explore but fails confirm is retired, final.
- "Beats control" measures the entry. The exit is identical on both sides, so a pass is an entry-timing effect under
  that exit, not an exit effect (the H-82 lesson).
- Expectation: candle rules have no replicated net-of-cost support (Marshall et al. 2006 found none on the DJIA). Prior
  of an explore pass ~5% per row (about 1-2 of 34 by chance at t >= 2 alone); confirm pass < 1% per row. **The most
  likely result is 0 survivors**, and it will be reported as such.

## 7. Trial cost

K +34 (rows 01-34). Compute: one local run on 2 worker processes; confirm only for explore-passers. Outputs go to
`~/tmp/sprint200_patterns/` (nothing market-derived in the repo beyond the aggregates in the read). Code:
`scripts/sprint200/patterns_screen.py`, committed before the run.
