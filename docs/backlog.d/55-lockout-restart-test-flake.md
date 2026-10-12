---
number: 55
rank: 47
title: 'test_lockout_survives_a_restart: make it reliable under load'
stage: Platform (CI) · SECURE
size: S
status: proposed (ci follow-up, A15 lane, 2026-10-12)
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
