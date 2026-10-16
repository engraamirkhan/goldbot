- `test_lockout_survives_a_restart` no longer depends on the wall clock (2026-10-16, BACKLOG 55). Root cause: the
  lockout counts `auth_failures` rows by `time.time_ns` and prunes rows older than 15 minutes on every attempt, so a
  run where the wall clock moved 15 minutes between the first failure and the restarted sign-in (a paused VM, a
  starved worker) pruned the five failures, and the sign-in went through ("DID NOT RAISE"). A lock held on aux.db
  past `busy_timeout` was ruled out: it raises `database is locked` and never lets a sign-in through. The test now
  takes the `lockout_clock` fixture (tests/conftest.py: `time.time_ns` pinned, moved only by the test) and restarts
  the store 1 s before the window ends. That makes it stronger: it now fails when the window is halved as well as
  when the failure write is disabled. Evidence: with the lockout clock running 10,000 times faster than real time
  (a scratch pytest plugin), the old test failed 20 of 20 and the new one passed 200 of 200; it also passed 200 of
  200 under CPU stress (load average up to 256). Production code is unchanged: `_reserve` checks and counts in one
  BEGIN IMMEDIATE transaction, and no race was found.
  - Follow-up for the test-engineer: the other lockout tests (tests/test_api.py `test_lockout_after_five_failures`,
    `test_one_ip_is_limited_across_many_emails`, tests/test_api_code_lockout.py) still use the wall clock and can
    fail the same way. They can switch to `lockout_clock`.
