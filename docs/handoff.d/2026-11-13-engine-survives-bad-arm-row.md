- 2026-11-13 (branch fix/engine-survives-bad-arm-row; BACKLOG 117; R136): a bad exploration arm row no longer stops
  the engine, and a config defect never closes a healthy position (trading-safety rework of f838b7d3). The engine
  remembers every cleanly loaded arm row (a hash mismatch aside) in aux.db `arm_spec_last_good` (migration 29,
  goldbot/engine/arm_snapshot.py: upserted by arm id in one transaction, never deleted, shared by every account's
  engine). A row that fails validation, a duplicate id, a bad twin or a row gone missing
  keeps its arm from that snapshot with entries refused (`arm row invalid`, journalled `exploration_arm_invalid`);
  its open positions keep stop, target, hold and every exit. An unreadable ARM-SPEC keeps every remembered arm with
  all entries refused. A registered arm whose row is bad (or the file unreadable) with no last good row refuses
  the start (fail loud): a config defect never orphan-closes a position. Behaviour change: on the engine, an arm deliberately
  deleted from the spec while still registered on the account is now kept with entries refused (its positions run
  to their own exits) instead of being orphan-closed; unregister it in config/accounts.yaml to close it. The first
  start after deploy saves the rows (a bad registered row before that refuses the start). The research loader
  still raises, so CI refuses a bad file. Not covered: the account-level `ExplorationConfig` validators and the
  live-class `spec_rule` refusal still raise by design. Reviews: trading-safety-reviewer, code-reviewer.
