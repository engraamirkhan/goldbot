- Gates on frozen release candidate 0bd0c10 (test-engineer, 2026-10-16, `GATES_WORKERS=4 scripts/gates.sh --web`, 415 s, then e2e), all green:

  | Gate | Result |
  |---|---|
  | dry_run | PASS lookahead check: 0 of 240 columns use future data [] |
  | integration | PASS 90 passed, 1 skipped, 1 warning in 413.59s (0:06:53) |
  | mypy | PASS Success: no issues found in 364 source files |
  | pre-commit | PASS |
  | ruff | PASS All checks passed! |
  | unit | PASS 5512 passed in 244.61s (0:04:04) |
  | web (lint, typecheck, vitest, build) | PASS Tests 142 passed (142) |
  | e2e (`npm --prefix web run test:e2e`, chromium_headless_shell-1208) | PASS 19 passed (7.3m); the WebServer CancelledError trace is teardown noise after the run |
