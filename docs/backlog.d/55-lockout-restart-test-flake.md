---
number: 55
title: 'test_lockout_survives_a_restart: make it reliable under load'
stage: Platform (CI) · SECURE
size: S
status: 'done 2026-10-16 (29021ac + 71a7889; code-reviewer APPROVED 2026-10-16, tests-only diff, 10 clean runs; merged into the integration branch at 3092923; ships in the next PR after #79) · accountable: test-engineer'
---
- **55. test_lockout_survives_a_restart: make it reliable under load (ci follow-up from the A15 lane,
  2026-10-12).** Value: `tests/test_auth_security.py::test_lockout_survives_a_restart` failed once in a full
  `pytest -m "not integration" -n 4` run with the load average near 20 ("DID NOT RAISE PermissionError" on the
  second AuthStore's login). The same file passed alone (29 passed), and the change under test did not touch
  goldbot/api/auth.py. A flaky security test hides a real lockout regression. Likely cause, not yet confirmed: the
  test opens a second `AuthStore` on the same aux.db while the first may still hold a write, and under load the
  failure rows are not visible to it within the `busy_timeout`. A second candidate is the lockout window: five slow
  password hashes under load may push the first failure out of the window. Do: reproduce under load (for example
  `pytest -n 8 --count 50` with `stress` running), then confirm which cause it is. Then either give the fixture's
  stores a longer busy timeout, or retry the second login, or freeze the clock for the window. Never loosen the
  lockout itself. Acceptance: 200 repeated runs under load pass; the test still fails when the lockout is not
  persisted (checked by temporarily disabling the write).
  Sweep 2026-10-16 (product owner): accountable `test-engineer`, due 2026-10-23. Acceptance as above (200 runs under load; still fails when the lockout write is disabled).
  Done 2026-10-16 (test-engineer). Root cause: the second candidate, in a more general form. The lockout prunes
  `auth_failures` by the wall clock, so any run where 15 minutes of wall time passed mid-test (a paused VM or a
  starved worker, not only slow hashes) dropped the five failures and let the sign-in through. The first candidate
  is ruled out: a lock held past `busy_timeout` raises `database is locked`, it never lets a sign-in through. Fixed
  by freezing the clock: the `lockout_clock` fixture in tests/conftest.py, with the restart moved to 1 s before the
  window ends. Neither the lockout nor its timeout was loosened, and nothing is retried. Evidence: the old test fails
  20 of 20 with the clock running 10,000 times faster than real time; the new one passes 200 of 200 that way and 200
  of 200 under CPU stress. It still fails when the failure write is disabled, and also when the window is halved.
  Follow-up 71a7889 (2026-10-16): the remaining lockout tests (`tests/test_api.py`,
  `tests/test_api_code_lockout.py`) pin the lockout clock too and prove both window edges. Code-reviewer APPROVED
  (tests-only diff, 10 clean runs); merged at 3092923.
  **Acceptance scorecard 2026-10-16 (product owner, AGENT_STANDARDS 7 checks):** (1) criteria: yes, 200 of 200 under CPU stress and with the accelerated clock, and the test still fails
  with the failure write disabled (test-engineer's evidence above); the lockout suites re-run 2026-10-16 at 3092923:
  pass. (2) DoD: yes. (3) S1-S12: yes (no production change). (4) code-reviewer APPROVED; no other reviewer routed
  (tests only). (5) CI/merged: **no, on the integration branch, not in PR #79; goes in the next PR**. (6) PR text:
  n/a (no owner-visible change). (7) role and date: yes (test-engineer, due 2026-10-23, met). Accepted, conditional
  on the next PR merging.
