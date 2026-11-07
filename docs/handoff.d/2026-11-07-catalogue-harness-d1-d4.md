- Catalogue harness defects D1-D4 fixed (WS-3 audit 2026-11-07, EVAL-1 preconditions i-iv; branch
  `fix/catalogue-harness-d1-d4`, from harness b3f14388). `goldbot/research/catalogue/sim.py`:
  D1 risk floor: stop distance >= max(0.25 x ATR20 at the decision bar, 3 x round trip), else the event is not
  traded and is counted (`risk_floor`); ATR20 not yet defined = not traded; an OCO pair is placed whole or not at all.
  D2 the OCO random-side twin is no longer forced to -1 when both legs fill on one bar: it takes the opposite of
  every leg the bar touched (unbiased on a martingale). D3 the random-time twin of a short puts its target below
  entry. D4 the random-time twin starts after the fill: intraday the same time of day on one of the next 7 calendar
  days, 1d the next 20 bars, 1w the next 8 (1d/1w were +-span before, the same before-signal selection).
  `dispatcher.unit_trades` returns events, trades and risk-floor counts; `run.py` reports `n_trades`, `n_events` and
  `n_risk_floor` separately (the 53,850 read as S21-V2's n was events).
  `tests/test_catalogue_random_walk.py`: every registered unit x control on a driftless random walk with the unit's
  own bar structure, |t_excess| and |t_gross| < 2 (a pair fails only if |t| >= 2 with the same sign on 3 independent
  samples: one walk per pair would fail a correct harness by chance at ~300 pairs). On b3f14388 about 100 pairs show
  |t| >= 2 on the first sample, with t up to 41.
  Open, not fixed here (D5, found by the new test): a stop/limit entry filled inside a bar has its stop checked
  against the whole bar, including the part before the fill. B04-V0 loses -0.49 R/trade on zero-edge data (t -4 to
  -6; B04-V0/V3 strict xfail, V2 has the same bias below the replication bar). Conservative for a pass, but it fails EVAL-1 (ii) and would trip the EVAL-1 twin kill;
  B04 cannot be screened under EVAL-1 until the fill bar is resolved from finer bars (quant-reviewer).
  No real-data window read or re-run; WS-3 stays closed (prereg section 8).
