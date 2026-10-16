- **BACKLOG 65 code parts (a), (b), (d N1/N2) built; reviews pending (implementer, 2026-10-16).** (a) BACKLOG 61 L1:
  the loss budget's currency check counts only running engines (a `ts` over 60 s old is left out, the supervisor's
  own rule; an unstamped file is never assumed gone), so a removed engine's leftover file no longer keeps a
  single-currency account amber; L2: mixed currencies during any supervisor halt read red (`mixed_currencies_paused`),
  as every engine pauses then (`tests/test_account_view.py`). (b) the rollover block says "until" server midnight +
  5 min and the weekend block "opens" at the first open minute after the weekend close, on the dashboard and Telegram
  in the owner's zone (`tests/test_api_entry_status.py`, `tests/test_telegram_status.py`). (d) N1: gate 1's best
  backtest trial is chosen among each configuration's latest run only, so a passing rerun meets gate 1 and is the
  reference (`tests/test_phase_gates.py`); N2: settings with `holdout_from` but no `holdout_to` fail to load
  (`tests/test_config.py`). Needs: quant-reviewer (N1, N2), trading-safety-reviewer (L1/L2 display), code-reviewer
  (all). Not built: N3 (research-director ADR), (c) F1/F2 (W2-3).
