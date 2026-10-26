- Engine lane E1 (2026-10-26): live == history for the new arms through the engine's own pass (`exploration.step`).
  F1A/F1C/F2A/F2C on the engine's 1m memory queue exactly history's candidates (tests/test_exploration_live_parity.py).
  The 1m memory (`EngineConfig.max_bars_in_memory`) is 90 000 (was 60 000; ~62 trading days) so fakey L0's 4h box
  set (~80 000 minutes) reproduces history; `LOOKBACK_MAX_1M` follows it (a test holds them equal). WFRI declares a
  1,600-bar lookback (its ATR14(4h) stop was ~15% off history on the default 200 bars). The pass resamples once per
  timeframe (`_shared_bars`, exact per lookback, tested). Measured at the worst close (00:00 UTC, every timeframe),
  real 2025 bars, F1/F2/WFRI added to every host: icm-demo 25 arms 1.69 s on the Mac (x3 = 5.1 s on the brain,
  budget 10 s), vantage-demo 1.44 s, icm-probe 0.15 s; before the change 1.68 / 1.34 / 0.15 s at the same load. RAM:
  the 1m frame 7.4 -> 11.1 MB, a close's transient peak 25 -> 32 MB per engine.
  WFRI: `crosses_friday` lets an entry hold across the Friday cut only for a rule that declares the cut its exit
  (`arm_rules_clock_box.FOLLOWS_FRIDAY_CUT` = friday_long), never into the weekend (refused at the server's Saturday
  or after the cut); the cut itself (`Engine._weekend_rule`) is unchanged and closes a losing WFRI. For such a rule the
  cut's loser close and its tightened stop are the arm's own exit, not a censoring (`_censored`).
  Stop-band skips: `arm_rules.screened` returns the candidates the band dropped; the engine journals
  `exploration_skipped:stop_lt_floor|stop_gt_cap` with the arm's propensity and records each blocked (mu 0) through
  `record_candidate`. For trading-safety review (order path: `crosses_friday`) and quant review (censoring, lookbacks).
