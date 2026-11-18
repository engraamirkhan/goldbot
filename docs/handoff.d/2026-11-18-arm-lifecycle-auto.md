- 2026-11-18 arm lifecycle, builds 2 and 3 (owner directive "find, deploy, measure, promote and retire strategies by
  itself"; rule docs/research/arm-lifecycle-2026-11-18.md). Build 3: aux.db `arm_registry` (migration 37,
  append-only, hash-chained), written by `autodisc-register --to-db STATE_DIR`, hot-loaded by the engine at a pass
  boundary (goldbot/engine/arm_registry.py) on demo probe_only accounts only (one predicate, `risk.gate.is_demo`, at
  load, re-read and signal); spec_rule rows only, field whitelist, cap within the style ceiling, a bad row refuses
  only its arm, a broken chain / stale read / partial load refuses every hot-loaded entry, no orphan close until a
  load succeeds. Build 2: nightly job `arm_lifecycle` (06:25 UTC; goldbot/research/arm_lifecycle.py) journals per
  (broker, arm) net GBP, fills, mean R, the fill-replay gap and the excess over the matched replay twin in aux.db
  `arm_lifecycle_reads`; RETIRE (kill rule or worse e-process >= 40) through `exploration_arm_status`, SIZE_UP a
  reviewed proposal only (trading-safety: a resize is a new hashed arm), else HOLD. Until the replay-parity monitor
  (feat/replay-only-controls) is merged every twin reads unvalidated and only kill rules decide. Forward evidence
  (`forward:`/`adr0088:`) is refused on non-demo accounts and by `phase_gate_refusal`.
