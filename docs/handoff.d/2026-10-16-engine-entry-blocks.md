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
