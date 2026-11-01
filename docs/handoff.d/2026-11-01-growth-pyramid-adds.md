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
  Known limits: at about 0.05 lots a unit, the 0.10 net-lot cap leaves room for one add; the read does not fold a
  shadow add into its group. Tests: `tests/test_growth_pyramid.py`.
