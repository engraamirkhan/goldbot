- Growth test: GS1's pyramid adds, the part of growth sprint section 3 that was deferred (a logged deviation, due
  11-03). GS1C gets the same adds. Registered as `adds: 3` on both arms in config/accounts.yaml (growth-sized arms
  only). An add goes out at each +1 R from the first unit's entry, R being the stop distance the gate set on that
  unit. Each add is a signal like any other: every engine bound, then the RiskGate. An add is sized at most at the
  first unit's lots and takes its group's arm and pot slot. Before an add is sent, every earlier stop moves to the
  newest add's 1 R stop, so the group never loses more than one unit. This rule is tighter than the C2 screen's
  common stop: ADR 0065 gives the reason and asks for quant review. A refused add's level is spent and recorded as a
  blocked candidate (a shadow fill). Every pass, whatever the entry state, a group above one unit has its earlier
  stops moved back. After a restart the groups are adopted from the deterministic add ids and the ladder resumes.
  Each add carries `group_id`. Also fixed: every growth-pot candidate had been quarantined as an unknown style since
  PR #257, so the nightly replay never scored it and no shadow fill existed. Rows written from 11-01 up to the
  deploy are released once by `scripts/unquarantine_growth_obs_2026_11_02.py` (RUNBOOK, icm-demo learning host).
  Review round 1 (PR #266). Safety: the add path confirms every earlier stop at the broker before the add goes out
  (a rejected or raising modify refuses it), and the group stop check runs first in the pass in its own try. Quant
  (growth sprint Â§3, protocol deviation 2): ADR 0065 is accepted. Units are 0.01 lots (`unit_lots`), so both legs'
  8 units fit the 0.10 net lots. A pyramid unit has no profit target, and a group exits together at its stop or its
  end. The read (perf-report, health, `scripts/growth_sprint_read.py`) never counts a refused add as a trade: it is
  listed as `refused_adds`, appears in an uncapped (approx.) ledger, and drives a pairing sensitivity. Growth rows
  never reach the H-61 bandit (`learn`). Tests: `tests/test_growth_pyramid.py`.
