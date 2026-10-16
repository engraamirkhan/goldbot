- **Gates on the frozen release candidate 3db12c8 (claude/gifted-goldberg-lcb7pl tip), 2026-10-16, all PASS.**
  `GATES_WORKERS=4 scripts/gates.sh --web` (372 s total) plus e2e, on a tree nobody edited:

  | Gate | Result |
  |---|---|
  | dry_run | PASS: lookahead check: 0 of 240 columns use future data [] |
  | integration | PASS: 80 passed, 1 skipped in 370.03s (0:06:10) |
  | mypy | PASS: Success: no issues found in 357 source files |
  | pre_commit | PASS |
  | ruff | PASS: All checks passed! |
  | unit | PASS: 5235 passed in 192.88s (0:03:12) |
  | web (lint, typecheck, unit, build) | PASS: Tests 131 passed (131) |
  | e2e (`npm --prefix web run test:e2e`) | PASS: 19 passed (7.3m); chromium_headless_shell-1208, `PYTHON` set to the worktree venv |

  Notes: e2e needs `PYTHON=<venv>/bin/python` (playwright.config.ts defaults to bare `python`, which resolved to a pyenv 3.10 without duckdb on the first try).
  dashboard.spec.ts takes 5.7 m (Playwright flagged it slow). The e2e server logs a CancelledError on shutdown (graceful-shutdown timeout on the /ws sleep, goldbot/api/app.py:795); it does not fail the run.
