---
number: 59
rank: 45
title: 'Engine state keeps moving on the host clock while the market is closed'
stage: Operational resilience · RUN
size: S
status: done (lane market-closed-state-cadence, 2026-10-16; review fixes in, re-review pending)
---
- **59. Engine state keeps moving on the host clock while the market is closed.** Value: with the market closed
  (weekend, weekday daily break 20:59-22:02 UTC) the terminal repeats its last quote, so the tick stamp is frozen and
  the tick-time state cadence (`state_every_s`) never fired. `engine_<acct>.json` went stale, and health compared its
  `bridge_compat.checked_at` with the wall clock: FAIL after 5 min, a Healthchecks.io `/fail` every 2 min all
  weekend, and a FAIL/recovered pair every weekday evening, which masks a real outage. Acceptance:
  - on the live clock, and only while the session table has the market closed, the state file is rewritten every
    `state_every_s` of host time when no write happened (any write restarts it; a host clock stepped back rewrites
    it in any session; an unreadable session table never does), so `bridge_compat:<acct>` and `engine:<acct>` stay OK through a frozen-tick weekend and the
    daily break (`tests/test_closed_market_state_cadence.py`);
  - the age rules are not skipped: an engine that writes nothing still FAILs `bridge_compat` on the weekend and
    `engine` after the reopen, and a feed frozen in an OPEN session still FAILs `bridge_compat` after 5 min and
    `engine` after 45 min (review HIGH);
  - the dashboard's entry status shows only "Market closed", not "stale data", when the engine says the market is
    closed;
  - the host-clock write only republishes: no account read, no stage machine, kill switch, weekend rule or other
    exit step, and no EngineState field changes; `last_tick_age_s`, `tick_ahead_s` and `market_closed` in the file
    are restated on the host clock so they stay true;
  - the write rate stays one per `state_every_s`, with today's fsync rules; replays (tick clock) are unchanged.
