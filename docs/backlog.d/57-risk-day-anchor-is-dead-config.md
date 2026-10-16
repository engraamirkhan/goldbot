---
number: 57
rank: 47
title: 'risk_day_anchor is read by no code: pin it or make it real'
stage: Execution and risk (loss caps) · PROTECT
size: S
status: proposed (found in the north-star day lane, 2026-10-16; needs trading-safety review)
---
- **57. risk_day_anchor is read by no code: pin it or make it real.** Value: `config/settings.yaml` says
  `risk_day_anchor: { tz: UTC, time: "00:00" }  # loss caps reset`, and `Settings.risk_day_anchor` loads it, but no
  code reads it. The 00:00 UTC risk day is decided separately in five places: `timeutil.risk_day` (gate `new_day`,
  `throttle.entries_on_day`, perf_report's drawdown), the runner's UTC `.date()` shortcut in `_roll_risk_period`,
  `entry_status._next_day`, `gate._risk_week`, and the supervisor through the engines' `day_start_equity`. An owner
  who sets it in `settings.local.yaml` believes the caps' reset has moved; it has not (HANDOFF 2026-10-16, "Finding:
  risk_day_anchor"). Acceptance:
  - a Settings validator refuses any `risk_day_anchor` other than UTC 00:00 at load, naming the fixed risk day; the
    same for `feature_day_anchor` other than America/New_York 13:30, or the setting is wired to `resample`;
  - a test proves an edited anchor fails to load, and the shipped yaml loads;
  - no change to when any cap resets (a test pins `new_day` at 00:00 UTC); rollback-safe (an older release ignores
    the key);
  - moving the risk day itself (for example to the 22:00 UTC roll) is a separate owner decision. It needs one
    shared risk-day function for all five places, a migration for the persisted reset time, and a trading-safety
    review;
  - **before the validator ships** (trading-safety review of the north-star day lane, 2026-10-16): check each
    server's `config/settings.local.yaml` for `risk_day_anchor` and `feature_day_anchor`, because a server that sets
    either would stop loading its settings after the deploy. Put the validator's exact error text and the fix
    (delete the key) into `docs/RUNBOOK.md` in the same change.
