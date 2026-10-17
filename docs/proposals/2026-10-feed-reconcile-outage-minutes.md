# Proposal: feed reconcile outage minutes, and a kill-switch closes-due health check

Status: built, SRE, 2026-10-17 (both parts are under the ~60-line bar). Reviews: code-reviewer; trading-safety for
part 2 (it reads kill-switch state, changes no engine path).

## 1. An engine outage reads as a feed fault

**Seen.** After the 2026-10-16 deploy, `goldbot run health`:
`FAIL reconcile:icm-demo — feed: 99.92% of 1297 broker minutes diverge (close 3, missing engine 1293, missing broker 0)`.
The engine was stopped for most of Friday's session (deploy, MT5 login), so it stored no ticks for those minutes.
The 3 close mismatches sit on the minutes the engine stopped inside: their close is an earlier tick, not the
minute's last.

**Cost of leaving it.** A FAIL that is not a data fault. Worse, the dead man's switch pings `/fail` while any check
fails (ADR 0014), so this FAIL held the outside alert "down" over the weekend and would have masked a real brain
outage. Every future deploy or restart in market hours would repeat it.

**Options.**
- (a) Exclude minutes the engine was not running. Needs a trustworthy uptime record. `deploys.jsonl` misses manual
  stops (Friday's MT5 login work), the loop heartbeat holds only the current run, the journal is not readable from
  the job. It would need a new engine-side uptime log in `runner.py`, which another lane holds (BACKLOG 62). Rejected
  for now.
- (b) Report missing-engine minutes separately from true divergence. The engine's own tick store already holds the
  evidence: a minute with no engine bar is a coverage gap. **Chosen.**
- (c) Leave it and let it clear on the next run (Mon 19 Oct 23:20). Rejected: there is no manual job run, so the false
  FAIL and the masked dead man's switch would last the weekend and come back after every daytime deploy.

**Decision (b), as built** (`goldbot/data/crossfeed.py`):
- divergence = (close_mismatch + missing_broker) / engine minutes (was: all three / broker minutes); graded as before
  (0.5% warn, 5% error).
- missing_engine above 0.5% of broker minutes is a **warning** on its own ("engine bars missing for N of M broker
  minutes (engine stopped or not collecting: a coverage gap, not a price fault)"). It is still an **error** when the
  broker had bars and the engine stored none in the window (unchanged).
- The last engine minute before a gap is partial; its close is not compared (`edge_excluded`, shown in the reason).
- Fewer than `MIN_COMPARED` = 60 compared minutes cap divergence at a warning: a handful of minutes cannot show a
  price fault.
- Health grades the stored counts with the same `grade()` rule rather than the stored word, so Friday's report
  reads WARN as soon as the release is deployed. A report from this release read by an older one (rollback) loads
  with a schema-skew note (`edge_excluded` ignored), never unreadable.
- dq_events: `reconcile_missing_engine` is a warning while the engine stored any bar.

**What would change my mind.** If coverage gaps start hiding a collection fault while the engine runs (ticks
dropped by a live engine), build (a): the engine appends its run intervals to `core.db`, the job subtracts them, and
missing-engine minutes inside a run escalate to error. `engine:<account>` (last tick age) and `engine_loop` already
FAIL on a live engine that stops collecting, so this is not uncovered today.

**Tests** (`tests/test_crossfeed.py`): `test_minutes_the_engine_did_not_store_are_a_coverage_gap_not_divergence`,
`test_the_partial_last_minute_before_an_engine_gap_is_not_compared`,
`test_a_friday_outage_with_four_engine_minutes_warns_instead_of_failing`,
`test_a_real_price_fault_while_the_engine_ran_still_fails` (guard, passed before and after),
`test_the_health_reason_separates_engine_coverage_from_divergence`,
`test_health_regrades_a_report_written_before_the_rule_change`,
`test_a_coverage_gap_writes_a_warning_event_and_a_price_fault_an_error`.

## 2. Kill switch: close-everything stays set with closes due (C9, BACKLOG 62 plan review)

**Gap.** After a 12% trip the engine closes the whole account and keeps `kill_everything` set until it reads the
account flat. A close the broker keeps refusing (no quote for another symbol, market closed, a requote loop) leaves
positions open after a halt, and today nothing tells the owner.

**Check** `kill_switch:<account>` (`goldbot/ops/checks/risk.py`, slot account/25), health side only: reads
`kill_everything` and `halted_at` from `state/risk_<account>.json` and `closes_due` from `engine_<account>.json`
(both already published; no `runner.py` change).
- Nothing while the flag is clear or no close is due.
- **ok** inside N = **5 minutes** of the trip (`halted_at`). Closes retry every tick; a refused close in an open
  market should clear within seconds, and 5 min is one Telegram health pass under the 6-minute alert SLO.
- **WARN** after 5 min, sent on Telegram (`NOTIFY_ON_WARN`):
  "N close(s) still due after the 12% kill switch: the whole account is not flat yet; closes are re-sent every tick
  (with back-off while the broker says the market is closed), stops rest with the broker; tripped 6 min ago; close
  them by hand in the terminal if this persists (RUNBOOK, kill switch)". Adds "market closed: the closes go out when
  it opens" outside market hours.
- **FAIL** after 30 min with the market open throughout: open positions half an hour after a drawdown halt in an
  open market is money at risk, so it also reaches the dead man's switch.
- `halted_at` missing (older file, or cleared by a re-arm while the flag is still set): WARN at once.
- Unreadable files: nothing (check_engine and check_risk_state report them).

**Tests** (`tests/test_kill_switch_health.py`): `test_no_check_while_the_flag_is_clear_or_nothing_is_due`,
`test_a_fresh_trip_with_closes_due_is_ok_inside_the_grace`,
`test_closes_due_past_five_minutes_warn_and_tell_the_owner`,
`test_closes_due_past_thirty_minutes_of_open_market_fail`, `test_a_closed_market_keeps_it_a_warning`,
`test_an_unknown_trip_time_warns_at_once`, `test_unreadable_files_leave_it_to_the_engine_and_risk_checks`,
`test_run_checks_includes_it`.

**Not covered here.** The flag set with no close due but the account not read flat (an unreadable book) is already
`engine:<account>` `positions_unreadable` (warn, FAIL after 5 reads). If PR-B of BACKLOG 62 adds a published
`kill_pending_since`, switch the timer to it from `halted_at`.
