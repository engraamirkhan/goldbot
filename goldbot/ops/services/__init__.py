"""The long-running services `python -m goldbot.ops.run <service>` starts (NSSM on the VPS, systemd on Linux), one
module each; goldbot/ops/run.py re-exports every name they had there.

  engine.py      run_engine and the helpers its loop runs: session refresh, bridge compatibility, engine_pass,
                 refuse_start, start_loop_watch, paper_stand_in, engine_limits
  supervisor.py  run_supervisor, supervisor_pass
  bridge.py      run_bridge (the MT5 terminal's bridge, under Wine or Windows)
  api.py         run_api (dashboard)
  webhook.py     run_webhook (TradingView)
  scheduler.py   run_scheduler and its read-only job brokers and staff agents
  telegram.py    run_telegram
  news.py        run_news
  _base.py       the shared logger and `resolve` / `patched` (goldbot.ops.run stays the patch point)

Adding a service: write `run_<name>` in its own module here (lazy imports only: importing goldbot.ops.run loads no
third-party package), register its command in goldbot/ops/commands/services.py and re-export it from
goldbot/ops/run.py (import and `__all__`; tests/test_ops_run_registry.py enforces both).
"""
