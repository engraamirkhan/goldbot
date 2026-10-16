- **The engine publishes why it refuses entries (BACKLOG 43, `entry_blocks` part; implementer).**
  `RiskGate.account_reasons(st, now)` is every account-level reason `check()` refuses for, and `check()` starts from
  it (one list, cannot drift). `engine_<account>.json` carries `entry_blocks` (those codes, at every state write; a
  heartbeat judges the feed it publishes) and `entry_refusals` (per agent, the last per-trade refusal and its time,
  cleared when the gate next allows that agent; in memory only). The API (`goldbot/api/entry_status.py`) now only
  words them: the flag mapping and the min-lot "Likely" fallback are gone, rollover and the weekend cut show, a
  strategy's last refusal is its own block "since" then, and an engine without `entry_blocks` shows
  `entry_reasons_missing` (act), never OPEN. Order change on refusals only: `account_class_unknown` and
  `open_risk_unknown` now come before the intent's own reasons. Deploy: brain and API together (same release);
  exits are never gated by any of it. R43; reviews owed: code-reviewer, trading-safety-reviewer (gate and engine),
  ui-ux-designer (new wording).
  Reviews of 558588e: ui-ux-designer 2026-10-16: CHANGES applied (per-trade refusals worded per code in
  `REFUSAL_TEXT`, so a per-trade `daily_loss_limit_abs` never says "limit reached"; every strategy line and the
  PARTLY OPEN headline use `agent_label`, e.g. "Trend (trend-g0)"; new `entry_reasons_missing` and `invalid_state`
  texts). code-reviewer 2026-10-16: APPROVE, both LOWs fixed: the risk-day rollover clears `entry_refusals`, and
  "every strategy held" now needs the whole throttle roster held (an agent named only by a refusal never makes it
  read blocked for everyone). trading-safety review of 558588e: running, not recorded here yet.
  trading-safety-reviewer 2026-10-16: APPROVE 558588e (0 differences in 420k old-vs-new differential cases); LOW-1
  fixed (entry_blocks and the refusal note are guarded: a failure is logged, the state, risk and orders still write,
  entry_blocks is left out and the API shows `entry_reasons_missing`), INFO-4 fixed (a non-empty list with no valid
  code is `entry_reasons_missing`, never OPEN), INFO-3 pinned (a same-direction-cap intent in the account-reasons
  test). INFO-2: refusal reason lists are longer in the invalid-number cases (`account_class_unknown` and
  `open_risk_unknown` now come before `invalid_state`, and with a bad intent number on a valid state the loss caps
  too), so perf_report's gate_refusals counts rise slightly; every decision is unchanged.
