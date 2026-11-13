- 2026-11-13 (branch fix/engine-survives-bad-arm-row; BACKLOG 117; R136): a bad ARM-SPEC row
  (config/exploration_arms.yaml) no longer stops the engine. `exploration.config_for` loads it with
  `load_arms(..., rejected=)`: a row that fails validation, a duplicate id (both rows) or a bad twin is left out and
  journalled at start as `exploration_arm_dropped` with a `why` (already an engine incident in the daily review), the
  other arms run, and an unreadable file drops every arm instead of raising. A dropped arm's open position is closed
  as an orphan by `exploration.exits` (the arm's rule is unknown, so flat is the safe state); reconciliation, the
  Friday cut and every exit keep running. The research loader without `rejected` still raises, so CI still refuses a
  bad file. Not covered: the account-level `ExplorationConfig` validators (pots, 5-character key collisions) and the
  live-class `spec_rule` refusal still raise by design. Reviews: trading-safety-reviewer, code-reviewer.
