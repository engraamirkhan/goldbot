- Gates on the frozen next-PR candidate tree 626b56b (nobody editing; macOS, own venv, `GATES_WORKERS=4`, Node 22.22.3). All green. Raw output of `scripts/gates.sh --web` plus e2e:

  | Gate | Result |
  |---|---|
  | unit (`pytest -m "not integration"`) | PASS, 5194 passed in 159.33s |
  | integration (`pytest -m integration`) | PASS, 80 passed, 1 skipped, 1 warning in 382.26s |
  | dry_run | PASS, lookahead check: 0 of 240 columns use future data [] |
  | ruff | PASS, All checks passed! |
  | mypy | PASS, Success: no issues found in 354 source files |
  | pre-commit (`--all-files`) | PASS |
  | web (npm ci, lint, typecheck, vitest, build) | PASS, Tests 129 passed (129) |
  | e2e (`npm --prefix web run test:e2e`) | PASS, 19 passed (7.2m) |

  Total wall time of gates.sh: 384 s. E2E needed `PW_CHROMIUM_PATH` set to the cached chromium_headless_shell-1208 (Playwright wanted build 1248, not installed); the first attempt without it failed on browser launch only. The `CancelledError` traceback at e2e server shutdown is the websocket sleep being cancelled at teardown, not a test failure.
