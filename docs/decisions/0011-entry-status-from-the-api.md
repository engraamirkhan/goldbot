# 0011: Why entries are blocked is worded by the API, never decided in the browser

Status: accepted (ui-ux-designer, 2026-10-11)

## Context
The engine refuses entries for some thirty reasons (goldbot/risk/gate.py, the supervisor, the drift watch, the owner's
halt, the throttle, the server-clock probe, the bridge check). The dashboard showed a handful, and the browser derived
them itself (`blockers()` in `web/src/lib/approval.ts`), so the owner on a phone often saw "New entries allowed" while
the engine refused everything (market closed, clock mismatch, a throttled strategy). Copying the gate's rules into
TypeScript would drift from the engine with every new reason.

## Decision
- `GET /api/accounts` carries `entry_status` per account (`goldbot/api/entry_status.py`): state (open, partial,
  waiting, blocked), a headline, each reason with plain words, a next step, act or wait, a time (reopens, until,
  resets, since) and its Runbook section. The browser lays it out and formats times in the owner's zone; it decides
  nothing.
- Inputs are what the engine publishes, the supervisor halt exactly as the engines read it
  (`Supervisor.engine_should_halt`), the owner's control file, the drift halt and the loss budget; thresholds come from
  `RiskLimits.from_settings`, never a copy.
- An engine-published `entry_blocks` list is honoured (merged) when present; unknown codes render generically
  ("New entries paused (…)"), never as nothing. A test fails when the gate or supervisor gains a reason without words.
- No free text from state files reaches the screen (zones, commits, file names); no account identifiers.

## Consequences
- The Overview, the Approvals safety strip and the proposal card read the same reasons and cannot disagree.
- The API still mirrors which published flags block entries. The lasting fix is for the engine to publish
  `entry_blocks` (the gate's account-level reasons at each refresh, including rollover and the weekend cut, which are
  not published today); the API then only words them. Recommended to the engine lane.
- Telegram `/status` can reuse the same function so both channels say the same words.
- Would change our mind: if the engine publishes `entry_blocks` for every reason, the API's flag mapping can go and
  only the wording stays.
