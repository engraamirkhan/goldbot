- Gates on release candidate ab671be (W1-1 bridge smoke on top of PR #84), test-engineer, 2026-10-17. `GATES_WORKERS=4 scripts/gates.sh --web` (total 377 s), then e2e. All green:

  | Gate | Result |
  |---|---|
  | dry_run | PASS lookahead check: 0 of 240 columns use future data [] |
  | integration | PASS 90 passed, 1 skipped, 1 warning in 375.79s (0:06:15) |
  | mypy | PASS Success: no issues found in 369 source files |
  | pre_commit | PASS |
  | ruff | PASS All checks passed! |
  | unit | PASS 5662 passed in 176.07s (0:02:56) |
  | web | PASS Tests 142 passed (142) (lint, typecheck, build included in the gate) |
  | e2e | PASS 19 passed (7.5m), `E2E_PORT=8890` because another lane held 8790; the teardown CancelledError trace in the log is graceful-shutdown noise after the pass |
