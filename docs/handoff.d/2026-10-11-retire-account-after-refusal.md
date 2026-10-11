- Clearing an `engine_startup_refused` halt (2026-10-11, re-verify follow-up of LOW-D). A test now shows that a
  refused engine file, followed by a successful start and one engine state write, gives a supervisor evaluation
  without `engine_startup_refused` (the engine's first write replaces the file). The RUNBOOK's "Engines are running
  but block every entry" table has the `engine_startup_refused` row: fix the cause and restart that engine, or retire
  the account (stop and disable its engine, move `state/engine_<account>.json` aside, only with no open position).
  The dashboard's owner text for the code ends "or retire that account". Tests:
  tests/test_startup_error_exposure.py.
