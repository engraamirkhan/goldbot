- **MT5 adapter waits up to 60 s for the terminal to start (W1-1 plan, item A3; implementer).**
  `goldbot/execution/mt5_adapter.py` passes `timeout=MT5_INIT_TIMEOUT_MS` (60 000 ms) to `mt5.initialize`, so a
  terminal under Wine that is slow on first login or a history sync no longer fails the bridge start on the package's
  short default. Test: `tests/test_mt5_init.py::test_initialize_gets_the_terminal_path_and_a_60_s_timeout`. Bridge
  side: takes effect only after the MT5 box is updated (`sudo goldbot-deploy latest` on it, RUNBOOK "Updating the VPS
  to a new version"). Review: code-reviewer, trading-safety-reviewer (bridge start path), not yet recorded.
