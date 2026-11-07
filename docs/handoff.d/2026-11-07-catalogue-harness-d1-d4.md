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
  Quant re-review (NO-GO until C7, C9, C10; eval1_signoff_1107) applied on the same branch:
  C7 the risk floor is decided once at the signal bar on the order price (market: the decision close); R is measured
  on that order risk; a filled order is never dropped (a fill through its stop exits at once as -1 R, past its
  target at once at the fill); a trade with no random-time twin bar is kept, control NaN, left out of the excess only.
  C10 (D5) a stop/limit entry filled inside a bar is filled and walked on gold 1m bars (`data.load_minutes`,
  `dispatcher.get_minutes`; random-side twins too); stop-entry fills pay the stop overshoot; a unit with a stop/limit
  entry on an instrument without 1m bars is excluded whole and listed (`excluded_no_1m`).
  C9 the random-walk guard pools 3 samples (<= 8 walks each) per pair: fail if |t| >= 3.5, or |mean| >= 0.03 R with
  |t| >= 2, on the first 3 samples and on a retest of 3 fresh ones; `goldbot/research/catalogue/rw_guard.json` lists
  the 9 sparse units (< 30 trades on 24 walks) and the 3 failed pairs (B08-V1 both controls, C04-V1 random_time;
  strict xfail, both flip sign between test and retest); `run.py` leaves those units out. Result: 311 passed,
  18 skipped (sparse), 3 xfailed; B04 (D5) passes. No real-data window read or re-run; WS-3 stays closed (prereg 8).
